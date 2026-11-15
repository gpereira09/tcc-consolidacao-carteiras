"""Registro de auditoria de ações sensíveis (RF-026)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import AuditLog

LOGIN = "LOGIN"
LOGIN_FALHA = "LOGIN_FALHA"
CRIAR_CONTA = "CRIAR_CONTA"
IMPORTAR_TEMPLATE = "IMPORTAR_TEMPLATE"
REGISTRAR_EVENTO_PE = "REGISTRAR_EVENTO_PE"
ATUALIZAR_NAV = "ATUALIZAR_NAV"
EXPORTAR_LGPD = "EXPORTAR_LGPD"
EXCLUIR_LGPD = "EXCLUIR_LGPD"
ALTERAR_USUARIO = "ALTERAR_USUARIO"
REDEFINIR_SENHA = "REDEFINIR_SENHA"
CONSENTIMENTO_LGPD = "CONSENTIMENTO_LGPD"
CORRIGIR_DADOS = "CORRIGIR_DADOS"
ATIVAR_2FA = "ATIVAR_2FA"
DESATIVAR_2FA = "DESATIVAR_2FA"
CADASTRAR_ATIVO = "CADASTRAR_ATIVO"
REGISTRAR_TRANSACAO = "REGISTRAR_TRANSACAO"
CADASTRAR_FUNDO = "CADASTRAR_FUNDO"
REAVALIAR_IMOVEL = "REAVALIAR_IMOVEL"
CRIAR_CARTEIRA = "CRIAR_CARTEIRA"
CRIAR_USUARIO = "CRIAR_USUARIO"
EXCLUIR_USUARIO = "EXCLUIR_USUARIO"
VINCULAR_COLABORADOR = "VINCULAR_COLABORADOR"
COLETA_AGENDADA = "COLETA_AGENDADA"
BACKUP_AGENDADO = "BACKUP_AGENDADO"

ACOES = (
    LOGIN, LOGIN_FALHA, CRIAR_CONTA, REDEFINIR_SENHA, CONSENTIMENTO_LGPD, CORRIGIR_DADOS,
    ATIVAR_2FA, DESATIVAR_2FA, IMPORTAR_TEMPLATE, CADASTRAR_ATIVO,
    REGISTRAR_TRANSACAO, CADASTRAR_FUNDO, REAVALIAR_IMOVEL, REGISTRAR_EVENTO_PE, ATUALIZAR_NAV,
    CRIAR_CARTEIRA, CRIAR_USUARIO, ALTERAR_USUARIO, EXCLUIR_USUARIO,
    VINCULAR_COLABORADOR, EXPORTAR_LGPD, EXCLUIR_LGPD, COLETA_AGENDADA,
    BACKUP_AGENDADO,
)


def consultar(sessao: Session, desde: datetime, acoes: list[str] | None = None,
              limite: int = 500) -> list[AuditLog]:
    """Registros a partir de `desde`, do mais recente ao mais antigo (UC-20)."""
    stmt = select(AuditLog).where(AuditLog.data_hora >= desde)
    if acoes:
        stmt = stmt.where(AuditLog.acao.in_(acoes))
    return list(sessao.scalars(stmt.order_by(AuditLog.data_hora.desc()).limit(limite)).all())


def _serializa(dados: Any) -> str | None:
    if dados is None:
        return None
    return json.dumps(dados, ensure_ascii=False, default=str)


def registrar(
    sessao: Session,
    *,
    usuario_id: str | None,
    acao: str,
    entidade: str | None = None,
    entidade_id: str | None = None,
    dados_anteriores: Any = None,
    dados_novos: Any = None,
) -> AuditLog:
    log = AuditLog(
        usuario_id=usuario_id,
        acao=acao,
        entidade=entidade,
        entidade_id=entidade_id,
        dados_anteriores=_serializa(dados_anteriores),
        dados_novos=_serializa(dados_novos),
    )
    sessao.add(log)
    return log
