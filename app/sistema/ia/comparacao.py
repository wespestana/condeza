import re
import unicodedata


def normalizar(campo, valor):
    if valor is None:
        return None
    if isinstance(valor, int):
        return valor
    valor = str(valor).strip()
    if campo == "cpf_cnpj":
        return re.sub(r"\D", "", valor)
    if campo == "prazo_meses":
        return int(valor) if valor.isdigit() else valor
    if campo.startswith("data"):
        encontrado = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", valor)
        if encontrado:
            dia, mes, ano = encontrado.groups()
            return f"{ano}-{int(mes):02d}-{int(dia):02d}"
        padrao_extenso = r"(\d{1,2})\D{0,2}\s+de\s+(\w+)\s+de\s+(\d{4})"
        encontrado = re.fullmatch(padrao_extenso, _sem_acento(valor))
        if encontrado and encontrado.group(2) in MESES:
            dia, mes, ano = encontrado.groups()
            return f"{ano}-{MESES.index(mes) + 1:02d}-{int(dia):02d}"
        return valor
    if campo == "unidade":
        return chave_unidade(valor)
    if campo == "condominio":
        valor = re.sub(
            r"\b(condom[ií]nio|residencial|edif[ií]cio|conjunto)\b", " ", valor, flags=re.I
        )
    sem_acento = unicodedata.normalize("NFKD", valor).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^\w\s]", " ", sem_acento.lower()).split())

MESES = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _sem_acento(texto):
    return "".join(
        (unicodedata.normalize("NFKD", caractere)[0] if caractere.strip() else caractere).lower()
        for caractere in texto
    )


def _padroes(campo, valor):
    if valor is None or valor == "":
        return []
    if campo == "cpf_cnpj":
        digitos = re.sub(r"\D", "", str(valor))
        return [r"[.\-/\s]{0,3}".join(digitos)] if digitos else []
    if campo == "prazo_meses":
        return [rf"\b{int(valor)}\b\s*(\([^)]*\)\s*)?meses"]
    if campo == "forma_assinatura":
        padroes_assinatura = {
            "firma_reconhecida": [
                r"reconhe[cç]\w*\s+(por\s+\w+\s+)?(as\s+)?firma",
                r"firma reconhecida",
            ],
            "assinatura_digital_icp": [r"icp-brasil"],
            "sem_reconhecimento": [r"assinaturas?"],
        }
        return padroes_assinatura[valor]
    if campo.startswith("data"):
        encontrado = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", normalizar(campo, valor) or "")
        if not encontrado:
            return []
        ano, mes, dia = encontrado.groups()
        padrao_dia = rf"0?{int(dia)}" + (r"(º|o)?" if int(dia) == 1 else "")
        return [
            rf"\b0?{int(dia)}/{mes}/{ano}\b",
            rf"\b0?{int(dia)}/{int(mes)}/{ano}\b",
            rf"\b{padrao_dia}\s+de\s+{MESES[int(mes) - 1]}\s+de\s+{ano}\b",
        ]
    if campo == "unidade":
        encontrado = re.match(r"\s*(\w+)\D*?(\d+)", _sem_acento(str(valor)))
        if encontrado:
            tipo, numero = encontrado.groups()
            return [rf"\b{re.escape(tipo[:2])}\w*\.?\W+(n\W*o?\W*)?{numero}\b"]
    palavras = re.findall(r"\w+", _sem_acento(str(valor)))
    return [r"\W+".join(map(re.escape, palavras))] if palavras else []


def localizar_evidencia(campo, valor, texto):
    base = _sem_acento(texto)
    for padrao in _padroes(campo, valor):
        encontrado = re.search(padrao, base)
        if encontrado:
            inicio = max(0, encontrado.start() - 30)
            fim = min(len(texto), encontrado.end() + 30)
            return " ".join(texto[inicio:fim].split())
    return None


def chave_unidade(valor):
    texto = _sem_acento(str(valor or ""))
    achado_numero = re.search(r"\b(ap\w*|casa|sala|loja|conj\w*)\W+(?:n\W*o?\W*)?(\d+)", texto)
    achado_bloco = re.search(r"\b(?:bloco|torre)\s+(\w+)", texto)
    tipo = achado_numero.group(1)[:2] if achado_numero else None
    if achado_bloco and not achado_bloco.group(1).startswith("unic"):
        bloco = achado_bloco.group(1)
    else:
        bloco = None
    numero = achado_numero.group(2) if achado_numero else None
    return (tipo, numero, bloco)
