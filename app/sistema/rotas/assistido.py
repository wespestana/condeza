from datetime import date
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..gateways import ERP, ServicoIndisponivel
from ..modelos import Documento, Pedido, Sessao, Situacao, TipoPedido
from ..servicos import documentos, trilha
from ..util import cpf_cnpj_valido, digitos, email_valido, formatar_cpf_cnpj
from . import proprietario, titularidade
from .equipe import so_conferente

bp = Blueprint("assistido", __name__, url_prefix="/equipe/assistido")
TIPOS = {"titularidade": "Mudança de titularidade", "inclusao_locatario": "Inclusão de locatário",
         "saida_locatario": "Saída de locatário"}


def _form(tipo, dados, erros, status=200):
    try:
        condominios = ERP().condominios()
    except ServicoIndisponivel:
        flash("O ERP não respondeu. O atendimento assistido precisa consultar o cadastro.", "erro")
        return redirect(url_for("equipe.esteira"))
    return render_template("equipe/assistido.html", tipo=tipo, tipos=TIPOS, condominios=condominios,
                           dados=dados, erros=erros), status


@bp.get("")
@so_conferente
def novo():
    tipo = request.args.get("tipo")
    return _form(tipo if tipo in TIPOS else None, {}, {})


@bp.post("")
@so_conferente
def registrar():
    formulario = request.form
    tipo = formulario.get("tipo")
    if tipo not in TIPOS:
        return redirect(url_for("assistido.novo"))
    dados = {chave: (formulario.get(chave) or "").strip() for chave in formulario.keys()}
    erros = {}
    if not dados.get("condominio_id", "").isdigit():
        erros["condominio_id"] = "Escolha o condomínio."
    if not dados.get("numero"):
        erros["numero"] = "Informe o número da unidade."
    if not cpf_cnpj_valido(dados.get("cpf_cnpj", "")):
        erros["cpf_cnpj"] = "CPF ou CNPJ inválido."
    if erros:
        return _form(tipo, dados, erros, 400)
    autor = session["usuario"]["email"]
    try:
        condominio_id = int(dados["condominio_id"])
        bloco = dados.get("bloco", "").upper() or None
        unidade = ERP().localizar_unidade(condominio_id, dados["numero"], bloco)
        if unidade:
            unidade = ERP().unidade(unidade["id"])
    except ServicoIndisponivel:
        flash("O ERP não respondeu. Tente de novo.", "erro")
        return _form(tipo, dados, {}, 503)
    if tipo == "titularidade":
        return _titularidade(dados, unidade, autor)
    return _locatario(tipo, dados, unidade, autor)


def _titularidade(dados, unidade, autor):
    erros = {}
    if len(dados.get("nome", "").split()) < 2:
        erros["nome"] = "Informe o nome completo."
    if not email_valido(dados.get("email", "")):
        erros["email"] = "E-mail inválido."
    if erros:
        return _form("titularidade", dados, erros, 400)
    gravados = []
    with Sessao() as sessao:
        pedido = _novo_pedido_de_titularidade(dados, unidade, erros)
        sessao.add(pedido)
        sessao.flush()
        _guardar_arquivos_de_titularidade(sessao, pedido, erros, gravados)
        sessao.flush()
        documentos_do_pedido = titularidade._docs(pedido)
        if not erros:
            erros = titularidade._faltas_para_envio(pedido, documentos_do_pedido)
        if erros:
            sessao.rollback()
            for caminho in gravados:
                Path(caminho).unlink(missing_ok=True)
            return _form("titularidade", dados, erros, 400)
        trilha.registrar(sessao, "registrado_assistido", autor, pedido_id=pedido.id)
        titularidade._enviar(sessao, pedido, documentos_do_pedido, autor=autor)
        sessao.commit()
        flash(f"Pedido {pedido.numero} registrado em atendimento assistido.", "ok")
        return redirect(url_for("equipe.detalhe", pedido_id=pedido.id))


def _novo_pedido_de_titularidade(dados, unidade, erros):
    pedido = Pedido(tipo=TipoPedido.TITULARIDADE, canal="assistido",
                    condominio_id=int(dados["condominio_id"]),
                    unidade_id=unidade["id"] if unidade else None,
                    solicitante_nome=dados["nome"],
                    solicitante_cpf_cnpj=formatar_cpf_cnpj(dados["cpf_cnpj"]),
                    solicitante_email=dados["email"],
                    dados_informados={"numero": dados["numero"],
                                      "bloco": dados.get("bloco", "").upper()})
    comprovante_informado = dados.get("tipo_comprovante")
    comprovante_aceito = comprovante_informado in ("matricula", "contrato")
    pedido.tipo_comprovante = comprovante_informado if comprovante_aceito else None
    try:
        data_informada = dados.get("data_chaves")
        data_chaves = date.fromisoformat(dados["data_chaves"]) if data_informada else None
        pedido.data_chaves = data_chaves
    except ValueError:
        erros["data_chaves"] = "Data inválida."
    return pedido


