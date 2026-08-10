"""Stratégie long terme — position trading / DCA sur timeframe 1d.

Paradigme : **cycle macro**. On regarde où on est dans le cycle multi-années
(post-bottom = accumuler, ATH = alléger), pas les fluctuations courtes.

- Régime SMA50/SMA200 : Golden/Death cross récent OU position relative (un seul
  des deux, pour éviter une double pénalité corrélée — le cross implique
  généralement la position)
- Position vs SMA200 (filtre macro indépendant : "bull market" structurel)
- Drawdown vs ATH multi-cycle (~4 ans) → opportunité d'accumulation, MAIS
  seulement si la reprise a commencé (close > SMA50) — pas en chute libre
- RSI lissé 14 jours → momentum macro

Stop = 5×ATR, TP = 10×ATR  → horizon multi-mois.

Stratégie **long-only en backtest** (StrategyMeta.long_only) : un short spot
multi-mois n'existe pas, et le côté SELL était perdant sur toutes les paires
testées (audit v6). Le SELL reste émis en live comme alerte d'allègement.
"""

from __future__ import annotations

import pandas as pd

from events import detect_all
from sentiment import fear_greed_event
from strategies import Signal
from strategies._utils import ScoreBuilder, add_event_contributions, build_signal, crossed_above, crossed_below

CROSS_LOOKBACK = 50          # ~7 semaines pour qualifier le cross de "récent"
ATH_LOOKBACK_DAYS = 1460     # ~4 ans = un cycle BTC complet


def analyze(df: pd.DataFrame) -> Signal:
    last = df.iloc[-1]
    builder = ScoreBuilder()

    # --- Régime SMA50/SMA200 (poids 0.35) -----------------------------------
    # Un seul item parmi 4 cas mutuellement exclusifs : pas de double comptage.
    if pd.notna(last["sma_50"]) and pd.notna(last["sma_200"]):
        cross_up = crossed_above(df["sma_50"], df["sma_200"], lookback=CROSS_LOOKBACK)
        cross_dn = crossed_below(df["sma_50"], df["sma_200"], lookback=CROSS_LOOKBACK)
        if cross_up:
            builder.add(+0.35, f"Golden cross dans les {CROSS_LOOKBACK} dernières bougies")
        elif cross_dn:
            builder.add(-0.35, f"Death cross dans les {CROSS_LOOKBACK} dernières bougies")
        elif last["sma_50"] > last["sma_200"]:
            builder.add(+0.10, "SMA50 > SMA200 (régime haussier établi)")
        else:
            builder.add(-0.10, "SMA50 < SMA200 (régime baissier établi)")

    # --- Position vs SMA200 (poids 0.20) — filtre macro indépendant ---------
    # Réduit (0.30 -> 0.20) car partiellement corrélé au bloc Golden/Death cross.
    if pd.notna(last["sma_200"]):
        ratio = last["close"] / last["sma_200"]
        if ratio > 1.10:
            builder.add(+0.20, f"Prix {((ratio-1)*100):.0f}% au-dessus de la SMA200")
        elif ratio < 0.90:
            builder.add(-0.20, f"Prix {((1-ratio)*100):.0f}% sous la SMA200")
        # Zone [0.90, 1.10] = transition, pas de contribution.

    # --- Drawdown vs ATH multi-cycle (poids 0.25) — opportunité DCA ---------
    # Le bonus n'est actif que si la reprise a commencé (close > SMA50) : acheter
    # -60% pendant la chute libre, c'est le couteau qui tombe. Les backtests
    # comparatifs (audit v6) montrent le gate neutre ou gagnant côté BUY.
    window = df["high"].tail(ATH_LOOKBACK_DAYS)
    if len(window) > 30:
        ath = window.max()
        drawdown = (last["close"] / ath) - 1.0
        recovery = pd.notna(last["sma_50"]) and last["close"] > last["sma_50"]
        if drawdown < -0.60 and recovery:
            builder.add(+0.25, f"Drawdown {drawdown*100:.0f}% vs ATH 4Y + reprise amorcée (prix > SMA50) — zone de capitulation")
        elif drawdown < -0.40 and recovery:
            builder.add(+0.15, f"Drawdown {drawdown*100:.0f}% vs ATH 4Y + reprise amorcée — accumulation DCA")
        elif drawdown > -0.05:
            builder.add(-0.15, f"À {drawdown*100:.1f}% de l'ATH 4Y — prise de bénéfices partielle")

    # --- RSI lissé 14 jours (poids 0.10) ------------------------------------
    rsi_avg = df["rsi"].tail(14).mean()
    if pd.notna(rsi_avg):
        if rsi_avg > 60:
            builder.add(+0.10, f"RSI moyen 14j = {rsi_avg:.1f} (momentum positif)")
        elif rsi_avg < 40:
            builder.add(-0.10, f"RSI moyen 14j = {rsi_avg:.1f} (momentum négatif)")

    # --- Événements de marché (techniques + sentiment F&G) -----------------
    events = detect_all(df, horizon="long")
    fg = fear_greed_event()
    if fg is not None:
        events.append(fg)
    add_event_contributions(builder, events)

    sig = build_signal(
        horizon="long",
        builder=builder,
        last_close=float(last["close"]),
        atr_val=float(last["atr"]),
        buy_threshold=0.30,
        sell_threshold=0.30,
        stop_atr=5.0,
        tp_atr=10.0,
        events=events,
    )
    # Cas par défaut quand aucun indicateur ne contribue (marché en range).
    if not builder.contributions:
        sig.reasons = ["Marché en range, attendre une cassure"]
    return sig
