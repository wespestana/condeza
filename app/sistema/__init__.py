import json
import mimetypes
import requests
from flask import Flask, has_request_context, render_template, url_for
from sqlalchemy import text

from . import config
from .modelos import motor


mimetypes.add_type("font/woff2", ".woff2")

ROTULOS = {
    "titularidade": "mudança de titularidade",
    "inclusao_locatario": "inclusão de locatário",
    "conferente": "Cadastro e Emissão",
    "gerente": "Gerente da carteira",
    "tecnologia": "Tecnologia",
    "saida_locatario": "saída de locatário",
    "conciliacao": "conciliação de contato",
    "rascunho": "rascunho",
    "a_conferir": "a conferir",
    "com_pendencia": "com pendência",
    "concluido": "concluído",
    "recusado": "recusado",
    "proprietario": "proprietário",
    "locatario": "locatário",
    "identidade": "identidade",
    "matricula": "matrícula",
    "compra_venda": "contrato de compra e venda",
    "locacao": "contrato de locação",
    "contrato": "contrato de compra e venda",
}

ROTULOS_EVENTO = {
    "alerta_tecnologia": "Alerta à tecnologia",
    "alteracao_ignorada": "Alteração ignorada",
    "analise_ia_concluida": "Análise da IA concluída",
    "analise_ia_dispensada": "Análise da IA dispensada",
    "analise_ia_sem_indice": "Análise da IA sem índice",
    "area_condomino_acesso": "Acesso à Área do condômino",
    "aviso_enviado": "Aviso enviado",
    "aviso_falhou": "Falha no envio do aviso",
    "aviso_reemissao": "Aviso de reemissão de boleto",
    "conciliacao_recebida": "Alteração de contato recebida",
    "contestacao_analisada": "Contestação analisada",
    "contestado": "Pedido contestado",
    "criterio_conferido": "Critério conferido",
    "documento_aberto": "Documento aberto",
    "documentos_eliminados": "Documentos eliminados",
    "email_conciliacao_recusada": "E-mail de alteração não aceita",
    "email_concluido": "E-mail de conclusão",
    "email_gerente": "E-mail ao gerente",
    "email_pendencia": "E-mail de pendência",
    "email_recebido": "E-mail de recebimento",
    "email_recusado": "E-mail de recusa",
    "envio_bloqueado": "Envio bloqueado",
    "escalonamento_decidido": "Escalonamento decidido",
    "gravacao_falhou": "Falha na gravação",
    "gravado_aplicativo": "Gravado no aplicativo",
    "gravado_erp": "Gravado no ERP",
    "lembrete_matricula": "Lembrete da matrícula",
    "link_limite": "Limite de links atingido",
    "link_solicitado": "Link solicitado",
    "link_usado": "Link usado",
    "login": "Entrada na área da equipe",
    "login_falhou": "Entrada na área da equipe recusada",
    "segundo_fator_falhou": "Código do segundo fator recusado",
    "login_condomino_falhou": "Entrada na Área do condômino recusada",
    "login_condomino_bloqueado": "Área do condômino bloqueada",
    "marcacao_aplicada": "Marcação aplicada",
    "marcacao_retirada": "Marcação retirada",
    "pedido_aprovado": "Pedido aprovado",
    "pedido_concluido": "Pedido concluído",
    "pedido_encerrado_por_prazo": "Pedido encerrado por prazo",
    "pedido_enviado": "Pedido enviado",
    "pedido_escalado": "Pedido escalado ao gerente",
    "pedido_recusado": "Pedido recusado",
    "pendencia_registrada": "Pendência registrada",
    "pendencia_respondida": "Pendência respondida",
    "rascunho_eliminado": "Rascunho eliminado",
    "rascunho_salvo": "Rascunho salvo",
    "reanalise_pedida": "Nova análise pedida",
    "reemissao_resolvida": "Boleto reemitido",
    "registrado_assistido": "Registrado em atendimento assistido",
    "responsaveis_calculados": "Responsáveis calculados",
    "conta_criada": "Conta da equipe criada",
    "conta_ativada": "Conta da equipe ativada",
    "conta_desativada": "Conta da equipe desativada",
    "conta_reativada": "Conta da equipe reativada",
    "acesso_redefinido": "Acesso redefinido (novo link de ativação)",
}
CHAVES_DETALHE = {
    "criterio": "critério",
    "observacao": "observação",
    "indice": "índice",
    "carga_modelo": "carga do modelo",
    "alteracao": "alteração",
    "alteracoes": "alterações",
    "numero": "número",
    "descricao": "descrição",
    "decisao": "decisão",
    "duvida": "dúvida",
    "analise": "análise",
    "consultou_juridico": "consultou o jurídico",
    "unidade_localizada": "unidade localizada",
    "recebido_em": "recebido em",
    "depende_solicitante": "depende do solicitante",
}
VALORES_DETALHE = {
    "conferente": "Cadastro e Emissão",
    "gerente": "Gerente da carteira",
    "tecnologia": "Tecnologia",
    "confere": "confere",
    "nao_confere": "não confere",
    "tempo_esgotado": "tempo-limite atingido",
}


