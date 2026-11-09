"""
Consolidação patrimonial (RF-014, RF-019 a RF-022).

Reúne posições líquidas, fundos ilíquidos e imóveis em uma visão única na
moeda base do cliente. Toda posição é avaliada por uma regra só, conforme
sua natureza:

    listado    → quantidade × último preço conhecido (RN-006)
    PE/VC      → último NAV até a data, ajustado pelos fluxos posteriores
                 a ele (src.calc.pe, seção 2.4.2)
    imóvel     → última avaliação de mercado até a data
    não listado→ quantidade × preço médio do template (RN-007)

Conversão para a moeda base (RN-009), com forward fill:
    valor de mercado → taxa da data de avaliação;
    custo            → taxa da data de cada aquisição (custo histórico), de
                       modo que o "resultado" inclui a variação cambial.
Valores sem cotação de câmbio NÃO entram nos totais nem nos fluxos: são
excluídos e geram alerta, em vez de serem somados na moeda errada.
"""
from __future__ import annotations

from bisect import bisect_right
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

import config
from src.calc import pe as calc_pe
from src.calc import rentabilidade as calc_rent
from src.market import benchmarks as bmk_srv
from src.market import cambio
from src.models import (
    Ativo,
    AtivoIliquido,
    AtivoImovel,
    AuditLog,
    BenchmarkValor,
    Carteira,
    Cliente,
    EventoPE,
    Posicao,
    PrecoHistorico,
    TaxaCambio,
    Transacao,
)

CLASSES_ROTULO = {
    "ACAO": "Ações",
    "ETF": "ETFs",
    "FUNDO": "Fundos",
    "TESOURO_DIRETO": "Tesouro Direto",
    "FII": "FIIs",
    "RENDA_FIXA": "Renda Fixa",
    "PRIVATE_EQUITY": "Private Equity",
    "VENTURE_CAPITAL": "Venture Capital",
    "REAL_ESTATE": "Real Estate (fundos)",
    "IMOVEL": "Imóveis diretos",
}

def macro_classe(classe: str, regiao: str) -> str:
    if classe in config.SUBCLASSES_ILIQUIDAS:
        return "Alternativos (PE/VC/RE)"
    if classe == config.CLASSE_IMOVEL:
        return "Imóveis"
    if regiao == "Offshore":
        return "Internacional"
    if classe in config.CLASSES_RENDA_VARIAVEL:
        return "Renda Variável"
    if classe in ("RENDA_FIXA", "TESOURO_DIRETO"):
        return "Renda Fixa"
    return "Fundos"


def pais_de(ativo: Ativo) -> str:
    """País de exposição; bases antigas gravavam só BRASIL/EXTERIOR."""
    if ativo.pais and ativo.pais not in (config.PAIS_EXTERIOR, ""):
        return ativo.pais
    if ativo.mercado in config.PAIS_POR_MERCADO:
        return config.PAIS_POR_MERCADO[ativo.mercado]
    if ativo.pais == config.PAIS_EXTERIOR and ativo.moeda == "BRL":
        return config.PAIS_EXTERIOR
    return config.PAIS_POR_MOEDA.get(ativo.moeda, ativo.pais or config.PAIS_EXTERIOR)


def rotulo_pais(pais: str) -> str:
    return " ".join(p if p in ("do", "da", "de", "dos") else p.capitalize()
                    for p in pais.lower().split())


CLASSES_ILIQUIDAS = (*config.SUBCLASSES_ILIQUIDAS, config.CLASSE_IMOVEL)


@dataclass
class LinhaPosicao:
    posicao_id: str
    ativo_id: str
    ticker: str
    nome: str
    classe: str
    classe_rotulo: str
    liquidez: str
    pais: str
    moeda: str
    quantidade: float
    preco_unitario: float
    valor_local: float
    valor_base: float
    custo_base: float
    convertido: bool
    regiao: str = "Brasil"
    macro_classe: str = ""
    setor: str = ""

    @property
    def resultado(self) -> float:
        return self.valor_base - self.custo_base

    @property
    def retorno(self) -> float:
        return (self.valor_base / self.custo_base - 1.0) if self.custo_base > 0 else 0.0


