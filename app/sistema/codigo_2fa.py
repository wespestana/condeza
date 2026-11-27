import os
import sys
import time

import pyotp

from .modelos import Sessao, Usuario

if __name__ == "__main__":
    with Sessao() as sessao:
        variaveis_por_perfil = {
            "gerente": "GERENTE_TESTE_EMAIL",
            "tecnologia": "TECNOLOGIA_TESTE_EMAIL",
        }
        argumentos = sys.argv[1:] or [""]
        perfil_escolhido = argumentos[0]
        nome_da_variavel = variaveis_por_perfil.get(perfil_escolhido, "EQUIPE_TESTE_EMAIL")
        email_da_conta = os.environ[nome_da_variavel]
        usuario = sessao.query(Usuario).filter_by(email=email_da_conta).one()
        restam = 30 - int(time.time()) % 30
        codigo = pyotp.TOTP(usuario.totp_segredo).now()
        print(f"{codigo}   (conta {usuario.email}; vale por mais {restam} s)")
