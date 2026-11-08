"""
Camada de persistência: engine, sessão e inicialização do schema.

O SQLite é configurado com WAL mode e enforcement de chaves estrangeiras
(desligado por padrão no SQLite), conforme justificado na seção 5.1.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import config
from src.models import (
    Ativo,
    AtivoIliquido,
    AtivoImovel,
    Base,
    EventoPE,
    Posicao,
    PrecoHistorico,
)

_engine: Engine = create_engine(
    config.DB_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)


@event.listens_for(_engine, "connect")
def _configura_sqlite(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


_COLUNAS_NOVAS = {
    "usuario": [
        ("totp_segredo_cifrado", "TEXT"),
        ("totp_ativo", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
    "evento_pe": [("subtipo", "VARCHAR(20)")],
    "ativo": [("carteira_id", "VARCHAR(36) REFERENCES carteira(id) ON DELETE CASCADE")],
}


def _migrar(conn) -> None:
    for tabela, colunas in _COLUNAS_NOVAS.items():
        existentes = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({tabela})")}
        if not existentes:
            continue
        for nome, ddl in colunas:
            if nome not in existentes:
                conn.exec_driver_sql(f"ALTER TABLE {tabela} ADD COLUMN {nome} {ddl}")


def _copiar(origem, modelo, **trocas):
    """Nova instância de `modelo` com as colunas de `origem`, exceto id e `trocas`."""
    campos = {c.key: getattr(origem, c.key) for c in modelo.__table__.columns if c.key != "id"}
    return modelo(**{**campos, **trocas})


def _isolar_ativos_iliquidos(s: Session) -> int:
    """
    Bases anteriores à separação por cliente casavam fundos e imóveis só pelo
    identificador: dois clientes com o mesmo "FUND-XYZ" dividiam um único
    registro. Cada ativo ilíquido passa a pertencer a uma carteira. Se várias
    carteiras usavam o mesmo registro, a primeira fica com ele e cada uma das
    demais recebe uma cópia (ativo, fundo ou imóvel, eventos e preços), com a
    sua posição apontando para a cópia. Nenhum registro é apagado.
    Devolve o número de cópias criadas.
    """
    sem_dono = s.scalars(
        select(Ativo).where(Ativo.carteira_id.is_(None),
                            or_(Ativo.iliquido.has(), Ativo.imovel.has()))
    ).all()
    copias = 0
    for ativo in sem_dono:
        posicoes = s.scalars(
            select(Posicao).where(Posicao.ativo_id == ativo.id).order_by(Posicao.id)
        ).all()
        if not posicoes:
            continue
        ativo.carteira_id = posicoes[0].carteira_id
        for pos in posicoes[1:]:
            novo = _copiar(ativo, Ativo, carteira_id=pos.carteira_id)
            s.add(novo)
            s.flush()
            if ativo.iliquido is not None:
                fundo = _copiar(ativo.iliquido, AtivoIliquido, ativo_id=novo.id)
                s.add(fundo)
                s.flush()
                s.add_all(_copiar(e, EventoPE, fundo_id=fundo.id) for e in ativo.iliquido.eventos)
            if ativo.imovel is not None:
                s.add(_copiar(ativo.imovel, AtivoImovel, ativo_id=novo.id))
            s.add_all(_copiar(p, PrecoHistorico, ativo_id=novo.id) for p in ativo.precos)
            pos.ativo_id = novo.id
            copias += 1
    return copias


def criar_schema() -> None:
    """Cria as tabelas do MER que ainda não existam e aplica migrações leves."""
    Base.metadata.create_all(_engine)
    with _engine.begin() as conn:
        _migrar(conn)
    with sessao() as s:
        _isolar_ativos_iliquidos(s)


def resetar_schema() -> None:
    """Destrói e recria o schema. Usado por scripts de seed e testes."""
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)


@contextmanager
def sessao() -> Iterator[Session]:
    """Sessão transacional: commit no sucesso, rollback em qualquer exceção."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
