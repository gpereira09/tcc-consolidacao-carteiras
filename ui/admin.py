"""Administração: usuários, vínculos, auditoria e operação (RF-013, RF-024 a RF-026; UC-19, UC-20)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

import config
from src import administracao, agendador, audit, backup, lgpd, security
from src.models import Usuario
from ui import comum


def render() -> None:
    sessao = comum.sessao()
    usuario = comum.usuario_atual()
    try:
        security.exigir_perfil(usuario, "ADMIN")
    except security.ErroAutorizacao as erro:
        st.error(str(erro))
        return

    comum.cabecalho("Administração",
                    "Gestão de contas, perfis de acesso e trilha de auditoria.")

    aba_usuarios, aba_vinculos, aba_auditoria, aba_operacao = st.tabs(
        ["Usuários", "Vínculos cliente–colaborador", "Auditoria", "Operação"])
    with aba_usuarios:
        _usuarios(sessao, usuario)
    with aba_vinculos:
        _vinculos(sessao, usuario)
    with aba_auditoria:
        _auditoria(sessao)
    with aba_operacao:
        _operacao(sessao)


def _usuarios(sessao, admin) -> None:
    usuarios = administracao.listar_usuarios(sessao, admin)

    tabela = pd.DataFrame([
        {
            "E-mail": u.email,
            "Nome": _nome(u),
            "Perfil": u.perfil.title(),
            "Ativa": "Sim" if u.ativo else "Não",
            "Criada em": u.data_criacao.strftime("%d/%m/%Y"),
            "Último acesso": u.ultimo_login.strftime("%d/%m/%Y %H:%M")
            if u.ultimo_login else "—",
            "Bloqueada até": u.bloqueado_ate.strftime("%d/%m/%Y %H:%M")
            if u.bloqueado_ate else "—",
        }
        for u in usuarios
    ])
    st.dataframe(tabela, hide_index=True, use_container_width=True)

    rotulos = {u.id: f"{u.email} ({u.perfil.title()})" for u in usuarios}
    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("## Criar conta")
        with st.form("criar_usuario"):
            nome = st.text_input("Nome")
            email = st.text_input("E-mail")
            perfil = st.selectbox("Perfil", config.PERFIS, index=2, key="perfil_novo")
            senha = st.text_input("Senha inicial (vazio = gerar automaticamente)",
                                  type="password")
            criar = st.form_submit_button("Criar conta", type="primary")
        if criar:
            try:
                novo, inicial = administracao.criar_usuario(
                    sessao, admin, email=email, nome=nome, perfil=perfil, senha=senha or None)
            except ValueError as erro:
                comum.falha(erro)
            else:
                st.success(f"Conta {novo.email} criada.")
                if not senha:
                    st.info(f"Senha provisória: `{inicial}` — repasse ao usuário por canal "
                            f"seguro. Ele pode trocá-la em “Esqueci minha senha”.")
                if perfil == "CLIENTE":
                    st.caption("O consentimento LGPD será solicitado ao titular no primeiro acesso.")

    with col_b:
        st.markdown("## Alterar conta")
        # A conta fica fora do formulário para que perfil e situação partam dos
        # valores atuais dela; dentro do form, o padrão seria sempre ADMIN/Ativa e
        # um simples desbloqueio promoveria a conta a administrador.
        alvo_id = st.selectbox("Conta", list(rotulos), format_func=lambda i: rotulos[i])
        alvo = next(u for u in usuarios if u.id == alvo_id)
        with st.form(f"alterar_usuario_{alvo_id}"):
            c1, c2 = st.columns(2)
            with c1:
                perfil = st.selectbox("Perfil", config.PERFIS,
                                      index=config.PERFIS.index(alvo.perfil))
            with c2:
                ativo = st.selectbox("Situação", ["Ativa", "Desativada"],
                                     index=0 if alvo.ativo else 1)
            desbloquear = st.checkbox("Remover bloqueio por tentativas falhas (RN-001)")
            enviar = st.form_submit_button("Aplicar alterações", type="primary")
        if enviar:
            try:
                administracao.alterar_usuario(sessao, admin, alvo_id, perfil=perfil,
                                              ativo=ativo == "Ativa", desbloquear=desbloquear)
            except ValueError as erro:
                comum.falha(erro)
            else:
                comum.sucesso("Conta atualizada.")

    st.markdown("## Excluir conta")
    st.caption("Remove cadastro e patrimônio da conta e anonimiza sua trilha de auditoria "
               "(mesmo procedimento da exclusão LGPD, RN-015). Irreversível.")
    with st.form("excluir_usuario"):
        outros = {k: v for k, v in rotulos.items() if k != admin.id}
        alvo = st.selectbox("Conta a excluir", list(outros), format_func=lambda i: outros[i])
        confirmacao = st.text_input("Digite EXCLUIR para confirmar")
        excluir = st.form_submit_button("Excluir conta")
    if excluir:
        if confirmacao.strip().upper() != "EXCLUIR":
            st.error("Digite EXCLUIR para confirmar.")
        else:
            try:
                contagem = administracao.excluir_usuario(sessao, admin, alvo)
            except ValueError as erro:
                comum.falha(erro)
            else:
                comum.sucesso(f"Conta excluída: {contagem['carteiras']} carteira(s) e "
                              f"{contagem['posicoes']} posição(ões) removidas.")


def _vinculos(sessao, admin) -> None:
    """RN-013 — o colaborador enxerga apenas as carteiras dos clientes vinculados."""
    clientes, colaboradores = administracao.listar_vinculos(sessao, admin)
    nomes = {c.id: c.email for c in colaboradores}

    st.dataframe(pd.DataFrame([{
        "Cliente": c.nome, "E-mail": u.email,
        "Colaborador responsável": nomes.get(c.colaborador_id, "—"),
    } for c, u in clientes]), hide_index=True, use_container_width=True)

    if not clientes:
        st.info("Nenhum cliente cadastrado.")
        return
    opcoes_cli = {c.id: f"{c.nome} ({u.email})" for c, u in clientes}
    opcoes_col = {"": "— sem colaborador —", **nomes}
    with st.form("vincular"):
        cid = st.selectbox("Cliente", list(opcoes_cli), format_func=opcoes_cli.get)
        colab = st.selectbox("Colaborador responsável", list(opcoes_col),
                             format_func=opcoes_col.get)
        enviar = st.form_submit_button("Salvar vínculo", type="primary")
    if enviar:
        try:
            administracao.vincular_colaborador(sessao, admin, cid, colab or None)
        except ValueError as erro:
            comum.falha(erro)
        else:
            comum.sucesso("Vínculo atualizado.")


def _operacao(sessao) -> None:
    """RF-013, RNF-006, RNF-008 — tarefas agendadas e backups."""
    proximas = agendador.proximas_execucoes()
    c1, c2 = st.columns(2)
    with c1:
        quando = proximas.get("coleta_precos")
        st.markdown(comum.ficha("Próxima coleta de preços",
                                f"{quando:%d/%m/%Y %H:%M}" if quando else "agendador inativo",
                                "Diária às 19h30 (Brasília) — RNF-006"),
                    unsafe_allow_html=True)
        if st.button("Executar coleta agora", use_container_width=True):
            with st.spinner("Coletando cotações, PTAX e benchmarks…"):
                r = agendador.coletar_precos()
            st.success(f"{r.atualizados} de {r.ativos} ativos atualizados. "
                       f"Câmbio: {r.cambio}. Benchmarks: {r.benchmarks}. "
                       f"Falhas: {', '.join(r.falhas) or 'nenhuma'}.")
    with c2:
        quando = proximas.get("backup_diario")
        st.markdown(comum.ficha("Próximo backup",
                                f"{quando:%d/%m/%Y %H:%M}" if quando else "agendador inativo",
                                f"Diário; mantém as {config.BACKUP_RETENCAO_DIAS} cópias mais recentes"),
                    unsafe_allow_html=True)
        if st.button("Fazer backup agora", use_container_width=True):
            caminho = agendador.backup_diario()
            st.success(f"Backup gravado: {caminho.name}")

    copias = backup.listar()
    st.markdown("#### Cópias disponíveis")
    if copias:
        st.dataframe(pd.DataFrame([{
            "Arquivo": c.name, "Tamanho": f"{c.stat().st_size / 1024:,.0f} KB".replace(",", "."),
        } for c in copias]), hide_index=True, use_container_width=True)
        st.caption("Para restaurar: pare a aplicação e execute "
                   "`python -c \"from src import backup; from pathlib import Path; "
                   "backup.restaurar(Path('data/backups/ARQUIVO.db'))\"`.")
    else:
        st.info("Nenhum backup ainda.")


def _nome(usuario: Usuario) -> str:
    cliente = comum.cliente_do_usuario(usuario)
    return cliente.nome if cliente else "—"


def _auditoria(sessao) -> None:
    col1, col2 = st.columns([1, 2])
    with col1:
        dias = st.selectbox("Janela", [7, 30, 90, 365],
                            format_func=lambda d: f"Últimos {d} dias")
    with col2:
        acoes = st.multiselect("Ações", audit.ACOES, placeholder="Todas as ações")

    limite = datetime.now(timezone.utc) - timedelta(days=dias)
    registros = audit.consultar(sessao, limite, acoes)

    if not registros:
        st.info("Nenhum registro de auditoria na janela selecionada.")
        return

    tabela = pd.DataFrame([
        {
            "Data/hora": r.data_hora.strftime("%d/%m/%Y %H:%M:%S"),
            "Usuário": _rotulo_usuario(sessao, r.usuario_id),
            "Ação": r.acao.replace("_", " ").title(),
            "Entidade": r.entidade or "—",
            "Detalhe": (r.dados_novos or "")[:120],
        }
        for r in registros
    ])
    st.dataframe(tabela, hide_index=True, use_container_width=True, height=520)
    comum.nota(
        "Registros de contas excluídas permanecem na trilha com o identificador "
        "anonimizado, atendendo simultaneamente à retenção para auditoria e ao "
        "direito de eliminação (RN-015)."
    )


def _rotulo_usuario(sessao, usuario_id: str | None) -> str:
    if not usuario_id:
        return "—"
    if usuario_id == lgpd.ANONIMO:
        return "(anonimizado)"
    usuario = sessao.get(Usuario, usuario_id)
    return usuario.email if usuario else "(removido)"
