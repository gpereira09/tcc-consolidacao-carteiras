"""Fundos ilíquidos PE/VC (RF-010, RF-011, RF-012, RF-017; UC-09, UC-10, UC-11, UC-15)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from src import operacoes
from src.calc import consolidacao, pe
from ui import comum, estilo


ROTULO_EVENTO = {"CAPITAL_CALL": "Capital call", "DISTRIBUICAO": "Distribuição",
                 "NAV_UPDATE": "NAV update"}


def render() -> None:
    carteira = comum.exigir_carteira()
    sessao = comum.sessao()

    comum.cabecalho(
        "Fundos ilíquidos",
        "Private Equity, Venture Capital e Real Estate: chamadas de capital, "
        "distribuições e marcação por NAV.",
    )

    fundos = consolidacao.fundos_da_carteira(sessao, carteira.id)

    if not fundos:
        st.info(
            "Nenhum fundo ilíquido nesta carteira. Importe o template de "
            "Private Equity / Venture Capital na tela “Importar carteira” ou "
            "cadastre o fundo em “Lançamentos manuais”."
        )
        return

    metricas = {f.id: pe.calcular(f, sessao) for f in fundos}
    _quadro_geral(list(metricas.values()))
    st.divider()

    rotulos = {f.id: f"{f.ativo.nome} · {f.gestor}" for f in fundos}
    escolhido = st.selectbox("Fundo", list(rotulos), format_func=lambda i: rotulos[i])
    fundo = next(f for f in fundos if f.id == escolhido)
    _detalhe(sessao, fundo, metricas[escolhido])


def _quadro_geral(metricas) -> None:
    st.markdown("## Quadro geral")

    tabela = pd.DataFrame([
        {
            "Fundo": m.nome,
            "Gestor": m.gestor,
            "Classe": m.sub_classe.replace("_", " ").title(),
            "Moeda": m.moeda,
            "Commitment": comum.numero(m.commitment),
            "Paid-in": comum.numero(m.paid_in),
            "Dry powder": comum.numero(m.dry_powder),
            "Distribuído": comum.numero(m.distribuicoes),
            "NAV": comum.numero(m.nav),
            "DPI": comum.numero(m.dpi),
            "TVPI": comum.numero(m.tvpi),
            "IRR": comum.percentual(m.irr, 1),
        }
        for m in metricas
    ])
    st.dataframe(tabela, hide_index=True, use_container_width=True)
    comum.nota(
        "DPI mede quanto do capital integralizado já retornou em caixa; TVPI soma a "
        "isso o valor residual (NAV). Sob a convenção adotada no trabalho, MOIC e "
        "TVPI coincidem, e por isso apenas o TVPI é exibido. Quando há chamada ou "
        "distribuição depois da última marcação do gestor, o NAV é ajustado por esses "
        "fluxos (roll-forward)."
    )


def _detalhe(sessao, fundo, m: pe.MetricasPE) -> None:
    moeda = m.moeda

    st.markdown(f"## {m.nome}")
    colunas = st.columns(4)
    itens = [
        ("Commitment", comum.moeda(m.commitment, moeda),
         f"{comum.percentual(m.percentual_chamado, 1)} chamado"),
        ("Capital integralizado", comum.moeda(m.paid_in, moeda),
         f"Pendente: {comum.moeda(m.pendente, moeda)}"),
        ("Dry powder", comum.moeda(m.dry_powder, moeda), "Ainda não chamado"),
        ("NAV atual", comum.moeda(m.nav, moeda),
         (f"Marcação de {m.data_nav:%d/%m/%Y}" + (" + fluxos posteriores"
                                                   if m.nav_ajustado else ""))
         if m.data_nav else "Sem marcação do gestor"),
    ]
    for coluna, (rotulo, valor, extra) in zip(colunas, itens):
        with coluna:
            st.markdown(comum.ficha(rotulo, valor, extra), unsafe_allow_html=True)

    anos = pe.idade_anos(fundo, config.hoje())
    prazo = ""
    if fundo.data_fim_prevista:
        prazo = f"de {comum.numero(pe.idade_anos(fundo, fundo.data_fim_prevista), 1)} previstos"
    colunas = st.columns(5)
    for coluna, (rotulo, valor, extra) in zip(colunas, [
        ("DPI", comum.numero(m.dpi), "Distribuído / integralizado"),
        ("RVPI", comum.numero(m.rvpi), "NAV / integralizado"),
        ("TVPI · MOIC", comum.numero(m.tvpi), "Valor total / integralizado"),
        ("IRR", comum.percentual(m.irr, 1),
         "Mais de uma solução: ler com o TVPI" if m.irr_ambigua
         else "Money-weighted, anualizada"),
        ("Anos do fundo", comum.numero(anos, 1), prazo or f"Início {fundo.data_inicio:%d/%m/%Y}"),
    ]):
        with coluna:
            st.markdown(comum.ficha(rotulo, valor, extra), unsafe_allow_html=True)

    _curva_j(sessao, fundo, moeda)

    eventos = pe.eventos(fundo, sessao)
    aba_evt, aba_novo = st.tabs(["Eventos registrados", "Registrar evento"])
    with aba_evt:
        _lista_eventos(eventos, moeda)
        _confirmar_pendente(sessao, fundo, eventos, moeda)
        _cancelar_evento(sessao, fundo, eventos, moeda)
    with aba_novo:
        _formulario_evento(sessao, fundo, m)


def _curva_j(sessao, fundo, moeda: str) -> None:
    pontos = pe.curva_j(fundo, sessao)
    if not pontos:
        return
    datas, acumulado = zip(*pontos)

    figura = go.Figure()
    figura.add_trace(go.Scatter(
        x=datas, y=acumulado, mode="lines+markers",
        line=dict(color=estilo.TINTA, width=2),
        marker=dict(size=6, color=estilo.TINTA),
        hovertemplate="%{x|%d/%m/%Y}<br>%{y:,.2f}<extra></extra>",
    ))
    figura.add_hline(y=0, line=dict(color=estilo.LINHA, width=1))
    figura.update_layout(**estilo.layout_grafico(
        height=280, margin=dict(t=30, b=20, l=10, r=10),
        title=dict(text="Fluxo acumulado do investidor (curva J)",
                   font=dict(family="Source Serif 4, Georgia, serif",
                             color=estilo.TINTA, size=15)),
        xaxis=dict(showgrid=False, linecolor=estilo.LINHA),
        yaxis=dict(gridcolor=estilo.LINHA, zeroline=False, title=moeda),
        showlegend=False,
    ))
    st.plotly_chart(figura, use_container_width=True)


def _lista_eventos(eventos, moeda: str) -> None:
    if not eventos:
        st.info("Nenhum evento registrado.")
        return

    tabela = pd.DataFrame([
        {
            "Data": e.data_evento.strftime("%d/%m/%Y"),
            "Tipo": ROTULO_EVENTO.get(e.tipo_evento, e.tipo_evento)
            + (f" · {comum.ROTULO_DISTRIBUICAO.get(e.subtipo, e.subtipo)}" if e.subtipo else ""),
            "Valor": comum.moeda(e.valor, moeda),
            "Status": e.status.title(),
            "NAV/cota": comum.numero(e.nav_unidade, 4) if e.nav_unidade else "—",
            "Observação": e.observacao or "",
        }
        for e in eventos
    ])
    st.dataframe(tabela, hide_index=True, use_container_width=True)


def _confirmar_pendente(sessao, fundo, eventos, moeda: str) -> None:
    """UC-09 FA-01 — a chamada pendente foi paga."""
    pendentes = {e.id: e for e in eventos
                 if e.tipo_evento == "CAPITAL_CALL" and e.status == "PENDENTE"}
    if not pendentes:
        return
    with st.form(f"confirmar_{fundo.id}"):
        st.markdown("**Confirmar pagamento de chamada pendente**")
        col1, col2 = st.columns(2)
        with col1:
            escolhido = st.selectbox(
                "Chamada", list(pendentes),
                format_func=lambda i: f"{pendentes[i].data_evento:%d/%m/%Y} · "
                                      f"{comum.moeda(pendentes[i].valor, moeda)}")
        with col2:
            data_pagamento = st.date_input("Data do pagamento", value=config.hoje(),
                                           max_value=config.hoje(), format="DD/MM/YYYY")
        enviar = st.form_submit_button("Confirmar pagamento")
    if not enviar:
        return
    try:
        operacoes.confirmar_capital_call(sessao, comum.usuario_atual(), escolhido,
                                         data_pagamento)
    except ValueError as erro:
        comum.falha(erro)
        return
    comum.sucesso("Chamada confirmada como paga. As métricas do fundo foram recalculadas.")


def _cancelar_evento(sessao, fundo, eventos, moeda: str) -> None:
    """Estorno de chamada ou distribuição lançada por engano."""
    cancelaveis = {e.id: e for e in eventos
                   if e.tipo_evento != "NAV_UPDATE" and e.status != "CANCELADO"}
    if not cancelaveis:
        return
    with st.expander("Cancelar um evento lançado por engano"):
        st.caption("O evento cancelado sai dos cálculos, mas continua na lista e na "
                   "trilha de auditoria. Uma marcação de NAV errada é corrigida com "
                   "uma nova marcação.")
        with st.form(f"cancelar_{fundo.id}"):
            escolhido = st.selectbox(
                "Evento", list(cancelaveis),
                format_func=lambda i: f"{cancelaveis[i].data_evento:%d/%m/%Y} · "
                                      f"{ROTULO_EVENTO[cancelaveis[i].tipo_evento]} · "
                                      f"{comum.moeda(cancelaveis[i].valor, moeda)}")
            ciente = st.checkbox("Confirmo o cancelamento deste evento.",
                                 key=comum.chave_campo(f"cancelar_{fundo.id}", "ciente"))
            enviar = st.form_submit_button("Cancelar evento")
        if not enviar:
            return
        if not ciente:
            st.error("Marque a confirmação antes de cancelar o evento.")
            return
        try:
            evento = operacoes.cancelar_evento_pe(sessao, comum.usuario_atual(), escolhido)
        except ValueError as erro:
            comum.falha(erro)
            return
        comum.sucesso(
            f"Evento cancelado: {ROTULO_EVENTO[evento.tipo_evento]} de "
            f"{comum.moeda(evento.valor, moeda)}. As métricas do fundo foram recalculadas.",
            limpar=f"cancelar_{fundo.id}")


def _formulario_evento(sessao, fundo, m) -> None:
    form = f"evento_{fundo.id}"
    tipo = st.radio("Tipo de evento", list(ROTULO_EVENTO), horizontal=True,
                    key=f"tipo_evento_{fundo.id}", format_func=ROTULO_EVENTO.get)
    with st.form(form):
        col1, col2 = st.columns(2)
        with col1:
            data_evento = st.date_input(
                "Data de referência" if tipo == "NAV_UPDATE" else "Data do evento",
                value=config.hoje(), format="DD/MM/YYYY")
            rotulo_valor = ("NAV total da participação" if tipo == "NAV_UPDATE" else "Valor")
            valor = st.number_input(f"{rotulo_valor} ({m.moeda})", min_value=0.0,
                                    step=1000.0, format="%.2f",
                                    key=comum.chave_campo(form, f"valor_{tipo}"))
        with col2:
            status, subtipo, nav_unidade, acima_do_nav = "RECEBIDO", None, None, False
            if tipo == "CAPITAL_CALL":
                status = st.selectbox("Status", ["PAGO", "PENDENTE"],
                                      help="Chamada PENDENTE reduz o dry powder e conta "
                                           "no limite do commitment (RN-004), mas não "
                                           "entra no capital integralizado.")
                st.caption(f"Disponível para chamada: {comum.moeda(m.dry_powder, m.moeda)}")
            elif tipo == "DISTRIBUICAO":
                subtipo = st.selectbox("Classificação (RF-012)",
                                       list(comum.ROTULO_DISTRIBUICAO),
                                       format_func=comum.ROTULO_DISTRIBUICAO.get)
                st.caption(f"NAV atual: {comum.moeda(m.nav, m.moeda)}")
                acima_do_nav = st.checkbox(
                    "Confirmo uma distribuição maior que o NAV atual",
                    key=comum.chave_campo(form, "acima_do_nav"),
                    help="Só é necessário quando o valor excede o NAV atual, o que "
                         "zera o valor residual do fundo.")
            else:
                nav_unidade = st.number_input(
                    "NAV por cota (opcional)", min_value=0.0, step=0.01, format="%.4f",
                    key=comum.chave_campo(form, "nav_unidade")) or None
        observacao = st.text_input("Observação", key=comum.chave_campo(form, "observacao"))
        enviar = st.form_submit_button("Registrar evento", type="primary")

    if not enviar:
        return
    try:
        operacoes.registrar_evento_pe(
            sessao, comum.usuario_atual(), fundo, tipo=tipo, data_evento=data_evento,
            valor=valor, status=status, nav_unidade=nav_unidade, subtipo=subtipo,
            observacao=observacao, confirmar_acima_do_nav=acima_do_nav)
    except ValueError as erro:
        comum.falha(erro)
        return
    comum.sucesso(
        f"Evento registrado: {ROTULO_EVENTO[tipo]} de {comum.moeda(valor, m.moeda)} em "
        f"{data_evento:%d/%m/%Y}. As métricas do fundo foram recalculadas.",
        limpar=form)
