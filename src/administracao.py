"""
Administração de contas (RF-024, RF-025; UC-19).

CRUD completo de usuários pelo perfil ADMIN — criar, consultar, alterar
perfil/situação/bloqueio, vincular cliente a colaborador (RN-013) e excluir —
com as salvaguardas do UC-19 e auditoria de estado anterior e novo.
"""
from __future__ import annotations

import secrets
import string

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import config
from src import audit, lgpd, security
from src.models import Carteira, Cliente, Usuario


class ErroAdministracao(ValueError):
    pass


def _exigir_admin(admin: Usuario) -> None:
    try:
        security.exigir_perfil(admin, "ADMIN")
    except security.ErroAutorizacao as erro:
        raise ErroAdministracao(str(erro)) from erro


def listar_usuarios(sessao: Session, admin: Usuario) -> list[Usuario]:
    _exigir_admin(admin)
    return list(sessao.scalars(select(Usuario).order_by(Usuario.data_criacao)).all())


def listar_vinculos(sessao: Session, admin: Usuario
                    ) -> tuple[list[tuple[Cliente, Usuario]], list[Usuario]]:
    """(clientes com o usuário titular, colaboradores ativos) — RN-013."""
    _exigir_admin(admin)
    clientes = sessao.execute(
        select(Cliente, Usuario).join(Usuario, Usuario.id == Cliente.usuario_id)
        .where(Usuario.perfil == "CLIENTE").order_by(Cliente.nome)
    ).all()
    colaboradores = sessao.scalars(
        select(Usuario).where(Usuario.perfil == "COLABORADOR", Usuario.ativo.is_(True))
    ).all()
    return [(c, u) for c, u in clientes], list(colaboradores)


def _conta(sessao: Session, alvo_id: str) -> Usuario:
    alvo = sessao.get(Usuario, alvo_id)
    if alvo is None:
        raise ErroAdministracao("Conta não encontrada.")
    return alvo


def _validar_perfil(perfil: str) -> None:
    if perfil not in config.PERFIS:
        raise ErroAdministracao(f"Perfil inválido: {perfil}.")


def _garantir_outro_admin(sessao: Session, alvo: Usuario) -> None:
    """UC-19 — o sistema nunca fica sem um administrador ativo."""
    if alvo.perfil == "ADMIN" and alvo.ativo and admins_ativos(sessao) <= 1:
        raise ErroAdministracao("O sistema precisa de ao menos um administrador ativo.")


def admins_ativos(sessao: Session) -> int:
    return sessao.scalar(
        select(func.count()).select_from(Usuario)
        .where(Usuario.perfil == "ADMIN", Usuario.ativo.is_(True))
    ) or 0


def senha_provisoria() -> str:
    """Senha aleatória que atende à RN-001 (letras e números, 12 caracteres)."""
    alfabeto = string.ascii_letters + string.digits
    while True:
        senha = "".join(secrets.choice(alfabeto) for _ in range(12))
        if not security.validar_politica_senha(senha):
            return senha


def criar_usuario(
    sessao: Session, admin: Usuario, *, email: str, nome: str, perfil: str,
    senha: str | None = None, moeda_base: str = config.MOEDA_BASE_PADRAO,
) -> tuple[Usuario, str]:
    """
    Cria a conta e devolve (usuário, senha inicial). Contas CLIENTE recebem
    cadastro de cliente e carteira padrão, mas o consentimento LGPD fica
    pendente: só o próprio titular pode dá-lo, no primeiro acesso.
    """
    _exigir_admin(admin)
    _validar_perfil(perfil)
    senha = senha or senha_provisoria()
    try:
        email = security.validar_email_novo(sessao, email)
        security.exigir_politica_senha(senha)
    except ValueError as erro:
        raise ErroAdministracao(str(erro)) from erro

    usuario = Usuario(email=email, senha_hash=security.hash_senha(senha), perfil=perfil)
    sessao.add(usuario)
    sessao.flush()
    if perfil == "CLIENTE":
        cliente = Cliente(usuario_id=usuario.id, nome=(nome or email).strip(),
                          moeda_base=moeda_base, consentimento_lgpd=False)
        sessao.add(cliente)
        sessao.flush()
        sessao.add(Carteira(cliente_id=cliente.id, nome=config.NOME_CARTEIRA_PADRAO))
    audit.registrar(sessao, usuario_id=admin.id, acao=audit.CRIAR_USUARIO, entidade="USUARIO",
                    entidade_id=usuario.id,
                    dados_novos={"email": email, "perfil": perfil, "nome": nome})
    sessao.commit()
    return usuario, senha


