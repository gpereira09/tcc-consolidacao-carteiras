# Sistema de Consolidação de Carteiras de Investimentos

**Ativos líquidos e ilíquidos — Brasil e offshore**
Universidade Positivo · Engenharia de Software · TCC 2026

Plataforma web para consolidar o patrimônio de investidores pessoa física cuja carteira combina ativos listados (Brasil e exterior), fundos fechados de Private Equity, Venture Capital e Real Estate, e imóveis diretos. O fluxo é este:

1. A ingestão é feita por três templates XLSX padronizados.
2. O sistema converte tudo para a moeda base.
3. Apura a rentabilidade (TWR, inclusive por ativo, e MWR) e as métricas de PE/VC (DPI, RVPI, TVPI e IRR).
4. Compara o resultado com benchmarks.

A especificação do sistema é a monografia do TCC, entregue à banca à parte deste repositório, com 28 requisitos funcionais, 12 não funcionais e 15 regras de negócio. Onde o código aplica uma regra do trabalho, o comentário cita o código (`RN-004`, `RF-017`, `UC-12`…).

---

## Execução

Requer Python 3.11 ou superior.

```bash
pip install -r requirements.txt
python scripts/seed_demo.py                                   # banco, templates e carga demonstrativa
CARTEIRA_DATA_REFERENCIA=2026-09-22 streamlit run app.py      # http://localhost:8501
```

No Windows (PowerShell): `$env:CARTEIRA_DATA_REFERENCIA="2026-09-22"; streamlit run app.py`.

Também é possível usar `./run.sh` (Linux/macOS) ou `run.bat` (Windows). Esses scripts carregam a demonstração se não houver banco e sobem a aplicação. Sem `CARTEIRA_DATA_REFERENCIA`, a aplicação calcula até a data de hoje.

Em todos os modos a aplicação atende só na própria máquina (`127.0.0.1`), porque as contas da demonstração têm senha pública. Para abrir na rede, troque essas senhas antes e depois passe `--server.address` ao Streamlit ou ajuste a porta publicada no `docker-compose.yml`.

| Outros modos | Comando |
|---|---|
| HTTPS/TLS local (RNF-005) | `./run.sh --https` ou `run.bat --https` → https://localhost:8501 (certificado autoassinado gerado na hora) |
| Docker (RNF-009) | `docker compose up --build` → https://localhost:8501 |
| Tarefas diárias via cron | `python scripts/tarefas_diarias.py [coleta\|backup]` |
| Testes | `pip install -r requirements-dev.txt` e depois `python -m pytest -q` |

### Instalador para Windows (usuário final)

Para quem só quer usar o sistema, sem Python, VS Code nem terminal: `Instalar-ConsolidacaoCarteiras-<versão>.exe`.

- Instala por usuário, **sem pedir administrador**, em `%LOCALAPPDATA%\Programs\ConsolidacaoCarteiras`, com Python 3.14 embutido.
- Os dados (banco, chaves, backups, registros) ficam à parte, em `%LOCALAPPDATA%\ConsolidacaoCarteiras`. Atualizar o programa não toca neles, e a desinstalação pergunta se devem ser apagados (o padrão é mantê-los).
- Opções do assistente: carregar a demonstração (só se ainda não houver banco), **iniciar com o Windows em segundo plano** (coleta às 19h30 e backup às 22h30 sem abrir nada) e atalho na área de trabalho.
- O atalho abre o navegador em `http://127.0.0.1:<porta>`; o servidor escuta só na própria máquina. Um ícone na bandeja oferece *Abrir*, *Pasta de dados* e *Encerrar*.
- Cada instalação gera o seu próprio segredo JWT; a chave de desenvolvimento de `config.py` não é usada.

Para gerar o instalador (máquina de build com Python 3.14 e Inno Setup 6):

```powershell
powershell -ExecutionPolicy Bypass -File distribuicao\construir.ps1            # → distribuicao\saida\
powershell -ExecutionPolicy Bypass -File distribuicao\construir.ps1 -SemInstalador   # só a pasta portátil
```

As versões das bibliotecas embarcadas ficam travadas em `distribuicao/requisitos-instalador.txt`, validadas com a suíte completa. O executável não tem assinatura digital, então o Windows SmartScreen pode pedir "Mais informações → Executar assim mesmo".

Contas da carga demonstrativa (senha `Senha123` para todas):

| Perfil | E-mail | O que vê (RN-013) |
|---|---|---|
| CLIENTE | investidor@exemplo.com | só as próprias carteiras |
| COLABORADOR | assessor@exemplo.com | as carteiras dos clientes vinculados a ele |
| ADMIN | admin@exemplo.com | todas as carteiras e a tela de Administração |

### Números da demonstração (reproduzíveis)

