from ..modelos import Evento


def registrar(sessao, tipo, autor, pedido_id=None, **detalhe):
    sessao.add(Evento(pedido_id=pedido_id, tipo=tipo, autor=autor, detalhe=detalhe))
