"""Importação via template padronizado (RF-004 a RF-006; UC-04)."""
from __future__ import annotations

import uuid

import pandas as pd
import streamlit as st

import config
from src import ingest
from src.schema import TEMPLATES
from src.templates_xlsx import gerar_template
from ui import comum, estilo

ROTULOS = {
    "LIQUIDOS": "Ativos líquidos",
    "PEVC": "Private Equity / Venture Capital",
    "IMOVEIS": "Imóveis diretos",
}


def render() -> None:
    carteira = comum.exigir_carteira()
    sessao = comum.sessao()

    comum.cabecalho(
        "Importar carteira",
        "A ingestão é feita por templates XLSX padronizados, um por classe de ativo.",
    )

    st.markdown("## 1 · Baixe o template")
    colunas = st.columns(3)
    for coluna, chave in zip(colunas, TEMPLATES):
        tpl = TEMPLATES[chave]
        with coluna:
            st.markdown(
                f'<div class="ficha"><div class="ficha-rotulo">{ROTULOS[chave]}</div>'
                f'<div style="color:{estilo.GRAFITE};font-size:0.82rem;'
                f'margin-top:0.35rem;min-height:4.2rem;">{tpl.descricao}</div></div>',
                unsafe_allow_html=True,
            )
            caminho = config.TEMPLATE_DIR / tpl.arquivo
            if not caminho.exists():
                gerar_template(chave, caminho)
            st.download_button(
                "Baixar modelo", data=caminho.read_bytes(), file_name=tpl.arquivo,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, key=f"dl_{chave}",
            )

    st.markdown("## 2 · Envie o arquivo preenchido")
    esquerda, direita = st.columns([1, 2])
    with esquerda:
        chave = st.selectbox("Tipo de template", list(TEMPLATES),
                             format_func=lambda c: ROTULOS[c])
    with direita:
        arquivo = st.file_uploader("Arquivo XLSX preenchido", type=["xlsx"],
                                   key=comum.chave_campo("importacao", "arquivo"))

    if arquivo is not None:
        if st.button("Validar arquivo", type="primary"):
            _validar(sessao, arquivo, chave, carteira.id)

    preview = st.session_state.get("preview")
    if preview is not None and st.session_state.get("preview_carteira") != carteira.id:
        st.session_state.pop("preview", None)
        st.info("A carteira selecionada mudou; valide o arquivo novamente.")
        preview = None
    if preview is not None:
        st.divider()
        st.markdown("## 3 · Confira o resultado da validação")
        _mostrar_preview(sessao, preview, carteira.id)


def _validar(sessao, arquivo, chave: str, carteira_id: str) -> None:
    destino = config.UPLOAD_DIR / f"{uuid.uuid4().hex}.xlsx"
    destino.write_bytes(arquivo.getbuffer())
    try:
        with st.spinner("Validando estrutura e conteúdo…"):
            preview = ingest.validar_arquivo(destino, chave, sessao, carteira_id)
    finally:
        destino.unlink(missing_ok=True)
    preview.nome_arquivo = arquivo.name

    st.session_state.preview = preview
    st.session_state.preview_chave = chave
    st.session_state.preview_carteira = carteira_id
    st.rerun()


def _mostrar_preview(sessao, prev, carteira_id: str) -> None:
    if prev.abortado:
        st.error(
            "**Importação abortada — erro estrutural.** O arquivo não corresponde ao "
            "template esperado. Segundo a RN-003, um erro de estrutura invalida o "
            "arquivo inteiro, pois não há como interpretar as colunas com segurança."
        )
        for erro in prev.erros_estruturais:
            st.markdown(f"- {erro}")
        st.info("Baixe o modelo novamente e refaça o preenchimento sem alterar o cabeçalho.")
        if st.button("Descartar"):
            _limpar()
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(comum.ficha("Linhas lidas", str(prev.total_lido)),
                    unsafe_allow_html=True)
    with col2:
        st.markdown(comum.ficha("Válidas", str(prev.total_valido)),
                    unsafe_allow_html=True)
    with col3:
        st.markdown(comum.ficha("Rejeitadas", str(prev.total_rejeitado)),
                    unsafe_allow_html=True)

    if prev.erros_semanticos:
        st.warning(
            f"{len(prev.erros_semanticos)} problema(s) de conteúdo. As linhas afetadas "
            f"ficam de fora; as demais podem ser importadas normalmente (RN-003)."
        )
        with st.expander("Ver problemas encontrados", expanded=True):
            tabela = pd.DataFrame([
                {"Aba": o.aba, "Linha": o.linha, "Coluna": o.coluna or "—",
                 "Problema": o.mensagem}
                for o in prev.erros_semanticos
            ])
            st.dataframe(tabela, hide_index=True, use_container_width=True)

    if prev.avisos:
        with st.expander(f"{len(prev.avisos)} ajuste(s) automático(s) aplicado(s)"):
            for aviso in prev.avisos:
                st.markdown(f"- {aviso}")

    for aba, registros in prev.validos.items():
        if not registros:
            continue
        st.markdown(f"### Prévia — aba “{aba}” ({len(registros)} linhas)")
        limpos = [
            {k: v for k, v in registro.items() if not k.startswith("_")}
            for registro in registros
        ]
        st.dataframe(pd.DataFrame(limpos), hide_index=True, use_container_width=True)

    st.divider()
    esquerda, direita = st.columns([1, 3])
    with esquerda:
        if st.button("Descartar", use_container_width=True):
            _limpar()
    with direita:
        rotulo = f"Confirmar importação de {prev.total_valido} linha(s)"
        if st.button(rotulo, type="primary", disabled=not prev.importavel,
                     use_container_width=True):
            _confirmar(sessao, prev, carteira_id)


def _confirmar(sessao, prev, carteira_id: str) -> None:
    usuario = comum.usuario_atual()
    try:
        log = ingest.importar(sessao, prev, carteira_id, usuario.id if usuario else "")
    except ValueError as erro:
        comum.falha(erro)
        return
    _esquecer_preview()
    comum.sucesso(
        f"Importação concluída: {log.linhas_importadas} linha(s) gravada(s) de "
        f"{log.linhas_lidas} lida(s) no arquivo {prev.nome_arquivo}.",
        limpar="importacao",
    )


def _esquecer_preview() -> None:
    for chave in ("preview", "preview_chave", "preview_carteira"):
        st.session_state.pop(chave, None)


def _limpar() -> None:
    _esquecer_preview()
    st.rerun()
