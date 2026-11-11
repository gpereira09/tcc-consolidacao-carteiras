"""Testes de segurança, controle de acesso e direitos LGPD."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from src import lgpd, security
from src.models import AuditLog, Carteira, Cliente, Posicao, Usuario
from tests.apoio import criar_usuario, importar_template


class TestSenhas:
    def test_hash_nao_guarda_senha_em_claro(self):
        h = security.hash_senha("Senha123")
        assert "Senha123" not in h
        assert security.verificar_senha("Senha123", h)
        assert not security.verificar_senha("Senha124", h)

    def test_politica_rn001(self):
        assert security.validar_politica_senha("Senha123") == []
        assert security.validar_politica_senha("curta1")
        assert security.validar_politica_senha("somenteletras")
        assert security.validar_politica_senha("12345678")


class TestJWT:
    def test_emissao_e_validacao(self, sessao):
        u = criar_usuario(sessao, "jwt@exemplo.com", "JWT")
        payload = security.validar_token(security.gerar_token(u))
        assert payload["sub"] == u.id
        assert payload["perfil"] == "CLIENTE"

    def test_token_invalido(self):
        assert security.validar_token("nao.e.um.token") is None


class TestCriacaoDeConta:
    def test_cria_usuario_cliente_e_carteira(self, sessao):
        u = security.criar_conta(sessao, email="Novo@Exemplo.com ", nome="Novo",
                                 senha="Senha123", consentimento_lgpd=True)
        assert u.email == "novo@exemplo.com"
        cliente = sessao.scalar(select(Cliente).where(Cliente.usuario_id == u.id))
        assert cliente is not None
        assert sessao.scalar(select(Carteira).where(Carteira.cliente_id == cliente.id))

    def test_consentimento_lgpd_obrigatorio(self, sessao):
        with pytest.raises(ValueError, match="LGPD"):
            security.criar_conta(sessao, email="x@exemplo.com", nome="X",
                                 senha="Senha123", consentimento_lgpd=False)

    def test_email_duplicado(self, sessao, carteira):
        with pytest.raises(ValueError, match="[Jj]á existe"):
            security.criar_conta(sessao, email="teste@exemplo.com", nome="Outro",
                                 senha="Senha123", consentimento_lgpd=True)

    def test_senha_fraca(self, sessao):
        with pytest.raises(ValueError):
            security.criar_conta(sessao, email="fraca@exemplo.com", nome="F",
                                 senha="123", consentimento_lgpd=True)


class TestAutenticacao:
    def test_login_valido(self, sessao, carteira):
        usuario, token = security.autenticar(sessao, "teste@exemplo.com", "Senha123")
        assert usuario.ultimo_login is not None
        assert security.validar_token(token) is not None

    def test_bloqueio_apos_cinco_tentativas(self, sessao, carteira):
        """RN-001: 5 falhas bloqueiam a conta por 15 minutos."""
        for _ in range(4):
            with pytest.raises(security.ErroAutenticacao) as erro:
                security.autenticar(sessao, "teste@exemplo.com", "errada")
            assert str(erro.value) == security.MSG_CREDENCIAIS

        with pytest.raises(security.ErroAutenticacao, match="bloqueada"):
            security.autenticar(sessao, "teste@exemplo.com", "errada")

        with pytest.raises(security.ErroAutenticacao, match="bloqueada"):
            security.autenticar(sessao, "teste@exemplo.com", "Senha123")

    def test_contador_zera_apos_sucesso(self, sessao, carteira):
        with pytest.raises(security.ErroAutenticacao):
            security.autenticar(sessao, "teste@exemplo.com", "errada")
        security.autenticar(sessao, "teste@exemplo.com", "Senha123")
        usuario = sessao.scalar(select(Usuario).where(Usuario.email == "teste@exemplo.com"))
        assert usuario.tentativas_falhas == 0


class TestRBAC:
    @pytest.fixture()
    def cenario(self, sessao, carteira):
        """Cliente A (com assessor), cliente B (sem vínculo) e um admin."""
        assessor = criar_usuario(sessao, "assessor@exemplo.com", "Assessor", perfil="COLABORADOR")
        admin = criar_usuario(sessao, "admin@exemplo.com", "Admin", perfil="ADMIN")
        outro = criar_usuario(sessao, "outro@exemplo.com", "Outro")

        cliente_a = sessao.scalar(select(Cliente).where(Cliente.nome == "Investidor Teste"))
        cliente_a.colaborador_id = assessor.id
        sessao.commit()

        titular = sessao.get(Usuario, cliente_a.usuario_id)
        return {"titular": titular, "assessor": assessor, "admin": admin, "outro": outro}

    def test_cliente_ve_so_a_propria_carteira(self, sessao, cenario, carteira):
        visiveis = security.carteiras_visiveis(sessao, cenario["titular"])
        assert [c.id for c in visiveis] == [carteira.id]

    def test_cliente_nao_ve_carteira_alheia(self, sessao, cenario, carteira):
        visiveis = security.carteiras_visiveis(sessao, cenario["outro"])
        assert carteira.id not in [c.id for c in visiveis]

    def test_colaborador_ve_apenas_vinculados(self, sessao, cenario, carteira):
        visiveis = security.carteiras_visiveis(sessao, cenario["assessor"])
        assert [c.id for c in visiveis] == [carteira.id]

    def test_admin_ve_todas(self, sessao, cenario, carteira):
        visiveis = security.carteiras_visiveis(sessao, cenario["admin"])
        assert len(visiveis) == 4

    def test_pode_acessar_carteira(self, sessao, cenario, carteira):
        assert security.pode_acessar_carteira(sessao, cenario["titular"], carteira.id)
        assert not security.pode_acessar_carteira(sessao, cenario["outro"], carteira.id)

    def test_exigir_perfil(self, cenario):
        security.exigir_perfil(cenario["admin"], "ADMIN")
        with pytest.raises(security.ErroAutorizacao):
            security.exigir_perfil(cenario["titular"], "ADMIN")


class TestLGPD:
    @pytest.fixture()
    def carteira_liq_pevc(self, sessao, carteira, tmp_path):
        """Carteira com os templates de líquidos e de PE/VC (13 posições)."""
        for chave in ("LIQUIDOS", "PEVC"):
            importar_template(sessao, carteira, chave, tmp_path)
        return carteira

    def test_exportacao_json_contem_o_patrimonio(self, sessao, carteira_liq_pevc):
        usuario_id = carteira_liq_pevc.cliente.usuario_id
        exp = lgpd.exportar(sessao, usuario_id, "JSON")

        assert exp.caminho.exists()
        dossie = json.loads(exp.caminho.read_text(encoding="utf-8"))
        assert dossie["cliente"]["nome"] == "Investidor Teste"
        posicoes = dossie["carteiras"][0]["posicoes"]
        assert len(posicoes) == 13
        assert any(p.get("fundo_iliquido") for p in posicoes)

    def test_cpf_e_devolvido_em_claro_ao_titular(self, sessao, carteira):
        """Portabilidade exige o dado legível, ainda que cifrado em repouso."""
        cliente = carteira.cliente
        cliente.cpf_cifrado = security.cifrar("123.456.789-00")
        sessao.commit()
        dossie = lgpd.coletar_dados(sessao, cliente.usuario_id)
        assert dossie["cliente"]["cpf"] == "123.456.789-00"

    def test_exportacao_csv_gera_zip(self, sessao, carteira_liq_pevc):
        exp = lgpd.exportar(sessao, carteira_liq_pevc.cliente.usuario_id, "CSV")
        assert exp.caminho.suffix == ".zip"

    def test_formato_invalido(self, sessao, carteira):
        with pytest.raises(ValueError):
            lgpd.exportar(sessao, carteira.cliente.usuario_id, "PDF")

    def test_link_e_de_uso_unico(self, sessao, carteira):
        """RN-014: o token vale uma vez só."""
        exp = lgpd.exportar(sessao, carteira.cliente.usuario_id, "JSON")
        assert lgpd.resgatar(exp.token) is not None
        assert lgpd.resgatar(exp.token) is None

    def test_exclusao_remove_dados_e_anonimiza_auditoria(self, sessao, carteira_liq_pevc):
        """RF-028 / RN-015."""
        usuario_id = carteira_liq_pevc.cliente.usuario_id
        carteira_id = carteira_liq_pevc.id

        contagem = lgpd.excluir(sessao, usuario_id)

        assert sessao.get(Usuario, usuario_id) is None
        assert sessao.scalar(select(Cliente).where(Cliente.usuario_id == usuario_id)) is None
        assert sessao.scalars(
            select(Posicao).where(Posicao.carteira_id == carteira_id)
        ).all() == []
        assert contagem["posicoes"] == 13

        restantes = sessao.scalars(
            select(AuditLog).where(AuditLog.usuario_id == usuario_id)
        ).all()
        assert restantes == []
        anonimos = sessao.scalars(
            select(AuditLog).where(AuditLog.usuario_id == lgpd.ANONIMO)
        ).all()
        assert len(anonimos) > 0

    def test_exclusao_de_usuario_inexistente(self, sessao):
        with pytest.raises(ValueError):
            lgpd.excluir(sessao, "id-que-nao-existe")
