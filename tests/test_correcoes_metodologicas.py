"""
Correções metodológicas [MET] verificadas contra valores calculados à mão.

Cada teste monta uma carteira mínima, com preços e câmbio conhecidos, e
compara o resultado do sistema com a conta feita independentemente no
próprio teste (fórmula fechada ou bisseção simples, sem usar src.calc).
"""
from __future__ import annotations

from datetime import date

import pytest

import config
from src import ingest, operacoes
from src.calc import consolidacao, pe, rentabilidade
from src.market import benchmarks, cambio
from tests.apoio import cadastrar_acao, criar_fundo, evento, preco, usuario_de


def _xirr_independente(fluxos):
    """XIRR Actual/365 por bisseção pura, escrito à parte do sistema."""
    t0 = min(d for d, _ in fluxos)

    def vpl(taxa):
        return sum(v / (1 + taxa) ** ((d - t0).days / 365) for d, v in fluxos)

    baixo, alto = -0.99, 10.0
    for _ in range(300):
        meio = (baixo + alto) / 2
        if vpl(baixo) * vpl(meio) <= 0:
            alto = meio
        else:
            baixo = meio
    return (baixo + alto) / 2


class TestMWRDePeriodoParcial:
    """
    100 ações compradas a 10 em 02/01/2024; valem 12 em 31/12/2024 e 13,20
    em 30/06/2025. Janela de 01/01/2025 a 30/06/2025, sem fluxos:
    V₀ = 1.200, V₁ = 1.320, 180 dias → MWR = 1,1^(365/180) − 1.
    Antes da correção, o MWR saía None e o retorno total, 0.
    """

    @pytest.fixture()
    def pos(self, sessao, carteira):
        pos = cadastrar_acao(sessao, carteira)
        preco(sessao, pos, date(2024, 12, 31), 12.0)
        preco(sessao, pos, date(2025, 6, 30), 13.2)
        return pos

    def test_janela_sem_fluxos(self, sessao, carteira, pos):
        r = consolidacao.rentabilidade(sessao, carteira.id, date(2025, 1, 1), date(2025, 6, 30))
        assert r.patrimonio_inicial == pytest.approx(1200.0)
        assert r.mwr == pytest.approx(1.1 ** (365 / 180) - 1, abs=1e-6)
        assert r.retorno_total == pytest.approx(0.10)
        assert r.twr == pytest.approx(0.10)
        assert r.aportes == 0

    def test_janela_com_aporte(self, sessao, carteira, pos):
        """Compra de 50 a 12,50 em 31/03/2025: V₁ = 150 × 13,20 = 1.980."""
        operacoes.registrar_transacao(sessao, usuario_de(sessao, carteira), pos.id,
                                      tipo="COMPRA", data_transacao=date(2025, 3, 31),
                                      quantidade=50, preco=12.5)
        r = consolidacao.rentabilidade(sessao, carteira.id, date(2025, 1, 1), date(2025, 6, 30))
        esperado = _xirr_independente([(date(2025, 1, 1), -1200.0),
                                       (date(2025, 3, 31), -625.0),
                                       (date(2025, 6, 30), 1980.0)])
        assert r.mwr == pytest.approx(esperado, abs=1e-6)
        assert r.aportes == pytest.approx(625.0)
        assert r.retorno_total == pytest.approx((1980 - 1200 - 625) / (1200 + 625))

    def test_desde_o_inicio_nao_muda(self, sessao, carteira, pos):
        """Desde a compra: −1.000 em 02/01/2024 e +1.320 em 30/06/2025."""
        r = consolidacao.rentabilidade(sessao, carteira.id, None, date(2025, 6, 30))
        esperado = _xirr_independente([(date(2024, 1, 2), -1000.0),
                                       (date(2025, 6, 30), 1320.0)])
        assert r.mwr == pytest.approx(esperado, abs=1e-6)
        assert r.retorno_total == pytest.approx(0.32)
        assert r.twr == pytest.approx(0.32)


