"""
Coleta das séries reais de mercado, sem acesso à rede: benchmarks (RF-018),
PTAX histórica (RN-009) e histórico de preços dos ativos listados (RF-008).
Em todos os casos, falha da fonte mantém o que já está armazenado (seção 5.8).
"""
from __future__ import annotations

from datetime import date

import pytest

from src.market import benchmarks, cambio, precos
from src.models import Ativo, PrecoHistorico, TaxaCambio
from tests.apoio import cadastrar_acao

INICIO, FIM = date(2024, 1, 1), date(2024, 3, 15)


class _Resposta:
    def __init__(self, corpo):
        self._corpo = corpo

    def raise_for_status(self):
        pass

    def json(self):
        return self._corpo


def _sem_rede(*a, **k):
    raise OSError("sem rede")


class TestBenchmarks:
    def test_serie_regravada_e_rebaseada_em_100(self, sessao):
        coletores = {"IBOV": lambda i, f: [(date(2024, 1, 2), 2000.0), (date(2024, 1, 3), 2100.0)]}
        assert benchmarks.atualizar(sessao, INICIO, FIM, coletores) == {"IBOV": 2}
        assert benchmarks.serie(sessao, "IBOV") == [(date(2024, 1, 2), 100.0),
                                                    (date(2024, 1, 3), 105.0)]
        assert benchmarks.retorno(sessao, "IBOV", date(2024, 1, 2), FIM) == pytest.approx(0.05)

        # A nova coleta substitui a série inteira, sem sobras da anterior.
        coletores = {"IBOV": lambda i, f: [(date(2024, 2, 1), 50.0), (date(2024, 2, 2), 55.0)]}
        benchmarks.atualizar(sessao, INICIO, FIM, coletores)
        assert [d for d, _ in benchmarks.serie(sessao, "IBOV")] == [date(2024, 2, 1),
                                                                    date(2024, 2, 2)]

    def test_falha_de_coleta_mantem_a_serie_armazenada(self, sessao):
        benchmarks.atualizar(sessao, INICIO, FIM,
                             {"CDI": lambda i, f: [(date(2024, 1, 2), 1.0), (date(2024, 1, 3), 2.0)]})
        antes = benchmarks.serie(sessao, "CDI")
        assert benchmarks.atualizar(sessao, INICIO, FIM, {"CDI": _sem_rede}) == {"CDI": 0}
        assert benchmarks.atualizar(sessao, INICIO, FIM, {"CDI": lambda i, f: []}) == {"CDI": 0}
        assert benchmarks.serie(sessao, "CDI") == antes

    def test_indice_sem_serie_e_recoletado_ate_a_fonte_responder(self, sessao):
        pontos = [(date(2024, 1, 2), 10.0), (date(2024, 1, 3), 11.0)]
        chamadas = {"IBOV": 0, "CDI": 0}

        def ibov(inicio, fim):
            chamadas["IBOV"] += 1
            return pontos if chamadas["IBOV"] >= 3 else []

        def cdi(inicio, fim):
            chamadas["CDI"] += 1
            return pontos

        coletores = {"IBOV": ibov, "CDI": cdi}
        assert benchmarks.atualizar(sessao, INICIO, FIM, coletores) == {"IBOV": 0, "CDI": 2}

        # Só o índice sem série volta à fonte; o que já tem pontos não é consultado de novo.
        assert benchmarks.recoletar_faltantes(sessao, INICIO, FIM, coletores, espera=0) == {"IBOV": 2}
        assert chamadas == {"IBOV": 3, "CDI": 1}
        assert len(benchmarks.serie(sessao, "IBOV")) == 2
        assert benchmarks.recoletar_faltantes(sessao, INICIO, FIM, coletores, espera=0) == {}

    def test_recoleta_desiste_depois_das_tentativas(self, sessao):
        chamadas = []
        coletores = {"IBOV": lambda i, f: chamadas.append(1) or []}
        assert benchmarks.recoletar_faltantes(sessao, INICIO, FIM, coletores,
                                              tentativas=2, espera=0) == {"IBOV": 0}
        assert len(chamadas) == 2
        assert benchmarks.serie(sessao, "IBOV") == []

    def test_cdi_e_ipca_acumulam_as_taxas_do_bcb(self, monkeypatch):
        monkeypatch.setattr(benchmarks, "_sgs",
                            lambda serie, i, f: [(date(2024, 1, 2), 1.0), (date(2024, 1, 3), 2.0)])
        assert benchmarks._coletar_cdi(INICIO, FIM) == [
            (date(2024, 1, 2), pytest.approx(101.0)), (date(2024, 1, 3), pytest.approx(103.02))]

        # IPCA de janeiro entra em 31/01; o de março só valeria em 31/03, depois do fim.
        monkeypatch.setattr(benchmarks, "_sgs", lambda serie, i, f: [
            (date(2024, 1, 1), 1.0), (date(2024, 2, 1), 1.0), (date(2024, 3, 1), 1.0)])
        assert benchmarks._coletar_ipca(INICIO, FIM) == [
            (date(2024, 1, 1), 100.0), (date(2024, 1, 31), pytest.approx(101.0)),
            (date(2024, 2, 29), pytest.approx(102.01))]

    def test_indice_da_b3_le_a_tabela_dia_por_mes(self, monkeypatch):
        import requests

        tabela = {"results": [
            {"day": 2, "rateValue1": "3.000,50", "rateValue2": None, "rateValue3": "3.100,00"},
            {"day": 20, "rateValue1": None, "rateValue2": "3.050,25", "rateValue3": "3.200,00"},
        ]}
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resposta(tabela))
        assert benchmarks._coletar_b3("IFIX", INICIO, FIM) == [
            (date(2024, 1, 2), 3000.50), (date(2024, 2, 20), 3050.25), (date(2024, 3, 2), 3100.0)]


