import smtplib
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Blueprint, redirect, render_template, request, session, url_for
from sqlalchemy import func

from .. import config
from ..gateways import ERP, ServicoIndisponivel
from ..modelos import (AnaliseIA, AvisoProprietario, Documento, LinkAcesso, Pedido, Pendencia,
                       Sessao, Situacao, TipoDocumento, TipoPedido)
from ..servicos import documentos, email, trilha
from ..util import cpf_cnpj_valido, email_valido, formatar_cpf_cnpj, hash_token, novo_token

bp = Blueprint("titularidade", __name__, url_prefix="/titularidade")

LIMITE_LINKS_POR_HORA = 3


@bp.get("")
def pedir_link():
    return _form_link({}, {})


def _form_link(dados, erros):
    try:
        condominios = ERP().condominios()
    except ServicoIndisponivel:
        return render_template("titularidade/indisponivel.html"), 503
    return render_template("titularidade/pedir_link.html", condominios=condominios, dados=dados,
                           erros=erros)


def _limite_atingido(sessao, cpf, email_, condominio_id, numero, bloco):
    desde = datetime.now() - timedelta(hours=1)
    recentes = sessao.query(LinkAcesso).filter(LinkAcesso.criado_em >= desde)
    por_cpf = recentes.filter(LinkAcesso.cpf_cnpj == cpf).count()
    por_email = recentes.filter(func.lower(LinkAcesso.email) == email_.lower()).count()
    links_do_condominio = recentes.filter(LinkAcesso.condominio_id == condominio_id)
    unidade_procurada = f"{numero}|{bloco}"
    por_unidade = sum(1 for link in links_do_condominio
                      if (link.unidade_informada or "") == unidade_procurada)
    return max(por_cpf, por_email, por_unidade) >= LIMITE_LINKS_POR_HORA


@bp.post("")
def enviar_link():
    formulario_enviado = request.form
    campos = ("condominio_id", "numero", "bloco", "nome", "cpf_cnpj", "email")
    dados = {campo: (formulario_enviado.get(campo) or "").strip() for campo in campos}
    erros = _validar_pedido_de_link(dados)
    if erros:
        return _form_link(dados, erros), 400

    cpf = formatar_cpf_cnpj(dados["cpf_cnpj"])
    cond_id = int(dados["condominio_id"])
    numero = dados["numero"]
    bloco = dados["bloco"].upper()
    with Sessao() as sessao:
        if _limite_atingido(sessao, cpf, dados["email"], cond_id, numero, bloco):
            trilha.registrar(sessao, "link_limite", "sistema", cpf=cpf)
            sessao.commit()
            return render_template("titularidade/limite.html"), 429
        try:
            unidade = ERP().localizar_unidade(cond_id, numero, bloco or None)
        except ServicoIndisponivel:
            return render_template("titularidade/indisponivel.html"), 503
        com_pendencia = _buscar_pedido_com_pendencia(sessao, cpf, dados, cond_id)
        rascunho = com_pendencia or _buscar_rascunho_recente(sessao, cpf, dados, cond_id)
        token = _criar_link(sessao, dados, cpf, cond_id, numero, bloco, unidade, rascunho,
                            com_pendencia)
        sessao.commit()
    _enviar_email_do_link(dados, token)
    return render_template("titularidade/link_enviado.html", email=dados["email"])


def _validar_pedido_de_link(dados):
    erros = {}
    if not dados["condominio_id"].isdigit():
        erros["condominio_id"] = "Escolha o condomínio."
    if not dados["numero"]:
        erros["numero"] = "Informe o número da unidade."
    if len(dados["nome"].split()) < 2:
        erros["nome"] = "Informe o nome completo."
    if not cpf_cnpj_valido(dados["cpf_cnpj"]):
        erros["cpf_cnpj"] = "CPF ou CNPJ inválido."
    if not email_valido(dados["email"]):
        erros["email"] = "E-mail inválido."
    return erros


def _buscar_pedido_com_pendencia(sessao, cpf, dados, cond_id):
    consulta = sessao.query(Pedido).filter(
        Pedido.situacao == Situacao.COM_PENDENCIA,
        Pedido.tipo == TipoPedido.TITULARIDADE,
        Pedido.solicitante_cpf_cnpj == cpf,
        func.lower(Pedido.solicitante_email) == dados["email"].lower(),
        Pedido.condominio_id == cond_id)
    return consulta.order_by(Pedido.id.desc()).first()


