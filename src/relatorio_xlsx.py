"""
Relatório em Excel (RF-023): resumo, posições, fundos ilíquidos e imóveis.

Par do relatório em PDF (src/relatorio_pdf.py): recebe os mesmos objetos já
calculados pela consolidação e não depende da interface, o que permite
testá-lo sem Streamlit.
"""
from __future__ import annotations

import io

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

CAB_FILL = PatternFill("solid", fgColor="17253F")
CAB_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFF")
TXT_FONT = Font(name="Arial", size=10)
TITULO_FONT = Font(name="Arial", size=13, bold=True, color="17253F")


def _pct(valor: float | None) -> float | str:
    return round(valor * 100, 2) if valor is not None else "n/d"


def df_posicoes(snap, base: str) -> pd.DataFrame:
    """Tabela de posições, usada na prévia da tela, no CSV e na planilha."""
    return pd.DataFrame([
        {
            "Ativo": l.nome,
            "Código": l.ticker,
            "Classe": l.classe_rotulo,
            "Liquidez": l.liquidez,
            "Região": l.pais.title(),
            "Moeda": l.moeda,
            "Quantidade": round(l.quantidade, 6),
            "Preço/NAV (moeda origem)": round(l.preco_unitario, 4),
            f"Valor de mercado ({base})": round(l.valor_base, 2),
            f"Custo ({base})": round(l.custo_base, 2),
            f"Resultado ({base})": round(l.resultado, 2),
            "Retorno (%)": round(l.retorno * 100, 2),
        }
        for l in snap.linhas
    ])


def linhas_resumo(carteira_nome: str, base: str, snap, rent) -> list[tuple[str, object]]:
    linhas: list[tuple[str, object]] = [
        ("Carteira", carteira_nome),
        ("Moeda base", base),
        ("Data de referência", snap.data.strftime("%d/%m/%Y")),
        ("", ""),
        ("Patrimônio consolidado", round(snap.patrimonio, 2)),
        ("Custo de aquisição (câmbio histórico)", round(snap.custo, 2)),
        ("Resultado", round(snap.resultado, 2)),
        ("", ""),
    ]
    for classe, valor in snap.por("classe_rotulo").items():
        linhas.append((f"Alocação · {classe}", round(valor, 2)))
    linhas.append(("", ""))
    for liquidez, valor in snap.por("liquidez").items():
        linhas.append((f"Liquidez · {liquidez}", round(valor, 2)))

    if rent is not None:
        linhas += [
            ("", ""),
            ("Período apurado", f"{rent.inicio:%d/%m/%Y} a {rent.fim:%d/%m/%Y}"),
            ("Retorno total (%)", _pct(rent.retorno_total)),
            ("TWR acumulado (%)", _pct(rent.twr)),
            ("TWR anualizado (%)", _pct(rent.twr_anualizado)),
            ("MWR / IRR anualizado (%)", _pct(rent.mwr)),
            ("Volatilidade anualizada (%)", _pct(rent.volatilidade)),
            ("Máximo drawdown (%)", _pct(rent.max_drawdown)),
            ("Índice de Sharpe",
             round(rent.sharpe, 2) if rent.sharpe is not None else "n/d"),
            ("Patrimônio inicial do período", round(rent.patrimonio_inicial, 2)),
            ("Aportes no período", round(rent.aportes, 2)),
            ("Resgates no período", round(rent.resgates, 2)),
            ("", ""),
        ]
        for c in rent.comparativos:
            linhas.append((f"Benchmark · {c.codigo} em {c.moeda} (%)", _pct(c.retorno)))
        for alerta in rent.alertas:
            linhas.append(("Alerta", alerta))
    return linhas


def _df_fundos(metricas) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Fundo": m.nome, "Gestor": m.gestor,
            "Classe": m.sub_classe.replace("_", " ").title(), "Moeda": m.moeda,
            "Commitment": round(m.commitment, 2),
            "Paid-in": round(m.paid_in, 2),
            "Pendente": round(m.pendente, 2),
            "Dry powder": round(m.dry_powder, 2),
            "Distribuições": round(m.distribuicoes, 2),
            "NAV": round(m.nav, 2),
            "DPI": round(m.dpi, 4), "RVPI": round(m.rvpi, 4),
            "TVPI": round(m.tvpi, 4),
            "IRR (%)": _pct(m.irr),
        }
        for m in metricas
    ])


def _df_imoveis(imoveis) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Identificador": i["Identificador"], "Imóvel": i["Imóvel"],
            "Tipo": i["Tipo"], "Moeda": i["Moeda"],
            "Aquisição": round(i["Aquisição"], 2),
            "Valor de mercado": round(i["Valor de mercado"], 2),
            "Valorização (%)": round(i["Valorização"] * 100, 2),
            "Renda líquida anual": round(i["Renda líq. anual"], 2),
            "Yield líquido (%)": round(i["Yield líq."] * 100, 2),
            "Última avaliação": i["Última avaliação"].strftime("%d/%m/%Y"),
        }
        for i in imoveis
    ])


def _escrever_tabela(ws, df: pd.DataFrame, linha_inicial: int = 1) -> None:
    for j, coluna in enumerate(df.columns, start=1):
        celula = ws.cell(row=linha_inicial, column=j, value=coluna)
        celula.font = CAB_FONT
        celula.fill = CAB_FILL
        celula.alignment = Alignment(horizontal="center", vertical="center",
                                     wrap_text=True)
        largura = max(len(str(coluna)) + 2,
                      *(len(str(v)) + 2 for v in df[coluna].head(60)), 12)
        ws.column_dimensions[get_column_letter(j)].width = min(largura, 40)

    for i, (_, registro) in enumerate(df.iterrows(), start=linha_inicial + 1):
        for j, valor in enumerate(registro, start=1):
            celula = ws.cell(row=i, column=j, value=valor)
            celula.font = TXT_FONT
            if isinstance(valor, (int, float)):
                celula.number_format = "#,##0.00"
    ws.freeze_panes = ws.cell(row=linha_inicial + 1, column=1)


def gerar(carteira_nome: str, base: str, snap, rent, metricas, imoveis) -> bytes:
    wb = Workbook()

    ws = wb.active
    ws.title = "Resumo"
    ws["A1"] = "Relatório de Carteira Consolidada"
    ws["A1"].font = TITULO_FONT
    ws["A2"] = ("Valores em moeda base, salvo indicação em contrário. "
                "Fonte dos dados: importação por template e cotações do sistema.")
    ws["A2"].font = Font(name="Arial", size=9, italic=True, color="55607A")
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 26

    for i, (rotulo, valor) in enumerate(linhas_resumo(carteira_nome, base, snap, rent),
                                        start=4):
        ws.cell(row=i, column=1, value=rotulo).font = Font(
            name="Arial", size=10, bold=not str(rotulo).startswith(("Alocação", "Liquidez",
                                                                    "Benchmark", "Alerta")))
        celula = ws.cell(row=i, column=2, value=valor)
        celula.font = TXT_FONT
        if isinstance(valor, (int, float)):
            celula.number_format = "#,##0.00"

    _escrever_tabela(wb.create_sheet("Posições"), df_posicoes(snap, base))
    if metricas:
        _escrever_tabela(wb.create_sheet("Fundos ilíquidos"), _df_fundos(metricas))
    if imoveis:
        _escrever_tabela(wb.create_sheet("Imóveis"), _df_imoveis(imoveis))

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
