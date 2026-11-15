"""
Carga demonstrativa de ponta a ponta.

Executa a jornada completa do sistema em um único comando:
   1. recria o schema;
   2. gera os três templates XLSX;
   3. cria os usuários dos três perfis (RBAC);
   4. coleta câmbio (PTAX, Banco Central) e benchmarks (Banco Central, Yahoo e B3);
   5. importa os três templates preenchidos com os dados de exemplo;
   6. coleta o histórico de preços dos ativos listados (Yahoo Finance);
   7. imprime o resumo consolidado apurado pelo sistema.

As posições, os fundos e os imóveis são os exemplos dos templates; as séries
de mercado são as reais até a DATA DE REFERÊNCIA (padrão:
config.DATA_REFERENCIA_DEMO). Sem internet, ou com `--simulado`, as séries são
geradas por simulação determinística (fonte SIMULADO): com a mesma data, os
números são idênticos em qualquer dia e em qualquer máquina.

Uso:  python scripts/seed_demo.py [--data AAAA-MM-DD] [--simulado]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from sqlalchemy import select

import config
from src import ingest, security
from src.calc import consolidacao
from src.db import SessionLocal, resetar_schema
from src.market import benchmarks, cambio, precos, seed_sintetico
from src.models import Ativo, Carteira, Cliente, Posicao, Usuario
from src.templates_xlsx import gerar_todos

CONTAS = [
    ("investidor@exemplo.com", "Ana Carolina Ribeiro", "Senha123", "CLIENTE"),
    ("assessor@exemplo.com", "Marcos Tavares", "Senha123", "COLABORADOR"),
    ("admin@exemplo.com", "Administrador do Sistema", "Senha123", "ADMIN"),
]


def titulo(texto: str) -> None:
    print(f"\n{'─' * 72}\n{texto}\n{'─' * 72}")


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Carga demonstrativa.")
    parser.add_argument("--data", type=date.fromisoformat,
                        default=config.DATA_REFERENCIA_DEMO,
                        help="data de referência AAAA-MM-DD "
                             f"(padrão {config.DATA_REFERENCIA_DEMO:%Y-%m-%d})")
    parser.add_argument("--simulado", action="store_true",
                        help="não consulta a internet: gera as séries de mercado por simulação")
    return parser.parse_args(argv)


def _carregar_mercado(sessao, inicio: date, hoje: date, simulado: bool) -> bool:
    """Câmbio e benchmarks. Devolve True se as séries tiverem de ser simuladas."""
    moedas = [m for m in config.MOEDAS if m != "BRL"]
    if not simulado:
        coletadas = {m: cambio.atualizar_bcb(sessao, m, inicio, hoje) for m in moedas}
        simulado = not any(coletadas.values())
    if simulado:
        print("    sem acesso às fontes externas: séries geradas por simulação (fonte SIMULADO)")
        n_cambio = seed_sintetico.gerar_cambio(sessao, inicio, hoje)
        n_bmk = seed_sintetico.gerar_benchmarks(sessao, inicio, hoje)
        print(f"    {n_cambio} cotações de câmbio · {n_bmk} pontos de benchmark")
        return True

    for moeda, n in coletadas.items():
        if not n:
            n = seed_sintetico.gerar_cambio(sessao, inicio, hoje, moedas=(moeda,))
        print(f"    câmbio {moeda}/BRL: {n} cotações "
              f"({'PTAX/BCB' if coletadas[moeda] else 'SIMULADO'})")
    for codigo, n in benchmarks.atualizar(sessao, inicio, hoje).items():
        print(f"    benchmark {codigo}: {f'{n} pontos coletados' if n else 'fonte indisponível'}")
    return False


def main(argv: list[str] | None = None) -> int:
    args = _argumentos(argv)
    hoje = args.data
    os.environ["CARTEIRA_DATA_REFERENCIA"] = hoje.isoformat()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    titulo("1/7 · Recriando o schema")
    resetar_schema()
    print(f"    banco: {config.DB_PATH}")

    titulo("2/7 · Gerando templates XLSX")
    caminhos = gerar_todos()
    for c in caminhos:
        print(f"    {c.name}")

    sessao = SessionLocal()

    titulo("3/7 · Criando usuários (RBAC)")
    usuarios: dict[str, Usuario] = {}
    for email, nome, senha, perfil in CONTAS:
        u = security.criar_conta(
            sessao, email=email, nome=nome, senha=senha,
            consentimento_lgpd=True, perfil=perfil,
            cpf="000.000.000-00" if perfil == "CLIENTE" else None,
        )
        usuarios[perfil] = u
        print(f"    {perfil:<12} {email}  (senha: {senha})")

    cliente = sessao.scalar(
        select(Cliente).where(Cliente.usuario_id == usuarios["CLIENTE"].id)
    )
    cliente.colaborador_id = usuarios["COLABORADOR"].id
    carteira = sessao.scalar(select(Carteira).where(Carteira.cliente_id == cliente.id))
    sessao.commit()
    print(f"    carteira: {carteira.nome} ({carteira.id[:8]}…) · moeda base {cliente.moeda_base}")

    titulo("4/7 · Carregando câmbio e benchmarks")
    print(f"    data de referência: {hoje:%d/%m/%Y}")
    simulado = _carregar_mercado(sessao, config.INICIO_SERIES_MERCADO, hoje, args.simulado)

    titulo("5/7 · Importando os três templates")
    total_importado = 0
    for chave, arquivo in (
        ("LIQUIDOS", "Template_Ativos_Liquidos.xlsx"),
        ("PEVC", "Template_PE_VC.xlsx"),
        ("IMOVEIS", "Template_Imoveis.xlsx"),
    ):
        caminho = config.TEMPLATE_DIR / arquivo
        prev = ingest.validar_arquivo(caminho, chave, sessao, carteira.id)

        if prev.abortado:
            print(f"    ✗ {chave}: ABORTADO")
            for erro in prev.erros_estruturais:
                print(f"        {erro}")
            continue

        for erro in prev.erros_semanticos:
            print(f"        ! {erro}")
        for aviso in prev.avisos:
            print(f"        ~ {aviso}")

        log = ingest.importar(sessao, prev, carteira.id, usuarios["CLIENTE"].id)
        total_importado += log.linhas_importadas
        print(f"    ✓ {chave:<9} lidas={log.linhas_lidas:>3}  "
              f"importadas={log.linhas_importadas:>3}  rejeitadas={log.linhas_rejeitadas:>3}")

    titulo("6/7 · Carregando o histórico de preços dos ativos listados")
    listados = sessao.scalars(
        select(Ativo)
        .join(Posicao, Posicao.ativo_id == Ativo.id)
        .where(Posicao.carteira_id == carteira.id, Ativo.possui_ticker_listado.is_(True))
        .distinct()
    ).all()
    total_precos, sem_cotacao = 0, []
    for ativo in listados:
        pos = sessao.scalar(
            select(Posicao).where(Posicao.carteira_id == carteira.id,
                                  Posicao.ativo_id == ativo.id)
        )
        if simulado:
            n = seed_sintetico.gerar_precos_ativo(
                sessao, ativo, pos.preco_medio, pos.data_inicial, hoje
            )
        else:
            n = precos.carregar_historico(sessao, ativo, pos.data_inicial, hoje)
            if not n:
                sem_cotacao.append(ativo.ticker_canonico)
        total_precos += n
    sessao.commit()
    print(f"    {len(listados)} ativos · {total_precos} cotações diárias")
    if sem_cotacao:
        print(f"    sem cotação na fonte externa, mantido o preço do template (RN-007): "
              f"{', '.join(sem_cotacao)}")

    titulo("7/7 · Consolidação apurada pelo sistema")
    snap = consolidacao.snapshot(sessao, carteira.id, hoje)
    moeda = snap.moeda_base
    print(f"    Patrimônio total ....... {moeda} {snap.patrimonio:>16,.2f}")
    print(f"    Custo de aquisição ..... {moeda} {snap.custo:>16,.2f}")
    print(f"    Resultado .............. {moeda} {snap.resultado:>16,.2f}")
    for alerta in snap.alertas:
        print(f"    ! {alerta}")

    print("\n    Alocação por classe:")
    for classe, valor in snap.por("classe_rotulo").items():
        pct = valor / snap.patrimonio * 100 if snap.patrimonio else 0
        print(f"      {classe:<24} {moeda} {valor:>14,.2f}   {pct:>5.1f}%")

    print("\n    Alocação por liquidez:")
    for tipo, valor in snap.por("liquidez").items():
        pct = valor / snap.patrimonio * 100 if snap.patrimonio else 0
        print(f"      {tipo:<24} {moeda} {valor:>14,.2f}   {pct:>5.1f}%")

    print("\n    Alocação geográfica:")
    for pais, valor in snap.por("pais").items():
        pct = valor / snap.patrimonio * 100 if snap.patrimonio else 0
        print(f"      {pais:<24} {moeda} {valor:>14,.2f}   {pct:>5.1f}%")

    rent = consolidacao.rentabilidade(sessao, carteira.id, fim=hoje)
    if rent:
        print(f"\n    Rentabilidade ({rent.inicio:%d/%m/%Y} a {rent.fim:%d/%m/%Y}):")
        print(f"      TWR acumulado ........ {rent.twr * 100:>8.2f}%")
        print(f"      TWR anualizado ....... {rent.twr_anualizado * 100:>8.2f}%")
        mwr = f"{rent.mwr * 100:>8.2f}%" if rent.mwr is not None else "     n/d"
        print(f"      MWR (IRR) ............ {mwr}")
        print(f"      Volatilidade ......... {rent.volatilidade * 100:>8.2f}%")
        print(f"      Max drawdown ......... {rent.max_drawdown * 100:>8.2f}%")
        sh = f"{rent.sharpe:>8.2f}" if rent.sharpe is not None else "     n/d"
        print(f"      Sharpe ............... {sh}")
        print(f"      Aportes .............. {moeda} {rent.aportes:>14,.2f}")

        print("\n    Comparativo com benchmarks (TWR da carteira vs. índice):")
        for c in rent.comparativos:
            marca = "▲" if c.superou else "▼"
            print(f"      {marca} {c.codigo:<7} índice {c.retorno * 100:>8.2f}%   "
                  f"diferença {c.diferenca * 100:>+8.2f} p.p.")

    metricas = consolidacao.metricas_pe(sessao, carteira.id, hoje)
    if metricas:
        print("\n    Fundos ilíquidos (PE/VC/RE):")
        cab = f"      {'Fundo':<28}{'Moeda':<7}{'Paid-in':>14}{'NAV':>14}{'DPI':>7}{'TVPI':>7}{'IRR':>9}"
        print(cab)
        for m in metricas:
            irr_txt = f"{m.irr * 100:>8.2f}%" if m.irr is not None else "     n/d"
            print(f"      {m.nome[:27]:<28}{m.moeda:<7}{m.paid_in:>14,.2f}"
                  f"{m.nav:>14,.2f}{m.dpi:>7.2f}{m.tvpi:>7.2f}{irr_txt}")

    imoveis = consolidacao.resumo_imoveis(sessao, carteira.id)
    if imoveis:
        print("\n    Imóveis diretos:")
        print(f"      {'Imóvel':<34}{'Moeda':<7}{'Mercado':>14}{'Valoriz.':>10}{'Yield':>8}")
        for i in imoveis:
            print(f"      {i['Imóvel'][:33]:<34}{i['Moeda']:<7}"
                  f"{i['Valor de mercado']:>14,.2f}{i['Valorização'] * 100:>9.1f}%"
                  f"{i['Yield líq.'] * 100:>7.1f}%")

    sessao.close()
    titulo("Carga concluída")
    print("    Para ver na interface exatamente estes números, fixe a mesma data:")
    print(f"      CARTEIRA_DATA_REFERENCIA={hoje:%Y-%m-%d} streamlit run app.py")
    print("    Sem a variável, a interface calcula até a data de hoje.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
