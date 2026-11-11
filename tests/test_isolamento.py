"""
Isolamento entre clientes, autorização e LGPD (correções C2, C3 e A5).

Cenário central: dois clientes importam o MESMO template de PE/VC e de
imóveis, portanto com os mesmos identificadores (FUND-VC-001, IMV-...).
Antes da correção, os dois dividiam um único registro de fundo/imóvel: o
commitment de um sobrescrevia o do outro e a exclusão LGPD de um apagava a
posição do outro.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from src import db, lgpd, operacoes
from src.calc import consolidacao
from src.models import (
    Ativo,
    AtivoIliquido,
    AtivoImovel,
    AuditLog,
    Carteira,
    Cliente,
    EventoPE,
    Posicao,
    PrecoHistorico,
)
from tests.apoio import carteira_do_email, criar_usuario, importar_template, usuario_de


def _cliente(sessao, email, nome):
    u = criar_usuario(sessao, email, nome, cpf="123.456.789-09")
    return u, carteira_do_email(sessao, email)


def _contar(sessao, modelo, *filtros):
    return sessao.scalar(select(func.count()).select_from(modelo).where(*filtros))


@pytest.fixture()
def dois_clientes(sessao, tmp_path):
    """Ana e Bruno com os mesmos fundos e imóveis (mesmos identificadores)."""
    ana, cart_a = _cliente(sessao, "ana@exemplo.com", "Ana Teste")
    bruno, cart_b = _cliente(sessao, "bruno@exemplo.com", "Bruno Teste")
    for usuario, carteira in ((ana, cart_a), (bruno, cart_b)):
        for chave in ("PEVC", "IMOVEIS"):
            importar_template(sessao, carteira, chave, tmp_path, usuario.id)
    return ana, cart_a, bruno, cart_b


def _fundo(sessao, carteira, ident="FUND-VC-001"):
    return next(f for f in consolidacao.fundos_da_carteira(sessao, carteira.id)
                if f.identificador_fundo == ident)


class TestIsolamentoPorCliente:
    def test_mesmo_identificador_gera_registros_distintos(self, sessao, dois_clientes):
        _, cart_a, _, cart_b = dois_clientes
        fa, fb = _fundo(sessao, cart_a), _fundo(sessao, cart_b)
        assert fa.id != fb.id and fa.ativo_id != fb.ativo_id
        assert sessao.get(Ativo, fa.ativo_id).carteira_id == cart_a.id
        assert sessao.get(Ativo, fb.ativo_id).carteira_id == cart_b.id
        assert len(fa.eventos) == len(fb.eventos) == 7

    def test_commitment_de_um_nao_sobrescreve_o_outro(self, sessao, dois_clientes):
        ana, cart_a, _, cart_b = dois_clientes
        operacoes.cadastrar_fundo(
            sessao, ana, cart_a.id, identificador="FUND-VC-001", nome="Orvalheira Ventures II",
            gestor="Orvalheira Gestão de Recursos", sub_classe="VENTURE_CAPITAL", moeda="BRL",
            commitment=5_000_000, data_inicio=date(2022, 3, 10))
        assert _fundo(sessao, cart_a).commitment == 5_000_000
        assert _fundo(sessao, cart_b).commitment == 2_000_000

    def test_evento_de_um_cliente_nao_altera_metricas_do_outro(self, sessao, dois_clientes):
        ana, cart_a, _, cart_b = dois_clientes
        antes = consolidacao.metricas_pe(sessao, cart_b.id)
        operacoes.registrar_evento_pe(sessao, ana, _fundo(sessao, cart_a), tipo="NAV_UPDATE",
                                      data_evento=date(2025, 12, 31), valor=9_999_999,
                                      status="RECEBIDO")
        depois = consolidacao.metricas_pe(sessao, cart_b.id)
        assert [m.nav for m in antes] == [m.nav for m in depois]

    def test_exclusao_lgpd_de_um_preserva_o_outro(self, sessao, dois_clientes):
        ana, cart_a, _, cart_b = dois_clientes
        patrimonio_b = consolidacao.snapshot(sessao, cart_b.id).patrimonio
        eventos_b = len(_fundo(sessao, cart_b).eventos)

        lgpd.excluir(sessao, ana.id)

        assert sessao.get(Carteira, cart_b.id) is not None
        assert consolidacao.snapshot(sessao, cart_b.id).patrimonio == pytest.approx(patrimonio_b)
        assert len(_fundo(sessao, cart_b).eventos) == eventos_b
        assert _contar(sessao, Ativo, Ativo.carteira_id == cart_a.id) == 0
        assert _contar(sessao, Posicao, Posicao.carteira_id == cart_a.id) == 0
        assert _contar(sessao, AtivoIliquido) == 3
        assert _contar(sessao, AtivoImovel) == 4

    def test_ativo_listado_continua_compartilhado(self, sessao, carteira, tmp_path):
        """Ação é cadastro comum: dois clientes com PETR4 usam o mesmo ativo."""
        dono = usuario_de(sessao, carteira)
        outro, cart_outro = _cliente(sessao, "carla@exemplo.com", "Carla")
        importar_template(sessao, carteira, "LIQUIDOS", tmp_path, dono.id)
        importar_template(sessao, cart_outro, "LIQUIDOS", tmp_path, outro.id)
        petr = sessao.scalars(select(Ativo).where(Ativo.ticker_canonico == "PETR4")).all()
        assert len(petr) == 1 and petr[0].carteira_id is None
        lgpd.excluir(sessao, outro.id)
        assert sessao.get(Ativo, petr[0].id) is not None


class TestMigracaoDeBaseAntiga:
    def test_registro_compartilhado_e_copiado_sem_perda(self, sessao, carteira):
        """
        Base antiga: um único fundo (sem dono) com posição em duas carteiras.
        A migração dá o original à primeira e uma cópia completa à segunda.
        """
        _, outra = _cliente(sessao, "legado@exemplo.com", "Legado")
        ativo = Ativo(ticker_canonico="FUND-OLD", nome="Fundo Antigo", classe="PRIVATE_EQUITY",
                      moeda="BRL", possui_ticker_listado=False)
        sessao.add(ativo)
        sessao.flush()
        fundo = AtivoIliquido(ativo_id=ativo.id, identificador_fundo="FUND-OLD",
                              sub_classe="PRIVATE_EQUITY", gestor="G", commitment=1000,
                              data_inicio=date(2022, 1, 1))
        sessao.add(fundo)
        sessao.flush()
        sessao.add_all([
            EventoPE(fundo_id=fundo.id, data_evento=date(2022, 2, 1), tipo_evento="CAPITAL_CALL",
                     valor=400, status="PAGO"),
            EventoPE(fundo_id=fundo.id, data_evento=date(2022, 12, 31), tipo_evento="NAV_UPDATE",
                     valor=500, status="RECEBIDO"),
            PrecoHistorico(ativo_id=ativo.id, data=date(2022, 12, 31), preco_fechamento=500),
        ])
        for c in (carteira, outra):
            sessao.add(Posicao(carteira_id=c.id, ativo_id=ativo.id, quantidade=1,
                               data_inicial=date(2022, 1, 1)))
        sessao.commit()

        assert db._isolar_ativos_iliquidos(sessao) == 1
        sessao.commit()

        fundos = sessao.scalars(select(AtivoIliquido)
                                .where(AtivoIliquido.identificador_fundo == "FUND-OLD")).all()
        assert len(fundos) == 2
        donos = {sessao.get(Ativo, f.ativo_id).carteira_id for f in fundos}
        assert donos == {carteira.id, outra.id}
        for f in fundos:
            assert len(f.eventos) == 2
            pos = sessao.scalar(select(Posicao).where(Posicao.ativo_id == f.ativo_id))
            assert pos.carteira_id == sessao.get(Ativo, f.ativo_id).carteira_id
        assert _contar(sessao, PrecoHistorico) == 2
        assert db._isolar_ativos_iliquidos(sessao) == 0


class TestAutorizacao:
    def test_evento_em_fundo_alheio_e_recusado(self, sessao, dois_clientes):
        _, cart_a, bruno, _ = dois_clientes
        with pytest.raises(operacoes.ErroOperacao, match="RN-008"):
            operacoes.registrar_evento_pe(sessao, bruno, _fundo(sessao, cart_a),
                                          tipo="NAV_UPDATE", data_evento=date(2025, 12, 31),
                                          valor=1, status="RECEBIDO")

    def test_confirmar_chamada_alheia_e_recusado(self, sessao, dois_clientes):
        _, cart_a, bruno, _ = dois_clientes
        pendente = next(e for e in _fundo(sessao, cart_a).eventos if e.status == "PENDENTE")
        with pytest.raises(operacoes.ErroOperacao, match="RN-008"):
            operacoes.confirmar_capital_call(sessao, bruno, pendente.id, date(2025, 9, 1))
        assert sessao.get(EventoPE, pendente.id).status == "PENDENTE"

    def test_reavaliar_imovel_alheio_idor(self, sessao, dois_clientes):
        """Bruno informa a PRÓPRIA carteira e o id de um imóvel de Ana."""
        _, cart_a, bruno, cart_b = dois_clientes
        imovel_a = consolidacao.imoveis_da_carteira(sessao, cart_a.id)[0]
        valor_antes = imovel_a.valor_de_mercado
        with pytest.raises(operacoes.ErroOperacao, match="nesta carteira"):
            operacoes.reavaliar_imovel(sessao, bruno, cart_b.id, imovel_a.id,
                                       valor=1.0, data_avaliacao=date(2025, 1, 1))
        with pytest.raises(operacoes.ErroOperacao, match="RN-008"):
            operacoes.reavaliar_imovel(sessao, bruno, cart_a.id, imovel_a.id,
                                       valor=1.0, data_avaliacao=date(2025, 1, 1))
        sessao.refresh(imovel_a)
        assert imovel_a.valor_de_mercado == valor_antes

    def test_importar_em_carteira_alheia_e_recusado(self, sessao, dois_clientes, tmp_path):
        _, cart_a, bruno, _ = dois_clientes
        with pytest.raises(ValueError, match="RN-008"):
            importar_template(sessao, cart_a, "LIQUIDOS", tmp_path, bruno.id)
        assert _contar(sessao, Posicao, Posicao.carteira_id == cart_a.id) == 7

    def test_colaborador_vinculado_pode_operar(self, sessao, dois_clientes):
        ana, cart_a, _, _ = dois_clientes
        assessor = criar_usuario(sessao, "col@exemplo.com", "Col", perfil="COLABORADOR")
        fundo = _fundo(sessao, cart_a)
        with pytest.raises(operacoes.ErroOperacao):
            operacoes.registrar_evento_pe(sessao, assessor, fundo, tipo="NAV_UPDATE",
                                          data_evento=date(2025, 12, 31), valor=1,
                                          status="RECEBIDO")
        sessao.get(Cliente, cart_a.cliente_id).colaborador_id = assessor.id
        sessao.commit()
        operacoes.registrar_evento_pe(sessao, assessor, fundo, tipo="NAV_UPDATE",
                                      data_evento=date(2025, 12, 31), valor=1, status="RECEBIDO")

    def test_confirmar_chamada_pendente(self, sessao, dois_clientes):
        """UC-09 FA-01: a pendente vira PAGA e passa a contar no paid-in."""
        ana, cart_a, _, _ = dois_clientes
        fundo = _fundo(sessao, cart_a)
        pendente = next(e for e in fundo.eventos if e.status == "PENDENTE")
        operacoes.confirmar_capital_call(sessao, ana, pendente.id, date(2025, 9, 1))
        sessao.refresh(fundo)
        assert fundo.paid_in_capital == pytest.approx(1_750_000)
        assert fundo.dry_powder == pytest.approx(250_000)


class TestLGPD:
    def test_dossie_e_apagado_apos_o_resgate(self, sessao, dois_clientes):
        ana = dois_clientes[0]
        exportacao = lgpd.exportar(sessao, ana.id, "JSON")
        assert exportacao.caminho.exists()
        resgatada = lgpd.resgatar(exportacao.token)
        assert resgatada.conteudo and b"ana@exemplo.com" in resgatada.conteudo
        assert not exportacao.caminho.exists()
        assert lgpd.resgatar(exportacao.token) is None

    def test_exportacao_nao_resgatada_expira(self, sessao, dois_clientes):
        from datetime import datetime, timedelta, timezone
        exportacao = lgpd.exportar(sessao, dois_clientes[0].id, "CSV")
        depois = datetime.now(timezone.utc) + timedelta(hours=25)
        assert lgpd.expurgar_exportacoes(depois) >= 1
        assert not exportacao.caminho.exists()

    def test_exclusao_anonimiza_toda_a_trilha(self, sessao, dois_clientes):
        from src import administracao
        ana, cart_a, bruno, _ = dois_clientes
        admin = criar_usuario(sessao, "adm@exemplo.com", "Adm", perfil="ADMIN")
        administracao.alterar_usuario(sessao, admin, ana.id, perfil="CLIENTE", ativo=True)
        lgpd.exportar(sessao, ana.id, "JSON")
        dora, _ = administracao.criar_usuario(sessao, admin, email="dora@exemplo.com",
                                              nome="Dora Teste", perfil="CLIENTE")

        lgpd.excluir(sessao, ana.id)
        lgpd.excluir(sessao, dora.id)

        assert list(lgpd.config.EXPORT_DIR.glob(f"lgpd_{ana.id}_*")) == []
        proibidos = (ana.id, cart_a.id, "ana@exemplo.com", "Ana Teste",
                     dora.id, "dora@exemplo.com", "Dora Teste")
        for log in sessao.scalars(select(AuditLog)).all():
            texto = " ".join(filter(None, (log.usuario_id, log.entidade_id,
                                           log.dados_novos, log.dados_anteriores)))
            for proibido in proibidos:
                assert proibido not in texto
        assert _contar(sessao, AuditLog, AuditLog.usuario_id == bruno.id) > 0