@dataclass
class Snapshot:
    data: date
    moeda_base: str
    linhas: list[LinhaPosicao] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)

    @property
    def patrimonio(self) -> float:
        return sum(l.valor_base for l in self.linhas)

    @property
    def custo(self) -> float:
        return sum(l.custo_base for l in self.linhas)

    @property
    def resultado(self) -> float:
        return self.patrimonio - self.custo

    def por(self, atributo: str) -> dict[str, float]:
        saida: dict[str, float] = {}
        for linha in self.linhas:
            chave = getattr(linha, atributo)
            saida[chave] = saida.get(chave, 0.0) + linha.valor_base
        return dict(sorted(saida.items(), key=lambda kv: kv[1], reverse=True))


@dataclass
class Rentabilidade:
    inicio: date
    fim: date
    patrimonio_inicial: float
    patrimonio_final: float
    aportes: float
    resgates: float
    retorno_total: float | None
    twr: float
    twr_anualizado: float
    mwr: float | None
    volatilidade: float
    max_drawdown: float
    sharpe: float | None
    serie: list[tuple[date, float]]
    comparativos: list[bmk_srv.ComparativoBenchmark]
    cota: list[tuple[date, float]] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)


class _Leitor:
    """
    Leituras usadas na avaliação, memorizadas durante um cálculo: cadastro do
    ativo, do fundo e do imóvel, transações, histórico de preços e taxas de
    câmbio. Uma série mensal avalia a mesma posição em dezenas de datas; sem
    a memorização, cada data repetia as mesmas consultas (com 100 posições,
    mais de 40 s para a série desde o início — RNF-003 pede menos de 3 s).
    O "último preço até a data" é localizado por bisseção sobre o histórico
    já carregado, com o mesmo resultado da consulta `data <= dia`.
    """

    def __init__(self, sessao: Session) -> None:
        self.sessao = sessao
        self._memo: dict[tuple, object] = {}

    def _lembrar(self, chave: tuple, carregar):
        if chave not in self._memo:
            self._memo[chave] = carregar()
        return self._memo[chave]

    def ativo(self, ativo_id: str) -> Ativo | None:
        return self._lembrar(("ativo", ativo_id), lambda: self.sessao.get(Ativo, ativo_id))

    def fundo(self, ativo_id: str) -> AtivoIliquido | None:
        return self._lembrar(("fundo", ativo_id), lambda: self.sessao.scalar(
            select(AtivoIliquido).where(AtivoIliquido.ativo_id == ativo_id)))

    def imovel(self, ativo_id: str) -> AtivoImovel | None:
        return self._lembrar(("imovel", ativo_id), lambda: self.sessao.scalar(
            select(AtivoImovel).where(AtivoImovel.ativo_id == ativo_id)))

    def transacoes(self, posicao_id: str) -> list[Transacao]:
        return self._lembrar(("transacoes", posicao_id), lambda: list(self.sessao.scalars(
            select(Transacao).where(Transacao.posicao_id == posicao_id)
            .order_by(Transacao.data, case((Transacao.tipo == "COMPRA", 0), else_=1),
                      Transacao.id)).all()))

    def preco_ate(self, ativo_id: str, dia: date) -> float | None:
        if self._memo.setdefault(("primeira_data", ativo_id), dia) == dia                 and ("precos", ativo_id) not in self._memo:
            return self._lembrar(("preco", ativo_id, dia), lambda: self.sessao.scalar(
                select(PrecoHistorico.preco_fechamento)
                .where(PrecoHistorico.ativo_id == ativo_id, PrecoHistorico.data <= dia)
                .order_by(PrecoHistorico.data.desc()).limit(1)))
        datas, valores = self._lembrar(("precos", ativo_id), lambda: _colunas(
            self.sessao.execute(
                select(PrecoHistorico.data, PrecoHistorico.preco_fechamento)
                .where(PrecoHistorico.ativo_id == ativo_id).order_by(PrecoHistorico.data)
            ).all()))
        i = bisect_right(datas, dia)
        return valores[i - 1] if i else None

    def converter(self, valor: float, origem: str, destino: str, dia: date) -> tuple[float, bool]:
        """Como cambio.converter_seguro, memorizando a taxa de cada (par, data)."""
        if valor == 0 or origem == destino:
            return valor, True

        def taxa():
            try:
                return cambio.obter_taxa(self.sessao, origem, destino, dia)
            except cambio.TaxaIndisponivel:
                return None

        t = self._lembrar(("taxa", origem, destino, dia), taxa)
        return (valor * t, True) if t is not None else (valor, False)


