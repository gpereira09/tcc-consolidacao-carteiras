"""
Atendimento aos direitos do titular — LGPD (RF-027, RF-028, RN-014, RN-015).

Portabilidade (art. 18, V): exporta todos os dados do titular em JSON ou CSV
(pacote .zip). O arquivo recebe um token de uso único (RN-014): ao ser
resgatado, o conteúdo é entregue e o arquivo é apagado do servidor. Arquivos
não resgatados são expurgados depois de 24 horas.

Eliminação (art. 18, VI): apaga os dados pessoais, o patrimônio associado e
as exportações do titular. Registros retidos por obrigação legal — os logs de
auditoria — não são apagados, mas deixam de identificar a pessoa (RN-015):
o vínculo com o titular, os identificadores do cliente e das carteiras e
qualquer ocorrência de e-mail, nome ou CPF nos dados registrados são
substituídos pela constante 'EXCLUIDO_LGPD'.

Ativos listados são cadastro comum e não são apagados; fundos e imóveis
pertencem à carteira do titular e saem com ela, sem afetar outros clientes.
"""
from __future__ import annotations

import csv
import io
import json
import secrets
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

import config
from src import audit, security
from src.models import (
    Ativo,
    AtivoIliquido,
    AtivoImovel,
    AuditLog,
    Carteira,
    Cliente,
    EventoPE,
    ImportacaoLog,
    Posicao,
    Transacao,
    Usuario,
)

ANONIMO = "EXCLUIDO_LGPD"


@dataclass
class Exportacao:
    caminho: Path
    token: str
    expira_em: datetime
    formato: str
    conteudo: bytes | None = None

    @property
    def expirado(self) -> bool:
        return datetime.now(timezone.utc) > self.expira_em


_tokens: dict[str, Exportacao] = {}


def _serializa(valor):
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    return valor


def _dados_ativo(ativo: Ativo | None) -> dict:
    if ativo is None:
        return dict.fromkeys(("ticker", "nome", "classe", "moeda", "mercado"))
    return {"ticker": ativo.ticker_canonico, "nome": ativo.nome, "classe": ativo.classe,
            "moeda": ativo.moeda, "mercado": ativo.mercado}


def coletar_dados(sessao: Session, usuario_id: str) -> dict:
    """Monta o dossiê completo do titular, atravessando todas as entidades."""
    usuario = sessao.get(Usuario, usuario_id)
    if usuario is None:
        raise ValueError("Usuário inexistente.")
    cliente = sessao.scalar(select(Cliente).where(Cliente.usuario_id == usuario_id))

    dossie: dict = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "aviso": (
            "Documento gerado em atendimento ao direito de portabilidade previsto "
            "no art. 18, V, da Lei 13.709/2018 (LGPD)."
        ),
        "usuario": {
            "id": usuario.id,
            "email": usuario.email,
            "perfil": usuario.perfil,
            "data_criacao": _serializa(usuario.data_criacao),
            "ultimo_login": _serializa(usuario.ultimo_login),
            "ativo": usuario.ativo,
        },
        "cliente": None,
        "carteiras": [],
        "auditoria": [],
    }

    if cliente is not None:
        dossie["cliente"] = {
            "id": cliente.id,
            "nome": cliente.nome,
            "cpf": security.decifrar(cliente.cpf_cifrado),
            "perfil_risco": cliente.perfil_risco,
            "moeda_base": cliente.moeda_base,
            "consentimento_lgpd": cliente.consentimento_lgpd,
            "data_consentimento": _serializa(cliente.data_consentimento),
        }

        for carteira in sessao.scalars(
            select(Carteira).where(Carteira.cliente_id == cliente.id)
        ).all():
            bloco = {
                "id": carteira.id,
                "nome": carteira.nome,
                "data_criacao": _serializa(carteira.data_criacao),
                "posicoes": [],
                "importacoes": [],
            }
            for pos in sessao.scalars(
                select(Posicao).where(Posicao.carteira_id == carteira.id)
            ).all():
                ativo = sessao.get(Ativo, pos.ativo_id)
                item = {
                    **_dados_ativo(ativo),
                    "quantidade": pos.quantidade,
                    "preco_medio": pos.preco_medio,
                    "valor_financeiro": pos.valor_financeiro,
                    "data_inicial": _serializa(pos.data_inicial),
                    "observacao": pos.observacao,
                    "transacoes": [
                        {
                            "tipo": t.tipo, "data": _serializa(t.data),
                            "quantidade": t.quantidade, "preco": t.preco, "taxas": t.taxas,
                        }
                        for t in sessao.scalars(
                            select(Transacao).where(Transacao.posicao_id == pos.id)
                            .order_by(Transacao.data)
                        ).all()
                    ],
                }

                fundo = sessao.scalar(
                    select(AtivoIliquido).where(AtivoIliquido.ativo_id == pos.ativo_id)
                )
                if fundo:
                    item["fundo_iliquido"] = {
                        "identificador": fundo.identificador_fundo,
                        "gestor": fundo.gestor,
                        "sub_classe": fundo.sub_classe,
                        "commitment": fundo.commitment,
                        "paid_in_capital": fundo.paid_in_capital,
                        "dry_powder": fundo.dry_powder,
                        "nav_atual": fundo.nav_atual,
                        "data_inicio": _serializa(fundo.data_inicio),
                        "eventos": [
                            {
                                "data": _serializa(e.data_evento), "tipo": e.tipo_evento,
                                "valor": e.valor, "status": e.status,
                                "nav_unidade": e.nav_unidade, "observacao": e.observacao,
                            }
                            for e in sessao.scalars(
                                select(EventoPE).where(EventoPE.fundo_id == fundo.id)
                                .order_by(EventoPE.data_evento)
                            ).all()
                        ],
                    }

                imovel = sessao.scalar(
                    select(AtivoImovel).where(AtivoImovel.ativo_id == pos.ativo_id)
                )
                if imovel:
                    item["imovel"] = {
                        "identificador": imovel.identificador_imovel,
                        "tipo": imovel.tipo_imovel,
                        "endereco": imovel.endereco,
                        "area_m2": imovel.area_m2,
                        "valor_aquisicao": imovel.valor_aquisicao,
                        "valor_de_mercado": imovel.valor_de_mercado,
                        "aluguel_mensal": imovel.aluguel_mensal,
                        "despesas_mensais": imovel.despesas_mensais,
                        "data_avaliacao": _serializa(imovel.data_avaliacao),
                    }
                bloco["posicoes"].append(item)

            bloco["importacoes"] = [
                {
                    "arquivo": i.nome_arquivo, "template": i.tipo_template,
                    "data_hora": _serializa(i.data_hora),
                    "lidas": i.linhas_lidas, "importadas": i.linhas_importadas,
                    "rejeitadas": i.linhas_rejeitadas,
                }
                for i in sessao.scalars(
                    select(ImportacaoLog).where(ImportacaoLog.carteira_id == carteira.id)
                ).all()
            ]
            dossie["carteiras"].append(bloco)

    dossie["auditoria"] = [
        {
            "data_hora": _serializa(a.data_hora), "acao": a.acao,
            "entidade": a.entidade, "entidade_id": a.entidade_id,
        }
        for a in sessao.scalars(
            select(AuditLog).where(AuditLog.usuario_id == usuario_id)
            .order_by(AuditLog.data_hora)
        ).all()
    ]
    return dossie


