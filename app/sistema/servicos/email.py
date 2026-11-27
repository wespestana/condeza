import smtplib
from email.message import EmailMessage

from flask import render_template

from .. import config


def enviar(para, assunto, modelo, **contexto):
    mensagem = EmailMessage()
    mensagem["From"] = f"Condeza <{config.EMAIL_REMETENTE}>"
    mensagem["To"] = para
    mensagem["Subject"] = assunto
    mensagem.set_content(render_template(f"emails/{modelo}.txt", **contexto))
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as smtp:
        smtp.send_message(mensagem)
