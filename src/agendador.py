"""
Tarefas agendadas (RF-013, RNF-006, RNF-008).

  * Coleta diária de preços dos ativos listados, da PTAX (BCB) e dos benchmarks, às
    19h30 de Brasília — dentro da janela de 19h a 22h do RNF-006, após o
    fechamento do pregão da B3 e dos mercados americanos.
  * Backup diário do banco às 22h30 (RNF-008).

O agendador (APScheduler, em thread de fundo) é iniciado uma única vez pela
aplicação. Para quem prefere o agendador do sistema operacional, as mesmas
tarefas estão em `scripts/tarefas_diarias.py`, pronto para cron ou para o
Agendador de Tarefas do Windows. Falhas de rede não interrompem a tarefa:
cada ativo mantém o último preço conhecido (seção 5.8).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select

import config
from src import audit
from src.db import SessionLocal
from src.db import sessao as sessao_transacional
from src.models import Ativo

log = logging.getLogger("carteira.agendador")


@dataclass
class ResumoColeta:
    inicio: datetime
    fim: datetime | None = None
    ativos: int = 0
    atualizados: int = 0
    falhas: list[str] = field(default_factory=list)
    cambio: dict[str, int] = field(default_factory=dict)
    benchmarks: dict[str, int] = field(default_factory=dict)


def coletar_precos(coletor_cambio=None, coletor_benchmarks=None) -> ResumoColeta:
    """RF-013 — atualiza preços dos ativos listados, a PTAX e os benchmarks (RF-018)."""
    from src.market import benchmarks, cambio, precos

    resumo = ResumoColeta(inicio=datetime.now())
    sessao = SessionLocal()
    try:
        ativos = sessao.scalars(select(Ativo).where(Ativo.possui_ticker_listado.is_(True))).all()
        resumo.ativos = len(ativos)
        for ativo in ativos:
            r = precos.atualizar_ativo(sessao, ativo, forcar=True)
            if r.sucesso and r.mensagem == "coletado":
                resumo.atualizados += 1
            elif not r.sucesso:
                resumo.falhas.append(ativo.ticker_canonico)
        sessao.commit()

        coletor = coletor_cambio or cambio.atualizar_bcb
        inicio = date.today() - timedelta(days=7)
        for moeda in (m for m in config.MOEDAS if m != "BRL"):
            resumo.cambio[moeda] = coletor(sessao, moeda, inicio)
        resumo.benchmarks = (coletor_benchmarks or benchmarks.atualizar)(
            sessao, config.INICIO_SERIES_MERCADO)

        resumo.fim = datetime.now()
        audit.registrar(sessao, usuario_id=None, acao=audit.COLETA_AGENDADA, entidade="PRECO_HISTORICO",
                        dados_novos={"ativos": resumo.ativos, "atualizados": resumo.atualizados,
                                     "falhas": resumo.falhas[:50], "cambio": resumo.cambio,
                                     "benchmarks": resumo.benchmarks})
        sessao.commit()
    finally:
        sessao.close()
    log.info("Coleta diária: %s/%s ativos atualizados", resumo.atualizados, resumo.ativos)
    return resumo


def backup_diario():
    """RNF-008 — cópia diária do banco; também expurga dossiês LGPD vencidos (RN-014)."""
    from src import backup, lgpd

    caminho = backup.executar_backup()
    expurgados = lgpd.expurgar_exportacoes()
    with sessao_transacional() as sessao:
        audit.registrar(sessao, usuario_id=None, acao=audit.BACKUP_AGENDADO,
                        dados_novos={"arquivo": caminho.name,
                                     "exportacoes_expurgadas": expurgados})
    return caminho


_agendador = None


def iniciar():
    """Inicia o agendador em segundo plano (idempotente)."""
    global _agendador
    if _agendador is not None:
        return _agendador
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.warning("APScheduler não instalado: tarefas diárias desativadas.")
        return None

    ag = BackgroundScheduler(timezone=config.FUSO, daemon=True)
    ag.add_job(coletar_precos, CronTrigger(hour=config.COLETA_HORA, minute=config.COLETA_MINUTO,
                                           timezone=config.FUSO),
               id="coleta_precos", replace_existing=True, misfire_grace_time=2 * 3600,
               coalesce=True, max_instances=1)
    ag.add_job(backup_diario, CronTrigger(hour=config.BACKUP_HORA, minute=config.BACKUP_MINUTO,
                                          timezone=config.FUSO),
               id="backup_diario", replace_existing=True, misfire_grace_time=6 * 3600,
               coalesce=True, max_instances=1)
    ag.start()
    _agendador = ag
    return ag


def proximas_execucoes() -> dict[str, datetime | None]:
    if _agendador is None:
        return {}
    return {job.id: job.next_run_time for job in _agendador.get_jobs()}

