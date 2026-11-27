from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, abort, redirect, render_template, request, session, url_for

from .. import config
from ..gateways import ERP, Aplicativo, ServicoIndisponivel
from ..modelos import (AnaliseIA, Documento, Pedido, Sessao, Situacao, TentativaToken,
                       TipoDocumento, TipoPedido)
from ..servicos import conciliacao, documentos, email, trilha
from ..util import cpf_cnpj_valido, digitos, email_valido, formatar_cpf_cnpj

bp = Blueprint("proprietario", __name__, url_prefix="/condomino")
ABERTOS = (Situacao.A_CONFERIR, Situacao.COM_PENDENCIA)


def _bloqueado(sessao, ip):
    desde = datetime.now() - timedelta(minutes=config.TOKEN_BLOQUEIO_MINUTOS)
    tentativas_recentes = sessao.query(TentativaToken).filter(
        TentativaToken.ip == ip, TentativaToken.criada_em >= desde)
    return tentativas_recentes.count() >= config.TOKEN_TENTATIVAS


@bp.get("")
def inicio():
    if "proprietario" in session:
        rota = "proprietario.area"
    else:
        rota = "proprietario.entrar"
    return redirect(url_for(rota))


@bp.route("/entrar", methods=["GET", "POST"])
def entrar():
    destino = request.values.get("destino", "area")
    if request.method == "GET":
        return render_template("proprietario/entrar.html", destino=destino, erro=None, cpf="")
    ip = request.remote_addr or "?"
    cpf = (request.form.get("cpf_cnpj") or "").strip()
    with Sessao() as sessao:
        if _bloqueado(sessao, ip):
            trilha.registrar(sessao, "login_condomino_bloqueado", "sistema", ip=ip)
            sessao.commit()
            pagina = render_template("proprietario/bloqueado.html",
                                     minutos=config.TOKEN_BLOQUEIO_MINUTOS)
            return pagina, 429
        try:
            if cpf_cnpj_valido(cpf):
                pessoa = Aplicativo().autenticar(cpf, request.form.get("senha") or "")
            else:
                pessoa = None
        except ServicoIndisponivel:
            return render_template("proprietario/indisponivel.html"), 503
        if not pessoa:
            sessao.add(TentativaToken(ip=ip))
            trilha.registrar(sessao, "login_condomino_falhou", "sistema", ip=ip)
            sessao.commit()
            return render_template("proprietario/entrar.html", destino=destino, cpf=cpf,
                                   erro="CPF ou CNPJ e senha não conferem."), 401
        session.pop("proprietario", None)
        validade = datetime.now() + timedelta(hours=config.AREA_PROPRIETARIO_HORAS)
        session["proprietario"] = {"cpf_cnpj": pessoa["cpf_cnpj"], "nome": pessoa["nome"],
                                   "ate": validade.isoformat()}
        trilha.registrar(sessao, "area_condomino_acesso", f"condomino:{pessoa['cpf_cnpj']}")
        sessao.commit()
    if destino == "dados":
        rota = "proprietario.meus_dados"
    else:
        rota = "proprietario.area"
    return redirect(url_for(rota))


def exige_proprietario(visao):
    @wraps(visao)
    def interno(*args, **kwargs):
        acesso = session.get("proprietario")
        if not acesso or datetime.fromisoformat(acesso["ate"]) < datetime.now():
            session.pop("proprietario", None)
            return redirect(url_for("proprietario.entrar", expirou=1 if acesso else None))
        return visao(*args, **kwargs)
    return interno


def _eu():
    return session["proprietario"]


def _minhas_unidades():
    cpf = digitos(_eu()["cpf_cnpj"])
    unidades = ERP().unidades_do_responsavel(_eu()["cpf_cnpj"])
    return [unidade for unidade in unidades if _sou_proprietario_da_unidade(unidade, cpf)]


def _sou_proprietario_da_unidade(unidade, cpf):
    for responsavel in unidade["responsaveis"]:
        if digitos(responsavel["cpf_cnpj"]) == cpf and responsavel["papel"] == "proprietario":
            return True
    return False


def _unidade_minha(uid):
    encontrada = None
    for unidade in _minhas_unidades():
        if unidade["id"] == uid:
            encontrada = unidade
            break
    if not encontrada:
        abort(403)
    return encontrada


