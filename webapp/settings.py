"""Settings de l'app web — lecture depuis variables d'environnement / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Chargement opportuniste du .env (sans dépendance dure à python-dotenv).
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    return _env(key, "1" if default else "0").lower() in ("1", "true", "yes", "on")


def _env_list(key: str, default: list[str]) -> list[str]:
    raw = _env(key, "")
    if not raw:
        return default
    return [s.strip() for s in raw.split(",") if s.strip()]


def _on_paas() -> bool:
    """Détecte un hébergeur qui impose le port d'écoute via $PORT.

    Convention partagée par Render, Railway, Fly, Heroku, Cloud Run… Sur ces
    plateformes le routeur est en frontal : écouter sur 127.0.0.1 rend le
    service définitivement injoignable, sans la moindre erreur au démarrage.
    """
    return bool(_env("PORT"))


def _is_remote_host() -> bool:
    """Vrai si le process tourne dans un conteneur ou chez un hébergeur."""
    return bool(
        Path("/.dockerenv").exists()
        or os.environ.get("KUBERNETES_SERVICE_HOST")
        or _on_paas()
    )


_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _resolve_http_host() -> tuple[str, str]:
    """Choisit l'interface d'écoute. Retourne (effective, valeur_corrigee).

    Écouter sur la loopback n'a de sens qu'en local : dans un conteneur ou
    derrière le routeur d'un PaaS, la boucle locale est isolée et le service
    est injoignable — tout en démarrant sans la moindre erreur, ce qui rend la
    panne très longue à diagnostiquer.

    Un HTTP_HOST=127.0.0.1 explicite dans cet environnement n'est donc jamais
    une intention : c'est un .env local recopié tel quel dans le dashboard de
    l'hébergeur. On corrige au lieu de démarrer un service mort, et on renvoie
    la valeur écrasée pour que l'appelant puisse le dire clairement.
    """
    requested = _env("HTTP_HOST")
    if not requested:
        return ("0.0.0.0" if _is_remote_host() else "127.0.0.1"), ""
    if requested in _LOOPBACK and _is_remote_host():
        return "0.0.0.0", requested
    return requested, ""


@dataclass(frozen=True)
class Settings:
    # Worker
    poll_interval: int
    watched_symbols: list[str]
    exchange_id: str
    # Mail
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    alert_to: list[str]
    alert_min_interval: int
    alert_dry_run: bool
    # HTTP
    http_host: str
    http_port: int
    # Non vide si http_host a été corrigé automatiquement : contient la valeur
    # d'origine, pour que le démarrage puisse expliquer l'écrasement.
    http_host_overridden_from: str = ""

    @property
    def mail_enabled(self) -> bool:
        return bool(self.smtp_user and self.smtp_password and self.alert_to) or self.alert_dry_run


def load() -> Settings:
    # Import différé pour ne pas créer un cycle si config.py importe quoi que ce soit.
    from config import SYMBOLS, DEFAULT_EXCHANGE

    http_host, overridden_from = _resolve_http_host()

    return Settings(
        poll_interval=_env_int("POLL_INTERVAL_SECONDS", 300),
        watched_symbols=_env_list("WATCHED_SYMBOLS", SYMBOLS),
        exchange_id=_env("EXCHANGE_ID", DEFAULT_EXCHANGE),
        smtp_host=_env("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=_env_int("SMTP_PORT", 587),
        smtp_user=_env("SMTP_USER", ""),
        smtp_password=_env("SMTP_PASSWORD", ""),
        alert_to=_env_list("ALERT_TO", []),
        alert_min_interval=_env_int("ALERT_MIN_INTERVAL_SECONDS", 60),
        alert_dry_run=_env_bool("ALERT_DRY_RUN", False),
        # cf. _resolve_http_host : loopback en local, 0.0.0.0 en hébergement,
        # et correction d'un HTTP_HOST loopback recopié depuis un .env local.
        http_host=http_host,
        http_host_overridden_from=overridden_from,
        # $PORT est imposé par l'hébergeur et n'est pas négociable : il prime
        # sur le défaut 8000, mais reste surchargeable par HTTP_PORT explicite.
        http_port=_env_int("HTTP_PORT", _env_int("PORT", 8000)),
    )
