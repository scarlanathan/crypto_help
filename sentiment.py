"""Sentiment global du marché crypto — Fear & Greed Index (alternative.me).

API publique gratuite sans clé : https://alternative.me/crypto/fear-and-greed-index/
Endpoint JSON : https://api.alternative.me/fng/

L'index est rafraîchi 1×/jour côté source, donc on cache localement pendant 1 h
pour éviter de spammer l'API à chaque cycle du worker (toutes les 5 min).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from dataclasses import dataclass

from events import MarketEvent

_API_URL = "https://api.alternative.me/fng/?limit=1"
_CACHE_TTL_SECONDS = 3600  # 1 h
_TIMEOUT = 10

# Le F&G est une donnée LIVE (valeur du jour). L'injecter dans des slices
# historiques appliquerait le sentiment d'aujourd'hui à des bougies vieilles de
# plusieurs années (look-ahead). backtest.py met ce flag à True le temps du run.
BACKTEST_MODE = False


@dataclass
class FearGreed:
    value: int               # 0..100
    classification: str      # "Extreme Fear" | "Fear" | "Neutral" | "Greed" | "Extreme Greed"
    fetched_at: float        # epoch seconds

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "classification": self.classification,
            "fetched_at": self.fetched_at,
            "age_seconds": time.time() - self.fetched_at,
        }


class _Cache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: FearGreed | None = None

    def get(self) -> FearGreed | None:
        with self._lock:
            if self._value is None:
                return None
            if time.time() - self._value.fetched_at > _CACHE_TTL_SECONDS:
                return None
            return self._value

    def set(self, value: FearGreed) -> None:
        with self._lock:
            self._value = value


_cache = _Cache()


def get_fear_greed(force_refresh: bool = False) -> FearGreed | None:
    """Retourne le Fear & Greed actuel (cache 1h). None en cas d'échec réseau."""
    if not force_refresh:
        cached = _cache.get()
        if cached is not None:
            return cached
    try:
        with urllib.request.urlopen(_API_URL, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        latest = payload["data"][0]
        fg = FearGreed(
            value=int(latest["value"]),
            classification=str(latest["value_classification"]),
            fetched_at=time.time(),
        )
        _cache.set(fg)
        return fg
    except Exception:
        return None


def fear_greed_event() -> MarketEvent | None:
    """Convertit le F&G courant en MarketEvent prêt à injecter dans une stratégie.

    Seuils (interprétation contrarian classique) :
        ≤ 25  → Extreme Fear : opportunité historique d'achat (direction +1)
        ≤ 40  → Fear : léger biais haussier (direction +1, strength réduite)
        ≥ 75  → Extreme Greed : risque de retournement (direction -1)
        ≥ 60  → Greed : léger biais baissier (direction -1, strength réduite)
        sinon → Neutral : pas d'event
    """
    if BACKTEST_MODE:
        return None
    fg = get_fear_greed()
    if fg is None:
        return None
    if fg.value <= 25:
        return MarketEvent("fear_greed_extreme_fear", direction=+1, strength=1.0,
                           description=f"Fear & Greed = {fg.value} ({fg.classification})", emoji="😱")
    if fg.value <= 40:
        return MarketEvent("fear_greed_fear", direction=+1, strength=0.5,
                           description=f"Fear & Greed = {fg.value} ({fg.classification})", emoji="😨")
    if fg.value >= 75:
        return MarketEvent("fear_greed_extreme_greed", direction=-1, strength=1.0,
                           description=f"Fear & Greed = {fg.value} ({fg.classification})", emoji="🤑")
    if fg.value >= 60:
        return MarketEvent("fear_greed_greed", direction=-1, strength=0.5,
                           description=f"Fear & Greed = {fg.value} ({fg.classification})", emoji="😏")
    return None
