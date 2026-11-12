"""Servidor MCP: mesmos números da aplicação, isolamento por usuário e auditoria."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp")

from sqlalchemy import select

import mcp_server
from src import audit, security
from src.calc import consolidacao
from src.models import AuditLog
from tests.apoio import criar_usuario


@pytest.fixture()
def como(monkeypatch):
    def _como(email: str) -> None:
        monkeypatch.setenv("CARTEIRA_MCP_EMAIL", email)
    return _como


@pytest.fixture()
def outro(sessao):
    criar_usuario(sessao, "outro@exemplo.com", "Outro Cliente")
    sessao.commit()


def test_ferramentas_publicadas_somente_leitura():
    ferramentas = asyncio.run(mcp_server.servidor.list_tools())
    nomes = {f.name for f in ferramentas}
    assert nomes == {"listar_carteiras", "posicao_consolidada", "rentabilidade", "fundos_iliquidos"}
    assert all(f.annotations.read_only_hint for f in ferramentas)


def test_lista_apenas_a_propria_carteira(carteira_populada, outro, como):
    como("teste@exemplo.com")
    assert [c["carteira_id"] for c in mcp_server.listar_carteiras()] == [carteira_populada.id]


def test_posicao_igual_ao_calculo_da_aplicacao(sessao, carteira_populada, como):
    como("teste@exemplo.com")
    resposta = mcp_server.posicao_consolidada(carteira_populada.id)
    esperado = consolidacao.snapshot(sessao, carteira_populada.id)
    assert resposta["patrimonio"] == round(esperado.patrimonio, 2)
    assert len(resposta["posicoes"]) == len(esperado.linhas)
    assert sum(resposta["alocacao_por_classe"].values()) == pytest.approx(1.0, abs=1e-3)


def test_rentabilidade_e_fundos_iguais_ao_calculo(sessao, carteira_populada, como):
    como("teste@exemplo.com")
    r = mcp_server.rentabilidade(carteira_populada.id)
    esperado = consolidacao.rentabilidade(sessao, carteira_populada.id)
    assert r["twr"] == round(esperado.twr, 4)
    fundos = mcp_server.fundos_iliquidos(carteira_populada.id)
    assert [f["identificador"] for f in fundos] == \
        [m.identificador for m in consolidacao.metricas_pe(sessao, carteira_populada.id)]


def test_carteira_alheia_e_negada(carteira_populada, outro, como):
    como("outro@exemplo.com")
    with pytest.raises(security.ErroAutorizacao):
        mcp_server.posicao_consolidada(carteira_populada.id)


def test_sem_usuario_configurado_nada_e_exposto(carteira_populada, monkeypatch):
    monkeypatch.delenv("CARTEIRA_MCP_EMAIL", raising=False)
    with pytest.raises(security.ErroAutenticacao):
        mcp_server.listar_carteiras()


def test_consulta_registrada_na_auditoria(sessao, carteira_populada, como):
    como("teste@exemplo.com")
    mcp_server.posicao_consolidada(carteira_populada.id)
    sessao.expire_all()
    log = sessao.scalar(select(AuditLog).where(AuditLog.acao == audit.CONSULTA_MCP))
    assert log is not None and log.entidade_id == carteira_populada.id
