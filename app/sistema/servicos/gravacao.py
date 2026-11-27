from datetime import date, datetime


from .. import config
from ..gateways import ERP, Aplicativo, ServicoIndisponivel
from ..modelos import AnaliseIA, AvisoReemissao, Campo, Evento, Pedido, Situacao, TipoPedido
from ..util import cpf_cnpj_valido, digitos
from . import email, trilha


class AprovacaoBloqueada(Exception):
    pass


def bloqueios(sessao, pedido, unidade=None):
    mensagens = []
    _bloqueios_de_situacao(sessao, pedido, mensagens)
    campos = sessao.query(Campo).filter_by(pedido_id=pedido.id).all()
    _bloqueios_de_analise(sessao, pedido, campos, mensagens)
    _bloqueios_de_conferencia(campos, mensagens)
    _bloqueios_de_documentos(pedido, mensagens)
    if unidade:
        _bloqueios_de_responsaveis(sessao, pedido, unidade, mensagens)
    return mensagens


def _bloqueios_de_situacao(sessao, pedido, mensagens):
    if pedido.situacao != Situacao.A_CONFERIR:
        mensagens.append("Só pedido \"a conferir\" pode ser aprovado.")
    if pedido.aprovado_em:
        mensagens.append("O pedido já foi aprovado.")
    if not pedido.unidade_id:
        mensagens.append("A unidade informada não foi localizada no ERP.")
    if pedido.contestado:
        contestacao_analisada = (sessao.query(Evento)
                                 .filter_by(pedido_id=pedido.id, tipo="contestacao_analisada")
                                 .first())
        if not contestacao_analisada:
            mensagens.append("Pedido contestado: registre a análise da contestação "
                             "antes de aprovar (RD07).")
    if pedido.escalado:
        mensagens.append("Pedido escalado: aguarde a decisão do gerente da carteira (RF14).")


def _bloqueios_de_analise(sessao, pedido, campos, mensagens):
    ids_documentos = [documento.id for documento in pedido.documentos
                      if not documento.eliminado_em]
    analise_pendente = None
    if ids_documentos:
        analise_pendente = (sessao.query(AnaliseIA)
                            .filter(AnaliseIA.documento_id.in_(ids_documentos),
                                    AnaliseIA.status.in_(("fila", "processando")))
                            .first())
    if analise_pendente:
        mensagens.append("O documento ainda está em análise: aguarde os critérios para conferir.")
    elif not campos and _tem_documento_para_conferir(pedido):
        mensagens.append("A conferência não tem critérios: "
                         "use \"Analisar de novo\" na página do pedido.")


def _tem_documento_para_conferir(pedido):
    return any(documento.tipo.value in ("matricula", "compra_venda", "locacao")
               and not documento.eliminado_em
               for documento in pedido.documentos)


def _bloqueios_de_conferencia(campos, mensagens):
    faltam = [campo for campo in campos if not campo.conferencia]
    if faltam:
        quantidade = len(faltam)
        mensagens.append("Confira todos os critérios antes de aprovar: "
                         f"falta{'m' if quantidade > 1 else ''} {quantidade}.")
    if any(campo.conferencia == "nao_confere" for campo in campos):
        mensagens.append("Há critério marcado como \"não confere\": "
                         "registre uma pendência, recuse ou escale.")


def _bloqueios_de_documentos(pedido, mensagens):
    contrato_de_titularidade = (pedido.tipo == TipoPedido.TITULARIDADE
                                and pedido.tipo_comprovante == "contrato")
    if contrato_de_titularidade and not pedido.data_chaves:
        mensagens.append("Contrato sem data de entrega das chaves (RD05).")
    if pedido.tipo == TipoPedido.INCLUSAO_LOCATARIO and not _tem_contrato_de_locacao(pedido):
        mensagens.append("Inclusão de locatário sem contrato de locação: "
                         "registre uma pendência (RD03).")


def _tem_contrato_de_locacao(pedido):
    return any(documento.tipo.value == "locacao" and not documento.eliminado_em
               for documento in pedido.documentos)


def _bloqueios_de_responsaveis(sessao, pedido, unidade, mensagens):
    for responsavel in previa(sessao, pedido, unidade) or []:
        partes_do_nome = (responsavel.get("nome") or "").split()
        cpf_cnpj_do_responsavel = responsavel.get("cpf_cnpj") or ""
        if len(partes_do_nome) < 2 or not cpf_cnpj_valido(cpf_cnpj_do_responsavel):
            nome_exibido = responsavel.get('nome') or '(sem nome)'
            mensagens.append("Responsável sem nome completo ou com CPF/CNPJ inválido: "
                             f"{nome_exibido} (RNF10).")


def _data_extraida(sessao, pedido, campo):
    for documento in pedido.documentos:
        if documento.eliminado_em:
            continue
        analise = (sessao.query(AnaliseIA).filter_by(documento_id=documento.id, status="concluida")
                   .order_by(AnaliseIA.id.desc()).first())
        valor = None
        if analise:
            resultado = analise.resultado or {}
            extraido = resultado.get("extraido") or {}
            valor = extraido.get(campo)
        try:
            return date.fromisoformat(valor)
        except (TypeError, ValueError):
            continue
    return None


