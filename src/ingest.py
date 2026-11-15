"""
Camada de Ingestão e Normalização (seções 2.3.1 e 2.3.2).

Implementa a validação em duas etapas exigida pela RN-002:

  1. ESTRUTURAL — presença, nome e ordem das abas e colunas. Qualquer falha
     aborta o arquivo inteiro (RN-003, primeira parte).
  2. SEMÂNTICA — tipos, domínios, coerências e invariantes de negócio,
     avaliada linha a linha. O usuário decide se importa apenas as linhas
     válidas (RN-003, segunda parte).

A importação só toca o banco depois que o usuário confirma o preview.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from src import audit, security
from src.calc import pe
from src.calc.consolidacao import fundos_da_carteira
from src.formatacao import numero, percentual
from src.market.precos import registrar_preco
from src.models import (
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
from src.schema import Aba, Coluna, Template, TEMPLATES


@dataclass
class Ocorrencia:
    """Um problema encontrado, localizado por aba/linha/coluna."""

    aba: str
    linha: int | None
    coluna: str | None
    mensagem: str

    def __str__(self) -> str:
        local = f"{self.aba}"
        if self.linha is not None:
            local += f"!L{self.linha}"
        if self.coluna:
            local += f" [{self.coluna}]"
        return f"{local}: {self.mensagem}"


@dataclass
class Preview:
    """Resultado da leitura + validação, antes da persistência."""

    template: Template
    nome_arquivo: str
    erros_estruturais: list[Ocorrencia] = field(default_factory=list)
    erros_semanticos: list[Ocorrencia] = field(default_factory=list)
    avisos: list[Ocorrencia] = field(default_factory=list)
    validos: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    total_lido: int = 0

    @property
    def abortado(self) -> bool:
        return bool(self.erros_estruturais)

    @property
    def total_valido(self) -> int:
        return sum(len(v) for v in self.validos.values())

    @property
    def total_rejeitado(self) -> int:
        return self.total_lido - self.total_valido

    @property
    def importavel(self) -> bool:
        return not self.abortado and self.total_valido > 0


_NUM_LIMPEZA = re.compile(r"[^\d,.\-]")


def _norm(texto: str) -> str:
    """Normaliza cabeçalho: sem acento, minúsculo, sem espaços extras."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", "_", t.strip().lower())


def coagir_decimal(valor: Any) -> float:
    """
    Aceita 1234.56, '1.234,56', '1,234.56', 'R$ 1.234,56'.
    A heurística usa a posição do último separador para decidir o decimal.
    """
    if valor is None or valor == "":
        raise ValueError("valor numérico ausente")
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor)

    texto = _NUM_LIMPEZA.sub("", str(valor).strip())
    if not texto:
        raise ValueError("valor numérico ausente")

    ult_virgula, ult_ponto = texto.rfind(","), texto.rfind(".")
    if ult_virgula > ult_ponto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(",", "")
    try:
        return float(texto)
    except ValueError as exc:
        raise ValueError(f"'{valor}' não é um número válido") from exc


def coagir_data(valor: Any) -> date:
    if valor is None or valor == "":
        raise ValueError("data ausente")
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{valor}' não é uma data válida (use dd/mm/aaaa)")


def coagir_texto(valor: Any) -> str:
    return "" if valor is None else str(valor).strip()


def _coagir(col: Coluna, bruto: Any) -> Any:
    if col.tipo == "data":
        return coagir_data(bruto)
    if col.tipo in ("decimal", "inteiro"):
        num = coagir_decimal(bruto)
        return int(round(num)) if col.tipo == "inteiro" else num
    texto = coagir_texto(bruto)
    if col.tipo == "lista":
        alvo = texto.upper().replace(" ", "_").replace("-", "_")
        if alvo not in col.valores:
            raise ValueError(
                f"'{texto}' não pertence ao domínio: {', '.join(col.valores)}"
            )
        return alvo
    return texto


