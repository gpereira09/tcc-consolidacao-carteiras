"""
Sistema de Consolidação de Carteiras de Investimentos
Universidade Positivo · Engenharia de Software · 2026

Ponto de entrada da camada de apresentação. Executar com:

    streamlit run app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import os

import streamlit as st

from src import agendador, security
from src.db import criar_schema
from ui import comum
from ui import (
    admin,
    dashboard,
    fundos_pe,
    imoveis,
    importacao,
    lancamentos,
    login,
    privacidade,
    relatorios,
)

TELAS = {
    "Visão consolidada": (dashboard.render, ("ADMIN", "COLABORADOR", "CLIENTE")),
    "Importar carteira": (importacao.render, ("ADMIN", "COLABORADOR", "CLIENTE")),
    "Lançamentos manuais": (lancamentos.render, ("ADMIN", "COLABORADOR", "CLIENTE")),
    "Fundos PE/VC": (fundos_pe.render, ("ADMIN", "COLABORADOR", "CLIENTE")),
    "Imóveis": (imoveis.render, ("ADMIN", "COLABORADOR", "CLIENTE")),
    "Relatórios": (relatorios.render, ("ADMIN", "COLABORADOR", "CLIENTE")),
    "Privacidade e dados": (privacidade.render, ("ADMIN", "COLABORADOR", "CLIENTE")),
    "Administração": (admin.render, ("ADMIN",)),
}


def barra_lateral() -> str:
    usuario = comum.usuario_atual()
    sessao = comum.sessao()

    with st.sidebar:
        st.markdown("### Consolidação de Carteiras")
        st.markdown(
            f'<div class="sidebar-identidade">{usuario.email}<br>'
            f'Perfil: {usuario.perfil.title()}</div>',
            unsafe_allow_html=True,
        )

        carteiras = security.carteiras_visiveis(sessao, usuario)
        if carteiras:
            rotulos = {}
            for c in carteiras:
                nome = c.cliente.nome if c.cliente else "—"
                rotulos[c.id] = f"{nome} · {c.nome}" if usuario.perfil != "CLIENTE" else c.nome

            atual = st.session_state.get("carteira_id")
            if atual not in rotulos:
                atual = carteiras[0].id
            escolhida = st.selectbox(
                "Carteira", options=list(rotulos), index=list(rotulos).index(atual),
                format_func=lambda cid: rotulos[cid],
            )
            st.session_state.carteira_id = escolhida
        else:
            st.info("Nenhuma carteira acessível neste perfil.")

        st.divider()
        permitidas = [n for n, (_, perfis) in TELAS.items() if usuario.perfil in perfis]
        escolha = st.radio("Navegação", permitidas, label_visibility="collapsed")

        st.divider()
        if st.button("Encerrar sessão", use_container_width=True):
            comum.encerrar_sessao()
            st.rerun()

    return escolha


@st.cache_resource
def _servicos_de_fundo() -> bool:
    """Inicia uma única vez por processo: schema e agendador (RF-013, RNF-008)."""
    criar_schema()
    if os.getenv("CARTEIRA_AGENDADOR", "1") != "0":
        agendador.iniciar()
    return True


def main() -> None:
    comum.configurar_pagina("Carteiras")
    _servicos_de_fundo()
    comum.nova_sessao()

    if not comum.autenticado():
        login.render()
        return

    usuario = comum.usuario_atual()
    try:
        security.aplicar_limite(f"usuario:{usuario.id}")
    except security.ErroLimiteTaxa as erro:
        st.error(str(erro))
        st.stop()

    cliente = comum.cliente_do_usuario(usuario)
    if usuario.perfil == "CLIENTE" and cliente is not None and not cliente.consentimento_lgpd:
        login.consentimento_pendente(cliente)
        return

    escolha = barra_lateral()
    funcao, perfis = TELAS[escolha]
    if usuario.perfil not in perfis:
        st.error("Você não tem permissão para acessar esta tela.")
        return
    funcao()


if __name__ == "__main__":
    main()
