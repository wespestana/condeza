from flask import Blueprint, render_template

bp = Blueprint("site", __name__)


@bp.get("/")
def inicio():
    return render_template("site/inicio.html")


@bp.get("/quem-somos")
def quem_somos():
    return render_template("site/quem_somos.html")


@bp.get("/condominos")
def condominos():
    return render_template("site/condominos.html")


@bp.get("/contato")
def contato():
    return render_template("site/contato.html")


@bp.get("/sindicos")
def sindicos():
    return render_template("site/sindicos.html")


@bp.get("/privacidade")
def privacidade():
    return render_template("site/privacidade.html")
