"""Stratégie court terme — mean-reversion sur timeframe 15m.

Paradigme unique : on parie sur le **retour à la moyenne** quand le prix s'éloigne
trop. Les 3 indicateurs sont alignés sur cette logique :

- RSI extrême      → marché surétiré, retournement probable
- Bollinger Bands  → prix qui dépasse 2σ revient statistiquement vers la moyenne
- Croisement MACD  → utilisé comme **confirmation** que le rebond a démarré
  (MACD qui croise à la hausse dans une fenêtre récente après un creux RSI/BB)

Stop = 1.5×ATR, TP = 3×ATR  → R:R = 1:2. Un stop à 1×ATR sur du 15m est dans
le bruit d'une seule bougie ; 1.5×ATR réduit aussi le poids relatif des frais
(~0.3% aller-retour) dans chaque trade.

Filtre de régime (±0.25) : on ne fait du mean-reversion QUE dans le sens de la
tendance SMA200 (acheter les creux d'un uptrend, vendre les rebonds d'un
downtrend). Le backtest montre que fader le régime est le scénario perdant
type (couteau qui tombe). Avec un seuil à 0.70 :
- avec le régime : 2 indicateurs de réversion alignés suffisent (ex 0.4+0.3+0.25)
- contre le régime : il faut les 3 indicateurs alignés (1.0-0.25=0.75)
- un indicateur isolé ne déclenche jamais (0.4+0.25=0.65 < 0.70)
"""

from __future__ import annotations

import pandas as pd

from events import detect_all
from strategies import Signal
from strategies._utils import ScoreBuilder, add_event_contributions, build_signal, crossed_above, crossed_below

LOOKBACK = 3  # fenêtre de détection du croisement MACD (bougies)


def analyze(df: pd.DataFrame) -> Signal:
    last = df.iloc[-1]
    builder = ScoreBuilder()

    # --- RSI extrême (poids 0.40) -------------------------------------------
    if last["rsi"] < 30:
        builder.add(+0.40, f"RSI={last['rsi']:.1f} en survente")
    elif last["rsi"] > 70:
        builder.add(-0.40, f"RSI={last['rsi']:.1f} en surachat")

    # --- Position vs Bollinger Bands (poids 0.30) ---------------------------
    bb_range = last["bb_up"] - last["bb_low"]
    if pd.notna(bb_range) and bb_range > 0:
        bb_pos = (last["close"] - last["bb_low"]) / bb_range
        if bb_pos < 0.15:
            builder.add(+0.30, "Prix collé à la BB inférieure")
        elif bb_pos > 0.85:
            builder.add(-0.30, "Prix collé à la BB supérieure")

    # --- MACD: confirmation du retournement (poids 0.30) --------------------
    # On cherche un croisement RÉCENT dans le sens attendu : haussier en survente,
    # baissier en surachat. Cohérent avec le mean-reversion (le creux est passé).
    macd_up_recent = crossed_above(df["macd"], df["signal"], lookback=LOOKBACK)
    macd_dn_recent = crossed_below(df["macd"], df["signal"], lookback=LOOKBACK)
    if macd_up_recent and last["rsi"] < 50:
        builder.add(+0.30, f"MACD croisement haussier (≤{LOOKBACK} bougies) — rebond confirmé")
    elif macd_dn_recent and last["rsi"] > 50:
        builder.add(-0.30, f"MACD croisement baissier (≤{LOOKBACK} bougies) — repli confirmé")

    # --- Filtre de régime SMA200 (poids 0.25) -------------------------------
    # Mean-revert avec la tendance dominante : un trade contre-régime exige
    # l'alignement des 3 indicateurs de réversion (cf. docstring).
    if pd.notna(last["sma_200"]):
        if last["close"] > last["sma_200"]:
            builder.add(+0.25, "Régime haussier (prix > SMA200) — acheter les creux")
        else:
            builder.add(-0.25, "Régime baissier (prix < SMA200) — vendre les rebonds")

    # --- Événements de marché (bonus / contexte) ----------------------------
    events = detect_all(df, horizon="short")
    add_event_contributions(builder, events)

    return build_signal(
        horizon="short",
        builder=builder,
        last_close=float(last["close"]),
        atr_val=float(last["atr"]),
        buy_threshold=0.70,
        sell_threshold=0.70,
        stop_atr=1.5,
        tp_atr=3.0,
        events=events,
    )
