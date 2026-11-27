from datetime import datetime
from functools import wraps

import pyotp
from flask import (Blueprint, abort, flash, redirect, render_template, request, send_file, session,
                   url_for)
from werkzeug.security import check_password_hash

from .. import config, indicador as ind, prazos
from ..gateways import ERP, ServicoIndisponivel
from ..ia.indice import NATUREZA, ROTULOS, sugestao, sugestao_conferencia
from ..modelos import (AnaliseIA, AvisoReemissao, Campo, Documento, Evento, Pedido, Pendencia,
                       Sessao, Situacao, TipoPedido, Usuario)
from ..servicos import email, gravacao, retencao, trilha

bp = Blueprint("equipe", __name__, url_prefix="/equipe")


def _conta_valida():
    if "usuario" not in session:
        return False
    with Sessao() as sessao:
        usuario = sessao.get(Usuario, session["usuario"]["id"])
        if usuario and usuario.ativo and usuario.senha_hash:
            return True
    session.clear()
    return False


def exige_perfil(*perfis):
    def deco(visao):
        @wraps(visao)
        def interno(*args, **kwargs):
            if not _conta_valida():
                return redirect(url_for("equipe.entrar"))
            if session["usuario"]["perfil"] not in perfis:
                abort(403)
            return visao(*args, **kwargs)
        return interno
    return deco


exige_equipe = exige_perfil("conferente", "gerente")


so_conferente = exige_perfil("conferente")


def _carteira():
    email_do_gerente = session["usuario"]["email"]
    return {condominio["id"]: condominio for condominio in ERP().condominios()
            if condominio.get("gerente") == email_do_gerente}


def autor():
    return session["usuario"]["email"]


@bp.route("/entrar", methods=["GET", "POST"])
def entrar():
    erro = None
    if request.method == "POST":
        with Sessao() as sessao:
            email_digitado = request.form.get("email", "").strip().lower()
            consulta = sessao.query(Usuario).filter_by(email=email_digitado, ativo=True)
            usuario = consulta.filter(Usuario.senha_hash != "").first()
            if usuario and check_password_hash(usuario.senha_hash, request.form.get("senha", "")):
                session.clear()
                session["pre_auth"] = usuario.id
                session["pre_auth_email"] = usuario.email
                return redirect(url_for("equipe.segundo_fator"))
            quem_tentou = request.form.get("email", "")[:160] or "desconhecido"
            trilha.registrar(sessao, "login_falhou", quem_tentou)
            sessao.commit()
        erro = "E-mail ou senha incorretos."
    return render_template("equipe/entrar.html", erro=erro)


@bp.route("/2fa", methods=["GET", "POST"])
def segundo_fator():
    if "pre_auth" not in session:
        return redirect(url_for("equipe.entrar"))
    erro = None
    if request.method == "POST":
        with Sessao() as sessao:
            usuario = sessao.get(Usuario, session["pre_auth"])
            codigo = request.form.get("codigo", "").strip()
            if usuario and pyotp.TOTP(usuario.totp_segredo).verify(codigo, valid_window=1):
                session.clear()
                session["usuario"] = {"id": usuario.id, "nome": usuario.nome,
                                      "email": usuario.email, "perfil": usuario.perfil.value}
                trilha.registrar(sessao, "login", usuario.email)
                sessao.commit()
                if usuario.perfil.value == "tecnologia":
                    destino = "contas.lista"
                else:
                    destino = "equipe.esteira"
                return redirect(url_for(destino))
            quem_tentou = session.get("pre_auth_email", "desconhecido")
            trilha.registrar(sessao, "segundo_fator_falhou", quem_tentou)
            sessao.commit()
        session["erros_2fa"] = session.get("erros_2fa", 0) + 1
        if session["erros_2fa"] >= 5:
            session.clear()
            return render_template("equipe/entrar.html",
                                   erro="Muitos códigos inválidos. Entre de novo com a senha.")
        erro = ("Código inválido ou vencido. "
                "Confira se o código é desta conta e se foi gerado agora.")
    return render_template("equipe/segundo_fator.html", erro=erro,
                           conta=session.get("pre_auth_email"))


@bp.get("/sair")
def sair():
    session.clear()
    return redirect(url_for("equipe.entrar"))


