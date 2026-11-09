"""Testes das metodologias de rentabilidade (seção 2.4.1)."""
from __future__ import annotations

from datetime import date

import pytest

from src.calc import rentabilidade as r


class TestTWR:
    def test_exemplo_do_tcc(self):
        """
        Exemplo da seção 2.4.1: 5% no primeiro mês, aporte de R$ 5.000,
        depois 2% no segundo mês. O TWR deve ser 7,1%, independentemente
        do aporte intermediário.
        """
        resultado = r.twr_subperiodos([0.05, 0.02])
        assert resultado == pytest.approx(0.071, abs=1e-6)

    def test_aporte_nao_vira_rentabilidade(self):
        """Carteira que só recebe aporte, sem valorizar, tem TWR zero."""
        serie = [(date(2025, 1, 31), 100_000.0), (date(2025, 2, 28), 150_000.0)]
        fluxos = [(date(2025, 2, 1), -50_000.0)]
        assert r.twr(serie, fluxos) == pytest.approx(0.0, abs=1e-6)

    def test_dietz_pondera_pelo_tempo_investido(self):
        """
        Aporte no meio do período rende apenas metade do período: a base do
        Dietz modificado é V_ini + 0,5·CF, não V_ini + CF.
        """
        serie = [(date(2025, 1, 1), 100_000.0), (date(2025, 3, 2), 165_000.0)]
        fluxos = [(date(2025, 1, 31), -50_000.0)]
        retorno = r.twr(serie, fluxos)
        assert retorno == pytest.approx(0.12, abs=1e-12)

    def test_resgate_nao_vira_prejuizo(self):
        serie = [(date(2025, 1, 31), 100_000.0), (date(2025, 2, 28), 70_000.0)]
        fluxos = [(date(2025, 2, 1), 30_000.0)]
        assert r.twr(serie, fluxos) == pytest.approx(0.0, abs=1e-6)

    def test_serie_insuficiente(self):
        assert r.twr([(date(2025, 1, 1), 100.0)]) == 0.0
        assert r.twr([]) == 0.0

    def test_anualizacao(self):
        assert r.anualizar(0.21, 730) == pytest.approx(0.10, abs=1e-12)


class TestIRR:
    def test_caso_simples(self):
        """−1.000 hoje, +1.100 em um ano ⇒ 10% a.a."""
        fluxos = [(date(2023, 1, 1), -1000.0), (date(2024, 1, 1), 1100.0)]
        assert r.irr(fluxos) == pytest.approx(0.10, abs=1e-4)

    def test_convencao_actual_365(self):
        """
        Em ano bissexto o intervalo tem 366 dias. Sob actual/365 o expoente
        passa de 1,0 e a taxa fica levemente abaixo de 10% — comportamento
        esperado, não erro de convergência.
        """
        fluxos = [(date(2024, 1, 1), -1000.0), (date(2025, 1, 1), 1100.0)]
        taxa = r.irr(fluxos)
        assert taxa is not None
        assert 0.0990 < taxa < 0.10

    def test_multiplos_fluxos(self):
        fluxos = [
            (date(2022, 1, 1), -1000.0),
            (date(2023, 1, 1), -500.0),
            (date(2024, 1, 1), 400.0),
            (date(2025, 1, 1), 1500.0),
        ]
        taxa = r.irr(fluxos)
        assert taxa is not None
        vpl = sum(v / (1 + taxa) ** ((d - date(2022, 1, 1)).days / 365)
                  for d, v in fluxos)
        assert vpl == pytest.approx(0.0, abs=1e-4)

    def test_sem_solucao_quando_sinal_unico(self):
        """Sem fluxo de sinal oposto não existe taxa: o retorno é None."""
        assert r.irr([(date(2024, 1, 1), -100.0), (date(2025, 1, 1), -100.0)]) is None
        assert r.irr([(date(2024, 1, 1), 100.0), (date(2025, 1, 1), 100.0)]) is None

    def test_fluxo_unico(self):
        assert r.irr([(date(2024, 1, 1), -100.0)]) is None

    def test_prejuizo_total_converge(self):
        """Perda severa: a bisseção deve achar taxa fortemente negativa."""
        taxa = r.irr([(date(2023, 1, 1), -1000.0), (date(2025, 1, 1), 100.0)])
        assert taxa is not None and taxa < -0.5

    def test_mwr_e_alias_de_irr(self):
        assert r.mwr is r.irr


class TestRisco:
    def test_volatilidade_serie_constante_e_zero(self):
        serie = [(date(2025, 1, d), 100.0) for d in range(1, 20)]
        assert r.volatilidade(serie) == 0.0

    def test_anualizacao_respeita_frequencia(self):
        """Mesma dispersão em série mensal e diária gera fatores diferentes."""
        mensal = [(date(2025, m, 1), 100.0) for m in range(1, 13)]
        diaria = [(date(2025, 1, d), 100.0) for d in range(1, 29)]
        assert r.periodos_por_ano(mensal) == pytest.approx(12.0, abs=0.6)
        assert r.periodos_por_ano(diaria) == 252.0

    def test_max_drawdown(self):
        serie = [
            (date(2025, 1, 1), 100.0),
            (date(2025, 2, 1), 120.0),
            (date(2025, 3, 1), 90.0),
            (date(2025, 4, 1), 110.0),
        ]
        assert r.max_drawdown(serie) == pytest.approx(-0.25, abs=1e-6)

    def test_drawdown_nao_negativo_em_serie_crescente(self):
        serie = [(date(2025, m, 1), 100.0 + m) for m in range(1, 13)]
        assert r.max_drawdown(serie) == 0.0

    def test_sharpe_indefinido_sem_volatilidade(self):
        assert r.sharpe(0.10, 0.0, 0.05) is None

    def test_sharpe(self):
        assert r.sharpe(0.15, 0.20, 0.05) == pytest.approx(0.5)


class TestUnitizacao:
    def test_indice_ignora_captacao(self):
        """
        Carteira que dobra de tamanho apenas por aporte mantém cota em 100.
        É esse o ponto da unitização: separar captação de rentabilidade.
        """
        serie = [
            (date(2025, 1, 31), 100_000.0),
            (date(2025, 2, 28), 200_000.0),
            (date(2025, 3, 31), 300_000.0),
        ]
        fluxos = [(date(2025, 2, 1), -100_000.0), (date(2025, 3, 1), -100_000.0)]
        indice = r.serie_indice(serie, fluxos)
        assert all(valor == pytest.approx(100.0, abs=1e-6) for _, valor in indice)

    def test_indice_captura_valorizacao(self):
        serie = [(date(2025, 1, 31), 100_000.0), (date(2025, 2, 28), 110_000.0)]
        indice = r.serie_indice(serie)
        assert indice[-1][1] == pytest.approx(110.0, abs=1e-6)
