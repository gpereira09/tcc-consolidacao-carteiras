"""
Correções dos achados do teste de banca: totais de imóveis na moeda base,
trava e estorno de eventos de fundo, validação de CPF e de e-mail no cadastro
e texto com caracteres de marcação no PDF.
"""
from __future__ import annotations

from datetime import date

import pytest

import config
from src import lgpd, operacoes, relatorio_pdf
from src.calc import consolidacao, pe
from src.market import cambio
from tests.apoio import SENHA, criar_fundo, criar_usuario, usuario_de


class TestTotaisDeImoveis:
    def test_converte_cada_imovel_antes_de_somar(self, sessao, carteira_populada):
        linhas = consolidacao.resumo_imoveis(sessao, carteira_populada.id)
        assert {l["Moeda"] for l in linhas} > {"BRL"}, "o template traz imóvel fora do Brasil"

        hoje = config.hoje()
        esperado = sum(cambio.converter(sessao, l["Valor de mercado"], l["Moeda"], "BRL", hoje)
                       for l in linhas)
        totais = consolidacao.totais_imoveis(sessao, linhas, "BRL", hoje)

        assert totais["Valor de mercado"] == pytest.approx(esperado)
        assert totais["Valor de mercado"] != pytest.approx(
            sum(l["Valor de mercado"] for l in linhas))
        assert totais["sem_cambio"] == []

    def test_moeda_sem_taxa_fica_fora_do_total(self, sessao, carteira):
        linhas = [
            {"Moeda": "BRL", "Valor de mercado": 100.0, "Aquisição": 80.0,
             "Renda líq. anual": 6.0},
            {"Moeda": "USD", "Valor de mercado": 50.0, "Aquisição": 40.0,
             "Renda líq. anual": 3.0},
        ]
        totais = consolidacao.totais_imoveis(sessao, linhas, "BRL", date(2024, 1, 2))
        assert totais["Valor de mercado"] == 100.0 and totais["Aquisição"] == 80.0
        assert totais["sem_cambio"] == ["USD"]


class TestEventosDeFundo:
    @pytest.fixture()
    def fundo(self, sessao, carteira):
        fundo = criar_fundo(sessao, carteira, commitment=1_000_000)
        operacoes.registrar_evento_pe(sessao, usuario_de(sessao, carteira), fundo,
                                      tipo="CAPITAL_CALL", data_evento=date(2022, 6, 1),
                                      valor=400_000, status="PAGO")
        return fundo

    def _distribuir(self, sessao, carteira, fundo, valor, **extra):
        return operacoes.registrar_evento_pe(
            sessao, usuario_de(sessao, carteira), fundo, tipo="DISTRIBUICAO",
            data_evento=date(2023, 1, 1), valor=valor, status="RECEBIDO",
            subtipo="RETORNO_CAPITAL", **extra)

    def test_distribuicao_acima_do_nav_pede_confirmacao(self, sessao, carteira, fundo):
        with pytest.raises(ValueError, match="maior que o NAV"):
            self._distribuir(sessao, carteira, fundo, 9_000_000)
        assert pe.calcular(fundo, sessao).distribuicoes == 0

        self._distribuir(sessao, carteira, fundo, 9_000_000, confirmar_acima_do_nav=True)
        assert pe.calcular(fundo, sessao).nav == 0

    def test_cancelamento_desfaz_o_efeito_do_evento(self, sessao, carteira, fundo):
        antes = pe.calcular(fundo, sessao)
        evento = self._distribuir(sessao, carteira, fundo, 100_000)
        assert pe.calcular(fundo, sessao).dpi == pytest.approx(0.25)

        operacoes.cancelar_evento_pe(sessao, usuario_de(sessao, carteira), evento.id)
        depois = pe.calcular(fundo, sessao)
        assert evento.status == "CANCELADO"
        assert (depois.dpi, depois.nav, depois.tvpi) == (antes.dpi, antes.nav, antes.tvpi)
        assert fundo.nav_atual == antes.nav

        with pytest.raises(ValueError, match="canceladas"):
            operacoes.cancelar_evento_pe(sessao, usuario_de(sessao, carteira), evento.id)

    def test_chamada_com_distribuicao_nao_e_cancelada(self, sessao, carteira, fundo):
        self._distribuir(sessao, carteira, fundo, 100_000)
        chamada = next(e for e in pe.eventos(fundo, sessao) if e.tipo_evento == "CAPITAL_CALL")
        with pytest.raises(ValueError, match="RN-005"):
            operacoes.cancelar_evento_pe(sessao, usuario_de(sessao, carteira), chamada.id)
        assert pe.calcular(fundo, sessao).paid_in == 400_000

    def test_cancelamento_exige_acesso_a_carteira(self, sessao, carteira, fundo):
        evento = self._distribuir(sessao, carteira, fundo, 100_000)
        intruso = criar_usuario(sessao, "intruso@exemplo.com")
        with pytest.raises(ValueError, match="RN-008"):
            operacoes.cancelar_evento_pe(sessao, intruso, evento.id)


class TestCadastro:
    @pytest.mark.parametrize("cpf", ["123", "abc", "111.111.111-11"])
    def test_cpf_invalido_e_recusado_na_criacao(self, sessao, cpf):
        with pytest.raises(ValueError, match="CPF inválido"):
            criar_usuario(sessao, "novo@exemplo.com", cpf=cpf)

    def test_texto_sem_digitos_nao_remove_o_cpf(self, sessao):
        titular = criar_usuario(sessao, "titular@exemplo.com", cpf="123.456.789-09")
        with pytest.raises(ValueError, match="CPF inválido"):
            lgpd.corrigir_cadastro(sessao, titular.id, senha=SENHA, cpf="abc")

    @pytest.mark.parametrize("email", [
        "x<b>negrito</b>@exemplo.test", '"<i>a</i>"@exemplo.test', "com espaco@exemplo.com",
        "sem-dominio@exemplo", "@exemplo.com",
    ])
    def test_email_com_marcacao_ou_fora_do_formato(self, sessao, email):
        with pytest.raises(ValueError, match="E-mail inválido"):
            criar_usuario(sessao, email)

    def test_email_comum_e_aceito(self, sessao):
        assert criar_usuario(sessao, "Maria.Silva+tcc@Exemplo.com.br").email \
            == "maria.silva+tcc@exemplo.com.br"


class TestPdf:
    def test_nomes_com_caracteres_de_marcacao(self, sessao, carteira_populada):
        cid = carteira_populada.id
        snap = consolidacao.snapshot(sessao, cid)
        snap.linhas[0].nome = "Fundo S&P 500 <teste>"
        pdf = relatorio_pdf.gerar(
            "Carteira A & B <principal>", "BRL", snap, consolidacao.rentabilidade(sessao, cid),
            consolidacao.metricas_pe(sessao, cid), consolidacao.resumo_imoveis(sessao, cid),
            titular="João & Maria")
        assert pdf.startswith(b"%PDF")