def _validar_estrutura(wb, tpl: Template) -> list[Ocorrencia]:
    erros: list[Ocorrencia] = []
    presentes = set(wb.sheetnames)

    for aba in tpl.abas:
        if aba.nome not in presentes:
            erros.append(
                Ocorrencia(aba.nome, None, None,
                           f"Aba obrigatória '{aba.nome}' não encontrada no arquivo. "
                           f"Abas presentes: {', '.join(sorted(presentes))}.")
            )
            continue

        ws = wb[aba.nome]
        cabecalho = [_norm(c.value) if c.value is not None else "" for c in ws[1]]
        while cabecalho and not cabecalho[-1]:
            cabecalho.pop()

        esperado = [c.nome for c in aba.colunas if not c.extensao]
        extensoes = [c.nome for c in aba.colunas if c.extensao]
        excedente = [c for c in cabecalho[len(esperado):] if c]
        if cabecalho[: len(esperado)] == esperado and excedente \
                and excedente != extensoes[: len(excedente)]:
            erros.append(Ocorrencia(
                aba.nome, 1, None,
                f"Cabeçalho inválido — colunas após '{esperado[-1]}' devem ser, nesta ordem: "
                f"{', '.join(extensoes) or '(nenhuma)'}."))
            continue
        if cabecalho[: len(esperado)] != esperado:
            faltando = [c for c in esperado if c not in cabecalho]
            sobrando = [c for c in cabecalho if c and c not in esperado]
            detalhe = []
            if faltando:
                detalhe.append(f"colunas ausentes: {', '.join(faltando)}")
            if sobrando:
                detalhe.append(f"colunas desconhecidas: {', '.join(sobrando)}")
            if not detalhe:
                detalhe.append(
                    f"ordem das colunas divergente. Esperado: {', '.join(esperado)}"
                )
            erros.append(Ocorrencia(aba.nome, 1, None,
                                    "Cabeçalho inválido — " + "; ".join(detalhe) + "."))
    return erros


def _vazio(valor: Any) -> bool:
    return valor is None or str(valor).strip() == ""


def _ler_linhas(ws, aba: Aba) -> list[tuple[int, dict[str, Any]]]:
    """Devolve [(numero_linha_excel, {coluna: valor_bruto})], ignorando linhas vazias."""
    saida: list[tuple[int, dict[str, Any]]] = []
    nomes = [c.nome for c in aba.colunas]
    for idx, linha in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        valores = list(linha[: len(nomes)])
        if all(_vazio(v) for v in valores):
            continue
        saida.append((idx, dict(zip(nomes, valores))))
    return saida


def _tipar_linha(aba: Aba, bruto: dict[str, Any], nlinha: int) -> tuple[dict[str, Any], list[Ocorrencia]]:
    """Aplica obrigatoriedade e coerção de tipo. Não avalia regras de negócio."""
    tipado: dict[str, Any] = {}
    erros: list[Ocorrencia] = []
    for col in aba.colunas:
        valor = bruto.get(col.nome)
        if _vazio(valor):
            if col.obrigatoria:
                erros.append(Ocorrencia(aba.nome, nlinha, col.nome,
                                        "campo obrigatório não preenchido"))
            tipado[col.nome] = None
            continue
        try:
            tipado[col.nome] = _coagir(col, valor)
        except ValueError as exc:
            erros.append(Ocorrencia(aba.nome, nlinha, col.nome, str(exc)))
            tipado[col.nome] = None
    return tipado, erros


def _coletor(aba: str, nlinha: int):
    """Lista de ocorrências da linha e a função que acrescenta uma nova."""
    erros: list[Ocorrencia] = []
    return erros, lambda coluna, msg: erros.append(Ocorrencia(aba, nlinha, coluna, msg))


def _exigir_positivos(reg: dict, campos: tuple[str, ...], add) -> None:
    for campo in campos:
        v = reg.get(campo)
        if v is not None and v <= 0:
            add(campo, "deve ser maior que zero")


def _proibir_datas_futuras(reg: dict, campos: tuple[str, ...], add) -> None:
    hoje = config.hoje()
    for campo in campos:
        d = reg.get(campo)
        if d and d > hoje:
            add(campo, "não pode ser uma data futura")


