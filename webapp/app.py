"""FastAPI app : dashboard temps réel + endpoints REST.

- GET /                  → page HTML (static/index.html)
- GET /api/snapshot      → snapshot complet (paires, signaux, transitions)
- GET /api/transitions   → seulement les transitions récentes
- GET /api/stats         → récap cumulé (TP/SL/TIMEOUT en %) sur TOUTES les transitions
- POST /api/refresh      → force un cycle d'analyse maintenant
- GET /api/health        → diagnostic
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from webapp.mailer import Mailer
from webapp.settings import load as load_settings
from webapp.state import Store
from webapp.worker import Worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("webapp")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    store = Store()
    mailer = Mailer(settings)
    worker = Worker(settings, store, mailer)

    app.state.settings = settings
    app.state.store = store
    app.state.mailer = mailer
    app.state.worker = worker

    if not settings.mail_enabled:
        log.warning("Mailer non configuré (SMTP_USER/SMTP_PASSWORD/ALERT_TO manquants) "
                    "— les alertes ne seront pas envoyées. Définir ALERT_DRY_RUN=1 "
                    "pour voir les mails simulés dans les logs.")
    elif settings.alert_dry_run:
        log.warning("ALERT_DRY_RUN=1 : les mails seront loggés, pas envoyés.")

    worker.start()
    try:
        yield
    finally:
        log.info("Arrêt du worker…")
        await worker.stop()


app = FastAPI(title="Crypto Analyzer", lifespan=lifespan)

# Servir les assets statiques sous /static/
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# HEAD en plus de GET : FastAPI n'ajoute pas HEAD automatiquement (contrairement
# aux routes Starlette nues), et les sondes des hebergeurs et des services
# d'uptime l'utilisent couramment — sans quoi elles recoivent un 405.
@app.api_route("/", methods=["GET", "HEAD"])
async def index():
    index_html = STATIC_DIR / "index.html"
    if not index_html.exists():
        raise HTTPException(500, "index.html introuvable")
    return FileResponse(str(index_html))


@app.get("/api/snapshot")
async def snapshot() -> JSONResponse:
    return JSONResponse(app.state.store.to_dict())


@app.get("/api/transitions")
async def transitions(limit: int = 50) -> JSONResponse:
    snap = app.state.store.to_dict()
    return JSONResponse(snap["recent_transitions"][-limit:])


@app.get("/api/stats")
async def stats() -> JSONResponse:
    """Récap des issues (TP / SL / TIMEOUT) en pourcentage.

    Porte sur toutes les transitions détectées depuis le démarrage du worker,
    pas seulement sur les 50 que `/api/transitions` renvoie par défaut.
    """
    return JSONResponse(app.state.store.stats_dict())


@app.post("/api/refresh")
async def refresh() -> dict:
    """Force un cycle d'analyse en arrière-plan."""
    # Une task asyncio sans référence forte peut être ramassée par le GC avant
    # (ou pendant) son exécution — on l'accroche à app.state.
    app.state.refresh_task = asyncio.create_task(app.state.worker.run_cycle_once())
    return {"status": "queued"}


@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health() -> dict:
    s = app.state.settings
    snap = app.state.store.to_dict()
    return {
        "status": "ok",
        "settings": {
            "poll_interval": s.poll_interval,
            "watched_symbols": s.watched_symbols,
            "exchange_id": s.exchange_id,
            "mail_enabled": s.mail_enabled,
            "alert_dry_run": s.alert_dry_run,
            "alert_to": s.alert_to,
        },
        "cycle_count": snap["cycle_count"],
        "error_count": snap["error_count"],
        "last_cycle_ts": snap["last_cycle_ts"],
        "last_cycle_duration_s": snap["last_cycle_duration_s"],
    }
