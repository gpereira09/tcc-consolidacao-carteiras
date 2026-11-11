"""Privacidade, direitos do titular e segurança da conta (RF-027, RF-028; UC-17, UC-18)."""
from __future__ import annotations

import streamlit as st

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
        st.markdown(comum.ficha("Titular", cliente.nome if cliente else usuario.email),
                    unsafe_allow_html=True)
        st.markdown(comum.ficha("E-mail de acesso", usuario.email),
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
            f"Registrado em {cliente.data_consentimento:%d/%m/%Y}"
            if cliente and cliente.data_consentimento else "—"
        )
        st.markdown(comum.ficha("Consentimento", consentimento), unsafe_allow_html=True)

    st.divider()
    aba_export, aba_excluir, aba_seg = st.tabs(
        ["Exportar meus dados", "Excluir minha conta", "Segurança da conta"])

    with aba_export:
        _exportacao(sessao, usuario)
    with aba_excluir:
        _exclusao(sessao, usuario)
    with aba_seg:
        _seguranca(sessao, usuario)


def _campo_2fa(usuario) -> str | None:
    """Campo do segundo fator, exibido apenas se o usuário habilitou o 2FA."""
    if not usuario.totp_ativo:
        return None
    return st.text_input("Código do autenticador (2FA)", max_chars=6)


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
        st.error("Reautenticação falhou: senha" + (" ou código 2FA" if usuario.totp_ativo else "")
                 + " incorreto(s).")
        return
    if confirmacao.strip().upper() != "EXCLUIR":
        st.error("Digite EXCLUIR para confirmar a operação.")
        return
    if not ciente:
        st.error("Marque a confirmação de ciência antes de prosseguir.")
        return

    security.revogar_sessoes(sessao, usuario.id)
    contagem = lgpd.excluir(sessao, usuario.id)
    st.success(
        f"Conta excluída: {contagem['carteiras']} carteira(s), "
        f"{contagem['posicoes']} posição(ões) e {contagem['eventos']} evento(s) "
        f"removidos; {contagem['logs_anonimizados']} registro(s) de auditoria "
        f"anonimizado(s)."
    )
    comum.encerrar_sessao(revogar=False)
    st.rerun()


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
