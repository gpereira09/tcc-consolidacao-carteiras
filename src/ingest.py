"""
Validação e importação dos templates padronizados (UC-04, RF-004 a RF-006).

O processo tem duas chamadas, como no UC-04:

  1. `validar()` lê o arquivo e não grava nada. Primeiro faz a validação
     estrutural (abas e colunas, na ordem do template). Um erro estrutural
     aborta o arquivo inteiro com `ErroEstrutural` (RN-003). Depois faz a
     validação semântica linha a linha (RN-002): obrigatoriedade, tipos,
     domínios, coerência quantidade × preço × valor, datas, moeda × mercado
     e, no template PE/VC, RN-004 e RN-005 em ordem cronológica. O resultado
     é uma `Previa` com as linhas válidas, as rejeitadas e os problemas de
     cada uma (erros e avisos).
  2. `importar()` persiste, em uma transação, apenas as linhas válidas da
     prévia confirmada (RN-003), grava IMPORTACAO_LOG e registra
     IMPORTAR_TEMPLATE na auditoria. A carteira precisa estar visível ao
     usuário (RN-008, RN-013).

As colunas vêm de `src.schema`, a mesma especificação usada pelo gerador
dos templates, de modo que gerador e validador não divergem.

Decisões de persistência:
  * Ativos líquidos são cadastro comum (chave ticker + mercado). Cada linha
    do template vira uma TRANSACAO de compra, e linhas repetidas do mesmo
    ativo são consolidadas por preço médio ponderado (FA-02). Se o ativo já
    está na carteira, as quantidades são somadas (FA-03).
  * O preço médio do template não é gravado em PRECO_HISTORICO: a série de
    um ativo listado é compartilhada entre clientes e só recebe preços de
    mercado. Sem preço de mercado, a consolidação usa o preço médio da
    posição (RN-007). O enriquecimento por API (RN-006) roda depois do
    commit, e uma falha só marca o preço como desatualizado (FA-01).
  * Fundos PE/VC e imóveis pertencem à carteira. Um fundo já cadastrado é
    atualizado (UC-08 FA-01), e cada avaliação de imóvel é versionada em
    PRECO_HISTORICO.
"""
from __future__ import annotations

import io
import json
import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, BinaryIO

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel
from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from src import audit
from src.calc import pe as calc_pe
from src.formatacao import numero
from src.models import (
    AssetMapping,
    Ativo,
    AtivoIliquido,
    AtivoImovel,
    Carteira,
    EventoPE,
    ImportacaoLog,
    Posicao,
    Transacao,
    Usuario,
)
from src.schema import TEMPLATES, Aba, Coluna, Template
from src.security import ErroAutorizacao, pode_acessar_carteira

ERRO = "ERRO"
AVISO = "AVISO"
FONTE_TEMPLATE = "TEMPLATE"

Arquivo = str | Path | bytes | bytearray | BinaryIO


class ErroEstrutural(Exception):
    """RN-003: o arquivo não corresponde ao template e é rejeitado por inteiro."""

    def __init__(self, problemas: list["Problema"]) -> None:
        self.problemas = problemas
        super().__init__("; ".join(p.mensagem for p in problemas))


@dataclass
class Problema:
    aba: str | None
    linha: int | None
    coluna: str | None
    mensagem: str
    nivel: str = ERRO

    def __str__(self) -> str:
        onde = " · ".join(
            p for p in (self.aba, f"linha {self.linha}" if self.linha else None, self.coluna) if p
        )
        return f"[{self.nivel}] {onde}: {self.mensagem}" if onde else f"[{self.nivel}] {self.mensagem}"


@dataclass
class Registro:
    """Uma linha da planilha, com os valores já convertidos para o tipo da coluna."""

    aba: str
    linha: int
    dados: dict[str, Any]
    valido: bool = True


@dataclass
class Previa:
    tipo: str
    nome_arquivo: str
    carteira_id: str | None
    registros: list[Registro] = field(default_factory=list)
    problemas: list[Problema] = field(default_factory=list)

    @property
    def template(self) -> Template:
        return TEMPLATES[self.tipo]

    @property
    def linhas_lidas(self) -> int:
        return len(self.registros)

    @property
    def validos(self) -> list[Registro]:
        return [r for r in self.registros if r.valido]

    @property
    def linhas_validas(self) -> int:
        return len(self.validos)

    @property
    def linhas_rejeitadas(self) -> int:
        return self.linhas_lidas - self.linhas_validas

    @property
    def erros(self) -> list[Problema]:
        return [p for p in self.problemas if p.nivel == ERRO]

    @property
    def avisos(self) -> list[Problema]:
        return [p for p in self.problemas if p.nivel == AVISO]

    @property
    def pode_importar(self) -> bool:
        return self.linhas_validas > 0

    def da_aba(self, aba: str, apenas_validos: bool = False) -> list[Registro]:
        return [r for r in self.registros if r.aba == aba and (r.valido or not apenas_validos)]

    def problemas_da_linha(self, aba: str, linha: int) -> list[Problema]:
        return [p for p in self.problemas if p.aba == aba and p.linha == linha]


@dataclass
class ResultadoImportacao:
    importacao_id: str
    tipo: str
    linhas_importadas: int
    linhas_rejeitadas: int
    criados: list[str] = field(default_factory=list)
    atualizados: list[str] = field(default_factory=list)
    eventos: int = 0
    desatualizados: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- leitura


