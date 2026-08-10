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


def _report_host_override(overridden_from: str) -> None:
    """Explique la correction d'un HTTP_HOST loopback en hébergement distant.

    Cas piégeux, rencontre deux fois : un `.env` local contient
    HTTP_HOST=127.0.0.1 (correct sur un poste de dev), puis il est recopie tel
    quel dans le dashboard de l'hebergeur ou passe via --env-file, ou il
    ecrase le HTTP_HOST=0.0.0.0 de l'image. Le service demarre, le healthcheck
    interne passe, mais rien n'y accede jamais depuis l'exterieur.

    settings.load() corrige desormais la valeur ; on se contente de le dire,
    pour qu'une config trompeuse reste visible dans les logs.
    """
    if not overridden_from:
        return
    print(
        f"NOTE: HTTP_HOST={overridden_from} ignore -> ecoute forcee sur 0.0.0.0.\n"
        f"      La loopback est isolee en conteneur / derriere un PaaS : le\n"
        f"      service aurait demarre sans erreur en restant injoignable.\n"
        f"      Retirez HTTP_HOST de la config de l'hebergeur pour lever ce message.",
        flush=True,
    )


def _warn_if_port_shadows_platform(port: int) -> None:
    """Signale un HTTP_PORT qui masque le $PORT impose par l'hebergeur.

    Contrairement au cas loopback, ce n'est pas systematiquement fatal (Render
    sait detecter le port reellement ouvert), mais le routeur cible $PORT : un
    ecart donne un 502 sans trace cote application. On avertit sans ecraser,
    car un port choisi peut etre legitime hors PaaS.
    """
    platform_port = os.environ.get("PORT", "")
    if platform_port and platform_port.isdigit() and int(platform_port) != port:
        print(
            f"ATTENTION: HTTP_PORT={port} masque le PORT={platform_port} impose par\n"
            f"           l'hebergeur, dont le routeur pointe vers {platform_port}.\n"
            f"           Retirez HTTP_PORT de la config pour eviter un 502.",
            flush=True,
        )


def main() -> None:
    s = load()
    _report_host_override(s.http_host_overridden_from)
    _warn_if_port_shadows_platform(s.http_port)
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