def semantica_liquidos(reg: dict, nlinha: int) -> list[Ocorrencia]:
    e, add = _coletor("Dados", nlinha)

    _exigir_positivos(reg, ("quantidade", "preco_medio", "valor_financeiro"), add)

    q, pm, vf = reg.get("quantidade"), reg.get("preco_medio"), reg.get("valor_financeiro")
    if None not in (q, pm, vf) and vf > 0:
        esperado = q * pm
        if abs(esperado - vf) / vf > config.TOLERANCIA_VALOR_FINANCEIRO:
            add("valor_financeiro",
                f"incoerente com quantidade x preco_medio (esperado ≈ {numero(esperado)}, "
                f"informado {numero(vf)}; tolerância de "
                f"{percentual(config.TOLERANCIA_VALOR_FINANCEIRO, 0)})")

    _proibir_datas_futuras(reg, ("data_aquisicao",), add)

    mercado, moeda = reg.get("mercado"), reg.get("moeda")
    if mercado and moeda:
        esperada = config.MOEDA_POR_MERCADO.get(mercado)
        if esperada and esperada != moeda:
            add("moeda", f"incoerente com o mercado {mercado} (esperado {esperada})")

    classe, ticker = reg.get("classe"), reg.get("ticker_ou_cnpj")
    if classe == "FUNDO" and ticker and ticker.isdigit() and len(ticker) not in (14, 8):
        add("ticker_ou_cnpj", "CNPJ deve ter 14 dígitos (ou 8, se raiz)")
    return e


def _semantica_pevc_fundo(reg: dict, nlinha: int) -> list[Ocorrencia]:
    e, add = _coletor("Fundos", nlinha)

    _exigir_positivos(reg, ("commitment",), add)
    _proibir_datas_futuras(reg, ("data_inicio",), add)
    ini, fim = reg.get("data_inicio"), reg.get("data_fim_prevista")
    if ini and fim and fim <= ini:
        add("data_fim_prevista", "deve ser posterior a data_inicio")
    return e


def _semantica_pevc_evento(reg: dict, nlinha: int, fundos: dict[str, dict]) -> list[Ocorrencia]:
    e, add = _coletor("Eventos", nlinha)
    hoje = config.hoje()

    ident = reg.get("identificador_fundo")
    fundo = fundos.get(ident) if ident else None
    if ident and fundo is None:
        add("identificador_fundo",
            f"'{ident}' não foi cadastrado na aba 'Fundos' nem existe na carteira")

    _exigir_positivos(reg, ("valor_evento",), add)

    tipo, status = reg.get("tipo_evento"), reg.get("status")
    if tipo and status:
        permitidos = config.STATUS_POR_TIPO_EVENTO.get(tipo, set())
        if status not in permitidos:
            add("status", f"status '{status}' inválido para {tipo} "
                          f"(permitidos: {', '.join(sorted(permitidos))})")

    if tipo != "NAV_UPDATE" and reg.get("nav_unidade") is not None:
        add("nav_unidade", "só se aplica a eventos do tipo NAV_UPDATE")
    if tipo != "DISTRIBUICAO" and reg.get("subtipo"):
        add("subtipo", "só se aplica a eventos do tipo DISTRIBUICAO")

    data_ev = reg.get("data_evento")
    if data_ev:
        if fundo and fundo.get("data_inicio") and data_ev < fundo["data_inicio"]:
            add("data_evento",
                f"anterior à data_inicio do fundo ({fundo['data_inicio']:%d/%m/%Y})")
        futuro_permitido = tipo == "CAPITAL_CALL" and status == "PENDENTE"
        if data_ev > hoje and not futuro_permitido:
            add("data_evento",
                "data futura só é aceita em CAPITAL_CALL com status PENDENTE")
    return e