def _data_base(sessao, pedido):
    if pedido.tipo == TipoPedido.INCLUSAO_LOCATARIO:
        campo = "data_inicio"
    else:
        campo = "data_registro_venda"
    return _data_extraida(sessao, pedido, campo)


def montar_responsaveis(pedido, atuais, data_registro=None):
    if pedido.tipo == TipoPedido.CONCILIACAO:
        return _responsaveis_da_conciliacao(pedido, atuais)
    if pedido.tipo == TipoPedido.INCLUSAO_LOCATARIO:
        return _responsaveis_da_inclusao_de_locatario(pedido, atuais, data_registro)
    if pedido.tipo == TipoPedido.SAIDA_LOCATARIO:
        return _responsaveis_da_saida_de_locatario(pedido, atuais)
    return _responsaveis_da_titularidade(pedido, atuais, data_registro)


def _responsaveis_da_conciliacao(pedido, atuais):
    itens = pedido.dados_informados["conciliacao"]["itens"]
    novos = {item["campo"]: item["valor_novo"] for item in itens}
    alvo = digitos(pedido.solicitante_cpf_cnpj)
    return [dict(responsavel, **novos) if digitos(responsavel["cpf_cnpj"]) == alvo else responsavel
            for responsavel in atuais]


def _responsaveis_da_inclusao_de_locatario(pedido, atuais, data_registro):
    locatario = pedido.dados_informados["locatario"]
    ja_e_responsavel = any(digitos(responsavel["cpf_cnpj"]) == digitos(locatario["cpf_cnpj"])
                           for responsavel in atuais)
    if ja_e_responsavel:
        return list(atuais)
    desde = (data_registro or date.today()).isoformat()
    novo_locatario = {"nome": locatario["nome"], "cpf_cnpj": locatario["cpf_cnpj"],
                      "email": locatario.get("email"),
                      "telefone": locatario.get("telefone") or None,
                      "papel": "locatario", "pagador": False,
                      "desde": desde}
    return list(atuais) + [novo_locatario]


def _responsaveis_da_saida_de_locatario(pedido, atuais):
    alvo = digitos(pedido.dados_informados["locatario"]["cpf_cnpj"])
    return [responsavel for responsavel in atuais
            if not (responsavel["papel"] == "locatario"
                    and digitos(responsavel["cpf_cnpj"]) == alvo)]


def _responsaveis_da_titularidade(pedido, atuais, data_registro):
    cpf = digitos(pedido.solicitante_cpf_cnpj)
    pessoa = {"nome": pedido.solicitante_nome, "cpf_cnpj": pedido.solicitante_cpf_cnpj,
              "email": pedido.solicitante_email, "telefone": None}
    if pedido.tipo_comprovante == "matricula":
        locatarios = [dict(responsavel, pagador=False) for responsavel in atuais
                      if responsavel["papel"] == "locatario"
                      and digitos(responsavel["cpf_cnpj"]) != cpf]
        desde = (data_registro or date.today()).isoformat()
        novo_proprietario = dict(pessoa, papel="proprietario", pagador=True, desde=desde)
        return [novo_proprietario] + locatarios
    if pedido.tipo_comprovante == "contrato":
        outros = [responsavel for responsavel in atuais
                  if digitos(responsavel["cpf_cnpj"]) != cpf]
        novo_comprador = dict(pessoa, papel="comprador", pagador=False,
                              desde=pedido.data_chaves.isoformat())
        return outros + [novo_comprador]
    raise AprovacaoBloqueada("Pedido de titularidade sem comprovante definido.")


def previa(sessao, pedido, unidade):
    if not unidade:
        return None
    try:
        responsaveis_atuais = unidade.get("responsaveis", [])
        data_base = _data_base(sessao, pedido)
        return montar_responsaveis(pedido, responsaveis_atuais, data_base)
    except AprovacaoBloqueada:
        return None


def aprovar(sessao, pedido, usuario):
    try:
        if pedido.unidade_id:
            unidade = ERP().unidade(pedido.unidade_id)
        else:
            unidade = None
    except ServicoIndisponivel:
        unidade = None
    mensagens = bloqueios(sessao, pedido, unidade)
    if mensagens:
        raise AprovacaoBloqueada(" ".join(mensagens))
    pedido.aprovado_em = datetime.now()
    pedido.aprovado_por = usuario["id"]
    trilha.registrar(sessao, "pedido_aprovado", usuario["email"], pedido_id=pedido.id)
    return gravar(sessao, pedido)


def _calcular(sessao, pedido, autor):
    unidade = ERP().unidade(pedido.unidade_id)
    data_base = _data_base(sessao, pedido)
    pedido.responsaveis_novos = montar_responsaveis(pedido, unidade["responsaveis"], data_base)
    trilha.registrar(sessao, "responsaveis_calculados", autor, pedido_id=pedido.id,
                     antes=unidade["responsaveis"], depois=pedido.responsaveis_novos)


