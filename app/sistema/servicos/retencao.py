from datetime import datetime
from pathlib import Path

from . import trilha


def eliminar_documentos(sessao, pedido, autor, motivo):
    apagados = []
    for documento in pedido.documentos:
        if documento.eliminado_em and not documento.caminho:
            continue
        if documento.caminho:
            Path(documento.caminho).unlink(missing_ok=True)
        apagados.append({
            "documento": documento.id,
            "tipo": documento.tipo.value,
            "nome": documento.nome_original,
            "enviado_em": documento.criado_em.isoformat(timespec="minutes"),
        })
        documento.eliminado_em = documento.eliminado_em or datetime.now()
        documento.caminho = ""
    if apagados:
        trilha.registrar(
            sessao,
            "documentos_eliminados",
            autor,
            pedido_id=pedido.id,
            motivo=motivo,
            documentos=apagados,
        )
    return len(apagados)