def _buscar_rascunho_recente(sessao, cpf, dados, cond_id):
    consulta = sessao.query(Pedido).filter(
        Pedido.situacao == Situacao.RASCUNHO,
        Pedido.tipo == TipoPedido.TITULARIDADE,
        Pedido.solicitante_cpf_cnpj == cpf,
        func.lower(Pedido.solicitante_email) == dados["email"].lower(),
        Pedido.condominio_id == cond_id,
        Pedido.atualizado_em >= datetime.now() - timedelta(days=config.RASCUNHO_DIAS))
    return consulta.first()


def _criar_link(sessao, dados, cpf, cond_id, numero, bloco, unidade, rascunho, com_pendencia):
    token, hash_do_token = novo_token()
    unidade_id = unidade["id"] if unidade else None
    pedido_id = rascunho.id if rascunho else None
    validade = timedelta(hours=config.LINK_VALIDADE_HORAS)
    link = LinkAcesso(token_hash=hash_do_token, email=dados["email"], cpf_cnpj=cpf,
                      nome=dados["nome"], condominio_id=cond_id, unidade_id=unidade_id,
                      unidade_informada=f"{numero}|{bloco}", pedido_id=pedido_id,
                      expira_em=datetime.now() + validade)
    sessao.add(link)
    finalidade = "pendencia" if com_pendencia else "pedido"
    trilha.registrar(sessao, "link_solicitado", "solicitante", pedido_id=link.pedido_id, cpf=cpf,
                     unidade_localizada=bool(unidade), finalidade=finalidade)
    return token


def _enviar_email_do_link(dados, token):
    caminho_do_link = url_for('titularidade.abrir_link', token=token)
    email.enviar(dados["email"], "Seu link para o pedido de mudança de titularidade", "link",
                 nome=dados["nome"], url=f"{config.URL_PUBLICA}{caminho_do_link}",
                 horas=config.LINK_VALIDADE_HORAS)


def _link_valido(sessao, token):
    link = sessao.query(LinkAcesso).filter_by(token_hash=hash_token(token)).first()
    if not link or link.usado_em or link.expira_em < datetime.now():
        return None
    return link


@bp.get("/l/<token>")
def abrir_link(token):
    with Sessao() as sessao:
        if not _link_valido(sessao, token):
            return render_template("titularidade/link_invalido.html"), 410
    return render_template("titularidade/continuar.html", token=token)


@bp.post("/l/<token>")
def usar_link(token):
    with Sessao() as sessao:
        link = _link_valido(sessao, token)
        if not link:
            return render_template("titularidade/link_invalido.html"), 410
        link.usado_em = datetime.now()
        link.ip = request.remote_addr
        pedido = sessao.get(Pedido, link.pedido_id) if link.pedido_id else None
        if pedido and pedido.situacao != Situacao.RASCUNHO:
            trilha.registrar(sessao, "link_usado", "solicitante", pedido_id=pedido.id,
                             finalidade="pendencia")
            sessao.commit()
            if pedido.situacao != Situacao.COM_PENDENCIA:
                pagina = render_template("titularidade/nao_aceita_resposta.html", pedido=pedido)
                return pagina, 410
            validade = datetime.now() + timedelta(hours=config.LINK_VALIDADE_HORAS)
            session["resposta"] = {"id": pedido.id, "ate": validade.isoformat()}
            return redirect(url_for("titularidade.responder"))
        if not pedido:
            unidade_informada = link.unidade_informada or "|"
            numero, bloco = unidade_informada.split("|")
            pedido = Pedido(tipo=TipoPedido.TITULARIDADE, condominio_id=link.condominio_id,
                            unidade_id=link.unidade_id, solicitante_nome=link.nome,
                            solicitante_cpf_cnpj=link.cpf_cnpj, solicitante_email=link.email,
                            dados_informados={"numero": numero, "bloco": bloco})
            sessao.add(pedido)
            sessao.flush()
            link.pedido_id = pedido.id
        trilha.registrar(sessao, "link_usado", "solicitante", pedido_id=pedido.id)
        sessao.commit()
        validade = datetime.now() + timedelta(hours=config.LINK_VALIDADE_HORAS)
        session["pedido"] = {"id": pedido.id, "ate": validade.isoformat()}
    return redirect(url_for("titularidade.formulario"))


