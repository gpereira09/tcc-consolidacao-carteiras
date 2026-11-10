"""
Colunas de extensão do template PE/VC: a classificação da distribuição
(RF-012) é opcional, de modo que planilhas no layout original do Quadro 17
continuam válidas.
"""
from __future__ import annotations

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from src import ingest
from src.models import EventoPE
from src.templates_xlsx import gerar_template
from tests.apoio import importar_arquivo


@pytest.fixture()
def pevc(tmp_path):
    return gerar_template("PEVC", tmp_path / "p.xlsx")


def _editar_eventos(caminho, alterar):
    """Aplica `alterar` à aba Eventos e salva a planilha."""
    wb = load_workbook(caminho)
    alterar(wb["Eventos"])
    wb.save(caminho)


class TestTemplateSubtipo:
    def test_template_gerado_tem_subtipo(self, pevc):
        ws = load_workbook(pevc)["Eventos"]
        assert [c.value for c in ws[1]][-1] == "subtipo"

    def test_layout_original_continua_valido(self, sessao, carteira, pevc):
        """Planilha no layout do Quadro 17, sem a coluna nova."""
        _editar_eventos(pevc, lambda ws: ws.delete_cols(8))
        prev = ingest.validar_arquivo(pevc, "PEVC", sessao, carteira.id)
        assert not prev.erros_estruturais and prev.importavel

    def test_coluna_desconhecida_apos_extensao(self, sessao, carteira, pevc):
        _editar_eventos(pevc, lambda ws: ws.cell(row=1, column=8, value="qualquer_coisa"))
        prev = ingest.validar_arquivo(pevc, "PEVC", sessao, carteira.id)
        assert prev.erros_estruturais

    def test_subtipo_importado(self, sessao, carteira, pevc):
        importar_arquivo(sessao, carteira, pevc, "PEVC")
        dists = sessao.scalars(select(EventoPE).where(EventoPE.tipo_evento == "DISTRIBUICAO")).all()
        assert {d.subtipo for d in dists} == {"GANHO_CAPITAL", "RETORNO_CAPITAL"}

    def test_subtipo_em_capital_call_rejeitado(self, sessao, carteira, pevc):
        _editar_eventos(pevc, lambda ws: ws.cell(row=2, column=8, value="RENDIMENTO"))
        prev = ingest.validar_arquivo(pevc, "PEVC", sessao, carteira.id)
        assert any(o.coluna == "subtipo" for o in prev.erros_semanticos)
