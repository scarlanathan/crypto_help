"""Détecteurs d'événements de marché purement techniques.

Chaque détecteur prend un DataFrame OHLCV enrichi (via `indicators.add_all`)
et retourne `None` ou un `MarketEvent`. Les détecteurs ne dépendent que des
données déjà chargées — aucun appel API supplémentaire.

Convention sur `direction` :
  +1 = bullish (renforce un BUY, affaiblit un SELL)
  -1 = bearish
   0 = neutre (juste un signal d'attention / volatilité accrue)

Convention sur `strength` ∈ [0, 1] : à quel point l'event est marqué.
Sert à pondérer la contribution dans le ScoreBuilder.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class MarketEvent:
    name: str             # identifiant court ("vol_spike", "breakout_up", …)
    direction: int        # +1 / -1 / 0
    strength: float       # 0..1 (intensité)
    description: str      # texte lisible pour affichage/mail
    emoji: str = ""       # pour l'UI

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "direction": self.direction,
            "strength": round(self.strength, 3),
            "description": self.description,
            "emoji": self.emoji,
        }


# ---------------------------------------------------------------- détecteurs

def detect_volatility_spike(df: pd.DataFrame, k: float = 2.0, window: int = 30) -> MarketEvent | None:
    """ATR courant >= k × moyenne ATR sur `window` bougies → marché agité.

    Neutre directionnellement (juste un warning). Réduit la confidence des
    signaux de tendance car la volatilité brouille les indicateurs.
    """
    if len(df) < window + 1 or "atr" not in df:
        return None
    atr_now = df["atr"].iloc[-1]
    atr_avg = df["atr"].iloc[-window - 1:-1].mean()
    if pd.isna(atr_now) or pd.isna(atr_avg) or atr_avg == 0:
        return None
    ratio = atr_now / atr_avg
    if ratio < k:
        return None
    strength = min(1.0, (ratio - k) / k + 0.5)
    return MarketEvent(
        name="vol_spike",
        direction=0,
        strength=strength,
        description=f"Volatilité {ratio:.1f}× supérieure à la moyenne",
        emoji="🔥",
    )


def detect_volume_anomaly(df: pd.DataFrame, k: float = 2.0, window: int = 30) -> MarketEvent | None:
    """Volume courant >= k × moyenne. Direction = sens du mouvement de prix.

    Volume confirme un mouvement : un BUY avec volume anormal est plus crédible.
    """
    if len(df) < window + 1:
        return None
    vol_now = df["volume"].iloc[-1]
    vol_avg = df["volume"].iloc[-window - 1:-1].mean()
    if pd.isna(vol_now) or pd.isna(vol_avg) or vol_avg == 0:
        return None
    ratio = vol_now / vol_avg
    if ratio < k:
        return None
    # Direction = sens du dernier mouvement de close
    price_change = df["close"].iloc[-1] - df["close"].iloc[-2]
    direction = 1 if price_change > 0 else (-1 if price_change < 0 else 0)
    strength = min(1.0, (ratio - k) / k + 0.4)
    return MarketEvent(
        name="volume_anomaly",
        direction=direction,
        strength=strength,
        description=f"Volume {ratio:.1f}× la moyenne ({'↑' if direction > 0 else '↓' if direction < 0 else '='})",
        emoji="📊",
    )


def detect_bb_squeeze(df: pd.DataFrame, percentile: float = 0.15, lookback: int = 50) -> MarketEvent | None:
    """Largeur des Bollinger Bands dans le bas du percentile → cassure imminente.

    Le squeeze annonce souvent un grand mouvement, mais sa direction est inconnue
    a priori. Direction = 0, sert d'avertissement.
    """
    if len(df) < lookback or "bb_up" not in df or "bb_low" not in df:
        return None
    width = (df["bb_up"] - df["bb_low"]).tail(lookback)
    if width.isna().any() or width.iloc[-1] <= 0:
        return None
    threshold = width.quantile(percentile)
    if width.iloc[-1] > threshold:
        return None
    return MarketEvent(
        name="bb_squeeze",
        direction=0,
        strength=0.6,
        description=f"Bollinger Bands compressées (largeur dans le {int(percentile*100)}e percentile {lookback}b)",
        emoji="🤏",
    )


def detect_range_breakout(df: pd.DataFrame, window: int = 20) -> MarketEvent | None:
    """Close[-1] sort du range [low.tail(window), high.tail(window)] → breakout.

    Direction = haut/bas du breakout. Strong signal de continuation.
    """
    if len(df) < window + 1:
        return None
    recent_high = df["high"].iloc[-window - 1:-1].max()
    recent_low = df["low"].iloc[-window - 1:-1].min()
    close = df["close"].iloc[-1]
    if pd.isna(recent_high) or pd.isna(recent_low):
        return None
    if close > recent_high:
        delta = (close / recent_high - 1)
        return MarketEvent(
            name="breakout_up",
            direction=+1,
            strength=min(1.0, delta * 20),  # 5% de breakout = strength 1.0
            description=f"Breakout au-dessus du range {window}b (+{delta*100:.1f}%)",
            emoji="🚀",
        )
    if close < recent_low:
        delta = (1 - close / recent_low)
        return MarketEvent(
            name="breakout_down",
            direction=-1,
            strength=min(1.0, delta * 20),
            description=f"Breakdown sous le range {window}b (-{delta*100:.1f}%)",
            emoji="📉",
        )
    return None


def detect_gap(df: pd.DataFrame, threshold: float = 0.03) -> MarketEvent | None:
    """Écart open[-1] vs close[-2] > threshold (3% par défaut)."""
    if len(df) < 2:
        return None
    prev_close = df["close"].iloc[-2]
    curr_open = df["open"].iloc[-1]
    if pd.isna(prev_close) or pd.isna(curr_open) or prev_close == 0:
        return None
    gap = (curr_open / prev_close) - 1
    if abs(gap) < threshold:
        return None
    direction = 1 if gap > 0 else -1
    return MarketEvent(
        name="gap",
        direction=direction,
        strength=min(1.0, abs(gap) / (threshold * 3)),
        description=f"Gap {'haussier' if direction > 0 else 'baissier'} de {gap*100:+.1f}% à l'open",
        emoji="⚠️",
    )


def detect_rsi_divergence(df: pd.DataFrame, window: int = 14) -> MarketEvent | None:
    """Divergence prix/RSI sur les `window` dernières bougies.

    - Divergence bullish : prix fait un nouveau low, mais RSI ne fait pas un nouveau low.
    - Divergence bearish : prix fait un nouveau high, mais RSI ne fait pas un nouveau high.
    """
    if len(df) < window * 2 or "rsi" not in df:
        return None
    recent = df.tail(window)
    earlier = df.iloc[-2 * window:-window]

    # Bullish divergence
    if recent["low"].min() < earlier["low"].min() and recent["rsi"].min() > earlier["rsi"].min():
        return MarketEvent(
            name="rsi_divergence_bull",
            direction=+1,
            strength=0.7,
            description=f"Divergence haussière RSI/prix ({window}b)",
            emoji="🔄",
        )
    # Bearish divergence
    if recent["high"].max() > earlier["high"].max() and recent["rsi"].max() < earlier["rsi"].max():
        return MarketEvent(
            name="rsi_divergence_bear",
            direction=-1,
            strength=0.7,
            description=f"Divergence baissière RSI/prix ({window}b)",
            emoji="🔄",
        )
    return None


# ---------------------------------------------------------------- orchestrateurs

def detect_all(df: pd.DataFrame, horizon: str) -> list[MarketEvent]:
    """Retourne tous les events pertinents pour un horizon donné.

    Tous les détecteurs ne sont pas applicables à chaque timeframe : par exemple
    le gap est plus parlant en short, la divergence RSI en moyen/long.
    """
    detectors_by_horizon = {
        "short":  [detect_volume_anomaly, detect_gap, detect_bb_squeeze, detect_range_breakout],
        "medium": [detect_volatility_spike, detect_volume_anomaly, detect_range_breakout,
                   detect_rsi_divergence, detect_bb_squeeze],
        "long":   [detect_volatility_spike, detect_rsi_divergence, detect_range_breakout],
    }
    events = []
    for fn in detectors_by_horizon.get(horizon, []):
        try:
            ev = fn(df)
        except Exception:
            ev = None  # Un détecteur ne doit jamais faire planter une stratégie.
        if ev is not None:
            events.append(ev)
    return events
