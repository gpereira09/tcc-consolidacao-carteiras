"""
Relatório da carteira consolidada em PDF (RF-023; UC-16).

Mesmo conteúdo do relatório Excel — resumo, indicadores, alocações,
benchmarks, posições, fundos ilíquidos e imóveis — em documento A4 pronto
para impressão ou envio, gerado com ReportLab (sem dependência de navegador).
"""
from __future__ import annotations

import io
from datetime import datetime
from functools import lru_cache

from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src import formatacao

TINTA = colors.HexColor("#17253F")
LATAO = colors.HexColor("#8C6D2F")
GRAFITE = colors.HexColor("#4B566B")
LINHA = colors.HexColor("#D5D9E0")
FUNDO = colors.HexColor("#F3F4F7")
PALETA = [colors.HexColor(c) for c in (
    "#17253F", "#8C6D2F", "#4E6E8E", "#B89A5E", "#6B7A90", "#2F4A6B",
    "#C9B38A", "#9AA6B8", "#5A4A2A", "#3C5A78")]
NOTA_COR = "#6B7A90"
SISTEMA = "Sistema de Consolidação de Carteiras"


def _num(v: float | None, casas: int = 2) -> str:
    return formatacao.numero(v, casas, vazio="n/d")


def _moeda(v: float | None, codigo: str) -> str:
    return formatacao.moeda(v, codigo, vazio="n/d")


_qtd = formatacao.quantidade


def _pct(v: float | None, casas: int = 2) -> str:
    return formatacao.percentual(v, casas, vazio="n/d")


def _duas_linhas(principal: str, secundario: str) -> str:
    return f"{principal}<br/><font color='{NOTA_COR}'>{secundario}</font>"


@lru_cache(maxsize=1)
def _estilos():
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold",
                                 fontSize=18, textColor=TINTA, alignment=TA_LEFT, spaceAfter=4),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontSize=9.5, textColor=GRAFITE,
                              spaceAfter=10),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold",
                             fontSize=12.5, textColor=TINTA, spaceBefore=12, spaceAfter=6),
        "normal": ParagraphStyle("n", parent=base["Normal"], fontSize=9, leading=12),
        "nota": ParagraphStyle("nota", parent=base["Normal"], fontSize=7.8, leading=10,
                               textColor=GRAFITE),
        "cel": ParagraphStyle("cel", parent=base["Normal"], fontSize=7.8, leading=9.5),
        "cel_d": ParagraphStyle("celd", parent=base["Normal"], fontSize=7.8, leading=9.5,
                                alignment=TA_RIGHT),
        "cab": ParagraphStyle("cab", parent=base["Normal"], fontSize=7.6, leading=9.2,
                              textColor=colors.white, fontName="Helvetica-Bold"),
        "cab_d": ParagraphStyle("cabd", parent=base["Normal"], fontSize=7.6, leading=9.2,
                                textColor=colors.white, fontName="Helvetica-Bold",
                                alignment=TA_RIGHT),
    }


