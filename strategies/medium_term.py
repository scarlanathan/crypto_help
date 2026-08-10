"""Stratégie moyen terme — swing trading sur timeframe 4h.

Paradigme : **suivi de tendance**. On entre quand la tendance vient de basculer
ou est confirmée par plusieurs indicateurs alignés.

- Croisement EMA12/EMA26 dans une fenêtre récente (8 bougies = ~32h)
- Histogramme MACD : signe + momentum (croissant/décroissant)
- Pente SMA50 sur 10 bougies (filtre de tendance moyen terme)
- RSI : pénalise les zones euphorie/capitulation extrêmes (overshoot)
- Régime SMA200 (±0.15) : un signal contre la tendance ~33j exige une
  confirmation supplémentaire

Stop = 2×ATR, TP = 4×ATR  → R:R = 1:2.

Seuil d'entrée 0.60 : un croisement récent + histogramme (0.65) suffit si le
régime ne s'y oppose pas ; contre-régime (0.65-0.15=0.50) il faut en plus la
pente SMA50 ou un event aligné.
"""

from __future__ import annotations

import pandas as pd

from events import detect_all
from strategies import Signal
from strategies._utils import ScoreBuilder, add_event_contributions, build_signal, crossed_above, crossed_below

LOOKBACK_CROSS = 8  # fenêtre de détection EMA crossover (bougies 4h = ~32h)


def analyze(df: pd.DataFrame) -> Signal:
    last = df.iloc[-1]
    prev = df.iloc[-2]
    builder = ScoreBuilder()

    # --- Croisement EMA (poids 0.40) ----------------------------------------
    # Croisement récent prime sur la simple position au-dessus / en-dessous.
    if pd.notna(last["ema_fast"]) and pd.notna(last["ema_slow"]):
        if crossed_above(df["ema_fast"], df["ema_slow"], lookback=LOOKBACK_CROSS):
            builder.add(+0.40, f"EMA12 a croisé EMA26 à la hausse (≤{LOOKBACK_CROSS} bougies)")
        elif crossed_below(df["ema_fast"], df["ema_slow"], lookback=LOOKBACK_CROSS):
            builder.add(-0.40, f"EMA12 a croisé EMA26 à la baisse (≤{LOOKBACK_CROSS} bougies)")
        elif last["ema_fast"] > last["ema_slow"]:
            builder.add(+0.15, "EMA12 > EMA26 (tendance haussière établie)")
        else:
            builder.add(-0.15, "EMA12 < EMA26 (tendance baissière établie)")

    # --- Histogramme MACD : signe + momentum (poids 0.25) -------------------
    if pd.notna(last["hist"]) and pd.notna(prev["hist"]):
        if last["hist"] > 0 and last["hist"] > prev["hist"]:
            builder.add(+0.25, "MACD histogramme positif et croissant")
        elif last["hist"] < 0 and last["hist"] < prev["hist"]:
            builder.add(-0.25, "MACD histogramme négatif et décroissant")

    # --- Pente SMA50 (poids 0.20) — filtre tendance --------------------------
    if len(df) >= 10 and pd.notna(df["sma_50"].iloc[-10]):
        slope = last["sma_50"] - df["sma_50"].iloc[-10]
        if slope > 0:
            builder.add(+0.20, "SMA50 en hausse sur 10 bougies")
        else:
            builder.add(-0.20, "SMA50 en baisse sur 10 bougies")

    # --- Filtre RSI overshoot (poids 0.15) ----------------------------------
    if pd.notna(last["rsi"]):
        if last["rsi"] > 75:
            builder.add(-0.15, f"RSI={last['rsi']:.1f} : euphorie, risque de retournement")
        elif last["rsi"] < 25:
            builder.add(+0.15, f"RSI={last['rsi']:.1f} : capitulation, opportunité d'entrée")

    # --- Régime SMA200 (poids 0.15) — ne pas trader contre la marée ----------
    # Sur 4h, la SMA200 ≈ tendance ~33 jours. Un signal de tendance 4h qui va
    # contre ce régime (ex: BUY sous la SMA200) est statistiquement le plus
    # fragile : le biais exige une confirmation supplémentaire dans ce cas.
    if pd.notna(last["sma_200"]):
        if last["close"] > last["sma_200"]:
            builder.add(+0.15, "Prix au-dessus de la SMA200 (régime haussier ~33j)")
        else:
            builder.add(-0.15, "Prix sous la SMA200 (régime baissier ~33j)")

    # --- Événements de marché (bonus / contexte) ----------------------------
    events = detect_all(df, horizon="medium")
    add_event_contributions(builder, events)

    return build_signal(
        horizon="medium",
        builder=builder,
        last_close=float(last["close"]),
        atr_val=float(last["atr"]),
        buy_threshold=0.60,
        sell_threshold=0.60,
        stop_atr=2.0,
        tp_atr=4.0,
        events=events,
    )
