"""Stratégies d'analyse: court / moyen / long terme.

Chaque module expose `analyze(df) -> Signal` où `df` est un OHLCV enrichi par
`indicators.add_all`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Callable, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

Action = Literal["BUY", "SELL", "HOLD"]


@dataclass
class Signal:
    horizon: str                 # "short" | "medium" | "long"
    action: Action               # BUY / SELL / HOLD
    score: float                 # somme des poids signés des indicateurs + events
    confidence: float            # 0.0 ... 1.0 — accord entre indicateurs actifs (|score|/Σ|poids|)
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    reasons: list[str] = field(default_factory=list)
    # Événements de marché détectés (vol-spike, breakout, F&G, etc.). Affichés
    # comme badges dans le dashboard ; certains ont aussi contribué au score.
    events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StrategyMeta:
    """Métadonnées d'une stratégie pour orchestration (analyse + backtest)."""
    analyze: Callable[[pd.DataFrame], Signal]
    max_bars: int                # durée max d'un trade en bougies (backtest)
    warmup: int                  # bougies de warmup minimales pour stabilité
    # Le backtest ne simule pas les SELL : un short multi-mois est irréalisable
    # en spot, et les backtests comparatifs (audit v6) montrent le côté SELL
    # perdant sur toutes les paires testées. Le SELL reste émis en live comme
    # alerte d'allègement.
    long_only: bool = False


def get_strategies() -> dict[str, StrategyMeta]:
    """Source unique de vérité pour les stratégies disponibles.

    Import lazy des sous-modules pour éviter un cycle au chargement du package
    (les sous-modules importent `Signal` depuis ce __init__).
    """
    from strategies import short_term, medium_term, long_term

    # warmup : 200 bougies suffisent à stabiliser RSI/MACD/BB (EWM convergés) ;
    # le long terme garde 500 pour disposer d'une SMA200 fiable.
    return {
        "short":  StrategyMeta(short_term.analyze,  max_bars=24,  warmup=200),
        # max_bars=45 (~7.5 jours) : à 30, ~30% des trades sortaient en TIMEOUT
        # avant d'atteindre le TP à 4×ATR — on coupait les tendances trop tôt.
        "medium": StrategyMeta(medium_term.analyze, max_bars=45,  warmup=200),
        "long":   StrategyMeta(long_term.analyze,   max_bars=90,  warmup=500, long_only=True),
    }
