import smtplib
from datetime import datetime, timedelta

import pyotp
from flask import (Blueprint, abort, flash, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import generate_password_hash

from .. import config
from ..modelos import AtivacaoConta, Evento, Perfil, Sessao, Usuario
from ..servicos import email, trilha
from ..util import email_valido, hash_token, novo_token
from .equipe import autor, exige_perfil

bp = Blueprint("contas", __name__, url_prefix="/equipe")
so_tecnologia = exige_perfil("tecnologia")
EVENTOS_CONTA = ["conta_criada", "conta_ativada", "conta_desativada", "conta_reativada",
                 "acesso_redefinido"]


def situacao(usuario):
    if not usuario.ativo:
        return "desativada"
    return "ativa" if usuario.senha_hash else "aguardando ativação"


def _enviar_link(sessao, usuario):
    for antigo in sessao.query(AtivacaoConta).filter_by(usuario_id=usuario.id, usado_em=None):
        antigo.usado_em = datetime.now()
    token, hash_do_token = novo_token()
    validade = timedelta(hours=config.ATIVACAO_VALIDADE_HORAS)
    expira_em = datetime.now() + validade
    sessao.add(AtivacaoConta(usuario_id=usuario.id, token_hash=hash_do_token, expira_em=expira_em))
    sessao.commit()
    try:
        endereco_ativacao = f"{config.URL_PUBLICA}/equipe/ativar/{token}"
        email.enviar(
            usuario.email, "Ative sua conta da equipe Condeza", "ativacao_conta",
            nome=usuario.nome, url=endereco_ativacao, horas=config.ATIVACAO_VALIDADE_HORAS,
        )
        return True
    except (smtplib.SMTPException, OSError):
        return False


@bp.get("/contas")
@so_tecnologia
def lista():
    with Sessao() as sessao:
        contas = sessao.query(Usuario).order_by(Usuario.ativo.desc(), Usuario.nome).all()
        eventos = (sessao.query(Evento).filter(Evento.tipo.in_(EVENTOS_CONTA))
                   .order_by(Evento.id.desc()).limit(30).all())
    return render_template("equipe/contas.html", contas=contas, eventos=eventos,
                           situacao=situacao, eu=session["usuario"]["id"], dados=request.args,
                           horas=config.ATIVACAO_VALIDADE_HORAS)


@bp.post("/contas")
@so_tecnologia
def criar():
    nome = " ".join(request.form.get("nome", "").split())
    mail = request.form.get("email", "").strip().lower()
    perfil = request.form.get("perfil", "")
    erros = []
    if len(nome.split()) < 2:
        erros.append("Informe o nome completo da pessoa: a conta é nominal, "
                     "sem contas compartilhadas.")
    if not email_valido(mail):
        erros.append("Informe um e-mail válido.")
    perfis_validos = [opcao.value for opcao in Perfil]
    if perfil not in perfis_validos:
        erros.append("Escolha o perfil.")
    with Sessao() as sessao:
        if mail and sessao.query(Usuario).filter_by(email=mail).first():
            erros.append("Já existe uma conta com este e-mail. "
                         "Para devolver o acesso, use Reenviar link.")
        if erros:
            for mensagem in erros:
                flash(mensagem, "erro")
            return redirect(url_for("contas.lista", nome=nome, email=mail, perfil=perfil))
        usuario = Usuario(nome=nome, email=mail, perfil=Perfil(perfil), senha_hash="",
                          totp_segredo="", ativo=True)
        sessao.add(usuario)
        sessao.flush()
        trilha.registrar(sessao, "conta_criada", autor(), conta=mail, perfil=perfil)
        enviado = _enviar_link(sessao, usuario)
    if enviado:
        flash(f"Conta criada. O link de ativação foi enviado para {mail}.", "ok")
    else:
        flash(f"Conta criada, mas o e-mail não saiu. Tente Reenviar link para {mail}.", "erro")
    return redirect(url_for("contas.lista"))


def _outra_conta(sessao, conta_id):
    usuario = sessao.get(Usuario, conta_id) or abort(404)
    if usuario.id == session["usuario"]["id"]:
        flash("Você não pode alterar a própria conta. Peça a outra pessoa da Tecnologia.", "erro")
        return None
    return usuario


@bp.post("/contas/<int:conta_id>/link")
@so_tecnologia
def reenviar(conta_id):
    with Sessao() as sessao:
        usuario = _outra_conta(sessao, conta_id)
        if usuario is None:
            return redirect(url_for("contas.lista"))
        if not usuario.ativo:
            flash("Reative a conta antes de reenviar o link.", "erro")
            return redirect(url_for("contas.lista"))
        usuario.senha_hash = ""
        usuario.totp_segredo = ""
        trilha.registrar(sessao, "acesso_redefinido", autor(), conta=usuario.email)
        enviado = _enviar_link(sessao, usuario)
        mail = usuario.email
    if enviado:
        mensagem = (f"Novo link de ativação enviado para {mail}. "
                    "A senha e o autenticador anteriores não valem mais.")
        categoria = "ok"
    else:
        mensagem = f"O e-mail para {mail} não saiu. Tente de novo."
        categoria = "erro"
    flash(mensagem, categoria)
    return redirect(url_for("contas.lista"))


@bp.post("/contas/<int:conta_id>/situacao")
@so_tecnologia
def alternar(conta_id):
    with Sessao() as sessao:
        usuario = _outra_conta(sessao, conta_id)
        if usuario is None:
            return redirect(url_for("contas.lista"))
        usuario.ativo = not usuario.ativo
        tipo_do_evento = "conta_reativada" if usuario.ativo else "conta_desativada"
        trilha.registrar(sessao, tipo_do_evento, autor(), conta=usuario.email)
        sessao.commit()
        acao = 'reativada' if usuario.ativo else 'desativada'
        flash(f"Conta de {usuario.nome} {acao}.", "ok")
    return redirect(url_for("contas.lista"))


def _link_valido(sessao, token):
    ativacao = sessao.query(AtivacaoConta).filter_by(token_hash=hash_token(token)).first()
    if not ativacao or ativacao.usado_em or ativacao.expira_em < datetime.now():
        return None, None
    usuario = sessao.get(Usuario, ativacao.usuario_id)
    return (ativacao, usuario) if usuario and usuario.ativo else (None, None)


@bp.route("/ativar/<token>", methods=["GET", "POST"])
def ativar(token):
    with Sessao() as sessao:
        ativacao, usuario = _link_valido(sessao, token)
        if not ativacao:
            return render_template("equipe/ativar.html", invalido=True), 410
        guardado = session.get("ativacao") or {}
        if guardado.get("link") != ativacao.token_hash:
            guardado = {"link": ativacao.token_hash, "segredo": pyotp.random_base32()}
            session["ativacao"] = guardado
        segredo = guardado["segredo"]
        erros = []
        if request.method == "POST":
            senha = request.form.get("senha", "")
            if len(senha) < config.SENHA_EQUIPE_MINIMO:
                erros.append(f"A senha precisa ter pelo menos {config.SENHA_EQUIPE_MINIMO} "
                             "caracteres.")
            if senha != request.form.get("confirmacao", ""):
                erros.append("A confirmação não é igual à senha.")
            codigo = request.form.get("codigo", "").strip()
            if not pyotp.TOTP(segredo).verify(codigo, valid_window=1):
                erros.append("Código inválido. Confira se a chave foi cadastrada no autenticador "
                             "e digite o código atual.")
            if not erros:
                usuario.senha_hash = generate_password_hash(senha)
                usuario.totp_segredo = segredo
                ativacao.usado_em = datetime.now()
                trilha.registrar(sessao, "conta_ativada", usuario.email, conta=usuario.email)
                sessao.commit()
                session.pop("ativacao", None)
                return render_template("equipe/ativar.html", concluido=True, conta=usuario.email)
        blocos = [segredo[inicio:inicio + 4] for inicio in range(0, len(segredo), 4)]
        chave = " ".join(blocos)
        uri = pyotp.TOTP(segredo).provisioning_uri(name=usuario.email, issuer_name="Condeza")
        return render_template("equipe/ativar.html", conta=usuario.email, nome=usuario.nome,
                               chave=chave, uri=uri, erros=erros,
                               minimo=config.SENHA_EQUIPE_MINIMO)
