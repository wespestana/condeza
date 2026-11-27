import argparse
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import config
from .modelos import LembreteMatricula, LinkAcesso, Pedido, Sessao, Situacao, TentativaToken
from .gateways import ServicoIndisponivel
from .servicos import conciliacao, email, gravacao, trilha


def meses_completos(inicio: date, hoje: date) -> int:
    meses = (hoje.year - inicio.year) * 12 + hoje.month - inicio.month
    return meses - (1 if hoje.day < inicio.day else 0)


def reprocessar_gravacoes(sessao):
    pendentes = sessao.query(Pedido).filter(
        Pedido.aprovado_em.is_not(None), Pedido.situacao == Situacao.A_CONFERIR
    ).all()
    for pedido in pendentes:
        gravacao.gravar(sessao, pedido)
        sessao.commit()
    return len(pendentes)


def lembretes_matricula(sessao, hoje: date):
    enviados = 0
    aguardando = sessao.query(Pedido).filter(
        Pedido.aguardando_matricula.is_(True), Pedido.situacao == Situacao.CONCLUIDO
    ).all()
    for pedido in aguardando:
        devidos = meses_completos(pedido.concluido_em.date(), hoje)
        feitos = sessao.query(LembreteMatricula).filter_by(pedido_id=pedido.id).count()
        if devidos > feitos:
            email.enviar(
                pedido.solicitante_email,
                f"Pedido {pedido.numero}: envie a matrícula quando for registrada",
                "lembrete_matricula",
                pedido=pedido,
                url=f"{config.URL_PUBLICA}/titularidade",
            )
            sessao.add(LembreteMatricula(pedido_id=pedido.id))
            trilha.registrar(
                sessao, "lembrete_matricula", "sistema", pedido_id=pedido.id, mes=feitos + 1
            )
            sessao.commit()
            enviados += 1
    return enviados


def encerrar_pendencias(sessao, hoje: date):
    limite = datetime.combine(hoje - timedelta(days=config.PENDENCIA_SEM_RESPOSTA_DIAS), time.max)
    encerrados = 0
    for pedido in sessao.query(Pedido).filter(Pedido.situacao == Situacao.COM_PENDENCIA).all():
        abertas = [
            pendencia.criada_em
            for pendencia in pedido.pendencias
            if pendencia.depende_solicitante and not pendencia.respondida_em
        ]
        if abertas and min(abertas) <= limite:
            pedido.situacao = Situacao.RECUSADO
            pedido.motivo_recusa = "Pendência não respondida no prazo."
            trilha.registrar(
                sessao,
                "pedido_encerrado_por_prazo",
                "sistema",
                pedido_id=pedido.id,
                desde=min(abertas).isoformat(),
            )
            email.enviar(
                pedido.solicitante_email,
                f"Pedido {pedido.numero} recusado",
                "recusado",
                pedido=pedido,
            )
            trilha.registrar(sessao, "email_recusado", "sistema", pedido_id=pedido.id)
            sessao.commit()
            encerrados += 1
    return encerrados


def eliminar_rascunhos(sessao, hoje: date):
    limite = datetime.combine(hoje - timedelta(days=config.RASCUNHO_DIAS), time.max)
    apagados = 0
    rascunhos = sessao.query(Pedido).filter(
        Pedido.situacao == Situacao.RASCUNHO,
        Pedido.eliminado_em.is_(None),
        Pedido.atualizado_em <= limite,
    ).all()
    for pedido in rascunhos:
        for documento in pedido.documentos:
            Path(documento.caminho).unlink(missing_ok=True)
            documento.eliminado_em = documento.eliminado_em or datetime.now()
            documento.caminho = ""
            documento.nome_original = "(eliminado)"
        for link in sessao.query(LinkAcesso).filter_by(pedido_id=pedido.id):
            link.email = "(eliminado)"
            link.cpf_cnpj = "(eliminado)"
            link.nome = "(eliminado)"
            link.ip = None
        pedido.solicitante_nome = "(eliminado)"
        pedido.solicitante_cpf_cnpj = "(eliminado)"
        pedido.solicitante_email = "(eliminado)"
        pedido.dados_informados = {}
        pedido.eliminado_em = datetime.now()
        trilha.registrar(
            sessao, "rascunho_eliminado", "sistema", pedido_id=pedido.id, dias=config.RASCUNHO_DIAS
        )
        sessao.commit()
        apagados += 1
    return apagados


def eliminar_documentos_condominios_encerrados(sessao, hoje: date):
    from .gateways import ERP
    from .servicos import retencao
    encerrados = [
        condominio["id"]
        for condominio in ERP().condominios()
        if condominio.get("fim_administracao")
        and date.fromisoformat(condominio["fim_administracao"]) <= hoje
    ]
    total = 0
    concluidos = sessao.query(Pedido).filter(
        Pedido.situacao == Situacao.CONCLUIDO, Pedido.condominio_id.in_(encerrados)
    ).all()
    for pedido in concluidos:
        total += retencao.eliminar_documentos(
            sessao, pedido, "sistema", "fim da administração do condomínio"
        )
        sessao.commit()
    return total


def limpar_tentativas(sessao):
    limite = datetime.now() - timedelta(days=1)
    quantidade = sessao.query(TentativaToken).filter(TentativaToken.criada_em < limite).delete()
    sessao.commit()
    return quantidade


_app = None


def rodar(hoje: date | None = None):
    global _app
    if _app is None:
        from . import create_app
        _app = create_app()
    with _app.app_context(), Sessao() as sessao:
        gravacoes = reprocessar_gravacoes(sessao)
        try:
            conciliacoes = conciliacao.importar(sessao)
        except ServicoIndisponivel:
            sessao.rollback()
            conciliacoes = None
        hoje = hoje or date.today()
        lembretes = lembretes_matricula(sessao, hoje)
        pendencias = encerrar_pendencias(sessao, hoje)
        rascunhos = eliminar_rascunhos(sessao, hoje)
        try:
            documentos = eliminar_documentos_condominios_encerrados(sessao, hoje)
        except ServicoIndisponivel:
            sessao.rollback()
            documentos = None
        tentativas = limpar_tentativas(sessao)
    return {
        "gravacoes_pendentes": gravacoes,
        "lembretes_enviados": lembretes,
        "pendencias_encerradas": pendencias,
        "rascunhos_eliminados": rascunhos,
        "conciliacoes": conciliacoes,
        "documentos_eliminados": documentos,
        "tentativas_apagadas": tentativas,
    }


if __name__ == "__main__":
    analisador = argparse.ArgumentParser()
    analisador.add_argument(
        "--hoje", type=date.fromisoformat, help="data simulada (AAAA-MM-DD), só para teste"
    )
    print(rodar(analisador.parse_args().hoje))
