"""Store en mémoire de l'état actuel des paires + détection de transitions.

Une transition est un changement d'action entre deux cycles consécutifs sur le
même (symbole, horizon). On filtre pour ne garder que les transitions
significatives : HOLD ↔ BUY/SELL et BUY ↔ SELL (les flips directs).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Transition:
    symbol: str
    horizon: str
    previous_action: str
    new_action: str
    score: float
    confidence: float
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    reasons: list[str]
    ts: datetime

    def to_dict(self) -> dict:
        return {
            **{k: v for k, v in self.__dict__.items() if k != "ts"},
            "ts": self.ts.isoformat(),
        }


@dataclass
class Snapshot:
    """Données affichables dans le dashboard."""
    # snapshot["BTC/USDT"] = {"ticker": {...}, "signals": {"short": {...}, ...}}
    pairs: dict = field(default_factory=dict)
    # Dernières transitions détectées (ordre chronologique).
    recent_transitions: list[Transition] = field(default_factory=list)
    last_cycle_ts: datetime | None = None
    last_cycle_duration_s: float | None = None
    cycle_count: int = 0
    error_count: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict:
        return {
            "pairs": self.pairs,
            "recent_transitions": [t.to_dict() for t in self.recent_transitions[-50:]],
            "last_cycle_ts": self.last_cycle_ts.isoformat() if self.last_cycle_ts else None,
            "last_cycle_duration_s": self.last_cycle_duration_s,
            "cycle_count": self.cycle_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
        }


class Store:
    """Thread-safe wrapper autour du Snapshot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = Snapshot()
        # Dernière action observée par (symbole, horizon) — pour détecter les changements.
        self._last_action: dict[tuple[str, str], str] = {}

    def update_pair(self, symbol: str, ticker: dict, signals: dict) -> list[Transition]:
        """Met à jour les données d'une paire et retourne les transitions détectées."""
        transitions: list[Transition] = []
        now = datetime.now(timezone.utc)

        with self._lock:
            self._snapshot.pairs[symbol] = {
                "ticker": ticker,
                "signals": {h: s.to_dict() for h, s in signals.items()},
                "updated_at": now.isoformat(),
            }

            for horizon, sig in signals.items():
                key = (symbol, horizon)
                prev_action = self._last_action.get(key)
                new_action = sig.action

                if prev_action is not None and prev_action != new_action:
                    # Filtre : on garde HOLD↔BUY/SELL et BUY↔SELL (pas les "vide" initial).
                    transitions.append(Transition(
                        symbol=symbol,
                        horizon=horizon,
                        previous_action=prev_action,
                        new_action=new_action,
                        score=sig.score,
                        confidence=sig.confidence,
                        entry=sig.entry,
                        stop_loss=sig.stop_loss,
                        take_profit=sig.take_profit,
                        reasons=list(sig.reasons),
                        ts=now,
                    ))
                self._last_action[key] = new_action

            # Garder une trace ordonnée (les 50 dernières suffisent à l'affichage).
            if transitions:
                self._snapshot.recent_transitions.extend(transitions)
                self._snapshot.recent_transitions = self._snapshot.recent_transitions[-200:]

        return transitions

    def mark_cycle_done(self, duration_s: float) -> None:
        with self._lock:
            self._snapshot.last_cycle_ts = datetime.now(timezone.utc)
            self._snapshot.last_cycle_duration_s = duration_s
            self._snapshot.cycle_count += 1

    def mark_error(self, err: str) -> None:
        with self._lock:
            self._snapshot.error_count += 1
            self._snapshot.last_error = err

    def to_dict(self) -> dict:
        with self._lock:
            return self._snapshot.to_dict()
