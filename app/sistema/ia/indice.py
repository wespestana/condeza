from dataclasses import dataclass
from datetime import date, timedelta

from .. import config
from ..util import cpf_cnpj_valido
from .comparacao import chave_unidade, localizar_evidencia, normalizar

PESO = 1

ROTULOS = {
    "unidade": "Unidade do documento é a do pedido",
    "condominio": "Condomínio do documento é o do pedido",
    "adquirente_solicitante": "Solicitante é adquirente no último registro",
    "comprador_solicitante": "Solicitante é comprador no contrato",
    "transmitentes_proprietarios": "Vendedores no registro são os proprietários no cadastro",
    "vendedores_proprietarios": "Vendedores no contrato são os proprietários no cadastro",
    "venda_registrada": "Venda registrada na matrícula",
    "certidao_recente": f"Certidão emitida há até {config.CERTIDAO_VALIDADE_DIAS} dias",
    "assinatura_valida": "Firma reconhecida ou assinatura digital ICP-Brasil",
    "data_chaves": "Data das chaves igual à informada",
    "locador_proprietario": "Proprietário que pede é locador no contrato",
    "locatario_informado": "Locatário informado está no contrato",
    "contrato_em_vigor": "Contrato de locação em vigor",
}


NATUREZA = {
    "unidade": "vinculo", "condominio": "vinculo",
    "adquirente_solicitante": "vinculo", "comprador_solicitante": "vinculo",
    "transmitentes_proprietarios": "vinculo", "vendedores_proprietarios": "vinculo",
    "venda_registrada": "vinculo",
    "locador_proprietario": "vinculo", "locatario_informado": "vinculo",
    "certidao_recente": "documental",
    "assinatura_valida": "documental",
    "data_chaves": "documental",
    "contrato_em_vigor": "documental",
}

MOTIVO_NAO_COMPROVA = {
    "titularidade": (
        "Após a análise, os documentos apresentados não comprovam a titularidade do solicitante."
    ),
    "inclusao_locatario": (
        "Após a análise, os documentos apresentados não comprovam a locação informada."
    ),
}

PENDENCIA_SUGERIDA = {
    "certidao_recente": (
        f"Enviar certidão da matrícula emitida há até {config.CERTIDAO_VALIDADE_DIAS} dias."
    ),
    "assinatura_valida": (
        "Enviar o contrato com firma reconhecida ou com assinatura digital ICP-Brasil."
    ),
    "data_chaves": (
        "Confirmar a data de entrega das chaves: a informada no pedido difere da que consta "
        "no contrato."
    ),
    "contrato_em_vigor": "Enviar o contrato de locação em vigor: o enviado já terminou.",
}


def sugestao(campos, tipo_pedido="titularidade"):
    if not campos:
        return None
    if any(campo.atendido is None for campo in campos):
        return None
    falhas = [campo.nome for campo in campos if not campo.atendido]
    vinculo = [nome for nome in falhas if NATUREZA.get(nome) == "vinculo"]
    if vinculo:
        texto = MOTIVO_NAO_COMPROVA.get(str(tipo_pedido), MOTIVO_NAO_COMPROVA["titularidade"])
        return {"tipo": "nao_comprova", "criterios": vinculo, "texto": texto}
    if falhas:
        texto = "\n".join(PENDENCIA_SUGERIDA[nome] for nome in falhas if nome in PENDENCIA_SUGERIDA)
        return {"tipo": "pendencia", "criterios": falhas, "texto": texto}
    return {"tipo": "sem_divergencia", "criterios": [], "texto": ""}


def sugestao_conferencia(campos, tipo_pedido="titularidade"):
    if not campos:
        return None
    faltam = [campo.nome for campo in campos if not campo.conferencia]
    if faltam:
        return {"tipo": "incompleta", "criterios": faltam, "texto": ""}
    respostas = [
        type("Resposta", (), {"nome": campo.nome, "atendido": campo.conferencia == "confere"})()
        for campo in campos
    ]
    return sugestao(respostas, tipo_pedido)


@dataclass
class Criterio:
    nome: str
    informado: str | None
    cadastro: str | None
    extraido: str | None
    evidencia: str | None
    atendido: bool | None
    peso: int

    @property
    def rotulo(self):
        return ROTULOS[self.nome]