def _filtrar_esteira(consulta, gerente, marcacao, tipo, filtro):
    if gerente:
        try:
            consulta = consulta.filter(Pedido.condominio_id.in_(list(_carteira())))
        except ServicoIndisponivel:
            consulta = consulta.filter(False)
    if marcacao in ("contestado", "escalado", "aguardando_matricula"):
        consulta = consulta.filter(getattr(Pedido, marcacao).is_(True))
    if tipo in {opcao.value for opcao in TipoPedido}:
        consulta = consulta.filter(Pedido.tipo == tipo)
    if filtro == "abertos":
        situacoes_abertas = [Situacao.A_CONFERIR, Situacao.COM_PENDENCIA]
        consulta = consulta.filter(Pedido.situacao.in_(situacoes_abertas))
    elif filtro in {opcao.value for opcao in Situacao}:
        consulta = consulta.filter(Pedido.situacao == filtro)
    return consulta


def _nomes_dos_condominios():
    try:
        return {condominio["id"]: condominio["nome"] for condominio in ERP().condominios()}
    except ServicoIndisponivel:
        return {}


def _linhas_da_esteira(pedidos, condominios):
    linhas = []
    for pedido in pedidos:
        prazos_do_pedido = prazos.prazos(pedido)
        alerta = None
        if pedido.situacao == Situacao.A_CONFERIR:
            alerta = (prazos.alerta(prazos_do_pedido.get("conclusao"))
                      or prazos.alerta(prazos_do_pedido.get("pendencias")))
        nome_do_condominio = condominios.get(pedido.condominio_id, f"#{pedido.condominio_id}")
        linhas.append({"p": pedido, "condominio": nome_do_condominio,
                       "prazo": prazos_do_pedido.get("conclusao"), "alerta": alerta})
    return linhas


def _avisos_pendentes(sessao):
    consulta = sessao.query(AvisoReemissao).filter(AvisoReemissao.resolvido_em.is_(None))
    return consulta.order_by(AvisoReemissao.id).all()


@bp.get("")
@exige_equipe
def esteira():
    gerente = session["usuario"]["perfil"] == "gerente"
    filtro = request.args.get("situacao", "todos" if gerente else "abertos")
    marcacao_padrao = "escalado" if gerente and "situacao" not in request.args else ""
    marcacao = request.args.get("marcacao", marcacao_padrao)
    tipo = request.args.get("tipo", "")
    if filtro == "escalados":
        filtro = "todos"
        marcacao = "escalado"
    with Sessao() as sessao:
        consulta = sessao.query(Pedido).filter(Pedido.situacao != Situacao.RASCUNHO)
        consulta = _filtrar_esteira(consulta, gerente, marcacao, tipo, filtro)
        pedidos = consulta.order_by(Pedido.recebido_em).all()
        condominios = _nomes_dos_condominios()
        linhas = _linhas_da_esteira(pedidos, condominios)
        avisos = [] if gerente else _avisos_pendentes(sessao)
        ids_dos_pedidos_avisados = [aviso.pedido_id for aviso in avisos]
        pedidos_avisados = sessao.query(Pedido).filter(Pedido.id.in_(ids_dos_pedidos_avisados))
        numeros = {pedido.id: pedido.numero for pedido in pedidos_avisados}
        sem_gravar = (sessao.query(Pedido)
                      .filter(Pedido.aprovado_em.is_not(None),
                              Pedido.situacao == Situacao.A_CONFERIR)
                      .order_by(Pedido.aprovado_em).all())
    return render_template("equipe/esteira.html", linhas=linhas, filtro=filtro, marcacao=marcacao,
                           tipo=tipo, avisos=avisos, numeros=numeros, sem_gravar=sem_gravar)


def _carregar(sessao, pedido_id):
    pedido = sessao.get(Pedido, pedido_id)
    if not pedido or pedido.situacao == Situacao.RASCUNHO:
        abort(404)
    if session["usuario"]["perfil"] == "gerente":
        try:
            if pedido.condominio_id not in _carteira():
                abort(403)
        except ServicoIndisponivel:
            abort(503)
    return pedido


