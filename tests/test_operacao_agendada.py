"""
Operação do sistema: backup diário consistente (RNF-008), coleta agendada de
preços tolerante a falha de rede (RF-013, RNF-006), preenchimento automático
do cadastro de ativos listados (Quadro 16) e determinismo da carga sintética.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import config
from src import agendador, backup
from src.market import precos
from src.models import Ativo
from tests.apoio import cadastrar_acao


class TestOperacao:
    def test_backup_consistente_e_rotacao(self, sessao, carteira, tmp_path):
        for _ in range(3):
            copia = backup.executar_backup(tmp_path)
        assert sqlite3.connect(copia).execute("select count(*) from usuario").fetchone()[0] == 1
        removidas = backup.rotacionar(tmp_path, manter=1)
        assert len(removidas) == 2 and len(backup.listar(tmp_path)) == 1

    def test_coleta_agendada_tolera_falha_de_rede(self, sessao, carteira, monkeypatch):
        cadastrar_acao(sessao, carteira, "VALE3", quantidade=1, preco=60, dia=date(2024, 1, 1))
        monkeypatch.setattr(precos, "_coletar_yahoo", lambda *a, **k: None)
        resumo = agendador.coletar_precos(coletor_cambio=lambda s, m, i: 0,
                                          coletor_benchmarks=lambda s, i: {"CDI": 0})
        assert resumo.ativos == 1 and resumo.falhas == ["VALE3"]
        assert resumo.cambio == {"USD": 0, "EUR": 0, "GBP": 0}
        assert resumo.benchmarks == {"CDI": 0}

    def test_coleta_yahoo_ignora_fechamento_vazio(self, monkeypatch):
        """RF-008: pregão com fechamento NaN não vira preço; vale o último válido."""
        import pandas as pd
        import yfinance

        def falso_ticker(fechamentos):
            dias = pd.to_datetime(["2024-01-02", "2024-01-03"])
            hist = pd.DataFrame({"Close": fechamentos}, index=dias)
            return lambda simbolo: type("T", (), {"history": lambda self, **k: hist})()

        monkeypatch.setattr(yfinance, "Ticker", falso_ticker([117.35, float("nan")]))
        assert precos._coletar_yahoo("VOD.L") == (date(2024, 1, 2), 117.35)
        monkeypatch.setattr(yfinance, "Ticker", falso_ticker([float("nan"), float("nan")]))
        assert precos._coletar_yahoo("VOD.L") is None

    def test_agendador_na_janela_do_rnf006(self):
        assert 19 <= config.COLETA_HORA < 22


class TestCadastroAutomatico:
    def test_preenche_nome_e_setor_sem_sobrescrever(self, sessao, carteira):
        pos = cadastrar_acao(sessao, carteira, "AAPL", "NASDAQ", "USD", quantidade=1, preco=100,
                             dia=date(2024, 1, 1))
        ativo = sessao.get(Ativo, pos.ativo_id)
        assert precos.completar_cadastro(sessao, ativo, lambda s: ("Apple Inc.", "Tecnologia"))
        assert (ativo.nome, ativo.setor) == ("Apple Inc.", "Tecnologia")
        assert not precos.completar_cadastro(sessao, ativo, lambda s: ("Outro", "Outro"))


def test_carga_sintetica_deterministica_entre_processos():
    """A semente não pode depender de hash() — aleatorizado a cada processo."""
    codigo = ("from src.market.seed_sintetico import _passeio; import zlib; from datetime import date;"
              "s=zlib.crc32(b'PETR4')%(2**31);"
              "print(_passeio(date(2024,1,1),date(2024,3,1),30.0,0.1,0.2,s)[-1])")
    saidas = {
        subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True,
                       cwd=Path(__file__).resolve().parents[1],
                       env={**os.environ, "PYTHONHASHSEED": str(h)}).stdout
        for h in (1, 2, 3)
    }
    assert len(saidas) == 1 and saidas.pop().strip()
