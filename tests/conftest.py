"""
Configuração da suíte de testes.

Todo o diretório de dados (banco, chaves, exportações, backups, e-mails) é
apontado para uma pasta temporária por variável de ambiente ANTES de `config`
ser importado: nenhum teste toca a pasta `data/` do projeto. A pasta é única
por execução, então duas suítes simultâneas não disputam os mesmos arquivos.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_DADOS_TESTE = Path(tempfile.mkdtemp(prefix="carteira_pytest_"))
os.environ["CARTEIRA_DATA_DIR"] = str(_DADOS_TESTE)
os.environ["CARTEIRA_DB"] = str(_DADOS_TESTE / "teste.db")
os.environ["CARTEIRA_AGENDADOR"] = "0"
os.environ.pop("CARTEIRA_DATA_REFERENCIA", None)
os.environ.pop("CARTEIRA_AES_KEY", None)


def _limpar_dados() -> None:
    from src import db
    db._engine.dispose()
    shutil.rmtree(_DADOS_TESTE, ignore_errors=True)


atexit.register(_limpar_dados)

import pytest

from src import security
from src.db import SessionLocal, resetar_schema
from tests.apoio import base_modelo, carteira_do_email, criar_usuario, popular


@pytest.fixture(autouse=True)
def _limitador_limpo():
    """O rate limiting (RNF-011) é global ao processo; cada teste começa zerado."""
    security.limitador.limpar()
    yield
    security.limitador.limpar()


@pytest.fixture()
def sessao():
    resetar_schema()
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def carteira(sessao):
    """Cliente de teste com carteira principal já criada."""
    criar_usuario(sessao, "teste@exemplo.com", "Investidor Teste")
    return carteira_do_email(sessao, "teste@exemplo.com")


@pytest.fixture()
def carteira_populada(sessao, tmp_path):
    """Carteira com as três classes de ativo importadas e séries de mercado."""
    def montar(s):
        usuario = criar_usuario(s, "teste@exemplo.com", "Investidor Teste")
        popular(s, usuario, carteira_do_email(s, usuario.email), tmp_path)

    base_modelo(sessao, "carteira_populada", montar)
    return carteira_do_email(sessao, "teste@exemplo.com")