@bp.get("/pedido/<int:pedido_id>")
@exige_equipe
def detalhe(pedido_id):
    with Sessao() as sessao:
        pedido = _carregar(sessao, pedido_id)
        erp = ERP()
        try:
            unidade = erp.unidade(pedido.unidade_id) if pedido.unidade_id else None
            nomes = (condominio_erp["nome"] for condominio_erp in erp.condominios()
                     if condominio_erp["id"] == pedido.condominio_id)
            condominio = next(nomes, "")
        except ServicoIndisponivel:
            unidade = None
            condominio = "(ERP indisponível)"
        docs = [arquivo for arquivo in pedido.documentos if not arquivo.eliminado_em]
        analises = []
        if docs:
            ids_dos_documentos = [arquivo.id for arquivo in docs]
            analises = (sessao.query(AnaliseIA)
                        .filter(AnaliseIA.documento_id.in_(ids_dos_documentos))
                        .order_by(AnaliseIA.id.desc()).all())
        campos = sessao.query(Campo).filter_by(pedido_id=pedido.id).order_by(Campo.id).all()
        conferentes = {usuario.id: usuario.email for usuario in sessao.query(Usuario).all()}
        eventos = sessao.query(Evento).filter_by(pedido_id=pedido.id).order_by(Evento.id).all()
        analisou_contestacao = any(evento.tipo == "contestacao_analisada" for evento in eventos)
        escalonamentos = [evento for evento in eventos
                          if evento.tipo in ("pedido_escalado", "escalonamento_decidido")]
        return render_template(
            "equipe/pedido.html", p=pedido, unidade=unidade, condominio=condominio, docs=docs,
            analise=analises[0] if analises else None, campos=campos, rotulos=ROTULOS,
            conferentes=conferentes, eventos=eventos, prazos=prazos.prazos(pedido),
            analisou_contestacao=analisou_contestacao, Situacao=Situacao,
            sugestao=sugestao(campos, pedido.tipo.value),
            bloqueios=gravacao.bloqueios(sessao, pedido, unidade),
            previa=gravacao.previa(sessao, pedido, unidade) if not pedido.aprovado_em else None,
            aprovador=conferentes.get(pedido.aprovado_por), escalonamentos=escalonamentos,
            pendencias=sorted(pedido.pendencias, key=lambda item: item.id),
            avisos_falhos=[evento.detalhe for evento in eventos if evento.tipo == "aviso_falhou"])


@bp.get("/documento/<int:doc_id>")
@exige_equipe
def documento(doc_id):
    with Sessao() as sessao:
        arquivo = sessao.get(Documento, doc_id)
        if not arquivo or arquivo.eliminado_em:
            abort(404)
        _carregar(sessao, arquivo.pedido_id)
        trilha.registrar(sessao, "documento_aberto", autor(), pedido_id=arquivo.pedido_id,
                         documento=arquivo.id)
        sessao.commit()
        return send_file(arquivo.caminho, mimetype=arquivo.mime,
                         download_name=arquivo.nome_original)


ETAPAS = ["Pedido e documentos", "Critério por critério", "Resumo", "Decisão"]


def _criterio_inicial(etapa, campos):
    indice_informado = request.args.get("c", type=int) or 0
    ultimo_indice = max(len(campos) - 1, 0)
    indice = min(max(indice_informado, 0), ultimo_indice)
    if etapa == 2 and campos and request.args.get("c") is None:
        pendentes = (posicao for posicao, campo in enumerate(campos) if not campo.conferencia)
        indice = next(pendentes, 0)
    return indice


@bp.get("/pedido/<int:pedido_id>/conferencia/<int:etapa>")
@so_conferente
def conferencia(pedido_id, etapa):
    if etapa not in (1, 2, 3, 4):
        abort(404)
    with Sessao() as sessao:
        pedido = _carregar(sessao, pedido_id)
        situacoes_abertas = (Situacao.A_CONFERIR, Situacao.COM_PENDENCIA)
        if pedido.situacao not in situacoes_abertas or pedido.aprovado_em:
            return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))
        erp = ERP()
        try:
            unidade = erp.unidade(pedido.unidade_id) if pedido.unidade_id else None
            nomes = (condominio_erp["nome"] for condominio_erp in erp.condominios()
                     if condominio_erp["id"] == pedido.condominio_id)
            condominio = next(nomes, "")
        except ServicoIndisponivel:
            unidade = None
            condominio = "(ERP indisponível)"
        docs = [arquivo for arquivo in pedido.documentos if not arquivo.eliminado_em]
        analises = []
        if docs:
            ids_dos_documentos = [arquivo.id for arquivo in docs]
            analises = (sessao.query(AnaliseIA)
                        .filter(AnaliseIA.documento_id.in_(ids_dos_documentos))
                        .order_by(AnaliseIA.id.desc()).all())
        campos = sessao.query(Campo).filter_by(pedido_id=pedido.id).order_by(Campo.id).all()
        conferentes = {usuario.id: usuario.email for usuario in sessao.query(Usuario).all()}
        eventos = sessao.query(Evento).filter_by(pedido_id=pedido.id).order_by(Evento.id).all()
        indice = _criterio_inicial(etapa, campos)
        return render_template(
            "equipe/conferencia.html", p=pedido, etapa=etapa, etapas=ETAPAS, unidade=unidade,
            condominio=condominio, docs=docs, analise=analises[0] if analises else None,
            campos=campos, i=indice, rotulos=ROTULOS, natureza=NATUREZA, conferentes=conferentes,
            prazos=prazos.prazos(pedido), Situacao=Situacao,
            analisou_contestacao=any(evento.tipo == "contestacao_analisada" for evento in eventos),
            sugestao=sugestao_conferencia(campos, pedido.tipo.value),
            bloqueios=gravacao.bloqueios(sessao, pedido, unidade),
            previa=gravacao.previa(sessao, pedido, unidade),
            avisos_falhos=[evento.detalhe for evento in eventos if evento.tipo == "aviso_falhou"])


