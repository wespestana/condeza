import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from .. import config

LIMITE_BYTES = 10 * 1024 * 1024


class ArquivoInvalido(ValueError):
    pass


def guardar(pedido_id, arquivo):
    dados = arquivo.read()
    if not dados:
        raise ArquivoInvalido("arquivo vazio")
    if len(dados) > LIMITE_BYTES:
        raise ArquivoInvalido("arquivo maior que 10 MB")
    pasta = Path(config.PASTA_DOCUMENTOS) / str(pedido_id)
    pasta.mkdir(parents=True, exist_ok=True)
    nome = uuid.uuid4().hex
    if dados[:5] == b"%PDF-":
        destino = pasta / f"{nome}.pdf"
        destino.write_bytes(dados)
        return str(destino), "application/pdf", len(dados)
    try:
        imagem = Image.open(io.BytesIO(dados))
        imagem.verify()
        imagem_reaberta = Image.open(io.BytesIO(dados))
        imagem = ImageOps.exif_transpose(imagem_reaberta)
    except Exception as erro:
        raise ArquivoInvalido("envie um PDF ou uma foto (JPG ou PNG)") from erro
    imagem_limpa = Image.new(imagem.mode, imagem.size)
    pixels = list(imagem.getdata())
    imagem_limpa.putdata(pixels)
    if imagem_limpa.mode not in ("RGB", "L"):
        imagem_limpa = imagem_limpa.convert("RGB")
    destino = pasta / f"{nome}.jpg"
    imagem_limpa.save(destino, "JPEG", quality=90)
    return str(destino), "image/jpeg", destino.stat().st_size
