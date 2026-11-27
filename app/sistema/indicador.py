from datetime import date

from . import prazos


def no_prazo(pedido) -> bool:
    return pedido.concluido_em.date() <= prazos.prazos(pedido)["conclusao"]


def calcular(pedidos, ano: int, mes: int):
    def resumo(lista):
        concluidos = len(lista)
        quantidade_no_prazo = sum(1 for pedido in lista if no_prazo(pedido))
        percentual = round(100 * quantidade_no_prazo / concluidos) if concluidos else None
        return {"concluidos": concluidos, "no_prazo": quantidade_no_prazo, "percentual": percentual}

    do_mes = [
        pedido for pedido in pedidos
        if pedido.concluido_em
        and (pedido.concluido_em.year, pedido.concluido_em.month) == (ano, mes)
    ]
    tipos = sorted({pedido.tipo.value for pedido in do_mes})
    total = resumo(do_mes)
    por_tipo = {}
    for tipo in tipos:
        pedidos_do_tipo = [pedido for pedido in do_mes if pedido.tipo.value == tipo]
        por_tipo[tipo] = resumo(pedidos_do_tipo)
    pedidos_ordenados = sorted(do_mes, key=lambda pedido: pedido.concluido_em)
    pedidos_e_situacao = [(pedido, no_prazo(pedido)) for pedido in pedidos_ordenados]
    return {"total": total, "por_tipo": por_tipo, "pedidos": pedidos_e_situacao}


def mes_anterior(hoje: date):
    return (hoje.year - 1, 12) if hoje.month == 1 else (hoje.year, hoje.month - 1)
