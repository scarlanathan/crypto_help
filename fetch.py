"""Récupération des cours crypto en live via ccxt.

Reprend l'approche de ../CryptoViz/services/data-collector (même librairie ccxt,
mêmes exchanges) mais en mode synchrone, sans Kafka/Redis : appel direct aux
endpoints publics, retour sous forme de DataFrame pandas.
"""

from __future__ import annotations

import time

import ccxt
import pandas as pd

from config import DEFAULT_EXCHANGE, FALLBACK_EXCHANGES, TIMEFRAME_SECONDS


def _build_exchange(exchange_id: str) -> ccxt.Exchange:
    """Instancie un exchange ccxt avec rate-limit activé."""
    klass = getattr(ccxt, exchange_id)
    return klass({
        "enableRateLimit": True,
        "timeout": 30_000,
    })


def get_exchange(exchange_id: str = DEFAULT_EXCHANGE) -> ccxt.Exchange:
    """Retourne un exchange opérationnel, avec fallback si l'API par défaut échoue."""
    candidates = [exchange_id] + [e for e in FALLBACK_EXCHANGES if e != exchange_id]
    last_err: Exception | None = None
    for cid in candidates:
        try:
            ex = _build_exchange(cid)
            ex.load_markets()
            return ex
        except Exception as exc:
            last_err = exc
            continue
    raise RuntimeError(f"Aucun exchange disponible: {last_err}")


def fetch_ticker(exchange: ccxt.Exchange, symbol: str) -> dict:
    """Récupère le ticker live (prix, volume, variation 24h)."""
    t = exchange.fetch_ticker(symbol)
    return {
        "symbol": symbol,
        "last": t.get("last"),
        "bid": t.get("bid"),
        "ask": t.get("ask"),
        "high_24h": t.get("high"),
        "low_24h": t.get("low"),
        "change_24h_pct": t.get("percentage"),
        "volume_24h": t.get("quoteVolume"),
        "timestamp": t.get("timestamp"),
    }


_TIMEFRAME_MS = {tf: s * 1000 for tf, s in TIMEFRAME_SECONDS.items()}


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str = "1h",
    limit: int = 200,
    drop_incomplete: bool = True,
) -> pd.DataFrame:
    """Récupère l'historique OHLCV.

    - Si `limit` dépasse la limite par requête de l'exchange (ex: 1000 sur Binance),
      paginate automatiquement en utilisant `since` pour remonter dans le temps.
    - Par défaut, retire la dernière bougie si elle n'est pas encore clôturée :
      cette bougie évolue en temps réel et les indicateurs calculés dessus seraient
      instables (un MACD/RSI sur bougie en cours change à chaque tick).
    """
    if not exchange.has.get("fetchOHLCV"):
        raise RuntimeError(f"L'exchange {exchange.id} ne supporte pas fetchOHLCV")
    if timeframe not in _TIMEFRAME_MS:
        raise ValueError(f"Timeframe {timeframe} non reconnu")

    per_request_max = 1000  # plancher sûr (Binance, OKX, Kraken plafonnent à ~1000)
    tf_ms = _TIMEFRAME_MS[timeframe]

    if limit <= per_request_max:
        raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    else:
        # Pagination : on part de maintenant - limit*tf et on remonte par lots.
        now_ms = int(time.time() * 1000)
        since = now_ms - limit * tf_ms
        chunks: list[list] = []
        cursor = since
        while True:
            batch = exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, since=cursor, limit=per_request_max
            )
            if not batch:
                break
            chunks.extend(batch)
            last_ts = batch[-1][0]
            next_cursor = last_ts + tf_ms
            if next_cursor <= cursor or next_cursor >= now_ms or len(batch) < per_request_max:
                break
            cursor = next_cursor
        # Déduplication par timestamp (les bornes de pagination peuvent recouvrir).
        seen: dict[int, list] = {row[0]: row for row in chunks}
        raw = sorted(seen.values(), key=lambda r: r[0])[-limit:]

    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    # Le timestamp brut (int ms) sert au check drop_incomplete avant de l'indexer.
    last_open_ms = int(df["ts"].iloc[-1]) if not df.empty else 0
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    df = df.astype({c: float for c in ("open", "high", "low", "close", "volume")})

    if drop_incomplete and not df.empty:
        if int(time.time() * 1000) - last_open_ms < tf_ms:
            df = df.iloc[:-1]
    return df


def fetch_all_tickers(exchange: ccxt.Exchange, symbols: list[str]) -> pd.DataFrame:
    """Récupère le ticker pour chaque symbole, retourne un DataFrame résumé."""
    rows = []
    for sym in symbols:
        try:
            rows.append(fetch_ticker(exchange, sym))
        except Exception as exc:
            rows.append({"symbol": sym, "error": str(exc)})
        # Respect minimal du rate-limit côté client
        time.sleep(exchange.rateLimit / 1000)
    return pd.DataFrame(rows)