def _pessoas(lista):
    descricoes = (f"{pessoa.get('nome')} ({pessoa.get('cpf_cnpj')})" for pessoa in lista or [])
    return "; ".join(descricoes) or None


def _validas(lista):
    boas = [pessoa for pessoa in lista or [] if cpf_cnpj_valido(pessoa.get("cpf_cnpj") or "")]
    return boas, [pessoa for pessoa in lista or [] if pessoa not in boas]


def _mostrar(boas, descartadas):
    texto = _pessoas(boas) or ""
    if descartadas:
        texto += f" [descartado, número não é CPF/CNPJ válido: {_pessoas(descartadas)}]"
    return texto.strip() or None


def _cpfs(lista):
    return {
        normalizar("cpf_cnpj", pessoa.get("cpf_cnpj"))
        for pessoa in lista or []
        if pessoa.get("cpf_cnpj")
    }


def _evid_pessoas(lista, texto):
    trechos = [
        localizar_evidencia("cpf_cnpj", pessoa.get("cpf_cnpj"), texto)
        for pessoa in lista or []
    ]
    return " | ".join(trechos) if trechos and all(trechos) else None


def _somar_meses(data, meses):
    total_meses = data.month - 1 + meses
    ano = data.year + total_meses // 12
    mes = total_meses % 12 + 1
    fevereiro = 29 if ano % 4 == 0 and (ano % 100 or ano % 400 == 0) else 28
    dias = [31, fevereiro, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(ano, mes, min(data.day, dias[mes - 1]))


def _avaliar_matricula(
    adicionar, extraido, pedido, texto, solicitante, cpfs_cadastro, proprietarios
):
    adquirentes, adquirentes_descartados = _validas(extraido.get("adquirentes"))
    transmitentes, transmitentes_descartados = _validas(extraido.get("transmitentes"))
    adicionar(
        "adquirente_solicitante",
        pedido.solicitante_cpf_cnpj,
        None,
        _mostrar(adquirentes, adquirentes_descartados),
        localizar_evidencia("cpf_cnpj", pedido.solicitante_cpf_cnpj, texto),
        solicitante in _cpfs(adquirentes),
    )
    adicionar(
        "transmitentes_proprietarios",
        None,
        _pessoas(proprietarios),
        _mostrar(transmitentes, transmitentes_descartados),
        _evid_pessoas(transmitentes, texto),
        bool(cpfs_cadastro) and _cpfs(transmitentes) == cpfs_cadastro,
    )
    data_registro = extraido.get("data_registro_venda")
    adicionar(
        "venda_registrada",
        None,
        None,
        data_registro,
        localizar_evidencia("data_registro_venda", data_registro, texto),
        bool(data_registro),
    )
    data_emissao = normalizar("data", extraido.get("data_emissao_certidao"))
    try:
        emitida_em = date.fromisoformat(data_emissao)
        limite = pedido.recebido_em.date() - timedelta(days=config.CERTIDAO_VALIDADE_DIAS)
        recente = emitida_em >= limite
    except (TypeError, ValueError):
        recente = False
    adicionar(
        "certidao_recente",
        None,
        None,
        data_emissao,
        localizar_evidencia("data_emissao_certidao", data_emissao, texto),
        recente,
    )


def _avaliar_locacao(adicionar, extraido, pedido, texto, solicitante):
    locatario = (pedido.dados_informados or {}).get("locatario") or {}
    locadores, locadores_descartados = _validas(extraido.get("locadores"))
    locatarios, locatarios_descartados = _validas(extraido.get("locatarios"))
    adicionar(
        "locador_proprietario",
        pedido.solicitante_cpf_cnpj,
        None,
        _mostrar(locadores, locadores_descartados),
        localizar_evidencia("cpf_cnpj", pedido.solicitante_cpf_cnpj, texto),
        solicitante in _cpfs(locadores),
    )
    adicionar(
        "locatario_informado",
        locatario.get("cpf_cnpj"),
        None,
        _mostrar(locatarios, locatarios_descartados),
        localizar_evidencia("cpf_cnpj", locatario.get("cpf_cnpj"), texto),
        normalizar("cpf_cnpj", locatario.get("cpf_cnpj")) in _cpfs(locatarios),
    )
    inicio = normalizar("data", extraido.get("data_inicio"))
    prazo = extraido.get("prazo_meses")
    try:
        fim = _somar_meses(date.fromisoformat(inicio), int(prazo))
        vigente = fim >= pedido.recebido_em.date()
        mostrado = f"início {inicio}, {prazo} meses, até {fim.isoformat()}"
    except (TypeError, ValueError):
        vigente = False
        mostrado = f"início {inicio or '?'}, prazo {prazo or '?'}"
    adicionar(
        "contrato_em_vigor",
        None,
        None,
        mostrado,
        localizar_evidencia("data_inicio", inicio, texto),
        vigente,
    )


def _avaliar_contrato(
    adicionar, extraido, pedido, texto, solicitante, cpfs_cadastro, proprietarios
):
    compradores, compradores_descartados = _validas(extraido.get("compradores"))
    vendedores, vendedores_descartados = _validas(extraido.get("vendedores"))
    adicionar(
        "comprador_solicitante",
        pedido.solicitante_cpf_cnpj,
        None,
        _mostrar(compradores, compradores_descartados),
        localizar_evidencia("cpf_cnpj", pedido.solicitante_cpf_cnpj, texto),
        solicitante in _cpfs(compradores),
    )
    adicionar(
        "vendedores_proprietarios",
        None,
        _pessoas(proprietarios),
        _mostrar(vendedores, vendedores_descartados),
        _evid_pessoas(vendedores, texto),
        bool(cpfs_cadastro) and _cpfs(vendedores) == cpfs_cadastro,
    )
    assinatura = extraido.get("forma_assinatura")
    adicionar(
        "assinatura_valida",
        None,
        None,
        assinatura,
        localizar_evidencia("forma_assinatura", assinatura, texto),
        assinatura in ("firma_reconhecida", "assinatura_digital_icp"),
    )
    chaves = normalizar("data", extraido.get("data_entrega_chaves"))
    informado = pedido.data_chaves.isoformat() if pedido.data_chaves else None
    adicionar(
        "data_chaves",
        informado,
        None,
        chaves,
        localizar_evidencia("data_entrega_chaves", chaves, texto),
        informado is not None and chaves == informado,
    )


def avaliar(tipo, extraido, pedido, unidade, condominio, proprietarios, texto):
    if unidade:
        descricao_unidade = f"{unidade['tipo']} {unidade['numero']}"
        descricao_unidade += f", Bloco {unidade['bloco']}" if unidade.get("bloco") else ""
    else:
        descricao_unidade = None
    solicitante = normalizar("cpf_cnpj", pedido.solicitante_cpf_cnpj)
    cpfs_cadastro = _cpfs(proprietarios)
    criterios = []

    def add(nome, informado, cadastro, extr, evid, regra):
        atendido = bool(regra and evid)
        criterios.append(Criterio(nome, informado, cadastro, extr, evid, atendido, PESO))

    unidade_extraida = extraido.get("unidade")
    add(
        "unidade",
        None,
        descricao_unidade or "não localizada no ERP",
        unidade_extraida,
        localizar_evidencia("unidade", unidade_extraida, texto),
        unidade is not None and chave_unidade(unidade_extraida) == chave_unidade(descricao_unidade),
    )
    condominio_extraido = extraido.get("condominio")
    add(
        "condominio",
        None,
        condominio,
        condominio_extraido,
        localizar_evidencia("condominio", condominio_extraido, texto),
        bool(condominio)
        and normalizar("condominio", condominio_extraido) == normalizar("condominio", condominio),
    )

    if tipo == "matricula":
        _avaliar_matricula(add, extraido, pedido, texto, solicitante, cpfs_cadastro, proprietarios)
    elif tipo == "locacao":
        _avaliar_locacao(add, extraido, pedido, texto, solicitante)
    else:
        _avaliar_contrato(add, extraido, pedido, texto, solicitante, cpfs_cadastro, proprietarios)

    total = sum(criterio.peso for criterio in criterios)
    pontos = sum(criterio.peso for criterio in criterios if criterio.atendido)
    indice = round(100 * pontos / total) if total else None
    return criterios, indice


def criterios_sem_ia(tipo, pedido, unidade, condominio, proprietarios):
    criterios, _ = avaliar(tipo, {}, pedido, unidade, condominio, proprietarios, "")
    for criterio in criterios:
        criterio.extraido = None
        criterio.evidencia = None
        criterio.atendido = None
    return criterios
