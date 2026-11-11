"""Testes das métricas de ativos ilíquidos e da conversão multi-moeda."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from src.calc import pe
from src.market import cambio
from src.models import Posicao
from tests.apoio import criar_fundo, evento


@pytest.fixture()
def fundo(sessao, carteira):
    """Fundo de VC com 2M de commitment e nenhum evento."""
    return criar_fundo(sessao, carteira, 2_000_000.0, ident="FUND-TESTE",
                       classe="VENTURE_CAPITAL")


class TestMetricasPE:
    def test_fundo_sem_eventos(self, sessao, fundo):
        m = pe.calcular(fundo, sessao)
        assert m.paid_in == 0.0
        assert m.dry_powder == 2_000_000.0
        assert m.dpi == 0.0 and m.tvpi == 0.0
        assert m.irr is None

    def test_paid_in_ignora_pendentes(self, sessao, fundo):
        evento(sessao, fundo, date(2022, 6, 1), "CAPITAL_CALL", 500_000.0, "PAGO")
        evento(sessao, fundo, date(2023, 6, 1), "CAPITAL_CALL", 300_000.0, "PENDENTE")
        m = pe.calcular(fundo, sessao)
        assert m.paid_in == 500_000.0
        assert m.pendente == 300_000.0
        assert m.dry_powder == 1_200_000.0

    def test_cancelado_e_ignorado(self, sessao, fundo):
        evento(sessao, fundo, date(2022, 6, 1), "CAPITAL_CALL", 500_000.0, "PAGO")
        evento(sessao, fundo, date(2022, 7, 1), "CAPITAL_CALL", 400_000.0, "CANCELADO")
        assert pe.calcular(fundo, sessao).paid_in == 500_000.0

    def test_dpi_tvpi_moic(self, sessao, fundo):
        evento(sessao, fundo, date(2022, 6, 1), "CAPITAL_CALL", 1_000_000.0, "PAGO")
        evento(sessao, fundo, date(2024, 6, 1), "DISTRIBUICAO", 400_000.0, "RECEBIDO")
        evento(sessao, fundo, date(2025, 6, 1), "NAV_UPDATE", 900_000.0, "RECEBIDO")

        m = pe.calcular(fundo, sessao)
        assert m.dpi == pytest.approx(0.40)
        assert m.rvpi == pytest.approx(0.90)
        assert m.tvpi == pytest.approx(1.30)
        assert m.moic == m.tvpi

    def test_nav_sem_marcacao_usa_custo(self, sessao, fundo):
        """Sem NAV do gestor, o valor residual é o capital líquido investido."""
        evento(sessao, fundo, date(2022, 6, 1), "CAPITAL_CALL", 1_000_000.0, "PAGO")
        evento(sessao, fundo, date(2023, 6, 1), "DISTRIBUICAO", 200_000.0, "RECEBIDO")
        m = pe.calcular(fundo, sessao)
        assert m.nav == pytest.approx(800_000.0)
        assert m.data_nav is None

    def test_usa_o_nav_mais_recente(self, sessao, fundo):
        evento(sessao, fundo, date(2022, 6, 1), "CAPITAL_CALL", 1_000_000.0, "PAGO")
        evento(sessao, fundo, date(2024, 6, 1), "NAV_UPDATE", 1_100_000.0, "RECEBIDO")
        evento(sessao, fundo, date(2025, 6, 1), "NAV_UPDATE", 1_400_000.0, "RECEBIDO")
        m = pe.calcular(fundo, sessao)
        assert m.nav == pytest.approx(1_400_000.0)
        assert m.data_nav == date(2025, 6, 1)

    def test_irr_do_fundo(self, sessao, fundo):
        evento(sessao, fundo, date(2022, 1, 1), "CAPITAL_CALL", 1_000_000.0, "PAGO")
        evento(sessao, fundo, date(2025, 1, 1), "NAV_UPDATE", 1_331_000.0, "RECEBIDO")
        assert pe.calcular(fundo, sessao).irr == pytest.approx(0.10, abs=0.005)

    def test_percentual_chamado(self, sessao, fundo):
        evento(sessao, fundo, date(2022, 6, 1), "CAPITAL_CALL", 500_000.0, "PAGO")
        assert pe.calcular(fundo, sessao).percentual_chamado == pytest.approx(0.25)


class TestValidacoesPE:
    def test_rn004_bloqueia_chamada_acima_do_commitment(self, sessao, fundo):
        evento(sessao, fundo, date(2022, 6, 1), "CAPITAL_CALL", 1_800_000.0, "PAGO")
        with pytest.raises(ValueError, match="RN-004"):
            pe.validar_capital_call(fundo, 300_000.0, sessao)

    def test_rn004_permite_chamada_dentro_do_limite(self, sessao, fundo):
        evento(sessao, fundo, date(2022, 6, 1), "CAPITAL_CALL", 1_000_000.0, "PAGO")
        pe.validar_capital_call(fundo, 900_000.0, sessao)

    def test_rn004_conta_chamadas_pendentes(self, sessao, fundo):
        """
        Commitment 2M: 1,4M pagos + 500k pendentes = 1,9M já comprometidos.
        Uma nova chamada de 200k levaria a 2,1M e é recusada (antes era aceita,
        e o dry powder ficava negativo quando a pendente fosse paga).
        """
        evento(sessao, fundo, date(2022, 6, 1), "CAPITAL_CALL", 1_400_000.0, "PAGO")
        evento(sessao, fundo, date(2022, 7, 1), "CAPITAL_CALL", 500_000.0, "PENDENTE")
        with pytest.raises(ValueError, match="RN-004"):
            pe.validar_capital_call(fundo, 200_000.0, sessao)
        pe.validar_capital_call(fundo, 100_000.0, sessao)

    def test_rn004_mensagem_em_formato_brasileiro(self, sessao, fundo):
        evento(sessao, fundo, date(2022, 6, 1), "CAPITAL_CALL", 1_800_000.0, "PAGO")
        with pytest.raises(ValueError, match=r"Disponível para chamada: 200\.000,00"):
            pe.validar_capital_call(fundo, 300_000.0, sessao)

    def test_rn005_bloqueia_distribuicao_sem_paid_in(self, sessao, fundo):
        with pytest.raises(ValueError, match="RN-005"):
            pe.validar_distribuicao(fundo, 100_000.0, sessao)

    def test_valores_negativos_rejeitados(self, sessao, fundo):
        with pytest.raises(ValueError):
            pe.validar_capital_call(fundo, -1.0, sessao)

    def test_recalculo_materializa_no_registro(self, sessao, fundo):
        """RN-012: métricas gravadas no fundo e refletidas na posição."""
        evento(sessao, fundo, date(2022, 6, 1), "CAPITAL_CALL", 800_000.0, "PAGO")
        evento(sessao, fundo, date(2025, 6, 1), "NAV_UPDATE", 950_000.0, "RECEBIDO")
        pe.recalcular_metricas(sessao, fundo)
        sessao.commit()

        assert fundo.paid_in_capital == pytest.approx(800_000.0)
        assert fundo.nav_atual == pytest.approx(950_000.0)
        pos = sessao.scalar(select(Posicao).where(Posicao.ativo_id == fundo.ativo_id))
        assert pos.valor_financeiro == pytest.approx(950_000.0)


class TestCambio:
    def test_mesma_moeda(self, sessao):
        assert cambio.obter_taxa(sessao, "BRL", "BRL", date(2025, 1, 1)) == 1.0

    def test_taxa_direta(self, sessao):
        cambio.registrar_taxa(sessao, date(2025, 1, 10), "USD", "BRL", 5.50)
        sessao.commit()
        assert cambio.obter_taxa(sessao, "USD", "BRL", date(2025, 1, 10)) == 5.50

    def test_forward_fill(self, sessao):
        """RN-009: sem cotação na data, usa a última anterior conhecida."""
        cambio.registrar_taxa(sessao, date(2025, 1, 10), "USD", "BRL", 5.50)
        sessao.commit()
        assert cambio.obter_taxa(sessao, "USD", "BRL", date(2025, 1, 12)) == 5.50

    def test_nao_usa_taxa_futura(self, sessao):
        cambio.registrar_taxa(sessao, date(2025, 6, 1), "USD", "BRL", 5.50)
        sessao.commit()
        with pytest.raises(cambio.TaxaIndisponivel):
            cambio.obter_taxa(sessao, "USD", "BRL", date(2025, 1, 1))

    def test_taxa_inversa(self, sessao):
        cambio.registrar_taxa(sessao, date(2025, 1, 10), "USD", "BRL", 5.00)
        sessao.commit()
        assert cambio.obter_taxa(sessao, "BRL", "USD", date(2025, 1, 10)) == pytest.approx(0.20)

    def test_triangulacao_via_brl(self, sessao):
        cambio.registrar_taxa(sessao, date(2025, 1, 10), "USD", "BRL", 5.00)
        cambio.registrar_taxa(sessao, date(2025, 1, 10), "EUR", "BRL", 6.00)
        sessao.commit()
        taxa = cambio.obter_taxa(sessao, "USD", "EUR", date(2025, 1, 10))
        assert taxa == pytest.approx(5.0 / 6.0)

    def test_conversao_segura_nao_levanta(self, sessao):
        valor, ok = cambio.converter_seguro(sessao, 100.0, "USD", "BRL", date(2025, 1, 1))
        assert ok is False
        assert valor == 100.0

    def test_converter(self, sessao):
        cambio.registrar_taxa(sessao, date(2025, 1, 10), "USD", "BRL", 5.50)
        sessao.commit()
        assert cambio.converter(sessao, 1000.0, "USD", "BRL", date(2025, 1, 10)) == 5500.0