def _csv(cabecalho: list[str], linhas) -> str:
    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";")
    escritor.writerow(cabecalho)
    escritor.writerows(linhas)
    return buffer.getvalue()


def _csv_de_posicoes(dossie: dict) -> str:
    return _csv(
        ["carteira", "ticker", "nome", "classe", "moeda", "mercado",
         "quantidade", "preco_medio", "valor_financeiro", "data_inicial", "observacao"],
        (
            [carteira["nome"], pos["ticker"], pos["nome"], pos["classe"],
             pos["moeda"], pos["mercado"], pos["quantidade"], pos["preco_medio"],
             pos["valor_financeiro"], pos["data_inicial"], pos["observacao"] or ""]
            for carteira in dossie["carteiras"]
            for pos in carteira["posicoes"]
        ),
    )


def _csv_de_eventos(dossie: dict) -> str:
    return _csv(
        ["carteira", "fundo", "gestor", "data", "tipo", "valor", "status"],
        (
            [carteira["nome"], fundo["identificador"], fundo["gestor"],
             e["data"], e["tipo"], e["valor"], e["status"]]
            for carteira in dossie["carteiras"]
            for pos in carteira["posicoes"]
            if (fundo := pos.get("fundo_iliquido"))
            for e in fundo["eventos"]
        ),
    )


