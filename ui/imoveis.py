"""Imóveis diretos: valorização, renda e reavaliação de mercado."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
from src import operacoes
from src.calc import consolidacao
from ui import comum


def render() -> None:
    carteira = comum.exigir_carteira()
    sessao = comum.sessao()

    comum.cabecalho(
        "Imóveis diretos",
        "Imóveis detidos diretamente. Fundos imobiliários (FII) aparecem entre os "
        "ativos líquidos, por terem cotação em bolsa.",
    )

    linhas = consolidacao.resumo_imoveis(sessao, carteira.id)
    if not linhas:
        st.info(
            "Nenhum imóvel nesta carteira. Importe o template de Imóveis na tela "
            "“Importar carteira”."
        )
        return

    total = sum(l["Valor de mercado"] for l in linhas)
    renda = sum(l["Renda líq. anual"] for l in linhas)
    custo = sum(l["Aquisição"] for l in linhas)

    colunas = st.columns(3)
    with colunas[0]:
        st.markdown(comum.ficha("Valor de mercado", comum.numero(total),
                                "Soma em moeda de origem"), unsafe_allow_html=True)
    with colunas[1]:
        st.markdown(comum.ficha("Valorização", comum.percentual(
            total / custo - 1 if custo else 0, 1, sinal=True), "Sobre a aquisição"),
            unsafe_allow_html=True)
    with colunas[2]:
        st.markdown(comum.ficha("Renda líquida anual", comum.numero(renda),
                                "Aluguéis menos despesas"), unsafe_allow_html=True)

    tabela = pd.DataFrame([
        {
            "Identificador": l["Identificador"],
            "Imóvel": l["Imóvel"],
            "Tipo": l["Tipo"],
            "Moeda": l["Moeda"],
            "Aquisição": comum.numero(l["Aquisição"]),
            "Valor de mercado": comum.numero(l["Valor de mercado"]),
            "Valorização": comum.percentual(l["Valorização"], 1, sinal=True),
            "Renda líq. anual": comum.numero(l["Renda líq. anual"]),
            "Yield líq.": comum.percentual(l["Yield líq."], 2),
            "Última avaliação": l["Última avaliação"].strftime("%d/%m/%Y"),
        }
        for l in linhas
    ])
    st.dataframe(tabela, hide_index=True, use_container_width=True)
    comum.nota(
        "O yield líquido considera aluguel menos despesas recorrentes, sobre o valor "
        "de mercado. Cada reavaliação registrada é versionada no histórico do imóvel, "
        "de modo que a evolução patrimonial reflita a marcação vigente em cada data."
    )

    st.divider()
    st.markdown("## Registrar reavaliação")
    _formulario_reavaliacao(sessao, carteira.id)


def _formulario_reavaliacao(sessao, carteira_id: str) -> None:
    imoveis = consolidacao.imoveis_da_carteira(sessao, carteira_id)
    if not imoveis:
        return

    rotulos = {i.id: f"{i.identificador_imovel} · {i.ativo.nome}" for i in imoveis}

    with st.form("reavaliacao"):
        col1, col2, col3 = st.columns(3)
        with col1:
            escolhido = st.selectbox("Imóvel", list(rotulos),
                                     format_func=lambda i: rotulos[i])
        with col2:
            valor = st.number_input("Novo valor de mercado", min_value=0.0,
                                    step=10_000.0, format="%.2f")
        with col3:
            data_avaliacao = st.date_input("Data da avaliação", value=config.hoje(),
                                           format="DD/MM/YYYY")
        enviar = st.form_submit_button("Registrar reavaliação", type="primary")

    if not enviar:
        return

    try:
        operacoes.reavaliar_imovel(sessao, comum.usuario_atual(), carteira_id, escolhido,
                                   valor=valor, data_avaliacao=data_avaliacao)
    except ValueError as erro:
        comum.falha(erro)
        return

    st.success("Reavaliação registrada e histórico patrimonial atualizado.")
    st.rerun()
