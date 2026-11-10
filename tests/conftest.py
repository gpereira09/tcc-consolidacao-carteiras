"""
Configuração comum dos testes.

O banco, as chaves e os arquivos gerados vão para um diretório temporário,
definido antes de `config` ser importado, para que os testes nunca toquem
em `data/`. A data de referência é fixa, o que torna as validações de data
futura determinísticas.
"""
from __future__ import annotations

import os
import tempfile

_DIR = tempfile.mkdtemp(prefix="carteiras-testes-")
os.environ["CARTEIRA_DATA_DIR"] = _DIR
os.environ["CARTEIRA_DB"] = os.path.join(_DIR, "carteira.db")
os.environ["CARTEIRA_DATA_REFERENCIA"] = "2026-09-22"
