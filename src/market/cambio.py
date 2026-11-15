"""
Conversão multi-moeda com taxas históricas (RN-009).

Regra: a conversão usa a taxa da data de referência da posição. Se não
houver cotação para a data (fim de semana, feriado, falha de coleta),
aplica-se forward fill — a última taxa conhecida anterior à data.

Fonte de produção: API de dados abertos do Banco Central (PTAX).
Em ambiente offline, o sistema opera sobre a série já persistida em
TAXA_CAMBIO, o que mantém o cálculo determinístico e auditável.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from src.models import TaxaCambio

BCB_PTAX_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
    "CotacaoMoedaPeriodo(moeda=@moeda,dataInicial=@dataInicial,"
    "dataFinalCotacao=@dataFinalCotacao)"
)


class TaxaIndisponivel(Exception):
    """Nenhuma taxa conhecida para o par/data solicitados."""


def obter_taxa(sessao: Session, origem: str, destino: str, dia: date) -> float:
    """
    Taxa para converter 1 unidade de `origem` em `destino` na data `dia`.
    Resolve por: identidade → cotação direta → forward fill → inversa → triangulação via BRL.
    """
    origem, destino = origem.upper(), destino.upper()
    if origem == destino:
        return 1.0

    direta = _buscar_ffill(sessao, origem, destino, dia)
    if direta is not None:
        return direta

    inversa = _buscar_ffill(sessao, destino, origem, dia)
    if inversa is not None and inversa != 0:
        return 1.0 / inversa

    if "BRL" not in (origem, destino):
        para_brl = _buscar_ffill(sessao, origem, "BRL", dia)
        de_brl = _buscar_ffill(sessao, destino, "BRL", dia)
        if para_brl is not None and de_brl:
            return para_brl / de_brl

    raise TaxaIndisponivel(
        f"Sem cotação de {origem}/{destino} para {dia:%d/%m/%Y} ou data anterior."
    )


def _buscar_ffill(sessao: Session, origem: str, destino: str, dia: date) -> float | None:
    """Última taxa com data <= `dia` (forward fill), conforme RN-009."""
    stmt = (
        select(TaxaCambio)
        .where(
            TaxaCambio.moeda_origem == origem,
            TaxaCambio.moeda_destino == destino,
            TaxaCambio.data <= dia,
        )
        .order_by(TaxaCambio.data.desc())
        .limit(1)
    )
    registro = sessao.scalar(stmt)
    return registro.taxa if registro else None


def serie(sessao: Session, origem: str, destino: str, fim: date) -> list[tuple[date, float]]:
    """Cotações (data, taxa) do par até `fim`; usa a série inversa se a direta não existir."""
    def _consulta(o: str, d: str):
        return sessao.execute(
            select(TaxaCambio.data, TaxaCambio.taxa)
            .where(TaxaCambio.moeda_origem == o, TaxaCambio.moeda_destino == d,
                   TaxaCambio.data <= fim)
            .order_by(TaxaCambio.data)
        ).all()

    direta = _consulta(origem.upper(), destino.upper())
    if direta:
        return [(d, t) for d, t in direta]
    return [(d, 1.0 / t) for d, t in _consulta(destino.upper(), origem.upper()) if t]


def converter(sessao: Session, valor: float, origem: str, destino: str, dia: date) -> float:
    if valor == 0 or origem.upper() == destino.upper():
        return valor
    return valor * obter_taxa(sessao, origem, destino, dia)


def converter_seguro(
    sessao: Session, valor: float, origem: str, destino: str, dia: date
) -> tuple[float, bool]:
    """
    Como `converter`, mas devolve (valor, convertido_com_sucesso) em vez de
    lançar exceção. O dashboard usa esta variante para sinalizar ao usuário
    a posição que não pôde ser convertida, em vez de falhar a tela inteira.
    """
    try:
        return converter(sessao, valor, origem, destino, dia), True
    except TaxaIndisponivel:
        return valor, False


def registrar_taxa(
    sessao: Session, dia: date, origem: str, destino: str, taxa: float, fonte: str = "BCB"
) -> TaxaCambio:
    existente = sessao.scalar(
        select(TaxaCambio).where(
            TaxaCambio.data == dia,
            TaxaCambio.moeda_origem == origem,
            TaxaCambio.moeda_destino == destino,
        )
    )
    if existente:
        existente.taxa = taxa
        existente.fonte = fonte
        return existente
    registro = TaxaCambio(
        data=dia, moeda_origem=origem, moeda_destino=destino, taxa=taxa, fonte=fonte
    )
    sessao.add(registro)
    return registro


def atualizar_bcb(
    sessao: Session, moeda: str, inicio: date, fim: date | None = None
) -> int:
    """
    Coleta a série PTAX de venda (moeda/BRL) no período e persiste.
    Devolve a quantidade de dias com cotação gravada. Falha de rede não propaga:
    o sistema segue operando com a série já armazenada (seção 5.8).
    """
    import requests

    fim = fim or date.today()
    params = {
        "@moeda": f"'{moeda.upper()}'",
        "@dataInicial": f"'{inicio:%m-%d-%Y}'",
        "@dataFinalCotacao": f"'{fim:%m-%d-%Y}'",
        "$format": "json",
        "$select": "cotacaoVenda,dataHoraCotacao",
    }
    try:
        resp = requests.get(BCB_PTAX_URL, params=params, timeout=config.HTTP_TIMEOUT_SERIE)
        resp.raise_for_status()
        itens = resp.json().get("value", [])
    except Exception:
        return 0

    # O BCB publica vários boletins por dia; vale o último (fechamento).
    cotacoes: dict[date, float] = {}
    for item in itens:
        try:
            dia = date.fromisoformat(item["dataHoraCotacao"][:10])
            taxa = float(item["cotacaoVenda"])
        except (KeyError, ValueError, TypeError):
            continue
        if taxa > 0:
            cotacoes[dia] = taxa
    for dia, taxa in cotacoes.items():
        registrar_taxa(sessao, dia, moeda.upper(), "BRL", taxa, "BCB_PTAX")
    sessao.commit()
    return len(cotacoes)