class TestNAVAjustado:
    """
    Commitment 1.000; calls de 400 e 300; distribuição de 200; NAV de 800 em
    31/12/2023; nova distribuição de 300 em 01/03/2024 (depois do NAV).
    NAV ajustado = 800 − 300 = 500; TVPI = (500 + 500) / 700 = 1,4286.
    Antes: NAV 800 e TVPI (500 + 800) / 700 = 1,857 (dupla contagem).
    """

    @pytest.fixture()
    def fundo(self, sessao, carteira):
        f = criar_fundo(sessao, carteira)
        evento(sessao, f, date(2022, 6, 1), "CAPITAL_CALL", 400)
        evento(sessao, f, date(2022, 12, 1), "CAPITAL_CALL", 300)
        evento(sessao, f, date(2023, 6, 1), "DISTRIBUICAO", 200, "RECEBIDO", "GANHO_CAPITAL")
        evento(sessao, f, date(2023, 12, 31), "NAV_UPDATE", 800, "RECEBIDO")
        evento(sessao, f, date(2024, 3, 1), "DISTRIBUICAO", 300, "RECEBIDO", "GANHO_CAPITAL")
        return f

    def test_distribuicao_depois_do_nav(self, sessao, fundo):
        m = pe.calcular(fundo, sessao)
        assert m.nav == pytest.approx(500.0) and m.nav_ajustado
        assert m.tvpi == pytest.approx(1000 / 700)
        assert m.dpi == pytest.approx(500 / 700)

    def test_data_de_corte_anterior_a_distribuicao(self, sessao, fundo):
        m = pe.calcular(fundo, sessao, date(2024, 1, 31))
        assert m.nav == pytest.approx(800.0) and not m.nav_ajustado
        assert m.tvpi == pytest.approx((200 + 800) / 700)

    def test_call_pago_depois_do_nav_nao_vira_prejuizo(self, sessao, carteira):
        """
        Call de 400 e NAV de 400 em 31/12/2022; call de 300 pago em 15/03/2023.
        O patrimônio passa a 700 e o TWR do semestre é zero: o aporte não é
        rentabilidade negativa. Antes, o fundo continuava valendo 400 e o TWR
        do semestre ficava perto de −43%.
        """
        f = criar_fundo(sessao, carteira)
        evento(sessao, f, date(2022, 6, 1), "CAPITAL_CALL", 400)
        evento(sessao, f, date(2022, 12, 31), "NAV_UPDATE", 400, "RECEBIDO")
        evento(sessao, f, date(2023, 3, 15), "CAPITAL_CALL", 300)
        snap = consolidacao.snapshot(sessao, carteira.id, date(2023, 6, 30))
        assert snap.patrimonio == pytest.approx(700.0)
        r = consolidacao.rentabilidade(sessao, carteira.id, date(2022, 12, 31), date(2023, 6, 30))
        assert r.twr == pytest.approx(0.0, abs=1e-9)
        assert r.mwr == pytest.approx(0.0, abs=1e-6)


def test_custo_do_fundo_desconta_so_retorno_de_capital(sessao, carteira):
    """
    Pago 700; retorno de capital de 100; ganho de capital de 50; NAV 800.
    Custo líquido = 700 − 100 = 600; resultado = 800 − 600 = 200.
    """
    f = criar_fundo(sessao, carteira)
    evento(sessao, f, date(2022, 6, 1), "CAPITAL_CALL", 700)
    evento(sessao, f, date(2023, 1, 1), "DISTRIBUICAO", 100, "RECEBIDO", "RETORNO_CAPITAL")
    evento(sessao, f, date(2023, 2, 1), "DISTRIBUICAO", 50, "RECEBIDO", "GANHO_CAPITAL")
    evento(sessao, f, date(2023, 2, 1), "NAV_UPDATE", 800, "RECEBIDO")
    linha = consolidacao.snapshot(sessao, carteira.id, date(2023, 6, 30)).linhas[0]
    assert linha.custo_base == pytest.approx(600.0)
    assert linha.resultado == pytest.approx(200.0)


