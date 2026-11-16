"""Privacidade, direitos do titular e segurança da conta (RF-027, RF-028; UC-17, UC-18; correção, seção 2.6.2)."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st

import config
from src import lgpd, security
from ui import comum, estilo


def render() -> None:
    sessao = comum.sessao()
    usuario = comum.usuario_atual()
    cliente = comum.cliente_do_usuario(usuario)

    comum.cabecalho(
        "Privacidade e dados",
        "Exercício dos direitos previstos na Lei nº 13.709/2018 (LGPD).",
    )

    st.markdown("## Dados sob tratamento")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(comum.ficha("Titular", html.escape(cliente.nome if cliente
                                                       else usuario.email)),
                    unsafe_allow_html=True)
        st.markdown(comum.ficha("E-mail de acesso", html.escape(usuario.email)),
                    unsafe_allow_html=True)
    with col2:
        cpf = security.decifrar(cliente.cpf_cifrado) if cliente else None
        st.markdown(
            comum.ficha("CPF", security.mascarar_cpf(cpf) or ("informado" if cpf
                                                              else "não informado"),
                        "Cifrado em repouso com AES-256-GCM; exibido mascarado"
                        if cpf else ""),
            unsafe_allow_html=True,
        )
        consentimento = (
            f"Registrado em {_hora_local(cliente.data_consentimento):%d/%m/%Y}"
            if cliente and cliente.data_consentimento else "—"
        )
        st.markdown(comum.ficha("Consentimento", consentimento), unsafe_allow_html=True)

    st.divider()
    aba_corrigir, aba_export, aba_excluir, aba_seg = st.tabs(
        ["Corrigir meus dados", "Exportar meus dados", "Excluir minha conta",
         "Segurança da conta"])

    with aba_corrigir:
        _correcao(sessao, usuario, cliente)
    with aba_export:
        _exportacao(sessao, usuario)
    with aba_excluir:
        _exclusao(sessao, usuario)
    with aba_seg:
        _seguranca(sessao, usuario)


def _hora_local(instante: datetime) -> datetime:
    """O banco guarda os instantes em UTC; a tela mostra no fuso do sistema."""
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    try:
        return instante.astimezone(ZoneInfo(config.FUSO))
    except Exception:  # base de fusos ausente no sistema: usa o fuso da máquina
        return instante.astimezone()


def _campo_2fa(usuario) -> str | None:
    """Campo do segundo fator, exibido apenas se o usuário habilitou o 2FA."""
    if not usuario.totp_ativo:
        return None
    return st.text_input("Código do autenticador (2FA)", max_chars=6)


ROTULOS_CAMPOS = {"nome": "nome", "email": "e-mail", "cpf": "CPF",
                  "moeda_base": "moeda base", "perfil_risco": "perfil de risco"}


def _correcao(sessao, usuario, cliente) -> None:
    """Direito de correção (LGPD, art. 18, III; seção 2.6.2)."""
    mensagem = st.session_state.pop("correcao_ok", None)
    if mensagem:
        st.success(mensagem)

    st.markdown(
        "Corrija dados cadastrais incompletos, inexatos ou desatualizados. Os valores "
        "anteriores também deixam de constar na trilha de auditoria, que registra só "
        "quais campos mudaram."
    )
    cpf_atual = security.decifrar(cliente.cpf_cifrado) if cliente else None

    with st.form("corrigir_dados"):
        email = st.text_input("E-mail de acesso", value=usuario.email)
        nome = cpf = moeda = risco = None
        remover_cpf = False
        if cliente is not None:
            nome = st.text_input("Nome", value=cliente.nome)
            cpf = st.text_input(
                "CPF", placeholder=security.mascarar_cpf(cpf_atual) or "não informado",
                help="Deixe em branco para manter o CPF atual. Ele é guardado cifrado.",
            )
            remover_cpf = bool(cpf_atual) and st.checkbox("Remover o CPF do cadastro")
            c1, c2 = st.columns(2)
            with c1:
                moeda = st.selectbox("Moeda base", config.MOEDAS,
                                     index=config.MOEDAS.index(cliente.moeda_base))
            with c2:
                risco = st.selectbox("Perfil de risco", config.PERFIS_RISCO,
                                     index=config.PERFIS_RISCO.index(cliente.perfil_risco))
        senha = st.text_input("Confirme sua senha", type="password")
        codigo = _campo_2fa(usuario)
        enviar = st.form_submit_button("Salvar correções", type="primary")

    if not enviar:
        return
    if cliente is not None:
        cpf = "" if remover_cpf else (cpf.strip() or None)
    try:
        alterados = lgpd.corrigir_cadastro(
            sessao, usuario.id, senha=senha, codigo_2fa=codigo, nome=nome, email=email,
            cpf=cpf, moeda_base=moeda, perfil_risco=risco,
        )
    except (ValueError, security.ErroLimiteTaxa) as erro:
        st.error(str(erro))
        return
    if not alterados:
        st.info("Nenhum dado foi alterado.")
        return
    st.session_state.correcao_ok = (
        "Dados corrigidos: " + ", ".join(ROTULOS_CAMPOS[c] for c in alterados) + "."
    )
    st.rerun()


def _exportacao(sessao, usuario) -> None:
    st.markdown(
        "Gera um arquivo com **todos** os dados vinculados à sua conta: cadastro, "
        "carteiras, posições, transações, eventos de fundos, imóveis e histórico de "
        "importações. O arquivo é entregue uma única vez e apagado do servidor logo "
        "em seguida; se não for resgatado, é expurgado em 24 horas (RN-014)."
    )

    with st.form("exportar_lgpd"):
        formato = st.radio("Formato", ["JSON", "CSV"], horizontal=True,
                           help="JSON entrega o dossiê completo; CSV entrega um "
                                "pacote .zip com planilhas separadas.")
        senha = st.text_input("Confirme sua senha", type="password",
                              help="A RN-014 exige reautenticação para esta operação.")
        codigo = _campo_2fa(usuario)
        enviar = st.form_submit_button("Gerar arquivo", type="primary")

    if not enviar:
        return
    if not security.reautenticar(usuario, senha, codigo):
        st.error("Reautenticação falhou. A exportação exige senha"
                 + (" e código 2FA." if usuario.totp_ativo else "."))
        return

    exportacao = lgpd.exportar(sessao, usuario.id, formato, solicitante_id=usuario.id)
    resgatada = lgpd.resgatar(exportacao.token)
    if resgatada is None:
        st.error("O link de download expirou. Gere o arquivo novamente.")
        return

    st.success("Arquivo gerado. Baixe agora: por segurança, ele não fica armazenado.")
    st.download_button(
        f"Baixar {resgatada.caminho.name}",
        data=resgatada.conteudo,
        file_name=resgatada.caminho.name,
        mime="application/json" if formato == "JSON" else "application/zip",
    )


def _exclusao(sessao, usuario) -> None:
    st.markdown(
        "A exclusão remove definitivamente seu cadastro, carteiras, posições, "
        "transações, fundos e imóveis. **Esta ação não pode ser desfeita.**"
    )
    st.markdown(
        f'<div class="ficha" style="border-left-color:{estilo.VINHO};">'
        f'<div class="ficha-rotulo">O que é retido por obrigação legal</div>'
        f'<div style="color:{estilo.GRAFITE};font-size:0.86rem;margin-top:0.4rem;">'
        f'Os registros de auditoria são preservados, mas deixam de identificar você: '
        f'o vínculo com sua conta é substituído pela constante '
        f'<code>{lgpd.ANONIMO}</code> (RN-015). Preserva-se a trilha, não a pessoa.'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    with st.form("excluir_lgpd"):
        senha = st.text_input("Confirme sua senha", type="password")
        codigo = _campo_2fa(usuario)
        confirmacao = st.text_input(
            "Digite EXCLUIR para confirmar",
            help="Confirmação explícita para evitar exclusão acidental.",
        )
        ciente = st.checkbox("Entendo que esta ação é irreversível.")
        enviar = st.form_submit_button("Excluir minha conta definitivamente")

    if not enviar:
        return
    if not security.reautenticar(usuario, senha, codigo):
        st.error("Reautenticação falhou: senha ou código 2FA incorretos."
                 if usuario.totp_ativo else "Reautenticação falhou: senha incorreta.")
        return
    if confirmacao.strip().upper() != "EXCLUIR":
        st.error("Digite EXCLUIR para confirmar a operação.")
        return
    if not ciente:
        st.error("Marque a confirmação de ciência antes de prosseguir.")
        return

    usuario_id = usuario.id
    try:
        contagem = lgpd.excluir(sessao, usuario_id)
    except ValueError as erro:
        comum.falha(erro)
        return
    security.revogar_sessoes(sessao, usuario_id)
    comum.encerrar_sessao(revogar=False)
    comum.sucesso(
        f"Conta excluída: {contagem['carteiras']} carteira(s), "
        f"{contagem['posicoes']} posição(ões) e {contagem['eventos']} evento(s) "
        f"removidos; {contagem['logs_anonimizados']} registro(s) de auditoria "
        f"anonimizado(s)."
    )


def _qr_svg(uri: str) -> str | None:
    try:
        import segno
    except ImportError:
        return None
    import io
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2, dark="#17253F")
    return buf.getvalue().decode()


def _seguranca(sessao, usuario) -> None:
    st.markdown("### Autenticação em dois fatores (2FA)")
    st.markdown(
        "Com o 2FA ativo, o login e as operações de privacidade passam a exigir, além "
        "da senha, um código de 6 dígitos gerado por um aplicativo autenticador "
        "(Google Authenticator, Microsoft Authenticator, Authy, 1Password)."
    )

    if usuario.totp_ativo:
        st.success("2FA ativo nesta conta.")
        with st.form("desativar_2fa"):
            senha = st.text_input("Senha", type="password")
            codigo = st.text_input("Código atual do autenticador", max_chars=6)
            enviar = st.form_submit_button("Desativar 2FA")
        if enviar:
            try:
                security.desativar_2fa(sessao, usuario, senha, codigo)
            except ValueError as erro:
                st.error(str(erro))
                return
            st.rerun()
        return

    segredo = st.session_state.setdefault("totp_novo", security.gerar_segredo_totp())
    uri = security.uri_totp(usuario, segredo)
    col_qr, col_txt = st.columns([1, 2])
    with col_qr:
        svg = _qr_svg(uri)
        if svg:
            st.markdown(svg, unsafe_allow_html=True)
    with col_txt:
        st.markdown("1. Leia o QR code com o aplicativo autenticador, ou digite a chave:")
        st.code(" ".join(segredo[i:i + 4] for i in range(0, len(segredo), 4)), language=None)
        st.markdown("2. Informe o código que o aplicativo exibir para confirmar.")
        with st.form("ativar_2fa"):
            codigo = st.text_input("Código de 6 dígitos", max_chars=6)
            enviar = st.form_submit_button("Ativar 2FA", type="primary")
        if enviar:
            try:
                security.ativar_2fa(sessao, usuario, segredo, codigo)
            except ValueError as erro:
                st.error(str(erro))
                return
            st.session_state.pop("totp_novo", None)
            st.rerun()