def _semantica_imovel(reg: dict, nlinha: int) -> list[Ocorrencia]:
    e, add = _coletor("Dados", nlinha)

    _exigir_positivos(reg, ("valor_aquisicao", "valor_mercado_atual"), add)
    for campo in ("renda_mensal", "despesas_mensais", "area_m2"):
        v = reg.get(campo)
        if v is not None and v < 0:
            add(campo, "não pode ser negativo")

    _proibir_datas_futuras(reg, ("data_aquisicao", "data_avaliacao"), add)
    aq, av = reg.get("data_aquisicao"), reg.get("data_avaliacao")
    if aq and av and av < aq:
        add("data_avaliacao", "não pode ser anterior a data_aquisicao")
    return e


def validar_arquivo(
    caminho: str | Path,
    chave_template: str,
    sessao: Session | None = None,
    carteira_id: str | None = None,
) -> Preview:
    """
    Lê e valida um arquivo XLSX contra o template indicado.
    Nenhuma escrita em banco acontece aqui — apenas leitura para checagens
    de existência (fundos já cadastrados na carteira).
    """
    caminho = Path(caminho)
    tpl = TEMPLATES[chave_template]
    prev = Preview(template=tpl, nome_arquivo=caminho.name)

    try:
        wb = load_workbook(caminho, data_only=True, read_only=True)
    except Exception as exc:
        prev.erros_estruturais.append(
            Ocorrencia("arquivo", None, None, f"não foi possível abrir o arquivo: {exc}")
        )
        return prev

    try:
        prev.erros_estruturais = _validar_estrutura(wb, tpl)
        if prev.abortado:
            return prev
        if chave_template == "PEVC":
            _processar_pevc(wb, tpl, prev, sessao, carteira_id)
        else:
            _processar_aba_simples(wb, tpl, prev, chave_template)
    finally:
        wb.close()
    return prev


def _linha_anterior(vistos: dict[str, int], chave: str, nlinha: int) -> int | None:
    """Linha em que `chave` já apareceu; registra a atual se for a primeira vez."""
    if chave in vistos:
        return vistos[chave]
    vistos[chave] = nlinha
    return None


def _aceitar(prev: Preview, erros: list[Ocorrencia], reg: dict, nlinha: int,
             destino: list[dict]) -> bool:
    """Linha sem erros vai para `destino`; com erros, para o relatório do preview."""
    if erros:
        prev.erros_semanticos.extend(erros)
        return False
    reg["_linha"] = nlinha
    destino.append(reg)
    return True


def _processar_aba_simples(wb, tpl: Template, prev: Preview, chave: str) -> None:
    aba = tpl.aba("Dados")
    ws = wb[aba.nome]
    validador, campo_unico, pos_processar = _ABA_SIMPLES[chave]

    validos: list[dict] = []
    vistos: dict[str, int] = {}

    for nlinha, bruto in _ler_linhas(ws, aba):
        prev.total_lido += 1
        reg, erros = _tipar_linha(aba, bruto, nlinha)
        erros += validador(reg, nlinha)

        chave_dup = str(reg.get(campo_unico) or "").upper() if campo_unico else ""
        if chave_dup:
            anterior = _linha_anterior(vistos, chave_dup, nlinha)
            if anterior is not None:
                erros.append(Ocorrencia(aba.nome, nlinha, campo_unico,
                                        f"identificador duplicado (já usado na linha {anterior})"))

        _aceitar(prev, erros, reg, nlinha, validos)

    if pos_processar:
        validos = pos_processar(validos, prev)
    prev.validos[aba.nome] = validos


def _consolidar_duplicados(registros: list[dict], prev: Preview) -> list[dict]:
    """
    Mesma posição informada em mais de uma linha: consolida por preço médio
    ponderado em vez de rejeitar, comportamento documentado no template.
    """
    agrupado: dict[tuple[str, str], dict] = {}
    for reg in registros:
        chave = (str(reg["ticker_ou_cnpj"]).upper(), reg["mercado"])
        if chave not in agrupado:
            agrupado[chave] = reg
            continue
        base = agrupado[chave]
        qtd_total = base["quantidade"] + reg["quantidade"]
        base["preco_medio"] = (
            (base["quantidade"] * base["preco_medio"] + reg["quantidade"] * reg["preco_medio"])
            / qtd_total
        ) if qtd_total else 0.0
        base["quantidade"] = qtd_total
        base["valor_financeiro"] = base["valor_financeiro"] + reg["valor_financeiro"]
        base["data_aquisicao"] = min(base["data_aquisicao"], reg["data_aquisicao"])
        prev.avisos.append(Ocorrencia(
            "Dados", reg["_linha"], "ticker_ou_cnpj",
            f"posição consolidada com a linha {base['_linha']} por preço médio ponderado"
        ))
    return list(agrupado.values())


