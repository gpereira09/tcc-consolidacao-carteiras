"""Visão consolidada (RF-014, RF-015, RF-016, RF-018 a RF-022; UC-12, UC-13, UC-14)."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from src import formatacao
from src.calc import consolidacao
from src.db import SessionLocal
from src.market import benchmarks as bmk_srv
from src.market import precos
from ui import comum, estilo

PERIODOS = {
    "Desde o início": None,
    "12 meses": 365,
    "24 meses": 730,
    "No ano": "ytd",
}


def render() -> None:
    carteira = comum.exigir_carteira()
    sessao = comum.sessao()
    base = comum.moeda_base()

    comum.cabecalho("Visão consolidada",
                    "Patrimônio agregado em ativos líquidos, fundos ilíquidos e imóveis.")

    col_periodo, col_acao = st.columns([3, 1])
    with col_periodo:
        rotulo = st.radio("Período", list(PERIODOS), horizontal=True,
                          label_visibility="collapsed")
    with col_acao:
        if st.button("Atualizar cotações", use_container_width=True):
            _atualizar(sessao, carteira.id)

    inicio = _inicio_do_periodo(PERIODOS[rotulo])
    versao = consolidacao.versao_dos_dados(sessao)
    snap, variacoes, inicio_carteira = _retrato(carteira.id, base, versao)

    if not snap.linhas:
        st.info("Esta carteira ainda não possui posições. "
                "Use a tela “Importar carteira” para carregar um template.")
        return

    for alerta in snap.alertas:
        st.warning(alerta)

    desde = _rentabilidade(carteira.id, None, base, versao)
    _patrimonio(snap, base)
    _variacoes(variacoes, inicio_carteira, base, desde)
    st.divider()

    rent = desde if inicio is None else _rentabilidade(carteira.id, inicio, base, versao)
    for alerta in rent.alertas if rent else []:
        st.warning(alerta)
    _indicadores(snap, rent, base)

    aba_aloc, aba_evol, aba_bmk, aba_pos = st.tabs(
        ["Alocação", "Evolução", "Benchmarks", "Posições"]
    )
    with aba_aloc:
        _alocacao(snap, base)
    with aba_evol:
        _evolucao(rent, base)
    with aba_bmk:
        _benchmarks(rent, base, versao)
    with aba_pos:
        _posicoes(snap, base)
        _twr_ativos(carteira.id, inicio, base, versao)


@st.cache_data(show_spinner=False, max_entries=32)
def _retrato(carteira_id: str, moeda: str, versao: tuple):
    """Snapshot de hoje e variações no dia, no mês e no ano (RF-019)."""
    with SessionLocal() as s:
        snap = consolidacao.snapshot(s, carteira_id, moeda_base=moeda)
        inicio_carteira = consolidacao.data_inicial_carteira(s, carteira_id)
        hoje = snap.data
        variacoes = []
        for rotulo, inicio in (("No dia", hoje - timedelta(days=1)),
                               ("No mês", hoje.replace(day=1) - timedelta(days=1)),
                               ("No ano", date(hoje.year - 1, 12, 31))):
            if inicio_carteira and inicio < inicio_carteira:
                inicio = inicio_carteira
            variacoes.append(consolidacao.variacao(s, carteira_id, inicio, hoje, moeda, rotulo,
                                                   patrimonio_final=snap.patrimonio))
        return snap, variacoes, inicio_carteira


@st.cache_data(show_spinner=False, max_entries=64)
def _serie_benchmark(codigo: str, fim: date, moeda: str, versao: tuple):
    with SessionLocal() as s:
        return bmk_srv.serie_na_moeda(s, codigo, None, fim, moeda)


@st.cache_data(show_spinner=False, max_entries=32)
def _rentabilidade(carteira_id: str, inicio: date | None, moeda: str, versao: tuple):
    """Cache por (carteira, período, moeda, versão dos dados); `versao` invalida."""
    with SessionLocal() as s:
        return consolidacao.rentabilidade(s, carteira_id, inicio, moeda_base=moeda)


@st.cache_data(show_spinner=False, max_entries=32)
def _rentabilidade_por_ativo(carteira_id: str, inicio: date | None, moeda: str, versao: tuple):
    with SessionLocal() as s:
        return consolidacao.rentabilidade_por_ativo(s, carteira_id, inicio, moeda_base=moeda)


def _inicio_do_periodo(janela) -> date | None:
    if janela is None:
        return None
    if janela == "ytd":
        return date(config.hoje().year, 1, 1)
    return config.hoje() - timedelta(days=janela)


def _atualizar(sessao, carteira_id: str) -> None:
    with st.spinner("Consultando a fonte de cotações…"):
        resultados = precos.atualizar_carteira(sessao, carteira_id, forcar=True)
    for funcao in (_retrato, _rentabilidade, _rentabilidade_por_ativo):
        funcao.clear()
    ok = sum(1 for r in resultados if r.sucesso)
    if ok:
        st.success(f"{ok} de {len(resultados)} ativos atualizados.")
    if ok < len(resultados):
        st.info(
            "Alguns ativos não puderam ser atualizados; o sistema manteve o último "
            "preço conhecido, conforme a política de contingência da seção 5.8."
        )


def _patrimonio(snap: consolidacao.Snapshot, base: str) -> None:
    por_liquidez = snap.por("liquidez")
    total = snap.patrimonio or 1.0
    liquido = por_liquidez.get("Líquido", 0.0)
    iliquido = por_liquidez.get("Ilíquido", 0.0)

    esq, dir_ = st.columns([1.6, 1])
    with esq:
        st.markdown(
            f'<div class="patrimonio-rotulo">Patrimônio consolidado em '
            f'{snap.data:%d/%m/%Y}</div>'
            f'<div class="patrimonio"><span class="patrimonio-moeda">'
            f'{comum.SIMBOLO.get(base, base)}</span>'
            f'{comum.numero(snap.patrimonio)}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="barra">'
            f'<span style="width:{liquido / total * 100:.2f}%;background:{estilo.TINTA};"></span>'
            f'<span style="width:{iliquido / total * 100:.2f}%;background:{estilo.LATAO};"></span>'
            f'</div>'
            f'<div class="barra-legenda">'
            f'<div>Líquido <b>{comum.percentual(liquido / total, 1)}</b></div>'
            f'<div>Ilíquido <b>{comum.percentual(iliquido / total, 1)}</b></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with dir_:
        resultado = snap.resultado
        cor = "positivo" if resultado >= 0 else "negativo"
        retorno = resultado / snap.custo if snap.custo else 0.0
        st.markdown(
            comum.ficha("Custo de aquisição", comum.moeda(snap.custo, base)),
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="ficha"><div class="ficha-rotulo">Resultado sobre o custo</div>'
            f'<div class="ficha-valor {cor}">{comum.moeda(resultado, base)}</div>'
            f'<div class="ficha-rotulo">{comum.percentual(retorno, 1, sinal=True)} '
            f'· custo pelo câmbio da aquisição</div></div>',
            unsafe_allow_html=True,
        )


def _variacoes(variacoes, inicio_carteira, base: str, desde) -> None:
    """RF-019 — variação no dia, no mês, no ano e desde o início."""
    colunas = st.columns(4)
    for coluna, v in zip(colunas, variacoes):
        with coluna:
            st.markdown(comum.ficha(
                f"Variação {v.rotulo.lower()}",
                comum.colorido(v.retorno, comum.percentual(v.retorno, 2, sinal=True)),
                f"{comum.moeda(v.variacao_absoluta, base)}"
                + (f" · aportes {comum.moeda(v.fluxo_liquido, base)}"
                   if abs(v.fluxo_liquido) > 0.5 else ""),
            ), unsafe_allow_html=True)
    twr_desde = desde.twr if desde else None
    with colunas[3]:
        st.markdown(comum.ficha(
            "Desde o início",
            comum.colorido(twr_desde, comum.percentual(twr_desde, 2, sinal=True)),
            f"TWR desde {inicio_carteira:%d/%m/%Y}" if inicio_carteira else "",
        ), unsafe_allow_html=True)


def _indicadores(snap, rent, base: str) -> None:
    if rent is None:
        st.info("Histórico insuficiente para apurar rentabilidade no período.")
        return

    colunas = st.columns(6)
    itens = [
        ("TWR no período", comum.percentual(rent.twr), "Expurga aportes e resgates"),
        ("TWR anualizado", comum.percentual(rent.twr_anualizado), "Equivalente a.a."),
        ("MWR (IRR)", comum.percentual(rent.mwr), "Retorno do seu dinheiro"),
        ("Volatilidade", comum.percentual(rent.volatilidade), "Anualizada, sobre a cota"),
        ("Índice de Sharpe", comum.numero(rent.sharpe) if rent.sharpe is not None else "n/d",
         "Excesso sobre o CDI por unidade de risco"),
        ("Máx. drawdown", comum.percentual(rent.max_drawdown), "Maior queda pico-a-vale"),
    ]
    for coluna, (rotulo, valor, extra) in zip(colunas, itens):
        with coluna:
            st.markdown(comum.ficha(rotulo, valor, extra), unsafe_allow_html=True)

    comum.nota(
        f"Período de {rent.inicio:%d/%m/%Y} a {rent.fim:%d/%m/%Y}. Patrimônio inicial de "
        f"{comum.moeda(rent.patrimonio_inicial, base)}, aportes de "
        f"{comum.moeda(rent.aportes, base)} e resgates de "
        f"{comum.moeda(rent.resgates, base)}; retorno simples de "
        f"{comum.percentual(rent.retorno_total)}. O TWR responde “como performaram os "
        f"ativos”; o MWR (anualizado) responde “quanto o meu dinheiro rendeu”, e por "
        f"isso os dois divergem quando há aportes concentrados no tempo."
    )


def _alocacao(snap, base: str) -> None:
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### Por classe de ativo")
        visao = st.radio("Visão", ["Agregada", "Detalhada"], horizontal=True,
                         label_visibility="collapsed", key="visao_classe")
        _rosca(snap.por("macro_classe" if visao == "Agregada" else "classe_rotulo"))
    with col2:
        st.markdown("### Brasil × Offshore")
        _rosca(snap.por("regiao"))

    col3, col4 = st.columns(2)
    with col3:
        st.markdown("### Por país de exposição")
        _barras(snap.por("pais"))
    with col4:
        st.markdown("### Setorial (parcela em ações)")
        setores = {k: v for k, v in snap.por("setor").items() if k}
        if setores:
            _barras(setores)
        else:
            st.info("Sem ações na carteira.")

    st.markdown("### Por moeda de origem")
    dados = snap.por("moeda")
    total = sum(dados.values()) or 1.0
    tabela = pd.DataFrame([
        {"Moeda": m, "Valor": comum.moeda(v, base), "Participação": comum.percentual(v / total, 1)}
        for m, v in dados.items()
    ])
    st.dataframe(tabela, hide_index=True, use_container_width=True)


def _barras(dados: dict[str, float]) -> None:
    total = sum(dados.values()) or 1.0
    itens = sorted(dados.items(), key=lambda kv: kv[1])
    figura = go.Figure(go.Bar(
        y=[k for k, _ in itens], x=[v / total * 100 for _, v in itens], orientation="h",
        marker_color=estilo.TINTA, text=[comum.percentual(v / total, 1) for _, v in itens],
        textposition="outside", cliponaxis=False,
        customdata=[v for _, v in itens],
        hovertemplate="%{y}<br>%{customdata:,.2f}<extra></extra>",
    ))
    figura.update_layout(**estilo.layout_grafico(
        height=max(180, 44 * len(itens) + 60), margin=dict(t=10, b=10, l=10, r=50),
        xaxis=dict(visible=False, range=[0, 115]), yaxis=dict(showgrid=False),
    ))
    st.plotly_chart(figura, use_container_width=True)


def _rosca(dados: dict[str, float]) -> None:
    if not dados:
        st.info("Sem dados.")
        return
    figura = go.Figure(go.Pie(
        labels=list(dados), values=list(dados.values()), hole=0.62, sort=False,
        marker=dict(colors=estilo.SEQUENCIA, line=dict(color="#FFFFFF", width=1.5)),
        textinfo="percent", texttemplate="%{percent:.1%}",
        hovertemplate="%{label}<br>%{value:,.2f}<extra></extra>",
    ))
    figura.update_layout(**estilo.layout_grafico(
        height=310, margin=dict(t=10, b=10, l=10, r=10),
        legend=dict(orientation="h", y=-0.12, x=0.5, xanchor="center"),
    ))
    st.plotly_chart(figura, use_container_width=True)


def _evolucao(rent, base: str) -> None:
    if rent is None or len(rent.serie) < 2:
        st.info("Histórico insuficiente para o gráfico de evolução.")
        return

    datas = [d for d, _ in rent.serie]
    valores = [v for _, v in rent.serie]

    figura = go.Figure()
    figura.add_trace(go.Scatter(
        x=datas, y=valores, mode="lines", name="Patrimônio",
        line=dict(color=estilo.TINTA, width=2),
        fill="tozeroy", fillcolor="rgba(23,37,63,0.07)",
        hovertemplate="%{x|%b/%Y}<br>%{y:,.2f}<extra></extra>",
    ))
    figura.update_layout(**estilo.layout_grafico(
        height=380, margin=dict(t=20, b=30, l=10, r=10),
        xaxis=dict(showgrid=False, linecolor=estilo.LINHA),
        yaxis=dict(gridcolor=estilo.LINHA, zeroline=False, title=base),
        showlegend=False,
    ))
    st.plotly_chart(figura, use_container_width=True)
    comum.nota(
        "A série é apurada em pontos mensais: cada posição é remarcada pelo último "
        "preço, NAV ou avaliação conhecidos até a data, e convertida pela taxa de "
        "câmbio daquela data."
    )


def _benchmarks(rent, base_moeda: str, versao: tuple) -> None:
    if rent is None or not rent.comparativos:
        st.info("Sem benchmarks disponíveis para o período.")
        return

    st.markdown("### Evolução comparada (base 100)")
    _linha_benchmarks(rent, base_moeda, versao)
    st.markdown("### Retorno acumulado no período")

    rotulos = ["Carteira (TWR)"] + [c.codigo for c in rent.comparativos]
    valores = [rent.twr] + [c.retorno for c in rent.comparativos]
    cores = [estilo.LATAO] + [estilo.TINTA] * len(rent.comparativos)

    figura = go.Figure(go.Bar(
        x=rotulos, y=[v * 100 for v in valores], marker_color=cores,
        text=[comum.percentual(v, 1) for v in valores], textposition="outside",
        hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>",
    ))
    figura.update_layout(**estilo.layout_grafico(
        height=360, margin=dict(t=30, b=30, l=10, r=10),
        xaxis=dict(showgrid=False, linecolor=estilo.LINHA),
        yaxis=dict(gridcolor=estilo.LINHA, zeroline=True, zerolinecolor=estilo.LINHA,
                   title="Retorno no período (%)"),
    ))
    st.plotly_chart(figura, use_container_width=True)

    tabela = pd.DataFrame([
        {
            "Benchmark": c.nome,
            "Código": c.codigo,
            "Moeda": c.moeda,
            "Retorno do índice": comum.percentual(c.retorno),
            "Diferença": comum.percentual(c.diferenca, sinal=True),
            "Situação": "Acima" if c.superou else "Abaixo",
        }
        for c in rent.comparativos
    ])
    st.dataframe(tabela, hide_index=True, use_container_width=True)
    comum.nota("Índices cotados em outra moeda (o S&P 500, em dólar) são convertidos "
               "para a moeda base pela variação cambial do período, para que a "
               "comparação com a carteira seja na mesma moeda.")


def _linha_benchmarks(rent, base_moeda: str, versao: tuple) -> None:
    """Carteira (cota TWR) e benchmarks na moeda base, rebaseados a 100 na data inicial."""
    figura = go.Figure()
    figura.add_trace(go.Scatter(
        x=[d for d, _ in rent.cota], y=[v for _, v in rent.cota], name="Carteira (TWR)",
        mode="lines", line=dict(color=estilo.LATAO, width=3),
        hovertemplate="%{x|%d/%m/%Y}<br>%{y:.1f}<extra>Carteira</extra>",
    ))
    cores = iter(estilo.SEQUENCIA)
    for c in rent.comparativos:
        serie = _serie_benchmark(c.codigo, rent.fim, base_moeda, versao)
        base = next((v for d, v in reversed(serie) if d <= rent.inicio), None) \
            or (serie[0][1] if serie else None)
        pontos = [(d, v / base * 100) for d, v in serie if d >= rent.inicio] if base else []
        if len(pontos) < 2:
            continue
        figura.add_trace(go.Scatter(
            x=[d for d, _ in pontos], y=[v for _, v in pontos], name=c.codigo, mode="lines",
            line=dict(color=next(cores, estilo.GRAFITE), width=1.4),
            hovertemplate="%{x|%d/%m/%Y}<br>%{y:.1f}<extra>" + c.codigo + "</extra>",
        ))
    figura.update_layout(**estilo.layout_grafico(
        height=380, margin=dict(t=10, b=30, l=10, r=10),
        xaxis=dict(showgrid=False, linecolor=estilo.LINHA),
        yaxis=dict(gridcolor=estilo.LINHA, title="Índice (base 100)"),
        legend=dict(orientation="h", y=-0.15, x=0.5, xanchor="center"),
    ))
    st.plotly_chart(figura, use_container_width=True)


def _posicoes(snap, base: str) -> None:
    tabela = pd.DataFrame([
        {
            "Ativo": l.nome,
            "Código": l.ticker,
            "Classe": l.classe_rotulo,
            "Liquidez": l.liquidez,
            "Moeda": l.moeda,
            "Quantidade": formatacao.quantidade(l.quantidade),
            "Preço/NAV": comum.numero(l.preco_unitario, 2),
            f"Valor ({base})": comum.numero(l.valor_base),
            "Resultado": comum.numero(l.resultado),
            "Retorno": comum.percentual(l.retorno, 1, sinal=True),
            "Peso": comum.percentual(
                l.valor_base / snap.patrimonio if snap.patrimonio else 0, 1
            ),
        }
        for l in snap.linhas
    ])
    st.dataframe(tabela, hide_index=True, use_container_width=True, height=460)


def _twr_ativos(carteira_id: str, inicio, base: str, versao: tuple) -> None:
    """RF-015 — TWR de cada ativo no período escolhido (desde a aquisição, se posterior)."""
    st.markdown("### Rentabilidade por ativo (TWR)")
    if not st.toggle("Calcular o TWR de cada ativo no período", key="twr_por_ativo"):
        return
    linhas = _rentabilidade_por_ativo(carteira_id, inicio, base, versao)
    if not linhas:
        st.info("Sem histórico suficiente no período.")
        return
    st.dataframe(pd.DataFrame([
        {
            "Ativo": r.nome,
            "Código": r.ticker,
            "Classe": r.classe_rotulo,
            "Desde": r.inicio.strftime("%d/%m/%Y"),
            "TWR no período": comum.percentual(r.twr, 2, sinal=True),
            "TWR a.a.": comum.percentual(r.twr_anualizado, 2, sinal=True),
        }
        for r in linhas
    ]), hide_index=True, use_container_width=True)
    comum.nota(f"Em {base}, pela mesma regra da carteira (Dietz modificado em subperíodos "
               "mensais, encadeado). Inclui a variação cambial dos ativos em outra moeda.")
