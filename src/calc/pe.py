"""
Métricas de ativos ilíquidos — PE / VC / RE (seção 2.4.2).

Esta é a ÚNICA implementação das regras de fundo ilíquido: a consolidação,
a ingestão por template e os lançamentos manuais chamam estas funções.

Definições (todas apuradas até a data de corte `dia`, quando informada):
    paid_in       = Σ capital calls com status PAGO
    pendente      = Σ capital calls com status PENDENTE
    dry_powder    = commitment − paid_in − pendente
    distribuições = Σ eventos DISTRIBUICAO com status RECEBIDO
    NAV           = último NAV_UPDATE, ajustado pelos fluxos posteriores a ele
                    (roll-forward: + calls pagas − distribuições após a data
                    do NAV). Sem NAV informado: paid_in − distribuições.

    DPI  = distribuições / paid_in
    RVPI = NAV / paid_in
    TVPI = (distribuições + NAV) / paid_in        (MOIC é sinônimo)

O ajuste do NAV evita a dupla contagem que ocorria quando um call era pago
(ou uma distribuição recebida) depois da última marcação do gestor: o caixa
já entrou ou saiu do fundo, mas o NAV antigo ainda não o refletia. Um NAV
na data d é tratado como posição de fim de dia: eventos da mesma data já
estão nele.

Custo líquido (base do "resultado" da consolidação): paid_in menos as
distribuições classificadas como RETORNO_CAPITAL (RF-012). Ganhos de capital
e rendimentos não reduzem o custo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from src.calc.rentabilidade import irr, irr_ambigua
from src.formatacao import numero
from src.models import AtivoIliquido, EventoPE, Posicao


@dataclass
class MetricasPE:
    identificador: str
    nome: str
    gestor: str
    sub_classe: str
    moeda: str
    commitment: float
    paid_in: float
    pendente: float
    dry_powder: float
    distribuicoes: float
    nav: float
    data_nav: date | None
    dpi: float
    rvpi: float
    tvpi: float
    moic: float
    irr: float | None
    percentual_chamado: float
    nav_ajustado: bool = False
    irr_ambigua: bool = False
    movimentos_custo: list[tuple[date, float]] = field(default_factory=list)

    @property
    def custo_liquido(self) -> float:
        return sum(v for _, v in self.movimentos_custo)


def _eventos_ordenados(fundo: AtivoIliquido, sessao: Session | None) -> list[EventoPE]:
    if sessao is not None:
        eventos = list(
            sessao.scalars(
                select(EventoPE)
                .where(EventoPE.fundo_id == fundo.id)
                .order_by(EventoPE.data_evento, EventoPE.id)
            ).all()
        )
    else:
        eventos = sorted(fundo.eventos, key=lambda e: (e.data_evento, e.id))
    return [e for e in eventos if e.status != "CANCELADO"]


def _chamada_paga(e: EventoPE) -> bool:
    return e.tipo_evento == "CAPITAL_CALL" and e.status == "PAGO"


def _chamada_pendente(e: EventoPE) -> bool:
    return e.tipo_evento == "CAPITAL_CALL" and e.status == "PENDENTE"


def _distribuicao_recebida(e: EventoPE) -> bool:
    return e.tipo_evento == "DISTRIBUICAO" and e.status == "RECEBIDO"


def calcular(fundo: AtivoIliquido, sessao: Session | None = None,
             dia: date | None = None) -> MetricasPE:
    """Métricas do fundo com os eventos até `dia` (todos, se omitido)."""
    eventos = [e for e in _eventos_ordenados(fundo, sessao)
               if dia is None or e.data_evento <= dia]

    paid_in = sum(e.valor for e in eventos if _chamada_paga(e))
    pendente = sum(e.valor for e in eventos if _chamada_pendente(e))
    distribuicoes = sum(e.valor for e in eventos if _distribuicao_recebida(e))

    navs = [e for e in eventos if e.tipo_evento == "NAV_UPDATE"]
    if navs:
        ultimo = navs[-1]
        data_nav = ultimo.data_evento
        posteriores = [e for e in eventos if e.data_evento > data_nav]
        chamado_depois = sum(e.valor for e in posteriores if _chamada_paga(e))
        distribuido_depois = sum(e.valor for e in posteriores if _distribuicao_recebida(e))
        nav = max(ultimo.valor + chamado_depois - distribuido_depois, 0.0)
        nav_ajustado = bool(chamado_depois or distribuido_depois)
    else:
        nav, data_nav, nav_ajustado = max(paid_in - distribuicoes, 0.0), None, False

    dry_powder = max(fundo.commitment - paid_in - pendente, 0.0)

    if paid_in > 0:
        dpi = distribuicoes / paid_in
        rvpi = nav / paid_in
        tvpi = (distribuicoes + nav) / paid_in
    else:
        dpi = rvpi = tvpi = 0.0

    fluxos: list[tuple[date, float]] = [
        (e.data_evento, -e.valor if _chamada_paga(e) else e.valor)
        for e in eventos if _chamada_paga(e) or _distribuicao_recebida(e)
    ]
    if fluxos and nav > 0:
        data_final = max([d for d, _ in fluxos] + ([data_nav] if data_nav else []))
        fluxos.append((data_final, nav))
    taxa = irr(fluxos)

    movimentos_custo = [(e.data_evento, e.valor) for e in eventos if _chamada_paga(e)]
    movimentos_custo += [(e.data_evento, -e.valor) for e in eventos
                         if _distribuicao_recebida(e) and e.subtipo == "RETORNO_CAPITAL"]

    return MetricasPE(
        identificador=fundo.identificador_fundo,
        nome=fundo.ativo.nome if fundo.ativo else fundo.identificador_fundo,
        gestor=fundo.gestor,
        sub_classe=fundo.sub_classe,
        moeda=fundo.ativo.moeda if fundo.ativo else config.MOEDA_BASE_PADRAO,
        commitment=fundo.commitment,
        paid_in=paid_in,
        pendente=pendente,
        dry_powder=dry_powder,
        distribuicoes=distribuicoes,
        nav=nav,
        data_nav=data_nav,
        dpi=dpi,
        rvpi=rvpi,
        tvpi=tvpi,
        moic=tvpi,
        irr=taxa,
        percentual_chamado=(paid_in / fundo.commitment) if fundo.commitment else 0.0,
        nav_ajustado=nav_ajustado,
        irr_ambigua=taxa is not None and irr_ambigua(fluxos),
        movimentos_custo=sorted(movimentos_custo),
    )


def eventos(fundo: AtivoIliquido, sessao: Session | None = None) -> list[EventoPE]:
    """Eventos do fundo, inclusive cancelados, do mais recente ao mais antigo."""
    if sessao is None:
        return sorted(fundo.eventos, key=lambda e: (e.data_evento, e.id), reverse=True)
    return list(sessao.scalars(
        select(EventoPE).where(EventoPE.fundo_id == fundo.id)
        .order_by(EventoPE.data_evento.desc(), EventoPE.id.desc())
    ).all())


def curva_j(fundo: AtivoIliquido, sessao: Session | None = None) -> list[tuple[date, float]]:
    """
    Fluxo acumulado do investidor — o desenho clássico da curva J de PE:
    chamadas pagas negativas, distribuições positivas e, no último ponto, o
    valor residual pelo NAV (ajustado). Vazia com menos de dois fluxos.
    """
    pontos: list[tuple[date, float]] = []
    acumulado = 0.0
    for e in _eventos_ordenados(fundo, sessao):
        if _chamada_paga(e) or _distribuicao_recebida(e):
            acumulado += -e.valor if _chamada_paga(e) else e.valor
            pontos.append((e.data_evento, acumulado))
    if len(pontos) < 2:
        return []
    m = calcular(fundo, sessao)
    if m.data_nav:
        pontos.append((max(m.data_nav, pontos[-1][0]), acumulado + m.nav))
    return pontos


def idade_anos(fundo: AtivoIliquido, dia: date) -> float:
    return (dia - fundo.data_inicio).days / 365.25


def recalcular_metricas(sessao: Session, fundo: AtivoIliquido) -> MetricasPE:
    """
    RN-012 — materializa paid_in, dry_powder e NAV no registro do fundo e
    reflete o NAV na posição correspondente da carteira.
    """
    m = calcular(fundo, sessao)
    fundo.paid_in_capital = m.paid_in
    fundo.dry_powder = m.dry_powder
    fundo.nav_atual = m.nav

    posicoes = sessao.scalars(
        select(Posicao).where(Posicao.ativo_id == fundo.ativo_id)
    ).all()
    for pos in posicoes:
        pos.valor_financeiro = m.nav
        pos.preco_medio = m.paid_in
    sessao.flush()
    return m


def checar_commitment(commitment: float, comprometido: float, valor: float,
                      identificador: str = "") -> None:
    """
    RN-004 — chamadas pagas + pendentes + a nova chamada não podem exceder o
    commitment, qualquer que seja o status da nova chamada. Uma chamada
    pendente já reserva capital: sem contá-la, o dry powder poderia ficar
    negativo quando ela fosse paga.
    """
    if valor <= 0:
        raise ValueError("O valor da chamada de capital deve ser positivo.")
    if comprometido + valor > commitment + config.TOLERANCIA_COMMITMENT:
        disponivel = max(commitment - comprometido, 0.0)
        fundo = f" do fundo {identificador}" if identificador else ""
        raise ValueError(
            f"RN-004: chamada de {numero(valor)} excede o commitment de "
            f"{numero(commitment)}{fundo} (já chamado, pago ou pendente: "
            f"{numero(comprometido)}). Disponível para chamada: {numero(disponivel)}."
        )


def checar_distribuicao(pago: float, valor: float, identificador: str = "") -> None:
    """RN-005 — distribuição exige capital integralizado."""
    if valor <= 0:
        raise ValueError("O valor da distribuição deve ser positivo.")
    if pago <= 0:
        fundo = f" {identificador}" if identificador else ""
        raise ValueError(
            f"RN-005: não é possível registrar distribuição no fundo{fundo} "
            "sem capital integralizado."
        )


def validar_capital_call(fundo: AtivoIliquido, valor: float,
                         sessao: Session | None = None) -> None:
    m = calcular(fundo, sessao)
    checar_commitment(fundo.commitment, m.paid_in + m.pendente, valor)


def validar_distribuicao(fundo: AtivoIliquido, valor: float,
                         sessao: Session | None = None) -> None:
    checar_distribuicao(calcular(fundo, sessao).paid_in, valor)