def _colunas(linhas) -> tuple[list, list]:
    return [l[0] for l in linhas], [l[1] for l in linhas]


@dataclass
class _Avaliacao:
    preco: float
    valor_local: float
    custo_local: float
    movimentos_custo: list[tuple[date, float]]


def _avaliar(leitor: _Leitor, pos: Posicao, ativo: Ativo, dia: date) -> _Avaliacao:
    """Preço unitário, valor de mercado e custo (moeda local) na data."""
    if ativo.classe == config.CLASSE_IMOVEL:
        imovel = leitor.imovel(ativo.id)
        preco = leitor.preco_ate(ativo.id, dia)
        if preco is None and imovel:
            preco = imovel.valor_de_mercado if imovel.data_avaliacao <= dia else imovel.valor_aquisicao
        preco = preco or 0.0
        custo = imovel.valor_aquisicao if imovel else pos.preco_medio
        return _Avaliacao(preco, preco, custo, [(pos.data_inicial, custo)])

    fundo = leitor.fundo(ativo.id)
    if fundo is not None:
        m = calc_pe.calcular(fundo, leitor.sessao, dia)
        return _Avaliacao(m.nav, m.nav, m.custo_liquido, m.movimentos_custo)

    quantidade, preco_medio, movimentos = _posicao_ate(leitor, pos, dia)
    if ativo.possui_ticker_listado:
        preco = leitor.preco_ate(ativo.id, dia) or preco_medio
    else:
        preco = preco_medio
    return _Avaliacao(preco, quantidade * preco, quantidade * preco_medio, movimentos)


def _custo_na_base(leitor: _Leitor, av: _Avaliacao, moeda: str, moeda_base: str,
                   dia: date) -> tuple[float, bool]:
    """
    Custo convertido pela taxa da data de cada aquisição. Se a soma local dos
    movimentos diferir do custo local (base antiga, sem todas as transações),
    aplica-se ao custo a taxa média histórica que os movimentos produzem.
    Sem movimentos, usa-se a taxa da data de avaliação.
    """
    if moeda == moeda_base or av.custo_local == 0:
        return av.custo_local, True
    local, base, ok = 0.0, 0.0, True
    for data_mov, valor in av.movimentos_custo:
        convertido, k = leitor.converter(valor, moeda, moeda_base, data_mov)
        local, base, ok = local + valor, base + convertido, ok and k
    if not av.movimentos_custo or not ok:
        return leitor.converter(av.custo_local, moeda, moeda_base, dia)
    if abs(local - av.custo_local) > 1e-6 and local > 0:
        return av.custo_local * base / local, True
    return base, True