A carga usa uma **data de referência explícita**: `python scripts/seed_demo.py --data AAAA-MM-DD`, com padrão 2026-09-22. Com a mesma data, os números são idênticos em qualquer dia e em qualquer máquina. O teste `tests/test_demo_reproduzivel.py` confere isso, e a carga foi repetida dentro do contêiner Docker com o mesmo resultado.

Em 22/09/2026, com moeda base BRL:

| Indicador | Valor |
|---|---|
| Patrimônio consolidado | R$ 17.307.431,30 |
| Custo de aquisição (câmbio histórico) | R$ 12.417.450,54 |
| Resultado sobre o custo | R$ 4.889.980,77 |
| TWR acumulado desde 20/07/2019 | 42,60% (5,07% a.a.) |
| MWR (IRR) desde o início | 7,41% a.a. |
| TWR e MWR em 12 meses | 4,16% e 4,16% |
| Volatilidade / drawdown máximo | 9,57% / −6,24% |

> **Os dados de mercado da demonstração são sintéticos.** Câmbio, benchmarks e cotações são gerados por simulação determinística (`src/market/seed_sintetico.py`) e gravados no banco com a fonte `SIMULADO`. Em produção, câmbio e cotações vêm dos coletores reais (PTAX do Banco Central e Yahoo Finance). Para benchmarks não há coletor real nesta versão.

---

## O que o sistema faz

| Capacidade | Onde | Requisitos |
|---|---|---|
| Ingestão por template XLSX, com validação em duas etapas e preview | `src/ingest.py`, tela *Importar carteira* | RF-004..006, RN-002, RN-003, RN-008 |
| Geração dos três templates, com instruções e listas suspensas | `src/schema.py`, `src/templates_xlsx.py` | Cap. 9 |
| Cadastro manual de ativo e de fundo; compra, venda e provento; novas carteiras | `src/operacoes.py`, tela *Lançamentos manuais* | RF-007, RF-009, UC-05, UC-06, UC-08 |
| Consolidação multimoeda (BRL, USD, EUR, GBP), com taxa histórica e forward fill | `src/market/cambio.py`, `src/calc/consolidacao.py` | RF-014, RN-009 |
| Enriquecimento de preço, nome e setor de ativos listados (Yahoo Finance) | `src/market/precos.py` | RF-008, RN-006, RN-007 |
| Coleta diária de preços e PTAX às 19h30 (Brasília) | `src/agendador.py`, `scripts/tarefas_diarias.py` | RF-013, RNF-006 |
| TWR da carteira e de cada ativo (Dietz modificado em subperíodos mensais) | `src/calc/rentabilidade.py`, `consolidacao.rentabilidade_por_ativo` | RF-015, RN-010 |
| MWR/IRR por Newton-Raphson, com bisseção de reserva | `src/calc/rentabilidade.py` | RF-016, RN-011 |
| Volatilidade, drawdown e Sharpe sobre a cota | `src/calc/rentabilidade.py` | Seção 2.4.1 |
| Capital calls (pendente → pago), distribuições classificadas, NAV com roll-forward, DPI/RVPI/TVPI/IRR, curva J | `src/calc/pe.py`, tela *Fundos PE/VC* | RF-010..012, RF-017, RN-004, RN-005, RN-012 |
| Imóveis diretos: valorização, yield e reavaliação versionada | tela *Imóveis* | RF-006 |
| Benchmarks CDI, IBOV, IPCA, S&P 500 e IFIX, na moeda base | `src/market/benchmarks.py` | RF-018 (dados simulados) |
| Dashboard: patrimônio, variações, alocações, evolução, posições | tela *Visão consolidada* | RF-019..022 |
| Relatórios em PDF, Excel e CSV | `src/relatorio_pdf.py`, `src/relatorio_xlsx.py` | RF-023 |
| Login com 2FA opcional, refresh token e recuperação de senha por e-mail | `src/security.py`, `src/notificacao.py` | RF-001..003, RN-001 |
| RBAC, CRUD de contas e vínculo cliente–colaborador | `src/administracao.py` | RF-024, RF-025, RN-013 |
| Auditoria de ações sensíveis | `src/audit.py` | RF-026 |
| Portabilidade (entrega única, expurgo em 24 h) e exclusão com anonimização | `src/lgpd.py`, tela *Privacidade* | RF-027, RF-028, RN-014, RN-015 |
| Backup diário consistente, com 30 cópias | `src/backup.py` | RNF-008 |
| Docker e execução standalone | `Dockerfile`, `docker-compose.yml`, `run.sh`, `run.bat` | RNF-009 |

Não estão implementados:

- coleta da CVM e do Tesouro Direto;
- coleta real de benchmarks;
- P/VP de imóveis;
- registro do IP na auditoria;
- cifragem de valores financeiros;
- retry e circuit breaker nas coletas.

