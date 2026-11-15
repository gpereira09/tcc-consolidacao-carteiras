# Instruções para agentes de código

Este arquivo vale para **qualquer agente** que trabalhe neste repositório: Claude Code, Codex, Cursor, GitHub Copilot, Gemini, Aider ou outro. Os arquivos específicos de cada ferramenta (`CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`, `.cursor/rules/`, `.claude/agents/`) apenas apontam para cá. **Mude as regras aqui, nunca neles.**

## 1. O projeto

O Sistema de Consolidação de Carteiras de Investimentos é o TCC de Engenharia de Software da Universidade Positivo (2026). Ele consolida ativos líquidos e ilíquidos (PE, VC, imóveis), do Brasil e do exterior, numa visão patrimonial única. Calcula TWR, MWR/IRR e as métricas de fundos ilíquidos (DPI, RVPI, TVPI), converte moedas com câmbio histórico e compara a carteira com benchmarks.

## 2. Fonte de verdade: a monografia

A **monografia do TCC** é a especificação do sistema. Ela é entregue à banca e não fica neste repositório; peça o arquivo a quem solicitou a tarefa se precisar consultar uma seção.

Antes de implementar, alterar ou revisar qualquer coisa, leia as seções correspondentes:

| Tarefa | Leia |
|---|---|
| Qualquer funcionalidade nova | 4.1 (Quadro 14), 4.2 (fora do escopo), 5.4 (Quadro 19, RF e prioridades MoSCoW) |
| Ingestão e templates XLSX | 2.3.1, 5.6 (RN-002, RN-003, RN-008), UC-04 (Quadro 26), capítulo 9 (Quadros 44 a 46) |
| Cálculos e rentabilidade | 2.4, 5.6 (RN-009 a RN-012), UC-12 a UC-15 (Quadros 34 a 37) |
| Fundos PE/VC e imóveis | 2.4.2, RN-004, RN-005, RN-012, UC-08 a UC-11 |
| Preços, câmbio e benchmarks | 2.3.2, 5.8, RN-006, RN-007, RN-009, Quadro 12 |
| Segurança, perfis e LGPD | 2.6, RN-001, RN-013 a RN-015, RNF-004, RNF-005, RNF-011, RNF-012, UC-01 a UC-03, UC-17 a UC-20 |
| Telas | 2.5, 5.2, Quadro 22, capítulo 10 (Figuras 25 a 28) e o UC da tela |
| Modelo de dados | 5.3 (Quadro 18), capítulo 7 (Figura 15) |

Se o pedido contradiz a monografia, **pare e avise** em vez de escolher sozinho. Se o código precisar ser diferente do texto por um bom motivo, implemente, registre a divergência na resposta e indique a seção a atualizar.

## 3. Guardrails obrigatórios

Os checklists completos estão em [`docs/guardioes/`](docs/guardioes/). Aplique o checklist correspondente antes de entregar uma mudança, ou peça a um subagente que o aplique:

| Checklist | Aplique quando… |
|---|---|
| [`guardiao-escopo`](docs/guardioes/guardiao-escopo.md) | for adicionar uma funcionalidade ou dependência |
| [`guardiao-regras-negocio`](docs/guardioes/guardiao-regras-negocio.md) | tocar ingestão, cálculos, PE/VC, câmbio, benchmarks ou transações |
| [`guardiao-seguranca-lgpd`](docs/guardioes/guardiao-seguranca-lgpd.md) | tocar autenticação, perfis, dados pessoais, criptografia, auditoria, exportação ou exclusão |
| [`guardiao-telas`](docs/guardioes/guardiao-telas.md) | criar ou alterar arquivos em `ui/` |
| [`guardiao-rastreabilidade`](docs/guardioes/guardiao-rastreabilidade.md) | antes de PR ou entrega, ou quando perguntarem o que já foi implementado |

### Regras que nunca podem ser quebradas
- **Escopo (seção 4.2):** nada de criptoativos, cálculo de impostos, gestão de passivos, execução de ordens, Open Finance, app mobile, planejamento financeiro ou gestão de patrimônio de terceiros.
- **Ingestão:** os templates XLSX são a fonte primária. As APIs externas só enriquecem o preço de ativos listados, e uma falha delas nunca interrompe a consolidação (RN-007, seção 5.8).
- **RN-003:** erro estrutural aborta o arquivo; erro semântico rejeita só a linha. Nada é gravado antes de o usuário confirmar a prévia.
- **RN-004 e RN-005:** chamadas (pagas e pendentes) ≤ commitment; distribuição só com paid-in > 0. Avalie em ordem cronológica.
- **RN-008 e RN-013:** toda leitura ou escrita de carteira passa por `security.pode_acessar_carteira` ou `carteiras_visiveis`.
- **Segurança:** senha só como hash bcrypt; CPF cifrado em repouso e mascarado na tela; segredos só em variáveis de ambiente; auditoria sem dados sensíveis; exclusão LGPD anonimiza a auditoria com `EXCLUIDO_LGPD`.
- **Dados:** nenhum dado real de investidor no repositório. Séries simuladas levam a fonte `SIMULADO`.

## 4. Arquitetura

| Camada | Pasta | Regra |
|---|---|---|
| Apresentação | `ui/` (Streamlit + Plotly) | Sem regra de negócio nem cálculo. Use `ui/comum.py` e `ui/estilo.py`. |
| Aplicação e domínio | `src/`, `src/calc/` | Não importa `streamlit`. |
| Persistência | `src/models.py`, `src/db.py` | SQLAlchemy 2.0 sobre SQLite (WAL). Nada de SQL bruto preso ao dialeto fora de `src/db.py`. |
| Integração | `src/market/` | Yahoo Finance, Banco Central (PTAX e SGS) e B3 (IFIX), com cache e fallback para o último valor. |

Outras regras de arquitetura:
- `src/schema.py` é a fonte única das colunas dos templates. O gerador (`src/templates_xlsx.py`) e o validador (`src/ingest.py`) leem dele.
- `src/calc/pe.py` é a única implementação das métricas de fundos.
- Parâmetros e domínios ficam em `config.py`, nunca soltos no código.
- Stack permitida: a do Quadro 17. Dependência nova exige justificativa no PR.

## 5. Convenções

- Código, comentários, docstrings, mensagens de interface e commits em **português**.
- Docstrings citam o requisito atendido (RF, RN, UC, seção ou quadro), sempre com a numeração **da monografia**.
- Números, moedas e datas exibidos passam por `src/formatacao.py` (padrão pt-BR).
- Commits no estilo Conventional Commits em português (`feat(ingest): …`, `fix(calc): …`, `docs(tcc): …`). **Não adicione trailers de coautoria** (`Co-Authored-By` ou similares).
- Nunca commite `data/` (banco, chaves, backups, uploads, exportações).

## 6. Testes

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

- Os testes usam um diretório temporário (`tests/conftest.py`) e nunca tocam `data/`. Utilitários comuns ficam em `tests/apoio.py`.
- Todo cálculo financeiro e toda regra de negócio precisam de teste unitário (seção 3.5). O exemplo da seção 2.4.1 (TWR de 7,1%) deve continuar passando.
- Ao terminar, informe o resultado real dos testes. Se não puder rodá-los, diga isso.

## 7. Ao terminar uma tarefa

Diga quais RF, RN e UC foram afetados, que checklist de `docs/guardioes/` você aplicou e se alguma seção da monografia precisa ser atualizada.
