"""Worker async : analyse périodique du marché.

Tourne en background (asyncio.create_task) et :
1. Pour chaque paire surveillée, appelle `analyze_symbol` (synchrone, ccxt) dans
   un thread pour ne pas bloquer la boucle async du serveur HTTP.
2. Compare avec l'état précédent → détecte les transitions.
3. Relit les bougies 1m écoulées pour les paires ayant un signal en cours, afin
   de savoir si le TP ou le SL a été touché entre deux cycles.
4. Met à jour le Store + enqueue les mails.
5. Sleep jusqu'au prochain cycle.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone

from analyze import analyze_symbol
from fetch import fetch_ohlcv, get_exchange
from webapp.mailer import Mailer
from webapp.settings import Settings
from webapp.state import Store, Transition

# Fenêtre max relue en bougies 1m pour le suivi TP/SL (~16 h). Au-delà, le
# worker a été interrompu assez longtemps pour que la précision à la minute
# n'apporte plus grand-chose, et on évite de paginer.
_MAX_TRACKING_BARS = 1000

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings: Settings, store: Store, mailer: Mailer) -> None:
        self.settings = settings
        self.store = store
        self.mailer = mailer
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._cycle_lock = asyncio.Lock()

    # ---------- lifecycle ----------
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run_loop(), name="crypto-worker")
            log.info("Worker démarré (intervalle %ds, %d paires)",
                     self.settings.poll_interval, len(self.settings.watched_symbols))

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()

    # ---------- main loop ----------
    async def _run_loop(self) -> None:
        # Premier cycle immédiat (sinon le dashboard reste vide pendant POLL_INTERVAL).
        await self.run_cycle_once()

        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.poll_interval)
                break  # stop demandé pendant le sleep
            except asyncio.TimeoutError:
                pass  # timeout = intervalle écoulé, on enchaîne
            await self.run_cycle_once()

    async def run_cycle_once(self) -> None:
        """Exécute un cycle d'analyse complet. Idempotent et sûr en concurrence."""
        if self._cycle_lock.locked():
            log.warning("Cycle déjà en cours, skip.")
            return

        async with self._cycle_lock:
            t0 = time.time()
            try:
                await asyncio.to_thread(self._cycle_sync)
            except Exception as exc:
                log.exception("Erreur cycle")
                self.store.mark_error(str(exc))
                return
            duration = time.time() - t0
            self.store.mark_cycle_done(duration)
            log.info("Cycle terminé en %.2fs", duration)
            # Tente un flush final (si on a accumulé des transitions pendant le cycle).
            self.mailer.flush()

    # ---------- sync core (exécuté dans un thread) ----------
    def _cycle_sync(self) -> None:
        exchange = get_exchange(self.settings.exchange_id)
        horizons = ["short", "medium", "long"]

        # Filtre les paires qui n'existent pas sur l'exchange.
        valid = [s for s in self.settings.watched_symbols if s in exchange.markets]
        skipped = set(self.settings.watched_symbols) - set(valid)
        for s in skipped:
            log.warning("Symbole %s indisponible sur %s, skip.", s, exchange.id)

        all_transitions = []
        for sym in valid:
            # Avant l'analyse : les prix réellement traités depuis le dernier
            # cycle, pour trancher TP/SL sur les signaux encore en cours.
            bars = self._tracking_bars(exchange, sym)
            try:
                result = analyze_symbol(exchange, sym, horizons)
            except Exception as exc:
                log.warning("Échec analyse %s : %s", sym, exc)
                continue
            transitions, closed = self.store.update_pair(
                sym, result["ticker"], result["signals"], bars
            )
            if transitions:
                log.info("%s : %d transition(s) détectée(s)", sym, len(transitions))
            for tr in closed:
                log.info("%s %s (%s) : %s — %s", sym, tr.horizon, tr.new_action,
                         tr.outcome, _fmt_pnl(tr))
            all_transitions.extend(transitions)

        if all_transitions:
            self.mailer.queue(all_transitions)

    def _tracking_bars(self, exchange, symbol: str) -> list[tuple[datetime, float, float]]:
        """Bougies 1m `(ts, high, low)` écoulées depuis le dernier contrôle.

        Le worker tourne toutes les `POLL_INTERVAL_SECONDS` (300 s par défaut) :
        comparer les niveaux au seul prix instantané raterait toute mèche entre
        deux cycles. On relit donc la minute par minute — mais uniquement pour
        les paires qui ont un signal en cours (sinon : aucun appel réseau).

        Liste vide en cas d'échec : le Store se rabat alors sur le dernier prix
        du ticker, moins fin mais jamais bloquant.
        """
        since = self.store.tracking_since(symbol)
        if since is None:
            return []

        elapsed_s = (datetime.now(timezone.utc) - since).total_seconds()
        # +2 bougies de marge : `since` tombe au milieu d'une minute (donc la
        # fenêtre en chevauche une de plus), et `drop_incomplete=False` garde la
        # minute en cours — sinon on perdrait la fin de fenêtre.
        limit = min(math.ceil(max(elapsed_s, 0) / 60) + 2, _MAX_TRACKING_BARS)
        try:
            df = fetch_ohlcv(exchange, symbol, timeframe="1m", limit=limit,
                             drop_incomplete=False)
        except Exception as exc:
            log.warning("Bougies 1m indisponibles pour %s (%s) — suivi TP/SL "
                        "sur le dernier prix uniquement.", symbol, exc)
            return []

        # On garde les bougies dont la clôture est postérieure au dernier
        # contrôle (celle qui contient `since` incluse : sans elle, la fin de la
        # minute du contrôle précédent ne serait jamais examinée). Les prix
        # antérieurs au signal lui-même sont écartés par Transition.check.
        cutoff = since - timedelta(minutes=1)
        return [
            (ts.to_pydatetime(), float(row["high"]), float(row["low"]))
            for ts, row in df.iterrows()
            if ts > cutoff
        ]


def _fmt_pnl(tr: Transition) -> str:
    if tr.pnl_pct is None:
        return "PnL n/a"
    return f"PnL {tr.pnl_pct * 100:+.2f}%"