def _pedido_da_sessao(sessao):
    pedido_na_sessao = session.get("pedido")
    if not pedido_na_sessao or datetime.fromisoformat(pedido_na_sessao["ate"]) < datetime.now():
        session.pop("pedido", None)
        return None
    pedido = sessao.get(Pedido, pedido_na_sessao["id"])
    return pedido if pedido and pedido.situacao == Situacao.RASCUNHO else None


CAMPOS_ARQUIVO = {"identidade": TipoDocumento.IDENTIDADE, "matricula": TipoDocumento.MATRICULA,
                  "contrato": TipoDocumento.CONTRATO_COMPRA_VENDA}
CAMPO_POR_TIPO = {tipo: campo for campo, tipo in CAMPOS_ARQUIVO.items()}


def _docs(pedido):
    return {CAMPO_POR_TIPO[documento.tipo]: documento
            for documento in pedido.documentos
            if not documento.eliminado_em and documento.tipo in CAMPO_POR_TIPO}


@bp.get("/pedido")
def formulario():
    with Sessao() as sessao:
        pedido = _pedido_da_sessao(sessao)
        if not pedido:
            return render_template("titularidade/link_invalido.html"), 410
        docs = _docs(pedido)
        return render_template("titularidade/pedido.html", pedido=pedido, docs=docs, erros={})


@bp.post("/pedido")
def salvar():
    acao = request.form.get("acao", "salvar")
    with Sessao() as sessao:
        pedido = _pedido_da_sessao(sessao)
        if not pedido:
            return render_template("titularidade/link_invalido.html"), 410
        erros = _gravar_dados_do_formulario(pedido)
        _gravar_arquivos_do_formulario(sessao, pedido, erros)
        sessao.flush()
        docs = _docs(pedido)

        if acao == "enviar" and not erros:
            erros = _faltas_para_envio(pedido, docs)
        if erros or acao != "enviar":
            return _registrar_rascunho_ou_bloqueio(sessao, pedido, docs, erros, acao)
        _enviar(sessao, pedido, docs)
        sessao.commit()
        session.pop("pedido", None)
        return render_template("titularidade/recebido.html", pedido=pedido)


def _gravar_dados_do_formulario(pedido):
    erros = {}
    tipo = request.form.get("tipo_comprovante")
    pedido.tipo_comprovante = tipo if tipo in ("matricula", "contrato") else None
    chaves = request.form.get("data_chaves") or None
    try:
        pedido.data_chaves = date.fromisoformat(chaves) if chaves else None
    except ValueError:
        erros["data_chaves"] = "Data inválida."
    return erros


def _gravar_arquivos_do_formulario(sessao, pedido, erros):
    for campo, tipo_doc in CAMPOS_ARQUIVO.items():
        arquivo = request.files.get(campo)
        if arquivo and arquivo.filename:
            try:
                caminho, mime, tamanho = documentos.guardar(pedido.id, arquivo)
            except documentos.ArquivoInvalido as erro:
                erros[campo] = str(erro).capitalize() + "."
                continue
            for antigo in pedido.documentos:
                if antigo.tipo == tipo_doc and not antigo.eliminado_em:
                    antigo.eliminado_em = datetime.now()
            documento = Documento(pedido=pedido, tipo=tipo_doc, caminho=caminho,
                                  nome_original=arquivo.filename[:255], mime=mime, tamanho=tamanho)
            sessao.add(documento)


def _registrar_rascunho_ou_bloqueio(sessao, pedido, docs, erros, acao):
    if acao == "enviar":
        trilha.registrar(sessao, "envio_bloqueado", "solicitante", pedido_id=pedido.id,
                         faltas=sorted(erros))
    else:
        trilha.registrar(sessao, "rascunho_salvo", "solicitante", pedido_id=pedido.id)
    sessao.commit()
    pagina = render_template("titularidade/pedido.html", pedido=pedido, docs=docs, erros=erros,
                             salvo=not erros)
    codigo = 400 if erros else 200
    return pagina, codigo


