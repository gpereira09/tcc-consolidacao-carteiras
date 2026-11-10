"""Testes da validação e importação dos templates (UC-04, RN-002 a RN-008)."""
from __future__ import annotations

import io
from datetime import date

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select

from src import db, ingest
from src.models import (
    Ativo,
    AtivoIliquido,
    AtivoImovel,
    AuditLog,
    Carteira,
    Cliente,
    EventoPE,
    ImportacaoLog,
    Posicao,
    PrecoHistorico,
    Transacao,
    Usuario,
)
from src.schema import TEMPLATES
from src.security import ErroAutorizacao
from src.templates_xlsx import gerar_template


@pytest.fixture
def sessao():
    db.resetar_schema()
    s = db.SessionLocal()
    yield s
    s.close()


def _cliente(s, email: str) -> tuple[Usuario, Carteira]:
    usuario = Usuario(email=email, senha_hash="x", perfil="CLIENTE")
    s.add(usuario)
    s.flush()
    cliente = Cliente(usuario_id=usuario.id, nome=email, moeda_base="BRL")
    s.add(cliente)
    s.flush()
    carteira = Carteira(cliente_id=cliente.id, nome="Carteira Principal")
    s.add(carteira)
    s.commit()
    return usuario, carteira


@pytest.fixture
def titular(sessao):
    return _cliente(sessao, "titular@exemplo.com")


def _planilha(tmp_path, chave: str, abas: dict[str, list[list]] | None = None,
              cabecalho: dict[str, list[str]] | None = None) -> bytes:
    """Template gerado pelo sistema, com as linhas de exemplo trocadas por `abas`."""
    caminho = gerar_template(chave, tmp_path / f"{chave}.xlsx")
    wb = load_workbook(caminho)
    for nome, linhas in (abas or {}).items():
        ws = wb[nome]
        ws.delete_rows(2, ws.max_row)
        for linha in linhas:
            ws.append(linha)
    for nome, colunas in (cabecalho or {}).items():
        ws = wb[nome]
        for j in range(1, ws.max_column + 1):
            ws.cell(row=1, column=j).value = None
        for j, coluna in enumerate(colunas, start=1):
            ws.cell(row=1, column=j, value=coluna)
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()


def _liquido(ticker="PETR4", classe="ACAO", mercado="B3", moeda="BRL", qtd=100,
             pm=30.0, vf=None, data="10/01/2025", nome="Petrobras PN"):
    return [ticker, nome, classe, mercado, moeda, qtd, pm, qtd * pm if vf is None else vf,
            data, "", ""]


def _erros(previa, coluna=None):
    return [p for p in previa.erros if coluna is None or p.coluna == coluna]


class TestEstrutura:
    def test_arquivo_ilegivel(self):
        with pytest.raises(ingest.ErroEstrutural, match="ilegível"):
            ingest.validar(b"nao sou xlsx", "LIQUIDOS")

    def test_coluna_renomeada_aborta_o_arquivo(self, tmp_path):
        colunas = [c.nome for c in TEMPLATES["LIQUIDOS"].aba("Dados").colunas]
        colunas[2] = "tipo"
        dados = _planilha(tmp_path, "LIQUIDOS", cabecalho={"Dados": colunas})
        with pytest.raises(ingest.ErroEstrutural) as exc:
            ingest.validar(dados, "LIQUIDOS")
        assert "esperado 'classe', encontrado 'tipo'" in str(exc.value)

    def test_coluna_obrigatoria_ausente(self, tmp_path):
        colunas = [c.nome for c in TEMPLATES["IMOVEIS"].aba("Dados").colunas][:-1]
        dados = _planilha(tmp_path, "IMOVEIS", cabecalho={"Dados": colunas})
        with pytest.raises(ingest.ErroEstrutural, match="'moeda' ausente"):
            ingest.validar(dados, "IMOVEIS")

    def test_template_trocado(self, tmp_path):
        dados = _planilha(tmp_path, "IMOVEIS")
        with pytest.raises(ingest.ErroEstrutural, match="foi enviado como"):
            ingest.validar(dados, "PEVC")

    def test_coluna_de_extensao_pode_faltar(self, tmp_path):
        colunas = [c.nome for c in TEMPLATES["PEVC"].aba("Eventos").colunas if not c.extensao]
        dados = _planilha(
            tmp_path, "PEVC",
            abas={"Fundos": [["F1", "Fundo 1", "Gestora", "PRIVATE_EQUITY", "BRL", 1000, "01/01/2024", ""]],
                  "Eventos": [["F1", "10/01/2024", "CAPITAL_CALL", 100, "PAGO", "", ""]]},
            cabecalho={"Eventos": colunas},
        )
        previa = ingest.validar(dados, "PEVC")
        assert previa.erros == []
        assert previa.linhas_validas == 2