class TestCambio:
    def test_custo_pela_taxa_da_aquisicao(self, sessao, carteira):
        """
        10 ações a US$ 100 compradas com dólar a 5,00; avaliadas a US$ 100 com
        dólar a 6,00. Custo R$ 5.000, valor R$ 6.000: o resultado de R$ 1.000
        é o ganho cambial (antes, o custo era convertido a 6,00 e o resultado
        dava zero).
        """
        cambio.registrar_taxa(sessao, date(2024, 1, 2), "USD", "BRL", 5.0)
        cambio.registrar_taxa(sessao, date(2025, 6, 30), "USD", "BRL", 6.0)
        pos = cadastrar_acao(sessao, carteira, "AAA", "NYSE", "USD", 10, 100.0)
        preco(sessao, pos, date(2025, 6, 30), 100.0)
        snap = consolidacao.snapshot(sessao, carteira.id, date(2025, 6, 30))
        assert snap.patrimonio == pytest.approx(6000.0)
        assert snap.custo == pytest.approx(5000.0)
        assert snap.resultado == pytest.approx(1000.0)

    def test_venda_baixa_custo_historico_proporcional(self, sessao, carteira):
        """Compra 10 a 5,00 e 10 a 6,00; vende 10: custo restante = metade de 11.000."""
        cambio.registrar_taxa(sessao, date(2024, 1, 2), "USD", "BRL", 5.0)
        cambio.registrar_taxa(sessao, date(2024, 6, 3), "USD", "BRL", 6.0)
        pos = cadastrar_acao(sessao, carteira, "BBB", "NYSE", "USD", 10, 100.0)
        u = usuario_de(sessao, carteira)
        operacoes.registrar_transacao(sessao, u, pos.id, tipo="COMPRA",
                                      data_transacao=date(2024, 6, 3), quantidade=10, preco=100)
        operacoes.registrar_transacao(sessao, u, pos.id, tipo="VENDA",
                                      data_transacao=date(2024, 6, 3), quantidade=10, preco=100)
        snap = consolidacao.snapshot(sessao, carteira.id, date(2024, 12, 31))
        assert snap.custo == pytest.approx(5500.0)

    def test_sem_cambio_fica_fora_dos_totais_e_alerta(self, sessao, carteira):
        """Ação em USD sem nenhuma cotação USD/BRL: não entra somada como se fosse real."""
        cadastrar_acao(sessao, carteira, "BRL3", "B3", "BRL", 100, 10.0)
        cadastrar_acao(sessao, carteira, "CCC", "NYSE", "USD", 10, 100.0)
        snap = consolidacao.snapshot(sessao, carteira.id, date(2024, 3, 31))
        assert snap.patrimonio == pytest.approx(1000.0)
        assert snap.alertas and "USD/BRL" in snap.alertas[0]
        r = consolidacao.rentabilidade(sessao, carteira.id, None, date(2024, 3, 31))
        assert any("USD/BRL" in a for a in r.alertas)


def test_benchmark_em_dolar_convertido_para_a_moeda_base(sessao):
    """S&P 500 de 100 para 110 (+10% em USD) e dólar de 5,00 para 5,50 (+10%): +21% em BRL."""
    catalogo = benchmarks.garantir_catalogo(sessao)
    benchmarks.registrar_valor(sessao, catalogo["SP500"], date(2024, 1, 2), 100.0)
    benchmarks.registrar_valor(sessao, catalogo["SP500"], date(2024, 12, 31), 110.0)
    cambio.registrar_taxa(sessao, date(2024, 1, 2), "USD", "BRL", 5.0)
    cambio.registrar_taxa(sessao, date(2024, 12, 31), "USD", "BRL", 5.5)
    sessao.commit()
    comp = benchmarks.comparar(sessao, 0.0, date(2024, 1, 2), date(2024, 12, 31), "BRL",
                               ["SP500"])[0]
    assert comp.retorno == pytest.approx(0.21)
    assert comp.moeda == "BRL"
    em_dolar = benchmarks.comparar(sessao, 0.0, date(2024, 1, 2), date(2024, 12, 31), "USD",
                                   ["SP500"])[0]
    assert em_dolar.retorno == pytest.approx(0.10)


