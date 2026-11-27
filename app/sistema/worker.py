import time
import traceback
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError

from . import config, rotinas
from .gateways import ERP, ServicoIndisponivel
from .ia import extracao, indice
from .modelos import AnaliseIA, Campo, Documento, Pedido, Sessao
from .servicos import trilha

TIPO_POR_COMPROVANTE = {
    "matricula": "matricula",
    "compra_venda": "compra_venda",
    "locacao": "locacao",
}


def proximo(sessao):
    consulta = (
        select(AnaliseIA)
        .where(AnaliseIA.status == "fila")
        .order_by(AnaliseIA.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return sessao.execute(consulta).scalar_one_or_none()


def _contexto(pedido):
    erp = ERP()
    unidade = erp.unidade(pedido.unidade_id) if pedido.unidade_id else None
    condominio = next(
        (item["nome"] for item in erp.condominios() if item["id"] == pedido.condominio_id),
        None,
    )
    proprietarios = [
        responsavel
        for responsavel in (unidade or {}).get("responsaveis", [])
        if responsavel["papel"] == "proprietario"
    ]
    return unidade, condominio, proprietarios


def _gravar_campos(sessao, pedido, criterios):
    for antigo in sessao.query(Campo).filter_by(pedido_id=pedido.id).all():
        sessao.delete(antigo)
    for criterio in criterios:
        sessao.add(Campo(
            pedido_id=pedido.id,
            nome=criterio.nome,
            valor_informado=criterio.informado,
            valor_cadastro=criterio.cadastro,
            valor_extraido=criterio.extraido,
            evidencia=criterio.evidencia,
            peso=criterio.peso,
            atendido=criterio.atendido,
        ))


def _sem_ia(sessao, analise, pedido, tipo, motivo):
    _gravar_campos(sessao, pedido, indice.criterios_sem_ia(tipo, pedido, *_contexto(pedido)))
    pedido.indice = None
    trilha.registrar(sessao, "analise_ia_sem_indice", "ia", pedido_id=pedido.id, motivo=motivo)


def processar(sessao, analise):
    documento = sessao.get(Documento, analise.documento_id)
    pedido = sessao.get(Pedido, documento.pedido_id)
    if pedido.aprovado_em or pedido.situacao.value not in ("a_conferir", "com_pendencia"):
        analise.status = "dispensada"
        analise.erro = "pedido já aprovado ou encerrado"
        trilha.registrar(
            sessao, "analise_ia_dispensada", "ia", pedido_id=pedido.id, motivo=analise.erro
        )
        return
    tipo = TIPO_POR_COMPROVANTE[documento.tipo.value]
    if not config.IA_HABILITADA:
        analise.status = "ia_desligada"
        _sem_ia(sessao, analise, pedido, tipo, analise.status)
        return
    carga = extracao.carregar_modelo()
    if carga > 1:
        print(f"análise {analise.id}: modelo carregado em {carga:.0f} s", flush=True)
    inicio = time.time()
    try:
        texto, origem = extracao.texto_do_documento(documento.caminho, documento.mime)
        extraido, _ = extracao.extrair(tipo, texto)
    except (extracao.TempoEsgotado, extracao.LeituraImpossivel) as erro:
        analise.status = "tempo_esgotado" if isinstance(erro, extracao.TempoEsgotado) else "falhou"
        analise.erro = str(erro) or type(erro).__name__
        analise.segundos = time.time() - inicio
        _sem_ia(sessao, analise, pedido, tipo, analise.status)
        return

    criterios, valor = indice.avaliar(tipo, extraido, pedido, *_contexto(pedido), texto)
    _gravar_campos(sessao, pedido, criterios)
    pedido.indice = valor
    analise.status = "concluida"
    analise.modelo = config.OLLAMA_MODELO
    analise.segundos = time.time() - inicio
    analise.resultado = {"origem": origem, "extraido": extraido}
    trilha.registrar(
        sessao,
        "analise_ia_concluida",
        "ia",
        pedido_id=pedido.id,
        indice=valor,
        segundos=round(analise.segundos),
        modelo=config.OLLAMA_MODELO,
        carga_modelo=round(carga),
    )


def _falha_inesperada(sessao, analise):
    documento = sessao.get(Documento, analise.documento_id)
    pedido = sessao.get(Pedido, documento.pedido_id)
    try:
        _sem_ia(sessao, analise, pedido, TIPO_POR_COMPROVANTE[documento.tipo.value], "falhou")
    except ServicoIndisponivel:
        for antigo in sessao.query(Campo).filter_by(pedido_id=pedido.id).all():
            sessao.delete(antigo)
        pedido.indice = None
        trilha.registrar(
            sessao, "analise_ia_sem_indice", "ia",
            pedido_id=pedido.id, motivo="falhou; ERP indisponível",
        )


def _executar_rotinas():
    try:
        resultado = rotinas.rodar()
        if any(resultado.values()):
            print(f"rotinas: {resultado}", flush=True)
    except Exception:
        print(traceback.format_exc()[-1500:], flush=True)


def _atender_proxima_analise(sessao):
    try:
        analise = proximo(sessao)
    except (OperationalError, ProgrammingError):
        print("banco indisponível ou sem tabelas; nova tentativa em 10 s", flush=True)
        time.sleep(10)
        return
    if not analise:
        sessao.rollback()
        time.sleep(5)
        return
    analise.status = "processando"
    sessao.commit()
    try:
        processar(sessao, analise)
    except (ServicoIndisponivel, extracao.IAIndisponivel) as erro:
        sessao.rollback()
        analise = sessao.get(AnaliseIA, analise.id)
        analise.status = "fila"
        analise.erro = str(erro)
        time.sleep(30)
    except Exception:
        sessao.rollback()
        analise = sessao.get(AnaliseIA, analise.id)
        analise.status = "falhou"
        analise.erro = traceback.format_exc()[-2000:]
        _falha_inesperada(sessao, analise)
    analise.concluido_em = datetime.now()
    sessao.commit()
    print(f"análise {analise.id}: {analise.status}", flush=True)


def rodar():
    print(f"worker da IA no ar (modelo {config.OLLAMA_MODELO}, limite {config.IA_TEMPO_LIMITE}s, "
          f"IA {'habilitada' if config.IA_HABILITADA else 'desligada'})", flush=True)
    ultima_rotina = 0
    while True:
        if time.time() - ultima_rotina > 300:
            ultima_rotina = time.time()
            _executar_rotinas()
        with Sessao() as sessao:
            _atender_proxima_analise(sessao)


if __name__ == "__main__":
    rodar()
