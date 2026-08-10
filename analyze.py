"""Script principal — analyse multi-horizon des cryptos suivies.

Usage:
    python analyze.py                       # toutes les paires, 3 horizons
    python analyze.py --symbol BTC/USDT     # une seule paire
    python analyze.py --horizon short       # un seul horizon
    python analyze.py --json                # sortie JSON (pour pipe / API)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

import indicators
from config import DEFAULT_EXCHANGE, SYMBOLS, STRATEGY_TIMEFRAMES
from fetch import fetch_ohlcv, fetch_ticker, get_exchange
from strategies import Signal, get_strategies

STRATEGIES = get_strategies()


def analyze_symbol(exchange, symbol: str, horizons: list[str]) -> dict:
    """Analyse un symbole sur les horizons demandés."""
    ticker = fetch_ticker(exchange, symbol)
    signals: dict[str, Signal] = {}
    for h in horizons:
        cfg = STRATEGY_TIMEFRAMES[h]
        limit = cfg.get("live_limit", cfg["limit"])
        df = fetch_ohlcv(exchange, symbol, timeframe=cfg["timeframe"], limit=limit)
        df = indicators.add_all(df)
        signals[h] = STRATEGIES[h].analyze(df)
    return {"ticker": ticker, "signals": signals}


def _fmt(v, digits=2):
    if v is None:
        return "—"
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.{digits}f}"
        return f"{v:.{max(digits, 4)}f}"
    return str(v)


def render_table(results: dict[str, dict], console: Console) -> None:
    """Tableau récapitulatif (1 ligne par symbole × horizon)."""
    table = Table(title=f"Analyse crypto — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    for col in ("Symbole", "Prix", "Δ24h", "Horizon", "Signal", "Score", "Entry", "Stop", "TP", "Raisons"):
        table.add_column(col, overflow="fold")

    color = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}

    for sym, res in results.items():
        if "error" in res:
            continue
        t = res["ticker"]
        change = t.get("change_24h_pct")
        change_str = f"{change:+.2f}%" if change is not None else "—"
        change_color = "green" if (change is not None and change >= 0) else "red"

        for horizon, sig in res["signals"].items():
            reasons = "\n".join(f"• {r}" for r in sig.reasons[:4])
            table.add_row(
                sym,
                _fmt(t.get("last")),
                f"[{change_color}]{change_str}[/{change_color}]",
                horizon,
                f"[bold {color[sig.action]}]{sig.action}[/bold {color[sig.action]}]",
                f"{sig.score:+.2f}",
                _fmt(sig.entry),
                _fmt(sig.stop_loss),
                _fmt(sig.take_profit),
                reasons,
            )
    console.print(table)


def to_json(results: dict[str, dict]) -> str:
    out = {}
    for sym, res in results.items():
        if "error" in res:
            out[sym] = res
            continue
        out[sym] = {
            "ticker": res["ticker"],
            "signals": {h: s.to_dict() for h, s in res["signals"].items()},
        }
    return json.dumps(out, indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(description="Analyse multi-horizon des cryptos")
    parser.add_argument("--symbol", action="append", help="Symbole(s) à analyser (peut être répété)")
    parser.add_argument("--horizon", choices=["short", "medium", "long"], action="append",
                        help="Horizon(s) à évaluer (peut être répété)")
    parser.add_argument("--exchange", default=DEFAULT_EXCHANGE, help=f"Exchange ccxt (def: {DEFAULT_EXCHANGE})")
    parser.add_argument("--json", action="store_true", help="Sortie JSON brute")
    args = parser.parse_args()

    symbols = args.symbol or SYMBOLS
    horizons = args.horizon or list(STRATEGY_TIMEFRAMES.keys())

    console = Console()
    if not args.json:
        console.print(f"[dim]Connexion à {args.exchange}…[/dim]")
    exchange = get_exchange(args.exchange)

    # Validation préalable : ne tente pas un fetch sur un symbole absent du marché.
    valid_symbols: list[str] = []
    results: dict[str, dict] = {}
    for sym in symbols:
        if sym not in exchange.markets:
            msg = f"Symbole {sym} indisponible sur {exchange.id}"
            if args.json:
                results[sym] = {"error": msg}
            else:
                console.print(f"[yellow]⚠ {msg}[/yellow]")
            continue
        valid_symbols.append(sym)

    if not args.json:
        console.print(
            f"[dim]Exchange actif: {exchange.id} — "
            f"{len(valid_symbols)}/{len(symbols)} paire(s), "
            f"horizons: {', '.join(horizons)}[/dim]\n"
        )

    for sym in valid_symbols:
        try:
            results[sym] = analyze_symbol(exchange, sym, horizons)
        except Exception as exc:
            if args.json:
                results[sym] = {"error": str(exc)}
            else:
                console.print(f"[red]Erreur sur {sym}: {exc}[/red]")

    if args.json:
        print(to_json(results))
    else:
        render_table(results, console)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
