"""Utilitários compartilhados pelas telas Streamlit."""
from __future__ import annotations

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from src import formatacao, security
from src.calc import consolidacao
from src.db import SessionLocal
from src.models import Carteira, Cliente, Usuario
from ui import estilo

SIMBOLO = config.SIMBOLO_MOEDA

ROTULO_DISTRIBUICAO = {
    "RETORNO_CAPITAL": "Retorno de capital",
    "GANHO_CAPITAL": "Ganho de capital",
    "RENDIMENTO": "Rendimento",
}


def nova_sessao() -> None:
    """
    Abre uma sessão de banco nova a cada execução do script (rerun) e fecha a
    anterior. Uma sessão que durasse a visita inteira manteria em memória
    objetos desatualizados — por exemplo, um usuário desativado pelo
    administrador continuaria "ativo" até o fim da visita.
    """
    anterior = st.session_state.pop("db", None)
    if anterior is not None:
        anterior.close()
    st.session_state.db = SessionLocal()


def sessao() -> Session:
    """Sessão de banco da execução atual do script."""
    if "db" not in st.session_state:
        st.session_state.db = SessionLocal()
    return st.session_state.db


def usuario_atual() -> Usuario | None:
    uid = st.session_state.get("usuario_id")
    if not uid:
        return None
    return sessao().get(Usuario, uid)


def iniciar_sessao(usuario: Usuario, access_token: str) -> None:
    """Grava o par de tokens da sessão: access JWT curto + refresh revogável."""
    st.session_state.usuario_id = usuario.id
    st.session_state.token = access_token
    st.session_state.refresh = security.emitir_refresh(sessao(), usuario)
    carteiras = security.carteiras_visiveis(sessao(), usuario)
    if carteiras:
        st.session_state.carteira_id = carteiras[0].id


def autenticado() -> bool:
    """
    Sessão válida exige usuário em memória e access token JWT dentro da
    validade. Com o access expirado, o refresh token é trocado por um novo
    par (rotação); se também estiver inválido ou revogado, a sessão termina.
    """
    if not st.session_state.get("usuario_id"):
        return False
    token = st.session_state.get("token")
    dados = security.validar_token(token) if token else None
    if dados is not None and dados.get("sub") == st.session_state.usuario_id:
        usuario = sessao().get(Usuario, dados["sub"])
        return usuario is not None and usuario.ativo
    renovado = security.renovar_sessao(sessao(), st.session_state.get("refresh", ""))
    if renovado is None or renovado[0].id != st.session_state.usuario_id:
        encerrar_sessao(revogar=False)
        return False
    _, st.session_state.token, st.session_state.refresh = renovado
    return True


def encerrar_sessao(revogar: bool = True) -> None:
    uid = st.session_state.get("usuario_id")
    if revogar and uid:
        security.revogar_sessoes(sessao(), uid)
    for chave in ("usuario_id", "token", "refresh", "carteira_id", "preview",
                  "preview_chave", "preview_carteira", "login_2fa", "totp_novo"):
        st.session_state.pop(chave, None)


def cliente_do_usuario(usuario: Usuario) -> Cliente | None:
    return sessao().scalar(select(Cliente).where(Cliente.usuario_id == usuario.id))


def moeda_base() -> str:
    """Moeda base do dono da carteira selecionada."""
    return consolidacao.moeda_base_da_carteira(sessao(), st.session_state.get("carteira_id", ""))


def falha(erro: Exception) -> None:
    """Desfaz a transação pendente e exibe o erro de validação ao usuário."""
    sessao().rollback()
    st.error(str(erro))


def carteira_selecionada() -> Carteira | None:
    """
    Carteira escolhida na barra lateral, conferida de novo a cada uso (RN-013).

    A escolha fica guardada na sessão. Se o usuário perder o acesso enquanto
    está conectado (vínculo removido ou perfil alterado), a carteira deixa de
    ser devolvida e a escolha é descartada.
    """
    cid = st.session_state.get("carteira_id")
    usuario = usuario_atual()
    if not cid or usuario is None:
        return None
    if not security.pode_acessar_carteira(sessao(), usuario, cid):
        st.session_state.pop("carteira_id", None)
        return None
    return sessao().get(Carteira, cid)


def exigir_carteira() -> Carteira:
    carteira = carteira_selecionada()
    if carteira is None:
        st.warning("Selecione uma carteira na barra lateral.")
        st.stop()
    return carteira


moeda = formatacao.moeda
percentual = formatacao.percentual
numero = formatacao.numero


def colorido(valor: float | None, texto: str) -> str:
    if valor is None:
        return f'<span class="neutro">{texto}</span>'
    classe = "positivo" if valor > 0 else ("negativo" if valor < 0 else "neutro")
    return f'<span class="{classe}">{texto}</span>'


def ficha(rotulo: str, valor: str, extra: str = "") -> str:
    complemento = f'<div class="ficha-rotulo">{extra}</div>' if extra else ""
    return (
        f'<div class="ficha"><div class="ficha-rotulo">{rotulo}</div>'
        f'<div class="ficha-valor">{valor}</div>{complemento}</div>'
    )


def nota(texto: str) -> None:
    st.markdown(f'<div class="nota">{texto}</div>', unsafe_allow_html=True)


def aviso_dados_sinteticos() -> None:
    st.markdown(
        '<div class="selo-sintetico"><b>Ambiente de demonstração.</b> Cotações, '
        'câmbio e benchmarks desta base são gerados por simulação e não '
        'representam valores reais de mercado.</div>',
        unsafe_allow_html=True,
    )


def configurar_pagina(titulo: str) -> None:
    st.set_page_config(
        page_title=f"{titulo} · Consolidação de Carteiras",
        page_icon="◧",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    estilo.aplicar()


def cabecalho(titulo: str, subtitulo: str = "") -> None:
    st.markdown(f"# {titulo}")
    if subtitulo:
        st.markdown(
            f'<div style="color:{estilo.GRAFITE};margin-top:-0.6rem;'
            f'margin-bottom:1.2rem;">{subtitulo}</div>',
            unsafe_allow_html=True,
        )
