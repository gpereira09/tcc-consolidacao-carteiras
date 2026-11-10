"""
Visões derivadas da consolidação: alocação por região, país, classe agregada
e setor (RF-020, RF-021), variação no período (RF-019) e cota unitizada.
"""
from __future__ import annotations

from datetime import date

import pytest

from src import operacoes
from src.calc import consolidacao
from tests.apoio import cadastrar_acao, preco, usuario_de


class TestVisoesDeAlocacao:
    def test_pais_regiao_macro_setor(self, sessao, carteira_populada):
        snap = consolidacao.snapshot(sessao, carteira_populada.id)
        regioes = snap.por("regiao")
        assert set(regioes) <= {"Brasil", "Offshore"} and "Offshore" in regioes
        assert "Estados Unidos" in snap.por("pais")
        assert sum(snap.por("macro_classe").values()) == pytest.approx(snap.patrimonio)
        assert "Alternativos (PE/VC/RE)" in snap.por("macro_classe")
        assert any(k for k in snap.por("setor") if k)

    def test_variacao_expurga_aportes(self, sessao, carteira):
        """
        100 ações a 10 em 02/01/2024; compra de 50 a 10,50 em 16/01; preço 11 em
        31/01. V₀ = 1.000, V₁ = 1.650, aporte de 525 a 15 dos 29 dias do fim:
        Dietz = (1.650 − 1.000 − 525) / (1.000 + 525 · 15/29) = 125 / 1.271,55.
        A variação absoluta (650) inclui o aporte; o retorno o expurga.
        """
        pos = cadastrar_acao(sessao, carteira, "VAR3")
        operacoes.registrar_transacao(sessao, usuario_de(sessao, carteira), pos.id, tipo="COMPRA",
                                      data_transacao=date(2024, 1, 16), quantidade=50, preco=10.5)
        preco(sessao, pos, date(2024, 1, 31), 11.0)

        v = consolidacao.variacao(sessao, carteira.id, date(2024, 1, 2), date(2024, 1, 31))
        assert v.variacao_absoluta == pytest.approx(650.0)
        assert v.fluxo_liquido == pytest.approx(525.0)
        assert v.retorno == pytest.approx(125 / (1000 + 525 * 15 / 29), abs=1e-12)

    def test_cota_base_100(self, sessao, carteira_populada):
        rent = consolidacao.rentabilidade(sessao, carteira_populada.id)
        assert rent.cota[0][1] == pytest.approx(100.0)
        assert rent.cota[-1][1] / 100 - 1 == pytest.approx(rent.twr, rel=1e-9)