def _posicao_ate(fonte: Session | _Leitor, pos: Posicao,
                 dia: date) -> tuple[float, float, list[tuple[date, float]]]:
    """
    Quantidade, preço médio e movimentos de custo da posição na data,
    reproduzindo as transações até ela. Sem essa reconstrução, uma venda
    registrada hoje reduziria a quantidade também no passado, e o TWR
    histórico contaria o resgate duas vezes. Posições sem transações usam
    os valores atuais. Uma venda baixa o custo proporcionalmente (preço
    médio mantido), o que vale também para o custo na moeda base.
    """
    leitor = fonte if isinstance(fonte, _Leitor) else _Leitor(fonte)
    transacoes = leitor.transacoes(pos.id)
    if not transacoes:
        return pos.quantidade, pos.preco_medio, [(pos.data_inicial,
                                                  pos.quantidade * pos.preco_medio)]
    qtd, pm = 0.0, 0.0
    movimentos: list[tuple[date, float]] = []
    for t in transacoes:
        if t.data > dia:
            break
        if t.tipo == "COMPRA":
            total = qtd + t.quantidade
            pm = (qtd * pm + t.quantidade * t.preco + t.taxas) / total if total > 0 else pm
            qtd = total
            movimentos.append((t.data, t.quantidade * t.preco + t.taxas))
        elif t.tipo == "VENDA" and qtd > 0:
            fracao = min(t.quantidade / qtd, 1.0)
            movimentos = [(d, v * (1.0 - fracao)) for d, v in movimentos]
            qtd = max(qtd - t.quantidade, 0.0)
    if dia >= config.hoje() and pos.quantidade >= 0:
        return pos.quantidade, pos.preco_medio, movimentos
    return qtd, pm, movimentos


def snapshot(sessao: Session, carteira_id: str, dia: date | None = None,
             moeda_base: str | None = None, com_custo: bool = True,
             leitor: _Leitor | None = None) -> Snapshot:
    """
    RF-019 — fotografia da carteira em uma data, na moeda base do cliente.
    `com_custo=False` dispensa o custo histórico (as séries só usam o valor);
    `leitor` reaproveita as leituras entre as datas de uma série.
    """
    leitor = leitor or _Leitor(sessao)
    dia = dia or config.hoje()
    if moeda_base is None:
        moeda_base = moeda_base_da_carteira(sessao, carteira_id)

    snap = Snapshot(data=dia, moeda_base=moeda_base)
    posicoes = sessao.scalars(
        select(Posicao).where(Posicao.carteira_id == carteira_id)
    ).all()

    nao_convertidas: set[str] = set()
    for pos in posicoes:
        ativo = leitor.ativo(pos.ativo_id)
        if ativo is None or pos.data_inicial > dia:
            continue

        av = _avaliar(leitor, pos, ativo, dia)
        if av.valor_local <= 0 and av.custo_local <= 0:
            continue

        valor_base, ok1 = leitor.converter(av.valor_local, ativo.moeda, moeda_base, dia)
        if com_custo:
            custo_base, ok2 = _custo_na_base(leitor, av, ativo.moeda, moeda_base, dia)
        else:
            custo_base, ok2 = 0.0, True
        if not (ok1 and ok2):
            nao_convertidas.add(ativo.moeda)
            valor_base = custo_base = 0.0

        pais = pais_de(ativo)
        regiao = "Brasil" if pais == "BRASIL" else "Offshore"
        snap.linhas.append(LinhaPosicao(
            posicao_id=pos.id,
            ativo_id=ativo.id,
            ticker=ativo.ticker_canonico,
            nome=ativo.nome,
            classe=ativo.classe,
            classe_rotulo=CLASSES_ROTULO.get(ativo.classe, ativo.classe),
            liquidez="Ilíquido" if ativo.classe in CLASSES_ILIQUIDAS else "Líquido",
            pais=rotulo_pais(pais),
            moeda=ativo.moeda,
            regiao=regiao,
            macro_classe=macro_classe(ativo.classe, regiao),
            setor=(ativo.setor or "Não informado") if ativo.classe == "ACAO" else "",
            quantidade=pos.quantidade,
            preco_unitario=av.preco,
            valor_local=av.valor_local,
            valor_base=valor_base,
            custo_base=custo_base,
            convertido=ok1 and ok2,
        ))

    for moeda in sorted(nao_convertidas):
        snap.alertas.append(
            f"Sem cotação {moeda}/{moeda_base} para {dia:%d/%m/%Y}: posições nessa moeda "
            f"ficaram fora dos totais. Atualize a série de câmbio."
        )
    snap.linhas.sort(key=lambda l: l.valor_base, reverse=True)
    return snap


