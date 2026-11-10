"""
Especificação declarativa dos templates de ingestão (capítulo 9 do TCC).

Este módulo é a *fonte única de verdade* das colunas: o gerador de XLSX
(`src.templates_xlsx`) e o validador de importação (`src.ingest`) leem daqui.
Alterar uma coluna em um só lugar mantém gerador e parser sincronizados,
eliminando a classe de defeito mais comum em ingestão por planilha.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import config

TipoCampo = Literal["texto", "inteiro", "decimal", "data", "lista"]


@dataclass(frozen=True)
class Coluna:
    nome: str
    tipo: TipoCampo
    obrigatoria: bool
    descricao: str
    valores: tuple[str, ...] = ()
    decimais: int = 2
    largura: int = 20
    extensao: bool = False


@dataclass(frozen=True)
class Aba:
    nome: str
    colunas: list[Coluna]
    exemplos: list[list] = field(default_factory=list)


@dataclass(frozen=True)
class Template:
    chave: str
    arquivo: str
    titulo: str
    descricao: str
    instrucoes: list[str]
    abas: list[Aba]

    def aba(self, nome: str) -> Aba:
        for a in self.abas:
            if a.nome == nome:
                return a
        raise KeyError(nome)


_COLS_LIQUIDOS = [
    Coluna("ticker_ou_cnpj", "texto", True,
           "Ticker do ativo (ex.: PETR4, AAPL) ou CNPJ do fundo, sem máscara.",
           largura=18),
    Coluna("nome", "texto", False,
           "Nome do ativo. Se vazio, o sistema preenche automaticamente.",
           largura=32),
    Coluna("classe", "lista", True, "Classe do ativo.",
           valores=config.CLASSES_LIQUIDAS, largura=16),
    Coluna("mercado", "lista", True, "Bolsa/mercado de negociação.",
           valores=config.MERCADOS, largura=13),
    Coluna("moeda", "lista", True, "Moeda de negociação do ativo.",
           valores=config.MOEDAS, largura=9),
    Coluna("quantidade", "decimal", True, "Quantidade de cotas/ações da posição.",
           decimais=8, largura=14),
    Coluna("preco_medio", "decimal", True, "Preço médio de aquisição, na moeda do ativo.",
           decimais=4, largura=14),
    Coluna("valor_financeiro", "decimal", True,
           "Total investido = quantidade x preco_medio. Confira: divergência acima de 1% é rejeitada.",
           decimais=2, largura=18),
    Coluna("data_aquisicao", "data", True, "Data da aquisição inicial (dd/mm/aaaa).",
           largura=16),
    Coluna("setor", "texto", False, "Setor da empresa. Preenchido automaticamente p/ listados.",
           largura=22),
    Coluna("observacao", "texto", False, "Observação livre para registro pessoal.",
           largura=30),
]

_EXEMPLOS_LIQUIDOS = [
    ["PETR4", "Petrobras PN", "ACAO", "B3", "BRL", 1500, 32.4500, 48675.00, "15/03/2023",
     "Petróleo e Gás", "Posição núcleo Brasil"],
    ["VALE3", "Vale ON", "ACAO", "B3", "BRL", 800, 61.2000, 48960.00, "22/05/2023",
     "Mineração", ""],
    ["ITUB4", "Itaú Unibanco PN", "ACAO", "B3", "BRL", 2000, 27.8000, 55600.00, "10/01/2024",
     "Financeiro", ""],
    ["BOVA11", "iShares Ibovespa", "ETF", "B3", "BRL", 400, 112.5000, 45000.00, "05/02/2024",
     "Índice", ""],
    ["HGLG11", "CSHG Logística FII", "FII", "B3", "BRL", 350, 160.0000, 56000.00, "18/08/2023",
     "Logística", "Renda mensal"],
    ["AAPL", "Apple Inc.", "ACAO", "NASDAQ", "USD", 120, 178.5000, 21420.00, "12/06/2023",
     "Tecnologia", "Offshore"],
    ["MSFT", "Microsoft Corp.", "ACAO", "NASDAQ", "USD", 90, 330.0000, 29700.00, "03/07/2023",
     "Tecnologia", ""],
    ["VOO", "Vanguard S&P 500 ETF", "ETF", "NYSE", "USD", 60, 410.0000, 24600.00, "20/09/2023",
     "Índice", "Core offshore"],
    ["TESOURO_IPCA_2035", "Tesouro IPCA+ 2035", "TESOURO_DIRETO", "B3", "BRL",
     12.5, 3200.0000, 40000.00, "14/11/2022", "Título Público", ""],
    ["ASML", "ASML Holding", "ACAO", "EURONEXT", "EUR", 25, 620.0000, 15500.00, "08/04/2024",
     "Tecnologia", ""],
]

TEMPLATE_LIQUIDOS = Template(
    chave="LIQUIDOS",
    arquivo="Template_Ativos_Liquidos.xlsx",
    titulo="Template de Ingestão — Ativos Líquidos",
    descricao=(
        "Ações (Brasil e offshore), ETFs, fundos, FIIs e Tesouro Direto. "
        "Ativos com ticker listado são enriquecidos automaticamente com preço "
        "de mercado após a importação (RN-006)."
    ),
    instrucoes=[
        "Preencha SOMENTE a aba 'Dados'. Não renomeie, não reordene e não exclua colunas.",
        "A primeira linha da aba 'Dados' é o cabeçalho técnico e não deve ser alterada.",
        "As linhas de exemplo já preenchidas são ilustrativas: apague-as antes de enviar.",
        "Colunas marcadas como obrigatórias não podem ficar vazias.",
        "Datas no formato dd/mm/aaaa. Números com vírgula ou ponto decimal são aceitos.",
        "Use os valores das listas suspensas em 'classe', 'mercado' e 'moeda'.",
        "Regra de coerência: valor_financeiro deve bater com quantidade x preco_medio (tolerância de 1%).",
        "A moeda deve ser coerente com o mercado (B3=BRL, NYSE/NASDAQ=USD, LSE=GBP, EURONEXT=EUR).",
        "Não informe datas futuras em data_aquisicao.",
        "Se o mesmo ticker aparecer duas vezes, as posições são consolidadas por preço médio ponderado.",
    ],
    abas=[Aba("Dados", _COLS_LIQUIDOS, _EXEMPLOS_LIQUIDOS)],
)


_COLS_PEVC_FUNDOS = [
    Coluna("identificador_fundo", "texto", True,
           "Identificador único do fundo, definido por você (ex.: FUND-VC-001). "
           "Deve ser repetido na aba 'Eventos'.", largura=22),
    Coluna("nome_fundo", "texto", True, "Nome completo do fundo.", largura=34),
    Coluna("gestor", "texto", True, "Gestora responsável.", largura=26),
    Coluna("sub_classe", "lista", True, "Classe do veículo.",
           valores=config.SUBCLASSES_ILIQUIDAS, largura=20),
    Coluna("moeda", "lista", True, "Moeda do commitment e dos eventos.",
           valores=config.MOEDAS, largura=9),
    Coluna("commitment", "decimal", True, "Valor total comprometido pelo investidor.",
           decimais=2, largura=18),
    Coluna("data_inicio", "data", True, "Data do primeiro aporte (initial close).",
           largura=14),
    Coluna("data_fim_prevista", "data", False, "Encerramento previsto (tipicamente 10 anos).",
           largura=18),
]

_COLS_PEVC_EVENTOS = [
    Coluna("identificador_fundo", "texto", True,
           "Deve corresponder exatamente a um identificador da aba 'Fundos'.",
           largura=22),
    Coluna("data_evento", "data", True, "Data do evento (dd/mm/aaaa).", largura=14),
    Coluna("tipo_evento", "lista", True, "Natureza do evento.",
           valores=config.TIPOS_EVENTO_PE, largura=18),
    Coluna("valor_evento", "decimal", True,
           "Valor monetário. Em NAV_UPDATE, informe o NAV total da sua participação.",
           decimais=2, largura=16),
    Coluna("status", "lista", True,
           "CAPITAL_CALL: PENDENTE ou PAGO. DISTRIBUICAO: RECEBIDO. NAV_UPDATE: RECEBIDO.",
           valores=config.STATUS_EVENTO_PE, largura=14),
    Coluna("nav_unidade", "decimal", False, "NAV por cota. Apenas para NAV_UPDATE.",
           decimais=4, largura=14),
    Coluna("observacao", "texto", False, "Observação livre.", largura=28),
    Coluna("subtipo", "lista", False,
           "Classificação da DISTRIBUICAO (RF-012): RETORNO_CAPITAL, GANHO_CAPITAL ou "
           "RENDIMENTO. Deixe vazio nos demais tipos de evento.",
           valores=config.TIPOS_DISTRIBUICAO, largura=18, extensao=True),
]

_EX_PEVC_FUNDOS = [
    ["FUND-VC-001", "Orvalheira Ventures II", "Orvalheira Gestão de Recursos", "VENTURE_CAPITAL",
     "BRL", 2000000.00, "10/03/2022", "10/03/2032"],
    ["FUND-PE-002", "Tarimbá Equity Partners III", "Tarimbá Capital", "PRIVATE_EQUITY",
     "BRL", 3000000.00, "01/07/2021", "01/07/2031"],
    ["FUND-RE-003", "Quelvora Real Estate Fund IV", "Quelvora Asset Management", "REAL_ESTATE",
     "USD", 500000.00, "15/09/2022", "15/09/2032"],
]

_EX_PEVC_EVENTOS = [
    ["FUND-VC-001", "15/06/2022", "CAPITAL_CALL", 400000.00, "PAGO", "", "1a chamada (20%)"],
    ["FUND-VC-001", "20/01/2023", "CAPITAL_CALL", 600000.00, "PAGO", "", "2a chamada (30%)"],
    ["FUND-VC-001", "18/09/2023", "CAPITAL_CALL", 500000.00, "PAGO", "", "3a chamada (25%)"],
    ["FUND-VC-001", "12/04/2024", "DISTRIBUICAO", 300000.00, "RECEBIDO", "", "Saída parcial",
     "GANHO_CAPITAL"],
    ["FUND-VC-001", "31/12/2024", "NAV_UPDATE", 1850000.00, "RECEBIDO", 1.2333, "NAV 4T24"],
    ["FUND-VC-001", "30/06/2025", "NAV_UPDATE", 2050000.00, "RECEBIDO", 1.3667, "NAV 2T25"],
    ["FUND-VC-001", "15/08/2025", "CAPITAL_CALL", 250000.00, "PENDENTE", "", "4a chamada"],
    ["FUND-PE-002", "10/09/2021", "CAPITAL_CALL", 900000.00, "PAGO", "", "1a chamada"],
    ["FUND-PE-002", "22/05/2022", "CAPITAL_CALL", 1200000.00, "PAGO", "", "2a chamada"],
    ["FUND-PE-002", "30/11/2023", "DISTRIBUICAO", 450000.00, "RECEBIDO", "", "Retorno de capital",
     "RETORNO_CAPITAL"],
    ["FUND-PE-002", "30/06/2025", "NAV_UPDATE", 2400000.00, "RECEBIDO", "", "NAV 2T25"],
    ["FUND-RE-003", "15/11/2022", "CAPITAL_CALL", 200000.00, "PAGO", "", "1a chamada"],
    ["FUND-RE-003", "10/07/2023", "CAPITAL_CALL", 150000.00, "PAGO", "", "2a chamada"],
    ["FUND-RE-003", "30/06/2025", "NAV_UPDATE", 395000.00, "RECEBIDO", "", "NAV 2T25"],
]

TEMPLATE_PEVC = Template(
    chave="PEVC",
    arquivo="Template_PE_VC.xlsx",
    titulo="Template de Ingestão — Private Equity / Venture Capital",
    descricao=(
        "Fundos de PE, VC e Real Estate fechados. Modelo multi-aba: 'Fundos' "
        "cadastra os veículos e 'Eventos' registra capital calls, distribuições "
        "e atualizações de NAV."
    ),
    instrucoes=[
        "Preencha as abas 'Fundos' e 'Eventos'. Não renomeie nem reordene colunas.",
        "Cadastre o fundo na aba 'Fundos' ANTES de lançar seus eventos na aba 'Eventos'.",
        "O campo identificador_fundo é a chave que liga as duas abas: escreva exatamente igual.",
        "As linhas de exemplo são ilustrativas: apague-as antes de enviar.",
        "RN-004: a soma dos CAPITAL_CALL com status PAGO não pode ultrapassar o commitment.",
        "RN-005: só é possível lançar DISTRIBUICAO em fundo com capital já integralizado.",
        "Em NAV_UPDATE, valor_evento é o NAV TOTAL da sua participação, não o valor por cota.",
        "nav_unidade é opcional e só se aplica a NAV_UPDATE.",
        "subtipo classifica cada DISTRIBUICAO em retorno de capital, ganho de capital ou rendimento (RF-012).",
        "Datas no formato dd/mm/aaaa. Nenhum evento pode ser anterior à data_inicio do fundo.",
        "Capital calls PENDENTES não entram no paid-in capital, mas reduzem o dry powder disponível.",
    ],
    abas=[
        Aba("Fundos", _COLS_PEVC_FUNDOS, _EX_PEVC_FUNDOS),
        Aba("Eventos", _COLS_PEVC_EVENTOS, _EX_PEVC_EVENTOS),
    ],
)


_COLS_IMOVEIS = [
    Coluna("identificador_imovel", "texto", True, "Identificador do imóvel (ex.: IMV-001).",
           largura=20),
    Coluna("nome_descritivo", "texto", True, "Descrição do imóvel.", largura=34),
    Coluna("tipo_imovel", "lista", True, "Tipo do imóvel.",
           valores=config.TIPOS_IMOVEL, largura=16),
    Coluna("endereco", "texto", False, "Endereço completo (uso interno).", largura=38),
    Coluna("area_m2", "decimal", False, "Área privativa em m².", decimais=2, largura=12),
    Coluna("data_aquisicao", "data", True, "Data da escritura/aquisição.", largura=16),
    Coluna("valor_aquisicao", "decimal", True, "Valor pago na aquisição.", decimais=2,
           largura=18),
    Coluna("valor_mercado_atual", "decimal", True, "Estimativa de valor de mercado atual.",
           decimais=2, largura=20),
    Coluna("data_avaliacao", "data", True, "Data da última avaliação de mercado.",
           largura=16),
    Coluna("renda_mensal", "decimal", False, "Aluguel mensal bruto, se houver.",
           decimais=2, largura=15),
    Coluna("despesas_mensais", "decimal", False, "IPTU, condomínio e manutenção.",
           decimais=2, largura=18),
    Coluna("moeda", "lista", True, "Moeda dos valores informados.",
           valores=config.MOEDAS, largura=9),
]

_EX_IMOVEIS = [
    ["IMV-001", "Apartamento Batel - Curitiba", "RESIDENCIAL",
     "Rua Bispo Dom José, 1200 - Curitiba/PR", 180.00, "20/07/2019",
     1200000.00, 1650000.00, "30/06/2025", 6500.00, 1400.00, "BRL"],
    ["IMV-002", "Sala comercial Av. Paulista", "COMERCIAL",
     "Av. Paulista, 2300 - São Paulo/SP", 95.00, "05/11/2021",
     980000.00, 1150000.00, "30/06/2025", 7200.00, 1900.00, "BRL"],
    ["IMV-003", "Condo Brickell - Miami", "RESIDENCIAL",
     "1300 Brickell Bay Dr - Miami/FL", 120.00, "14/02/2022",
     620000.00, 735000.00, "30/06/2025", 4100.00, 1250.00, "USD"],
    ["IMV-004", "Galpão logístico São José dos Pinhais", "INDUSTRIAL",
     "Rod. BR-376, km 12 - São José dos Pinhais/PR", 1400.00, "30/08/2020",
     2300000.00, 3050000.00, "30/06/2025", 22000.00, 3800.00, "BRL"],
]

TEMPLATE_IMOVEIS = Template(
    chave="IMOVEIS",
    arquivo="Template_Imoveis.xlsx",
    titulo="Template de Ingestão — Imóveis Diretos",
    descricao=(
        "Imóveis detidos diretamente (residenciais, comerciais, industriais e rurais). "
        "Fundos imobiliários (FII) NÃO entram aqui: use o template de Ativos Líquidos, "
        "pois possuem ticker listado em bolsa."
    ),
    instrucoes=[
        "Preencha SOMENTE a aba 'Dados'. Não renomeie, não reordene e não exclua colunas.",
        "As linhas de exemplo são ilustrativas: apague-as antes de enviar.",
        "FIIs não pertencem a este template — use o Template de Ativos Líquidos.",
        "valor_mercado_atual alimenta a marcação patrimonial; mantenha a avaliação atualizada.",
        "data_avaliacao não pode ser anterior a data_aquisicao nem futura.",
        "renda_mensal e despesas_mensais são opcionais e alimentam o cálculo de yield líquido.",
        "Datas no formato dd/mm/aaaa. Valores em moeda única por linha.",
        "Cada reavaliação importada é versionada no histórico patrimonial do imóvel.",
    ],
    abas=[Aba("Dados", _COLS_IMOVEIS, _EX_IMOVEIS)],
)


TEMPLATES: dict[str, Template] = {
    TEMPLATE_LIQUIDOS.chave: TEMPLATE_LIQUIDOS,
    TEMPLATE_PEVC.chave: TEMPLATE_PEVC,
    TEMPLATE_IMOVEIS.chave: TEMPLATE_IMOVEIS,
}
