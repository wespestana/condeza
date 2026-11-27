from datetime import datetime

from ..gateways import ERP, Aplicativo
from ..modelos import Controle, Pedido, Situacao, TipoPedido
from ..util import digitos, formatar_cpf_cnpj
from . import trilha

CAMPOS_CONTATO = {"email", "telefone", "endereco_correspondencia"}
PAPEIS_RESPONSAVEIS = {"proprietario", "comprador", "locatario"}
CURSOR = "conciliacao_ultima_alteracao"


def _grupos(alteracoes):
    grupos = []
    for alteracao in sorted(alteracoes, key=lambda item: item["id"]):
        chave = (digitos(alteracao["cpf_cnpj"]), alteracao.get("alterado_em"))
        if grupos and grupos[-1][0] == chave:
            grupos[-1][1].append(alteracao)
        else:
            grupos.append((chave, [alteracao]))
    return [alteracoes_do_grupo for _, alteracoes_do_grupo in grupos]


def importar(sessao):
    cursor = sessao.get(Controle, CURSOR, with_for_update=True) or Controle(chave=CURSOR, valor="0")
    sessao.add(cursor)
    erp = ERP()
    criadas = 0
    novas_alteracoes = Aplicativo().alteracoes_contato(int(cursor.valor))
    for grupo in _grupos(novas_alteracoes):
        contato = [alteracao for alteracao in grupo if alteracao.get("campo") in CAMPOS_CONTATO]
        for alteracao in grupo:
            if alteracao not in contato:
                trilha.registrar(sessao, "alteracao_ignorada", "sistema", alteracao=alteracao["id"],
                                 motivo="campo fora da conciliação")
        alvos = []
        if contato:
            alvos = _alvos_da_conciliacao(sessao, erp, grupo, contato)
        for unidade, responsavel in alvos:
            _criar_pedido_de_conciliacao(sessao, grupo, contato, unidade, responsavel)
            criadas += 1
        cursor.valor = str(grupo[-1]["id"])
    sessao.commit()
    return criadas


def _alvos_da_conciliacao(sessao, erp, grupo, contato):
    alvos = []
    for unidade in erp.unidades_do_responsavel(grupo[0]["cpf_cnpj"]):
        responsavel = next((pessoa for pessoa in unidade["responsaveis"]
                            if digitos(pessoa["cpf_cnpj"]) == digitos(grupo[0]["cpf_cnpj"])),
                           None)
        if responsavel and responsavel["papel"] in PAPEIS_RESPONSAVEIS:
            alvos.append((unidade, responsavel))
    if not alvos:
        for alteracao in contato:
            trilha.registrar(sessao, "alteracao_ignorada", "sistema", alteracao=alteracao["id"],
                             motivo="não é responsável de unidade no ERP")
    return alvos


def _criar_pedido_de_conciliacao(sessao, grupo, contato, unidade, responsavel):
    agora = datetime.now()
    itens = [{"alteracao": alteracao["id"], "campo": alteracao["campo"],
              "valor_erp": responsavel.get(alteracao["campo"]),
              "valor_novo": alteracao["valor_novo"]}
             for alteracao in contato]
    dados_da_conciliacao = {"papel": responsavel["papel"],
                            "alterado_em": grupo[0].get("alterado_em"),
                            "itens": itens}
    pedido = Pedido(tipo=TipoPedido.CONCILIACAO, situacao=Situacao.A_CONFERIR, canal="aplicativo",
                    condominio_id=unidade["condominio_id"], unidade_id=unidade["id"],
                    solicitante_nome=responsavel["nome"],
                    solicitante_cpf_cnpj=formatar_cpf_cnpj(responsavel["cpf_cnpj"]),
                    solicitante_email=responsavel.get("email") or "",
                    recebido_em=agora,
                    dados_informados={"numero": unidade["numero"],
                                      "bloco": unidade.get("bloco") or "",
                                      "conciliacao": dados_da_conciliacao})
    sessao.add(pedido)
    sessao.flush()
    pedido.numero = f"{agora.year}-{pedido.id:06d}"
    ids_das_alteracoes = [item["alteracao"] for item in itens]
    campos_alterados = [item["campo"] for item in itens]
    trilha.registrar(sessao, "conciliacao_recebida", "sistema", pedido_id=pedido.id,
                     alteracoes=ids_das_alteracoes, campos=campos_alterados)
