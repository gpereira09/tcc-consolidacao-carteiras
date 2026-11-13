@echo off
cd /d "%~dp0"
if not exist data\carteira.db (
  echo ^>^> Base nao encontrada: executando a carga demonstrativa...
  python scripts\seed_demo.py || exit /b 1
)
if "%1"=="--https" (
  if not exist certs\servidor.crt (
    python scripts\gerar_certificado.py || exit /b 1
  )
  python -m streamlit run app.py --server.sslCertFile certs\servidor.crt --server.sslKeyFile certs\servidor.key
  goto :eof
)
python -m streamlit run app.py
