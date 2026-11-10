"""Login, criação de conta e recuperação de senha (RF-001 a RF-003; UC-01 a UC-03)."""
from __future__ import annotations

import streamlit as st

import config
from src import security
from ui import comum, estilo

TERMO_LGPD = """
**Termo de consentimento — Lei nº 13.709/2018 (LGPD)**

Ao criar sua conta, você autoriza o tratamento dos dados pessoais e
financeiros informados, com a finalidade exclusiva de consolidar e exibir
o seu patrimônio nesta plataforma.

- **Dados tratados:** nome, e-mail, CPF e as posições de investimento que você importar.
- **Finalidade:** consolidação patrimonial, cálculo de rentabilidade e geração de relatórios para o seu próprio uso.
- **Armazenamento:** local, em banco de dados neste equipamento. O CPF é cifrado em repouso com AES-256-GCM.
- **Compartilhamento:** nenhum dado pessoal é enviado a terceiros. Apenas identificadores públicos de ativos (tickers) são usados em consultas a fontes de cotação.
- **Seus direitos:** a qualquer momento você pode exportar todos os seus dados ou solicitar a exclusão definitiva, na tela "Privacidade e dados".

O consentimento pode ser revogado a qualquer tempo pela exclusão da conta.
"""


def render() -> None:
    esquerda, centro, direita = st.columns([1, 1.5, 1])

    with centro:
        st.markdown(
            f'<div style="border-left:3px solid {estilo.TINTA};padding-left:1rem;'
            f'margin:2.5rem 0 1.5rem;">'
            f'<div style="font-family:\'Source Serif 4\',Georgia,serif;font-size:1.75rem;'
            f'color:{estilo.TINTA};font-weight:600;line-height:1.2;">'
            f'Consolidação de Carteiras</div>'
            f'<div style="color:{estilo.GRAFITE};font-size:0.9rem;margin-top:0.35rem;">'
            f'Ativos líquidos e ilíquidos · Brasil e offshore</div></div>',
            unsafe_allow_html=True,
        )

        token_url = st.query_params.get("redefinir")
        if token_url:
            _redefinir_senha(token_url)
            return

        aba_login, aba_conta, aba_senha = st.tabs(
            ["Entrar", "Criar conta", "Esqueci minha senha"])

        with aba_login:
            _formulario_login()
        with aba_conta:
            _formulario_conta()
        with aba_senha:
            _recuperar_senha()


def _formulario_login() -> None:
    pendente = st.session_state.get("login_2fa")
    with st.form("login"):
        email = st.text_input("E-mail", value=pendente["email"] if pendente else "")
        senha = st.text_input("Senha", type="password")
        codigo = None
        if pendente:
            codigo = st.text_input(
                "Código de verificação (6 dígitos)", max_chars=6,
                help="Gerado pelo aplicativo autenticador configurado na sua conta.")
        enviar = st.form_submit_button("Entrar", use_container_width=True)

    if not enviar:
        return

    sessao = comum.sessao()
    try:
        usuario, token = security.autenticar(sessao, email, senha, codigo_2fa=codigo)
    except security.SegundoFatorNecessario as aviso:
        st.session_state.login_2fa = {"email": email}
        st.info(str(aviso))
        st.rerun()
    except (security.ErroAutenticacao, security.ErroLimiteTaxa) as erro:
        st.error(str(erro))
        return

    st.session_state.pop("login_2fa", None)
    comum.iniciar_sessao(usuario, token)
    st.rerun()