def _meu_contato(unidade):
    cpf = digitos(_eu()["cpf_cnpj"])
    contatos_com_meu_cpf = (responsavel for responsavel in unidade["responsaveis"]
                            if digitos(responsavel["cpf_cnpj"]) == cpf)
    return next(contatos_com_meu_cpf)


@bp.get("/area")
@exige_proprietario
def area():
    try:
        unidades = _minhas_unidades()
        condominios = {condominio["id"]: condominio["nome"] for condominio in ERP().condominios()}
        no_app = Aplicativo().condomino(_eu()["cpf_cnpj"]) or {"unidades": []}
        erp = ERP()
        rotulos = {}
        for unidade_no_app in no_app["unidades"]:
            unidade = erp.unidade(unidade_no_app["id"])
            if unidade:
                rotulos[unidade["id"]] = unidade
    except ServicoIndisponivel:
        return render_template("proprietario/indisponivel.html"), 503
    with Sessao() as sessao:
        cpf_formatado = formatar_cpf_cnpj(_eu()["cpf_cnpj"])
        tipos_de_locacao = [TipoPedido.INCLUSAO_LOCATARIO, TipoPedido.SAIDA_LOCATARIO]
        abertos = sessao.query(Pedido).filter(Pedido.solicitante_cpf_cnpj == cpf_formatado,
                                              Pedido.tipo.in_(tipos_de_locacao),
                                              Pedido.situacao.in_(ABERTOS)).all()
        com_pendencia = [pedido for pedido in abertos
                         if pedido.situacao == Situacao.COM_PENDENCIA]
        em_andamento = {_chave_em_andamento(pedido) for pedido in abertos}
    return render_template("proprietario/area.html", unidades=unidades, condominios=condominios,
                           no_app=no_app, rotulos=rotulos, com_pendencia=com_pendencia,
                           em_andamento=em_andamento, TipoPedido=TipoPedido)


def _chave_em_andamento(pedido):
    locatario = pedido.dados_informados.get("locatario") or {}
    return (pedido.unidade_id, locatario.get("cpf_cnpj"), pedido.tipo)


def _registrar(sessao, tipo, unidade, dados_locatario, arquivo=None, contato=None, autor=None,
               canal="digital"):
    solicitante = contato or _meu_contato(unidade)
    agora = datetime.now()
    pedido = Pedido(tipo=tipo, situacao=Situacao.A_CONFERIR, canal=canal,
                    condominio_id=unidade["condominio_id"], unidade_id=unidade["id"],
                    solicitante_nome=solicitante["nome"],
                    solicitante_cpf_cnpj=formatar_cpf_cnpj(solicitante["cpf_cnpj"]),
                    solicitante_email=solicitante.get("email") or "", recebido_em=agora,
                    dados_informados={"numero": unidade["numero"],
                                      "bloco": unidade.get("bloco") or "",
                                      "locatario": dados_locatario})
    sessao.add(pedido)
    sessao.flush()
    if arquivo:
        caminho, mime, tamanho = documentos.guardar(pedido.id, arquivo)
        documento = Documento(pedido=pedido, tipo=TipoDocumento.CONTRATO_LOCACAO, caminho=caminho,
                              mime=mime, tamanho=tamanho, nome_original=arquivo.filename[:255])
        sessao.add(documento)
        sessao.flush()
        sessao.add(AnaliseIA(documento_id=documento.id))
    pedido.numero = f"{agora.year}-{pedido.id:06d}"
    autor_do_registro = autor or f"proprietario:{pedido.solicitante_cpf_cnpj}"
    trilha.registrar(sessao, "pedido_enviado", autor_do_registro, pedido_id=pedido.id,
                     numero=pedido.numero, canal=canal,
                     recebido_em=agora.isoformat(timespec="seconds"), tipo_pedido=tipo.value)
    if pedido.solicitante_email:
        email.enviar(pedido.solicitante_email, f"Pedido {pedido.numero} recebido", "recebido",
                     pedido=pedido)
        trilha.registrar(sessao, "email_recebido", "sistema", pedido_id=pedido.id)
    return pedido


