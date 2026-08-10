"""Helpers internes aux stratégies."""

from __future__ import annotations

from dataclasses import dataclass, field
import pandas as pd

from strategies import Action, Signal

# Poids appliqués aux events selon leur "name". Tous bas (0.05-0.15) pour ne
# pas dominer les indicateurs principaux (0.10-0.40). Un event de direction 0
# (vol_spike, bb_squeeze) n'a pas de poids signé : il est juste affiché.
_EVENT_WEIGHTS: dict[str, float] = {
    "volume_anomaly":            0.10,  # confirme le mouvement de prix
    "breakout_up":               0.15,
    "breakout_down":             0.15,
    "gap":                       0.10,
    "rsi_divergence_bull":       0.15,
    "rsi_divergence_bear":       0.15,
    # Fear & Greed (long terme seulement)
    "fear_greed_extreme_fear":   0.15,
    "fear_greed_fear":           0.08,
    "fear_greed_extreme_greed":  0.15,
    "fear_greed_greed":          0.08,
}


def crossed_above(a: pd.Series, b: pd.Series, lookback: int = 3) -> bool:
    """True si la série `a` a croisé `b` à la hausse dans les `lookback` dernières bougies."""
    ra, rb = a.tail(lookback + 1), b.tail(lookback + 1)
    return bool(((ra.shift(1) < rb.shift(1)) & (ra > rb)).any())


def crossed_below(a: pd.Series, b: pd.Series, lookback: int = 3) -> bool:
    """True si la série `a` a croisé `b` à la baisse dans les `lookback` dernières bougies."""
    ra, rb = a.tail(lookback + 1), b.tail(lookback + 1)
    return bool(((ra.shift(1) > rb.shift(1)) & (ra < rb)).any())


@dataclass
class ScoreBuilder:
    """Accumule des contributions signées et calcule score + confidence corrects.

    - score   = somme des poids signés (borné par la somme des poids absolus)
    - confidence = |score| / max(Σ|poids|, 1.0)  -> 1.0 si tous les indicateurs
                   sont alignés, 0.0 s'ils se neutralisent. Le plancher 1.0 au
                   dénominateur (≈ somme des poids principaux d'une stratégie)
                   évite qu'un indicateur isolé affiche une confiance de 100%.
    """

    contributions: list[tuple[float, str]] = field(default_factory=list)

    def add(self, weight: float, reason: str) -> None:
        """Ajoute une contribution. `weight > 0` = bullish, `weight < 0` = bearish, 0 ignoré."""
        if weight == 0:
            return
        self.contributions.append((weight, reason))

    @property
    def score(self) -> float:
        return sum(w for w, _ in self.contributions)

    @property
    def confidence(self) -> float:
        total_abs = sum(abs(w) for w, _ in self.contributions)
        if total_abs == 0:
            return 0.0
        return min(1.0, abs(self.score) / max(total_abs, 1.0))

    @property
    def reasons(self) -> list[str]:
        # Tri par poids absolu décroissant pour mettre les raisons fortes en premier.
        return [r for _, r in sorted(self.contributions, key=lambda x: -abs(x[0]))]


def add_event_contributions(builder: ScoreBuilder, events: list) -> None:
    """Ajoute au builder les contributions des events qui ont un poids défini.

    Le poids effectif = poids du nom × direction × strength. Un event de direction 0
    est juste loggé visuellement (via Signal.events) sans toucher au score.
    """
    for ev in events:
        base_weight = _EVENT_WEIGHTS.get(ev.name)
        if base_weight is None or ev.direction == 0:
            continue
        contribution = base_weight * ev.direction * ev.strength
        builder.add(contribution, f"[event] {ev.description}")


def build_signal(
    horizon: str,
    builder: ScoreBuilder,
    last_close: float,
    atr_val: float,
    *,
    buy_threshold: float,
    sell_threshold: float,
    stop_atr: float,
    tp_atr: float,
    events: list | None = None,
) -> Signal:
    """Convertit un ScoreBuilder en Signal final avec gestion stop/TP basée sur l'ATR.

    `events` (list[MarketEvent]) : événements de marché détectés, attachés au
    Signal pour affichage dans l'UI. Leur contribution éventuelle au score doit
    déjà avoir été ajoutée au builder par la stratégie appelante.
    """
    score = builder.score
    if score >= buy_threshold:
        action: Action = "BUY"
        stop = last_close - stop_atr * atr_val
        tp = last_close + tp_atr * atr_val
    elif score <= -sell_threshold:
        action = "SELL"
        stop = last_close + stop_atr * atr_val
        tp = last_close - tp_atr * atr_val
    else:
        action = "HOLD"
        stop = tp = None

    return Signal(
        horizon=horizon,
        action=action,
        score=round(score, 3),
        confidence=round(builder.confidence, 3),
        entry=last_close,
        stop_loss=stop,
        take_profit=tp,
        reasons=builder.reasons or ["Pas de signal franc"],
        events=[e.to_dict() for e in (events or [])],
    )
