#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -f data/carteira.db ]; then
  echo ">> Base não encontrada: executando a carga demonstrativa..."
  python3 scripts/seed_demo.py
fi
if [ "$1" = "--https" ]; then
  [ -f certs/servidor.crt ] || python3 scripts/gerar_certificado.py
  exec python3 -m streamlit run app.py \
    --server.sslCertFile certs/servidor.crt --server.sslKeyFile certs/servidor.key
fi
exec python3 -m streamlit run app.py