def exportar(
    sessao: Session, usuario_id: str, formato: str = "JSON",
    solicitante_id: str | None = None,
) -> Exportacao:
    """
    RF-027 / RN-014 — gera o pacote de portabilidade com link de uso único
    válido por 24 horas. A reautenticação exigida pela RN-014 é feita na
    camada de apresentação, antes de chamar esta função.
    """
    formato = formato.upper()
    if formato not in ("JSON", "CSV"):
        raise ValueError("Formato deve ser JSON ou CSV.")

    expurgar_exportacoes()
    dossie = coletar_dados(sessao, usuario_id)
    agora = datetime.now(timezone.utc)
    carimbo = agora.strftime("%Y%m%d_%H%M%S_%f")
    token = secrets.token_urlsafe(24)
    nome_base = f"lgpd_{usuario_id}_{carimbo}"
    texto_json = json.dumps(dossie, ensure_ascii=False, indent=2)

    if formato == "JSON":
        caminho = config.EXPORT_DIR / f"{nome_base}.json"
        caminho.write_text(texto_json, encoding="utf-8")
    else:
        caminho = config.EXPORT_DIR / f"{nome_base}.zip"
        with zipfile.ZipFile(caminho, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("posicoes.csv", _csv_de_posicoes(dossie))
            z.writestr("eventos_pe.csv", _csv_de_eventos(dossie))
            z.writestr("dossie_completo.json", texto_json)

    exportacao = Exportacao(
        caminho=caminho,
        token=token,
        expira_em=agora + timedelta(hours=config.EXPORT_VALIDADE_HORAS),
        formato=formato,
    )
    _tokens[token] = exportacao

    audit.registrar(
        sessao, usuario_id=solicitante_id or usuario_id, acao=audit.EXPORTAR_LGPD,
        entidade="USUARIO", entidade_id=usuario_id,
        dados_novos={"formato": formato, "arquivo": caminho.name},
    )
    sessao.commit()
    return exportacao


def resgatar(token: str) -> Exportacao | None:
    """
    Consome o link de uso único (RN-014): devolve o conteúdo e apaga o
    arquivo do servidor, de modo que o dossiê não fica armazenado.
    """
    exportacao = _tokens.pop(token, None)
    if exportacao is None:
        return None
    if not exportacao.expirado and exportacao.caminho.exists():
        exportacao.conteudo = exportacao.caminho.read_bytes()
    exportacao.caminho.unlink(missing_ok=True)
    return exportacao if exportacao.conteudo is not None else None


def expurgar_exportacoes(agora: datetime | None = None) -> int:
    """Apaga dossiês não resgatados há mais de 24 horas. Devolve quantos apagou."""
    limite = (agora or datetime.now(timezone.utc)).timestamp() \
        - config.EXPORT_VALIDADE_HORAS * 3600
    apagados = 0
    for arquivo in config.EXPORT_DIR.glob("lgpd_*"):
        if arquivo.stat().st_mtime < limite:
            arquivo.unlink(missing_ok=True)
            apagados += 1
    return apagados


def _anonimizar_auditoria(sessao: Session, identificadores: set[str],
                          textos: set[str]) -> int:
    """RN-015 — a trilha é mantida, sem nada que identifique o titular."""
    alterados = sessao.execute(
        update(AuditLog).where(AuditLog.usuario_id.in_(identificadores))
        .values(usuario_id=ANONIMO)
    ).rowcount or 0
    sessao.execute(
        update(AuditLog).where(AuditLog.entidade_id.in_(identificadores))
        .values(entidade_id=ANONIMO)
    )
    for texto in (t for t in textos | identificadores if t and len(t) >= 3):
        for coluna in (AuditLog.dados_novos, AuditLog.dados_anteriores):
            sessao.execute(
                update(AuditLog).where(coluna.contains(texto))
                .values({coluna: func.replace(coluna, texto, ANONIMO)})
            )
    return alterados


def excluir(sessao: Session, usuario_id: str) -> dict:
    """
    RF-028 / RN-015 — elimina os dados pessoais e anonimiza o que a lei
    obriga a reter. Executa em transação única.
    """
    usuario = sessao.get(Usuario, usuario_id)
    if usuario is None:
        raise ValueError("Usuário inexistente.")

    cliente = sessao.scalar(select(Cliente).where(Cliente.usuario_id == usuario_id))
    contagem = {"carteiras": 0, "posicoes": 0, "eventos": 0, "logs_anonimizados": 0}
    identificadores = {usuario_id}
    textos = {usuario.email}

    if cliente is not None:
        identificadores.add(cliente.id)
        textos |= {cliente.nome, security.decifrar(cliente.cpf_cifrado)}
        carteiras = sessao.scalars(
            select(Carteira).where(Carteira.cliente_id == cliente.id)
        ).all()
        contagem["carteiras"] = len(carteiras)

        for carteira in carteiras:
            identificadores.add(carteira.id)
            contagem["posicoes"] += len(sessao.scalars(
                select(Posicao.id).where(Posicao.carteira_id == carteira.id)).all())

            for ativo in sessao.scalars(
                select(Ativo).where(Ativo.carteira_id == carteira.id)
            ).all():
                if ativo.iliquido is not None:
                    contagem["eventos"] += len(ativo.iliquido.eventos)
                sessao.delete(ativo)

            sessao.execute(
                ImportacaoLog.__table__.delete().where(
                    ImportacaoLog.carteira_id == carteira.id
                )
            )
        sessao.flush()
        sessao.delete(cliente)

    for arquivo in config.EXPORT_DIR.glob(f"lgpd_{usuario_id}_*"):
        arquivo.unlink(missing_ok=True)
    contagem["logs_anonimizados"] = _anonimizar_auditoria(sessao, identificadores, textos)

    audit.registrar(
        sessao, usuario_id=ANONIMO, acao=audit.EXCLUIR_LGPD,
        entidade="USUARIO", entidade_id=ANONIMO, dados_novos=contagem,
    )
    sessao.delete(usuario)
    sessao.commit()
    return contagem
