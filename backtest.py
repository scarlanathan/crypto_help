"""Moteur de backtest walk-forward.

Algorithme :
1. Enrichit le DataFrame OHLCV avec tous les indicateurs UNE SEULE FOIS. Les
   indicateurs étant causaux (pas de look-ahead possible), itérer sur des
   slices `enriched.iloc[:i+1]` est équivalent à recalculer à chaque bougie,
   mais ~100× plus rapide.
2. Pour chaque bougie i ≥ warmup : passe le slice à la stratégie.
3. Si BUY/SELL et pas de position ouverte → simulation d'entrée à l'**open**
   de la bougie i+1 (le signal n'est connu qu'au close de i).
4. Sortie au SL, TP, ou TIMEOUT après `max_bars` bougies.

Conventions :
- Slippage hostile : appliqué à l'entrée ET à la sortie (toujours en défaveur).
- Si SL et TP touchés dans la même bougie : hypothèse pessimiste -> SL en premier.
- Frais : `2*fee` (entrée + sortie), aucun coût de financement modélisé.

Défauts CLI : fee=0.10%, slippage=0.05% (réalistes pour spot retail).
Défauts fonction `backtest()` : fee=0, slippage=0 (test "frictionless" pur).

Usage:
    python backtest.py --symbol BTC/USDT --horizon short
    python backtest.py --symbol BTC/USDT --horizon medium --fee 0.001 --slippage 0.0005
    python backtest.py --symbol ETH/USDT --horizon long  --max-bars 60
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import pandas as pd
from rich.console import Console
from rich.table import Table

import indicators
import sentiment
from config import DEFAULT_EXCHANGE, STRATEGY_TIMEFRAMES
from fetch import fetch_ohlcv, get_exchange
from strategies import Signal, get_strategies

STRATEGIES = get_strategies()


@dataclass
class Trade:
    side: str          # "BUY" | "SELL"
    entry_ts: pd.Timestamp
    entry_px: float
    exit_ts: pd.Timestamp
    exit_px: float
    exit_reason: str   # "TP" | "SL" | "TIMEOUT"
    pnl_pct: float     # net (frais inclus si fournis)


def _apply_slippage(price: float, side: str, is_entry: bool, slippage: float) -> float:
    """Applique le slippage de façon hostile (toujours en défaveur du trader).

    Buying a position (BUY entry, SELL exit) -> paie plus cher  (+slip)
    Selling a position (SELL entry, BUY exit) -> reçoit moins   (-slip)
    """
    is_buying_action = (side == "BUY" and is_entry) or (side == "SELL" and not is_entry)
    factor = 1 + slippage if is_buying_action else 1 - slippage
    return price * factor


def _simulate_trade(
    df: pd.DataFrame,
    signal_idx: int,
    signal: Signal,
    max_bars: int,
    fee: float,
    slippage: float,
) -> Trade | None:
    """Simule un trade — signal généré au close de `signal_idx`.

    Conventions (sans look-ahead) :
    - Le signal est connu seulement APRÈS la clôture de la bougie `signal_idx`,
      donc on entre à l'**open de signal_idx + 1**, avec slippage hostile.
    - Stop/TP comparés aux high/low de chaque bougie suivante. Exécution
      au niveau exact du stop/TP avec slippage hostile ; si la bougie ouvre
      en gap au-delà du stop, le fill est l'open (pas un prix jamais traité).
    - Si SL et TP touchés dans la même bougie : hypothèse pessimiste -> SL.
    - TIMEOUT : sortie au close de la dernière bougie de la fenêtre.
    """
    if signal.stop_loss is None or signal.take_profit is None:
        return None

    entry_idx = signal_idx + 1
    if entry_idx >= len(df):
        return None  # signal sur la toute dernière bougie : pas d'open dispo

    side = signal.action
    entry_open = float(df.iloc[entry_idx]["open"])
    entry_px = _apply_slippage(entry_open, side, is_entry=True, slippage=slippage)
    stop = signal.stop_loss
    tp = signal.take_profit

    end_idx = min(entry_idx + max_bars, len(df) - 1)
    for i in range(entry_idx, end_idx + 1):
        bar = df.iloc[i]
        hit_sl = (side == "BUY" and bar["low"] <= stop) or (side == "SELL" and bar["high"] >= stop)
        hit_tp = (side == "BUY" and bar["high"] >= tp) or (side == "SELL" and bar["low"] <= tp)

        if hit_sl:                  # priorité SL (couvre aussi le cas SL+TP simultanés)
            # Si la bougie ouvre déjà au-delà du stop (gap), le fill réel est
            # l'open de la bougie, pas le niveau du stop.
            bar_open = float(bar["open"])
            raw_exit = min(stop, bar_open) if side == "BUY" else max(stop, bar_open)
            reason = "SL"
        elif hit_tp:
            raw_exit, reason = tp, "TP"
        else:
            continue

        exit_px = _apply_slippage(raw_exit, side, is_entry=False, slippage=slippage)
        gross = (exit_px - entry_px) / entry_px if side == "BUY" else (entry_px - exit_px) / entry_px
        pnl = gross - 2 * fee
        return Trade(side, df.index[entry_idx], entry_px, df.index[i], exit_px, reason, pnl)

    # Sortie en TIMEOUT au close de la dernière bougie de la fenêtre
    last_bar = df.iloc[end_idx]
    exit_px = _apply_slippage(float(last_bar["close"]), side, is_entry=False, slippage=slippage)
    gross = (exit_px - entry_px) / entry_px if side == "BUY" else (entry_px - exit_px) / entry_px
    pnl = gross - 2 * fee
    return Trade(side, df.index[entry_idx], entry_px, df.index[end_idx], exit_px, "TIMEOUT", pnl)


def backtest(
    df_raw: pd.DataFrame,
    horizon: str,
    *,
    fee: float = 0.0,
    slippage: float = 0.0,
    max_bars: int | None = None,
    warmup: int | None = None,
    allow_shorts: bool = False,
) -> list[Trade]:
    """Walk-forward sur le DataFrame brut OHLCV.

    Optimisation : tous les indicateurs étant causaux (pas de look-ahead),
    on les calcule UNE SEULE FOIS sur le DataFrame complet, puis chaque
    itération passe `enriched.iloc[:i+1]` à la stratégie. Mathématiquement
    équivalent à un recalcul, ~100× plus rapide.
    """
    meta = STRATEGIES[horizon]
    max_bars = max_bars or meta.max_bars
    warmup = warmup if warmup is not None else meta.warmup
    strategy_fn = meta.analyze

    enriched = indicators.add_all(df_raw)

    trades: list[Trade] = []
    position_close_idx = -1  # index inclusif du dernier slot occupé par un trade

    # Le Fear & Greed est une donnée live : sans ce flag, le sentiment
    # d'AUJOURD'HUI serait appliqué à chaque bougie historique (look-ahead).
    sentiment.BACKTEST_MODE = True
    try:
        for i in range(warmup, len(df_raw)):
            if i <= position_close_idx:
                continue
            if pd.isna(enriched["atr"].iloc[i]):
                continue

            signal = strategy_fn(enriched.iloc[: i + 1])
            if signal.action == "HOLD":
                continue
            # Stratégie long-only : le SELL est une alerte d'allègement, pas un
            # short exécutable en spot (--allow-shorts pour comparer quand même).
            if signal.action == "SELL" and meta.long_only and not allow_shorts:
                continue

            trade = _simulate_trade(df_raw, i, signal, max_bars, fee, slippage)
            if trade is None:
                continue
            trades.append(trade)
            # get_loc peut renvoyer slice/array si l'index a des doublons (rare en OHLCV
            # mais possible). On force la conversion en int via searchsorted (l'index
            # OHLCV est trié) qui est robuste à ces cas.
            position_close_idx = int(df_raw.index.searchsorted(trade.exit_ts))
    finally:
        sentiment.BACKTEST_MODE = False
    return trades


def metrics(trades: list[Trade]) -> dict:
    if not trades:
        return {"n_trades": 0}

    pnls = pd.Series([t.pnl_pct for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gross_profit = wins.sum()
    gross_loss = -losses.sum()

    # Equity curve à 1 unité de capital, compounding multiplicatif
    equity = (1 + pnls).cumprod()
    drawdown = (equity / equity.cummax() - 1).min()

    return {
        "n_trades": len(trades),
        "win_rate": float((pnls > 0).mean()),
        "avg_pnl_pct": float(pnls.mean()),
        "best_trade_pct": float(pnls.max()),
        "worst_trade_pct": float(pnls.min()),
        "profit_factor": float(gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
        "total_return_pct": float(equity.iloc[-1] - 1),
        "max_drawdown_pct": float(drawdown),
        "exits": pd.Series([t.exit_reason for t in trades]).value_counts().to_dict(),
    }


def main():
    parser = argparse.ArgumentParser(description="Backtest walk-forward d'une stratégie sur un symbole")
    parser.add_argument("--symbol", required=True, help="Ex: BTC/USDT")
    parser.add_argument("--horizon", choices=list(STRATEGY_TIMEFRAMES), required=True)
    parser.add_argument("--exchange", default=DEFAULT_EXCHANGE)
    parser.add_argument("--fee", type=float, default=0.001, help="Frais aller (def: 0.10%%)")
    parser.add_argument("--slippage", type=float, default=0.0005, help="Slippage par côté (def: 0.05%%)")
    parser.add_argument("--max-bars", type=int, default=None, help="Durée max d'un trade (bougies)")
    parser.add_argument("--allow-shorts", action="store_true",
                        help="Simule aussi les SELL des stratégies long-only (short parfait, irréaliste en spot)")
    args = parser.parse_args()

    console = Console()
    cfg = STRATEGY_TIMEFRAMES[args.horizon]
    console.print(f"[dim]Téléchargement {args.symbol} {cfg['timeframe']} (limit={cfg['limit']})…[/dim]")
    exchange = get_exchange(args.exchange)
    if args.symbol not in exchange.markets:
        console.print(f"[red]Symbole {args.symbol} indisponible sur {exchange.id}[/red]")
        return
    df = fetch_ohlcv(exchange, args.symbol, timeframe=cfg["timeframe"], limit=cfg["limit"])

    console.print(f"[dim]{len(df)} bougies — lancement backtest {args.horizon}…[/dim]")
    trades = backtest(df, args.horizon, fee=args.fee, slippage=args.slippage,
                      max_bars=args.max_bars, allow_shorts=args.allow_shorts)
    m = metrics(trades)

    if m["n_trades"] == 0:
        console.print("[yellow]Aucun trade généré (signal toujours HOLD ou warmup insuffisant).[/yellow]")
        return

    table = Table(title=f"Backtest {args.symbol} — {args.horizon} ({cfg['timeframe']})")
    table.add_column("Métrique")
    table.add_column("Valeur", justify="right")
    table.add_row("Trades", str(m["n_trades"]))
    table.add_row("Win rate", f"{m['win_rate']*100:.1f}%")
    table.add_row("PnL moyen", f"{m['avg_pnl_pct']*100:+.2f}%")
    table.add_row("Best trade", f"{m['best_trade_pct']*100:+.2f}%")
    table.add_row("Worst trade", f"{m['worst_trade_pct']*100:+.2f}%")
    pf = m["profit_factor"]
    table.add_row("Profit factor", "∞" if pf == float("inf") else f"{pf:.2f}")
    table.add_row("Retour total (compounding)", f"{m['total_return_pct']*100:+.2f}%")
    table.add_row("Max drawdown", f"{m['max_drawdown_pct']*100:.2f}%")
    table.add_row("Sorties", ", ".join(f"{k}={v}" for k, v in m["exits"].items()))
    console.print(table)


if __name__ == "__main__":
    main()
