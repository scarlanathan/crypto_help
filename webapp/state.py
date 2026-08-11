"""Store en mémoire de l'état actuel des paires + détection de transitions.

Une transition est un changement d'action entre deux cycles consécutifs sur le
même (symbole, horizon). On filtre pour ne garder que les transitions
significatives : HOLD ↔ BUY/SELL et BUY ↔ SELL (les flips directs).

Chaque transition BUY/SELL est ensuite **suivie** jusqu'à son dénouement : à
chaque cycle on confronte ses niveaux (SL / TP) aux prix réellement traités
depuis le dernier contrôle, et on la clôture en TP, SL ou TIMEOUT. Mêmes
conventions que le backtest (cf. backtest._simulate_trade) :
- SL prioritaire si SL et TP sont touchés dans la même fenêtre (pessimiste) ;
- TIMEOUT après `max_bars` bougies de l'horizon (6 h en court, ~7,5 j en moyen,
  90 j en long) — le trade est alors sorti au dernier prix connu ;
- une nouvelle transition ne clôture PAS les précédentes : chaque signal est
  suivi indépendamment jusqu'à son propre TP/SL/TIMEOUT.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from config import STRATEGY_TIMEFRAMES, timeframe_seconds
from strategies import get_strategies

# Issues possibles d'une transition suivie.
OPEN = "OPEN"          # en cours — ni le TP ni le SL n'ont été touchés
TP = "TP"              # take profit atteint
SL = "SL"              # stop loss atteint
TIMEOUT = "TIMEOUT"    # durée max de suivi écoulée sans toucher TP ni SL

# Durée max de suivi par horizon = max_bars × durée d'une bougie, c'est-à-dire
# exactement la fenêtre que le backtest accorde à un trade avant de le couper.
_MAX_AGE_S: dict[str, int] = {
    h: meta.max_bars * timeframe_seconds(STRATEGY_TIMEFRAMES[h]["timeframe"])
    for h, meta in get_strategies().items()
    if h in STRATEGY_TIMEFRAMES
}


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
    # Suivi du résultat. `outcome` vaut None pour une transition non suivie
    # (retour à HOLD, ou niveaux absents), sinon OPEN puis TP / SL / TIMEOUT.
    outcome: str | None = None
    outcome_ts: datetime | None = None
    outcome_price: float | None = None
    pnl_pct: float | None = None      # gain/perte du signal, frais non déduits
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        trackable = (
            self.new_action in ("BUY", "SELL")
            and None not in (self.entry, self.stop_loss, self.take_profit)
        )
        if not trackable:
            return
        self.outcome = OPEN
        max_age = _MAX_AGE_S.get(self.horizon)
        if max_age:
            self.expires_at = self.ts + timedelta(seconds=max_age)

    @property
    def is_open(self) -> bool:
        return self.outcome == OPEN

    def _close(self, outcome: str, price: float, ts: datetime) -> None:
        self.outcome = outcome
        self.outcome_price = price
        self.outcome_ts = ts
        if self.entry:
            gross = (price - self.entry) / self.entry
            # Un SELL est une position vendeuse : le gain est inversé.
            self.pnl_pct = round(gross if self.new_action == "BUY" else -gross, 5)

    def check(self, bars: list[tuple[datetime, float, float]],
              last_price: float | None, now: datetime) -> bool:
        """Confronte les niveaux aux bougies `(ts, high, low)` écoulées.

        Retourne True si la transition vient d'être clôturée. Les bougies
        antérieures au signal sont ignorées (un TP touché *avant* l'entrée ne
        compte pas).
        """
        if not self.is_open:
            return False

        is_buy = self.new_action == "BUY"
        for bar_ts, high, low in bars:
            if bar_ts < self.ts:
                continue
            hit_sl = low <= self.stop_loss if is_buy else high >= self.stop_loss
            hit_tp = high >= self.take_profit if is_buy else low <= self.take_profit
            if hit_sl:      # priorité au SL (couvre le cas SL + TP dans la même bougie)
                self._close(SL, self.stop_loss, bar_ts)
                return True
            if hit_tp:
                self._close(TP, self.take_profit, bar_ts)
                return True

        if self.expires_at is not None and now >= self.expires_at and last_price is not None:
            self._close(TIMEOUT, last_price, now)
            return True
        return False

    def to_dict(self) -> dict:
        skip = {"ts", "outcome_ts", "expires_at"}
        return {
            **{k: v for k, v in self.__dict__.items() if k not in skip},
            "ts": self.ts.isoformat(),
            "outcome_ts": self.outcome_ts.isoformat() if self.outcome_ts else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
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
        # Transitions encore en cours, par symbole (mêmes objets que dans
        # recent_transitions : les clôturer met le snapshot à jour).
        self._open: dict[str, list[Transition]] = {}
        # Dernier instant où les prix du symbole ont été confrontés aux niveaux.
        self._last_check: dict[str, datetime] = {}

    def tracking_since(self, symbol: str) -> datetime | None:
        """Instant depuis lequel il faut relire les prix de `symbol`.

        None si aucune transition n'est en cours : inutile d'aller chercher
        l'historique fin d'un symbole que l'on ne suit pas.
        """
        with self._lock:
            if not self._open.get(symbol):
                return None
            return self._last_check.get(symbol)

    def update_pair(
        self,
        symbol: str,
        ticker: dict,
        signals: dict,
        bars: list[tuple[datetime, float, float]] | None = None,
    ) -> tuple[list[Transition], list[Transition]]:
        """Met à jour les données d'une paire.

        `bars` : bougies `(ts, high, low)` traitées depuis le dernier cycle,
        servant à savoir si un TP/SL a été touché entre deux cycles. Si la liste
        est vide, on se rabat sur le dernier prix du ticker (moins fin : une
        mèche entre deux cycles peut passer inaperçue).

        Retourne `(nouvelles_transitions, transitions_clôturées)`.
        """
        transitions: list[Transition] = []
        now = datetime.now(timezone.utc)
        last_price = ticker.get("last")

        with self._lock:
            self._snapshot.pairs[symbol] = {
                "ticker": ticker,
                "signals": {h: s.to_dict() for h, s in signals.items()},
                "updated_at": now.isoformat(),
            }

            # 1. Dénouement des positions déjà ouvertes — avant d'enregistrer les
            #    nouvelles, pour qu'un signal né maintenant ne soit pas confronté
            #    à des prix antérieurs à lui.
            price_bars = bars or ([(now, last_price, last_price)] if last_price is not None else [])
            still_open: list[Transition] = []
            closed: list[Transition] = []
            for tr in self._open.get(symbol, []):
                if tr.check(price_bars, last_price, now):
                    closed.append(tr)
                else:
                    still_open.append(tr)
            self._last_check[symbol] = now

            # 2. Détection des nouvelles transitions.
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

            still_open.extend(t for t in transitions if t.is_open)
            self._open[symbol] = still_open

            # Garder une trace ordonnée (les 50 dernières suffisent à l'affichage).
            if transitions:
                self._snapshot.recent_transitions.extend(transitions)
                # Le rognage ne peut pas faire disparaître un suivi : _open garde
                # ses propres références jusqu'au dénouement.
                self._snapshot.recent_transitions = self._snapshot.recent_transitions[-200:]

        return transitions, closed

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
