"""
Séries de mercado sintéticas para validação acadêmica.

⚠️  ATENÇÃO — ESTES DADOS NÃO SÃO REAIS.

Todas as séries produzidas aqui (câmbio, benchmarks e preços de ativos) são
geradas por simulação e existem apenas para exercitar o sistema em ambiente
offline, coerente com a nota do TCC de que os dados exibidos são ilustrativos
e sintéticos. Registros gerados por este módulo recebem a marca de fonte
'SIMULADO' em PRECO_HISTORICO / TAXA_CAMBIO, de modo que a origem fica
explícita no banco e nunca se confunde com coleta real.

Em produção, as mesmas tabelas são alimentadas por:
    câmbio     → src.market.cambio.atualizar_bcb   (PTAX / Banco Central)
    preços     → src.market.precos.atualizar_ativo (Yahoo Finance)
    benchmarks → coleta equivalente por índice

O gerador é determinístico (semente fixa por série): a mesma execução
produz sempre os mesmos números, o que torna os testes reprodutíveis.
"""
from __future__ import annotations

import math
import random
import zlib
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from src.market import benchmarks as bmk_srv
from src.market.cambio import registrar_taxa
from src.models import PrecoHistorico

FONTE = "SIMULADO"


def _dias_uteis(inicio: date, fim: date) -> list[date]:
    dias, atual = [], inicio
    while atual <= fim:
        if atual.weekday() < 5:
            dias.append(atual)
        atual += timedelta(days=1)
    return dias


def _passeio(
    inicio: date, fim: date, valor_inicial: float,
    deriva_anual: float, vol_anual: float, semente: int,
) -> list[tuple[date, float]]:
    """Movimento browniano geométrico diário, determinístico pela semente."""
    rng = random.Random(semente)
    dias = _dias_uteis(inicio, fim)
    dt = 1.0 / config.DIAS_UTEIS_ANO
    mu = (deriva_anual - 0.5 * vol_anual**2) * dt
    sigma = vol_anual * math.sqrt(dt)

    serie, valor = [], valor_inicial
    for dia in dias:
        valor *= math.exp(mu + sigma * rng.gauss(0.0, 1.0))
        serie.append((dia, round(valor, 6)))
    return serie


def _acumulador(
    inicio: date, fim: date, taxa_anual: float, ruido: float, semente: int,
) -> list[tuple[date, float]]:
    """Índice acumulado a partir de base 100, para CDI e IPCA."""
    rng = random.Random(semente)
    dias = _dias_uteis(inicio, fim)
    serie, indice = [], 100.0
    for dia in dias:
        taxa_dia = (1.0 + taxa_anual * (1.0 + rng.uniform(-ruido, ruido))) ** (1 / config.DIAS_UTEIS_ANO) - 1.0
        indice *= 1.0 + taxa_dia
        serie.append((dia, round(indice, 6)))
    return serie


def gerar_cambio(sessao: Session, inicio: date, fim: date) -> int:
    """USD/BRL, EUR/BRL e GBP/BRL sintéticos, em níveis plausíveis para o período."""
    total = 0
    for moeda, inicial, deriva, vol, semente in (
        ("USD", 3.85, 0.085, 0.14, 20260101),
        ("EUR", 4.40, 0.070, 0.13, 20260202),
        ("GBP", 4.95, 0.065, 0.13, 20260303),
    ):
        for dia, taxa in _passeio(inicio, fim, inicial, deriva, vol, semente):
            registrar_taxa(sessao, dia, moeda, "BRL", taxa, FONTE)
            total += 1
    sessao.commit()
    return total


PARAMETROS_BENCHMARK = {
    "IBOV": (0.085, 0.230, 20260028),
    "SP500": (0.105, 0.165, 20260010),
    "IFIX": (0.070, 0.110, 20260008),
}
PARAMETROS_TAXA = {
    "CDI": (0.105, 0.15, 20260606),
    "IPCA": (0.045, 0.30, 20260707),
}


def gerar_benchmarks(sessao: Session, inicio: date, fim: date) -> int:
    catalogo = bmk_srv.garantir_catalogo(sessao)
    total = 0

    for codigo, (deriva, vol, semente) in PARAMETROS_BENCHMARK.items():
        for dia, valor in _passeio(inicio, fim, 100.0, deriva, vol, semente):
            bmk_srv.registrar_valor(sessao, catalogo[codigo], dia, valor)
            total += 1

    for codigo, (taxa, ruido, semente) in PARAMETROS_TAXA.items():
        for dia, valor in _acumulador(inicio, fim, taxa, ruido, semente):
            bmk_srv.registrar_valor(sessao, catalogo[codigo], dia, valor)
            total += 1

    sessao.commit()
    return total


PERFIL_CLASSE = {
    "ACAO": (0.11, 0.28),
    "ETF": (0.09, 0.18),
    "FII": (0.08, 0.14),
    "FUNDO": (0.09, 0.12),
    "TESOURO_DIRETO": (0.10, 0.05),
    "RENDA_FIXA": (0.10, 0.03),
}


def gerar_precos_ativo(
    sessao: Session, ativo, preco_inicial: float, inicio: date, fim: date
) -> int:
    """Série diária de preços para um ativo listado, ancorada no preço médio."""
    deriva, vol = PERFIL_CLASSE.get(ativo.classe, (0.09, 0.20))
    semente = zlib.crc32(ativo.ticker_canonico.encode()) % (2**31)
    serie = _passeio(inicio, fim, preco_inicial, deriva, vol, semente)

    existentes = set(sessao.scalars(
        select(PrecoHistorico.data).where(PrecoHistorico.ativo_id == ativo.id)
    ))
    novos = 0
    for dia, preco in serie:
        if dia in existentes:
            continue
        sessao.add(PrecoHistorico(
            ativo_id=ativo.id, data=dia, preco_fechamento=round(preco, 4), fonte=FONTE
        ))
        novos += 1
    return novos
