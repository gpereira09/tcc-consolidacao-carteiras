"""
Operações manuais sobre a carteira — alternativa ao template (seção 5.2).

  * UC-05 / RF-007 — cadastrar ativo líquido pelo formulário;
  * UC-06 / RF-009 — registrar compra, venda ou provento;
  * UC-08          — cadastrar fundo ilíquido (PE, VC, Real Estate);
  * UC-09/10/11    — registrar eventos de fundo (RF-010, RF-011, RF-012),
                     inclusive confirmar o pagamento de uma chamada pendente
                     (UC-09 FA-01);
  * reavaliar imóvel e criar carteiras adicionais (MER: CLIENTE 1:N CARTEIRA).

Toda operação verifica, antes de qualquer escrita, se o usuário pode acessar
a carteira afetada (RN-008/RN-013). Fundos e imóveis pertencem a uma única
carteira (Ativo.carteira_id), de modo que o acesso é sempre conferido pela
carteira dona do ativo, e não por um identificador informado pela tela.

As regras são as mesmas da ingestão por template: a validação semântica de
líquidos é reaproveitada de `src.ingest`, e a persistência usa as mesmas
funções de upsert. Assim, um ativo cadastrado à mão e o mesmo ativo vindo de
planilha são consolidados na mesma posição, nunca duplicados.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from src import audit, security
from src.calc import pe
from src.calc.consolidacao import fundos_da_carteira
from src.ingest import (
    pais_do_mercado,
    semantica_liquidos,
    upsert_ativo,
    upsert_fundo,
    upsert_posicao,
)
from src.market import precos
from src.models import (
    Ativo,
    AtivoIliquido,
    AtivoImovel,
    Carteira,
    Cliente,
    EventoPE,
    Posicao,
    Transacao,
    Usuario,
)


class ErroOperacao(ValueError):
    pass


def _exigir_dominio(valor, permitidos, mensagem: str) -> None:
    """Valor fora do domínio de config → ErroOperacao("<mensagem>: <valor>.")."""
    if valor not in permitidos:
        raise ErroOperacao(f"{mensagem}: {valor}.")


def _exigir_nao_futura(dia: date, mensagem: str) -> None:
    if dia > config.hoje():
        raise ErroOperacao(mensagem)


def _autorizar(sessao: Session, usuario: Usuario | None, carteira_id: str | None) -> None:
    """RN-008 e RN-013: só se opera em carteira visível ao perfil."""
    if usuario is None or carteira_id is None \
            or not security.pode_acessar_carteira(sessao, usuario, carteira_id):
        raise ErroOperacao("Você não tem acesso a esta carteira (RN-008).")


def _carteira_do_fundo(sessao: Session, fundo: AtivoIliquido) -> str | None:
    ativo = sessao.get(Ativo, fundo.ativo_id)
    return ativo.carteira_id if ativo else None


def cadastrar_ativo_liquido(
    sessao: Session, usuario: Usuario, carteira_id: str, *,
    ticker: str, classe: str, mercado: str, moeda: str,
    quantidade: float, preco_medio: float, data_aquisicao: date,
    nome: str | None = None, setor: str | None = None, observacao: str | None = None,
    buscar_preco: bool = True,
) -> Posicao:
    _autorizar(sessao, usuario, carteira_id)
    ticker = (ticker or "").strip().upper()
    if not ticker:
        raise ErroOperacao("Informe o ticker ou o CNPJ do ativo.")
    _exigir_dominio(classe, config.CLASSES_LIQUIDAS, "Classe inválida")
    _exigir_dominio(mercado, config.MERCADOS, "Mercado inválido")
    _exigir_dominio(moeda, config.MOEDAS, "Moeda inválida")

    reg = {
        "ticker_ou_cnpj": ticker, "classe": classe, "mercado": mercado, "moeda": moeda,
        "quantidade": quantidade, "preco_medio": preco_medio,
        "valor_financeiro": quantidade * preco_medio, "data_aquisicao": data_aquisicao,
    }
    problemas = semantica_liquidos(reg, 0)
    if quantidade is None or quantidade <= 0 or preco_medio is None or preco_medio <= 0:
        raise ErroOperacao("Quantidade e preço médio devem ser maiores que zero.")
    if problemas:
        raise ErroOperacao("; ".join(f"{o.coluna}: {o.mensagem}" for o in problemas))

    ativo = upsert_ativo(
        sessao, ticker_canonico=ticker, nome=(nome or "").strip() or ticker,
        classe=classe, moeda=moeda, mercado=mercado, setor=(setor or "").strip() or None,
        pais=pais_do_mercado(mercado), possui_ticker_listado=True,
    )
    pos = upsert_posicao(
        sessao, carteira_id, ativo.id, quantidade=quantidade, preco_medio=preco_medio,
        valor_financeiro=quantidade * preco_medio, data_inicial=data_aquisicao,
        observacao=observacao or None,
    )
    sessao.add(Transacao(posicao_id=pos.id, tipo="COMPRA", data=data_aquisicao,
                         quantidade=quantidade, preco=preco_medio, taxas=0.0))
    precos.registrar_preco(sessao, ativo.id, data_aquisicao, preco_medio, "MANUAL")
    audit.registrar(sessao, usuario_id=usuario.id, acao=audit.CADASTRAR_ATIVO,
                    entidade="POSICAO", entidade_id=pos.id,
                    dados_novos={"ticker": ticker, "quantidade": quantidade,
                                 "preco_medio": preco_medio, "data": str(data_aquisicao)})
    sessao.commit()

    if buscar_preco:
        try:
            precos.atualizar_ativo(sessao, ativo)
            sessao.commit()
        except Exception:
            sessao.rollback()
    return pos


def posicoes_liquidas(sessao: Session, carteira_id: str) -> list[tuple[Posicao, Ativo]]:
    """Posições que aceitam transações: tudo o que não é fundo ilíquido nem imóvel."""
    fundos = select(AtivoIliquido.ativo_id)
    linhas = sessao.execute(
        select(Posicao, Ativo).join(Ativo, Ativo.id == Posicao.ativo_id)
        .where(Posicao.carteira_id == carteira_id, Ativo.classe != config.CLASSE_IMOVEL,
               Ativo.id.not_in(fundos))
        .order_by(Ativo.ticker_canonico)
    ).all()
    return [(p, a) for p, a in linhas]


def transacoes(sessao: Session, posicao_id: str) -> list[Transacao]:
    return list(sessao.scalars(
        select(Transacao).where(Transacao.posicao_id == posicao_id)
        .order_by(Transacao.data.desc())
    ).all())


def _exigir_saldo_na_data(sessao: Session, pos: Posicao, data_venda: date,
                          quantidade: float) -> None:
    """
    A venda não pode exceder a posição que existia na data dela (UC-06).

    Refaz o histórico de compras e vendas com a nova venda incluída e recusa
    se a quantidade ficar negativa em algum dia. Sem isso, uma venda datada
    antes de uma compra posterior seria aceita pelo saldo de hoje e deixaria
    a posição negativa no passado, distorcendo a rentabilidade.
    """
    movimentos = [(t.data, t.quantidade if t.tipo == "COMPRA" else -t.quantidade)
                  for t in transacoes(sessao, pos.id) if t.tipo in ("COMPRA", "VENDA")]
    if not movimentos:
        return
    movimentos.append((data_venda, -quantidade))
    saldo = 0.0
    for dia, variacao in sorted(movimentos, key=lambda m: (m[0], -m[1])):
        saldo += variacao
        if saldo < -1e-9:
            raise ErroOperacao(
                f"Venda de {quantidade:g} excede a quantidade em carteira em {dia:%d/%m/%Y}.")


def registrar_transacao(
    sessao: Session, usuario: Usuario, posicao_id: str, *,
    tipo: str, data_transacao: date, quantidade: float, preco: float, taxas: float = 0.0,
) -> Transacao:
    pos = sessao.get(Posicao, posicao_id)
    if pos is None:
        raise ErroOperacao("Posição não encontrada.")
    _autorizar(sessao, usuario, pos.carteira_id)
    ativo = sessao.get(Ativo, pos.ativo_id)
    if sessao.scalar(select(AtivoIliquido).where(AtivoIliquido.ativo_id == ativo.id)) \
            or ativo.classe == config.CLASSE_IMOVEL:
        raise ErroOperacao("Transações se aplicam a ativos líquidos. Para fundos, "
                           "registre eventos; para imóveis, reavaliações.")
    _exigir_dominio(tipo, config.TIPOS_TRANSACAO, "Tipo inválido")
    _exigir_nao_futura(data_transacao, "A data da transação não pode ser futura.")
    if data_transacao < pos.data_inicial and tipo != "COMPRA":
        raise ErroOperacao(
            f"A data não pode ser anterior à aquisição inicial ({pos.data_inicial:%d/%m/%Y}).")
    if taxas < 0:
        raise ErroOperacao("As taxas não podem ser negativas.")

    anterior = {"quantidade": pos.quantidade, "preco_medio": pos.preco_medio}
    if tipo == "PROVENTO":
        if preco <= 0:
            raise ErroOperacao("Informe o valor total do provento.")
        quantidade = 0.0
    else:
        if quantidade <= 0 or preco <= 0:
            raise ErroOperacao("Quantidade e preço devem ser maiores que zero.")

    if tipo == "COMPRA":
        total = pos.quantidade + quantidade
        pos.preco_medio = (pos.quantidade * pos.preco_medio + quantidade * preco + taxas) / total
        pos.quantidade = total
        pos.data_inicial = min(pos.data_inicial, data_transacao)
    elif tipo == "VENDA":
        if quantidade > pos.quantidade + 1e-9:
            raise ErroOperacao(
                f"Venda de {quantidade:g} excede a quantidade em carteira ({pos.quantidade:g}).")
        _exigir_saldo_na_data(sessao, pos, data_transacao, quantidade)
        pos.quantidade = round(pos.quantidade - quantidade, 10)
    pos.valor_financeiro = pos.quantidade * pos.preco_medio

    t = Transacao(posicao_id=pos.id, tipo=tipo, data=data_transacao,
                  quantidade=quantidade, preco=preco, taxas=taxas)
    sessao.add(t)
    if tipo in ("COMPRA", "VENDA"):
        precos.registrar_preco(sessao, ativo.id, data_transacao, preco, "MANUAL")
    sessao.flush()
    audit.registrar(sessao, usuario_id=usuario.id, acao=audit.REGISTRAR_TRANSACAO,
                    entidade="TRANSACAO", entidade_id=t.id, dados_anteriores=anterior,
                    dados_novos={"tipo": tipo, "data": str(data_transacao),
                                 "quantidade": quantidade, "preco": preco, "taxas": taxas})
    sessao.commit()
    return t


def cadastrar_fundo(
    sessao: Session, usuario: Usuario, carteira_id: str, *,
    identificador: str, nome: str, gestor: str, sub_classe: str, moeda: str,
    commitment: float, data_inicio: date, data_fim_prevista: date | None = None,
) -> AtivoIliquido:
    _autorizar(sessao, usuario, carteira_id)
    identificador = (identificador or "").strip().upper()
    if not identificador or not (nome or "").strip() or not (gestor or "").strip():
        raise ErroOperacao("Identificador, nome e gestor são obrigatórios.")
    _exigir_dominio(sub_classe, config.SUBCLASSES_ILIQUIDAS, "Subclasse inválida")
    _exigir_dominio(moeda, config.MOEDAS, "Moeda inválida")
    if commitment is None or commitment <= 0:
        raise ErroOperacao("O commitment deve ser maior que zero.")
    _exigir_nao_futura(data_inicio, "A data de início não pode ser futura.")
    if data_fim_prevista and data_fim_prevista <= data_inicio:
        raise ErroOperacao("A data de encerramento deve ser posterior ao início.")

    for existente in fundos_da_carteira(sessao, carteira_id):
        if existente.identificador_fundo == identificador:
            m = pe.calcular(existente, sessao)
            pe.checar_novo_commitment(commitment, m.paid_in + m.pendente, identificador)

    fundo = upsert_fundo(
        sessao, carteira_id, identificador=identificador, nome=nome.strip(),
        gestor=gestor.strip(), sub_classe=sub_classe, moeda=moeda, commitment=commitment,
        data_inicio=data_inicio, data_fim_prevista=data_fim_prevista,
    )
    pe.recalcular_metricas(sessao, fundo)
    audit.registrar(sessao, usuario_id=usuario.id, acao=audit.CADASTRAR_FUNDO,
                    entidade="ATIVO_ILIQUIDO", entidade_id=fundo.id,
                    dados_novos={"identificador": identificador, "commitment": commitment,
                                 "sub_classe": sub_classe})
    sessao.commit()
    return fundo


def registrar_evento_pe(
    sessao: Session, usuario: Usuario, fundo: AtivoIliquido, *,
    tipo: str, data_evento: date, valor: float, status: str,
    nav_unidade: float | None = None, subtipo: str | None = None,
    observacao: str | None = None,
) -> EventoPE:
    _autorizar(sessao, usuario, _carteira_do_fundo(sessao, fundo))
    _exigir_dominio(tipo, config.TIPOS_EVENTO_PE, "Tipo de evento inválido")
    if valor is None or valor <= 0:
        raise ErroOperacao("Informe um valor maior que zero.")
    if data_evento < fundo.data_inicio:
        raise ErroOperacao(f"O evento não pode ser anterior ao início do fundo "
                           f"({fundo.data_inicio:%d/%m/%Y}).")

    if tipo == "CAPITAL_CALL":
        if status not in ("PAGO", "PENDENTE"):
            raise ErroOperacao("Capital call deve ter status PAGO ou PENDENTE.")
        if status == "PAGO":
            _exigir_nao_futura(data_evento, "Chamada paga não pode ter data futura; use PENDENTE.")
        _validar(pe.validar_capital_call, fundo, valor, sessao)
        subtipo = None
    elif tipo == "DISTRIBUICAO":
        _exigir_nao_futura(data_evento,
                           "Distribuições registram fato consumado: data não pode ser futura.")
        if subtipo not in config.TIPOS_DISTRIBUICAO:
            raise ErroOperacao("Classifique a distribuição: retorno de capital, "
                               "ganho de capital ou rendimento (RF-012).")
        _validar(pe.validar_distribuicao, fundo, valor, sessao)
        status = "RECEBIDO"
    else:
        _exigir_nao_futura(data_evento, "A data de referência do NAV não pode ser futura.")
        status, subtipo = "RECEBIDO", None

    evento = EventoPE(
        fundo_id=fundo.id, data_evento=data_evento, tipo_evento=tipo, valor=valor,
        status=status, nav_unidade=nav_unidade if tipo == "NAV_UPDATE" else None,
        subtipo=subtipo, observacao=(observacao or "").strip() or None,
    )
    sessao.add(evento)
    sessao.flush()
    pe.recalcular_metricas(sessao, fundo)
    audit.registrar(
        sessao, usuario_id=usuario.id,
        acao=audit.ATUALIZAR_NAV if tipo == "NAV_UPDATE" else audit.REGISTRAR_EVENTO_PE,
        entidade="EVENTO_PE", entidade_id=evento.id,
        dados_novos={"fundo": fundo.identificador_fundo, "tipo": tipo, "valor": valor,
                     "status": status, "subtipo": subtipo, "data": str(data_evento)},
    )
    sessao.commit()
    return evento


def _validar(regra, *args) -> None:
    """Traduz o ValueError das regras de negócio de `pe` em ErroOperacao."""
    try:
        regra(*args)
    except ValueError as exc:
        raise ErroOperacao(str(exc)) from exc


def confirmar_capital_call(
    sessao: Session, usuario: Usuario, evento_id: str, data_pagamento: date,
) -> EventoPE:
    """
    UC-09 FA-01 — a chamada registrada como PENDENTE foi paga. O status passa
    a PAGO na data do pagamento. A RN-004 não precisa ser reavaliada: a
    chamada pendente já estava contada no limite do commitment.
    """
    evento = sessao.get(EventoPE, evento_id)
    if evento is None or evento.tipo_evento != "CAPITAL_CALL" or evento.status != "PENDENTE":
        raise ErroOperacao("Chamada de capital pendente não encontrada.")
    fundo = sessao.get(AtivoIliquido, evento.fundo_id)
    _autorizar(sessao, usuario, _carteira_do_fundo(sessao, fundo))
    _exigir_nao_futura(data_pagamento, "A data do pagamento não pode ser futura.")
    if data_pagamento < fundo.data_inicio:
        raise ErroOperacao("O pagamento não pode ser anterior ao início do fundo.")

    anterior = {"status": evento.status, "data": str(evento.data_evento)}
    evento.status, evento.data_evento = "PAGO", data_pagamento
    sessao.flush()
    pe.recalcular_metricas(sessao, fundo)
    audit.registrar(sessao, usuario_id=usuario.id, acao=audit.REGISTRAR_EVENTO_PE,
                    entidade="EVENTO_PE", entidade_id=evento.id, dados_anteriores=anterior,
                    dados_novos={"status": "PAGO", "data": str(data_pagamento)})
    sessao.commit()
    return evento


def reavaliar_imovel(
    sessao: Session, usuario: Usuario, carteira_id: str, imovel_id: str, *,
    valor: float, data_avaliacao: date,
) -> AtivoImovel:
    """Registra nova avaliação de mercado e remarca a posição na carteira."""
    _autorizar(sessao, usuario, carteira_id)
    imovel = sessao.get(AtivoImovel, imovel_id)
    ativo = sessao.get(Ativo, imovel.ativo_id) if imovel else None
    if ativo is None or ativo.carteira_id != carteira_id:
        raise ErroOperacao("Imóvel não encontrado nesta carteira.")
    if valor <= 0:
        raise ErroOperacao("Informe um valor maior que zero.")
    _exigir_nao_futura(data_avaliacao, "A data da avaliação não pode ser futura.")

    anterior = {"valor_de_mercado": imovel.valor_de_mercado,
                "data_avaliacao": str(imovel.data_avaliacao)}
    imovel.valor_de_mercado = valor
    imovel.data_avaliacao = data_avaliacao
    precos.registrar_preco(sessao, imovel.ativo_id, data_avaliacao, valor, "AVALIACAO")
    posicoes = sessao.scalars(
        select(Posicao).where(Posicao.ativo_id == imovel.ativo_id,
                              Posicao.carteira_id == carteira_id)
    ).all()
    for pos in posicoes:
        pos.valor_financeiro = valor
    audit.registrar(sessao, usuario_id=usuario.id, acao=audit.REAVALIAR_IMOVEL,
                    entidade="ATIVO_IMOVEL", entidade_id=imovel.id,
                    dados_anteriores=anterior,
                    dados_novos={"valor_de_mercado": valor, "data_avaliacao": str(data_avaliacao)})
    sessao.commit()
    return imovel


def criar_carteira(sessao: Session, usuario: Usuario, cliente_id: str, nome: str) -> Carteira:
    cliente = sessao.get(Cliente, cliente_id)
    if cliente is None:
        raise ErroOperacao("Cliente não encontrado.")
    if not security.pode_acessar_cliente(usuario, cliente):
        raise ErroOperacao("Você não pode criar carteiras para este cliente (RN-013).")
    nome = (nome or "").strip()
    if not nome:
        raise ErroOperacao("Informe o nome da carteira.")
    existentes = {c.nome.lower() for c in sessao.scalars(
        select(Carteira).where(Carteira.cliente_id == cliente_id))}
    if nome.lower() in existentes:
        raise ErroOperacao("Já existe uma carteira com este nome para o cliente.")
    carteira = Carteira(cliente_id=cliente_id, nome=nome)
    sessao.add(carteira)
    sessao.flush()
    audit.registrar(sessao, usuario_id=usuario.id, acao=audit.CRIAR_CARTEIRA,
                    entidade="CARTEIRA", entidade_id=carteira.id, dados_novos={"nome": nome})
    sessao.commit()
    return carteira
