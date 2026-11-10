"""
Lançamentos manuais — alternativa ao template (RF-007, RF-009; UC-05, UC-06, UC-08).

Usa as mesmas regras e a mesma persistência da importação por planilha
(`src.operacoes`), de modo que ativos lançados aqui e ativos importados se
consolidam na mesma posição.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import config
from src import operacoes
from src.models import Cliente
from ui import comum

ROTULO_CLASSE = {
    "ACAO": "Ação", "ETF": "ETF", "FUNDO": "Fundo de investimento",
    "TESOURO_DIRETO": "Tesouro Direto", "FII": "FII", "RENDA_FIXA": "Renda fixa",
}
ROTULO_TIPO = {"COMPRA": "Compra", "VENDA": "Venda", "PROVENTO": "Provento"}


def render() -> None:
    carteira = comum.exigir_carteira()
    sessao = comum.sessao()
    comum.cabecalho(
        "Lançamentos manuais",
        "Cadastro de ativos e fundos e registro de transações sem planilha.",
    )

    abas = st.tabs(["Registrar transação", "Cadastrar ativo líquido",
                    "Cadastrar fundo ilíquido", "Nova carteira"])
    with abas[0]:
        _transacao(sessao, carteira)
    with abas[1]:
        _ativo(sessao, carteira)
    with abas[2]:
        _fundo(sessao, carteira)
    with abas[3]:
        _carteira(sessao, carteira)


def _transacao(sessao, carteira) -> None:
    posicoes = operacoes.posicoes_liquidas(sessao, carteira.id)
    if not posicoes:
        st.info("Nenhuma posição líquida nesta carteira. Cadastre um ativo primeiro.")
        return

    rotulos = {p.id: f"{a.ticker_canonico} · {a.nome} ({comum.numero(p.quantidade, 4)} em carteira)"
               for p, a in posicoes}
    pos_id = st.selectbox("Posição", list(rotulos), format_func=rotulos.get)
    pos, ativo = next((p, a) for p, a in posicoes if p.id == pos_id)
    tipo = st.radio("Tipo", list(ROTULO_TIPO), horizontal=True, format_func=ROTULO_TIPO.get)

    with st.form("transacao"):
        col1, col2, col3 = st.columns(3)
        with col1:
            data_t = st.date_input("Data", value=config.hoje(), max_value=config.hoje(),
                                   format="DD/MM/YYYY")
        if tipo == "PROVENTO":
            with col2:
                valor = st.number_input(f"Valor total recebido ({ativo.moeda})",
                                        min_value=0.0, step=10.0, format="%.2f")
            quantidade, preco, taxas = 0.0, valor, 0.0
        else:
            with col2:
                quantidade = st.number_input("Quantidade", min_value=0.0, step=1.0,
                                             format="%.6f")
                taxas = st.number_input(f"Taxas ({ativo.moeda})", min_value=0.0,
                                        step=1.0, format="%.2f")
            with col3:
                preco = st.number_input(f"Preço unitário ({ativo.moeda})", min_value=0.0,
                                        step=0.01, format="%.4f")
        enviar = st.form_submit_button("Registrar", type="primary")

    if enviar:
        try:
            operacoes.registrar_transacao(
                sessao, comum.usuario_atual(), pos.id, tipo=tipo, data_transacao=data_t,
                quantidade=quantidade, preco=preco, taxas=taxas)
        except ValueError as erro:
            comum.falha(erro)
        else:
            st.success(f"{ROTULO_TIPO[tipo]} registrada. Posição e preço médio atualizados.")
            st.rerun()

    historico = operacoes.transacoes(sessao, pos.id)
    if historico:
        st.markdown("#### Histórico da posição")
        st.dataframe(pd.DataFrame([{
            "Data": t.data.strftime("%d/%m/%Y"), "Tipo": ROTULO_TIPO.get(t.tipo, t.tipo),
            "Quantidade": comum.numero(t.quantidade, 4) if t.tipo != "PROVENTO" else "—",
            "Preço / valor": comum.moeda(t.preco, ativo.moeda, 4 if t.tipo != "PROVENTO" else 2),
            "Taxas": comum.moeda(t.taxas, ativo.moeda),
        } for t in historico]), hide_index=True, use_container_width=True)


def _ativo(sessao, carteira) -> None:
    st.markdown("Mesmas regras do template de ativos líquidos. Se o ativo já existir "
                "na carteira, a compra é consolidada pelo preço médio ponderado.")
    with st.form("ativo_manual"):
        col1, col2, col3 = st.columns(3)
        with col1:
            ticker = st.text_input("Ticker ou CNPJ", placeholder="PETR4, AAPL, 12345678000190")
            classe = st.selectbox("Classe", config.CLASSES_LIQUIDAS, format_func=ROTULO_CLASSE.get)
            quantidade = st.number_input("Quantidade", min_value=0.0, step=1.0, format="%.6f")
        with col2:
            nome = st.text_input("Nome (opcional)")
            mercado = st.selectbox("Mercado", config.MERCADOS)
            preco = st.number_input("Preço médio", min_value=0.0, step=0.01, format="%.4f")
        with col3:
            setor = st.text_input("Setor (opcional)", placeholder="Financeiro, Energia…")
            moeda = st.selectbox("Moeda", config.MOEDAS,
                                 help="Deve ser coerente com o mercado (B3 = BRL; NYSE/NASDAQ = USD).")
            data_aq = st.date_input("Data de aquisição", value=config.hoje(),
                                    max_value=config.hoje(), format="DD/MM/YYYY")
        observacao = st.text_input("Observação (opcional)")
        enviar = st.form_submit_button("Cadastrar ativo", type="primary")
    if not enviar:
        return
    try:
        operacoes.cadastrar_ativo_liquido(
            sessao, comum.usuario_atual(), carteira.id, ticker=ticker, classe=classe,
            mercado=mercado, moeda=moeda, quantidade=quantidade, preco_medio=preco,
            data_aquisicao=data_aq, nome=nome, setor=setor, observacao=observacao)
    except ValueError as erro:
        comum.falha(erro)
        return
    st.success("Ativo cadastrado. O preço de mercado é buscado quando a fonte está disponível.")


def _fundo(sessao, carteira) -> None:
    st.markdown("Depois do cadastro, registre capital calls, distribuições e NAV na tela "
                "“Fundos PE/VC”.")
    with st.form("fundo_manual"):
        col1, col2 = st.columns(2)
        with col1:
            ident = st.text_input("Identificador do fundo", placeholder="FUND-PE-004")
            nome = st.text_input("Nome do fundo")
            gestor = st.text_input("Gestor")
            sub = st.selectbox("Subclasse", config.SUBCLASSES_ILIQUIDAS,
                               format_func=lambda s: s.replace("_", " ").title())
        with col2:
            moeda = st.selectbox("Moeda", config.MOEDAS, key="moeda_fundo")
            commitment = st.number_input("Commitment", min_value=0.0, step=10000.0, format="%.2f")
            inicio = st.date_input("Data de início", value=config.hoje(), max_value=config.hoje(),
                                   format="DD/MM/YYYY")
            informar_fim = st.checkbox("Informar encerramento previsto")
            fim = st.date_input("Encerramento previsto", value=date(config.hoje().year + 10, 1, 1),
                                format="DD/MM/YYYY")
        enviar = st.form_submit_button("Cadastrar fundo", type="primary")
    if not enviar:
        return
    try:
        operacoes.cadastrar_fundo(
            sessao, comum.usuario_atual(), carteira.id, identificador=ident, nome=nome,
            gestor=gestor, sub_classe=sub, moeda=moeda, commitment=commitment,
            data_inicio=inicio, data_fim_prevista=fim if informar_fim else None)
    except ValueError as erro:
        comum.falha(erro)
        return
    st.success("Fundo cadastrado com dry powder igual ao commitment.")


def _carteira(sessao, carteira) -> None:
    cliente = sessao.get(Cliente, carteira.cliente_id)
    st.markdown(f"Um cliente pode manter várias carteiras (por exemplo, por objetivo ou "
                f"por custodiante). A nova carteira pertencerá a **{cliente.nome}**.")
    with st.form("nova_carteira"):
        nome = st.text_input("Nome da carteira", placeholder="Previdência, Offshore…")
        enviar = st.form_submit_button("Criar carteira", type="primary")
    if not enviar:
        return
    try:
        nova = operacoes.criar_carteira(sessao, comum.usuario_atual(), cliente.id, nome)
    except ValueError as erro:
        comum.falha(erro)
        return
    st.session_state.carteira_id = nova.id
    st.success("Carteira criada e selecionada.")
    st.rerun()