def versao_dos_dados(sessao: Session) -> tuple:
    """
    Impressão digital barata da base, usada como chave de cache da interface:
    qualquer escrita relevante muda alguma das contagens (as operações de
    usuário e a coleta agendada geram registro de auditoria; importações,
    eventos, transações, preços e câmbio geram linhas novas).
    """
    contagens = tuple(
        sessao.scalar(select(func.count()).select_from(m))
        for m in (AuditLog, Transacao, EventoPE, Posicao, PrecoHistorico, TaxaCambio,
                  BenchmarkValor)
    )
    return contagens + (config.hoje(),)


def moeda_base_da_carteira(sessao: Session, carteira_id: str) -> str:
    carteira = sessao.get(Carteira, carteira_id)
    if carteira is None:
        return config.MOEDA_BASE_PADRAO
    cliente = sessao.get(Cliente, carteira.cliente_id)
    return cliente.moeda_base if cliente else config.MOEDA_BASE_PADRAO


def _fins_de_mes(inicio: date, fim: date) -> list[date]:
    datas: list[date] = [inicio]
    ano, mes = inicio.year, inicio.month
    while True:
        ultimo = date(ano, mes, monthrange(ano, mes)[1])
        if ultimo >= fim:
            break
        if ultimo > inicio:
            datas.append(ultimo)
        mes += 1
        if mes > 12:
            mes, ano = 1, ano + 1
    if datas[-1] != fim:
        datas.append(fim)
    return datas


def serie_patrimonio(
    sessao: Session, carteira_id: str, inicio: date, fim: date, moeda_base: str,
    sem_cambio: set[str] | None = None,
) -> list[tuple[date, float]]:
    """
    RF-022 — evolução patrimonial em pontos mensais (mais o ponto final).
    Moedas sem cotação em algum ponto são acrescentadas a `sem_cambio`.
    """
    serie = []
    leitor = _Leitor(sessao)
    for dia in _fins_de_mes(inicio, fim):
        snap = snapshot(sessao, carteira_id, dia, moeda_base, com_custo=False, leitor=leitor)
        if sem_cambio is not None:
            sem_cambio.update(l.moeda for l in snap.linhas if not l.convertido)
        serie.append((dia, snap.patrimonio))
    return serie


def fluxos_de_caixa(
    sessao: Session, carteira_id: str, moeda_base: str,
    inicio: date | None = None, fim: date | None = None,
    sem_cambio: set[str] | None = None, ativo_id: str | None = None,
) -> list[tuple[date, float]]:
    """
    Fluxos do investidor, na convenção do IRR:
    aporte = saída de caixa (negativo); resgate/provento/distribuição = entrada.
    Fluxo sem cotação de câmbio é descartado e sua moeda vai para `sem_cambio`.
    Com `ativo_id`, só os fluxos daquele ativo (TWR por ativo, RF-015).
    """
    fim = fim or config.hoje()
    fluxos: list[tuple[date, float]] = []

    stmt = (
        select(Transacao)
        .join(Posicao, Posicao.id == Transacao.posicao_id)
        .where(Posicao.carteira_id == carteira_id, Transacao.data <= fim)
    )
    if inicio:
        stmt = stmt.where(Transacao.data >= inicio)
    if ativo_id:
        stmt = stmt.where(Posicao.ativo_id == ativo_id)
    for t in sessao.scalars(stmt).all():
        pos = sessao.get(Posicao, t.posicao_id)
        ativo = sessao.get(Ativo, pos.ativo_id) if pos else None
        if ativo is None:
            continue
        bruto = t.quantidade * t.preco
        if t.tipo == "COMPRA":
            valor = -(bruto + t.taxas)
        elif t.tipo == "VENDA":
            valor = bruto - t.taxas
        else:
            valor = bruto if bruto else t.preco
        _acrescentar(fluxos, sessao, valor, ativo.moeda, moeda_base, t.data, sem_cambio)

    stmt = (
        select(EventoPE)
        .join(AtivoIliquido, AtivoIliquido.id == EventoPE.fundo_id)
        .join(Posicao, Posicao.ativo_id == AtivoIliquido.ativo_id)
        .where(
            Posicao.carteira_id == carteira_id,
            EventoPE.status.in_(config.STATUS_EFETIVADOS),
            EventoPE.tipo_evento != "NAV_UPDATE",
            EventoPE.data_evento <= fim,
        )
    )
    if inicio:
        stmt = stmt.where(EventoPE.data_evento >= inicio)
    if ativo_id:
        stmt = stmt.where(AtivoIliquido.ativo_id == ativo_id)
    for e in sessao.scalars(stmt).unique().all():
        fundo = sessao.get(AtivoIliquido, e.fundo_id)
        ativo = sessao.get(Ativo, fundo.ativo_id) if fundo else None
        if ativo is None:
            continue
        valor = -e.valor if e.tipo_evento == "CAPITAL_CALL" else e.valor
        _acrescentar(fluxos, sessao, valor, ativo.moeda, moeda_base, e.data_evento, sem_cambio)

    return sorted(fluxos, key=lambda t: t[0])