@bp.post("/pedido/<int:pedido_id>/criterio/<int:campo_id>")
@so_conferente
def responder_criterio(pedido_id, campo_id):
    resposta = request.form.get("resposta")
    if resposta not in ("confere", "nao_confere"):
        abort(400)
    with Sessao() as sessao:
        pedido = _carregar(sessao, pedido_id)
        situacoes_abertas = (Situacao.A_CONFERIR, Situacao.COM_PENDENCIA)
        if pedido.situacao not in situacoes_abertas or pedido.aprovado_em:
            abort(409)
        campos = sessao.query(Campo).filter_by(pedido_id=pedido.id).order_by(Campo.id).all()
        encontrados = (candidato for candidato in campos if candidato.id == campo_id)
        campo = next(encontrados, None) or abort(404)
        observacao = (request.form.get("observacao") or "").strip()[:1000] or None
        campo.conferencia = resposta
        campo.observacao = observacao
        campo.conferido_por = session["usuario"]["id"]
        campo.conferido_em = datetime.now()
        trilha.registrar(sessao, "criterio_conferido", autor(), pedido_id=pedido.id,
                         criterio=campo.nome, resposta=resposta, observacao=campo.observacao)
        sessao.commit()
        ids_dos_campos = [item.id for item in campos]
        posicao = ids_dos_campos.index(campo_id)
        if posicao + 1 < len(campos):
            return redirect(url_for("equipe.conferencia", pedido_id=pedido_id, etapa=2,
                                    c=posicao + 1))
    return redirect(url_for("equipe.conferencia", pedido_id=pedido_id, etapa=3))


@bp.post("/pedido/<int:pedido_id>/pendencia")
@so_conferente
def pendencia(pedido_id):
    descricao = request.form.get("descricao", "").strip()
    depende = request.form.get("depende_solicitante") == "1"
    if not descricao:
        return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))
    with Sessao() as sessao:
        pedido = _carregar(sessao, pedido_id)
        situacoes_abertas = (Situacao.A_CONFERIR, Situacao.COM_PENDENCIA)
        if pedido.situacao not in situacoes_abertas or pedido.aprovado_em:
            abort(409)
        sessao.add(Pendencia(pedido_id=pedido.id, descricao=descricao, depende_solicitante=depende,
                             criada_por=session["usuario"]["id"]))
        trilha.registrar(sessao, "pendencia_registrada", autor(), pedido_id=pedido.id,
                         descricao=descricao, depende_solicitante=depende)
        if depende:
            pedido.situacao = Situacao.COM_PENDENCIA
            ja_abertas = [item.descricao for item in pedido.pendencias
                          if not item.respondida_em and item.depende_solicitante]
            abertas = ja_abertas + [descricao]
            endereco_do_link = f"{config.URL_PUBLICA}{url_for('titularidade.pedir_link')}"
            email.enviar(pedido.solicitante_email, f"Pedido {pedido.numero}: falta um item",
                         "pendencia", pedido=pedido, itens=abertas, url=endereco_do_link,
                         dias=config.PENDENCIA_SEM_RESPOSTA_DIAS)
            trilha.registrar(sessao, "email_pendencia", "sistema", pedido_id=pedido.id)
        sessao.commit()
    return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))


@bp.post("/pedido/<int:pedido_id>/contestacao")
@so_conferente
def analisar_contestacao(pedido_id):
    texto = request.form.get("analise", "").strip()
    if texto:
        with Sessao() as sessao:
            pedido = _carregar(sessao, pedido_id)
            trilha.registrar(sessao, "contestacao_analisada", autor(), pedido_id=pedido.id,
                             analise=texto)
            sessao.commit()
    return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))