def _faltas_para_envio(pedido, docs):
    faltas = {}
    if "identidade" not in docs:
        faltas["identidade"] = "Anexe o documento de identidade."
    if pedido.tipo_comprovante == "matricula" and "matricula" not in docs:
        faltas["matricula"] = "Anexe a certidão de matrícula."
    elif pedido.tipo_comprovante == "contrato":
        if "contrato" not in docs:
            faltas["contrato"] = "Anexe o contrato com firma reconhecida ou assinatura digital."
        if not pedido.data_chaves:
            faltas["data_chaves"] = "Informe a data da entrega das chaves."
    elif not pedido.tipo_comprovante:
        faltas["tipo_comprovante"] = "Escolha o comprovante: matrícula ou contrato."
    return faltas


def _enviar(sessao, pedido, docs, autor="solicitante"):
    agora = datetime.now()
    pedido.situacao = Situacao.A_CONFERIR
    pedido.recebido_em = agora
    pedido.numero = f"{agora.year}-{pedido.id:06d}"
    comprovante = docs.get(pedido.tipo_comprovante)
    if comprovante:
        sessao.add(AnaliseIA(documento_id=comprovante.id))
    trilha.registrar(sessao, "pedido_enviado", autor, pedido_id=pedido.id, numero=pedido.numero,
                     canal=pedido.canal, recebido_em=agora.isoformat(timespec="seconds"),
                     comprovante=pedido.tipo_comprovante,
                     unidade_localizada=pedido.unidade_id is not None)
    email.enviar(pedido.solicitante_email, f"Pedido {pedido.numero} recebido", "recebido",
                 pedido=pedido)
    if pedido.unidade_id:
        _avisar_proprietarios(sessao, pedido)


def _avisar_proprietarios(sessao, pedido):
    try:
        proprietarios = ERP().proprietarios(pedido.unidade_id)
    except ServicoIndisponivel:
        trilha.registrar(sessao, "aviso_falhou", "sistema", pedido_id=pedido.id,
                         motivo="ERP indisponível")
        return
    for proprietario in proprietarios:
        if not proprietario.get("email"):
            trilha.registrar(sessao, "aviso_falhou", "sistema", pedido_id=pedido.id,
                             proprietario=proprietario["nome"],
                             motivo="proprietário sem e-mail no ERP")
            continue
        token, hash_do_token = novo_token()
        aviso = AvisoProprietario(pedido_id=pedido.id, nome=proprietario["nome"],
                                  email=proprietario["email"], token_hash=hash_do_token)
        sessao.add(aviso)
        try:
            caminho_da_contestacao = url_for('contestacao.contestar', token=token)
            email.enviar(proprietario["email"],
                         "Aviso: pedido de mudança de titularidade da sua unidade",
                         "aviso_proprietario", nome=proprietario["nome"], pedido=pedido,
                         url=f"{config.URL_PUBLICA}{caminho_da_contestacao}")
        except (smtplib.SMTPException, OSError):
            trilha.registrar(sessao, "aviso_falhou", "sistema", pedido_id=pedido.id,
                             proprietario=proprietario["nome"], para=proprietario["email"],
                             motivo="falha no envio do e-mail")
            continue
        trilha.registrar(sessao, "aviso_enviado", "sistema", pedido_id=pedido.id,
                         para=proprietario["email"])


def _pedido_com_pendencia(sessao):
    resposta_na_sessao = session.get("resposta")
    if not resposta_na_sessao or datetime.fromisoformat(resposta_na_sessao["ate"]) < datetime.now():
        session.pop("resposta", None)
        return None
    return sessao.get(Pedido, resposta_na_sessao["id"])


@bp.route("/pendencia", methods=["GET", "POST"])
def responder():
    with Sessao() as sessao:
        pedido = _pedido_com_pendencia(sessao)
        if not pedido:
            return render_template("titularidade/link_invalido.html"), 410
        if pedido.situacao != Situacao.COM_PENDENCIA:
            return render_template("titularidade/nao_aceita_resposta.html", pedido=pedido), 410
        abertas = [pendencia for pendencia in pedido.pendencias
                   if pendencia.depende_solicitante and not pendencia.respondida_em]
        if request.method == "GET":
            return render_template("titularidade/pendencia.html", pedido=pedido, abertas=abertas,
                                   docs=_docs(pedido), erros={}, dados={})
        return _receber_resposta(sessao, pedido, abertas)