def _ja_em_andamento(sessao, tipo, uid, cpf_locatario):
    abertos_da_unidade = sessao.query(Pedido).filter(Pedido.tipo == tipo, Pedido.unidade_id == uid,
                                                Pedido.situacao.in_(ABERTOS))
    for pedido in abertos_da_unidade:
        locatario = pedido.dados_informados.get("locatario") or {}
        cpf_do_pedido = locatario.get("cpf_cnpj") or ""
        if digitos(cpf_do_pedido) == digitos(cpf_locatario):
            return pedido
    return None


@bp.route("/unidade/<int:uid>/incluir", methods=["GET", "POST"])
@exige_proprietario
def incluir(uid):
    try:
        unidade = _unidade_minha(uid)
    except ServicoIndisponivel:
        return render_template("proprietario/indisponivel.html"), 503
    if request.method == "GET":
        return render_template("proprietario/incluir.html", unidade=unidade, dados={}, erros={})
    formulario = request.form
    campos = ("nome", "cpf_cnpj", "email", "telefone")
    dados = {campo: (formulario.get(campo) or "").strip() for campo in campos}
    erros = _validar_locatario(dados, unidade)
    arquivo = request.files.get("contrato")
    if not arquivo or not arquivo.filename:
        erros["contrato"] = "Anexe o contrato de locação."
    if erros:
        pagina = render_template("proprietario/incluir.html", unidade=unidade, dados=dados,
                                 erros=erros)
        return pagina, 400
    dados["cpf_cnpj"] = formatar_cpf_cnpj(dados["cpf_cnpj"])
    with Sessao() as sessao:
        existente = _ja_em_andamento(sessao, TipoPedido.INCLUSAO_LOCATARIO, uid, dados["cpf_cnpj"])
        if existente:
            return render_template("proprietario/enviado.html", pedido=existente, repetido=True)
        try:
            pedido = _registrar(sessao, TipoPedido.INCLUSAO_LOCATARIO, unidade, dados, arquivo)
        except documentos.ArquivoInvalido as erro:
            sessao.rollback()
            erros["contrato"] = str(erro).capitalize() + "."
            pagina = render_template("proprietario/incluir.html", unidade=unidade, dados=dados,
                                     erros=erros)
            return pagina, 400
        sessao.commit()
        return render_template("proprietario/enviado.html", pedido=pedido)


def _validar_locatario(dados, unidade):
    erros = {}
    if len(dados["nome"].split()) < 2:
        erros["nome"] = "Informe o nome completo do locatário."
    if not cpf_cnpj_valido(dados["cpf_cnpj"]):
        erros["cpf_cnpj"] = "CPF ou CNPJ inválido."
    elif digitos(dados["cpf_cnpj"]) == digitos(_eu()["cpf_cnpj"]):
        erros["cpf_cnpj"] = "O locatário não pode ser o próprio proprietário."
    elif any(digitos(responsavel["cpf_cnpj"]) == digitos(dados["cpf_cnpj"])
             for responsavel in unidade["responsaveis"]):
        erros["cpf_cnpj"] = "Essa pessoa já é responsável pela unidade."
    if not email_valido(dados["email"]):
        erros["email"] = "E-mail inválido."
    return erros


@bp.route("/unidade/<int:uid>/saida/<int:indice>", methods=["GET", "POST"])
@exige_proprietario
def saida(uid, indice):
    try:
        unidade = _unidade_minha(uid)
    except ServicoIndisponivel:
        return render_template("proprietario/indisponivel.html"), 503
    locatarios = [responsavel for responsavel in unidade["responsaveis"]
                  if responsavel["papel"] == "locatario"]
    if indice >= len(locatarios):
        abort(404)
    locatario = locatarios[indice]
    if request.method == "GET":
        return render_template("proprietario/saida.html", unidade=unidade, locatario=locatario)
    campos = ("nome", "cpf_cnpj", "email", "telefone")
    dados = {campo: locatario.get(campo) for campo in campos}
    with Sessao() as sessao:
        existente = _ja_em_andamento(sessao, TipoPedido.SAIDA_LOCATARIO, uid, locatario["cpf_cnpj"])
        if existente:
            return render_template("proprietario/enviado.html", pedido=existente, repetido=True)
        pedido = _registrar(sessao, TipoPedido.SAIDA_LOCATARIO, unidade, dados)
        sessao.commit()
        return render_template("proprietario/enviado.html", pedido=pedido)