class TestExemplosDoTemplate:
    @pytest.mark.parametrize("chave", ["LIQUIDOS", "PEVC", "IMOVEIS"])
    def test_exemplos_sao_validos(self, tmp_path, chave):
        """As linhas de exemplo do próprio template passam em todas as regras."""
        previa = ingest.validar(_planilha(tmp_path, chave), chave)
        assert previa.erros == []
        assert previa.linhas_rejeitadas == 0
        assert any("exemplo" in p.mensagem for p in previa.avisos)

    def test_importa_exemplos_pevc_e_calcula_metricas(self, tmp_path, sessao, titular):
        usuario, carteira = titular
        previa = ingest.validar(_planilha(tmp_path, "PEVC"), "PEVC", sessao, carteira.id)
        resultado = ingest.importar(sessao, previa, usuario=usuario, enriquecer=False)

        assert resultado.eventos == 14
        assert sorted(resultado.criados) == ["FUND-PE-002", "FUND-RE-003", "FUND-VC-001"]
        fundo = sessao.scalar(select(AtivoIliquido).where(
            AtivoIliquido.identificador_fundo == "FUND-VC-001"))
        assert fundo.paid_in_capital == pytest.approx(1_500_000)
        assert fundo.dry_powder == pytest.approx(250_000)
        assert fundo.nav_atual == pytest.approx(2_050_000)
        assert fundo.ativo.carteira_id == carteira.id