def _acrescentar(fluxos: list, sessao: Session, valor: float, moeda: str, moeda_base: str,
                 dia: date, sem_cambio: set[str] | None) -> None:
    convertido, ok = cambio.converter_seguro(sessao, valor, moeda, moeda_base, dia)
    if ok:
        fluxos.append((dia, convertido))
    elif sem_cambio is not None:
        sem_cambio.add(moeda)


def _alerta_cambio(moedas: set[str], moeda_base: str) -> list[str]:
    return [
        f"Sem cotação {m}/{moeda_base} em parte do período: valores e fluxos nessa moeda "
        f"ficaram fora do cálculo, e os indicadores estão incompletos. "
        f"Atualize a série de câmbio." for m in sorted(moedas)
    ]


@dataclass
class Variacao:
    rotulo: str
    inicio: date
    patrimonio_inicial: float
    patrimonio_final: float
    fluxo_liquido: float
    retorno: float | None

    @property
    def variacao_absoluta(self) -> float:
        return self.patrimonio_final - self.patrimonio_inicial


def variacao(sessao: Session, carteira_id: str, inicio: date, fim: date | None = None,
             moeda_base: str | None = None, rotulo: str = "",
             patrimonio_final: float | None = None) -> Variacao:
    """
    RF-019 — variação do patrimônio entre duas datas. A variação absoluta
    inclui aportes; o retorno (Dietz modificado, mesma convenção da RN-010)
    os expurga, ponderando cada fluxo pelo tempo em que ficou investido.
    """
    fim = fim or config.hoje()
    moeda_base = moeda_base or moeda_base_da_carteira(sessao, carteira_id)
    v0 = snapshot(sessao, carteira_id, inicio, moeda_base).patrimonio
    v1 = patrimonio_final if patrimonio_final is not None \
        else snapshot(sessao, carteira_id, fim, moeda_base).patrimonio
    aportes = [(d, -v) for d, v in fluxos_de_caixa(sessao, carteira_id, moeda_base, inicio, fim)
               if inicio < d <= fim]
    liquido = sum(v for _, v in aportes)
    retorno = calc_rent.dietz(v0, v1, aportes, inicio, fim)
    return Variacao(rotulo, inicio, v0, v1, liquido, retorno)


def data_inicial_carteira(sessao: Session, carteira_id: str) -> date | None:
    return sessao.scalar(
        select(func.min(Posicao.data_inicial)).where(Posicao.carteira_id == carteira_id)
    )


