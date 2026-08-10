"""Indicateurs techniques utilisés par les stratégies.

Implémentés en pandas/numpy purs : pas de dépendance à TA-Lib (qui demande des
binaires natifs pénibles à installer sous Windows).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    # avg_loss == 0 (aucune baisse depuis le début de la fenêtre EWM) : le RSI
    # vaut 100 par définition (50 si le prix est parfaitement flat), pas NaN —
    # sinon l'indicateur est muet précisément dans les uptrends les plus purs.
    no_loss = avg_loss.eq(0) & avg_gain.notna()
    out = out.mask(no_loss & avg_gain.gt(0), 100.0).mask(no_loss & avg_gain.eq(0), 50.0)
    return out


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return pd.DataFrame({
        "macd": macd_line,
        "signal": signal_line,
        "hist": macd_line - signal_line,
    })


def bollinger_bands(series: pd.Series, period: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    mid = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    return pd.DataFrame({
        "bb_mid": mid,
        "bb_up": mid + n_std * std,
        "bb_low": mid - n_std * std,
    })


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — utile pour calibrer stop-loss et take-profit."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """Enrichit un DataFrame OHLCV avec tous les indicateurs."""
    from config import (
        RSI_PERIOD, EMA_FAST, EMA_SLOW, MACD_SIGNAL,
        BB_PERIOD, BB_STD, ATR_PERIOD, SMA_LONG,
    )
    out = df.copy()
    out["rsi"] = rsi(out["close"], RSI_PERIOD)
    out = out.join(macd(out["close"], EMA_FAST, EMA_SLOW, MACD_SIGNAL))
    out = out.join(bollinger_bands(out["close"], BB_PERIOD, BB_STD))
    out["atr"] = atr(out, ATR_PERIOD)
    out["ema_fast"] = ema(out["close"], EMA_FAST)
    out["ema_slow"] = ema(out["close"], EMA_SLOW)
    out["sma_50"] = sma(out["close"], 50)
    out["sma_200"] = sma(out["close"], SMA_LONG)
    return out