def _guardar_arquivos_de_titularidade(sessao, pedido, erros, gravados):
    for campo, tipo_documento in titularidade.CAMPOS_ARQUIVO.items():
        arquivo = request.files.get(campo)
        if arquivo and arquivo.filename:
            try:
                caminho, mime, tamanho = documentos.guardar(pedido.id, arquivo)
                gravados.append(caminho)
            except documentos.ArquivoInvalido as erro:
                erros[campo] = str(erro).capitalize() + "."
                continue
            sessao.add(Documento(pedido=pedido, tipo=tipo_documento, caminho=caminho,
                                 nome_original=arquivo.filename[:255],
                                 mime=mime, tamanho=tamanho))


def _locatario(tipo, dados, unidade, autor):
    erros = {}
    dono = _proprietario_informado(dados, unidade)
    if not unidade:
        erros["numero"] = "Unidade não localizada no ERP."
    elif not dono:
        erros["cpf_cnpj"] = ("Esta pessoa não é proprietária da unidade no ERP: "
                             "só o proprietário inclui ou retira locatário.")
    if erros:
        return _form(tipo, dados, erros, 400)
    with Sessao() as sessao:
        if tipo == "inclusao_locatario":
            pedido, resposta_de_erro = _incluir_locatario(sessao, tipo, dados, unidade, autor, dono)
        else:
            pedido, resposta_de_erro = _retirar_locatario(sessao, tipo, dados, unidade, autor, dono)
        if resposta_de_erro is not None:
            return resposta_de_erro
        trilha.registrar(sessao, "registrado_assistido", autor, pedido_id=pedido.id)
        sessao.commit()
        flash(f"Pedido {pedido.numero} registrado em atendimento assistido.", "ok")
        return redirect(url_for("equipe.detalhe", pedido_id=pedido.id))


def _proprietario_informado(dados, unidade):
    responsaveis = (unidade or {}).get("responsaveis", [])
    return next((responsavel for responsavel in responsaveis
                 if responsavel["papel"] == "proprietario"
                 and digitos(responsavel["cpf_cnpj"]) == digitos(dados["cpf_cnpj"])), None)


def _incluir_locatario(sessao, tipo, dados, unidade, autor, dono):
    erros = {}
    campos_do_locatario = ("nome", "cpf_cnpj", "email", "telefone")
    locatario = {campo: dados.get(f"loc_{campo}", "") for campo in campos_do_locatario}
    if len(locatario["nome"].split()) < 2:
        erros["loc_nome"] = "Informe o nome completo do locatário."
    if not cpf_cnpj_valido(locatario["cpf_cnpj"]):
        erros["loc_cpf_cnpj"] = "CPF ou CNPJ do locatário inválido."
    elif any(digitos(responsavel["cpf_cnpj"]) == digitos(locatario["cpf_cnpj"])
             for responsavel in unidade["responsaveis"]):
        erros["loc_cpf_cnpj"] = "Essa pessoa já é responsável pela unidade."
    if not email_valido(locatario["email"]):
        erros["loc_email"] = "E-mail do locatário inválido."
    arquivo = request.files.get("contrato")
    if not arquivo or not arquivo.filename:
        erros["contrato"] = "Anexe o contrato de locação."
    if erros:
        return None, _form(tipo, dados, erros, 400)
    locatario["cpf_cnpj"] = formatar_cpf_cnpj(locatario["cpf_cnpj"])
    try:
        pedido = proprietario._registrar(sessao, TipoPedido.INCLUSAO_LOCATARIO, unidade,
                                         locatario, arquivo, contato=dono,
                                         autor=autor, canal="assistido")
    except documentos.ArquivoInvalido as erro:
        sessao.rollback()
        erros_do_arquivo = {"contrato": str(erro).capitalize() + "."}
        return None, _form(tipo, dados, erros_do_arquivo, 400)
    return pedido, None


def _retirar_locatario(sessao, tipo, dados, unidade, autor, dono):
    alvo = next((responsavel for responsavel in unidade["responsaveis"]
                 if responsavel["papel"] == "locatario"
                 and digitos(responsavel["cpf_cnpj"]) == digitos(dados.get("loc_cpf_cnpj", ""))),
                None)
    if not alvo:
        erros = {"loc_cpf_cnpj": "Não há locatário com esse CPF ou CNPJ nesta unidade."}
        return None, _form(tipo, dados, erros, 400)
    campos_do_locatario = ("nome", "cpf_cnpj", "email", "telefone")
    locatario = {campo: alvo.get(campo) for campo in campos_do_locatario}
    pedido = proprietario._registrar(sessao, TipoPedido.SAIDA_LOCATARIO, unidade, locatario,
                                     contato=dono, autor=autor, canal="assistido")
    return pedido, None