_ABA_SIMPLES = {
    "LIQUIDOS": (semantica_liquidos, None, _consolidar_duplicados),
    "IMOVEIS": (_semantica_imovel, "identificador_imovel", None),
}


def _chave_evento(identificador, data_evento, tipo, valor, status) -> tuple:
    """Identifica um evento de fundo para reconhecer a reimportação do mesmo arquivo."""
    return identificador, data_evento, tipo, round(float(valor), 2), status


def _processar_pevc(wb, tpl: Template, prev: Preview,
                    sessao: Session | None, carteira_id: str | None) -> None:
    aba_f, aba_e = tpl.aba("Fundos"), tpl.aba("Eventos")

    fundos_validos: list[dict] = []
    indice: dict[str, dict] = {}
    vistos: dict[str, int] = {}

    for nlinha, bruto in _ler_linhas(wb[aba_f.nome], aba_f):
        prev.total_lido += 1
        reg, erros = _tipar_linha(aba_f, bruto, nlinha)
        erros += _semantica_pevc_fundo(reg, nlinha)

        ident = reg.get("identificador_fundo")
        if ident:
            anterior = _linha_anterior(vistos, ident, nlinha)
            if anterior is not None:
                erros.append(Ocorrencia(aba_f.nome, nlinha, "identificador_fundo",
                                        f"duplicado (já usado na linha {anterior})"))

        if _aceitar(prev, erros, reg, nlinha, fundos_validos):
            indice[ident] = reg

    pago: dict[str, float] = {}
    comprometido: dict[str, float] = {}
    ja_registrados: set[tuple] = set()
    if sessao is not None and carteira_id:
        for fundo in fundos_da_carteira(sessao, carteira_id):
            m = pe.calcular(fundo, sessao)
            ident = fundo.identificador_fundo
            ja_registrados.update(
                _chave_evento(ident, e.data_evento, e.tipo_evento, e.valor, e.status)
                for e in fundo.eventos)
            pago[ident], comprometido[ident] = m.paid_in, m.paid_in + m.pendente
            cadastrado = {
                "identificador_fundo": ident,
                "commitment": fundo.commitment,
                "data_inicio": fundo.data_inicio,
                "moeda": fundo.ativo.moeda,
            }
            novo = indice.setdefault(ident, cadastrado)
            if novo is not cadastrado:
                try:
                    pe.checar_novo_commitment(novo["commitment"], comprometido[ident], ident)
                except ValueError as exc:
                    prev.erros_semanticos.append(
                        Ocorrencia(aba_f.nome, novo["_linha"], "commitment", str(exc)))
                    fundos_validos.remove(novo)
                    indice[ident] = cadastrado

    eventos_validos: list[dict] = []

    linhas_eventos = _ler_linhas(wb[aba_e.nome], aba_e)
    tipadas: list[tuple[int, dict, list[Ocorrencia]]] = []
    for nlinha, bruto in linhas_eventos:
        prev.total_lido += 1
        reg, erros = _tipar_linha(aba_e, bruto, nlinha)
        erros += _semantica_pevc_evento(reg, nlinha, indice)
        tipadas.append((nlinha, reg, erros))

    ordenadas = sorted(
        tipadas,
        key=lambda t: (t[1].get("data_evento") or date.min, t[0]),
    )

    for nlinha, reg, erros in ordenadas:
        ident = reg.get("identificador_fundo")
        if not erros and _chave_evento(ident, reg["data_evento"], reg["tipo_evento"],
                                       reg["valor_evento"], reg["status"]) in ja_registrados:
            prev.avisos.append(Ocorrencia(
                aba_e.nome, nlinha, None,
                "evento já registrado neste fundo; a linha foi ignorada para não duplicar"))
            continue
        if not erros and ident in indice:
            fundo = indice[ident]
            tipo, status, valor = reg["tipo_evento"], reg["status"], reg["valor_evento"]
            try:
                if tipo == "CAPITAL_CALL" and status != "CANCELADO":
                    pe.checar_commitment(fundo["commitment"], comprometido.get(ident, 0.0),
                                         valor, ident)
                    comprometido[ident] = comprometido.get(ident, 0.0) + valor
                    if status == "PAGO":
                        pago[ident] = pago.get(ident, 0.0) + valor
                elif tipo == "DISTRIBUICAO" and status != "CANCELADO":
                    pe.checar_distribuicao(pago.get(ident, 0.0), valor, ident)
            except ValueError as exc:
                coluna = "valor_evento" if tipo == "CAPITAL_CALL" else "tipo_evento"
                erros.append(Ocorrencia(aba_e.nome, nlinha, coluna, str(exc)))

        _aceitar(prev, erros, reg, nlinha, eventos_validos)

    prev.validos[aba_f.nome] = fundos_validos
    prev.validos[aba_e.nome] = sorted(eventos_validos, key=lambda r: r["_linha"])