class TestLiquidos:
    def test_regras_semanticas(self, tmp_path):
        dados = _planilha(tmp_path, "LIQUIDOS", {"Dados": [
            _liquido(),                                              # 2: válida
            _liquido("VALE3", vf=5000),                              # 3: valor incoerente
            _liquido("AAPL", mercado="NASDAQ", moeda="BRL"),         # 4: moeda x mercado
            _liquido("ITUB4", data="01/01/2027"),                    # 5: data futura
            _liquido("BBAS3", qtd=-5, vf=150),                       # 6: quantidade negativa
            _liquido("WEGE3", classe="CRIPTO"),                      # 7: fora da lista
            ["", "Sem ticker", "ACAO", "B3", "BRL", 1, 1, 1, "01/01/2025", "", ""],  # 8: obrigatório
        ]})
        previa = ingest.validar(dados, "LIQUIDOS")
        rejeitadas = {p.linha: p.coluna for p in previa.erros}
        assert rejeitadas == {3: "valor_financeiro", 4: "moeda", 5: "data_aquisicao",
                              6: "quantidade", 7: "classe", 8: "ticker_ou_cnpj"}
        assert previa.linhas_validas == 1

    def test_numeros_no_formato_brasileiro_e_lista_sem_acento(self, tmp_path):
        linha = ["petr4.sa", "", "ação", "B3", "BRL", "1500", "32,45", "48.675,00", "15/03/2023", "", ""]
        previa = ingest.validar(_planilha(tmp_path, "LIQUIDOS", {"Dados": [linha]}), "LIQUIDOS")
        assert previa.erros == []
        d = previa.validos[0].dados
        assert (d["ticker"], d["classe"], d["quantidade"], d["preco_medio"]) == ("PETR4", "ACAO", 1500, 32.45)

    def test_ponto_isolado_e_decimal_e_a_coerencia_denuncia(self, tmp_path):
        """'1.500' em texto é 1,5 (ponto decimal); a checagem quantidade × preço rejeita a linha."""
        linha = ["PETR4", "", "ACAO", "B3", "BRL", "1.500", "32,45", "48.675,00", "15/03/2023", "", ""]
        previa = ingest.validar(_planilha(tmp_path, "LIQUIDOS", {"Dados": [linha]}), "LIQUIDOS")
        assert [e.coluna for e in previa.erros] == ["valor_financeiro"]

    def test_duplicatas_consolidadas_por_preco_medio(self, tmp_path, sessao, titular):
        _, carteira = titular
        dados = _planilha(tmp_path, "LIQUIDOS", {"Dados": [
            _liquido(qtd=100, pm=30.0, data="10/01/2025"),
            _liquido(qtd=300, pm=34.0, data="10/03/2025"),
        ]})
        previa = ingest.validar(dados, "LIQUIDOS", sessao, carteira.id)
        assert any("consolidadas" in p.mensagem for p in previa.avisos)
        ingest.importar(sessao, previa, enriquecer=False)

        pos = sessao.scalar(select(Posicao))
        assert pos.quantidade == 400
        assert pos.preco_medio == pytest.approx(33.0)
        assert pos.data_inicial == date(2025, 1, 10)
        assert sessao.scalar(select(func.count()).select_from(Transacao)) == 2
        assert sessao.scalar(select(func.count()).select_from(PrecoHistorico)) == 0

    def test_importacao_parcial_e_rastreabilidade(self, tmp_path, sessao, titular):
        usuario, carteira = titular
        dados = _planilha(tmp_path, "LIQUIDOS", {"Dados": [_liquido(), _liquido("VALE3", vf=1)]})
        previa = ingest.validar(dados, "LIQUIDOS", sessao, carteira.id, nome_arquivo="minha.xlsx")
        resultado = ingest.importar(sessao, previa, usuario=usuario, enriquecer=False)

        assert (resultado.linhas_importadas, resultado.linhas_rejeitadas) == (1, 1)
        log = sessao.get(ImportacaoLog, resultado.importacao_id)
        assert (log.nome_arquivo, log.linhas_lidas, log.linhas_importadas) == ("minha.xlsx", 2, 1)
        auditoria = sessao.scalar(select(AuditLog).where(AuditLog.acao == "IMPORTAR_TEMPLATE"))
        assert auditoria.usuario_id == usuario.id
        assert sessao.scalar(select(Ativo.ticker_canonico)) == "PETR4"

    def test_reimportacao_soma_na_posicao_existente(self, tmp_path, sessao, titular):
        _, carteira = titular
        dados = _planilha(tmp_path, "LIQUIDOS", {"Dados": [_liquido(qtd=100, pm=30.0)]})
        ingest.importar(sessao, ingest.validar(dados, "LIQUIDOS", sessao, carteira.id), enriquecer=False)
        dados = _planilha(tmp_path, "LIQUIDOS", {"Dados": [_liquido(qtd=100, pm=40.0, data="01/06/2025")]})
        previa = ingest.validar(dados, "LIQUIDOS", sessao, carteira.id)
        assert any("já existe na carteira" in p.mensagem for p in previa.avisos)
        ingest.importar(sessao, previa, enriquecer=False)

        pos = sessao.scalar(select(Posicao))
        assert (pos.quantidade, pos.preco_medio) == (200, pytest.approx(35.0))
        assert sessao.scalar(select(func.count()).select_from(Ativo)) == 1

    def test_ativo_listado_e_compartilhado_entre_clientes(self, tmp_path, sessao, titular):
        _, carteira = titular
        _, outra = _cliente(sessao, "outro@exemplo.com")
        dados = _planilha(tmp_path, "LIQUIDOS", {"Dados": [_liquido()]})
        for c in (carteira, outra):
            ingest.importar(sessao, ingest.validar(dados, "LIQUIDOS", sessao, c.id), enriquecer=False)
        assert sessao.scalar(select(func.count()).select_from(Ativo)) == 1
        assert sessao.scalar(select(func.count()).select_from(Posicao)) == 2


