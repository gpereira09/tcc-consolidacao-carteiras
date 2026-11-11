"""
Relatórios da carteira em PDF e em Excel (RF-023; UC-16).
"""
from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

from src import relatorio_pdf, relatorio_xlsx
from src.calc import consolidacao


@pytest.fixture()
def dados_relatorio(sessao, carteira_populada):
    """Argumentos comuns aos geradores: nome, moeda, snapshot, rentabilidade, PE, imóveis."""
    cid = carteira_populada.id
    return (carteira_populada.nome, "BRL", consolidacao.snapshot(sessao, cid),
            consolidacao.rentabilidade(sessao, cid), consolidacao.metricas_pe(sessao, cid),
            consolidacao.resumo_imoveis(sessao, cid))


class TestRelatorioPdf:
    def test_gera_pdf_valido(self, dados_relatorio):
        pdf = relatorio_pdf.gerar(*dados_relatorio, "Teste")
        assert pdf.startswith(b"%PDF") and len(pdf) > 5000


class TestRelatorioExcel:
    def test_gera_planilha_com_quatro_abas(self, dados_relatorio):
        snap = dados_relatorio[2]
        wb = load_workbook(io.BytesIO(relatorio_xlsx.gerar(*dados_relatorio)))
        assert wb.sheetnames == ["Resumo", "Posições", "Fundos ilíquidos", "Imóveis"]
        resumo = {wb["Resumo"].cell(row=i, column=1).value: wb["Resumo"].cell(row=i, column=2).value
                  for i in range(1, wb["Resumo"].max_row + 1)}
        assert resumo["Patrimônio consolidado"] == round(snap.patrimonio, 2)
        assert wb["Posições"].max_row == len(snap.linhas) + 1