class TestIRRCasosDeBorda:
    def test_horizonte_nulo_nao_tem_irr(self):
        """Antes devolvia 0,10 (o chute inicial)."""
        d = date(2024, 1, 1)
        assert rentabilidade.irr([(d, -100.0), (d, 100.0)]) is None

    def test_fundo_com_uma_chamada_sem_nav(self, sessao, carteira):
        f = criar_fundo(sessao, carteira)
        evento(sessao, f, date(2022, 6, 1), "CAPITAL_CALL", 400)
        assert pe.calcular(f, sessao).irr is None

    def test_raizes_multiplas_sao_sinalizadas(self):
        """−100 / +230 / −132: o VPL zera em 10% e em 20% a.a."""
        fluxos = [(date(2020, 1, 1), -100.0), (date(2021, 1, 1), 230.0),
                  (date(2022, 1, 1), -132.0)]
        assert rentabilidade.irr_ambigua(fluxos)
        assert not rentabilidade.irr_ambigua([(date(2020, 1, 1), -100.0),
                                              (date(2021, 1, 1), 110.0)])


def test_dietz_unico(sessao):
    """V₀ 1.000, aporte de 500 no dia 10 de 30, V₁ 1.600 → 100 / 1.333,33 = 7,5%."""
    r = rentabilidade.dietz(1000, 1600, [(date(2024, 1, 11), 500.0)],
                            date(2024, 1, 1), date(2024, 1, 31))
    assert r == pytest.approx(100 / (1000 + 500 * 20 / 30))
    assert rentabilidade.dietz(0, 500, [(date(2024, 1, 31), 500.0)],
                               date(2024, 1, 1), date(2024, 1, 31)) is None


def test_lse_negocia_em_libra():
    reg = {"quantidade": 1, "preco_medio": 1, "valor_financeiro": 1,
           "data_aquisicao": date(2024, 1, 2), "mercado": "LSE", "classe": "ACAO",
           "ticker_ou_cnpj": "VOD"}
    assert config.MOEDA_POR_MERCADO["LSE"] == "GBP"
    assert ingest.semantica_liquidos({**reg, "moeda": "GBP"}, 2) == []
    assert ingest.semantica_liquidos({**reg, "moeda": "EUR"}, 2)


def test_twr_por_ativo(sessao, carteira):
    """
    A: 100 a 10 em 02/01/2024; 12 em 31/12/2024; compra de 50 a 12,50 em
    31/03/2025; 13,20 em 30/06/2025. O TWR do ativo é a variação do preço,
    13,20 / 10 − 1 = 32%, sem influência da compra no meio do período.
    B: 100 a 20 em 03/06/2024; 30 em 30/06/2025 → 50%.
    """
    a = cadastrar_acao(sessao, carteira, "AAA3")
    preco(sessao, a, date(2024, 12, 31), 12.0)
    operacoes.registrar_transacao(sessao, usuario_de(sessao, carteira), a.id, tipo="COMPRA",
                                  data_transacao=date(2025, 3, 31), quantidade=50, preco=12.5)
    preco(sessao, a, date(2025, 6, 30), 13.2)
    b = cadastrar_acao(sessao, carteira, "BBB3", preco=20.0, dia=date(2024, 6, 3))
    preco(sessao, b, date(2025, 6, 30), 30.0)

    por_ticker = {r.ticker: r for r in consolidacao.rentabilidade_por_ativo(
        sessao, carteira.id, None, date(2025, 6, 30))}
    assert por_ticker["AAA3"].twr == pytest.approx(0.32, abs=1e-12)
    assert por_ticker["BBB3"].twr == pytest.approx(0.50, abs=1e-12)
    assert por_ticker["BBB3"].inicio == date(2024, 6, 3)
    assert por_ticker["AAA3"].twr_anualizado == pytest.approx(1.32 ** (365 / 545) - 1)
