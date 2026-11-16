"""Relatórios e exportação (RF-023; UC-16)."""
from __future__ import annotations

from datetime import date

import streamlit as st

from src import relatorio_pdf, relatorio_xlsx
from src.calc import consolidacao
from src.db import SessionLocal
from src.models import Cliente
from ui import comum


def render() -> None:
    carteira = comum.exigir_carteira()
    sessao = comum.sessao()
    base = comum.moeda_base()

    comum.cabecalho("Relatórios",
                    "Exportação da carteira consolidada em PDF, Excel ou CSV.")

    snap = consolidacao.snapshot(sessao, carteira.id, moeda_base=base)
    if not snap.linhas:
        st.info("Não há posições para exportar nesta carteira.")
        return

    st.markdown("## Prévia do relatório")
    st.markdown(f"**Carteira:** {carteira.nome} · **Moeda base:** {base} · "
                f"**Data de referência:** {snap.data:%d/%m/%Y}")

    posicoes = relatorio_xlsx.df_posicoes(snap, base)
    st.dataframe(posicoes, hide_index=True, use_container_width=True, height=320)

    carteira_id, carteira_nome, cliente_id = carteira.id, carteira.nome, carteira.cliente_id

    def _gerar(formato: str) -> bytes:
        """
        O Streamlit só chama esta função depois do clique, numa execução
        posterior do script, quando a sessão de banco desta execução já foi
        fechada (`comum.nova_sessao`). Por isso o relatório abre a própria
        sessão e recalcula tudo nela, em vez de reaproveitar objetos lidos
        pela tela.
        """
        with SessionLocal() as propria:
            foto = consolidacao.snapshot(propria, carteira_id, moeda_base=base)
            insumos = (
                consolidacao.rentabilidade(propria, carteira_id, moeda_base=base),
                consolidacao.metricas_pe(propria, carteira_id),
                consolidacao.resumo_imoveis(propria, carteira_id),
            )
            if formato == "pdf":
                cliente = propria.get(Cliente, cliente_id)
                return relatorio_pdf.gerar(carteira_nome, base, foto, *insumos,
                                           titular=cliente.nome if cliente else None)
            return relatorio_xlsx.gerar(carteira_nome, base, foto, *insumos)

    st.markdown("## Exportar")
    col0, col1, col2 = st.columns(3)
    with col0:
        st.download_button(
            "Baixar relatório (.pdf)", data=lambda: _gerar("pdf"),
            file_name=f"relatorio_carteira_{date.today():%Y%m%d}.pdf",
            mime="application/pdf", type="primary", use_container_width=True,
        )
    with col1:
        st.download_button(
            "Baixar Excel (.xlsx)", data=lambda: _gerar("xlsx"),
            file_name=f"relatorio_carteira_{date.today():%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with col2:
        st.download_button(
            "Baixar posições (.csv)",
            data=posicoes.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
            file_name=f"posicoes_{date.today():%Y%m%d}.csv",
            mime="text/csv", use_container_width=True,
        )

    comum.nota(
        "O PDF reúne resumo, rentabilidade e risco, benchmarks, alocações, posições, "
        "fundos ilíquidos e imóveis, pronto para impressão. A planilha traz as mesmas "
        "informações em abas editáveis. Os valores estão convertidos para a moeda "
        "base, exceto onde a coluna “Moeda” indica a moeda de origem do ativo; o custo "
        "usa o câmbio da data de cada aquisição."
    )