class TestPEVC:
    FUNDO = ["F1", "Fundo Um", "Gestora", "VENTURE_CAPITAL", "BRL", 1000, "01/01/2024", "01/01/2034"]

    def _previa(self, tmp_path, eventos, fundos=None, sessao=None, carteira_id=None):
        dados = _planilha(tmp_path, "PEVC", {"Fundos": fundos or [self.FUNDO], "Eventos": eventos})
        return ingest.validar(dados, "PEVC", sessao, carteira_id)

    def test_rn004_em_ordem_cronologica(self, tmp_path):
        previa = self._previa(tmp_path, [
            ["F1", "01/06/2024", "CAPITAL_CALL", 600, "PAGO", "", ""],
            ["F1", "01/03/2024", "CAPITAL_CALL", 300, "PENDENTE", "", ""],
            ["F1", "01/09/2024", "CAPITAL_CALL", 200, "PAGO", "", ""],
        ])
        erros = _erros(previa)
        assert [(e.linha, e.coluna) for e in erros] == [(4, "valor_evento")]
        assert "RN-004" in erros[0].mensagem

    def test_rn005_distribuicao_antes_de_integralizar(self, tmp_path):
        previa = self._previa(tmp_path, [
            ["F1", "01/02/2024", "DISTRIBUICAO", 50, "RECEBIDO", "", "", "RENDIMENTO"],
            ["F1", "01/03/2024", "CAPITAL_CALL", 100, "PAGO", "", ""],
            ["F1", "01/04/2024", "DISTRIBUICAO", 50, "RECEBIDO", "", "", "RENDIMENTO"],
        ])
        assert [(e.linha, "RN-005" in e.mensagem) for e in previa.erros] == [(2, True)]

    def test_regras_de_evento(self, tmp_path):
        previa = self._previa(tmp_path, [
            ["F9", "01/03/2024", "CAPITAL_CALL", 100, "PAGO", "", ""],          # 2: fundo inexistente
            ["F1", "01/12/2023", "CAPITAL_CALL", 100, "PAGO", "", ""],          # 3: antes do início
            ["F1", "01/03/2024", "DISTRIBUICAO", 10, "PAGO", "", ""],           # 4: status incompatível
            ["F1", "01/12/2026", "CAPITAL_CALL", 100, "PAGO", "", ""],          # 5: futura paga
            ["F1", "01/12/2026", "CAPITAL_CALL", 100, "PENDENTE", "", ""],      # 6: futura pendente ok
            ["F1", "01/03/2024", "CAPITAL_CALL", 100, "PAGO", 1.5, "", ""],     # 7: nav_unidade ignorado
        ])
        assert {e.linha: e.coluna for e in previa.erros} == {
            2: "identificador_fundo", 3: "data_evento", 4: "status", 5: "data_evento"}
        assert {r.linha for r in previa.validos if r.aba == "Eventos"} == {6, 7}
        assert any(p.linha == 7 and p.coluna == "nav_unidade" for p in previa.avisos)

    def test_fundo_duplicado_e_rejeitado_com_eventos(self, tmp_path):
        previa = self._previa(
            tmp_path,
            [["F2", "01/03/2024", "CAPITAL_CALL", 100, "PAGO", "", ""]],
            fundos=[self.FUNDO, ["F1", "Fundo Um", "Gestora", "VENTURE_CAPITAL", "BRL", 1000, "01/01/2024", ""],
                    ["F2", "Fundo Dois", "Gestora", "PRIVATE_EQUITY", "BRL", 0, "01/01/2024", ""]],
        )
        assert any("linha 2" in e.mensagem for e in previa.erros if e.linha == 3)
        assert any(e.aba == "Eventos" and "rejeitado" in e.mensagem for e in previa.erros)

    def test_eventos_ja_registrados_contam_para_rn004(self, tmp_path, sessao, titular):
        _, carteira = titular
        primeira = self._previa(tmp_path, [["F1", "01/03/2024", "CAPITAL_CALL", 800, "PAGO", "", ""]],
                                sessao=sessao, carteira_id=carteira.id)
        ingest.importar(sessao, primeira, enriquecer=False)

        segunda = self._previa(tmp_path, [["F1", "01/03/2024", "CAPITAL_CALL", 800, "PAGO", "", ""],
                                          ["F1", "01/05/2024", "CAPITAL_CALL", 300, "PAGO", "", ""]],
                               sessao=sessao, carteira_id=carteira.id)
        assert any("idêntico" in e.mensagem for e in segunda.erros if e.linha == 2)
        assert any("RN-004" in e.mensagem for e in segunda.erros if e.linha == 3)
        assert any("já cadastrado" in p.mensagem for p in segunda.avisos)

    def test_fundo_de_outra_carteira_nao_e_reaproveitado(self, tmp_path, sessao, titular):
        _, carteira = titular
        _, outra = _cliente(sessao, "outro@exemplo.com")
        eventos = [["F1", "01/03/2024", "CAPITAL_CALL", 100, "PAGO", "", ""]]
        for c in (carteira, outra):
            ingest.importar(sessao, self._previa(tmp_path, eventos, sessao=sessao, carteira_id=c.id),
                            enriquecer=False)
        assert sessao.scalar(select(func.count()).select_from(AtivoIliquido)) == 2
        assert sessao.scalar(select(func.count()).select_from(EventoPE)) == 2