def _receber_resposta(sessao, pedido, abertas):
    formulario_enviado = request.form
    erros = {}
    mudancas = []
    gravados = []
    texto = (formulario_enviado.get("resposta") or "").strip()[:2000]
    _aplicar_correcoes(pedido, formulario_enviado, erros, mudancas)
    comprovante_novo = _gravar_arquivos_da_resposta(sessao, pedido, erros, mudancas, gravados)
    if not erros and not mudancas and not texto:
        erros["resposta"] = "Anexe um documento, corrija um dado ou escreva uma resposta."
    sessao.flush()
    docs = _docs(pedido)
    if not erros:
        erros = _faltas_para_envio(pedido, docs)
    if erros:
        return _desfazer_resposta(sessao, abertas, gravados, erros, texto)
    _concluir_resposta(sessao, pedido, abertas, docs, mudancas, comprovante_novo, texto)
    session.pop("resposta", None)
    return render_template("titularidade/resposta_recebida.html", pedido=pedido)


def _aplicar_correcoes(pedido, formulario_enviado, erros, mudancas):
    tipo = formulario_enviado.get("tipo_comprovante")
    if tipo in ("matricula", "contrato") and tipo != pedido.tipo_comprovante:
        pedido.tipo_comprovante = tipo
        mudancas.append("tipo_comprovante")
    chaves = formulario_enviado.get("data_chaves") or None
    if chaves:
        try:
            nova_data = date.fromisoformat(chaves)
            if nova_data != pedido.data_chaves:
                pedido.data_chaves = nova_data
                mudancas.append("data_chaves")
        except ValueError:
            erros["data_chaves"] = "Data inválida."


def _gravar_arquivos_da_resposta(sessao, pedido, erros, mudancas, gravados):
    comprovante_novo = None
    for campo, tipo_doc in CAMPOS_ARQUIVO.items():
        arquivo = request.files.get(campo)
        if arquivo and arquivo.filename:
            try:
                caminho, mime, tamanho = documentos.guardar(pedido.id, arquivo)
                gravados.append(caminho)
            except documentos.ArquivoInvalido as erro:
                erros[campo] = str(erro).capitalize() + "."
                continue
            for antigo in pedido.documentos:
                if antigo.tipo == tipo_doc and not antigo.eliminado_em:
                    antigo.eliminado_em = datetime.now()
            novo = Documento(pedido=pedido, tipo=tipo_doc, caminho=caminho,
                             nome_original=arquivo.filename[:255], mime=mime, tamanho=tamanho)
            sessao.add(novo)
            mudancas.append(campo)
            if campo != "identidade":
                comprovante_novo = campo
    return comprovante_novo


def _desfazer_resposta(sessao, abertas, gravados, erros, texto):
    sessao.rollback()
    for caminho in gravados:
        Path(caminho).unlink(missing_ok=True)
    pedido = sessao.get(Pedido, session["resposta"]["id"])
    pagina = render_template("titularidade/pendencia.html", pedido=pedido, abertas=abertas,
                             docs=_docs(pedido), erros=erros, dados={"resposta": texto})
    return pagina, 400


def _concluir_resposta(sessao, pedido, abertas, docs, mudancas, comprovante_novo, texto):
    agora = datetime.now()
    for pendencia in abertas:
        pendencia.respondida_em = agora
        pendencia.resposta = texto or None
    pedido.situacao = Situacao.A_CONFERIR
    pedido.documentacao_completa_em = agora
    comprovante = docs.get(pedido.tipo_comprovante)
    if comprovante and (comprovante_novo or "tipo_comprovante" in mudancas):
        sessao.add(AnaliseIA(documento_id=comprovante.id))
    trilha.registrar(sessao, "pendencia_respondida", "solicitante", pedido_id=pedido.id,
                     mudancas=mudancas, com_texto=bool(texto))
    sessao.commit()
