from datetime import date, timedelta

from . import config

FERIADOS = {date.fromisoformat(data_texto) for data_texto in [
    "2026-01-01", "2026-01-25", "2026-04-03", "2026-04-21", "2026-05-01", "2026-07-09",
    "2026-09-07", "2026-10-12", "2026-11-02", "2026-11-15", "2026-11-20", "2026-12-25",
    "2027-01-01", "2027-01-25", "2027-03-26", "2027-04-21", "2027-05-01", "2027-07-09",
    "2027-09-07", "2027-10-12", "2027-11-02", "2027-11-15", "2027-11-20", "2027-12-25",
]}


def dia_util(data: date) -> bool:
    return data.weekday() < 5 and data not in FERIADOS


def somar_dias_uteis(inicio: date, quantidade: int) -> date:
    dia = inicio
    while quantidade > 0:
        dia += timedelta(days=1)
        if dia_util(dia):
            quantidade -= 1
    return dia


def prazos(pedido):
    if not pedido.recebido_em:
        return {}
    data_recebimento = pedido.recebido_em.date()
    resultado = {
        "pendencias": somar_dias_uteis(data_recebimento, config.PRAZO_PENDENCIAS_DIAS_UTEIS),
    }
    base = pedido.documentacao_completa_em or pedido.recebido_em
    resultado["conclusao"] = somar_dias_uteis(base.date(), config.PRAZO_CONCLUSAO_DIAS_UTEIS)
    return resultado


def alerta(vencimento: date | None, hoje: date | None = None):
    if not vencimento:
        return None
    hoje = hoje or date.today()
    return "vencido" if vencimento < hoje else "hoje" if vencimento == hoje else None
