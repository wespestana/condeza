import base64
import json
import re
import time

import requests
from pypdf import PdfReader

from .. import config

def T(desc, tipo="string"):
    return {"type": [tipo, "null"], "description": desc}

def P(desc):
    return {
        "type": "array",
        "description": desc,
        "items": {
            "type": "object",
            "properties": {
                "nome": {
                    "type": "string",
                    "description": "nome completo da pessoa ou razão social da empresa",
                },
                "cpf_cnpj": {
                    "type": "string",
                    "description": "CPF ou CNPJ dessa mesma pessoa, como escrito no documento",
                },
            },
            "required": ["nome", "cpf_cnpj"],
        },
    }

UNIDADE = {
    "unidade_tipo": T(
        "tipo da unidade, como escrito no documento (ex.: Apartamento, Casa, Sala, Loja)"
    ),
    "unidade_numero": T(
        "número da unidade, só o número (ex.: 52); não é o número da matrícula, do andar nem do "
        "contribuinte"
    ),
    "unidade_bloco_ou_torre": T(
        "bloco ou torre onde fica a unidade, só a identificação (ex.: B, 2, Única); procure em "
        "todo o trecho que descreve o imóvel, mesmo depois do andar; null se o documento não citar "
        "bloco nem torre"
    ),
}
CONDOMINIO = T("nome do condomínio ou edifício onde fica a unidade, sem o endereço")

CAMPOS = {
    "matricula": {
        "data_emissao_certidao": T("data em que a certidão foi emitida ou expedida, AAAA-MM-DD"),
        "numero_matricula": T("número da matrícula do imóvel"),
        **UNIDADE,
        "condominio": CONDOMINIO,
        "transmitentes": P("TODAS as pessoas que venderam no último registro de venda e compra"),
        "adquirentes": P(
            "TODAS as pessoas que compraram no último registro de venda e compra; credor de "
            "hipoteca ou alienação fiduciária não é adquirente"
        ),
        "data_registro_venda": T(
            "data em que o último registro de venda foi feito na matrícula (não a data da "
            "escritura), AAAA-MM-DD"
        ),
    },
    "compra_venda": {
        "vendedores": P(
            "partes vendedoras; se for empresa, a empresa e o CNPJ, não o representante"
        ),
        "compradores": P("partes compradoras"),
        **UNIDADE,
        "condominio": CONDOMINIO,
        "data_contrato": T("data em que o contrato foi firmado ou assinado, AAAA-MM-DD"),
        "data_entrega_chaves": T(
            "data da entrega das chaves ou da posse, AAAA-MM-DD; se for 'na data da assinatura', "
            "use a data da assinatura"
        ),
        "forma_assinatura": {
            "type": "string",
            "enum": ["firma_reconhecida", "assinatura_digital_icp", "sem_reconhecimento"],
            "description": (
                "firma_reconhecida se houver reconhecimento de firma em cartório; "
                "assinatura_digital_icp se assinado com certificado ICP-Brasil"
            ),
        },
    },
    "locacao": {
        "locadores": P("partes locadoras (proprietário que aluga)"),
        "locatarios": P("partes locatárias (inquilinos); fiador não é locatário"),
        **UNIDADE,
        "condominio": CONDOMINIO,
        "data_inicio": T("data de início da locação, AAAA-MM-DD"),
        "prazo_meses": T("duração da locação em meses", "integer"),
    },
}

INSTRUCOES = {
    "matricula": (
        "Documento: certidão de matrícula de imóvel. Considere só o ÚLTIMO registro de venda e "
        "compra: transmitentes são TODOS os que venderam e adquirentes TODOS os que compraram "
        "nesse registro. Proprietários de registros anteriores, construtoras e credores de "
        "hipoteca ou alienação fiduciária não entram. data_registro_venda é a data do registro na "
        "matrícula, e não a data da escritura. O número de contribuinte do IPTU não é CPF."
    ),
    "compra_venda": (
        "Documento: contrato de compra e venda. Vendedores e compradores são as partes do "
        "contrato; testemunhas, representantes, diretores e tabeliães não são partes. Se a parte "
        "for empresa, informe a empresa e o CNPJ dela. forma_assinatura: firma_reconhecida se "
        "houver reconhecimento de firma em cartório (tabelião, 'reconheço'); "
        "assinatura_digital_icp só se houver certificado ICP-Brasil."
    ),
    "locacao": (
        "Documento: contrato de locação. Locadores e locatários são as partes; fiadores não são "
        "locatários. prazo_meses é a duração em meses."
    ),
}

