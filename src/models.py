"""
Modelo de dados relacional (capítulo 7 do TCC).

Implementa as 17 tabelas do MER em SQLAlchemy 2.0 ORM sobre SQLite.
A modelagem segue a 3FN e usa UUID v4 (TEXT) como chave primária, o que
preserva a portabilidade para PostgreSQL em evolução futura.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import config


def _uuid() -> str:
    return str(uuid.uuid4())


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _check(coluna: str, valores: tuple[str, ...]) -> str:
    lista = ", ".join(f"'{v}'" for v in valores)
    return f"{coluna} IN ({lista})"


class Base(DeclarativeBase):
    pass


class Usuario(Base):
    __tablename__ = "usuario"
    __table_args__ = (CheckConstraint(_check("perfil", config.PERFIS), name="ck_usuario_perfil"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    senha_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    perfil: Mapped[str] = mapped_column(String(20), nullable=False, default="CLIENTE")
    data_criacao: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_agora)
    ultimo_login: Mapped[datetime | None] = mapped_column(DateTime)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tentativas_falhas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bloqueado_ate: Mapped[datetime | None] = mapped_column(DateTime)
    totp_segredo_cifrado: Mapped[str | None] = mapped_column(Text)
    totp_ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    cliente: Mapped["Cliente | None"] = relationship(
        back_populates="usuario",
        cascade="all, delete-orphan",
        uselist=False,
        foreign_keys="Cliente.usuario_id",
    )


class Cliente(Base):
    """Investidor pessoa física de alta renda, dono do patrimônio consolidado."""

    __tablename__ = "cliente"
    __table_args__ = (
        CheckConstraint(_check("moeda_base", config.MOEDAS), name="ck_cliente_moeda"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    nome: Mapped[str] = mapped_column(String(150), nullable=False)
    cpf_cifrado: Mapped[str | None] = mapped_column(Text)
    perfil_risco: Mapped[str] = mapped_column(String(20), nullable=False, default="MODERADO")
    moeda_base: Mapped[str] = mapped_column(String(3), nullable=False, default=config.MOEDA_BASE_PADRAO)
    consentimento_lgpd: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data_consentimento: Mapped[datetime | None] = mapped_column(DateTime)
    colaborador_id: Mapped[str | None] = mapped_column(ForeignKey("usuario.id", ondelete="SET NULL"))

    usuario: Mapped[Usuario] = relationship(back_populates="cliente", foreign_keys=[usuario_id])
    carteiras: Mapped[list["Carteira"]] = relationship(
        back_populates="cliente", cascade="all, delete-orphan"
    )


class Carteira(Base):
    __tablename__ = "carteira"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    cliente_id: Mapped[str] = mapped_column(
        ForeignKey("cliente.id", ondelete="CASCADE"), nullable=False
    )
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    data_criacao: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_agora)

    cliente: Mapped[Cliente] = relationship(back_populates="carteiras")
    posicoes: Mapped[list["Posicao"]] = relationship(
        back_populates="carteira", cascade="all, delete-orphan"
    )


class Ativo(Base):
    """
    Cadastro mestre. `possui_ticker_listado` governa o enriquecimento (RN-006).

    Ativos listados (ações, ETFs, FIIs...) são cadastro comum a todos os
    clientes: `carteira_id` é nulo e a chave é (ticker, mercado). Fundos
    ilíquidos e imóveis pertencem a uma única carteira (`carteira_id`): dois
    clientes com o mesmo identificador "FUND-XYZ" têm registros distintos,
    com eventos, avaliações e exclusão LGPD independentes.
    """

    __tablename__ = "ativo"
    __table_args__ = (
        UniqueConstraint("ticker_canonico", "mercado", name="uq_ativo_ticker_mercado"),
        CheckConstraint(_check("moeda", config.MOEDAS), name="ck_ativo_moeda"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ticker_canonico: Mapped[str] = mapped_column(String(50), nullable=False)
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    classe: Mapped[str] = mapped_column(String(30), nullable=False)
    moeda: Mapped[str] = mapped_column(String(3), nullable=False)
    mercado: Mapped[str | None] = mapped_column(String(20))
    setor: Mapped[str | None] = mapped_column(String(100))
    pais: Mapped[str] = mapped_column(String(40), nullable=False, default="BRASIL")
    possui_ticker_listado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    carteira_id: Mapped[str | None] = mapped_column(
        ForeignKey("carteira.id", ondelete="CASCADE"), index=True
    )

    iliquido: Mapped["AtivoIliquido | None"] = relationship(
        back_populates="ativo", cascade="all, delete-orphan", uselist=False
    )
    imovel: Mapped["AtivoImovel | None"] = relationship(
        back_populates="ativo", cascade="all, delete-orphan", uselist=False
    )
    precos: Mapped[list["PrecoHistorico"]] = relationship(
        back_populates="ativo", cascade="all, delete-orphan"
    )


class AtivoIliquido(Base):
    """Especialização de ATIVO para fundos de Private Equity / Venture Capital."""

    __tablename__ = "ativo_iliquido"
    __table_args__ = (
        CheckConstraint(_check("sub_classe", config.SUBCLASSES_ILIQUIDAS), name="ck_iliq_subclasse"),
        CheckConstraint("commitment >= 0", name="ck_iliq_commitment"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ativo_id: Mapped[str] = mapped_column(
        ForeignKey("ativo.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    identificador_fundo: Mapped[str] = mapped_column(String(60), nullable=False)
    sub_classe: Mapped[str] = mapped_column(String(30), nullable=False)
    gestor: Mapped[str] = mapped_column(String(150), nullable=False)
    commitment: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    paid_in_capital: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    dry_powder: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    nav_atual: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    data_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    data_fim_prevista: Mapped[date | None] = mapped_column(Date)

    ativo: Mapped[Ativo] = relationship(back_populates="iliquido")
    eventos: Mapped[list["EventoPE"]] = relationship(
        back_populates="fundo", cascade="all, delete-orphan"
    )


class AtivoImovel(Base):
    """Especialização de ATIVO para imóveis diretos (seção 7.2)."""

    __tablename__ = "ativo_imovel"
    __table_args__ = (
        CheckConstraint(_check("tipo_imovel", config.TIPOS_IMOVEL), name="ck_imovel_tipo"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ativo_id: Mapped[str] = mapped_column(
        ForeignKey("ativo.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    identificador_imovel: Mapped[str] = mapped_column(String(60), nullable=False)
    tipo_imovel: Mapped[str] = mapped_column(String(20), nullable=False)
    endereco: Mapped[str | None] = mapped_column(Text)
    valor_aquisicao: Mapped[float] = mapped_column(Float, nullable=False)
    valor_de_mercado: Mapped[float] = mapped_column(Float, nullable=False)
    aluguel_mensal: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    despesas_mensais: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    area_m2: Mapped[float | None] = mapped_column(Float)
    data_avaliacao: Mapped[date] = mapped_column(Date, nullable=False)

    ativo: Mapped[Ativo] = relationship(back_populates="imovel")


class AssetMapping(Base):
    """Resolve nomenclaturas divergentes entre fontes para um mesmo ativo (seção 5.8)."""

    __tablename__ = "asset_mapping"
    __table_args__ = (
        UniqueConstraint("ticker_source", "fonte", name="uq_mapping_source_fonte"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ticker_source: Mapped[str] = mapped_column(String(60), nullable=False)
    ticker_canonical: Mapped[str] = mapped_column(String(60), nullable=False)
    fonte: Mapped[str] = mapped_column(String(40), nullable=False)
    asset_type: Mapped[str | None] = mapped_column(String(30))


class Posicao(Base):
    __tablename__ = "posicao"
    __table_args__ = (
        UniqueConstraint("carteira_id", "ativo_id", name="uq_posicao_carteira_ativo"),
        CheckConstraint("quantidade >= 0", name="ck_posicao_qtd"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    carteira_id: Mapped[str] = mapped_column(
        ForeignKey("carteira.id", ondelete="CASCADE"), nullable=False
    )
    ativo_id: Mapped[str] = mapped_column(ForeignKey("ativo.id", ondelete="CASCADE"), nullable=False)
    quantidade: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    preco_medio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    valor_financeiro: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    data_inicial: Mapped[date] = mapped_column(Date, nullable=False)
    observacao: Mapped[str | None] = mapped_column(Text)

    carteira: Mapped[Carteira] = relationship(back_populates="posicoes")
    ativo: Mapped[Ativo] = relationship()
    transacoes: Mapped[list["Transacao"]] = relationship(
        back_populates="posicao", cascade="all, delete-orphan"
    )


class Transacao(Base):
    __tablename__ = "transacao"
    __table_args__ = (
        CheckConstraint("tipo IN ('COMPRA','VENDA','PROVENTO')", name="ck_transacao_tipo"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    posicao_id: Mapped[str] = mapped_column(
        ForeignKey("posicao.id", ondelete="CASCADE"), nullable=False
    )
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    data: Mapped[date] = mapped_column(Date, nullable=False)
    quantidade: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    preco: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    taxas: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    posicao: Mapped[Posicao] = relationship(back_populates="transacoes")


class EventoPE(Base):
    """Capital calls, distribuições e atualizações de NAV (seção 2.4.2)."""

    __tablename__ = "evento_pe"
    __table_args__ = (
        CheckConstraint(_check("tipo_evento", config.TIPOS_EVENTO_PE), name="ck_evento_tipo"),
        CheckConstraint(_check("status", config.STATUS_EVENTO_PE), name="ck_evento_status"),
        Index("ix_evento_fundo_data", "fundo_id", "data_evento"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    fundo_id: Mapped[str] = mapped_column(
        ForeignKey("ativo_iliquido.id", ondelete="CASCADE"), nullable=False
    )
    data_evento: Mapped[date] = mapped_column(Date, nullable=False)
    tipo_evento: Mapped[str] = mapped_column(String(20), nullable=False)
    valor: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDENTE")
    nav_unidade: Mapped[float | None] = mapped_column(Float)
    subtipo: Mapped[str | None] = mapped_column(String(20))
    observacao: Mapped[str | None] = mapped_column(Text)

    fundo: Mapped[AtivoIliquido] = relationship(back_populates="eventos")


class PrecoHistorico(Base):
    __tablename__ = "preco_historico"
    __table_args__ = (
        UniqueConstraint("ativo_id", "data", name="uq_preco_ativo_data"),
        Index("ix_preco_ativo_data", "ativo_id", "data"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ativo_id: Mapped[str] = mapped_column(ForeignKey("ativo.id", ondelete="CASCADE"), nullable=False)
    data: Mapped[date] = mapped_column(Date, nullable=False)
    preco_fechamento: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float | None] = mapped_column(Float)
    fonte: Mapped[str] = mapped_column(String(40), nullable=False, default="TEMPLATE")

    ativo: Mapped[Ativo] = relationship(back_populates="precos")


class TaxaCambio(Base):
    __tablename__ = "taxa_cambio"
    __table_args__ = (
        UniqueConstraint("data", "moeda_origem", "moeda_destino", name="uq_cambio_data_par"),
        Index("ix_cambio_par_data", "moeda_origem", "moeda_destino", "data"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    data: Mapped[date] = mapped_column(Date, nullable=False)
    moeda_origem: Mapped[str] = mapped_column(String(3), nullable=False)
    moeda_destino: Mapped[str] = mapped_column(String(3), nullable=False)
    taxa: Mapped[float] = mapped_column(Float, nullable=False)
    fonte: Mapped[str] = mapped_column(String(40), nullable=False, default="BCB")


class Benchmark(Base):
    __tablename__ = "benchmark"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    moeda: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")

    valores: Mapped[list["BenchmarkValor"]] = relationship(
        back_populates="benchmark", cascade="all, delete-orphan"
    )


class BenchmarkValor(Base):
    __tablename__ = "benchmark_valor"
    __table_args__ = (
        UniqueConstraint("benchmark_id", "data", name="uq_bmk_valor"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    benchmark_id: Mapped[str] = mapped_column(
        ForeignKey("benchmark.id", ondelete="CASCADE"), nullable=False
    )
    data: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[float] = mapped_column(Float, nullable=False)

    benchmark: Mapped[Benchmark] = relationship(back_populates="valores")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_usuario_data", "usuario_id", "data_hora"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    usuario_id: Mapped[str | None] = mapped_column(String(36))
    data_hora: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_agora)
    acao: Mapped[str] = mapped_column(String(60), nullable=False)
    entidade: Mapped[str | None] = mapped_column(String(60))
    entidade_id: Mapped[str | None] = mapped_column(String(36))
    dados_anteriores: Mapped[str | None] = mapped_column(Text)
    dados_novos: Mapped[str | None] = mapped_column(Text)


class ImportacaoLog(Base):
    """Rastreabilidade de cada importação de template (UC-04)."""

    __tablename__ = "importacao_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    carteira_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tipo_template: Mapped[str] = mapped_column(String(20), nullable=False)
    nome_arquivo: Mapped[str] = mapped_column(String(255), nullable=False)
    data_hora: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_agora)
    linhas_lidas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    linhas_importadas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    linhas_rejeitadas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detalhe: Mapped[str | None] = mapped_column(Text)


class TokenSeguranca(Base):
    """
    Tokens persistidos de uso controlado: refresh token de sessão (JWT) e
    token de redefinição de senha (UC-03). Apenas o hash SHA-256 é gravado,
    de modo que um vazamento do banco não entrega tokens utilizáveis.
    """

    __tablename__ = "token_seguranca"
    __table_args__ = (
        CheckConstraint(_check("tipo", ("REFRESH", "RESET_SENHA")), name="ck_token_tipo"),
        Index("ix_token_hash", "token_hash", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False
    )
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    criado_em: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_agora)
    expira_em: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    usado_em: Mapped[datetime | None] = mapped_column(DateTime)
