"""
Geração dos templates padronizados em XLSX (capítulo 9).

Cada arquivo é produzido com três abas:
  * Instruções — orientações de preenchimento e dicionário das colunas;
  * Dados (ou Fundos/Eventos, no PE/VC) — área de preenchimento, com
    cabeçalho travado, listas suspensas e formatação numérica;
  * Listas — intervalos nomeados que alimentam as validações (aba oculta).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

import config
from src.schema import Aba, Coluna, Template, TEMPLATES

AZUL = "1F3864"
CINZA = "F2F2F2"
AMBAR = "FFF2CC"

F_TITULO = Font(name="Calibri", size=16, bold=True, color="FFFFFF")
F_SUB = Font(name="Calibri", size=11, color="FFFFFF")
F_CAB = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
F_SECAO = Font(name="Calibri", size=12, bold=True, color=AZUL)
F_TXT = Font(name="Calibri", size=10)
F_OBS = Font(name="Calibri", size=9, italic=True, color="808080")

P_TITULO = PatternFill("solid", fgColor=AZUL)
P_CAB_OBR = PatternFill("solid", fgColor=AZUL)
P_CAB_OPC = PatternFill("solid", fgColor="7F7F7F")
P_ZEBRA = PatternFill("solid", fgColor=CINZA)
P_AVISO = PatternFill("solid", fgColor=AMBAR)

_fina = Side(style="thin", color="BFBFBF")
B_GRADE = Border(left=_fina, right=_fina, top=_fina, bottom=_fina)

WRAP = Alignment(vertical="top", wrap_text=True)
CENTRO = Alignment(horizontal="center", vertical="center", wrap_text=True)
ESQ_RECUO = Alignment(horizontal="left", vertical="center", indent=1)


def _formato(col: Coluna) -> str | None:
    if col.tipo == "data":
        return "DD/MM/YYYY"
    if col.tipo == "decimal":
        return "#,##0." + ("0" * col.decimais) if col.decimais else "#,##0"
    if col.tipo == "inteiro":
        return "#,##0"
    return None


def _valor_celula(col: Coluna, bruto):
    """Converte o exemplo textual em tipo nativo do Excel quando aplicável."""
    if bruto in ("", None):
        return None
    if col.tipo == "data" and isinstance(bruto, str):
        return datetime.strptime(bruto, "%d/%m/%Y").date()
    return bruto


def _monta_instrucoes(ws: Worksheet, tpl: Template) -> None:
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 11
    ws.column_dimensions["E"].width = 74

    ws.merge_cells("B2:E2")
    ws["B2"] = tpl.titulo
    ws["B2"].font = F_TITULO
    ws["B2"].fill = P_TITULO
    ws["B2"].alignment = ESQ_RECUO
    ws.row_dimensions[2].height = 32

    ws.merge_cells("B3:E3")
    ws["B3"] = "Sistema de Consolidação de Carteiras · Universidade Positivo · 2026"
    ws["B3"].font = F_SUB
    ws["B3"].fill = P_TITULO
    ws["B3"].alignment = ESQ_RECUO

    linha = 5
    ws.merge_cells(f"B{linha}:E{linha + 1}")
    ws[f"B{linha}"] = tpl.descricao
    ws[f"B{linha}"].font = F_TXT
    ws[f"B{linha}"].alignment = WRAP

    linha += 3
    ws[f"B{linha}"] = "COMO PREENCHER"
    ws[f"B{linha}"].font = F_SECAO
    linha += 1
    for i, texto in enumerate(tpl.instrucoes, start=1):
        ws.merge_cells(f"B{linha}:E{linha}")
        ws[f"B{linha}"] = f"{i:>2}.   {texto}"
        ws[f"B{linha}"].font = F_TXT
        ws[f"B{linha}"].alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[linha].height = 16
        linha += 1

    linha += 1
    ws.merge_cells(f"B{linha}:E{linha}")
    ws[f"B{linha}"] = (
        "Importante: a importação valida a estrutura antes do conteúdo. Erro estrutural "
        "(coluna ausente ou renomeada) aborta o arquivo inteiro; erros de conteúdo são "
        "reportados linha a linha e você escolhe importar apenas as linhas válidas (RN-002, RN-003)."
    )
    ws[f"B{linha}"].font = F_TXT
    ws[f"B{linha}"].fill = P_AVISO
    ws[f"B{linha}"].alignment = WRAP
    ws[f"B{linha}"].border = B_GRADE
    ws.row_dimensions[linha].height = 40

    linha += 2
    for aba in tpl.abas:
        ws[f"B{linha}"] = f"DICIONÁRIO DE COLUNAS — ABA '{aba.nome.upper()}'"
        ws[f"B{linha}"].font = F_SECAO
        linha += 1

        for titulo, celula in zip(("Coluna", "Tipo", "Obrigat.", "Descrição"), "BCDE"):
            c = ws[f"{celula}{linha}"]
            c.value = titulo
            c.font = F_CAB
            c.fill = P_CAB_OBR
            c.alignment = CENTRO
            c.border = B_GRADE
        linha += 1

        for idx, col in enumerate(aba.colunas):
            tipo = col.tipo.capitalize()
            if col.tipo == "decimal":
                tipo = f"Numérico ({col.decimais} dec)"
            elif col.tipo == "data":
                tipo = "Data (dd/mm/aaaa)"
            descricao = col.descricao
            if col.tipo == "lista":
                descricao += "  →  " + ", ".join(col.valores)

            valores = (col.nome, tipo, "Sim" if col.obrigatoria else "Não", descricao)
            for celula, valor in zip("BCDE", valores):
                c = ws[f"{celula}{linha}"]
                c.value = valor
                c.font = F_TXT
                c.alignment = WRAP if celula == "E" else Alignment(vertical="top")
                c.border = B_GRADE
                if idx % 2:
                    c.fill = P_ZEBRA
            linha += 1
        linha += 1

    ws[f"B{linha}"] = "Fonte: Elaborado pelos autores (2026). Dados de exemplo são sintéticos."
    ws[f"B{linha}"].font = F_OBS
    ws.sheet_view.showGridLines = False

    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def _monta_dados(ws: Worksheet, aba: Aba, ws_listas: Worksheet, col_lista_ini: int) -> int:
    """Escreve cabeçalho, exemplos e validações. Devolve a próxima coluna livre em 'Listas'."""
    for j, col in enumerate(aba.colunas, start=1):
        c = ws.cell(row=1, column=j, value=col.nome)
        c.font = F_CAB
        c.fill = P_CAB_OBR if col.obrigatoria else P_CAB_OPC
        c.alignment = CENTRO
        c.border = B_GRADE
        c.comment = None
        ws.column_dimensions[get_column_letter(j)].width = col.largura
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    formatos = [_formato(col) for col in aba.colunas]
    for i, linha_ex in enumerate(aba.exemplos, start=2):
        for j, (col, bruto) in enumerate(zip(aba.colunas, linha_ex), start=1):
            c = ws.cell(row=i, column=j, value=_valor_celula(col, bruto))
            c.font = F_TXT
            c.border = B_GRADE
            fmt = formatos[j - 1]
            if fmt:
                c.number_format = fmt

    ultima_linha = max(len(aba.exemplos) + 1, 500)
    coluna_lista = col_lista_ini
    for j, col in enumerate(aba.colunas, start=1):
        if col.tipo != "lista":
            continue
        letra = get_column_letter(coluna_lista)
        ws_listas.cell(row=1, column=coluna_lista, value=f"{aba.nome}.{col.nome}").font = F_CAB
        for k, valor in enumerate(col.valores, start=2):
            ws_listas.cell(row=k, column=coluna_lista, value=valor)
        ref = f"Listas!${letra}$2:${letra}${len(col.valores) + 1}"

        dv = DataValidation(type="list", formula1=f"={ref}", allow_blank=not col.obrigatoria)
        dv.error = f"Valor inválido. Use um dos itens da lista: {', '.join(col.valores)}."
        dv.errorTitle = "Valor fora do domínio"
        dv.prompt = col.descricao
        dv.promptTitle = col.nome
        ws.add_data_validation(dv)
        alvo = get_column_letter(j)
        dv.add(f"{alvo}2:{alvo}{ultima_linha}")
        coluna_lista += 1

    for j, col in enumerate(aba.colunas, start=1):
        if col.tipo != "data":
            continue
        dv = DataValidation(
            type="date", operator="between",
            formula1="DATE(1950,1,1)", formula2="DATE(2100,12,31)",
            allow_blank=not col.obrigatoria,
        )
        dv.error = "Informe uma data válida no formato dd/mm/aaaa."
        dv.errorTitle = "Data inválida"
        ws.add_data_validation(dv)
        alvo = get_column_letter(j)
        dv.add(f"{alvo}2:{alvo}{ultima_linha}")

    return coluna_lista


def gerar_template(chave: str, destino: Path | None = None) -> Path:
    tpl = TEMPLATES[chave]
    destino = Path(destino) if destino else config.TEMPLATE_DIR / tpl.arquivo
    destino.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws_inst = wb.active
    ws_inst.title = "Instruções"
    _monta_instrucoes(ws_inst, tpl)

    ws_listas = wb.create_sheet("Listas")
    proxima = 1
    for aba in tpl.abas:
        ws = wb.create_sheet(aba.nome)
        proxima = _monta_dados(ws, aba, ws_listas, proxima)
    ws_listas.sheet_state = "hidden"

    wb.properties.title = tpl.titulo
    wb.properties.creator = "Sistema de Consolidação de Carteiras — UP 2026"
    wb.save(destino)
    return destino


def gerar_todos(destino_dir: Path | None = None) -> list[Path]:
    destino_dir = Path(destino_dir) if destino_dir else config.TEMPLATE_DIR
    return [gerar_template(chave, destino_dir / tpl.arquivo) for chave, tpl in TEMPLATES.items()]
