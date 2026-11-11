"""
Backup automático do banco SQLite (RNF-008).

Usa a API de backup online do SQLite (`sqlite3.Connection.backup`), que gera
uma cópia consistente mesmo com a aplicação em uso e com o banco em modo WAL
— ao contrário de copiar o arquivo, que pode capturar uma transação pela
metade. Mantém as BACKUP_RETENCAO_DIAS cópias mais recentes.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import config

_PREFIXO = "carteira-"
_PADRAO = f"{_PREFIXO}*.db"


def _copiar(origem: Path, destino: Path, verificar: bool = False) -> None:
    """Cópia consistente pela API de backup online do SQLite."""
    fonte = sqlite3.connect(origem)
    alvo = sqlite3.connect(destino)
    try:
        with alvo:
            fonte.backup(alvo)
        if verificar:
            ok = alvo.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise RuntimeError(f"Backup corrompido: {ok}")
    finally:
        alvo.close()
        fonte.close()


def executar_backup(destino_dir: Path | None = None, origem: Path | None = None) -> Path:
    destino_dir = destino_dir or config.BACKUP_DIR
    origem = origem or config.DB_PATH
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / f"{_PREFIXO}{datetime.now():%Y%m%d-%H%M%S-%f}.db"
    _copiar(origem, destino, verificar=True)
    rotacionar(destino_dir)
    return destino


def rotacionar(destino_dir: Path | None = None, manter: int | None = None) -> list[Path]:
    destino_dir = destino_dir or config.BACKUP_DIR
    manter = config.BACKUP_RETENCAO_DIAS if manter is None else manter
    copias = sorted(destino_dir.glob(_PADRAO))
    removidas = copias[:-manter] if manter and len(copias) > manter else []
    for c in removidas:
        c.unlink(missing_ok=True)
    return removidas


def listar(destino_dir: Path | None = None) -> list[Path]:
    return sorted((destino_dir or config.BACKUP_DIR).glob(_PADRAO), reverse=True)


def restaurar(copia: Path, destino: Path | None = None) -> None:
    """Restaura uma cópia sobre o banco (usar com a aplicação parada)."""
    _copiar(copia, destino or config.DB_PATH)
