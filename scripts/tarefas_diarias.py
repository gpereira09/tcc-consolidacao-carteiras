"""
Executa as tarefas diárias fora da aplicação (alternativa ao APScheduler).

    python scripts/tarefas_diarias.py            # coleta de preços + backup
    python scripts/tarefas_diarias.py coleta     # só coleta (RF-013)
    python scripts/tarefas_diarias.py backup     # só backup (RNF-008)

Exemplo de cron (Linux/macOS), às 19h30 e 22h30 de Brasília:
    30 19 * * *  cd /caminho/carteira && python scripts/tarefas_diarias.py coleta
    30 22 * * *  cd /caminho/carteira && python scripts/tarefas_diarias.py backup
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import agendador
from src.db import criar_schema


def main(argv: list[str]) -> None:
    criar_schema()
    tarefa = argv[1] if len(argv) > 1 else "todas"
    if tarefa in ("coleta", "todas"):
        r = agendador.coletar_precos()
        print(f"Coleta: {r.atualizados}/{r.ativos} ativos atualizados; câmbio {r.cambio}; "
              f"falhas: {', '.join(r.falhas) or 'nenhuma'}")
    if tarefa in ("backup", "todas"):
        print(f"Backup gravado em {agendador.backup_diario()}")


if __name__ == "__main__":
    main(sys.argv)
