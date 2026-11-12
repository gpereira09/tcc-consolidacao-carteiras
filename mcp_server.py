"""
Servidor MCP (Model Context Protocol) de consulta às carteiras.

Expõe ao assistente de IA, somente para leitura, os mesmos cálculos da
aplicação (consolidação, rentabilidade e fundos ilíquidos). Os números vêm
sempre do módulo `src.calc`; o modelo de linguagem apenas os interpreta.

O servidor age em nome de um usuário do sistema, informado em
CARTEIRA_MCP_EMAIL, e enxerga só as carteiras que esse usuário veria na
aplicação (RN-013). Cada consulta é registrada na auditoria (RF-026).

    CARTEIRA_MCP_EMAIL=investidor@exemplo.com python mcp_server.py
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from sqlalchemy import select

from src import audit, security
from src.calc import consolidacao
from src.db import criar_schema, sessao
from src.models import Usuario

servidor = MCPServer(
    name="carteira-tcc",
    instructions=(
        "Consulta, somente leitura, às carteiras de investimento do Sistema de "
        "Consolidação de Carteiras. Use listar_carteiras para obter o carteira_id. "
        "Valores monetários estão na moeda base da carteira; retornos são frações "
        "(0.05 = 5%). Não estime números: cite apenas o que as ferramentas devolvem."
    ),
)
_LEITURA = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


def _usuario(s) -> Usuario:
    email = os.getenv("CARTEIRA_MCP_EMAIL", "").strip().lower()
    usuario = s.scalar(select(Usuario).where(Usuario.email == email)) if email else None
    if usuario is None or not usuario.ativo:
        raise security.ErroAutenticacao(
            "Defina CARTEIRA_MCP_EMAIL com o e-mail de um usuário ativo do sistema.")
    return usuario


def _autorizar(s, carteira_id: str, ferramenta: str) -> Usuario:
    usuario = _usuario(s)
    if not security.pode_acessar_carteira(s, usuario, carteira_id):
        raise security.ErroAutorizacao("Carteira inexistente ou fora do seu acesso.")
    audit.registrar(s, usuario_id=usuario.id, acao=audit.CONSULTA_MCP,
                    entidade="CARTEIRA", entidade_id=carteira_id,
                    dados_novos={"ferramenta": ferramenta})
    return usuario


def _data(texto: str | None) -> date | None:
    return date.fromisoformat(texto) if texto else None


def _arred(valor: float | None, casas: int = 2) -> float | None:
    return None if valor is None else round(valor, casas)


@servidor.tool(annotations=_LEITURA)
def listar_carteiras() -> list[dict]:
    """Carteiras visíveis ao usuário configurado, com id, nome e moeda base."""
    with sessao() as s:
        usuario = _usuario(s)
        return [
            {"carteira_id": c.id, "nome": c.nome, "moeda_base": c.cliente.moeda_base}
            for c in security.carteiras_visiveis(s, usuario)
        ]


@servidor.tool(annotations=_LEITURA)
def posicao_consolidada(carteira_id: str, data: str | None = None) -> dict:
    """
    Patrimônio consolidado da carteira em uma data (AAAA-MM-DD; padrão: hoje),
    com a alocação por classe, moeda e país e as posições individuais (RF-019).
    """
    with sessao() as s:
        _autorizar(s, carteira_id, "posicao_consolidada")
        snap = consolidacao.snapshot(s, carteira_id, _data(data))
        total = snap.patrimonio or 1.0
        alocacao = lambda atributo: {k: _arred(v / total, 4) for k, v in snap.por(atributo).items()}
        return {
            "data": snap.data.isoformat(),
            "moeda_base": snap.moeda_base,
            "patrimonio": _arred(snap.patrimonio),
            "custo": _arred(snap.custo),
            "resultado": _arred(snap.resultado),
            "alocacao_por_classe": alocacao("classe_rotulo"),
            "alocacao_por_moeda": alocacao("moeda"),
            "alocacao_por_pais": alocacao("pais"),
            "posicoes": [
                {"ticker": l.ticker, "nome": l.nome, "classe": l.classe_rotulo,
                 "moeda": l.moeda, "quantidade": l.quantidade,
                 "valor_base": _arred(l.valor_base), "custo_base": _arred(l.custo_base),
                 "retorno": _arred(l.retorno, 4)}
                for l in sorted(snap.linhas, key=lambda l: l.valor_base, reverse=True)
            ],
            "alertas": snap.alertas,
        }


@servidor.tool(annotations=_LEITURA)
def rentabilidade(carteira_id: str, inicio: str | None = None, fim: str | None = None) -> dict:
    """
    Rentabilidade da carteira no período (AAAA-MM-DD; padrão: desde o início
    até hoje): TWR, MWR, volatilidade, drawdown, Sharpe e comparação com
    benchmarks (RF-015/016/018), além do TWR de cada ativo.
    """
    with sessao() as s:
        _autorizar(s, carteira_id, "rentabilidade")
        r = consolidacao.rentabilidade(s, carteira_id, _data(inicio), _data(fim))
        if r is None:
            return {"erro": "Período sem dados: a carteira não tem posições antes do fim pedido."}
        por_ativo = consolidacao.rentabilidade_por_ativo(s, carteira_id, r.inicio, r.fim)
        return {
            "inicio": r.inicio.isoformat(),
            "fim": r.fim.isoformat(),
            "patrimonio_inicial": _arred(r.patrimonio_inicial),
            "patrimonio_final": _arred(r.patrimonio_final),
            "aportes": _arred(r.aportes),
            "resgates": _arred(r.resgates),
            "twr": _arred(r.twr, 4),
            "twr_anualizado": _arred(r.twr_anualizado, 4),
            "mwr": _arred(r.mwr, 4),
            "volatilidade": _arred(r.volatilidade, 4),
            "max_drawdown": _arred(r.max_drawdown, 4),
            "sharpe": _arred(r.sharpe, 2),
            "benchmarks": [
                {"codigo": b.codigo, "nome": b.nome, "retorno": _arred(b.retorno, 4),
                 "diferenca": _arred(b.diferenca, 4), "superou": b.superou}
                for b in r.comparativos
            ],
            "por_ativo": [
                {"ticker": a.ticker, "classe": a.classe_rotulo, "inicio": a.inicio.isoformat(),
                 "twr": _arred(a.twr, 4), "twr_anualizado": _arred(a.twr_anualizado, 4)}
                for a in por_ativo
            ],
            "alertas": r.alertas,
        }


@servidor.tool(annotations=_LEITURA)
def fundos_iliquidos(carteira_id: str) -> list[dict]:
    """
    Métricas dos fundos de private equity, venture capital e real estate da
    carteira: commitment, paid-in, distribuições, NAV, DPI, RVPI, TVPI, MOIC
    e TIR (RF-017).
    """
    with sessao() as s:
        _autorizar(s, carteira_id, "fundos_iliquidos")
        return [
            {"identificador": m.identificador, "nome": m.nome, "gestor": m.gestor,
             "sub_classe": m.sub_classe, "moeda": m.moeda,
             "commitment": _arred(m.commitment), "paid_in": _arred(m.paid_in),
             "pendente": _arred(m.pendente), "distribuicoes": _arred(m.distribuicoes),
             "nav": _arred(m.nav), "data_nav": m.data_nav.isoformat() if m.data_nav else None,
             "dpi": _arred(m.dpi, 2), "rvpi": _arred(m.rvpi, 2), "tvpi": _arred(m.tvpi, 2),
             "moic": _arred(m.moic, 2), "tir": _arred(m.irr, 4),
             "percentual_chamado": _arred(m.percentual_chamado, 4)}
            for m in consolidacao.metricas_pe(s, carteira_id)
        ]


if __name__ == "__main__":
    criar_schema()
    servidor.run()
