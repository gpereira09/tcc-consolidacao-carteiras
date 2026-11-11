"""Funções de apoio compartilhadas pelos testes."""
from __future__ import annotations

import os
import sqlite3
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from src import ingest, operacoes, security
from src.db import SessionLocal
from src.market.precos import registrar_preco
from src.models import Ativo, AtivoIliquido, Carteira, Cliente, EventoPE, Posicao, Usuario

SENHA = "Senha123"


def usuario_de(sessao: Session, carteira: Carteira) -> Usuario:
    """Usuário titular da carteira informada."""
    cliente = sessao.get(Cliente, carteira.cliente_id)
    return sessao.get(Usuario, cliente.usuario_id)


def criar_usuario(sessao: Session, email: str, nome: str = "Usuário Teste", **extra) -> Usuario:
    """Conta com a senha padrão dos testes e consentimento LGPD dado."""
    return security.criar_conta(sessao, email=email, nome=nome, senha=SENHA,
                                consentimento_lgpd=True, **extra)


def carteira_do_email(s, email: str) -> Carteira:
    return s.scalar(select(Carteira).join(Cliente).join(Usuario, Usuario.id == Cliente.usuario_id)
                    .where(Usuario.email == email))


def importar_arquivo(sessao, carteira, caminho, chave, usuario_id=None):
    """Valida e importa uma planilha na carteira; devolve o log da importação."""
    prev = ingest.validar_arquivo(caminho, chave, sessao, carteira.id)
    return ingest.importar(sessao, prev, carteira.id,
                           usuario_id or usuario_de(sessao, carteira).id)


def importar_template(sessao, carteira, chave, pasta, usuario_id=None):
    """Gera o template `chave` intacto e o importa na carteira."""
    from src.templates_xlsx import gerar_template

    caminho = gerar_template(chave, pasta / f"{chave}_{carteira.id[:8]}.xlsx")
    return importar_arquivo(sessao, carteira, caminho, chave, usuario_id)


def cadastrar_acao(sessao, carteira, ticker="TST3", mercado="B3", moeda="BRL",
                   quantidade=100.0, preco=10.0, dia=date(2024, 1, 2)) -> Posicao:
    """Ação lançada manualmente, sem buscar preço na internet."""
    return operacoes.cadastrar_ativo_liquido(
        sessao, usuario_de(sessao, carteira), carteira.id, ticker=ticker, classe="ACAO",
        mercado=mercado, moeda=moeda, quantidade=quantidade, preco_medio=preco,
        data_aquisicao=dia, buscar_preco=False)


def preco(sessao, pos, dia, valor) -> None:
    registrar_preco(sessao, pos.ativo_id, dia, valor, "TESTE")
    sessao.commit()


def criar_fundo(sessao, carteira, commitment=1000.0, moeda="BRL", ident="FUND-T",
                classe="PRIVATE_EQUITY") -> AtivoIliquido:
    """Fundo ilíquido da carteira, sem eventos, iniciado em 01/01/2022."""
    ativo = Ativo(ticker_canonico=ident, nome=f"Fundo {ident}", classe=classe,
                  moeda=moeda, possui_ticker_listado=False, carteira_id=carteira.id)
    sessao.add(ativo)
    sessao.flush()
    fundo = AtivoIliquido(ativo_id=ativo.id, identificador_fundo=ident, sub_classe=classe,
                          gestor="G", commitment=commitment, dry_powder=commitment,
                          data_inicio=date(2022, 1, 1))
    sessao.add(fundo)
    sessao.add(Posicao(carteira_id=carteira.id, ativo_id=ativo.id, quantidade=1.0,
                       data_inicial=date(2022, 1, 1)))
    sessao.commit()
    return fundo


def evento(sessao, fundo, dia, tipo, valor, status="PAGO", subtipo=None) -> None:
    sessao.add(EventoPE(fundo_id=fundo.id, data_evento=dia, tipo_evento=tipo, valor=valor,
                        status=status, subtipo=subtipo))
    sessao.commit()


_MODELOS: dict[str, sqlite3.Connection] = {}


def base_modelo(sessao, chave: str, montar) -> None:
    """Deixa no banco de testes a base `chave`, montando-a só na primeira vez."""
    from src import db

    sessao.close()
    db._engine.dispose()
    destino = sqlite3.connect(os.environ["CARTEIRA_DB"])
    try:
        if chave in _MODELOS:
            _MODELOS[chave].backup(destino)
            return
        destino.close()
        with SessionLocal() as s:
            montar(s)
        db._engine.dispose()
        destino = sqlite3.connect(os.environ["CARTEIRA_DB"])
        copia = sqlite3.connect(":memory:")
        destino.backup(copia)
        _MODELOS[chave] = copia
    finally:
        destino.close()


def popular(s, usuario, carteira, pasta, com_precos: bool = False) -> None:
    """Câmbio, benchmarks e os três templates importados (e, opcionalmente, preços)."""
    from src.market import seed_sintetico

    seed_sintetico.gerar_cambio(s, date(2019, 1, 1), date.today())
    seed_sintetico.gerar_benchmarks(s, date(2019, 1, 1), date.today())
    for chave in ("LIQUIDOS", "PEVC", "IMOVEIS"):
        importar_template(s, carteira, chave, pasta, usuario.id)
    if com_precos:
        for pos in s.scalars(select(Posicao).join(Ativo, Ativo.id == Posicao.ativo_id).where(
                Posicao.carteira_id == carteira.id, Ativo.possui_ticker_listado.is_(True))):
            seed_sintetico.gerar_precos_ativo(s, s.get(Ativo, pos.ativo_id), pos.preco_medio,
                                              pos.data_inicial, date.today())
    s.commit()
