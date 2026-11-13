"""
Testes de fumaça da camada de apresentação.

Usam o AppTest do Streamlit para executar as telas de verdade, sem
navegador. O objetivo não é validar pixels, mas garantir que cada tela
renderize sem exceção com uma carteira populada — a falha mais comum e
mais cara de descobrir só na banca.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from sqlalchemy import select
from streamlit.testing.v1 import AppTest

import config
from src import administracao, security
from src.db import SessionLocal
from src.models import Posicao, Usuario
from tests.apoio import base_modelo, carteira_do_email, criar_usuario, popular

APP = str(Path(__file__).resolve().parent.parent / "app.py")

TELAS = [
    "Visão consolidada",
    "Importar carteira",
    "Lançamentos manuais",
    "Fundos PE/VC",
    "Imóveis",
    "Relatórios",
    "Privacidade e dados",
    "Administração",
]


def _app_logado(usuario, token=None, refresh=None):
    """AppTest com a sessão do Streamlit já autenticada como `usuario`."""
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state.db = SessionLocal()
    at.session_state.usuario_id = usuario.id
    at.session_state.token = token or security.gerar_token(usuario)
    if refresh:
        at.session_state.refresh = refresh
    return at


def _navegacao(at):
    """Radios do seletor de navegação (vazio quando o usuário não entrou)."""
    return [r for r in at.radio if "Navegação" in (r.label or "")]


@pytest.fixture()
def app_populado(sessao, tmp_path):
    """Admin autenticado com carteira contendo as três classes de ativo."""
    def montar(s):
        usuario = criar_usuario(s, "ui@exemplo.com", "Usuário UI", perfil="ADMIN")
        popular(s, usuario, carteira_do_email(s, usuario.email), tmp_path, com_precos=True)

    base_modelo(sessao, "app_populado", montar)
    carteira = carteira_do_email(sessao, "ui@exemplo.com")
    usuario = sessao.scalar(select(Usuario).where(Usuario.email == "ui@exemplo.com"))

    at = AppTest.from_file(APP, default_timeout=90)
    at.session_state.usuario_id = usuario.id
    at.session_state.token = security.gerar_token(usuario)
    at.session_state.carteira_id = carteira.id
    return at


class TestLogin:
    def test_tela_de_login_renderiza(self):
        at = AppTest.from_file(APP, default_timeout=60).run()
        assert not at.exception
        assert len(at.tabs) >= 2

    def test_credencial_invalida_mostra_erro(self):
        at = AppTest.from_file(APP, default_timeout=60).run()
        at.text_input[0].set_value("ninguem@exemplo.com")
        at.text_input[1].set_value("errada")
        at.button[0].click().run()
        assert not at.exception
        assert at.error


class TestTelasAutenticadas:
    @pytest.mark.parametrize("tela", TELAS)
    def test_tela_renderiza_sem_excecao(self, app_populado, tela):
        at = app_populado.run()
        assert not at.exception, f"exceção ao carregar a aplicação: {at.exception}"

        seletor = _navegacao(at)
        assert seletor, "seletor de navegação não encontrado"
        seletor[0].set_value(tela).run()
        assert not at.exception, f"exceção na tela '{tela}': {at.exception}"

    def test_dashboard_exibe_patrimonio(self, app_populado):
        at = app_populado.run()
        markdown = " ".join(m.value for m in at.markdown)
        assert "Patrimônio consolidado" in markdown

    def test_sem_carteira_nao_quebra(self, sessao):
        """Conta recém-criada, sem posições: a tela orienta em vez de falhar."""
        at = _app_logado(criar_usuario(sessao, "vazio@exemplo.com", "Vazio")).run()
        assert not at.exception
        assert at.info


class TestFluxosEtapa2:
    def test_login_tem_aba_de_recuperacao(self):
        at = AppTest.from_file(APP, default_timeout=60).run()
        assert [t.label for t in at.tabs][:3] == ["Entrar", "Criar conta", "Esqueci minha senha"]

    def test_recuperacao_sem_smtp_com_dados_fora_do_projeto(self, sessao):
        """Versão instalada: a pasta de dados fica fora do código (CARTEIRA_DATA_DIR)."""
        criar_usuario(sessao, "esqueci@exemplo.com", "Esqueci")
        assert not config.OUTBOX_DIR.is_relative_to(config.BASE_DIR)

        at = AppTest.from_file(APP, default_timeout=60).run()
        next(t for t in at.text_input if t.label == "E-mail cadastrado").set_value(
            "esqueci@exemplo.com")
        next(b for b in at.button if b.label == "Enviar código").click().run()
        assert not at.exception, f"exceção na recuperação de senha: {at.exception}"
        assert at.success
        assert any(str(config.OUTBOX_DIR) in c.value for c in at.caption)

    def test_login_pede_codigo_2fa(self, sessao):
        usuario = criar_usuario(sessao, "dois@exemplo.com", "Dois")
        seg = security.gerar_segredo_totp()
        security.ativar_2fa(sessao, usuario, seg, security.codigo_totp_atual(seg))

        at = AppTest.from_file(APP, default_timeout=60).run()
        at.text_input[0].set_value("dois@exemplo.com")
        at.text_input[1].set_value("Senha123")
        at.button[0].click().run()
        assert not at.exception
        rotulos = [t.label for t in at.text_input]
        assert any("Código de verificação" in r for r in rotulos)
        assert "token" not in at.session_state

    def test_access_expirado_renova_pelo_refresh(self, sessao):
        usuario = criar_usuario(sessao, "ref@exemplo.com", "Ref")
        vencido = jwt.encode({"sub": usuario.id, "tipo": "access",
                              "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                             config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)
        at = _app_logado(usuario, vencido, security.emitir_refresh(sessao, usuario)).run()
        assert not at.exception
        assert _navegacao(at)
        assert security.validar_token(at.session_state.token)

    def test_refresh_revogado_volta_ao_login(self, sessao):
        usuario = criar_usuario(sessao, "rev@exemplo.com", "Rev")
        refresh = security.emitir_refresh(sessao, usuario)
        security.revogar_sessoes(sessao, usuario.id)
        at = _app_logado(usuario, "invalido", refresh).run()
        assert not at.exception
        assert not _navegacao(at)

    def test_consentimento_pendente_bloqueia(self, sessao):
        admin = criar_usuario(sessao, "a2@exemplo.com", "A", perfil="ADMIN")
        u, _ = administracao.criar_usuario(sessao, admin, email="c2@exemplo.com", nome="C",
                                           perfil="CLIENTE", senha="Senha123")
        at = _app_logado(u).run()
        assert not at.exception
        assert not _navegacao(at)
        assert any("consentimento" in (c.label or "").lower() for c in at.checkbox)

    def test_alterar_conta_parte_do_perfil_atual(self, app_populado, sessao):
        """Desbloquear um cliente sem mexer no perfil não pode promovê-lo a ADMIN."""
        cliente = criar_usuario(sessao, "cli@exemplo.com", "Cli")
        at = app_populado.run()
        _navegacao(at)[0].set_value(
            "Administração").run()
        [s for s in at.selectbox if s.label == "Conta"][0].set_value(cliente.id).run()
        assert [s for s in at.selectbox if s.label == "Perfil"][-1].value == "CLIENTE"
        assert [s for s in at.selectbox if s.label == "Situação"][0].value == "Ativa"
        [c for c in at.checkbox if c.label.startswith("Remover bloqueio")][0].check()
        [b for b in at.button if b.label == "Aplicar alterações"][0].click().run()
        assert not at.exception
        verificacao = SessionLocal()
        assert verificacao.get(Usuario, cliente.id).perfil == "CLIENTE"
        verificacao.close()

    def test_registrar_venda_pela_tela(self, app_populado):
        at = app_populado.run()
        _navegacao(at)[0].set_value(
            "Lançamentos manuais").run()
        assert not at.exception
        tipo = [r for r in at.radio if r.label == "Tipo"][0]
        tipo.set_value("VENDA").run()
        pos_id = at.selectbox[0].value
        sessao = SessionLocal()
        antes = sessao.get(Posicao, pos_id).quantidade
        sessao.close()
        [n for n in at.number_input if n.label == "Quantidade"][0].set_value(1.0)
        [n for n in at.number_input if n.label.startswith("Preço unitário")][0].set_value(10.0)
        [b for b in at.button if b.label == "Registrar"][0].click().run()
        assert not at.exception
        assert not at.error, [e.value for e in at.error]
        sessao = SessionLocal()
        assert sessao.get(Posicao, pos_id).quantidade == pytest.approx(antes - 1)
        sessao.close()
