"""
Metodologias de rentabilidade (seção 2.4.1).

O TCC fixa duas metodologias e o sistema implementa exatamente essas duas,
sem alternativas concorrentes:

  * TWR (Time-Weighted Return) — produto geométrico dos retornos de cada
    subperíodo delimitado por fluxo de caixa. Usado para avaliar ativos
    individuais e comparar com benchmarks (RN-010).
  * MWR/IRR (Money-Weighted Return) — taxa que zera o valor presente dos
    fluxos, resolvida por Newton-Raphson com chute inicial de 10% a.a.
    e queda para bisseção quando a derivada degenera (RN-011). Usado para
    a rentabilidade consolidada da carteira e para fundos de PE/VC.

Métricas de risco complementares: volatilidade anualizada, maximum
drawdown e índice de Sharpe.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Iterable, Sequence

import config

DIAS_ANO = 365.0
_TAXA_MINIMA = -0.9999


def dietz(v_ini: float, v_fim: float, aportes: Sequence[tuple[date, float]],
          inicio: date, fim: date) -> float | None:
    """
    Dietz modificado de um período (RN-010):

        R = (V_fim − V_ini − ΣCF) / (V_ini + Σ wⱼ·CFⱼ),   wⱼ = (T − tⱼ) / T

    `aportes` está na ótica da carteira: aporte positivo, resgate negativo,
    e só contém fluxos em (inicio, fim]. Devolve None quando a base é nula
    ou negativa — não há capital exposto e, portanto, retorno a medir.
    """
    duracao = (fim - inicio).days
    if duracao <= 0:
        return None
    total = sum(v for _, v in aportes)
    ponderado = sum(v * (fim - dia).days / duracao for dia, v in aportes)
    base = v_ini + ponderado
    if base <= 0:
        return None
    return (v_fim - v_ini - total) / base


def twr_subperiodos(retornos: Iterable[float]) -> float:
    """TWR = Π(1 + Rᵢ) − 1."""
    acumulado = 1.0
    houve = False
    for r in retornos:
        acumulado *= 1.0 + r
        houve = True
    return acumulado - 1.0 if houve else 0.0


def retornos_subperiodos(
    serie: Sequence[tuple[date, float]],
    fluxos: Sequence[tuple[date, float]] | None = None,
) -> list[tuple[date, float]]:
    """
    Retorno de cada subperíodo delimitado pelos pontos da série, expurgando
    o efeito dos aportes e resgates ocorridos dentro do subperíodo.

    Como os pontos da série são mensais e os fluxos caem em datas arbitrárias
    dentro do mês, o retorno de cada subperíodo é apurado pelo Dietz
    modificado, que pondera cada fluxo pelo tempo em que ficou investido:

        Rᵢ = (V_fim − V_ini − ΣCF) / (V_ini + Σ wⱼ·CFⱼ),   wⱼ = (T − tⱼ) / T

    Ignorar essa ponderação — tratar o valor final como se todo o aporte
    tivesse rendido o período inteiro — infla o retorno em carteiras que
    recebem aportes relevantes, que é exatamente o caso de uma carteira
    patrimonial em formação.

    `fluxos` segue a convenção do IRR: aporte negativo, resgate positivo.
    Internamente o sinal é invertido, pois do ponto de vista da carteira o
    aporte é entrada de recursos.
    """
    pontos = sorted(serie, key=lambda t: t[0])
    if len(pontos) < 2:
        return []

    ordenados = sorted(fluxos or [], key=lambda t: t[0])
    saida: list[tuple[date, float]] = []

    for (data_ini, valor_ini), (data_fim, valor_fim) in zip(pontos, pontos[1:]):
        do_periodo = [
            (dia, -valor) for dia, valor in ordenados if data_ini < dia <= data_fim
        ]
        retorno = dietz(valor_ini, valor_fim, do_periodo, data_ini, data_fim)
        if retorno is not None:
            saida.append((data_fim, retorno))
    return saida


def serie_indice(
    serie: Sequence[tuple[date, float]],
    fluxos: Sequence[tuple[date, float]] | None = None,
    base: float = 100.0,
) -> list[tuple[date, float]]:
    """
    Unitização da carteira: converte patrimônio + fluxos em uma série de
    valor de cota com base 100. É sobre esta série — e não sobre o
    patrimônio bruto — que volatilidade e drawdown devem ser medidos, já
    que o patrimônio bruto sobe por aporte e não apenas por rentabilidade.
    """
    pontos = sorted(serie, key=lambda t: t[0])
    if not pontos:
        return []

    indice = [(pontos[0][0], base)]
    valor = base
    for dia, retorno in retornos_subperiodos(serie, fluxos):
        valor *= 1.0 + retorno
        indice.append((dia, valor))
    return indice


def twr(
    serie: Sequence[tuple[date, float]],
    fluxos: Sequence[tuple[date, float]] | None = None,
) -> float:
    """TWR = Π(1 + Rᵢ) − 1, com Rᵢ apurado por Dietz modificado (RN-010)."""
    return twr_subperiodos(r for _, r in retornos_subperiodos(serie, fluxos))


def anualizar(retorno_total: float, dias: float) -> float:
    """Converte retorno acumulado em taxa equivalente anual."""
    if dias <= 0 or retorno_total <= -1:
        return 0.0
    return (1.0 + retorno_total) ** (DIAS_ANO / dias) - 1.0


def _vpl(taxa: float, fluxos: Sequence[tuple[date, float]], t0: date) -> float:
    total = 0.0
    for dia, valor in fluxos:
        expoente = (dia - t0).days / DIAS_ANO
        total += valor / ((1.0 + taxa) ** expoente)
    return total


def _dvpl(taxa: float, fluxos: Sequence[tuple[date, float]], t0: date) -> float:
    total = 0.0
    for dia, valor in fluxos:
        expoente = (dia - t0).days / DIAS_ANO
        if expoente == 0:
            continue
        total += -expoente * valor / ((1.0 + taxa) ** (expoente + 1.0))
    return total


def irr(
    fluxos: Sequence[tuple[date, float]],
    chute: float = config.IRR_CHUTE_INICIAL,
) -> float | None:
    """
    Taxa interna de retorno anualizada sobre fluxos datados (XIRR).

    Devolve None quando não há solução econômica — o que ocorre se todos
    os fluxos têm o mesmo sinal ou estão na mesma data (horizonte nulo: o
    VPL não depende da taxa). Explicitar a ausência de resultado é
    preferível a devolver um número sem significado.
    """
    if len(fluxos) < 2:
        return None
    valores = [v for _, v in fluxos]
    if not (any(v > 0 for v in valores) and any(v < 0 for v in valores)):
        return None

    ordenados = sorted(fluxos, key=lambda t: t[0])
    t0 = ordenados[0][0]
    if ordenados[-1][0] == t0:
        return None

    taxa = chute
    for _ in range(config.IRR_MAX_ITERACOES):
        try:
            f = _vpl(taxa, ordenados, t0)
            if abs(f) < config.IRR_TOLERANCIA:
                return taxa
            df = _dvpl(taxa, ordenados, t0)
            if abs(df) < 1e-12:
                break
            proxima = taxa - f / df
        except (OverflowError, ZeroDivisionError, ValueError):
            break
        if proxima <= _TAXA_MINIMA:
            proxima = (taxa + _TAXA_MINIMA) / 2.0
        if abs(proxima - taxa) < config.IRR_TOLERANCIA:
            return proxima
        taxa = proxima

    return _irr_bissecao(ordenados, t0)


def _irr_bissecao(fluxos: Sequence[tuple[date, float]], t0: date) -> float | None:
    baixo, alto = _TAXA_MINIMA, 10.0
    try:
        f_baixo, f_alto = _vpl(baixo, fluxos, t0), _vpl(alto, fluxos, t0)
    except (OverflowError, ValueError):
        return None
    if f_baixo * f_alto > 0:
        return None
    for _ in range(200):
        meio = (baixo + alto) / 2.0
        try:
            f_meio = _vpl(meio, fluxos, t0)
        except (OverflowError, ValueError):
            return None
        if abs(f_meio) < config.IRR_TOLERANCIA or (alto - baixo) < 1e-9:
            return meio
        if f_baixo * f_meio < 0:
            alto, f_alto = meio, f_meio
        else:
            baixo, f_baixo = meio, f_meio
    return (baixo + alto) / 2.0


mwr = irr


def irr_ambigua(fluxos: Sequence[tuple[date, float]]) -> bool:
    """
    True quando mais de uma taxa zera o VPL (fluxos com várias inversões de
    sinal podem ter raízes múltiplas). Nesse caso o IRR devolvido é apenas
    uma das soluções e deve ser lido com cautela. A busca percorre taxas de
    −99% a +1000% a.a. e conta as trocas de sinal do VPL.
    """
    ordenados = sorted(fluxos, key=lambda t: t[0])
    if len(ordenados) < 3:
        return False
    t0 = ordenados[0][0]
    taxas = [-0.99 + i * 0.01 for i in range(100)] + [i * 0.05 for i in range(1, 201)]
    trocas, anterior = 0, None
    for taxa in taxas:
        try:
            atual = _vpl(taxa, ordenados, t0)
        except (OverflowError, ZeroDivisionError):
            continue
        if anterior is not None and atual * anterior < 0:
            trocas += 1
        if atual != 0:
            anterior = atual
    return trocas > 1


def retornos_diarios(serie: Sequence[tuple[date, float]]) -> list[float]:
    pontos = sorted(serie, key=lambda t: t[0])
    saida: list[float] = []
    for (_, anterior), (_, atual) in zip(pontos, pontos[1:]):
        if anterior > 0:
            saida.append(atual / anterior - 1.0)
    return saida


def desvio_padrao(valores: Sequence[float]) -> float:
    if len(valores) < 2:
        return 0.0
    media = sum(valores) / len(valores)
    var = sum((v - media) ** 2 for v in valores) / (len(valores) - 1)
    return math.sqrt(var)


def periodos_por_ano(serie: Sequence[tuple[date, float]]) -> float:
    """
    Frequência efetiva da série, inferida do intervalo médio entre pontos.
    Anualizar uma série mensal por √252 — como se fosse diária — inflaria a
    volatilidade em cerca de cinco vezes, por isso o fator vem da série real.
    """
    pontos = sorted(serie, key=lambda t: t[0])
    if len(pontos) < 2:
        return config.DIAS_UTEIS_ANO
    intervalos = [
        (b - a).days for (a, _), (b, _) in zip(pontos, pontos[1:]) if (b - a).days > 0
    ]
    if not intervalos:
        return config.DIAS_UTEIS_ANO
    medio = sum(intervalos) / len(intervalos)
    if medio <= 3:
        return config.DIAS_UTEIS_ANO
    return DIAS_ANO / medio


def volatilidade(
    serie: Sequence[tuple[date, float]], retornos: Sequence[float] | None = None
) -> float:
    """
    Volatilidade anualizada.

    Se `retornos` for informado, usa esses retornos (já expurgados de fluxo);
    caso contrário, deriva-os da própria série. O fator de anualização
    respeita a frequência da série.
    """
    rets = list(retornos) if retornos is not None else retornos_diarios(serie)
    if len(rets) < 2:
        return 0.0
    fator = math.sqrt(periodos_por_ano(serie))
    return desvio_padrao(rets) * fator


def max_drawdown(serie: Sequence[tuple[date, float]]) -> float:
    """Maior queda percentual do pico ao vale. Valor negativo ou zero."""
    pontos = sorted(serie, key=lambda t: t[0])
    pico, pior = float("-inf"), 0.0
    for _, valor in pontos:
        pico = max(pico, valor)
        if pico > 0:
            pior = min(pior, valor / pico - 1.0)
    return pior


def sharpe(retorno_anual: float, vol_anual: float, taxa_livre_risco: float) -> float | None:
    """Índice de Sharpe. None quando a volatilidade é nula (indefinido)."""
    if vol_anual <= 0:
        return None
    return (retorno_anual - taxa_livre_risco) / vol_anual
