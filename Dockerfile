FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=America/Sao_Paulo

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data /app/certs \
    && chown -R app:app /app
USER app

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request,ssl,os; u=('https' if os.path.exists('/app/certs/servidor.crt') else 'http')+'://localhost:8501/_stcore/health'; urllib.request.urlopen(u, context=ssl._create_unverified_context(), timeout=4)"

ENTRYPOINT ["sh", "docker-entrypoint.sh"]
