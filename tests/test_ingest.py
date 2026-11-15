"""Testes da camada de ingestão (RN-002 a RN-008)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from src import ingest
from src.models import Ativo, AtivoIliquido, Posicao
from src.templates_xlsx import gerar_template
from tests.apoio import importar_arquivo, usuario_de


@pytest.fixture()
def liquidos(tmp_path):
    return gerar_template("LIQUIDOS", tmp_path / "liq.xlsx")


@pytest.fixture()
def pevc(tmp_path):
    return gerar_template("PEVC", tmp_path / "pevc.xlsx")


@pytest.fixture()
def imoveis(tmp_path):
    return gerar_template("IMOVEIS", tmp_path / "imo.xlsx")


def _escrever(caminho, aba, linha, coluna_nome, valor):
    """
    Altera uma célula da aba de dados, localizando a coluna pelo nome.

    A atribuição é feita pelo atributo `.value` porque `ws.cell(..., value=None)`
    do openpyxl ignora o parâmetro quando ele é None, e vários testes precisam
    justamente esvaziar a célula.
    """
    wb = load_workbook(caminho)
    ws = wb[aba]
    nomes = [c.value for c in ws[1]]
    ws.cell(row=linha, column=nomes.index(coluna_nome) + 1).value = valor
    wb.save(caminho)


class TestValidacaoEstrutural:
    def test_template_intacto_e_valido(self, liquidos):
        prev = ingest.validar_arquivo(liquidos, "LIQUIDOS")
        assert not prev.abortado
        assert prev.total_lido == 10

    def test_coluna_renomeada_aborta_arquivo(self, liquidos):
        """RN-003: erro estrutural aborta a importação inteira."""
        wb = load_workbook(liquidos)
        wb["Dados"].cell(row=1, column=1, value="codigo_do_ativo")
        wb.save(liquidos)

        prev = ingest.validar_arquivo(liquidos, "LIQUIDOS")
        assert prev.abortado
        assert not prev.importavel
        assert "ticker_ou_cnpj" in str(prev.erros_estruturais[0])

    def test_aba_ausente_aborta(self, pevc):
        wb = load_workbook(pevc)
        del wb["Eventos"]
        wb.save(pevc)

        prev = ingest.validar_arquivo(pevc, "PEVC")
        assert prev.abortado
        assert "Eventos" in str(prev.erros_estruturais[0])

    def test_arquivo_ilegivel(self, tmp_path):
        ruim = tmp_path / "corrompido.xlsx"
        ruim.write_bytes(b"isto nao e um xlsx")
        prev = ingest.validar_arquivo(ruim, "LIQUIDOS")
        assert prev.abortado


class TestValidacaoSemantica:
    def test_linha_invalida_nao_derruba_as_validas(self, liquidos):
        """RN-003: erro semântico isola a linha, o resto segue importável."""
        _escrever(liquidos, "Dados", 2, "quantidade", -50)
        prev = ingest.validar_arquivo(liquidos, "LIQUIDOS")

        assert not prev.abortado
        assert prev.importavel
        assert prev.total_valido == 9
        assert prev.total_rejeitado == 1

    def test_valor_financeiro_incoerente(self, liquidos):
        _escrever(liquidos, "Dados", 2, "valor_financeiro", 999_999.0)
        prev = ingest.validar_arquivo(liquidos, "LIQUIDOS")
        assert any("incoerente" in o.mensagem for o in prev.erros_semanticos)

    def test_tolerancia_de_um_porcento_e_aceita(self, liquidos):
        _escrever(liquidos, "Dados", 2, "valor_financeiro", 48_900.0)
        prev = ingest.validar_arquivo(liquidos, "LIQUIDOS")
        assert not any(o.linha == 2 for o in prev.erros_semanticos)

    def test_moeda_incoerente_com_mercado(self, liquidos):
        _escrever(liquidos, "Dados", 2, "moeda", "USD")
        prev = ingest.validar_arquivo(liquidos, "LIQUIDOS")
        assert any(o.coluna == "moeda" for o in prev.erros_semanticos)

    def test_data_futura_rejeitada(self, liquidos):
        futuro = date.today() + timedelta(days=30)
        _escrever(liquidos, "Dados", 2, "data_aquisicao", futuro)
        prev = ingest.validar_arquivo(liquidos, "LIQUIDOS")
        assert any("futura" in o.mensagem for o in prev.erros_semanticos)

    def test_valor_fora_do_dominio(self, liquidos):
        _escrever(liquidos, "Dados", 2, "classe", "CRIPTOMOEDA")
        prev = ingest.validar_arquivo(liquidos, "LIQUIDOS")
        assert any(o.coluna == "classe" for o in prev.erros_semanticos)

    def test_obrigatorio_vazio(self, liquidos):
        _escrever(liquidos, "Dados", 2, "quantidade", None)
        prev = ingest.validar_arquivo(liquidos, "LIQUIDOS")
        assert any("obrigatório" in o.mensagem for o in prev.erros_semanticos)

    def test_avaliacao_anterior_a_aquisicao(self, imoveis):
        _escrever(imoveis, "Dados", 2, "data_avaliacao", date(2010, 1, 1))
        prev = ingest.validar_arquivo(imoveis, "IMOVEIS")
        assert any(o.coluna == "data_avaliacao" for o in prev.erros_semanticos)

    def test_identificador_de_imovel_duplicado(self, imoveis):
        _escrever(imoveis, "Dados", 3, "identificador_imovel", "IMV-001")
        prev = ingest.validar_arquivo(imoveis, "IMOVEIS")
        assert any("duplicado" in o.mensagem for o in prev.erros_semanticos)


class TestCoercaoDeTipos:
    def test_decimal_pt_br(self):
        assert ingest.coagir_decimal("1.234,56") == pytest.approx(1234.56)

    def test_decimal_en_us(self):
        assert ingest.coagir_decimal("1,234.56") == pytest.approx(1234.56)

    def test_decimal_com_simbolo(self):
        assert ingest.coagir_decimal("R$ 48.675,00") == pytest.approx(48675.0)

    def test_decimal_invalido(self):
        with pytest.raises(ValueError):
            ingest.coagir_decimal("abc")

    def test_data_varios_formatos(self):
        assert ingest.coagir_data("15/03/2023") == date(2023, 3, 15)
        assert ingest.coagir_data("2023-03-15") == date(2023, 3, 15)

    def test_data_invalida(self):
        with pytest.raises(ValueError):
            ingest.coagir_data("31/02/2023")


class TestRegrasPEVC:
    def test_capital_call_acima_do_commitment(self, pevc):
        """RN-004: chamadas pagas não podem exceder o commitment."""
        _escrever(pevc, "Eventos", 2, "valor_evento", 5_000_000.0)
        prev = ingest.validar_arquivo(pevc, "PEVC")
        assert any("RN-004" in o.mensagem for o in prev.erros_semanticos)

    def test_distribuicao_sem_capital_integralizado(self, pevc):
        """RN-005: distribuição exige capital já integralizado."""
        _escrever(pevc, "Eventos", 2, "status", "PENDENTE")
        _escrever(pevc, "Eventos", 3, "status", "PENDENTE")
        _escrever(pevc, "Eventos", 4, "status", "PENDENTE")
        prev = ingest.validar_arquivo(pevc, "PEVC")
        assert any("RN-005" in o.mensagem for o in prev.erros_semanticos)

    def test_evento_orfao(self, pevc):
        _escrever(pevc, "Eventos", 2, "identificador_fundo", "FUND-INEXISTENTE")
        prev = ingest.validar_arquivo(pevc, "PEVC")
        assert any("não foi cadastrado" in o.mensagem for o in prev.erros_semanticos)

    def test_status_incompativel_com_tipo(self, pevc):
        _escrever(pevc, "Eventos", 2, "tipo_evento", "DISTRIBUICAO")
        prev = ingest.validar_arquivo(pevc, "PEVC")
        assert any(o.coluna == "status" for o in prev.erros_semanticos)

    def test_evento_anterior_ao_inicio_do_fundo(self, pevc):
        _escrever(pevc, "Eventos", 2, "data_evento", date(2000, 1, 1))
        prev = ingest.validar_arquivo(pevc, "PEVC")
        assert any("anterior à data_inicio" in o.mensagem for o in prev.erros_semanticos)

    def test_capital_call_pendente_pode_ser_futuro(self, pevc):
        futuro = date.today() + timedelta(days=60)
        _escrever(pevc, "Eventos", 8, "data_evento", futuro)
        prev = ingest.validar_arquivo(pevc, "PEVC")
        assert not any(o.linha == 8 for o in prev.erros_semanticos)


class TestPersistencia:
    def test_importa_liquidos(self, sessao, carteira, liquidos):
        log = importar_arquivo(sessao, carteira, liquidos, "LIQUIDOS")

        assert log.linhas_importadas == 10
        posicoes = sessao.scalars(
            select(Posicao).where(Posicao.carteira_id == carteira.id)
        ).all()
        assert len(posicoes) == 10

        petr = sessao.scalar(select(Ativo).where(Ativo.ticker_canonico == "PETR4"))
        assert petr.possui_ticker_listado is True
        assert petr.pais == "BRASIL"

    def test_ativo_nao_listado_marcado_corretamente(self, sessao, carteira, pevc):
        importar_arquivo(sessao, carteira, pevc, "PEVC")

        fundo_ativo = sessao.scalar(
            select(Ativo).where(Ativo.ticker_canonico == "FUND-VC-001")
        )
        assert fundo_ativo.possui_ticker_listado is False

    def test_metricas_pe_materializadas_na_importacao(self, sessao, carteira, pevc):
        """RN-012: métricas recalculadas a cada inserção de evento."""
        importar_arquivo(sessao, carteira, pevc, "PEVC")

        fundo = sessao.scalar(
            select(AtivoIliquido).where(AtivoIliquido.identificador_fundo == "FUND-VC-001")
        )
        assert fundo.paid_in_capital == pytest.approx(1_500_000.0)
        assert fundo.dry_powder == pytest.approx(250_000.0)
        assert fundo.nav_atual == pytest.approx(2_050_000.0)

    def test_reimportacao_nao_duplica_eventos_do_fundo(self, sessao, carteira, pevc):
        """UC-08 FA-01: importar o mesmo arquivo de novo não repete os eventos."""
        importar_arquivo(sessao, carteira, pevc, "PEVC")
        fundos = sessao.scalars(select(AtivoIliquido)).all()
        antes = {f.identificador_fundo: (len(f.eventos), f.paid_in_capital,
                                         f.nav_atual) for f in fundos}

        prev = ingest.validar_arquivo(pevc, "PEVC", sessao, carteira.id)
        assert not prev.erros_semanticos
        assert not prev.validos["Eventos"]
        assert any("já registrado" in a.mensagem for a in prev.avisos)
        ingest.importar(sessao, prev, carteira.id, usuario_de(sessao, carteira).id)

        for f in sessao.scalars(select(AtivoIliquido)).all():
            sessao.refresh(f)
            assert (len(f.eventos), f.paid_in_capital,
                    f.nav_atual) == antes[f.identificador_fundo]

    def test_reimportacao_nao_reduz_commitment_abaixo_do_chamado(self, sessao, carteira, pevc):
        """RN-004: a linha do fundo é rejeitada; as demais seguem válidas."""
        importar_arquivo(sessao, carteira, pevc, "PEVC")
        _escrever(pevc, "Fundos", 2, "commitment", 1_000_000.0)

        prev = ingest.validar_arquivo(pevc, "PEVC", sessao, carteira.id)
        erros = [o for o in prev.erros_semanticos if o.coluna == "commitment"]
        assert len(erros) == 1 and "RN-004" in erros[0].mensagem
        validos = {r["identificador_fundo"] for r in prev.validos["Fundos"]}
        assert validos == {"FUND-PE-002", "FUND-RE-003"}

    def test_importa_imoveis(self, sessao, carteira, imoveis):
        log = importar_arquivo(sessao, carteira, imoveis, "IMOVEIS")
        assert log.linhas_importadas == 4

    def test_carteira_inexistente_e_rejeitada(self, sessao, carteira, liquidos):
        """RN-008: não se importa para carteira de terceiro/inexistente."""
        prev = ingest.validar_arquivo(liquidos, "LIQUIDOS", sessao, carteira.id)
        with pytest.raises(ValueError):
            ingest.importar(sessao, prev, "carteira-que-nao-existe", usuario_de(sessao, carteira).id)

    def test_reimportacao_consolida_posicao(self, sessao, carteira, liquidos):
        importar_arquivo(sessao, carteira, liquidos, "LIQUIDOS")
        importar_arquivo(sessao, carteira, liquidos, "LIQUIDOS")

        posicoes = sessao.scalars(
            select(Posicao).where(Posicao.carteira_id == carteira.id)
        ).all()
        assert len(posicoes) == 10
        petr = sessao.scalar(select(Ativo).where(Ativo.ticker_canonico == "PETR4"))
        pos = sessao.scalar(select(Posicao).where(Posicao.ativo_id == petr.id))
        assert pos.quantidade == pytest.approx(3000.0)

    def test_duplicata_no_mesmo_arquivo_e_consolidada(self, sessao, carteira, liquidos):
        """Mesmo ticker duas vezes: consolida por preço médio ponderado."""
        _escrever(liquidos, "Dados", 3, "ticker_ou_cnpj", "PETR4")
        _escrever(liquidos, "Dados", 3, "classe", "ACAO")
        _escrever(liquidos, "Dados", 3, "quantidade", 500)
        _escrever(liquidos, "Dados", 3, "preco_medio", 40.0)
        _escrever(liquidos, "Dados", 3, "valor_financeiro", 20_000.0)

        prev = ingest.validar_arquivo(liquidos, "LIQUIDOS", sessao, carteira.id)
        assert prev.avisos
        ingest.importar(sessao, prev, carteira.id, usuario_de(sessao, carteira).id)

        petr = sessao.scalar(select(Ativo).where(Ativo.ticker_canonico == "PETR4"))
        pos = sessao.scalar(select(Posicao).where(Posicao.ativo_id == petr.id))
        assert pos.quantidade == pytest.approx(2000.0)
        assert pos.preco_medio == pytest.approx(34.3375, abs=1e-4)
