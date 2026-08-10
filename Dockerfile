# syntax=docker/dockerfile:1

# =============================================================================
# Dashboard web crypto (FastAPI + worker d'analyse + alertes mail)
#
#   docker build -t crypto-analyzer .
#   docker run --rm -p 8000:8000 --env-file .env crypto-analyzer
#
# Python 3.13 = même version que le venv de dev (3.13.9).
# =============================================================================

# --- Stage 1 : dépendances ---------------------------------------------------
# On installe dans un venv dédié pour pouvoir le copier tel quel dans l'image
# finale, sans traîner le cache pip ni les outils de build.
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt


# --- Stage 2 : runtime -------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Les logs et les mails contiennent des caractères Unicode (•, →, ≤, emojis
    # des events) : on fige l'encodage pour ne pas dépendre de la locale.
    PYTHONIOENCODING=utf-8 \
    PATH="/opt/venv/bin:$PATH" \
    # Dans un conteneur, écouter sur 127.0.0.1 rend le service injoignable
    # depuis l'extérieur : on bind sur toutes les interfaces.
    HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8000

# ccxt et l'API Fear & Greed sont en HTTPS : sans les certificats CA, tous les
# appels échouent. python:*-slim ne les embarque pas par défaut.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Utilisateur non-root : l'app n'écrit rien sur le disque (état en mémoire).
RUN useradd --create-home --uid 10001 appuser

# Code applicatif — copié après les dépendances pour que le layer lourd
# (pip install) reste en cache quand seul le code change.
COPY --chown=appuser:appuser config.py indicators.py fetch.py events.py \
     sentiment.py analyze.py backtest.py webserver.py ./
COPY --chown=appuser:appuser strategies/ ./strategies/
COPY --chown=appuser:appuser webapp/ ./webapp/

USER appuser

EXPOSE 8000

# Le worker tourne en tâche de fond : /api/health répond dès le démarrage et
# expose le compteur de cycles, c'est le bon signal de vivacité.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('HTTP_PORT','8000')+'/api/health', timeout=8).status == 200 else sys.exit(1)"

# Forme exec : python devient PID 1 et reçoit SIGTERM, qu'uvicorn intercepte
# pour un arrêt propre (le worker est stoppé via le lifespan FastAPI).
CMD ["python", "webserver.py"]
