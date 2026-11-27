import json
import os
import threading
from functools import wraps
from pathlib import Path

from flask import abort, request

_trava = threading.Lock()


class Base:
    def __init__(self, inicial: Path, gravado: Path):
        self.gravado = gravado
        origem = gravado if gravado.exists() else inicial
        self.dados = json.loads(origem.read_text(encoding="utf-8"))

    def salvar(self):
        with _trava:
            self.gravado.parent.mkdir(exist_ok=True)
            conteudo = json.dumps(self.dados, ensure_ascii=False, indent=2)
            self.gravado.write_text(conteudo, encoding="utf-8")


def exige_chave(visao):
    @wraps(visao)
    def interno(*args, **kwargs):
        if request.headers.get("X-API-Chave") != os.environ.get("API_CHAVE"):
            abort(401)
        return visao(*args, **kwargs)
    return interno
