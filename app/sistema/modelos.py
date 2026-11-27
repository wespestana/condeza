from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from . import config

motor = create_engine(config.DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
Sessao = sessionmaker(bind=motor, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class TipoPedido(StrEnum):
    TITULARIDADE = "titularidade"
    INCLUSAO_LOCATARIO = "inclusao_locatario"
    SAIDA_LOCATARIO = "saida_locatario"
    CONCILIACAO = "conciliacao"


class Situacao(StrEnum):
    RASCUNHO = "rascunho"
    A_CONFERIR = "a_conferir"
    COM_PENDENCIA = "com_pendencia"
    CONCLUIDO = "concluido"
    RECUSADO = "recusado"


class TipoDocumento(StrEnum):
    IDENTIDADE = "identidade"
    MATRICULA = "matricula"
    CONTRATO_COMPRA_VENDA = "compra_venda"
    CONTRATO_LOCACAO = "locacao"


class Perfil(StrEnum):
    CONFERENTE = "conferente"
    GERENTE = "gerente"
    TECNOLOGIA = "tecnologia"


def agora():
    return datetime.now()


class Usuario(Base):
    __tablename__ = "usuario"
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True)
    senha_hash: Mapped[str] = mapped_column(String(255))
    totp_segredo: Mapped[str] = mapped_column(String(64))
    perfil: Mapped[Perfil] = mapped_column(
        Enum(
            Perfil,
            values_callable=lambda enumeracao: [membro.value for membro in enumeracao],
        )
    )
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)


class AtivacaoConta(Base):
    __tablename__ = "ativacao_conta"
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now())
    expira_em: Mapped[datetime] = mapped_column(DateTime)
    usado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LinkAcesso(Base):
    __tablename__ = "link_acesso"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(160))
    cpf_cnpj: Mapped[str] = mapped_column(String(18))
    nome: Mapped[str] = mapped_column(String(160))
    condominio_id: Mapped[int] = mapped_column(Integer)
    unidade_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unidade_informada: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pedido_id: Mapped[int | None] = mapped_column(ForeignKey("pedido.id"), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    expira_em: Mapped[datetime] = mapped_column(DateTime)
    usado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)


class Pedido(Base):
    __tablename__ = "pedido"
    id: Mapped[int] = mapped_column(primary_key=True)
    numero: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    tipo: Mapped[TipoPedido] = mapped_column(
        Enum(
            TipoPedido,
            values_callable=lambda enumeracao: [membro.value for membro in enumeracao],
        )
    )
    situacao: Mapped[Situacao] = mapped_column(
        Enum(
            Situacao,
            values_callable=lambda enumeracao: [membro.value for membro in enumeracao],
        ),
        default=Situacao.RASCUNHO,
    )
    contestado: Mapped[bool] = mapped_column(Boolean, default=False)
    escalado: Mapped[bool] = mapped_column(Boolean, default=False)
    aguardando_matricula: Mapped[bool] = mapped_column(Boolean, default=False)
    canal: Mapped[str] = mapped_column(String(12), default="digital")
    condominio_id: Mapped[int] = mapped_column(Integer)
    unidade_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    solicitante_nome: Mapped[str] = mapped_column(String(160))
    solicitante_cpf_cnpj: Mapped[str] = mapped_column(String(18))
    solicitante_email: Mapped[str] = mapped_column(String(160))
    tipo_comprovante: Mapped[str | None] = mapped_column(String(20), nullable=True)
    data_chaves: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    dados_informados: Mapped[dict] = mapped_column(JSON, default=dict)
    recebido_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    documentacao_completa_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    indice: Mapped[int | None] = mapped_column(Integer, nullable=True)
    motivo_recusa: Mapped[str | None] = mapped_column(Text, nullable=True)
    aprovado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    aprovado_por: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), nullable=True)
    responsaveis_novos: Mapped[list | None] = mapped_column(JSON, nullable=True)
    gravado_erp_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    gravado_app_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    erro_gravacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    eliminado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=agora, onupdate=agora)

    documentos: Mapped[list["Documento"]] = relationship(back_populates="pedido")
    pendencias: Mapped[list["Pendencia"]] = relationship(back_populates="pedido")
    campos: Mapped[list["Campo"]] = relationship(back_populates="pedido")


class Documento(Base):
    __tablename__ = "documento"
    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedido.id"))
    tipo: Mapped[TipoDocumento] = mapped_column(
        Enum(
            TipoDocumento,
            values_callable=lambda enumeracao: [membro.value for membro in enumeracao],
        )
    )
    caminho: Mapped[str] = mapped_column(String(255))
    nome_original: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(60))
    tamanho: Mapped[int] = mapped_column(Integer)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    eliminado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pedido: Mapped[Pedido] = relationship(back_populates="documentos")


class AnaliseIA(Base):
    __tablename__ = "analise_ia"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int] = mapped_column(ForeignKey("documento.id"))
    status: Mapped[str] = mapped_column(String(16), default="fila")
    modelo: Mapped[str | None] = mapped_column(String(60), nullable=True)
    segundos: Mapped[float | None] = mapped_column(nullable=True)
    resultado: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Campo(Base):
    __tablename__ = "campo"
    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedido.id"))
    nome: Mapped[str] = mapped_column(String(60))
    valor_informado: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor_cadastro: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor_extraido: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidencia: Mapped[str | None] = mapped_column(Text, nullable=True)
    peso: Mapped[int] = mapped_column(Integer, default=1)
    atendido: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    conferido_por: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), nullable=True)
    conferido_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    conferencia: Mapped[str | None] = mapped_column(String(12), nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    pedido: Mapped[Pedido] = relationship(back_populates="campos")


class Pendencia(Base):
    __tablename__ = "pendencia"
    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedido.id"))
    descricao: Mapped[str] = mapped_column(Text)
    depende_solicitante: Mapped[bool] = mapped_column(Boolean, default=True)
    criada_por: Mapped[int] = mapped_column(ForeignKey("usuario.id"))
    criada_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    respondida_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resposta: Mapped[str | None] = mapped_column(Text, nullable=True)
    pedido: Mapped[Pedido] = relationship(back_populates="pendencias")


class AvisoProprietario(Base):
    __tablename__ = "aviso_proprietario"
    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedido.id"))
    nome: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(160))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    enviado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    contestado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Controle(Base):
    __tablename__ = "controle"
    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(String(255))


class TentativaToken(Base):
    __tablename__ = "tentativa_token"
    id: Mapped[int] = mapped_column(primary_key=True)
    ip: Mapped[str] = mapped_column(String(45))
    criada_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class AvisoReemissao(Base):
    __tablename__ = "aviso_reemissao"
    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedido.id"))
    descricao: Mapped[str] = mapped_column(Text)
    emissao_verificada: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    resolvido_por: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), nullable=True)
    resolvido_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LembreteMatricula(Base):
    __tablename__ = "lembrete_matricula"
    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedido.id"))
    enviado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class Evento(Base):
    __tablename__ = "evento"
    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int | None] = mapped_column(ForeignKey("pedido.id"), nullable=True)
    tipo: Mapped[str] = mapped_column(String(40))
    autor: Mapped[str] = mapped_column(String(160))
    detalhe: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