def alterar_usuario(
    sessao: Session, admin: Usuario, alvo_id: str, *,
    perfil: str, ativo: bool, desbloquear: bool = False,
) -> Usuario:
    _exigir_admin(admin)
    alvo = _conta(sessao, alvo_id)
    _validar_perfil(perfil)
    perde_admin = perfil != "ADMIN" or not ativo
    if alvo.id == admin.id and perde_admin:
        raise ErroAdministracao("Não é possível rebaixar ou desativar a própria conta "
                                "enquanto estiver conectado.")
    if perde_admin:
        _garantir_outro_admin(sessao, alvo)

    anterior = {"perfil": alvo.perfil, "ativo": alvo.ativo,
                "bloqueado_ate": str(alvo.bloqueado_ate) if alvo.bloqueado_ate else None}
    alvo.perfil, alvo.ativo = perfil, ativo
    if desbloquear:
        security.limpar_bloqueio(alvo)
    if not ativo:
        security.revogar_sessoes(sessao, alvo.id)
    audit.registrar(sessao, usuario_id=admin.id, acao=audit.ALTERAR_USUARIO,
                    entidade="USUARIO", entidade_id=alvo.id, dados_anteriores=anterior,
                    dados_novos={"perfil": perfil, "ativo": ativo, "desbloqueio": desbloquear})
    sessao.commit()
    return alvo


def vincular_colaborador(sessao: Session, admin: Usuario, cliente_id: str,
                         colaborador_id: str | None) -> Cliente:
    """RN-013 — define qual colaborador enxerga a carteira do cliente."""
    _exigir_admin(admin)
    cliente = sessao.get(Cliente, cliente_id)
    if cliente is None:
        raise ErroAdministracao("Cliente não encontrado.")
    if colaborador_id:
        colaborador = sessao.get(Usuario, colaborador_id)
        if colaborador is None or colaborador.perfil != "COLABORADOR":
            raise ErroAdministracao("O responsável deve ter perfil COLABORADOR.")
    anterior = cliente.colaborador_id
    cliente.colaborador_id = colaborador_id or None
    audit.registrar(sessao, usuario_id=admin.id, acao=audit.VINCULAR_COLABORADOR,
                    entidade="CLIENTE", entidade_id=cliente.id,
                    dados_anteriores={"colaborador_id": anterior},
                    dados_novos={"colaborador_id": cliente.colaborador_id})
    sessao.commit()
    return cliente


def excluir_usuario(sessao: Session, admin: Usuario, alvo_id: str) -> dict:
    """
    Exclusão pelo administrador. Reutiliza o procedimento LGPD (RN-015):
    dados pessoais e patrimônio removidos, auditoria anonimizada.
    """
    _exigir_admin(admin)
    alvo = _conta(sessao, alvo_id)
    if alvo.id == admin.id:
        raise ErroAdministracao("Use “Privacidade e dados” para excluir a própria conta.")
    _garantir_outro_admin(sessao, alvo)
    email = alvo.email
    contagem = lgpd.excluir(sessao, alvo.id)
    audit.registrar(sessao, usuario_id=admin.id, acao=audit.EXCLUIR_USUARIO, entidade="USUARIO",
                    entidade_id=lgpd.ANONIMO,
                    dados_novos={"dominio_email": email.split("@")[-1], **contagem})
    sessao.commit()
    return contagem
