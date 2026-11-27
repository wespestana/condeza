import hashlib
import re
import secrets


def digitos(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


def cpf_valido(cpf: str) -> bool:
    numeros = digitos(cpf)
    if len(numeros) != 11 or numeros == numeros[0] * 11:
        return False
    for posicao in (9, 10):
        soma = sum(int(numeros[indice]) * (posicao + 1 - indice) for indice in range(posicao))
        if (soma * 10) % 11 % 10 != int(numeros[posicao]):
            return False
    return True


def cnpj_valido(cnpj: str) -> bool:
    numeros = digitos(cnpj)
    if len(numeros) != 14 or numeros == numeros[0] * 14:
        return False
    pesos_primeiro_digito = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos_segundo_digito = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for posicao, pesos in ((12, pesos_primeiro_digito), (13, pesos_segundo_digito)):
        produtos = (int(digito) * peso for digito, peso in zip(numeros[:posicao], pesos))
        resto = sum(produtos) % 11
        if resto < 2:
            digito_esperado = 0
        else:
            digito_esperado = 11 - resto
        if digito_esperado != int(numeros[posicao]):
            return False
    return True


def cpf_cnpj_valido(valor: str) -> bool:
    if len(digitos(valor)) == 11:
        return cpf_valido(valor)
    return cnpj_valido(valor)


def formatar_cpf_cnpj(valor: str) -> str:
    numeros = digitos(valor)
    if len(numeros) == 11:
        return f"{numeros[:3]}.{numeros[3:6]}.{numeros[6:9]}-{numeros[9:]}"
    if len(numeros) == 14:
        return f"{numeros[:2]}.{numeros[2:5]}.{numeros[5:8]}/{numeros[8:12]}-{numeros[12:]}"
    return valor


def novo_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def email_valido(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email or ""))
