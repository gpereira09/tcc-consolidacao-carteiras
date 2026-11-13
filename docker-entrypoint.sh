#!/bin/sh
set -e
if [ ! -f data/carteira.db ]; then
  if [ "${CARTEIRA_SEED_DEMO:-1}" = "1" ]; then
    python scripts/seed_demo.py
  else
    python -c "from src.db import criar_schema; criar_schema()"
  fi
fi
ARGS="--server.address=0.0.0.0 --server.port=8501"
if [ "${CARTEIRA_HTTPS:-0}" = "1" ]; then
  [ -f certs/servidor.crt ] || python scripts/gerar_certificado.py
  ARGS="$ARGS --server.sslCertFile=certs/servidor.crt --server.sslKeyFile=certs/servidor.key"
fi
exec python -m streamlit run app.py $ARGS