class TestCambioHistorico:
    def test_ptax_grava_o_ultimo_boletim_de_cada_dia(self, sessao, monkeypatch):
        import requests

        boletins = {"value": [
            {"cotacaoVenda": 5.01, "dataHoraCotacao": "2024-01-02 10:09:20.88"},
            {"cotacaoVenda": 5.05, "dataHoraCotacao": "2024-01-02 13:03:30.64"},
            {"cotacaoVenda": 5.10, "dataHoraCotacao": "2024-01-03 13:05:11.10"},
        ]}
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resposta(boletins))
        assert cambio.atualizar_bcb(sessao, "USD", INICIO, FIM) == 2
        assert cambio.obter_taxa(sessao, "USD", "BRL", date(2024, 1, 2)) == 5.05
        assert {t.fonte for t in sessao.query(TaxaCambio)} == {"BCB_PTAX"}

        monkeypatch.setattr(requests, "get", _sem_rede)
        assert cambio.atualizar_bcb(sessao, "USD", INICIO, FIM) == 0
        assert cambio.obter_taxa(sessao, "USD", "BRL", FIM) == 5.10


class TestHistoricoDePrecos:
    def test_grava_a_serie_e_preserva_o_preco_do_usuario(self, sessao, carteira):
        pos = cadastrar_acao(sessao, carteira, "VALE3", preco=60.0, dia=date(2024, 1, 2))
        ativo = sessao.get(Ativo, pos.ativo_id)
        pedidos = []

        def coletor(simbolo, inicio, fim):
            pedidos.append(simbolo)
            return [(date(2024, 1, 2), 61.0), (date(2024, 1, 3), 62.0)]

        assert precos.carregar_historico(sessao, ativo, INICIO, FIM, coletor) == 2
        sessao.commit()
        assert pedidos == ["VALE3.SA"]
        serie = {p.data: (p.preco_fechamento, p.fonte) for p in sessao.query(PrecoHistorico)}
        assert serie == {date(2024, 1, 2): (60.0, "MANUAL"), date(2024, 1, 3): (62.0, "YAHOO")}

    def test_falha_da_fonte_nao_grava_nada(self, sessao, carteira):
        pos = cadastrar_acao(sessao, carteira, "VALE3", preco=60.0, dia=date(2024, 1, 2))
        ativo = sessao.get(Ativo, pos.ativo_id)
        assert precos.carregar_historico(sessao, ativo, INICIO, FIM, lambda *a: []) == 0
        assert sessao.query(PrecoHistorico).count() == 1
