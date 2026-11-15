"""
C1 — a carga demonstrativa com séries simuladas (`--simulado`) é reproduzível:
com a mesma data de referência, os números são os mesmos em qualquer dia e máquina. Os valores conferidos
aqui são os publicados no README e no texto do TCC.
"""
from __future__ import annotations

import sys
from datetime import date

import pytest
from sqlalchemy import select

import config
from src.calc import consolidacao
from src.db import SessionLocal
from src.models import Carteira, Cliente, Usuario

sys.path.insert(0, str(config.BASE_DIR / "scripts"))


def test_seed_na_data_de_referencia(monkeypatch, capsys):
    import seed_demo

    monkeypatch.setenv("CARTEIRA_DATA_REFERENCIA", "")
    assert seed_demo.main(["--data", "2026-09-22", "--simulado"]) == 0
    saida = capsys.readouterr().out
    assert "data de referência: 22/09/2026" in saida

    dia = date(2026, 9, 22)
    with SessionLocal() as s:
        usuario = s.scalar(select(Usuario).where(Usuario.email == "investidor@exemplo.com"))
        carteira = s.scalar(select(Carteira).join(Cliente)
                            .where(Cliente.usuario_id == usuario.id))
        snap = consolidacao.snapshot(s, carteira.id, dia)
        assert snap.patrimonio == pytest.approx(17_307_431.30, abs=0.01)
        assert snap.custo == pytest.approx(12_417_450.54, abs=0.01)
        assert snap.alertas == []

        r = consolidacao.rentabilidade(s, carteira.id, fim=dia)
        assert r.twr == pytest.approx(0.425977, abs=1e-6)
        assert r.mwr == pytest.approx(0.074051, abs=1e-6)
        assert r.retorno_total == pytest.approx(0.428306, abs=1e-6)

        doze = consolidacao.rentabilidade(s, carteira.id, date(2025, 9, 22), dia)
        assert doze.mwr == pytest.approx(0.041561, abs=1e-6)
    assert config.hoje() == dia