def rentabilidade(
    sessao: Session, carteira_id: str,
    inicio: date | None = None, fim: date | None = None,
    moeda_base: str | None = None,
) -> Rentabilidade | None:
    """
    RF-015/016/018 — apura TWR, MWR e métricas de risco da carteira,
    comparando com os benchmarks do período.
    """
    moeda_base = moeda_base or moeda_base_da_carteira(sessao, carteira_id)
    fim = fim or config.hoje()
    inicio = inicio or data_inicial_carteira(sessao, carteira_id)
    if inicio is None or inicio >= fim:
        return None

    sem_cambio: set[str] = set()
    serie = serie_patrimonio(sessao, carteira_id, inicio, fim, moeda_base, sem_cambio)
    fluxos = [(d, v) for d, v in fluxos_de_caixa(sessao, carteira_id, moeda_base,
                                                 inicio, fim, sem_cambio)
              if inicio < d <= fim]

    aportes = -sum(v for _, v in fluxos if v < 0)
    resgates = sum(v for _, v in fluxos if v > 0)
    patrimonio_final = serie[-1][1] if serie else 0.0
    patrimonio_inicial = serie[0][1] if serie else 0.0

    retornos = calc_rent.retornos_subperiodos(serie, fluxos)
    indice = calc_rent.serie_indice(serie, fluxos)
    valor_twr = calc_rent.twr_subperiodos(r for _, r in retornos)
    dias = max((fim - inicio).days, 1)

    fluxos_irr = list(fluxos)
    if patrimonio_inicial > 0:
        fluxos_irr.insert(0, (inicio, -patrimonio_inicial))
    if patrimonio_final > 0:
        fluxos_irr.append((fim, patrimonio_final))
    valor_mwr = calc_rent.irr(fluxos_irr)

    empregado = patrimonio_inicial + aportes - resgates
    retorno_total = ((patrimonio_final - empregado) / empregado) if empregado > 0 else None

    alertas = _alerta_cambio(sem_cambio, moeda_base)
    if valor_mwr is not None and calc_rent.irr_ambigua(fluxos_irr):
        alertas.append("O MWR tem mais de uma solução para estes fluxos; o valor exibido "
                       "é uma delas e deve ser lido junto com o TWR.")

    vol = calc_rent.volatilidade(indice, [r for _, r in retornos])
    dd = calc_rent.max_drawdown(indice)
    twr_anual = calc_rent.anualizar(valor_twr, dias)
    rf = bmk_srv.taxa_livre_risco(sessao, inicio, fim)
    ind_sharpe = calc_rent.sharpe(twr_anual, vol, rf)

    return Rentabilidade(
        cota=indice,
        inicio=inicio,
        fim=fim,
        patrimonio_inicial=patrimonio_inicial,
        patrimonio_final=patrimonio_final,
        aportes=aportes,
        resgates=resgates,
        retorno_total=retorno_total,
        twr=valor_twr,
        twr_anualizado=twr_anual,
        mwr=valor_mwr,
        volatilidade=vol,
        max_drawdown=dd,
        sharpe=ind_sharpe,
        serie=serie,
        comparativos=bmk_srv.comparar(sessao, valor_twr, inicio, fim, moeda_base),
        alertas=alertas,
    )


@dataclass
class RentabilidadeAtivo:
    ticker: str
    nome: str
    classe_rotulo: str
    inicio: date
    twr: float
    twr_anualizado: float