@bp.route("/pedido/<int:pid>/pendencia", methods=["GET", "POST"])
@exige_proprietario
def responder(pid):
    with Sessao() as sessao:
        pedido = sessao.get(Pedido, pid)
        if (not pedido
                or digitos(pedido.solicitante_cpf_cnpj) != digitos(_eu()["cpf_cnpj"])
                or pedido.tipo not in (TipoPedido.INCLUSAO_LOCATARIO, TipoPedido.SAIDA_LOCATARIO)):
            abort(404)
        if pedido.situacao != Situacao.COM_PENDENCIA:
            return render_template("titularidade/nao_aceita_resposta.html", pedido=pedido), 410
        abertas = [pendencia for pendencia in pedido.pendencias
                   if pendencia.depende_solicitante and not pendencia.respondida_em]
        if request.method == "GET":
            return render_template("proprietario/pendencia.html", pedido=pedido, abertas=abertas,
                                   erros={}, texto="")
        texto = (request.form.get("resposta") or "").strip()[:2000]
        arquivo = request.files.get("contrato")
        novo = None
        if arquivo and arquivo.filename:
            try:
                caminho, mime, tamanho = documentos.guardar(pedido.id, arquivo)
            except documentos.ArquivoInvalido as erro:
                erros = {"contrato": str(erro).capitalize() + "."}
                pagina = render_template("proprietario/pendencia.html", pedido=pedido,
                                         abertas=abertas, texto=texto, erros=erros)
                return pagina, 400
            novo = _substituir_contrato(sessao, pedido, arquivo, caminho, mime, tamanho)
        if not novo and not texto:
            erros = {"resposta": "Anexe o contrato ou escreva uma resposta."}
            pagina = render_template("proprietario/pendencia.html", pedido=pedido,
                                     abertas=abertas, texto=texto, erros=erros)
            return pagina, 400
        _concluir_resposta(sessao, pedido, abertas, novo, texto)
        return render_template("titularidade/resposta_recebida.html", pedido=pedido)


def _substituir_contrato(sessao, pedido, arquivo, caminho, mime, tamanho):
    for antigo in pedido.documentos:
        if antigo.tipo == TipoDocumento.CONTRATO_LOCACAO and not antigo.eliminado_em:
            antigo.eliminado_em = datetime.now()
    novo = Documento(pedido=pedido, tipo=TipoDocumento.CONTRATO_LOCACAO, caminho=caminho,
                     mime=mime, tamanho=tamanho, nome_original=arquivo.filename[:255])
    sessao.add(novo)
    sessao.flush()
    return novo


def _concluir_resposta(sessao, pedido, abertas, novo, texto):
    agora = datetime.now()
    for pendencia in abertas:
        pendencia.respondida_em = agora
        pendencia.resposta = texto or None
    pedido.situacao = Situacao.A_CONFERIR
    pedido.documentacao_completa_em = agora
    if novo:
        sessao.add(AnaliseIA(documento_id=novo.id))
    mudancas = ["contrato"] if novo else []
    trilha.registrar(sessao, "pendencia_respondida", f"proprietario:{pedido.solicitante_cpf_cnpj}",
                     pedido_id=pedido.id, mudancas=mudancas, com_texto=bool(texto))
    sessao.commit()


@bp.route("/meus-dados", methods=["GET", "POST"])
@exige_proprietario
def meus_dados():
    erros = {}
    alteradas = None
    try:
        if request.method == "POST":
            campos = ("email", "telefone", "endereco_correspondencia")
            dados = {campo: (request.form.get(campo) or "").strip() for campo in campos}
            if dados["email"] and not email_valido(dados["email"]):
                erros["email"] = "E-mail inválido."
            if not erros:
                resultado = Aplicativo().alterar_contato(_eu()["cpf_cnpj"], dados)
                alteradas = resultado["alteradas"]
                if alteradas:
                    with Sessao() as sessao:
                        try:
                            conciliacao.importar(sessao)
                        except ServicoIndisponivel:
                            sessao.rollback()
        eu = Aplicativo().condomino(_eu()["cpf_cnpj"])
    except ServicoIndisponivel:
        return render_template("proprietario/indisponivel.html"), 503
    pagina = render_template("proprietario/meus_dados.html", eu=eu, erros=erros,
                             alteradas=alteradas)
    codigo = 400 if erros else 200
    return pagina, codigo


@bp.get("/sair")
def sair():
    session.pop("proprietario", None)
    return render_template("proprietario/saiu.html")
