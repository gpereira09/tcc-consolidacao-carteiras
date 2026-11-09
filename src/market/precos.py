"""
Enriquecimento de preços de mercado (RF-008, RN-006, RN-007).

Política adotada, alinhada à seção 5.8 do TCC: o preço informado pelo
usuário no template é a fonte primária; a API externa é complementar.
Falha de coleta nunca quebra a consolidação — o sistema recai sobre o
último preço conhecido em PRECO_HISTORICO.

O sistema usa um único provedor externo (Yahoo Finance, via yfinance).
Manter um provedor só, isolado nas funções `_coletar_yahoo` e `_info_yahoo`,
é uma decisão deliberada: acrescentar fontes multiplicaria os modos de
falha sem ganho proporcional no escopo do MVP. Não há retry nem circuit
breaker: uma falha de rede é registrada e a coleta segue com o último preço.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from src.models import AssetMapping, Ativo, BenchmarkValor, Posicao, PrecoHistorico, TaxaCambio

FONTE_YAHOO = "YAHOO"

SUFIXO_YAHOO = {
    "B3": ".SA",
    "NYSE": "",
    "NASDAQ": "",
    "LSE": ".L",
    "EURONEXT": ".AS",
}


@dataclass
class ResultadoColeta:
    ativo_id: str
    ticker: str
    sucesso: bool
    preco: float | None = None
    data: date | None = None
    mensagem: str = ""


def ticker_externo(sessao: Session, ativo: Ativo) -> str:
    """
    Converte o ticker canônico no símbolo da fonte externa, consultando
    ASSET_MAPPING antes de aplicar a regra de sufixo por mercado (seção 5.8).
    """
    mapeado = sessao.scalar(
        select(AssetMapping).where(
            AssetMapping.ticker_canonical == ativo.ticker_canonico,
            AssetMapping.fonte == FONTE_YAHOO,
        )
    )
    if mapeado:
        return mapeado.ticker_source
    return f"{ativo.ticker_canonico}{SUFIXO_YAHOO.get(ativo.mercado or '', '')}"


def ha_dados_simulados(sessao: Session) -> bool:
    """
    True se a base contém séries do gerador sintético (fonte SIMULADO) — preços,
    câmbio ou benchmarks. Os benchmarks não têm coletor real nesta versão, então
    qualquer valor de benchmark é sintético.
    """
    consultas = (
        select(PrecoHistorico.id).where(PrecoHistorico.fonte == "SIMULADO"),
        select(TaxaCambio.id).where(TaxaCambio.fonte == "SIMULADO"),
        select(BenchmarkValor.id),
    )
    return any(sessao.scalar(c.limit(1)) is not None for c in consultas)


def registrar_mapeamento(
    sessao: Session, canonico: str, externo: str, fonte: str = FONTE_YAHOO,
    asset_type: str | None = None,
) -> AssetMapping:
    existente = sessao.scalar(
        select(AssetMapping).where(
            AssetMapping.ticker_source == externo, AssetMapping.fonte == fonte
        )
    )
    if existente:
        existente.ticker_canonical = canonico
        return existente
    novo = AssetMapping(
        ticker_source=externo, ticker_canonical=canonico,
        fonte=fonte, asset_type=asset_type,
    )
    sessao.add(novo)
    return novo


def _coletar_yahoo(simbolo: str, dias: int = 7) -> tuple[date, float] | None:
    """Último fechamento disponível. Devolve None em qualquer falha."""
    try:
        import yfinance
    except ImportError:
        return None
    try:
        hist = yfinance.Ticker(simbolo).history(
            start=date.today() - timedelta(days=dias), auto_adjust=False
        )
        if hist is None or hist.empty:
            return None
        ultima = hist.iloc[-1]
        indice = hist.index[-1]
        dia = indice.date() if hasattr(indice, "date") else date.today()
        return dia, float(ultima["Close"])
    except Exception:
        return None


SETORES_PT = {
    "Technology": "Tecnologia", "Financial Services": "Financeiro",
    "Energy": "Petróleo e Gás", "Basic Materials": "Mineração e Materiais",
    "Consumer Cyclical": "Consumo Cíclico", "Consumer Defensive": "Consumo Não Cíclico",
    "Healthcare": "Saúde", "Industrials": "Industrial", "Utilities": "Utilities",
    "Real Estate": "Imobiliário", "Communication Services": "Comunicações",
}


def _info_yahoo(simbolo: str) -> tuple[str | None, str | None]:
    """(nome, setor) do cadastro do Yahoo Finance; (None, None) em qualquer falha."""
    try:
        import yfinance
        info = yfinance.Ticker(simbolo).info or {}
    except Exception:
        return None, None
    nome = info.get("shortName") or info.get("longName")
    setor = info.get("sector")
    return nome, SETORES_PT.get(setor, setor) if setor else None


def completar_cadastro(sessao: Session, ativo: Ativo, consulta=_info_yahoo) -> bool:
    """
    Quadro 16 — nome e setor dos ativos listados são preenchidos
    automaticamente quando o usuário os deixa em branco no template.
    Nunca sobrescreve o que o usuário informou.
    """
    falta_nome = not ativo.nome or ativo.nome == ativo.ticker_canonico
    falta_setor = not ativo.setor and ativo.classe in config.CLASSES_RENDA_VARIAVEL
    if not ativo.possui_ticker_listado or not (falta_nome or falta_setor):
        return False
    nome, setor = consulta(ticker_externo(sessao, ativo))
    alterou = False
    if falta_nome and nome:
        ativo.nome, alterou = nome, True
    if falta_setor and setor:
        ativo.setor, alterou = setor, True
    return alterou


def preco_mais_recente(
    sessao: Session, ativo_id: str, ate: date | None = None
) -> PrecoHistorico | None:
    stmt = select(PrecoHistorico).where(PrecoHistorico.ativo_id == ativo_id)
    if ate:
        stmt = stmt.where(PrecoHistorico.data <= ate)
    return sessao.scalar(stmt.order_by(PrecoHistorico.data.desc()).limit(1))


def registrar_preco(sessao: Session, ativo_id: str, dia: date, preco: float, fonte: str) -> None:
    """Grava (ou substitui) o preço do ativo na data em PRECO_HISTORICO."""
    existe = sessao.scalar(
        select(PrecoHistorico).where(
            PrecoHistorico.ativo_id == ativo_id, PrecoHistorico.data == dia
        )
    )
    if existe:
        existe.preco_fechamento = preco
        existe.fonte = fonte
    else:
        sessao.add(PrecoHistorico(ativo_id=ativo_id, data=dia,
                                  preco_fechamento=preco, fonte=fonte))


def _cache_valido(registro: PrecoHistorico | None) -> bool:
    if registro is None or registro.fonte == "TEMPLATE":
        return False
    idade = datetime.now().date() - registro.data
    return idade <= timedelta(hours=config.CACHE_PRECO_HORAS)


def atualizar_ativo(sessao: Session, ativo: Ativo, forcar: bool = False) -> ResultadoColeta:
    """Atualiza o preço de um único ativo listado (RN-006)."""
    if not ativo.possui_ticker_listado:
        return ResultadoColeta(ativo.id, ativo.ticker_canonico, False,
                               mensagem="ativo sem ticker listado — RN-007 aplica preço do template")
    registro = preco_mais_recente(sessao, ativo.id)
    if not forcar and _cache_valido(registro):
        return ResultadoColeta(ativo.id, ativo.ticker_canonico, True,
                               registro.preco_fechamento, registro.data, "cache local")

    simbolo = ticker_externo(sessao, ativo)
    coletado = _coletar_yahoo(simbolo)
    if coletado is None:
        return ResultadoColeta(
            ativo.id, ativo.ticker_canonico, False,
            registro.preco_fechamento if registro else None,
            registro.data if registro else None,
            "fonte externa indisponível — mantido o último preço conhecido",
        )

    dia, preco = coletado
    completar_cadastro(sessao, ativo)
    registrar_preco(sessao, ativo.id, dia, preco, FONTE_YAHOO)
    return ResultadoColeta(ativo.id, ativo.ticker_canonico, True, preco, dia, "coletado")


def atualizar_carteira(sessao: Session, carteira_id: str, forcar: bool = False) -> list[ResultadoColeta]:
    """RF-008 — enriquece todas as posições listadas de uma carteira."""
    ativos = sessao.scalars(
        select(Ativo)
        .join(Posicao, Posicao.ativo_id == Ativo.id)
        .where(Posicao.carteira_id == carteira_id, Ativo.possui_ticker_listado.is_(True))
        .distinct()
    ).all()
    resultados = [atualizar_ativo(sessao, ativo, forcar) for ativo in ativos]
    sessao.commit()
    return resultados