class TestImoveis:
    def _linha(self, ident="IMV-1", aquisicao="01/01/2020", avaliacao="30/06/2025", mercado=1_500_000):
        return [ident, "Apartamento", "RESIDENCIAL", "", 100, aquisicao, 1_000_000, mercado,
                avaliacao, 5000, 1000, "BRL"]

    def test_avaliacao_anterior_a_aquisicao(self, tmp_path):
        dados = _planilha(tmp_path, "IMOVEIS", {"Dados": [self._linha(avaliacao="01/01/2019")]})
        previa = ingest.validar(dados, "IMOVEIS")
        assert [e.coluna for e in previa.erros] == ["data_avaliacao"]

    def test_importa_e_versiona_reavaliacao(self, tmp_path, sessao, titular):
        _, carteira = titular
        dados = _planilha(tmp_path, "IMOVEIS", {"Dados": [self._linha()]})
        ingest.importar(sessao, ingest.validar(dados, "IMOVEIS", sessao, carteira.id), enriquecer=False)
        dados = _planilha(tmp_path, "IMOVEIS", {"Dados": [self._linha(avaliacao="31/08/2026", mercado=1_600_000)]})
        previa = ingest.validar(dados, "IMOVEIS", sessao, carteira.id)
        assert any("versionada" in p.mensagem for p in previa.avisos)
        ingest.importar(sessao, previa, enriquecer=False)

        imovel = sessao.scalar(select(AtivoImovel))
        assert (imovel.valor_de_mercado, imovel.data_avaliacao) == (1_600_000, date(2026, 8, 31))
        precos = sessao.scalars(select(PrecoHistorico.preco_fechamento).order_by(PrecoHistorico.data)).all()
        assert precos == [1_000_000, 1_500_000, 1_600_000]
        assert sessao.scalar(select(func.count()).select_from(Transacao)) == 1

        from src.calc import consolidacao
        snap = consolidacao.snapshot(sessao, carteira.id)
        assert snap.patrimonio == pytest.approx(1_600_000)


class TestAutorizacao:
    def test_rn008_carteira_de_outro_cliente(self, tmp_path, sessao, titular):
        _, carteira = titular
        intruso, _ = _cliente(sessao, "intruso@exemplo.com")
        dados = _planilha(tmp_path, "LIQUIDOS", {"Dados": [_liquido()]})
        previa = ingest.validar(dados, "LIQUIDOS", sessao, carteira.id)
        with pytest.raises(ErroAutorizacao, match="RN-008"):
            ingest.importar(sessao, previa, usuario=intruso, enriquecer=False)
        assert sessao.scalar(select(func.count()).select_from(Posicao)) == 0

    def test_previa_de_outra_carteira(self, tmp_path, sessao, titular):
        _, carteira = titular
        _, outra = _cliente(sessao, "outro@exemplo.com")
        dados = _planilha(tmp_path, "LIQUIDOS", {"Dados": [_liquido()]})
        previa = ingest.validar(dados, "LIQUIDOS", sessao, carteira.id)
        with pytest.raises(ValueError, match="outra carteira"):
            ingest.importar(sessao, previa, outra.id, enriquecer=False)
