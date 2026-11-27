import sys
from datetime import date, datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comum import Base, exige_chave

PASTA = Path(__file__).parent
base = Base(PASTA / "inicial.json", PASTA.parent / "dados" / "erp.json")
app = Flask(__name__)


def _unidade(uid):
    encontrada = next((unidade_erp for unidade_erp in base.dados["unidades"]
                       if unidade_erp["id"] == uid), None)
    return encontrada or abort(404)


@app.get("/api/condominios")
@exige_chave
def condominios():
    return jsonify(base.dados["condominios"])


@app.get("/api/unidades")
@exige_chave
def buscar_unidades():
    condominio_id = request.args.get("condominio_id", type=int)
    numero = (request.args.get("numero") or "").strip()
    bloco = (request.args.get("bloco") or "").strip().upper() or None
    achadas = [unidade_erp for unidade_erp in base.dados["unidades"]
               if (condominio_id is None or unidade_erp["condominio_id"] == condominio_id)
               and (not numero or unidade_erp["numero"] == numero)
               and (bloco is None or (unidade_erp["bloco"] or "").upper() == bloco)]
    sem_responsaveis = [{chave: valor for chave, valor in unidade_erp.items()
                         if chave != "responsaveis"}
                        for unidade_erp in achadas]
    return jsonify(sem_responsaveis)


@app.get("/api/responsaveis/unidades")
@exige_chave
def unidades_do_responsavel():
    cpf_cnpj_informado = request.args.get("cpf_cnpj", "")
    alvo = "".join(caractere for caractere in cpf_cnpj_informado if caractere.isdigit())
    if not alvo:
        abort(400)
    def dig(valores):
        return "".join(caractere for caractere in valores if caractere.isdigit())
    encontradas = [unidade_erp for unidade_erp in base.dados["unidades"]
                   if any(dig(responsavel["cpf_cnpj"]) == alvo
                          for responsavel in unidade_erp["responsaveis"])]
    return jsonify(encontradas)


@app.get("/api/unidades/<int:uid>")
@exige_chave
def unidade(uid):
    return jsonify(_unidade(uid))


@app.put("/api/unidades/<int:uid>/responsaveis")
@exige_chave
def gravar_responsaveis(uid):
    unidade_erp = _unidade(uid)
    novos = request.get_json(force=True)
    for responsavel in novos:
        if not responsavel.get("nome") or not responsavel.get("cpf_cnpj"):
            abort(400, "nome e CPF/CNPJ são obrigatórios para o boleto (RNF10)")
    registro_do_historico = {"unidade_id": uid,
                             "em": datetime.now().isoformat(timespec="seconds"),
                             "antes": unidade_erp["responsaveis"], "depois": novos,
                             "origem": request.headers.get("X-Origem", "desconhecida")}
    base.dados["historico"].append(registro_do_historico)
    unidade_erp["responsaveis"] = novos
    base.salvar()
    return jsonify(unidade_erp)


@app.put("/api/condominios/<int:cid>/fim-administracao")
@exige_chave
def fim_administracao(cid):
    encontrado = next((condominio for condominio in base.dados["condominios"]
                       if condominio["id"] == cid), None)
    condominio = encontrado or abort(404)
    corpo = request.get_json(force=True) or {}
    condominio["fim_administracao"] = corpo.get("data")
    base.salvar()
    return jsonify(condominio)


@app.get("/api/condominios/<int:cid>/emissao")
@exige_chave
def emissao(cid):
    encontrado = next((condominio for condominio in base.dados["condominios"]
                       if condominio["id"] == cid), None)
    condominio = encontrado or abort(404)
    hoje = date.today()
    data_corte = hoje.replace(day=condominio["dia_emissao"])
    emissao_registrada = any(registro["condominio_id"] == cid
                             and registro["mes"] == hoje.strftime("%Y-%m")
                             for registro in base.dados["emissoes"])
    emitido = emissao_registrada or hoje >= data_corte
    return jsonify({"condominio_id": cid, "data_corte": data_corte.isoformat(),
                    "emitido_no_mes": emitido})


@app.get("/saude")
def saude():
    return {"ok": True, "servico": "erp-simulado", "unidades": len(base.dados["unidades"])}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