def _configurar_seguranca(app):
    app.config.update(
        SECRET_KEY=config.SECRET_KEY,
        MAX_CONTENT_LENGTH=15 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.PRODUCAO,
    )
    if config.PRODUCAO:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _registrar_rota_saude(app):
    @app.get("/saude")
    def saude():
        def checa(nome, verificacao):
            try:
                return nome, verificacao()
            except Exception as erro:
                return nome, f"falhou: {type(erro).__name__}"

        def verificar_banco():
            conexao = motor.connect()
            resultado = conexao.execute(text("select 1")).scalar()
            return resultado == 1 and "ok"

        def verificar_erp():
            resposta = requests.get(f"{config.ERP_URL}/saude", timeout=3)
            return resposta.json()["ok"] and "ok"

        def verificar_aplicativo():
            resposta = requests.get(f"{config.APP_URL}/saude", timeout=3)
            return resposta.json()["ok"] and "ok"

        def verificar_ia():
            resposta = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
            modelos_instalados = [modelo["name"] for modelo in resposta.json()["models"]]
            return config.OLLAMA_MODELO in modelos_instalados and "ok"

        itens = dict([
            checa("banco", verificar_banco),
            checa("erp-simulado", verificar_erp),
            checa("app-simulado", verificar_aplicativo),
            checa("ia-local", verificar_ia),
        ])
        return render_template("saude.html", itens=itens, ia_habilitada=config.IA_HABILITADA)


def _registrar_blueprints(app):
    from .rotas import assistido, contas, contestacao, equipe, proprietario, site, titularidade
    app.register_blueprint(site.bp)
    app.register_blueprint(assistido.bp)
    app.register_blueprint(titularidade.bp)
    app.register_blueprint(proprietario.bp)
    app.register_blueprint(contestacao.bp)
    app.register_blueprint(equipe.bp)
    app.register_blueprint(contas.bp)


def _registrar_links_da_conta(app):
    @app.context_processor
    def links_da_conta():
        if not has_request_context():
            return {}
        return {
            "url_entrar_proprietario": url_for("proprietario.entrar"),
            "url_meus_dados": url_for("proprietario.entrar", destino="dados"),
        }


def _registrar_filtros(app):
    @app.template_filter("rotulo")
    def rotulo(valor):
        chave = getattr(valor, "value", valor)
        texto_padrao = str(chave or "").replace("_", " ")
        return ROTULOS.get(chave, texto_padrao)

    @app.template_filter("detalhe")
    def detalhe(dados):
        if not isinstance(dados, dict):
            return str(dados)

        def fmt(valor):
            if isinstance(valor, bool):
                return "sim" if valor else "não"
            if valor is None:
                return "—"
            if isinstance(valor, (list, tuple)):
                return ", ".join(fmt(item) for item in valor)
            if isinstance(valor, dict):
                return json.dumps(valor, ensure_ascii=False)
            texto = str(valor)
            return VALORES_DETALHE.get(texto) or ROTULOS.get(texto) or texto

        from .ia.indice import ROTULOS as CRITERIOS

        def formatar_par(chave, valor):
            nome_chave = CHAVES_DETALHE.get(chave, chave.replace("_", " "))
            if chave == "criterio":
                texto_valor = CRITERIOS.get(valor, valor)
            else:
                texto_valor = fmt(valor)
            return f"{nome_chave}: {texto_valor}"

        return " · ".join(formatar_par(chave, valor) for chave, valor in dados.items())

    @app.template_filter("evento")
    def evento(tipo):
        return ROTULOS_EVENTO.get(tipo, tipo.replace("_", " "))


def _criar_tratador_de_erro(codigo_http):
    def tratar_erro(erro):
        pagina = render_template("erro.html", codigo=codigo_http)
        return pagina, codigo_http
    return tratar_erro


def _registrar_paginas_de_erro(app):
    for codigo in (403, 404, 405, 500):
        tratador = _criar_tratador_de_erro(codigo)
        app.register_error_handler(codigo, tratador)


def _registrar_rota_suporte(app):
    @app.get("/suporte")
    def suporte():
        return render_template("suporte.html")


def create_app():
    app = Flask(__name__)
    _configurar_seguranca(app)
    _registrar_rota_saude(app)
    _registrar_blueprints(app)
    _registrar_links_da_conta(app)
    _registrar_filtros(app)
    _registrar_paginas_de_erro(app)
    _registrar_rota_suporte(app)
    return app