def rentabilidade_por_ativo(
    sessao: Session, carteira_id: str,
    inicio: date | None = None, fim: date | None = None,
    moeda_base: str | None = None,
) -> list[RentabilidadeAtivo]:
    """
    RF-015 / RN-010 — TWR de cada ativo, na moeda base, pela mesma regra da
    carteira: valor da posição nos fins de mês (mais início e fim), Dietz
    modificado em cada subperíodo com os fluxos daquele ativo, encadeamento
    geométrico. O período de cada ativo começa na data da sua aquisição, se
    posterior ao início pedido.
    """
    moeda_base = moeda_base or moeda_base_da_carteira(sessao, carteira_id)
    fim = fim or config.hoje()
    leitor = _Leitor(sessao)
    saida: list[RentabilidadeAtivo] = []
    for pos in sessao.scalars(select(Posicao).where(Posicao.carteira_id == carteira_id)):
        ativo = leitor.ativo(pos.ativo_id)
        ini = max(inicio or pos.data_inicial, pos.data_inicial)
        if ativo is None or ini >= fim:
            continue
        serie = []
        for dia in _fins_de_mes(ini, fim):
            valor, ok = leitor.converter(
                _avaliar(leitor, pos, ativo, dia).valor_local, ativo.moeda, moeda_base, dia)
            serie.append((dia, valor if ok else 0.0))
        fluxos = [(d, v) for d, v in fluxos_de_caixa(sessao, carteira_id, moeda_base, ini, fim,
                                                     ativo_id=ativo.id) if ini < d <= fim]
        twr = calc_rent.twr(serie, fluxos)
        saida.append(RentabilidadeAtivo(
            ticker=ativo.ticker_canonico, nome=ativo.nome,
            classe_rotulo=CLASSES_ROTULO.get(ativo.classe, ativo.classe), inicio=ini,
            twr=twr, twr_anualizado=calc_rent.anualizar(twr, (fim - ini).days)))
    return sorted(saida, key=lambda r: r.twr, reverse=True)


def _subtipos_da_carteira(sessao: Session, modelo, carteira_id: str, ordem) -> list:
    """Linhas de AtivoIliquido/AtivoImovel cujos ativos têm posição na carteira."""
    stmt = (
        select(modelo)
        .join(Ativo, Ativo.id == modelo.ativo_id)
        .join(Posicao, Posicao.ativo_id == Ativo.id)
        .where(Posicao.carteira_id == carteira_id)
        .order_by(ordem)
    )
    return list(sessao.scalars(stmt).unique().all())


def fundos_da_carteira(sessao: Session, carteira_id: str, ordem=None) -> list[AtivoIliquido]:
    return _subtipos_da_carteira(sessao, AtivoIliquido, carteira_id,
                                 AtivoIliquido.identificador_fundo if ordem is None else ordem)


def imoveis_da_carteira(sessao: Session, carteira_id: str, ordem=None) -> list[AtivoImovel]:
    return _subtipos_da_carteira(sessao, AtivoImovel, carteira_id,
                                 AtivoImovel.identificador_imovel if ordem is None else ordem)


def metricas_pe(sessao: Session, carteira_id: str,
                dia: date | None = None) -> list[calc_pe.MetricasPE]:
    """RF-017 — MOIC/DPI/TVPI dos fundos ilíquidos da carteira, até `dia`."""
    return [calc_pe.calcular(f, sessao, dia) for f in fundos_da_carteira(sessao, carteira_id)]


def resumo_imoveis(sessao: Session, carteira_id: str) -> list[dict]:
    """Yield líquido e valorização dos imóveis diretos."""
    imoveis = imoveis_da_carteira(sessao, carteira_id)

    saida: list[dict] = []
    for imovel in imoveis:
        ativo = sessao.get(Ativo, imovel.ativo_id)
        liquido_anual = (imovel.aluguel_mensal - imovel.despesas_mensais) * 12
        saida.append({
            "Identificador": imovel.identificador_imovel,
            "Imóvel": ativo.nome if ativo else "",
            "Tipo": imovel.tipo_imovel.title(),
            "Moeda": ativo.moeda if ativo else config.MOEDA_BASE_PADRAO,
            "Aquisição": imovel.valor_aquisicao,
            "Valor de mercado": imovel.valor_de_mercado,
            "Valorização": (imovel.valor_de_mercado / imovel.valor_aquisicao - 1.0)
            if imovel.valor_aquisicao else 0.0,
            "Renda líq. anual": liquido_anual,
            "Yield líq.": (liquido_anual / imovel.valor_de_mercado)
            if imovel.valor_de_mercado else 0.0,
            "Última avaliação": imovel.data_avaliacao,
        })
    return sorted(saida, key=lambda d: d["Valor de mercado"], reverse=True)
