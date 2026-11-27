from datetime import datetime

from flask import Blueprint, render_template

from ..modelos import AvisoProprietario, Pedido, Sessao
from ..servicos import trilha
from ..util import hash_token

bp = Blueprint("contestacao", __name__, url_prefix="/contestar")


def _aviso(sessao, token):
    return sessao.query(AvisoProprietario).filter_by(token_hash=hash_token(token)).first()


@bp.get("/<token>")
def contestar(token):
    with Sessao() as sessao:
        aviso = _aviso(sessao, token)
        if not aviso:
            return render_template("contestacao/invalido.html"), 404
        ja_contestado = aviso.contestado_em is not None
        return render_template("contestacao/confirmar.html", token=token, ja=ja_contestado)


@bp.post("/<token>")
def confirmar(token):
    with Sessao() as sessao:
        aviso = _aviso(sessao, token)
        if not aviso:
            return render_template("contestacao/invalido.html"), 404
        if not aviso.contestado_em:
            aviso.contestado_em = datetime.now()
            pedido = sessao.get(Pedido, aviso.pedido_id)
            pedido.contestado = True
            trilha.registrar(sessao, "contestado", f"proprietario:{aviso.email}",
                             pedido_id=pedido.id)
            sessao.commit()
        return render_template("contestacao/registrado.html")
