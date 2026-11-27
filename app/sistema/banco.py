import os

import pyotp
from werkzeug.security import generate_password_hash

from .modelos import Base, Perfil, Sessao, Usuario, motor


def criar():
    Base.metadata.create_all(motor)
    print("tabelas criadas (gatilhos: banco/gatilhos.sql, pelo administrador)")


CONTAS_TESTE = [
    ("EQUIPE_TESTE_EMAIL", "EQUIPE_TESTE_SENHA", "Conferente de Teste", Perfil.CONFERENTE),
    ("GERENTE_TESTE_EMAIL", "GERENTE_TESTE_SENHA", "Gerente da Carteira 1 (teste)", Perfil.GERENTE),
    ("TECNOLOGIA_TESTE_EMAIL", "TECNOLOGIA_TESTE_SENHA", "Tecnologia de Teste", Perfil.TECNOLOGIA),
]


def carga_inicial():
    with Sessao() as sessao:
        for var_email, var_senha, nome, perfil in CONTAS_TESTE:
            email = os.environ.get(var_email)
            if not email or sessao.query(Usuario).filter_by(email=email).first():
                continue
            segredo = pyotp.random_base32()
            senha_criptografada = generate_password_hash(os.environ[var_senha])
            novo_usuario = Usuario(
                nome=nome,
                email=email,
                perfil=perfil,
                senha_hash=senha_criptografada,
                totp_segredo=segredo,
            )
            sessao.add(novo_usuario)
            sessao.commit()
            gerador_totp = pyotp.TOTP(segredo)
            uri = gerador_totp.provisioning_uri(name=email, issuer_name="Condeza (teste)")
            with open(f"/dados/documentos/.totp_{perfil.value}_teste.txt", "w") as arquivo:
                arquivo.write(uri + "\n")
            print(f"conta de teste criada: {perfil.value}")


if __name__ == "__main__":
    criar()
    carga_inicial()