def pais_do_mercado(mercado: str | None) -> str:
    return config.PAIS_POR_MERCADO.get(mercado or "", config.PAIS_EXTERIOR)


def pais_da_moeda(moeda: str | None) -> str:
    return config.PAIS_POR_MOEDA.get(moeda or "", config.PAIS_EXTERIOR)


def upsert_ativo(sessao: Session, carteira_id: str | None = None, **campos) -> Ativo:
    """
    Localiza ou cria o ativo. Listados (carteira_id nulo) são cadastro comum,
    identificados por (ticker, mercado). Fundos e imóveis informam a carteira
    dona: o mesmo identificador em outra carteira é OUTRO ativo.
    """
    dono = (Ativo.carteira_id == carteira_id) if carteira_id else Ativo.carteira_id.is_(None)
    ativo = sessao.scalar(
        select(Ativo).where(
            Ativo.ticker_canonico == campos["ticker_canonico"],
            Ativo.mercado == campos.get("mercado"),
            dono,
        )
    )
    if ativo is None:
        ativo = Ativo(carteira_id=carteira_id, **campos)
        sessao.add(ativo)
        sessao.flush()
    else:
        for k, v in campos.items():
            if v not in (None, "") and k != "ticker_canonico":
                setattr(ativo, k, v)
    return ativo


def upsert_posicao(sessao: Session, carteira_id: str, ativo_id: str, **campos) -> Posicao:
    pos = sessao.scalar(
        select(Posicao).where(Posicao.carteira_id == carteira_id, Posicao.ativo_id == ativo_id)
    )
    if pos is None:
        pos = Posicao(carteira_id=carteira_id, ativo_id=ativo_id, **campos)
        sessao.add(pos)
        sessao.flush()
        return pos

    qtd_nova = campos.get("quantidade", 0.0)
    qtd_total = pos.quantidade + qtd_nova
    if qtd_total > 0:
        pos.preco_medio = (
            pos.quantidade * pos.preco_medio + qtd_nova * campos.get("preco_medio", 0.0)
        ) / qtd_total
    pos.quantidade = qtd_total
    pos.valor_financeiro += campos.get("valor_financeiro", 0.0)
    pos.data_inicial = min(pos.data_inicial, campos.get("data_inicial", pos.data_inicial))
    return pos