def gravar(sessao, pedido, autor="sistema"):
    if not pedido.aprovado_em or pedido.situacao != Situacao.A_CONFERIR:
        return False
    servico = "ERP"
    try:
        if pedido.responsaveis_novos is None:
            _calcular(sessao, pedido, autor)
        if not pedido.gravado_erp_em:
            ERP().gravar_responsaveis(pedido.unidade_id, pedido.responsaveis_novos)
            pedido.gravado_erp_em = datetime.now()
            trilha.registrar(sessao, "gravado_erp", autor, pedido_id=pedido.id)
        servico = "aplicativo"
        if not pedido.gravado_app_em and pedido.tipo != TipoPedido.CONCILIACAO:
            Aplicativo().gravar_responsaveis(pedido.unidade_id, pedido.responsaveis_novos)
            pedido.gravado_app_em = datetime.now()
            trilha.registrar(sessao, "gravado_aplicativo", autor, pedido_id=pedido.id)
    except ServicoIndisponivel as erro:
        _registrar_falha_de_gravacao(sessao, pedido, autor, servico, erro)
        return False
    pedido.erro_gravacao = None
    _concluir(sessao, pedido)
    return True


def _registrar_falha_de_gravacao(sessao, pedido, autor, servico, erro):
    primeira_falha = not pedido.erro_gravacao
    pedido.erro_gravacao = f"{servico}: {erro}"
    trilha.registrar(sessao, "gravacao_falhou", autor, pedido_id=pedido.id, servico=servico)
    if primeira_falha:
        assunto = f"Integração com o {servico} falhou"
        email.enviar(config.EMAIL_TECNOLOGIA, assunto, "alerta_tecnologia",
                     pedido=pedido, servico=servico, erro=str(erro))
        trilha.registrar(sessao, "alerta_tecnologia", "sistema", pedido_id=pedido.id,
                         servico=servico)


def _concluir(sessao, pedido):
    pedido.situacao = Situacao.CONCLUIDO
    pedido.concluido_em = datetime.now()
    trilha.registrar(sessao, "pedido_concluido", "sistema", pedido_id=pedido.id)

    if pedido.tipo_comprovante == "contrato":
        pedido.aguardando_matricula = True
        trilha.registrar(sessao, "marcacao_aplicada", "sistema", pedido_id=pedido.id,
                         marcacao="aguardando_matricula")
    elif pedido.tipo_comprovante == "matricula":
        _retirar_marcacoes_anteriores(sessao, pedido)

    if pedido.tipo != TipoPedido.CONCILIACAO:
        assunto = f"Pedido {pedido.numero} concluído"
        email.enviar(pedido.solicitante_email, assunto, "concluido", pedido=pedido)
        trilha.registrar(sessao, "email_concluido", "sistema", pedido_id=pedido.id)
    _avisar_reemissao(sessao, pedido)


def _retirar_marcacoes_anteriores(sessao, pedido):
    cpf = digitos(pedido.solicitante_cpf_cnpj)
    consulta = sessao.query(Pedido).filter(Pedido.unidade_id == pedido.unidade_id,
                                           Pedido.id != pedido.id,
                                           Pedido.aguardando_matricula.is_(True))
    for anterior in consulta.all():
        if digitos(anterior.solicitante_cpf_cnpj) == cpf:
            anterior.aguardando_matricula = False
            motivo = f"matrícula concluída no pedido {pedido.numero}"
            trilha.registrar(sessao, "marcacao_retirada", "sistema", pedido_id=anterior.id,
                             marcacao="aguardando_matricula", motivo=motivo)


def _avisar_reemissao(sessao, pedido):
    novo_pagador = next((responsavel for responsavel in pedido.responsaveis_novos
                         if responsavel.get("pagador")), None)
    eventos = sessao.query(Evento).filter_by(pedido_id=pedido.id, tipo="responsaveis_calculados")
    antes = next((evento.detalhe.get("antes") for evento in eventos), [])
    pagador_antes = next((responsavel for responsavel in antes or []
                          if responsavel.get("pagador")), None)
    if not novo_pagador:
        return
    if pagador_antes and digitos(pagador_antes["cpf_cnpj"]) == digitos(novo_pagador["cpf_cnpj"]):
        return
    try:
        emitido = ERP().emissao(pedido.condominio_id)["emitido_no_mes"]
        verificado = True
    except (ServicoIndisponivel, TypeError, KeyError):
        emitido = True
        verificado = False
    if not emitido:
        return
    numero = pedido.dados_informados.get("numero")
    bloco = pedido.dados_informados.get("bloco")
    descricao = (f"Unidade {numero}{' bloco ' + bloco if bloco else ''}: "
                 f"reemitir o boleto do mês em nome de "
                 f"{novo_pagador['nome']} ({novo_pagador['cpf_cnpj']}).")
    aviso = AvisoReemissao(pedido_id=pedido.id, descricao=descricao, emissao_verificada=verificado)
    sessao.add(aviso)
    trilha.registrar(
        sessao, "aviso_reemissao", "sistema", pedido_id=pedido.id, verificado=verificado
    )