def _abrir(arquivo: Arquivo):
    if isinstance(arquivo, (bytes, bytearray)):
        arquivo = io.BytesIO(arquivo)
    try:
        return load_workbook(arquivo, read_only=True, data_only=True)
    except Exception:
        raise ErroEstrutural([Problema(
            None, None, None,
            "Arquivo ilegível ou não é uma planilha XLSX. Baixe o template novamente "
            "e preencha sem alterar o formato.",
        )]) from None


def _nome_arquivo(arquivo: Arquivo, nome: str | None) -> str:
    if nome:
        return nome
    if isinstance(arquivo, (str, Path)):
        return Path(arquivo).name
    return getattr(arquivo, "name", None) or "planilha.xlsx"


def _vazio(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


def _cabecalho(ws) -> list[str]:
    primeira = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
    nomes = ["" if v is None else str(v).strip() for v in primeira]
    while nomes and not nomes[-1]:
        nomes.pop()
    return nomes


def _colunas_lidas(ws, aba: Aba) -> tuple[list[Coluna], list[str]]:
    """
    Colunas presentes, na ordem do template. As de extensão (`extensao=True`)
    podem faltar no fim, o que aceita arquivos gerados antes de existirem.
    Devolve as colunas aceitas e a lista de divergências.
    """
    lidos = _cabecalho(ws)
    esperados = [c.nome for c in aba.colunas]
    minimo = len(esperados)
    while minimo and aba.colunas[minimo - 1].extensao:
        minimo -= 1

    normalizados = [n.lower() for n in lidos]
    if minimo <= len(lidos) <= len(esperados) and normalizados == esperados[:len(lidos)]:
        return aba.colunas[:len(lidos)], []

    divergencias: list[str] = []
    for i in range(max(len(lidos), len(esperados))):
        lido = lidos[i] if i < len(lidos) else None
        esperado = esperados[i] if i < len(esperados) else None
        if lido is not None and esperado is not None and lido.lower() != esperado:
            divergencias.append(f"coluna {i + 1}: esperado '{esperado}', encontrado '{lido or '(vazio)'}'")
        elif lido is None and esperado is not None and i < minimo:
            divergencias.append(f"coluna obrigatória '{esperado}' ausente")
        elif esperado is None:
            divergencias.append(f"coluna '{lido}' não pertence ao template")
    return [], divergencias


def _validar_estrutura(wb, tpl: Template) -> dict[str, list[Coluna]]:
    problemas: list[Problema] = []
    colunas: dict[str, list[Coluna]] = {}
    for aba in tpl.abas:
        if aba.nome not in wb.sheetnames:
            problemas.append(Problema(aba.nome, None, None, f"Aba '{aba.nome}' não encontrada."))
            continue
        aceitas, divergencias = _colunas_lidas(wb[aba.nome], aba)
        if divergencias:
            problemas.append(Problema(
                aba.nome, 1, None,
                "Cabeçalho fora do padrão (não renomeie, não reordene e não exclua colunas): "
                + "; ".join(divergencias) + ".",
            ))
        else:
            colunas[aba.nome] = aceitas

    if problemas:
        titulo = (wb.properties.title or "") if wb.properties else ""
        outro = next((t for t in TEMPLATES.values() if t.titulo == titulo and t.chave != tpl.chave), None)
        if outro:
            problemas.insert(0, Problema(
                None, None, None,
                f"O arquivo é o '{outro.titulo}', mas foi enviado como '{tpl.titulo}'.",
            ))
        raise ErroEstrutural(problemas)
    return colunas


# --------------------------------------------------------------------------- conversão


_NUMERO = re.compile(r"^[+-]?\d+(\.\d+)?$")


def _texto(valor: Any) -> str:
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return str(valor).strip()


def _decimal(valor: Any) -> float:
    """Aceita número nativo, '1234.56', '1234,56', '1.234,56' e '1,234.56'."""
    if isinstance(valor, bool):
        raise ValueError
    if isinstance(valor, (int, float)):
        if not math.isfinite(valor):
            raise ValueError
        return float(valor)
    s = str(valor).strip().replace(" ", "").replace(" ", "")
    for simbolo in ("R$", "US$", "€", "£", "$"):
        s = s.replace(simbolo, "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if s.count(",") > 1:
            raise ValueError
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    if not _NUMERO.match(s):
        raise ValueError
    return float(s)


def _data(valor: Any) -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return from_excel(valor).date()
    s = str(valor).strip()
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, formato).date()
        except ValueError:
            continue
    raise ValueError


def _item_lista(valor: Any) -> str:
    s = unicodedata.normalize("NFKD", _texto(valor))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[\s\-]+", "_", s.upper())


def _converter(col: Coluna, bruto: Any) -> Any:
    """Valor tipado da célula; ValueError com a mensagem quando não converte."""
    if col.tipo == "texto":
        return _texto(bruto)
    if col.tipo in ("decimal", "inteiro"):
        try:
            valor = _decimal(bruto)
        except ValueError:
            raise ValueError(f"'{bruto}' não é um número válido.") from None
        if col.tipo == "inteiro" and not valor.is_integer():
            raise ValueError(f"'{bruto}' deve ser um número inteiro.")
        return valor
    if col.tipo == "data":
        try:
            return _data(bruto)
        except (ValueError, OverflowError):
            raise ValueError(f"'{bruto}' não é uma data válida (use dd/mm/aaaa).") from None
    item = _item_lista(bruto)
    if item not in col.valores:
        raise ValueError(f"'{bruto}' fora da lista. Use: {', '.join(col.valores)}.")
    return item


class _Coletor:
    """Acumula os problemas da prévia e marca as linhas rejeitadas."""

    def __init__(self, previa: Previa) -> None:
        self.previa = previa

    def erro(self, reg: Registro | None, coluna: str | None, mensagem: str, aba: str | None = None) -> None:
        if reg is not None:
            reg.valido = False
        self.previa.problemas.append(Problema(
            reg.aba if reg else aba, reg.linha if reg else None, coluna, mensagem, ERRO))

    def aviso(self, reg: Registro, coluna: str | None, mensagem: str) -> None:
        self.previa.problemas.append(Problema(reg.aba, reg.linha, coluna, mensagem, AVISO))


def _ler_aba(ws, aba: Aba, colunas: list[Coluna], col: _Coletor) -> list[Registro]:
    registros: list[Registro] = []
    exemplos = _linhas_exemplo(aba, colunas)
    for n, valores in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        valores = list(valores[:len(colunas)]) + [None] * max(0, len(colunas) - len(valores))
        if all(_vazio(v) for v in valores):
            continue
        reg = Registro(aba.nome, n, {})
        for coluna, bruto in zip(colunas, valores):
            if _vazio(bruto):
                reg.dados[coluna.nome] = None
                if coluna.obrigatoria:
                    col.erro(reg, coluna.nome, "Campo obrigatório vazio.")
                continue
            try:
                reg.dados[coluna.nome] = _converter(coluna, bruto)
            except ValueError as exc:
                reg.dados[coluna.nome] = None
                col.erro(reg, coluna.nome, str(exc))
        for coluna in aba.colunas[len(colunas):]:
            reg.dados[coluna.nome] = None
        if tuple(reg.dados.get(c.nome) for c in colunas) in exemplos:
            col.aviso(reg, None, "Linha idêntica a um exemplo do template. Apague-a se não for uma posição real.")
        registros.append(reg)
    return registros


def _linhas_exemplo(aba: Aba, colunas: list[Coluna]) -> set[tuple]:
    saida: set[tuple] = set()
    for exemplo in aba.exemplos:
        valores = []
        for coluna, bruto in zip(colunas, list(exemplo) + [None] * len(colunas)):
            try:
                valores.append(None if _vazio(bruto) else _converter(coluna, bruto))
            except ValueError:
                valores.append(None)
        saida.add(tuple(valores))
    return saida


# --------------------------------------------------------------------------- validação


def validar(
    arquivo: Arquivo,
    tipo: str,
    sessao: Session | None = None,
    carteira_id: str | None = None,
    nome_arquivo: str | None = None,
) -> Previa:
    """
    UC-04, passos 4 a 9: valida o arquivo e devolve a prévia, sem gravar.

    Com `sessao` e `carteira_id`, a prévia também considera o que já está
    cadastrado (fundos e imóveis da carteira, eventos anteriores para RN-004
    e RN-005, posições a somar e o último preço de mercado conhecido).
    """
    chave = tipo.upper()
    if chave not in TEMPLATES:
        raise ValueError(f"Tipo de template desconhecido: {tipo}. Use {', '.join(TEMPLATES)}.")
    tpl = TEMPLATES[chave]
    wb = _abrir(arquivo)
    try:
        colunas = _validar_estrutura(wb, tpl)
        previa = Previa(chave, _nome_arquivo(arquivo, nome_arquivo), carteira_id)
        col = _Coletor(previa)
        for aba in tpl.abas:
            previa.registros.extend(_ler_aba(wb[aba.nome], aba, colunas[aba.nome], col))
    finally:
        wb.close()

    if not previa.registros:
        col.erro(None, None, "Nenhuma linha preenchida no arquivo.", aba=tpl.abas[0].nome)
        return previa

    contexto = (sessao, carteira_id) if sessao is not None and carteira_id else (None, None)
    if chave == "LIQUIDOS":
        _validar_liquidos(previa, col, *contexto)
    elif chave == "PEVC":
        _validar_pevc(previa, col, *contexto)
    else:
        _validar_imoveis(previa, col, *contexto)
    previa.problemas.sort(key=lambda p: (p.aba or "", p.linha or 0, p.nivel != ERRO))
    return previa


def _positivo(reg: Registro, col: _Coletor, *campos: str, zero: bool = False) -> None:
    for campo in campos:
        valor = reg.dados.get(campo)
        if valor is not None and (valor < 0 or (valor == 0 and not zero)):
            col.erro(reg, campo, "Deve ser maior que zero." if not zero else "Não pode ser negativo.")


def _nao_futura(reg: Registro, col: _Coletor, campo: str) -> None:
    valor = reg.dados.get(campo)
    if valor is not None and valor > config.hoje():
        col.erro(reg, campo, f"Data futura ({valor:%d/%m/%Y}) não é aceita.")


def _identificador(reg: Registro, campo: str) -> str | None:
    valor = reg.dados.get(campo)
    if valor:
        reg.dados[campo] = valor.upper()
    return reg.dados.get(campo)


def _duplicados(registros: list[Registro], campo: str, col: _Coletor) -> dict[str, Registro]:
    """UC-08 EX-01: rejeita a segunda ocorrência de um identificador e indica a original."""
    primeiro: dict[str, Registro] = {}
    for reg in registros:
        ident = _identificador(reg, campo)
        if not ident:
            continue
        if ident in primeiro:
            col.erro(reg, campo, f"Identificador {ident} repetido: já declarado na linha "
                                 f"{primeiro[ident].linha}.")
        else:
            primeiro[ident] = reg
    return primeiro


# ---- líquidos


def normalizar_ticker(sessao: Session | None, bruto: str, classe: str | None,
                      mercado: str | None) -> str:
    """
    Ticker canônico (seção 2.3.2, Quadro 10): consulta ASSET_MAPPING, retira o
    sufixo de fonte da B3 (PETR4.SA, PETR4.SAO → PETR4) e deixa o CNPJ de
    fundo só com dígitos.
    """
    t = re.sub(r"\s+", "", bruto).upper()
    digitos = re.sub(r"\D", "", t)
    if classe == "FUNDO" and len(digitos) == 14 and re.fullmatch(r"[\d./-]+", t):
        return digitos
    if sessao is not None:
        mapeado = sessao.scalar(
            select(AssetMapping.ticker_canonical).where(AssetMapping.ticker_source == t).limit(1)
        )
        if mapeado:
            return mapeado
    if mercado == "B3":
        for sufixo in (".SAO", ".SA"):
            if t.endswith(sufixo):
                return t[: -len(sufixo)]
    return t


def _ativo_listado(sessao: Session, ticker: str, mercado: str) -> Ativo | None:
    return sessao.scalar(select(Ativo).where(
        Ativo.ticker_canonico == ticker, Ativo.mercado == mercado, Ativo.carteira_id.is_(None)))


def _posicao(sessao: Session, carteira_id: str, ativo_id: str) -> Posicao | None:
    return sessao.scalar(select(Posicao).where(
        Posicao.carteira_id == carteira_id, Posicao.ativo_id == ativo_id))


def _validar_liquidos(previa: Previa, col: _Coletor, sessao: Session | None,
                      carteira_id: str | None) -> None:
    tolerancia = config.TOLERANCIA_VALOR_FINANCEIRO
    for reg in previa.registros:
        d = reg.dados
        _positivo(reg, col, "quantidade", "preco_medio", "valor_financeiro")
        q, pm, vf = d.get("quantidade"), d.get("preco_medio"), d.get("valor_financeiro")
        if all(v is not None and v > 0 for v in (q, pm, vf)):
            esperado = q * pm
            if abs(vf - esperado) > tolerancia * esperado:
                col.erro(reg, "valor_financeiro",
                         f"{numero(vf)} não confere com quantidade × preco_medio = {numero(esperado)} "
                         f"(tolerância de {tolerancia:.0%}).")
        _nao_futura(reg, col, "data_aquisicao")

        mercado, moeda = d.get("mercado"), d.get("moeda")
        if mercado and moeda and config.MOEDA_POR_MERCADO.get(mercado) != moeda:
            col.erro(reg, "moeda", f"Moeda {moeda} incoerente com o mercado {mercado} "
                                   f"(esperado {config.MOEDA_POR_MERCADO[mercado]}).")

        if d.get("ticker_ou_cnpj"):
            ticker = normalizar_ticker(sessao, d["ticker_ou_cnpj"], d.get("classe"), mercado)
            if ticker != d["ticker_ou_cnpj"].upper():
                col.aviso(reg, "ticker_ou_cnpj", f"Ticker {d['ticker_ou_cnpj']} normalizado para {ticker}.")
            d["ticker"] = ticker
        d["listado"] = d.get("classe") in config.CLASSES_RENDA_VARIAVEL

    grupos: dict[tuple, list[Registro]] = defaultdict(list)
    for reg in previa.validos:
        grupos[(reg.dados["ticker"], reg.dados["mercado"])].append(reg)
    for (ticker, mercado), regs in grupos.items():
        base = regs[0].dados
        for reg in regs[1:]:
            for campo in ("classe", "moeda"):
                if reg.dados[campo] != base[campo]:
                    col.erro(reg, campo, f"{ticker} aparece na linha {regs[0].linha} com {campo} "
                                         f"{base[campo]}: o mesmo ativo não pode ter {campo} diferente.")
        validos = [r for r in regs if r.valido]
        if len(validos) > 1:
            linhas = ", ".join(str(r.linha) for r in validos)
            for reg in validos[1:]:
                col.aviso(reg, "ticker_ou_cnpj", f"{ticker} repetido (linhas {linhas}): posições "
                                                 "consolidadas por preço médio ponderado.")

    if sessao is None:
        return
    from src.market import precos

    for (ticker, mercado), regs in grupos.items():
        regs = [r for r in regs if r.valido]
        if not regs:
            continue
        ativo = _ativo_listado(sessao, ticker, mercado)
        if ativo is None:
            continue
        for reg in regs:
            if ativo.moeda != reg.dados["moeda"]:
                col.erro(reg, "moeda", f"{ticker} já está cadastrado em {ativo.moeda}.")
        regs = [r for r in regs if r.valido]
        if regs and carteira_id and _posicao(sessao, carteira_id, ativo.id):
            col.aviso(regs[0], "ticker_ou_cnpj", f"{ticker} já existe na carteira: as quantidades "
                                                 "serão somadas e o preço médio recalculado.")
        mercado_atual = precos.preco_mais_recente(sessao, ativo.id)
        if mercado_atual and mercado_atual.fonte != FONTE_TEMPLATE:
            for reg in regs:
                reg.dados["preco_mercado"] = mercado_atual.preco_fechamento
                reg.dados["data_preco_mercado"] = mercado_atual.data
                pm = reg.dados["preco_medio"]
                reg.dados["diferencial"] = mercado_atual.preco_fechamento / pm - 1.0 if pm else None


# ---- PE/VC


def _fundo_da_carteira(sessao: Session, carteira_id: str, ident: str) -> AtivoIliquido | None:
    return sessao.scalar(
        select(AtivoIliquido).join(Ativo, Ativo.id == AtivoIliquido.ativo_id)
        .where(Ativo.carteira_id == carteira_id, AtivoIliquido.identificador_fundo == ident)
    )


def _validar_pevc(previa: Previa, col: _Coletor, sessao: Session | None,
                  carteira_id: str | None) -> None:
    fundos = previa.da_aba("Fundos")
    eventos = previa.da_aba("Eventos")

    for reg in fundos:
        d = reg.dados
        _positivo(reg, col, "commitment")
        _nao_futura(reg, col, "data_inicio")
        if d.get("data_inicio") and d.get("data_fim_prevista") and d["data_fim_prevista"] <= d["data_inicio"]:
            col.erro(reg, "data_fim_prevista", "O encerramento previsto deve ser posterior à data de início.")
    por_id = _duplicados(fundos, "identificador_fundo", col)

    existentes: dict[str, AtivoIliquido] = {}
    if sessao is not None:
        for ident, reg in por_id.items():
            fundo = _fundo_da_carteira(sessao, carteira_id, ident)
            if fundo is None:
                continue
            existentes[ident] = fundo
            if fundo.ativo.moeda != reg.dados.get("moeda"):
                col.erro(reg, "moeda", f"O fundo {ident} já está cadastrado em {fundo.ativo.moeda}.")
            elif reg.valido:
                col.aviso(reg, "identificador_fundo", f"Fundo {ident} já cadastrado: commitment, gestor "
                                                      "e encerramento previsto serão atualizados.")

    def fundo_db(ident: str) -> AtivoIliquido | None:
        if ident not in existentes and sessao is not None and ident not in por_id:
            fundo = _fundo_da_carteira(sessao, carteira_id, ident)
            if fundo is not None:
                existentes[ident] = fundo
        return existentes.get(ident)

    hoje = config.hoje()
    for reg in eventos:
        d = reg.dados
        ident = _identificador(reg, "identificador_fundo")
        if not ident:
            continue
        reg_fundo, banco = por_id.get(ident), fundo_db(ident)
        if reg_fundo is None and banco is None:
            col.erro(reg, "identificador_fundo", f"Fundo {ident} não está na aba 'Fundos' nem cadastrado na carteira.")
            continue
        if reg_fundo is not None and not reg_fundo.valido:
            col.erro(reg, "identificador_fundo", f"O fundo {ident} foi rejeitado (aba 'Fundos', linha {reg_fundo.linha}).")
            continue

        tipo, status = d.get("tipo_evento"), d.get("status")
        if tipo and status and status not in config.STATUS_POR_TIPO_EVENTO[tipo]:
            col.erro(reg, "status", f"Status {status} não se aplica a {tipo}. Use: "
                                    f"{', '.join(sorted(config.STATUS_POR_TIPO_EVENTO[tipo]))}.")
        _positivo(reg, col, "valor_evento")

        inicio = reg_fundo.dados.get("data_inicio") if reg_fundo else banco.data_inicio
        data_evento = d.get("data_evento")
        if data_evento and inicio and data_evento < inicio:
            col.erro(reg, "data_evento", f"Evento anterior ao início do fundo ({inicio:%d/%m/%Y}).")
        if data_evento and data_evento > hoje and not (tipo == "CAPITAL_CALL" and status == "PENDENTE"):
            col.erro(reg, "data_evento", "Data futura só é aceita em CAPITAL_CALL com status PENDENTE.")

        if d.get("nav_unidade") is not None:
            if tipo != "NAV_UPDATE":
                col.aviso(reg, "nav_unidade", "nav_unidade só se aplica a NAV_UPDATE e foi ignorado.")
                d["nav_unidade"] = None
            else:
                _positivo(reg, col, "nav_unidade")
        if d.get("subtipo") is not None and tipo != "DISTRIBUICAO":
            col.aviso(reg, "subtipo", "subtipo só se aplica a DISTRIBUICAO e foi ignorado.")
            d["subtipo"] = None
        elif tipo == "DISTRIBUICAO" and d.get("subtipo") is None:
            col.aviso(reg, "subtipo", "Distribuição sem subtipo: não será tratada como retorno de capital.")

        if banco is not None and reg.valido and any(
            e.data_evento == data_evento and e.tipo_evento == tipo and e.status == status
            and abs(e.valor - d["valor_evento"]) < 0.005 for e in banco.eventos
        ):
            col.erro(reg, None, "Evento idêntico já registrado neste fundo (importação repetida?).")

    _checar_cronologia(previa, col, por_id, existentes)


def _checar_cronologia(previa: Previa, col: _Coletor, por_id: dict[str, Registro],
                       existentes: dict[str, AtivoIliquido]) -> None:
    """RN-004 e RN-005 avaliadas na ordem dos eventos, somando os já registrados."""
    por_fundo: dict[str, list[Registro]] = defaultdict(list)
    for reg in previa.da_aba("Eventos", apenas_validos=True):
        por_fundo[reg.dados["identificador_fundo"]].append(reg)

    for ident, regs in por_fundo.items():
        reg_fundo, banco = por_id.get(ident), existentes.get(ident)
        commitment = reg_fundo.dados["commitment"] if reg_fundo else banco.commitment
        linha_do_tempo: list[tuple] = []
        if banco is not None:
            linha_do_tempo += [
                (e.data_evento, 0, 0, e.tipo_evento, e.status, e.valor, None)
                for e in banco.eventos if e.status != "CANCELADO"
            ]
        linha_do_tempo += [
            (r.dados["data_evento"], 1, r.linha, r.dados["tipo_evento"], r.dados["status"],
             r.dados["valor_evento"], r)
            for r in regs if r.dados["status"] != "CANCELADO"
        ]
        comprometido = pago = 0.0
        for _, _, _, tipo, status, valor, reg in sorted(linha_do_tempo, key=lambda t: t[:3]):
            try:
                if tipo == "CAPITAL_CALL" and status in ("PAGO", "PENDENTE"):
                    if reg is not None:
                        calc_pe.checar_commitment(commitment, comprometido, valor, ident)
                    comprometido += valor
                    pago += valor if status == "PAGO" else 0.0
                elif tipo == "DISTRIBUICAO" and status == "RECEBIDO" and reg is not None:
                    calc_pe.checar_distribuicao(pago, valor, ident)
            except ValueError as exc:
                col.erro(reg, "valor_evento", str(exc))


# ---- imóveis


def _imovel_da_carteira(sessao: Session, carteira_id: str, ident: str) -> AtivoImovel | None:
    return sessao.scalar(
        select(AtivoImovel).join(Ativo, Ativo.id == AtivoImovel.ativo_id)
        .where(Ativo.carteira_id == carteira_id, AtivoImovel.identificador_imovel == ident)
    )


def _validar_imoveis(previa: Previa, col: _Coletor, sessao: Session | None,
                     carteira_id: str | None) -> None:
    for reg in previa.registros:
        d = reg.dados
        _positivo(reg, col, "valor_aquisicao", "valor_mercado_atual", "area_m2")
        _positivo(reg, col, "renda_mensal", "despesas_mensais", zero=True)
        _nao_futura(reg, col, "data_aquisicao")
        _nao_futura(reg, col, "data_avaliacao")
        if d.get("data_aquisicao") and d.get("data_avaliacao") and d["data_avaliacao"] < d["data_aquisicao"]:
            col.erro(reg, "data_avaliacao", "A avaliação não pode ser anterior à aquisição.")
        renda, despesas = d.get("renda_mensal") or 0.0, d.get("despesas_mensais") or 0.0
        if renda > 0 and despesas > renda:
            col.aviso(reg, "despesas_mensais", "Despesas maiores que a renda: o yield líquido ficará negativo.")
    por_id = _duplicados(previa.registros, "identificador_imovel", col)

    if sessao is None:
        return
    for ident, reg in por_id.items():
        imovel = _imovel_da_carteira(sessao, carteira_id, ident)
        if imovel is None or not reg.valido:
            continue
        if imovel.ativo.moeda != reg.dados["moeda"]:
            col.erro(reg, "moeda", f"O imóvel {ident} já está cadastrado em {imovel.ativo.moeda}.")
        elif reg.dados["data_avaliacao"] < imovel.data_avaliacao:
            col.aviso(reg, "data_avaliacao", f"Avaliação anterior à vigente ({imovel.data_avaliacao:%d/%m/%Y}): "
                                             "entra apenas no histórico.")
        else:
            col.aviso(reg, "identificador_imovel", f"Imóvel {ident} já cadastrado: a avaliação será "
                                                   "atualizada e versionada no histórico.")


# --------------------------------------------------------------------------- importação


def importar(
    sessao: Session,
    previa: Previa,
    carteira_id: str | None = None,
    usuario: Usuario | None = None,
    enriquecer: bool = True,
) -> ResultadoImportacao:
    """
    UC-04, passos 10 a 12: grava as linhas válidas da prévia em uma transação.

    `usuario`, quando informado, precisa enxergar a carteira (RN-008, RN-013).
    Com `enriquecer`, os ativos listados recebem preço de mercado depois do
    commit; uma falha de coleta não desfaz a importação (FA-01).
    """
    carteira_id = carteira_id or previa.carteira_id
    if not carteira_id or sessao.get(Carteira, carteira_id) is None:
        raise ValueError("Carteira inexistente.")
    if previa.carteira_id and previa.carteira_id != carteira_id:
        raise ValueError("A prévia foi validada para outra carteira. Valide o arquivo novamente.")
    if usuario is not None and not pode_acessar_carteira(sessao, usuario, carteira_id):
        raise ErroAutorizacao("RN-008: não é permitido importar posições para esta carteira.")
    if not previa.pode_importar:
        raise ValueError("Nenhuma linha válida para importar.")

    resultado = ResultadoImportacao("", previa.tipo, previa.linhas_validas, previa.linhas_rejeitadas)
    listados: list[Ativo] = []
    try:
        if previa.tipo == "LIQUIDOS":
            listados = _gravar_liquidos(sessao, previa, carteira_id, resultado)
        elif previa.tipo == "PEVC":
            _gravar_pevc(sessao, previa, carteira_id, resultado)
        else:
            _gravar_imoveis(sessao, previa, carteira_id, resultado)

        log = ImportacaoLog(
            carteira_id=carteira_id,
            tipo_template=previa.tipo,
            nome_arquivo=previa.nome_arquivo,
            linhas_lidas=previa.linhas_lidas,
            linhas_importadas=previa.linhas_validas,
            linhas_rejeitadas=previa.linhas_rejeitadas,
            detalhe=json.dumps({
                "criados": resultado.criados,
                "atualizados": resultado.atualizados,
                "eventos": resultado.eventos,
                "problemas": [str(p) for p in previa.problemas[:200]],
            }, ensure_ascii=False),
        )
        sessao.add(log)
        sessao.flush()
        resultado.importacao_id = log.id
        audit.registrar(
            sessao, usuario_id=usuario.id if usuario else None, acao=audit.IMPORTAR_TEMPLATE,
            entidade="CARTEIRA", entidade_id=carteira_id,
            dados_novos={
                "importacao_id": log.id, "tipo": previa.tipo, "arquivo": previa.nome_arquivo,
                "linhas_lidas": previa.linhas_lidas, "linhas_importadas": previa.linhas_validas,
                "linhas_rejeitadas": previa.linhas_rejeitadas,
            },
        )
        sessao.commit()
    except Exception:
        sessao.rollback()
        raise

    if enriquecer and listados:
        from src.market import precos

        for ativo in listados:
            coleta = precos.atualizar_ativo(sessao, ativo)
            if not coleta.sucesso:
                resultado.desatualizados.append(ativo.ticker_canonico)
        sessao.commit()
    return resultado


def _gravar_liquidos(sessao: Session, previa: Previa, carteira_id: str,
                     resultado: ResultadoImportacao) -> list[Ativo]:
    grupos: dict[tuple, list[Registro]] = defaultdict(list)
    for reg in previa.validos:
        grupos[(reg.dados["ticker"], reg.dados["mercado"])].append(reg)

    listados: list[Ativo] = []
    for (ticker, mercado), regs in grupos.items():
        base = regs[0].dados
        ativo = _ativo_listado(sessao, ticker, mercado)
        if ativo is None:
            ativo = Ativo(
                ticker_canonico=ticker, nome=base.get("nome") or ticker, classe=base["classe"],
                moeda=base["moeda"], mercado=mercado, setor=base.get("setor"),
                pais=config.PAIS_POR_MERCADO.get(mercado, config.PAIS_EXTERIOR),
                possui_ticker_listado=base["listado"],
            )
            sessao.add(ativo)
            sessao.flush()
            resultado.criados.append(ticker)
        else:
            if base.get("nome") and ativo.nome == ativo.ticker_canonico:
                ativo.nome = base["nome"]
            if base.get("setor") and not ativo.setor:
                ativo.setor = base["setor"]
        if ativo.possui_ticker_listado:
            listados.append(ativo)

        pos = _posicao(sessao, carteira_id, ativo.id)
        regs = sorted(regs, key=lambda r: (r.dados["data_aquisicao"], r.linha))
        if pos is None:
            pos = Posicao(carteira_id=carteira_id, ativo_id=ativo.id, quantidade=0.0,
                          preco_medio=0.0, valor_financeiro=0.0,
                          data_inicial=regs[0].dados["data_aquisicao"])
            sessao.add(pos)
            sessao.flush()
        else:
            resultado.atualizados.append(ticker)
            sem_transacoes = sessao.scalar(
                select(Transacao.id).where(Transacao.posicao_id == pos.id).limit(1)) is None
            if sem_transacoes and pos.quantidade > 0:
                sessao.add(Transacao(posicao_id=pos.id, tipo="COMPRA", data=pos.data_inicial,
                                     quantidade=pos.quantidade, preco=pos.preco_medio))

        for reg in regs:
            d = reg.dados
            total = pos.quantidade + d["quantidade"]
            pos.preco_medio = (pos.quantidade * pos.preco_medio + d["quantidade"] * d["preco_medio"]) / total
            pos.quantidade = total
            pos.data_inicial = min(pos.data_inicial, d["data_aquisicao"])
            if d.get("observacao") and not pos.observacao:
                pos.observacao = d["observacao"]
            sessao.add(Transacao(posicao_id=pos.id, tipo="COMPRA", data=d["data_aquisicao"],
                                 quantidade=d["quantidade"], preco=d["preco_medio"]))
        pos.valor_financeiro = pos.quantidade * pos.preco_medio
    sessao.flush()
    return listados


def _gravar_pevc(sessao: Session, previa: Previa, carteira_id: str,
                 resultado: ResultadoImportacao) -> None:
    fundos: dict[str, AtivoIliquido] = {}
    for reg in previa.da_aba("Fundos", apenas_validos=True):
        d = reg.dados
        ident = d["identificador_fundo"]
        fundo = _fundo_da_carteira(sessao, carteira_id, ident)
        if fundo is not None:
            fundo.commitment = d["commitment"]
            fundo.gestor = d["gestor"]
            fundo.sub_classe = fundo.ativo.classe = d["sub_classe"]
            fundo.data_fim_prevista = d.get("data_fim_prevista") or fundo.data_fim_prevista
            fundo.ativo.nome = d["nome_fundo"]
            resultado.atualizados.append(ident)
        else:
            ativo = Ativo(
                ticker_canonico=ident, nome=d["nome_fundo"], classe=d["sub_classe"],
                moeda=d["moeda"], pais=config.PAIS_POR_MOEDA.get(d["moeda"], config.PAIS_EXTERIOR),
                possui_ticker_listado=False, carteira_id=carteira_id,
            )
            sessao.add(ativo)
            sessao.flush()
            fundo = AtivoIliquido(
                ativo_id=ativo.id, identificador_fundo=ident, sub_classe=d["sub_classe"],
                gestor=d["gestor"], commitment=d["commitment"], paid_in_capital=0.0,
                dry_powder=d["commitment"], nav_atual=0.0, data_inicio=d["data_inicio"],
                data_fim_prevista=d.get("data_fim_prevista"),
            )
            sessao.add(fundo)
            sessao.add(Posicao(carteira_id=carteira_id, ativo_id=ativo.id, quantidade=1.0,
                               preco_medio=0.0, valor_financeiro=0.0, data_inicial=d["data_inicio"]))
            sessao.flush()
            resultado.criados.append(ident)
        fundos[ident] = fundo

    for reg in previa.da_aba("Eventos", apenas_validos=True):
        d = reg.dados
        ident = d["identificador_fundo"]
        fundo = fundos.get(ident) or _fundo_da_carteira(sessao, carteira_id, ident)
        fundos[ident] = fundo
        sessao.add(EventoPE(
            fundo_id=fundo.id, data_evento=d["data_evento"], tipo_evento=d["tipo_evento"],
            valor=d["valor_evento"], status=d["status"], nav_unidade=d.get("nav_unidade"),
            subtipo=d.get("subtipo"), observacao=d.get("observacao"),
        ))
        resultado.eventos += 1
    sessao.flush()

    for ident, fundo in fundos.items():
        sessao.expire(fundo, ["eventos"])
        m = calc_pe.recalcular_metricas(sessao, fundo)
        if m.paid_in + m.pendente > fundo.commitment + config.TOLERANCIA_COMMITMENT:
            raise ValueError(f"RN-004: os eventos do fundo {ident} excedem o commitment. "
                             "Os dados mudaram desde a validação: valide o arquivo novamente.")


def _gravar_imoveis(sessao: Session, previa: Previa, carteira_id: str,
                    resultado: ResultadoImportacao) -> None:
    from src.market.precos import registrar_preco

    for reg in previa.validos:
        d = reg.dados
        ident = d["identificador_imovel"]
        renda, despesas = d.get("renda_mensal") or 0.0, d.get("despesas_mensais") or 0.0
        imovel = _imovel_da_carteira(sessao, carteira_id, ident)
        if imovel is not None:
            ativo = imovel.ativo
            ativo.nome = d["nome_descritivo"]
            imovel.tipo_imovel = d["tipo_imovel"]
            imovel.aluguel_mensal, imovel.despesas_mensais = renda, despesas
            imovel.endereco = d.get("endereco") or imovel.endereco
            imovel.area_m2 = d.get("area_m2") or imovel.area_m2
            if d["data_avaliacao"] >= imovel.data_avaliacao:
                imovel.valor_de_mercado = d["valor_mercado_atual"]
                imovel.data_avaliacao = d["data_avaliacao"]
                pos = _posicao(sessao, carteira_id, ativo.id)
                if pos is not None:
                    pos.valor_financeiro = imovel.valor_de_mercado
            resultado.atualizados.append(ident)
        else:
            ativo = Ativo(
                ticker_canonico=ident, nome=d["nome_descritivo"], classe=config.CLASSE_IMOVEL,
                moeda=d["moeda"], pais=config.PAIS_POR_MOEDA.get(d["moeda"], config.PAIS_EXTERIOR),
                possui_ticker_listado=False, carteira_id=carteira_id,
            )
            sessao.add(ativo)
            sessao.flush()
            sessao.add(AtivoImovel(
                ativo_id=ativo.id, identificador_imovel=ident, tipo_imovel=d["tipo_imovel"],
                endereco=d.get("endereco"), valor_aquisicao=d["valor_aquisicao"],
                valor_de_mercado=d["valor_mercado_atual"], aluguel_mensal=renda,
                despesas_mensais=despesas, area_m2=d.get("area_m2"),
                data_avaliacao=d["data_avaliacao"],
            ))
            pos = Posicao(carteira_id=carteira_id, ativo_id=ativo.id, quantidade=1.0,
                          preco_medio=d["valor_aquisicao"], valor_financeiro=d["valor_mercado_atual"],
                          data_inicial=d["data_aquisicao"])
            sessao.add(pos)
            sessao.flush()
            sessao.add(Transacao(posicao_id=pos.id, tipo="COMPRA", data=d["data_aquisicao"],
                                 quantidade=1.0, preco=d["valor_aquisicao"]))
            registrar_preco(sessao, ativo.id, d["data_aquisicao"], d["valor_aquisicao"], FONTE_TEMPLATE)
            sessao.flush()
            resultado.criados.append(ident)
        registrar_preco(sessao, ativo.id, d["data_avaliacao"], d["valor_mercado_atual"], FONTE_TEMPLATE)
        sessao.flush()


__all__ = [
    "AVISO", "ERRO", "ErroAutorizacao", "ErroEstrutural", "Previa", "Problema", "Registro",
    "ResultadoImportacao", "importar", "normalizar_ticker", "validar",
]
