import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comum import Base, exige_chave

PASTA = Path(__file__).parent
base = Base(PASTA / "inicial.json", PASTA.parent / "dados" / "aplicativo.json")
app = Flask(__name__)

SENHA_TESTE = os.environ.get("CONDOMINO_TESTE_SENHA", "")


def _condomino(cpf):
    digitos_procurados = "".join(caractere for caractere in cpf if caractere.isdigit())
    return next((cadastrado for cadastrado in base.dados["condominos"]
                 if "".join(caractere for caractere in cadastrado["cpf_cnpj"]
                            if caractere.isdigit()) == digitos_procurados), None)


@app.get("/")
def raiz():
    return jsonify({"servico": "aplicativo do condômino (simulado)", "uso": "somente API"})


@app.post("/api/autenticar")
@exige_chave
def autenticar():
    dados = request.get_json(force=True) or {}
    cadastrado = _condomino(dados.get("cpf_cnpj", ""))
    if (not cadastrado or not SENHA_TESTE
            or not secrets.compare_digest(dados.get("senha", ""), SENHA_TESTE)):
        return jsonify({"valido": False})
    return jsonify({"valido": True, "cpf_cnpj": cadastrado["cpf_cnpj"], "nome": cadastrado["nome"]})


@app.get("/api/condominos/<path:cpf>")
@exige_chave
def condomino(cpf):
    cadastrado = _condomino(cpf) or abort(404)
    nomes = {pessoa["cpf_cnpj"]: pessoa["nome"] for pessoa in base.dados["condominos"]}
    unidades = [{"id": unidade["id"],
                 "responsaveis": [dict(responsavel, nome=nomes.get(responsavel["cpf_cnpj"], ""))
                                  for responsavel in unidade["responsaveis"]]}
                for unidade in base.dados["unidades"]
                if any(responsavel["cpf_cnpj"] == cadastrado["cpf_cnpj"]
                       for responsavel in unidade["responsaveis"])]
    campos_de_contato = ("nome", "cpf_cnpj", "email", "telefone", "endereco_correspondencia")
    contato = {campo: cadastrado.get(campo) for campo in campos_de_contato}
    return jsonify({**contato, "morador": bool(cadastrado.get("morador")), "unidades": unidades})


@app.put("/api/condominos/<path:cpf>/contato")
@exige_chave
def alterar_contato(cpf):
    cadastrado = _condomino(cpf) or abort(404)
    alteradas = []
    corpo = request.get_json(force=True) or {}
    for campo, novo in corpo.items():
        novo = (novo or "").strip()
        campo_de_contato = campo in ("email", "telefone", "endereco_correspondencia")
        if campo_de_contato and novo and novo != (cadastrado.get(campo) or ""):
            ids_existentes = [alteracao["id"] for alteracao in base.dados["alteracoes_contato"]]
            proximo_id = max(ids_existentes or [0]) + 1
            nova_alteracao = {"id": proximo_id, "cpf_cnpj": cadastrado["cpf_cnpj"],
                              "campo": campo, "valor_anterior": cadastrado.get(campo),
                              "valor_novo": novo,
                              "alterado_em": datetime.now().isoformat(timespec="seconds")}
            base.dados["alteracoes_contato"].append(nova_alteracao)
            cadastrado[campo] = novo
            alteradas.append(campo)
    base.salvar()
    return jsonify({"alteradas": alteradas})


@app.get("/api/alteracoes-contato")
@exige_chave
def alteracoes():
    desde = request.args.get("desde_id", 0, type=int)
    novas = [alteracao for alteracao in base.dados["alteracoes_contato"]
             if alteracao["id"] > desde]
    return jsonify(novas)


@app.put("/api/unidades/<int:uid>/responsaveis")
@exige_chave
def gravar_responsaveis(uid):
    encontrada = next((unidade for unidade in base.dados["unidades"]
                       if unidade["id"] == uid), None)
    unidade = encontrada or abort(404)
    novos = request.get_json(force=True)
    unidade["responsaveis"] = [{"cpf_cnpj": responsavel["cpf_cnpj"], "papel": responsavel["papel"]}
                               for responsavel in novos]
    for responsavel in novos:
        ja_cadastrado = any(pessoa["cpf_cnpj"] == responsavel["cpf_cnpj"]
                            for pessoa in base.dados["condominos"])
        if not ja_cadastrado:
            novo_condomino = {"nome": responsavel["nome"], "cpf_cnpj": responsavel["cpf_cnpj"],
                              "email": responsavel.get("email"),
                              "telefone": responsavel.get("telefone"),
                              "identidade_validada": False}
            base.dados["condominos"].append(novo_condomino)
    base.salvar()
    return jsonify(unidade)


@app.get("/saude")
def saude():
    return {"ok": True, "servico": "app-simulado", "condominos": len(base.dados["condominos"])}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
