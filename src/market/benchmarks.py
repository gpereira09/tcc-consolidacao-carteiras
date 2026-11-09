"""
Benchmarks de mercado (RF-018, seção 2.4.3).

Cinco referências suportadas, conforme o Quadro 5 do TCC:
    CDI · IBOVESPA · IPCA · S&P 500 · IFIX

Todas são armazenadas como número-índice (base 100 na primeira data da
série). Isso uniformiza o tratamento de índices de preço (IBOV, S&P, IFIX)
e de taxas acumuladas (CDI, IPCA): o retorno de qualquer benchmark entre
duas datas passa a ser sempre `valor_fim / valor_inicio − 1`.

Na comparação com a carteira, o retorno do índice é expresso na moeda base
do cliente: um índice em outra moeda (o S&P 500 é cotado em USD) tem o
retorno composto com a variação cambial do período,
(1 + r_local) × câmbio_fim / câmbio_início − 1. Sem câmbio disponível, o
retorno fica na moeda do índice e o comparativo informa essa moeda.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.calc.rentabilidade import anualizar
from src.market import cambio
from src.models import Benchmark, BenchmarkValor

CATALOGO = {
    "CDI": ("Certificado de Depósito Interbancário", "TAXA", "BRL"),
    "IBOV": ("Índice Bovespa", "INDICE", "BRL"),
    "IPCA": ("Índice Nacional de Preços ao Consumidor Amplo", "INFLACAO", "BRL"),
    "SP500": ("S&P 500", "INDICE", "USD"),
    "IFIX": ("Índice de Fundos Imobiliários", "INDICE", "BRL"),
}


@dataclass
class ComparativoBenchmark:
    codigo: str
    nome: str
    retorno: float
    diferenca: float
    superou: bool
    moeda: str = "BRL"


def _por_codigo(sessao: Session, codigo: str) -> Benchmark | None:
    return sessao.scalar(select(Benchmark).where(Benchmark.codigo == codigo))


def garantir_catalogo(sessao: Session) -> dict[str, Benchmark]:
    """Cria os registros de BENCHMARK que ainda não existirem."""
    saida: dict[str, Benchmark] = {}
    for codigo, (nome, tipo, moeda) in CATALOGO.items():
        bmk = _por_codigo(sessao, codigo)
        if bmk is None:
            bmk = Benchmark(codigo=codigo, nome=nome, tipo=tipo, moeda=moeda)
            sessao.add(bmk)
            sessao.flush()
        saida[codigo] = bmk
    return saida


def registrar_valor(sessao: Session, bmk: Benchmark, dia: date, valor: float) -> None:
    existente = sessao.scalar(
        select(BenchmarkValor).where(
            BenchmarkValor.benchmark_id == bmk.id, BenchmarkValor.data == dia
        )
    )
    if existente:
        existente.valor = valor
    else:
        sessao.add(BenchmarkValor(benchmark_id=bmk.id, data=dia, valor=valor))


def serie(sessao: Session, codigo: str,
          inicio: date | None = None, fim: date | None = None) -> list[tuple[date, float]]:
    bmk = _por_codigo(sessao, codigo)
    if bmk is None:
        return []
    stmt = select(BenchmarkValor).where(BenchmarkValor.benchmark_id == bmk.id)
    if inicio:
        stmt = stmt.where(BenchmarkValor.data >= inicio)
    if fim:
        stmt = stmt.where(BenchmarkValor.data <= fim)
    registros = sessao.scalars(stmt.order_by(BenchmarkValor.data)).all()
    return [(r.data, r.valor) for r in registros]


def serie_na_moeda(sessao: Session, codigo: str, inicio: date | None, fim: date | None,
                   moeda_base: str) -> list[tuple[date, float]]:
    """
    Série do índice expressa na moeda base: cada ponto é multiplicado pela
    taxa de câmbio da data (forward fill). Usada no gráfico, que é rebaseado
    a 100; sem câmbio disponível, devolve a série na moeda original.
    """
    pontos = serie(sessao, codigo, inicio, fim)
    moeda = CATALOGO.get(codigo, ("", "", moeda_base))[2]
    if moeda == moeda_base or not pontos:
        return pontos
    taxas = cambio.serie(sessao, moeda, moeda_base, pontos[-1][0])
    if not taxas or taxas[0][0] > pontos[0][0]:
        return pontos
    datas = [d for d, _ in taxas]
    return [(d, v * taxas[bisect_right(datas, d) - 1][1]) for d, v in pontos]


def _valor_em(sessao: Session, bmk: Benchmark, dia: date) -> float | None:
    """Valor do índice na data, com forward fill (mesma convenção da RN-009)."""
    registro = sessao.scalar(
        select(BenchmarkValor)
        .where(BenchmarkValor.benchmark_id == bmk.id, BenchmarkValor.data <= dia)
        .order_by(BenchmarkValor.data.desc())
        .limit(1)
    )
    return registro.valor if registro else None


def retorno(sessao: Session, codigo: str, inicio: date, fim: date) -> float | None:
    bmk = _por_codigo(sessao, codigo)
    if bmk is None:
        return None
    v0, v1 = _valor_em(sessao, bmk, inicio), _valor_em(sessao, bmk, fim)
    if not v0 or v1 is None:
        return None
    return v1 / v0 - 1.0


def _na_moeda_base(sessao: Session, r: float, moeda: str, moeda_base: str,
                   inicio: date, fim: date) -> tuple[float, str]:
    if moeda == moeda_base:
        return r, moeda
    try:
        variacao = (cambio.obter_taxa(sessao, moeda, moeda_base, fim)
                    / cambio.obter_taxa(sessao, moeda, moeda_base, inicio))
    except cambio.TaxaIndisponivel:
        return r, moeda
    return (1.0 + r) * variacao - 1.0, moeda_base


def comparar(
    sessao: Session, retorno_carteira: float, inicio: date, fim: date,
    moeda_base: str = "BRL", codigos: list[str] | None = None,
) -> list[ComparativoBenchmark]:
    """RF-018 — compara o retorno da carteira com cada benchmark no período."""
    saida: list[ComparativoBenchmark] = []
    for codigo in codigos or list(CATALOGO):
        r = retorno(sessao, codigo, inicio, fim)
        if r is None:
            continue
        r, moeda = _na_moeda_base(sessao, r, CATALOGO[codigo][2], moeda_base, inicio, fim)
        saida.append(ComparativoBenchmark(
            codigo=codigo,
            nome=CATALOGO[codigo][0],
            retorno=r,
            diferenca=retorno_carteira - r,
            superou=retorno_carteira > r,
            moeda=moeda,
        ))
    return saida


def taxa_livre_risco(sessao: Session, inicio: date, fim: date) -> float:
    """CDI acumulado no período, anualizado — insumo do índice de Sharpe."""
    r = retorno(sessao, "CDI", inicio, fim)
    if r is None:
        return 0.0
    return anualizar(r, max((fim - inicio).days, 1))