@bp.post("/pedido/<int:pedido_id>/recusar")
@so_conferente
def recusar(pedido_id):
    motivo = request.form.get("motivo", "").strip()
    if not motivo:
        return redirect(url_for("equipe.detalhe", pedido_id=pedido_id) + "?erro=motivo")
    with Sessao() as sessao:
        pedido = _carregar(sessao, pedido_id)
        situacoes_abertas = (Situacao.A_CONFERIR, Situacao.COM_PENDENCIA)
        if pedido.situacao not in situacoes_abertas or pedido.aprovado_em:
            abort(409)
        pedido.situacao = Situacao.RECUSADO
        pedido.motivo_recusa = motivo
        trilha.registrar(sessao, "pedido_recusado", autor(), pedido_id=pedido.id, motivo=motivo)
        if pedido.tipo.value == "conciliacao":
            if pedido.solicitante_email:
                email.enviar(pedido.solicitante_email, "Alteração de contato não aceita",
                             "conciliacao_recusada", pedido=pedido)
                trilha.registrar(sessao, "email_conciliacao_recusada", "sistema",
                                 pedido_id=pedido.id)
        else:
            email.enviar(pedido.solicitante_email, f"Pedido {pedido.numero} recusado", "recusado",
                         pedido=pedido)
            trilha.registrar(sessao, "email_recusado", "sistema", pedido_id=pedido.id)
        sessao.commit()
    return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))


@bp.post("/pedido/<int:pedido_id>/reanalisar")
@so_conferente
def reanalisar(pedido_id):
    with Sessao() as sessao:
        pedido = _carregar(sessao, pedido_id)
        situacoes_abertas = (Situacao.A_CONFERIR, Situacao.COM_PENDENCIA)
        if pedido.situacao not in situacoes_abertas or pedido.aprovado_em:
            abort(409)
        if pedido.tipo.value == "inclusao_locatario":
            esperado = "locacao"
        else:
            tipo_por_comprovante = {"matricula": "matricula", "contrato": "compra_venda"}
            esperado = tipo_por_comprovante.get(pedido.tipo_comprovante)
        candidatos = (arquivo for arquivo in pedido.documentos
                      if not arquivo.eliminado_em and arquivo.tipo.value == esperado)
        documento_esperado = next(candidatos, None)
        sessao.query(Pedido).filter_by(id=pedido.id).with_for_update().one()
        em_andamento = documento_esperado and (
            sessao.query(AnaliseIA)
            .filter(AnaliseIA.documento_id == documento_esperado.id,
                    AnaliseIA.status.in_(("fila", "processando")))
            .first())
        if documento_esperado and not em_andamento:
            sessao.add(AnaliseIA(documento_id=documento_esperado.id))
            trilha.registrar(sessao, "reanalise_pedida", autor(), pedido_id=pedido.id)
            sessao.commit()
    return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))


@bp.post("/pedido/<int:pedido_id>/aprovar")
@so_conferente
def aprovar(pedido_id):
    with Sessao() as sessao:
        pedido = _carregar(sessao, pedido_id)
        try:
            concluido = gravacao.aprovar(sessao, pedido, session["usuario"])
        except gravacao.AprovacaoBloqueada as erro:
            sessao.rollback()
            flash(str(erro), "erro")
            return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))
        sessao.commit()
        if concluido:
            mensagem = "Pedido aprovado e concluído: ERP e aplicativo atualizados."
            categoria = "ok"
        else:
            mensagem = (f"Pedido aprovado, mas a gravação falhou ({pedido.erro_gravacao}). "
                        "A Tecnologia foi avisada; "
                        "o sistema tenta de novo a cada 5 minutos.")
            categoria = "erro"
        flash(mensagem, categoria)
    return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))


@bp.post("/pedido/<int:pedido_id>/gravar")
@so_conferente
def reprocessar_gravacao(pedido_id):
    with Sessao() as sessao:
        pedido = _carregar(sessao, pedido_id)
        gravou = gravacao.gravar(sessao, pedido, autor())
        sessao.commit()
        if gravou:
            mensagem = "Gravação concluída."
            categoria = "ok"
        else:
            mensagem = f"A gravação falhou de novo ({pedido.erro_gravacao})."
            categoria = "erro"
        flash(mensagem, categoria)
    return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))


