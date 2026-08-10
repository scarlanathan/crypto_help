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

    @property
    def mail_enabled(self) -> bool:
        return bool(self.smtp_user and self.smtp_password and self.alert_to) or self.alert_dry_run


def load() -> Settings:
    # Import différé pour ne pas créer un cycle si config.py importe quoi que ce soit.
    from config import SYMBOLS, DEFAULT_EXCHANGE

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
        http_host=_env("HTTP_HOST", "127.0.0.1"),
        http_port=_env_int("HTTP_PORT", 8000),
    )