def upsert_fundo(sessao: Session, carteira_id: str, *, identificador: str, nome: str,
                 gestor: str, sub_classe: str, moeda: str, commitment: float,
                 data_inicio: date, data_fim_prevista: date | None) -> AtivoIliquido:
    """Cadastra (ou atualiza) um fundo PE/VC da carteira e a sua posição."""
    ativo = upsert_ativo(
        sessao,
        carteira_id,
        ticker_canonico=identificador,
        nome=nome,
        classe=sub_classe,
        moeda=moeda,
        mercado=None,
        pais=pais_da_moeda(moeda),
        possui_ticker_listado=False,
    )
    fundo = sessao.scalar(select(AtivoIliquido).where(AtivoIliquido.ativo_id == ativo.id))
    if fundo is None:
        fundo = AtivoIliquido(
            ativo_id=ativo.id,
            identificador_fundo=identificador,
            sub_classe=sub_classe,
            gestor=gestor,
            commitment=commitment,
            dry_powder=commitment,
            data_inicio=data_inicio,
            data_fim_prevista=data_fim_prevista,
        )
        sessao.add(fundo)
        sessao.flush()
    else:
        fundo.commitment = commitment
        fundo.gestor = gestor
        fundo.data_fim_prevista = data_fim_prevista

    upsert_posicao(
        sessao, carteira_id, ativo.id,
        quantidade=1.0, preco_medio=0.0, valor_financeiro=0.0,
        data_inicial=data_inicio,
    )
    return fundo


def importar(
    sessao: Session,
    prev: Preview,
    carteira_id: str,
    usuario_id: str,
) -> ImportacaoLog:
    """
    Persiste as linhas válidas do preview. Toda a operação é uma única
    transação: ou entra tudo, ou nada (atomicidade ACID do SQLite).
    RN-008: o usuário precisa ter acesso à carteira de destino (RN-013).
    """
    if not prev.importavel:
        raise ValueError("Preview sem linhas válidas para importação.")

    carteira = sessao.get(Carteira, carteira_id)
    usuario = sessao.get(Usuario, usuario_id) if usuario_id else None
    if carteira is None or usuario is None \
            or not security.pode_acessar_carteira(sessao, usuario, carteira_id):
        raise ValueError("Carteira inexistente ou sem permissão de acesso (RN-008).")

    chave = prev.template.chave
    _IMPORTADORES[chave](sessao, prev, carteira_id)

    log = ImportacaoLog(
        carteira_id=carteira_id,
        tipo_template=chave,
        nome_arquivo=prev.nome_arquivo,
        linhas_lidas=prev.total_lido,
        linhas_importadas=prev.total_valido,
        linhas_rejeitadas=prev.total_rejeitado,
        detalhe="; ".join(str(o) for o in prev.erros_semanticos[:50]) or None,
    )
    sessao.add(log)
    audit.registrar(
        sessao, usuario_id=usuario_id, acao=audit.IMPORTAR_TEMPLATE,
        entidade="CARTEIRA", entidade_id=carteira_id,
        dados_novos={"template": chave, "arquivo": prev.nome_arquivo,
                     "importadas": prev.total_valido, "rejeitadas": prev.total_rejeitado},
    )
    sessao.commit()
    return log


def _importar_liquidos(sessao: Session, prev: Preview, carteira_id: str) -> None:
    for reg in prev.validos.get("Dados", []):
        ticker = str(reg["ticker_ou_cnpj"]).upper()
        ativo = upsert_ativo(
            sessao,
            ticker_canonico=ticker,
            nome=reg.get("nome") or ticker,
            classe=reg["classe"],
            moeda=reg["moeda"],
            mercado=reg["mercado"],
            setor=reg.get("setor"),
            pais=pais_do_mercado(reg["mercado"]),
            possui_ticker_listado=True,
        )
        pos = upsert_posicao(
            sessao, carteira_id, ativo.id,
            quantidade=reg["quantidade"],
            preco_medio=reg["preco_medio"],
            valor_financeiro=reg["valor_financeiro"],
            data_inicial=reg["data_aquisicao"],
            observacao=reg.get("observacao"),
        )
        sessao.add(Transacao(
            posicao_id=pos.id, tipo="COMPRA", data=reg["data_aquisicao"],
            quantidade=reg["quantidade"], preco=reg["preco_medio"], taxas=0.0,
        ))
        registrar_preco(sessao, ativo.id, reg["data_aquisicao"],
                         reg["preco_medio"], "TEMPLATE")


