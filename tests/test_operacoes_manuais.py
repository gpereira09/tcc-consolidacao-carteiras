"""
Lançamentos manuais, alternativos ao template: cadastro de ativo líquido
(UC-05), compra, venda e provento (UC-06), cadastro de fundo ilíquido (UC-08),
eventos classificados (RF-012) e criação de carteiras.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src import operacoes
from src.calc import consolidacao, pe
from tests.apoio import cadastrar_acao, criar_usuario, usuario_de


class TestLancamentosManuais:
    def _ativo(self, sessao, carteira, preco=30.0, **extra):
        return cadastrar_acao(sessao, carteira, "petr4", preco=preco, dia=date(2024, 1, 10),
                              **extra)

    def test_cadastro_e_consolidacao(self, sessao, carteira):
        pos = self._ativo(sessao, carteira)
        pos2 = self._ativo(sessao, carteira, quantidade=100, preco=40.0)
        assert pos.id == pos2.id
        assert pos2.quantidade == 200 and pos2.preco_medio == pytest.approx(35.0)

    def test_moeda_incoerente(self, sessao, carteira):
        with pytest.raises(ValueError, match="moeda"):
            self._ativo(sessao, carteira, moeda="USD")

    def test_carteira_de_outro_usuario(self, sessao, carteira):
        intruso = criar_usuario(sessao, "x@exemplo.com")
        with pytest.raises(ValueError, match="RN-008"):
            operacoes.cadastrar_ativo_liquido(
                sessao, intruso, carteira.id, ticker="VALE3", classe="ACAO", mercado="B3",
                moeda="BRL", quantidade=1, preco_medio=1, data_aquisicao=date(2024, 1, 1),
                buscar_preco=False)

    def test_compra_venda_provento(self, sessao, carteira):
        pos = self._ativo(sessao, carteira)
        u = usuario_de(sessao, carteira)
        operacoes.registrar_transacao(sessao, u, pos.id, tipo="COMPRA",
                                      data_transacao=date(2024, 3, 1), quantidade=100, preco=50)
        assert pos.quantidade == 200 and pos.preco_medio == pytest.approx(40.0)
        operacoes.registrar_transacao(sessao, u, pos.id, tipo="VENDA",
                                      data_transacao=date(2024, 6, 1), quantidade=50, preco=60)
        assert pos.quantidade == 150 and pos.preco_medio == pytest.approx(40.0)
        operacoes.registrar_transacao(sessao, u, pos.id, tipo="PROVENTO",
                                      data_transacao=date(2024, 7, 1), quantidade=0, preco=120)
        assert pos.quantidade == 150

        fluxos = consolidacao.fluxos_de_caixa(sessao, carteira.id, "BRL")
        assert any(d == date(2024, 6, 1) and v == pytest.approx(3000.0) for d, v in fluxos)
        assert any(d == date(2024, 7, 1) and v == pytest.approx(120.0) for d, v in fluxos)

    def test_venda_acima_da_posicao(self, sessao, carteira):
        pos = self._ativo(sessao, carteira)
        with pytest.raises(ValueError, match="excede"):
            operacoes.registrar_transacao(sessao, usuario_de(sessao, carteira), pos.id,
                                          tipo="VENDA", data_transacao=date(2024, 2, 1),
                                          quantidade=101, preco=30)

    def test_venda_retroativa_acima_da_posicao_da_data(self, sessao, carteira):
        """UC-06: vale a posição que existia na data da venda, não a de hoje."""
        pos = self._ativo(sessao, carteira)
        u = usuario_de(sessao, carteira)
        operacoes.registrar_transacao(sessao, u, pos.id, tipo="COMPRA",
                                      data_transacao=date(2024, 3, 1), quantidade=100, preco=30)
        with pytest.raises(ValueError, match="excede"):
            operacoes.registrar_transacao(sessao, u, pos.id, tipo="VENDA",
                                          data_transacao=date(2024, 2, 1), quantidade=150, preco=30)
        assert pos.quantidade == 200
        operacoes.registrar_transacao(sessao, u, pos.id, tipo="VENDA",
                                      data_transacao=date(2024, 2, 1), quantidade=100, preco=30)
        assert pos.quantidade == 100

    def test_data_futura(self, sessao, carteira):
        pos = self._ativo(sessao, carteira)
        with pytest.raises(ValueError, match="futura"):
            operacoes.registrar_transacao(sessao, usuario_de(sessao, carteira), pos.id,
                                          tipo="COMPRA", data_transacao=date.today() + timedelta(1),
                                          quantidade=1, preco=30)

    def test_quantidade_historica(self, sessao, carteira):
        """Uma venda de hoje não pode reduzir a quantidade em datas passadas."""
        pos = self._ativo(sessao, carteira)
        operacoes.registrar_transacao(sessao, usuario_de(sessao, carteira), pos.id, tipo="VENDA",
                                      data_transacao=date(2025, 1, 10), quantidade=60, preco=35)
        assert consolidacao._posicao_ate(sessao, pos, date(2024, 12, 31))[:2] == (100, 30.0)
        assert consolidacao._posicao_ate(sessao, pos, date(2025, 2, 1))[0] == 40
        assert consolidacao._posicao_ate(sessao, pos, date(2023, 1, 1))[0] == 0


class TestFundosManuais:
    def _fundo(self, sessao, carteira):
        return operacoes.cadastrar_fundo(
            sessao, usuario_de(sessao, carteira), carteira.id, identificador="fund-x",
            nome="Fundo X", gestor="Gestora", sub_classe="PRIVATE_EQUITY", moeda="BRL",
            commitment=1_000_000, data_inicio=date(2022, 1, 1))

    def test_cadastro(self, sessao, carteira):
        fundo = self._fundo(sessao, carteira)
        assert fundo.identificador_fundo == "FUND-X" and fundo.dry_powder == 1_000_000

    def test_distribuicao_exige_classificacao(self, sessao, carteira):
        fundo = self._fundo(sessao, carteira)
        u = usuario_de(sessao, carteira)
        operacoes.registrar_evento_pe(sessao, u, fundo, tipo="CAPITAL_CALL",
                                      data_evento=date(2022, 6, 1), valor=400_000, status="PAGO")
        with pytest.raises(ValueError, match="RF-012"):
            operacoes.registrar_evento_pe(sessao, u, fundo, tipo="DISTRIBUICAO",
                                          data_evento=date(2023, 1, 1), valor=50_000,
                                          status="RECEBIDO")
        ev = operacoes.registrar_evento_pe(sessao, u, fundo, tipo="DISTRIBUICAO",
                                           data_evento=date(2023, 1, 1), valor=50_000,
                                           status="RECEBIDO", subtipo="RENDIMENTO")
        assert ev.subtipo == "RENDIMENTO"
        assert pe.calcular(fundo, sessao).dpi == pytest.approx(0.125)

    def test_rn005_e_rn004(self, sessao, carteira):
        fundo = self._fundo(sessao, carteira)
        u = usuario_de(sessao, carteira)
        with pytest.raises(ValueError, match="RN-005"):
            operacoes.registrar_evento_pe(sessao, u, fundo, tipo="DISTRIBUICAO",
                                          data_evento=date(2023, 1, 1), valor=1,
                                          status="RECEBIDO", subtipo="RENDIMENTO")
        with pytest.raises(ValueError, match="RN-004"):
            operacoes.registrar_evento_pe(sessao, u, fundo, tipo="CAPITAL_CALL",
                                          data_evento=date(2023, 1, 1), valor=1_000_001,
                                          status="PAGO")

    def test_nova_carteira(self, sessao, carteira):
        nova = operacoes.criar_carteira(sessao, usuario_de(sessao, carteira), carteira.cliente_id,
                                        "Offshore")
        assert nova.cliente_id == carteira.cliente_id
        with pytest.raises(ValueError):
            operacoes.criar_carteira(sessao, usuario_de(sessao, carteira), carteira.cliente_id,
                                     "offshore")