Os itens da seção 4.2 do TCC (ordens, impostos, passivos, criptoativos e Open Finance) continuam fora do escopo.

---

## Estrutura

```
tcc-consolidacao-carteiras/
├── app.py                    entrada Streamlit: autenticação, navegação, agendador
├── config.py                 parâmetros, domínios, caminhos e data de referência
├── requirements.txt          dependências de execução (requirements-dev.txt: testes)
├── Dockerfile, docker-compose.yml, docker-entrypoint.sh, run.sh, run.bat
├── distribuicao/            instalador para Windows: iniciador, build (construir.ps1), Inno Setup, ícone
├── src/
│   ├── models.py             17 tabelas (MER + importacao_log + token_seguranca)
│   ├── db.py                 SQLite (WAL, FKs ativas), sessão, migrações leves
│   ├── schema.py             especificação declarativa dos templates
│   ├── templates_xlsx.py     gerador dos XLSX
│   ├── ingest.py             validação estrutural e semântica, preview, persistência
│   ├── operacoes.py          lançamentos manuais e consultas da tela de lançamentos
│   ├── administracao.py      CRUD de contas e vínculos
│   ├── security.py           bcrypt, JWT + refresh, TOTP, AES-256-GCM, RBAC, rate limiting
│   ├── audit.py              trilha de auditoria (registro e consulta)
│   ├── lgpd.py               portabilidade e eliminação
│   ├── formatacao.py         formatação numérica pt-BR única
│   ├── relatorio_pdf.py      relatório em PDF (ReportLab)
│   ├── relatorio_xlsx.py     relatório em Excel (openpyxl)
│   ├── notificacao.py        e-mail (SMTP ou caixa de saída local)
│   ├── agendador.py          coleta, backup e expurgo diários (APScheduler)
│   ├── backup.py             backup online do SQLite com rotação
│   ├── calc/                 rentabilidade.py · pe.py · consolidacao.py
│   └── market/               cambio.py · precos.py · benchmarks.py · seed_sintetico.py
├── ui/                       uma tela por módulo, estilo e utilitários
├── scripts/                  seed_demo · gerar_certificado · tarefas_diarias
├── tests/                    261 testes (pytest), inclusive telas via AppTest
└── docs/
    ├── modelos/              os três templates XLSX do capítulo 9, prontos para preencher
    └── guardioes/            checklists de conformidade com a monografia
```

---

## Templates de ingestão

Os três arquivos estão em [`docs/modelos/`](docs/modelos/). Também são gerados em `data/templates/` pela carga demonstrativa e podem ser baixados na tela de importação. Cada um traz:

- uma aba **Instruções**;
- a área de preenchimento, com cabeçalho travado e listas suspensas;
- linhas de exemplo sintéticas.

| Arquivo | Abas de dados | Destinação |
|---|---|---|
| `Template_Ativos_Liquidos.xlsx` | Dados | ações, ETFs, fundos, FIIs, Tesouro Direto (B3, NYSE, NASDAQ, LSE em GBP, EURONEXT) |
| `Template_PE_VC.xlsx` | Fundos, Eventos | PE, VC, RE: cadastro mais capital calls, distribuições e NAV. A coluna `subtipo` (RF-012) é opcional |
| `Template_Imoveis.xlsx` | Dados | imóveis diretos (FIIs vão no template de líquidos) |

A validação separa dois tipos de erro:

- **Erro estrutural:** o arquivo inteiro é rejeitado.
- **Erro semântico:** só a linha afetada fica de fora.

Na RN-004, as chamadas pendentes também contam para o limite do commitment.

---

## Segurança e privacidade

- **Senhas e login:**
  - senhas com bcrypt (cost 12);
  - após 5 falhas, bloqueio de 15 minutos;
  - a mensagem de credenciais inválidas não revela se o e-mail existe (RN-001); o bloqueio, quando ocorre, é informado.
- **Sessão:** access token JWT de 15 minutos e refresh token de 7 dias, rotativo e revogado no logout, na troca de senha, na desativação e na exclusão.
- **Cifragem:**
  - CPF e segredo do 2FA são cifrados com **AES-256-GCM** (chave em `data/.aes256.key` ou em `CARTEIRA_AES_KEY`);
  - o CPF aparece mascarado na tela.
- **Autorização:** verificada nos serviços. Um cliente não consulta, altera nem exclui dados de outro (`tests/test_isolamento.py`).
- **Portabilidade:** o dossiê exige reautenticação, é entregue uma vez e não fica no servidor.
- **Exclusão:** anonimiza toda a trilha de auditoria do titular.
- **Limitações:**
  - IP não registrado;
  - backups de 30 dias ainda contêm dados de titulares excluídos;
  - o rate limiting conta interações do Streamlit.