def _recuperar_senha() -> None:
    """UC-03 — dois passos: solicitar o código por e-mail e definir a nova senha."""
    st.markdown(
        "Informe o e-mail da conta. Se ele estiver cadastrado, você receberá um código "
        f"de redefinição válido por {config.RESET_SENHA_MINUTOS} minutos."
    )
    with st.form("solicitar_reset"):
        email = st.text_input("E-mail cadastrado")
        pedir = st.form_submit_button("Enviar código", use_container_width=True)
    if pedir:
        try:
            security.solicitar_redefinicao(comum.sessao(), email)
        except security.ErroLimiteTaxa as erro:
            st.error(str(erro))
        else:
            st.success("Se o e-mail estiver cadastrado, o código foi enviado.")
            if not config.SMTP_HOST:
                st.caption(f"Modo local sem SMTP: a mensagem é gravada em "
                           f"`{config.OUTBOX_DIR.relative_to(config.BASE_DIR)}/`.")

    st.markdown("#### Já tenho o código")
    _formulario_nova_senha("")


def _redefinir_senha(token: str) -> None:
    st.markdown("### Redefinir senha")
    if _formulario_nova_senha(token):
        st.query_params.clear()


def _formulario_nova_senha(token_inicial: str) -> bool:
    with st.form(f"nova_senha_{bool(token_inicial)}"):
        token = token_inicial or st.text_input("Código recebido por e-mail")
        nova = st.text_input("Nova senha", type="password",
                             help="Mínimo de 8 caracteres, com letras e números.")
        confirmacao = st.text_input("Confirmar nova senha", type="password")
        enviar = st.form_submit_button("Redefinir senha", use_container_width=True)
    if not enviar:
        return False
    if nova != confirmacao:
        st.error("As senhas não conferem.")
        return False
    sessao = comum.sessao()
    try:
        security.redefinir_senha(sessao, token, nova)
    except ValueError as erro:
        st.error(str(erro))
        return False
    st.success("Senha redefinida. Use a aba “Entrar” com a nova senha.")
    return True


def consentimento_pendente(cliente) -> None:
    """
    Conta criada pelo administrador: o consentimento LGPD só pode ser dado
    pelo próprio titular, no primeiro acesso, antes de qualquer tratamento.
    """
    comum.cabecalho("Consentimento necessário",
                    "Sua conta foi criada por um administrador. Antes de continuar, "
                    "leia e aceite o termo de tratamento de dados.")
    st.markdown(TERMO_LGPD)
    with st.form("aceite_pendente"):
        aceite = st.checkbox("Li e concordo com o termo de consentimento (LGPD)")
        enviar = st.form_submit_button("Continuar", type="primary")
    if enviar:
        if not aceite:
            st.error("O consentimento é obrigatório para utilizar o sistema.")
            return
        security.registrar_consentimento(comum.sessao(), cliente)
        st.rerun()
    if st.button("Sair"):
        comum.encerrar_sessao()
        st.rerun()


def _formulario_conta() -> None:
    with st.expander("Ler o termo de consentimento"):
        st.markdown(TERMO_LGPD)

    with st.form("criar_conta"):
        nome = st.text_input("Nome completo")
        email = st.text_input("E-mail")
        cpf = st.text_input("CPF (opcional)", help="Armazenado cifrado com AES-256-GCM.")

        col1, col2 = st.columns(2)
        with col1:
            senha = st.text_input("Senha", type="password",
                                  help="Mínimo de 8 caracteres, com letras e números.")
            moeda = st.selectbox("Moeda base", config.MOEDAS)
        with col2:
            confirmacao = st.text_input("Confirmar senha", type="password")
            perfil_risco = st.selectbox("Perfil de risco", config.PERFIS_RISCO, index=1)

        aceite = st.checkbox("Li e concordo com o termo de consentimento (LGPD)")
        enviar = st.form_submit_button("Criar conta", use_container_width=True)

    if not enviar:
        return
    if senha != confirmacao:
        st.error("As senhas não conferem.")
        return

    sessao = comum.sessao()
    try:
        security.criar_conta(
            sessao, email=email, nome=nome, senha=senha,
            consentimento_lgpd=aceite, cpf=cpf or None,
            moeda_base=moeda, perfil_risco=perfil_risco,
        )
    except ValueError as erro:
        st.error(str(erro))
        return

    st.success("Conta criada. Use a aba “Entrar” para acessar o sistema.")
