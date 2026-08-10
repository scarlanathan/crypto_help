"""Entry point pour lancer le dashboard web :

    python webserver.py

Lit la config depuis .env (cf. .env.example) et démarre :
- un worker async qui analyse le marché toutes les POLL_INTERVAL_SECONDS
- un serveur HTTP FastAPI sur HTTP_HOST:HTTP_PORT
- un mailer qui notifie les transitions HOLD ↔ BUY/SELL via Gmail SMTP
"""

from __future__ import annotations

import sys

# Sur Windows, le stdout par défaut est cp1252 et plante sur les caractères
# Unicode des logs (•, →, etc.). On force UTF-8 si possible (Python 3.7+).
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import os
from pathlib import Path

import uvicorn

from webapp.settings import load


def _warn_if_unreachable_in_container(host: str) -> None:
    """Prévient si on écoute sur la loopback alors qu'on tourne en conteneur.

    Cas piégeux : `.env` contient HTTP_HOST=127.0.0.1 (correct en local) et
    `docker run --env-file .env` écrase le HTTP_HOST=0.0.0.0 de l'image. Le
    service démarre, le healthcheck passe (il teste depuis l'intérieur), mais
    le port publié ne répond jamais. Sans ce message, le diagnostic est long.
    """
    hosted = (
        Path("/.dockerenv").exists()
        or os.environ.get("KUBERNETES_SERVICE_HOST")
        # Render, Railway, Fly, Heroku… imposent le port via $PORT. Ce cas
        # manquait : sur Cloudflare le service a demarre sur 127.0.0.1 sans
        # aucun signal, et la panne ressemblait a un build casse.
        or os.environ.get("PORT")
    )
    if hosted and host in ("127.0.0.1", "localhost", "::1"):
        print(
            f"ATTENTION: HTTP_HOST={host} sur un hebergeur distant -> le service "
            f"sera injoignable de l'exterieur malgre le port publie.\n"
            f"           Corrigez avec HTTP_HOST=0.0.0.0 (docker compose et "
            f"render.yaml le forcent deja).",
            flush=True,
        )


def main() -> None:
    s = load()
    _warn_if_unreachable_in_container(s.http_host)
    print(f"Crypto Analyzer demarre sur http://{s.http_host}:{s.http_port}")
    print(f"  - Intervalle: {s.poll_interval}s | Paires: {len(s.watched_symbols)} | Exchange: {s.exchange_id}")
    mail_status = "dry-run" if s.alert_dry_run else ("active" if s.mail_enabled else "desactive")
    print(f"  - Mail: {mail_status}" + (f" -> {', '.join(s.alert_to)}" if s.alert_to else ""))
    uvicorn.run(
        "webapp.app:app",
        host=s.http_host,
        port=s.http_port,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
