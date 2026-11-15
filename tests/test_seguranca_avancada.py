"""
Segurança da sessão e da conta: criptografia AES-256-GCM em repouso (RNF-004),
segundo fator TOTP, access e refresh tokens, recuperação de senha (UC-03),
rate limiting (RNF-011) e administração de contas (RF-024, RN-013).
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import config
from src import administracao, lgpd, security
from src.models import AuditLog, Cliente, TokenSeguranca, Usuario

from tests.apoio import criar_usuario, usuario_de


class TestCriptografiaAES256:
    def test_ida_e_volta(self):
        cifrado = security.cifrar("123.456.789-00")
        assert cifrado.startswith("aes256gcm:")
        assert security.decifrar(cifrado) == "123.456.789-00"

    def test_chave_tem_256_bits(self):
        assert len(security._chave_aes()) == 32

    def test_nonce_aleatorio(self):
        assert security.cifrar("x") != security.cifrar("x")

    def test_adulteracao_detectada(self):
        cifrado = security.cifrar("segredo")
        bruto = bytearray(base64.urlsafe_b64decode(cifrado[len("aes256gcm:"):]))
        bruto[-1] ^= 0x01
        adulterado = "aes256gcm:" + base64.urlsafe_b64encode(bytes(bruto)).decode()
        assert security.decifrar(adulterado) is None

    def test_valores_vazios(self):
        assert security.cifrar(None) is None
        assert security.decifrar(None) is None

    def test_valor_sem_prefixo_aes_nao_e_aceito(self):
        """Só AES-256-GCM é aceito; o formato Fernet da versão inicial foi removido."""
        assert security.decifrar("gAAAAABtexto-qualquer") is None

    def test_mascara_de_cpf(self):
        assert security.mascarar_cpf("123.456.789-09") == "***.***.***-09"
        assert security.mascarar_cpf("123") is None


class TestTOTP:
    SEGREDO_RFC = base64.b32encode(b"12345678901234567890").decode()

    @pytest.mark.parametrize("instante, esperado", [
        (59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037"),
    ])
    def test_vetores_rfc_6238(self, instante, esperado):
        assert security.codigo_totp_atual(self.SEGREDO_RFC, instante) == esperado

    def test_janela_de_tolerancia(self):
        seg = security.gerar_segredo_totp()
        anterior = security.codigo_totp_atual(seg, 1_000_000 - 30)
        assert security.verificar_totp(seg, anterior, 1_000_000)
        muito_antigo = security.codigo_totp_atual(seg, 1_000_000 - 120)
        assert not security.verificar_totp(seg, muito_antigo, 1_000_000)

    @pytest.fixture()
    def com_2fa(self, sessao, carteira):
        """Titular da carteira com 2FA ativo; devolve (usuário, segredo)."""
        usuario = usuario_de(sessao, carteira)
        seg = security.gerar_segredo_totp()
        security.ativar_2fa(sessao, usuario, seg, security.codigo_totp_atual(seg))
        return usuario, seg

    def test_login_exige_segundo_fator(self, sessao, com_2fa):
        usuario, seg = com_2fa
        with pytest.raises(security.SegundoFatorNecessario):
            security.autenticar(sessao, "teste@exemplo.com", "Senha123")
        with pytest.raises(security.ErroAutenticacao):
            security.autenticar(sessao, "teste@exemplo.com", "Senha123", "000000")
        u, token = security.autenticar(sessao, "teste@exemplo.com", "Senha123",
                                       security.codigo_totp_atual(seg))
        assert u.id == usuario.id and security.validar_token(token)

    def test_segredo_gravado_cifrado(self, sessao, com_2fa):
        usuario, seg = com_2fa
        assert seg not in usuario.totp_segredo_cifrado
        assert security.decifrar(usuario.totp_segredo_cifrado) == seg

    def test_reautenticacao_lgpd_com_2fa(self, sessao, com_2fa):
        usuario, seg = com_2fa
        assert not security.reautenticar(usuario, "Senha123")
        assert security.reautenticar(usuario, "Senha123", security.codigo_totp_atual(seg))

    def test_desativar(self, sessao, com_2fa):
        usuario, seg = com_2fa
        security.desativar_2fa(sessao, usuario, "Senha123", security.codigo_totp_atual(seg))
        assert not usuario.totp_ativo
        security.autenticar(sessao, "teste@exemplo.com", "Senha123")


class TestRefreshToken:
    def test_rotacao(self, sessao, carteira):
        usuario = usuario_de(sessao, carteira)
        r1 = security.emitir_refresh(sessao, usuario)
        renovado = security.renovar_sessao(sessao, r1)
        assert renovado is not None
        _, access, r2 = renovado
        assert security.validar_token(access)["sub"] == usuario.id
        assert security.renovar_sessao(sessao, r1) is None
        assert security.renovar_sessao(sessao, r2) is not None

    def test_so_hash_persistido(self, sessao, carteira):
        r = security.emitir_refresh(sessao, usuario_de(sessao, carteira))
        assert not sessao.scalar(select(TokenSeguranca).where(TokenSeguranca.token_hash == r))

    def test_revogacao(self, sessao, carteira):
        usuario = usuario_de(sessao, carteira)
        r = security.emitir_refresh(sessao, usuario)
        assert security.revogar_sessoes(sessao, usuario.id) == 1
        assert security.renovar_sessao(sessao, r) is None

    def test_expirado(self, sessao, carteira):
        usuario = usuario_de(sessao, carteira)
        r = security.emitir_refresh(sessao, usuario)
        registro = sessao.scalar(select(TokenSeguranca))
        registro.expira_em = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        sessao.commit()
        assert security.renovar_sessao(sessao, r) is None

    def test_conta_desativada_nao_renova(self, sessao, carteira):
        usuario = usuario_de(sessao, carteira)
        r = security.emitir_refresh(sessao, usuario)
        usuario.ativo = False
        sessao.commit()
        assert security.renovar_sessao(sessao, r) is None

    def test_refresh_nao_serve_como_access(self, sessao, carteira):
        assert security.validar_token(security.emitir_refresh(sessao, usuario_de(sessao, carteira))) is None


class TestRecuperacaoSenha:
    @pytest.fixture(autouse=True)
    def outbox(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "OUTBOX_DIR", tmp_path)
        return tmp_path

    def test_fluxo_completo(self, sessao, carteira, outbox):
        usuario = usuario_de(sessao, carteira)
        refresh = security.emitir_refresh(sessao, usuario)
        token = security.solicitar_redefinicao(sessao, "teste@exemplo.com")
        emails = list(outbox.glob("*.eml"))
        assert len(emails) == 1 and token in emails[0].read_text()

        security.redefinir_senha(sessao, token, "NovaSenha9")
        security.autenticar(sessao, "teste@exemplo.com", "NovaSenha9")
        with pytest.raises(ValueError):
            security.redefinir_senha(sessao, token, "OutraSenha9")
        assert security.renovar_sessao(sessao, refresh) is None

    def test_email_inexistente_nao_revela(self, sessao, outbox):
        assert security.solicitar_redefinicao(sessao, "nao@existe.com") is None
        assert not list(outbox.glob("*.eml"))

    def test_token_expirado(self, sessao, carteira):
        token = security.solicitar_redefinicao(sessao, "teste@exemplo.com")
        registro = sessao.scalar(select(TokenSeguranca).where(TokenSeguranca.tipo == "RESET_SENHA"))
        registro.expira_em = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        sessao.commit()
        with pytest.raises(ValueError, match="expirado"):
            security.redefinir_senha(sessao, token, "NovaSenha9")

    def test_politica_de_senha(self, sessao, carteira):
        token = security.solicitar_redefinicao(sessao, "teste@exemplo.com")
        with pytest.raises(ValueError):
            security.redefinir_senha(sessao, token, "fraca")

    def test_desbloqueia_conta(self, sessao, carteira):
        for _ in range(5):
            with pytest.raises(security.ErroAutenticacao):
                security.autenticar(sessao, "teste@exemplo.com", "errada1")
        token = security.solicitar_redefinicao(sessao, "teste@exemplo.com")
        security.redefinir_senha(sessao, token, "NovaSenha9")
        security.autenticar(sessao, "teste@exemplo.com", "NovaSenha9")


class TestRateLimiting:
    def test_janela_deslizante(self):
        lim = security.LimitadorTaxa(60, 60)
        assert all(lim.permitir("u", agora=float(i) / 10) for i in range(60))
        assert not lim.permitir("u", agora=6.5)
        assert lim.permitir("outro", agora=6.5)
        assert lim.permitir("u", agora=60.05)

    def test_login_limitado(self, sessao, carteira):
        security.limitador.limpar()
        for _ in range(config.RATE_LIMIT_REQUISICOES):
            security.limitador.permitir("login:teste@exemplo.com")
        with pytest.raises(security.ErroLimiteTaxa):
            security.autenticar(sessao, "teste@exemplo.com", "Senha123")


class TestAdministracao:
    @pytest.fixture()
    def admin(self, sessao):
        return criar_usuario(sessao, "adm@exemplo.com", "Adm", perfil="ADMIN")

    def test_criar_cliente_com_consentimento_pendente(self, sessao, admin):
        u, senha = administracao.criar_usuario(sessao, admin, email="novo@exemplo.com",
                                               nome="Novo", perfil="CLIENTE")
        assert not security.validar_politica_senha(senha)
        cliente = sessao.scalar(select(Cliente).where(Cliente.usuario_id == u.id))
        assert cliente.consentimento_lgpd is False
        assert security.carteiras_visiveis(sessao, u)

    def test_somente_admin(self, sessao, carteira):
        with pytest.raises(ValueError):
            administracao.criar_usuario(sessao, usuario_de(sessao, carteira), email="a@b.com",
                                        nome="A", perfil="CLIENTE")

    def test_salvaguardas(self, sessao, admin):
        with pytest.raises(ValueError, match="própria"):
            administracao.alterar_usuario(sessao, admin, admin.id, perfil="CLIENTE", ativo=True)
        with pytest.raises(ValueError, match="própria"):
            administracao.excluir_usuario(sessao, admin, admin.id)

    def test_ultimo_admin_nao_exclui_a_propria_conta(self, sessao, admin):
        """UC-19: a exclusão pela tela de privacidade também respeita a salvaguarda."""
        with pytest.raises(ValueError, match="administrador ativo"):
            lgpd.excluir(sessao, admin.id)
        assert sessao.get(Usuario, admin.id) is not None

        criar_usuario(sessao, "adm2@exemplo.com", "Adm 2", perfil="ADMIN")
        lgpd.excluir(sessao, admin.id)
        assert sessao.get(Usuario, admin.id) is None

    def test_vinculo_colaborador_governa_visibilidade(self, sessao, admin, carteira):
        colab, _ = administracao.criar_usuario(sessao, admin, email="col@exemplo.com",
                                               nome="Col", perfil="COLABORADOR")
        assert security.carteiras_visiveis(sessao, colab) == []
        administracao.vincular_colaborador(sessao, admin, carteira.cliente_id, colab.id)
        assert [c.id for c in security.carteiras_visiveis(sessao, colab)] == [carteira.id]
        administracao.vincular_colaborador(sessao, admin, carteira.cliente_id, None)
        assert security.carteiras_visiveis(sessao, colab) == []

    def test_vinculo_exige_colaborador(self, sessao, admin, carteira):
        with pytest.raises(ValueError, match="COLABORADOR"):
            administracao.vincular_colaborador(sessao, admin, carteira.cliente_id, admin.id)

    def test_desativar_revoga_sessoes(self, sessao, admin, carteira):
        alvo = usuario_de(sessao, carteira)
        r = security.emitir_refresh(sessao, alvo)
        administracao.alterar_usuario(sessao, admin, alvo.id, perfil="CLIENTE", ativo=False)
        assert security.renovar_sessao(sessao, r) is None

    def test_excluir_anonimiza(self, sessao, admin, carteira):
        alvo = usuario_de(sessao, carteira)
        alvo_id = alvo.id
        administracao.excluir_usuario(sessao, admin, alvo_id)
        assert sessao.get(Usuario, alvo_id) is None
        assert not sessao.scalars(select(AuditLog).where(AuditLog.usuario_id == alvo_id)).all()