def _tabela(dados: list[list], larguras: list[float], alinhar_direita: set[int] = frozenset(),
            cabecalho: bool = True) -> Table:
    est = _estilos()
    linhas = []
    for i, linha in enumerate(dados):
        nova = []
        for j, v in enumerate(linha):
            if isinstance(v, (Paragraph, Drawing)):
                nova.append(v)
            elif i == 0 and cabecalho:
                nova.append(Paragraph(str(v), est["cab_d"] if j in alinhar_direita else est["cab"]))
            else:
                nova.append(Paragraph(str(v), est["cel_d"] if j in alinhar_direita else est["cel"]))
        linhas.append(nova)
    t = Table(linhas, colWidths=larguras, repeatRows=1 if cabecalho else 0)
    comandos = [
        ("GRID", (0, 0), (-1, -1), 0.4, LINHA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1 if cabecalho else 0), (-1, -1), [colors.white, FUNDO]),
    ]
    if cabecalho:
        comandos += [("BACKGROUND", (0, 0), (-1, 0), TINTA),
                     ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
    t.setStyle(TableStyle(comandos))
    return t


def _pizza(dados: dict[str, float], titulo: str, largura: float = 8.2 * cm) -> Drawing:
    altura = 6.4 * cm
    d = Drawing(largura, altura)
    d.add(String(0, altura - 12, titulo, fontName="Helvetica-Bold", fontSize=9.5,
                 fillColor=TINTA))
    total = sum(dados.values()) or 1.0
    itens = list(dados.items())[:8]
    if not itens:
        return d
    p = Pie()
    p.x, p.y, p.width, p.height = 4, 10, 3.6 * cm, 3.6 * cm
    p.data = [v for _, v in itens]
    p.labels = None
    p.slices.strokeColor = colors.white
    p.slices.strokeWidth = 0.8
    for i in range(len(itens)):
        p.slices[i].fillColor = PALETA[i % len(PALETA)]
    d.add(p)
    y = altura - 32
    for i, (rotulo, valor) in enumerate(itens):
        d.add(Rect(4.3 * cm, y, 7, 7, fillColor=PALETA[i % len(PALETA)], strokeColor=None))
        texto = f"{rotulo[:24]}  {_num(valor / total * 100, 1)}%"
        d.add(String(4.3 * cm + 11, y, texto, fontName="Helvetica", fontSize=7.5,
                     fillColor=GRAFITE))
        y -= 12
    return d


def gerar(carteira_nome: str, base: str, snap, rent, metricas, imoveis,
          titular: str | None = None, simulado: bool = False) -> bytes:
    """`simulado=True` acrescenta o aviso de que as séries de mercado são sintéticas."""
    est = _estilos()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4), leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title=f"Relatório — {carteira_nome}", author=SISTEMA,
    )
    largura = landscape(A4)[0] - doc.leftMargin - doc.rightMargin
    h: list = []

    h.append(Paragraph("Relatório de Carteira Consolidada", est["titulo"]))
    identificacao = f"{titular} · " if titular else ""
    h.append(Paragraph(
        f"{identificacao}{carteira_nome} · moeda base {base} · referência "
        f"{snap.data:%d/%m/%Y} · emitido em {datetime.now():%d/%m/%Y %H:%M}", est["sub"]))
    if simulado:
        h.append(Paragraph(
            "<b>Ambiente de demonstração:</b> cotações, câmbio e benchmarks desta base são "
            "gerados por simulação e não representam valores reais de mercado.", est["nota"]))
    for alerta in snap.alertas + (rent.alertas if rent is not None else []):
        h.append(Paragraph(f"<b>Atenção:</b> {alerta}", est["nota"]))

    por_liq = snap.por("liquidez")
    total = snap.patrimonio or 1.0
    resultado_pct = (snap.patrimonio / snap.custo - 1) if snap.custo > 0 else None
    fichas = [
        ["Patrimônio consolidado", "Custo de aquisição", "Resultado sobre o custo", "Líquido / Ilíquido"],
        [_moeda(snap.patrimonio, base), _moeda(snap.custo, base),
         f"{_moeda(snap.resultado, base)} ({_pct(resultado_pct, 1)})",
         f"{_pct(por_liq.get('Líquido', 0) / total, 1)} / "
         f"{_pct(por_liq.get('Ilíquido', 0) / total, 1)}"],
    ]
    h.append(_tabela(fichas, [largura / 4] * 4))

    if rent is not None:
        h.append(Paragraph("Rentabilidade e risco", est["h2"]))
        ind = [
            ["Período", "TWR acumulado", "TWR a.a.", "MWR (IRR) a.a.", "Volatilidade a.a.",
             "Máx. drawdown", "Sharpe"],
            [f"{rent.inicio:%d/%m/%Y} a {rent.fim:%d/%m/%Y}", _pct(rent.twr), _pct(rent.twr_anualizado),
             _pct(rent.mwr), _pct(rent.volatilidade), _pct(rent.max_drawdown),
             _num(rent.sharpe)],
        ]
        h.append(_tabela(ind, [largura * f for f in (0.22, 0.13, 0.11, 0.14, 0.14, 0.14, 0.12)],
                         alinhar_direita=set(range(1, 7))))
        if rent.comparativos:
            h.append(Spacer(1, 6))
            bm = [["Benchmark", "Retorno no período", "Carteira (TWR)", "Diferença", "Resultado"]]
            for c in rent.comparativos:
                bm.append([f"{c.nome} (em {c.moeda})", _pct(c.retorno), _pct(rent.twr),
                           f"{_num(c.diferenca * 100)} p.p.",
                           "Superado" if c.superou else "Não superado"])
            h.append(_tabela(bm, [largura * f for f in (0.28, 0.18, 0.18, 0.16, 0.20)],
                             alinhar_direita={1, 2, 3}))

    h.append(Paragraph("Alocação", est["h2"]))
    graficos = Table([[_pizza(snap.por("classe_rotulo"), "Por classe de ativo"),
                       _pizza(snap.por("pais"), "Por região"),
                       _pizza(snap.por("moeda"), "Por moeda de origem")]],
                     colWidths=[largura / 3] * 3)
    graficos.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    h.append(KeepTogether(graficos))

    h.append(Paragraph("Posições", est["h2"]))
    pos = [["Ativo", "Classe", "Moeda", "Qtd.", "Preço / NAV", f"Valor ({base})",
            f"Custo ({base})", "Retorno", "Part."]]
    for l in snap.linhas:
        pos.append([_duas_linhas(l.nome[:34], l.ticker), l.classe_rotulo,
                    l.moeda, _qtd(l.quantidade), _num(l.preco_unitario, 2),
                    _num(l.valor_base, 0), _num(l.custo_base, 0), _pct(l.retorno, 1),
                    _pct(l.valor_base / total, 1)])
    h.append(_tabela(pos, [largura * f for f in (0.24, 0.12, 0.065, 0.075, 0.125, 0.12, 0.115, 0.075, 0.065)],
                     alinhar_direita={3, 4, 5, 6, 7, 8}))

    if metricas:
        h.append(Paragraph("Fundos ilíquidos (PE / VC / Real Estate)", est["h2"]))
        fd = [["Fundo", "Moeda", "Commitment", "Paid-in", "Dry powder", "Distrib.", "NAV",
               "DPI", "TVPI", "IRR"]]
        for m in metricas:
            fd.append([_duas_linhas(m.nome[:30], m.gestor[:30]), m.moeda,
                       _num(m.commitment, 0), _num(m.paid_in, 0), _num(m.dry_powder, 0),
                       _num(m.distribuicoes, 0), _num(m.nav, 0), _num(m.dpi), _num(m.tvpi),
                       _pct(m.irr, 1)])
        h.append(_tabela(fd, [largura * f for f in (0.22, 0.06, 0.11, 0.10, 0.10, 0.10, 0.10, 0.07, 0.07, 0.07)],
                         alinhar_direita=set(range(2, 10))))

    if imoveis:
        h.append(Paragraph("Imóveis diretos", est["h2"]))
        im = [["Imóvel", "Tipo", "Moeda", "Aquisição", "Valor de mercado", "Valorização",
               "Renda líq. anual", "Yield líq.", "Avaliação"]]
        for i in imoveis:
            aval = i["Última avaliação"]
            im.append([_duas_linhas(i["Imóvel"][:32], i["Identificador"]),
                       i["Tipo"], i["Moeda"], _num(i["Aquisição"], 0), _num(i["Valor de mercado"], 0),
                       _pct(i["Valorização"], 1), _num(i["Renda líq. anual"], 0),
                       _pct(i["Yield líq."], 1),
                       aval.strftime("%d/%m/%Y") if hasattr(aval, "strftime") else str(aval)])
        h.append(_tabela(im, [largura * f for f in (0.23, 0.10, 0.065, 0.11, 0.12, 0.095, 0.11, 0.08, 0.09)],
                         alinhar_direita={3, 4, 5, 6, 7, 8}))

    h.append(Spacer(1, 10))
    h.append(Paragraph(
        "Valor de mercado convertido para a moeda base pela taxa da data de referência e custo "
        "pela taxa da data de cada aquisição (RN-009). Ativos listados marcados pelo último preço "
        "de mercado disponível (RN-006); fundos ilíquidos pelo último NAV informado, ajustado "
        "pelas chamadas e distribuições posteriores a ele; imóveis pela última avaliação; demais "
        "ativos pelo preço médio (RN-007). TWR por unitização com Dietz modificado em "
        "subperíodos mensais (RN-010); MWR anualizado por Newton-Raphson, com o patrimônio "
        "inicial e o final como fluxos (RN-011). Benchmarks em outra moeda convertidos para a "
        "moeda base. Rentabilidade passada não garante rentabilidade futura.",
        est["nota"]))

    def rodape(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GRAFITE)
        canvas.drawString(doc_.leftMargin, 1.1 * cm, SISTEMA)
        w, hgt = landscape(A4)
        canvas.drawRightString(w - doc_.rightMargin, 1.1 * cm, f"Página {doc_.page}")
        canvas.setStrokeColor(LATAO)
        canvas.setLineWidth(1.2)
        canvas.line(doc_.leftMargin, hgt - 1.3 * cm, w - doc_.rightMargin, hgt - 1.3 * cm)
        canvas.restoreState()

    doc.build(h, onFirstPage=rodape, onLaterPages=rodape)
    return buf.getvalue()