@bp.post("/aviso-reemissao/<int:aviso_id>/resolver")
@so_conferente
def resolver_aviso(aviso_id):
    with Sessao() as sessao:
        aviso = sessao.get(AvisoReemissao, aviso_id) or abort(404)
        if not aviso.resolvido_em:
            aviso.resolvido_por = session["usuario"]["id"]
            aviso.resolvido_em = datetime.now()
            trilha.registrar(sessao, "reemissao_resolvida", autor(), pedido_id=aviso.pedido_id,
                             aviso=aviso.id)
            sessao.commit()
    return redirect(url_for("equipe.esteira"))


@bp.post("/pedido/<int:pedido_id>/escalar")
@so_conferente
def escalar(pedido_id):
    duvida = request.form.get("duvida", "").strip()
    if not duvida:
        flash("Descreva a dúvida para escalar.", "erro")
        return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))
    with Sessao() as sessao:
        pedido = _carregar(sessao, pedido_id)
        situacoes_abertas = (Situacao.A_CONFERIR, Situacao.COM_PENDENCIA)
        if pedido.situacao not in situacoes_abertas or pedido.aprovado_em or pedido.escalado:
            abort(409)
        try:
            condominios = ERP().condominios()
            condominio = next(condominio_erp for condominio_erp in condominios
                              if condominio_erp["id"] == pedido.condominio_id)
        except (ServicoIndisponivel, StopIteration):
            flash("Não foi possível consultar o gerente da carteira no ERP. Tente de novo.", "erro")
            return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))
        pedido.escalado = True
        trilha.registrar(sessao, "pedido_escalado", autor(), pedido_id=pedido.id, duvida=duvida,
                         gerente=condominio["gerente"])
        endereco_do_pedido = (f"{config.URL_PUBLICA}"
                              f"{url_for('equipe.detalhe', pedido_id=pedido.id)}")
        email.enviar(condominio["gerente"], f"Pedido {pedido.numero} escalado para sua decisão",
                     "escalado", pedido=pedido, duvida=duvida, condominio=condominio["nome"],
                     url=endereco_do_pedido)
        trilha.registrar(sessao, "email_gerente", "sistema", pedido_id=pedido.id)
        sessao.commit()
        flash("Pedido escalado. O gerente da carteira foi avisado.", "ok")
    return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))


@bp.post("/pedido/<int:pedido_id>/decidir")
@exige_perfil("gerente")
def decidir(pedido_id):
    decisao = request.form.get("decisao", "").strip()
    juridico = request.form.get("juridico") == "1"
    with Sessao() as sessao:
        pedido = _carregar(sessao, pedido_id)
        if not pedido.escalado:
            abort(409)
        try:
            if pedido.condominio_id not in _carteira():
                abort(403)
        except ServicoIndisponivel:
            flash("Não foi possível confirmar a carteira no ERP. Tente de novo.", "erro")
            return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))
        if not decisao:
            flash("Registre a decisão.", "erro")
            return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))
        pedido.escalado = False
        trilha.registrar(sessao, "escalonamento_decidido", autor(), pedido_id=pedido.id,
                         decisao=decisao, consultou_juridico=juridico)
        sessao.commit()
        flash("Decisão registrada. O pedido volta ao Cadastro e Emissão.", "ok")
    return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))


@bp.get("/indicador")
@exige_equipe
def indicador():
    from datetime import date
    try:
        ano, mes = map(int, request.args.get("mes", "").split("-"))
        date(ano, mes, 1)
    except ValueError:
        hoje = date.today()
        ano = hoje.year
        mes = hoje.month
    with Sessao() as sessao:
        concluidos = (sessao.query(Pedido)
                      .filter(Pedido.situacao == Situacao.CONCLUIDO,
                              Pedido.concluido_em.is_not(None))
                      .all())
        resultado = ind.calcular(concluidos, ano, mes)
        return render_template("equipe/indicador.html", r=resultado, ano=ano, mes=mes,
                               prazos=prazos)


@bp.post("/pedido/<int:pedido_id>/encerrar-tratativas")
@so_conferente
def encerrar_tratativas(pedido_id):
    with Sessao() as sessao:
        pedido = _carregar(sessao, pedido_id)
        if pedido.situacao != Situacao.RECUSADO:
            abort(409)
        quantidade = retencao.eliminar_documentos(sessao, pedido, autor(),
                                                  "tratativas com o solicitante encerradas")
        sessao.commit()
        flash(f"{quantidade} documento(s) apagado(s). "
              "A trilha continua identificando o que foi enviado.", "ok")
    return redirect(url_for("equipe.detalhe", pedido_id=pedido_id))
