"""Direito de correção do titular (LGPD, art. 18, III; seção 2.6.2)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from src import administracao, audit, lgpd, security
from src.models import AuditLog, Cliente, Usuario
from tests.apoio import SENHA, criar_usuario

CPF_VALIDO = "529.982.247-25"
OUTRO_CPF = "111.444.777-35"


def _cliente(sessao, usuario: Usuario) -> Cliente:
    return sessao.scalar(select(Cliente).where(Cliente.usuario_id == usuario.id))


@pytest.fixture()
def titular(sessao):
    return criar_usuario(sessao, "titular@exemplo.com", "Maria Antiga", cpf=CPF_VALIDO)


class TestCpf:
    def test_normaliza_e_confere_digitos(self):
        assert security.normalizar_cpf(CPF_VALIDO) == "52998224725"
        assert security.normalizar_cpf("  ") is None

    @pytest.mark.parametrize("cpf", ["529.982.247-26", "123", "111.111.111-11"])
    def test_rejeita_invalido(self, cpf):
        with pytest.raises(ValueError, match="CPF inválido"):
            security.normalizar_cpf(cpf)


class TestCorrecao:
    def test_corrige_todos_os_campos(self, sessao, titular):
        alterados = lgpd.corrigir_cadastro(
            sessao, titular.id, senha=SENHA, nome="Maria Nova", email="Nova@Exemplo.com",
            cpf=OUTRO_CPF, moeda_base="USD", perfil_risco="ARROJADO",
        )
        assert alterados == ["email", "nome", "cpf", "moeda_base", "perfil_risco"]
        cliente = _cliente(sessao, titular)
        assert (titular.email, cliente.nome, cliente.moeda_base, cliente.perfil_risco) == \
            ("nova@exemplo.com", "Maria Nova", "USD", "ARROJADO")
        assert security.decifrar(cliente.cpf_cifrado) == "11144477735"
        assert "11144477735" not in cliente.cpf_cifrado

    def test_login_passa_a_usar_o_novo_email(self, sessao, titular):
        lgpd.corrigir_cadastro(sessao, titular.id, senha=SENHA, email="nova@exemplo.com")
        usuario, _ = security.autenticar(sessao, "nova@exemplo.com", SENHA)
        assert usuario.id == titular.id

    def test_remove_cpf_com_texto_vazio(self, sessao, titular):
        assert lgpd.corrigir_cadastro(sessao, titular.id, senha=SENHA, cpf="") == ["cpf"]
        assert _cliente(sessao, titular).cpf_cifrado is None

    def test_sem_mudanca_nao_audita(self, sessao, titular):
        alterados = lgpd.corrigir_cadastro(sessao, titular.id, senha=SENHA, nome="Maria Antiga",
                                           email="titular@exemplo.com", cpf=CPF_VALIDO)
        assert alterados == []
        assert sessao.scalar(select(AuditLog).where(AuditLog.acao == audit.CORRIGIR_DADOS)) is None

    def test_exige_reautenticacao(self, sessao, titular):
        with pytest.raises(ValueError, match="Reautenticação"):
            lgpd.corrigir_cadastro(sessao, titular.id, senha="Errada123", nome="Outra")
        assert _cliente(sessao, titular).nome == "Maria Antiga"

    def test_reautenticacao_tem_limite_de_tentativas(self, sessao, titular, monkeypatch):
        monkeypatch.setattr(security.limitador, "limite", 2)
        for _ in range(2):
            with pytest.raises(ValueError):
                lgpd.corrigir_cadastro(sessao, titular.id, senha="Errada123", nome="Outra")
        with pytest.raises(security.ErroLimiteTaxa):
            lgpd.corrigir_cadastro(sessao, titular.id, senha=SENHA, nome="Outra")

    @pytest.mark.parametrize("campos, mensagem", [
        ({"email": "invalido"}, "E-mail inválido"),
        ({"nome": "  "}, "nome não pode ficar vazio"),
        ({"cpf": "123.456.789-00"}, "CPF inválido"),
        ({"moeda_base": "JPY"}, "Moeda base inválida"),
        ({"perfil_risco": "AGRESSIVO"}, "Perfil de risco inválido"),
    ])
    def test_validacoes(self, sessao, titular, campos, mensagem):
        with pytest.raises(ValueError, match=mensagem):
            lgpd.corrigir_cadastro(sessao, titular.id, senha=SENHA, **campos)
        sessao.rollback()
        assert _cliente(sessao, titular).nome == "Maria Antiga"

    def test_email_de_outra_conta(self, sessao, titular):
        criar_usuario(sessao, "ocupado@exemplo.com")
        with pytest.raises(ValueError, match="Já existe"):
            lgpd.corrigir_cadastro(sessao, titular.id, senha=SENHA, email="ocupado@exemplo.com")

    def test_conta_sem_cliente_corrige_so_email(self, sessao, titular):
        admin = criar_usuario(sessao, "admin@exemplo.com")
        admin.perfil = "ADMIN"
        sessao.commit()
        colab, senha = administracao.criar_usuario(sessao, admin, email="colab@exemplo.com",
                                                   nome="Colab", perfil="COLABORADOR")
        assert lgpd.corrigir_cadastro(sessao, colab.id, senha=senha,
                                      email="colab2@exemplo.com") == ["email"]
        with pytest.raises(ValueError, match="só o e-mail"):
            lgpd.corrigir_cadastro(sessao, colab.id, senha=senha, nome="Outro")


class TestAuditoria:
    def test_registra_so_os_campos(self, sessao, titular):
        lgpd.corrigir_cadastro(sessao, titular.id, senha=SENHA, nome="Maria Nova", cpf=OUTRO_CPF)
        log = sessao.scalar(select(AuditLog).where(AuditLog.acao == audit.CORRIGIR_DADOS))
        assert log.usuario_id == titular.id
        assert "Maria Nova" not in log.dados_novos and "11144477735" not in log.dados_novos
        assert "nome" in log.dados_novos and "cpf" in log.dados_novos

    def test_valores_antigos_saem_da_trilha(self, sessao, titular):
        audit.registrar(sessao, usuario_id=titular.id, acao=audit.CRIAR_USUARIO,
                        dados_novos={"email": "titular@exemplo.com", "nome": "Maria Antiga"})
        sessao.commit()
        lgpd.corrigir_cadastro(sessao, titular.id, senha=SENHA, nome="Maria Nova",
                               email="nova@exemplo.com")
        textos = " ".join(l.dados_novos or "" for l in sessao.scalars(select(AuditLog)))
        assert "Maria Antiga" not in textos and "titular@exemplo.com" not in textos
        assert lgpd.CORRIGIDO in textos

    def test_exclusao_depois_da_correcao_nao_deixa_rastro(self, sessao, titular):
        lgpd.corrigir_cadastro(sessao, titular.id, senha=SENHA, nome="Maria Nova",
                               email="nova@exemplo.com", cpf=OUTRO_CPF)
        lgpd.excluir(sessao, titular.id)
        for log in sessao.scalars(select(AuditLog)):
            dados = (log.dados_novos or "") + (log.dados_anteriores or "")
            for valor in ("Maria", "titular@", "nova@", "52998224725", "11144477735", titular.id):
                assert valor not in dados
            assert log.usuario_id in (None, lgpd.ANONIMO)