REGRAS = (
    "Você extrai campos de documentos imobiliários brasileiros e responde só com o JSON pedido, "
    "seguindo a descrição de cada campo. Use apenas o que está escrito no documento; "
    "se um campo não existir, use null. Todas as datas no formato AAAA-MM-DD."
)


def _retirar(extraido, chave):
    return extraido.pop(chave, None)


def juntar_unidade(extraido):
    tipo = _retirar(extraido, "unidade_tipo")
    numero = _retirar(extraido, "unidade_numero")
    bloco = _retirar(extraido, "unidade_bloco_ou_torre")
    if not numero:
        extraido["unidade"] = None
    else:
        bloco = re.sub(r"^\s*(bloco|torre)\s+", "", str(bloco or ""), flags=re.I).strip()
        complemento = f", Bloco {bloco}" if bloco else ""
        extraido["unidade"] = f"{tipo or ''} {numero}".strip() + complemento
    return extraido


def esquema(tipo):
    campos = CAMPOS[tipo]
    return {"type": "object", "properties": campos, "required": list(campos)}


class TempoEsgotado(Exception):
    pass


class IAIndisponivel(Exception):
    pass


class LeituraImpossivel(Exception):
    pass


OPCOES = {"temperature": 0, "num_ctx": 8192}


def _chamar(mensagens, formato=None, limite=None):
    corpo = {
        "model": config.OLLAMA_MODELO,
        "stream": False,
        "think": False,
        "options": OPCOES,
        "messages": mensagens,
    }
    if formato:
        corpo["format"] = formato
    try:
        resposta = requests.post(
            f"{config.OLLAMA_URL}/api/chat",
            json=corpo,
            timeout=limite or config.IA_TEMPO_LIMITE,
        )
    except requests.Timeout as erro:
        raise TempoEsgotado() from erro
    except requests.ConnectionError as erro:
        raise IAIndisponivel(str(erro)) from erro
    if resposta.status_code >= 500:
        raise IAIndisponivel(f"Ollama HTTP {resposta.status_code}")
    resposta.raise_for_status()
    return resposta.json()["message"]["content"]


def carregar_modelo():
    inicio = time.time()
    try:
        resposta = requests.post(
            f"{config.OLLAMA_URL}/api/generate",
            json={"model": config.OLLAMA_MODELO, "options": OPCOES},
            timeout=config.IA_TEMPO_CARGA,
        )
    except (requests.Timeout, requests.ConnectionError) as erro:
        raise IAIndisponivel(f"o modelo não carregou: {erro}") from erro
    if resposta.status_code >= 500:
        raise IAIndisponivel(f"Ollama HTTP {resposta.status_code} ao carregar o modelo")
    resposta.raise_for_status()
    return time.time() - inicio


def texto_do_documento(caminho, mime):
    if mime == "application/pdf":
        paginas = PdfReader(caminho).pages
        texto = "\n".join(pagina.extract_text() or "" for pagina in paginas)
        if len(texto.strip()) > 200:
            return texto, "pdf"
        raise LeituraImpossivel("PDF sem camada de texto (digitalizado)")
    with open(caminho, "rb") as arquivo:
        imagem = base64.b64encode(arquivo.read()).decode()
    mensagem = {
        "role": "user",
        "content": "Transcreva fielmente todo o texto desta imagem, sem comentar.",
        "images": [imagem],
    }
    texto = _chamar([mensagem])
    return texto, "foto"


def extrair(tipo, texto):
    inicio = time.time()
    mensagens = [
        {"role": "system", "content": REGRAS + " " + INSTRUCOES[tipo]},
        {"role": "user", "content": texto},
    ]
    resposta = _chamar(mensagens, formato=esquema(tipo))
    return juntar_unidade(json.loads(resposta)), time.time() - inicio