def _importar_pevc(sessao: Session, prev: Preview, carteira_id: str) -> None:
    indice: dict[str, AtivoIliquido] = {}

    for reg in prev.validos.get("Fundos", []):
        ident = reg["identificador_fundo"]
        indice[ident] = upsert_fundo(
            sessao, carteira_id,
            identificador=ident,
            nome=reg["nome_fundo"],
            gestor=reg["gestor"],
            sub_classe=reg["sub_classe"],
            moeda=reg["moeda"],
            commitment=reg["commitment"],
            data_inicio=reg["data_inicio"],
            data_fim_prevista=reg.get("data_fim_prevista"),
        )

    for fundo in fundos_da_carteira(sessao, carteira_id):
        indice.setdefault(fundo.identificador_fundo, fundo)

    for reg in prev.validos.get("Eventos", []):
        fundo = indice.get(reg["identificador_fundo"])
        if fundo is None:
            continue
        sessao.add(EventoPE(
            fundo_id=fundo.id,
            data_evento=reg["data_evento"],
            tipo_evento=reg["tipo_evento"],
            valor=reg["valor_evento"],
            status=reg["status"],
            nav_unidade=reg.get("nav_unidade"),
            subtipo=reg.get("subtipo") if reg["tipo_evento"] == "DISTRIBUICAO" else None,
            observacao=reg.get("observacao"),
        ))
    sessao.flush()

    for fundo in indice.values():
        pe.recalcular_metricas(sessao, fundo)


def _importar_imoveis(sessao: Session, prev: Preview, carteira_id: str) -> None:
    for reg in prev.validos.get("Dados", []):
        ident = reg["identificador_imovel"]
        ativo = upsert_ativo(
            sessao,
            carteira_id,
            ticker_canonico=ident,
            nome=reg["nome_descritivo"],
            classe=config.CLASSE_IMOVEL,
            moeda=reg["moeda"],
            mercado=None,
            pais=pais_da_moeda(reg["moeda"]),
            possui_ticker_listado=False,
        )
        imovel = sessao.scalar(select(AtivoImovel).where(AtivoImovel.ativo_id == ativo.id))
        campos = dict(
            identificador_imovel=ident,
            tipo_imovel=reg["tipo_imovel"],
            endereco=reg.get("endereco"),
            valor_aquisicao=reg["valor_aquisicao"],
            valor_de_mercado=reg["valor_mercado_atual"],
            aluguel_mensal=reg.get("renda_mensal") or 0.0,
            despesas_mensais=reg.get("despesas_mensais") or 0.0,
            area_m2=reg.get("area_m2"),
            data_avaliacao=reg["data_avaliacao"],
        )
        if imovel is None:
            sessao.add(AtivoImovel(ativo_id=ativo.id, **campos))
        else:
            for k, v in campos.items():
                setattr(imovel, k, v)

        pos = sessao.scalar(
            select(Posicao).where(Posicao.carteira_id == carteira_id,
                                  Posicao.ativo_id == ativo.id)
        )
        if pos is None:
            pos = Posicao(
                carteira_id=carteira_id, ativo_id=ativo.id, quantidade=1.0,
                preco_medio=reg["valor_aquisicao"],
                valor_financeiro=reg["valor_mercado_atual"],
                data_inicial=reg["data_aquisicao"],
            )
            sessao.add(pos)
            sessao.flush()
            sessao.add(Transacao(posicao_id=pos.id, tipo="COMPRA",
                                 data=reg["data_aquisicao"], quantidade=1.0,
                                 preco=reg["valor_aquisicao"], taxas=0.0))
        else:
            pos.valor_financeiro = reg["valor_mercado_atual"]

        registrar_preco(sessao, ativo.id, reg["data_aquisicao"],
                         reg["valor_aquisicao"], "TEMPLATE")
        registrar_preco(sessao, ativo.id, reg["data_avaliacao"],
                         reg["valor_mercado_atual"], "AVALIACAO")


_IMPORTADORES = {
    "LIQUIDOS": _importar_liquidos,
    "PEVC": _importar_pevc,
    "IMOVEIS": _importar_imoveis,
}
