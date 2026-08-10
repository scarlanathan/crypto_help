"""Configuration centrale du module d'analyse crypto."""

# Exchange utilisé pour les données publiques (pas besoin de clé API).
# Binance offre le plus de paires et la meilleure liquidité — mêmes choix que CryptoViz.
DEFAULT_EXCHANGE = "binance"
FALLBACK_EXCHANGES = ["kraken", "coinbase", "okx"]

# Paires suivies par défaut.
# - /USDT : référence stablecoin (mesure le prix absolu en dollars)
# - /BTC, /ETH : paires cross (mesurent la force d'un token vs BTC/ETH,
#   utile pour repérer les altcoins qui surperforment/sous-performent BTC)
SYMBOLS = [
    # Quote USDT — prix en dollars
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "ADA/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "AVAX/USDT",
    "SUSHI/USDT",
    # Quote BTC — force vs Bitcoin
    "ETH/BTC",
    "BNB/BTC",
    # Quote ETH — force vs Ethereum
    "SOL/ETH",
]

# Mapping stratégie -> timeframe + nombre de bougies à charger.
# - `limit`      : fenêtre backtest — doit dépasser largement le warmup des
#                  stratégies (cf. strategies.get_strategies).
# - `live_limit` : fenêtre pour l'analyse live — juste assez pour le warmup
#                  (200) + lookbacks des détecteurs, ce qui évite la pagination
#                  (2 requêtes au lieu d'1) à chaque cycle du worker.
#                  Long garde 1500 : le drawdown vs ATH a besoin de ~4 ans.
STRATEGY_TIMEFRAMES = {
    "short":  {"timeframe": "15m", "limit": 1500, "live_limit": 450},   # scalping / day trading (~15 jours)
    "medium": {"timeframe": "4h",  "limit": 1000, "live_limit": 450},   # swing trading (~6 mois)
    "long":   {"timeframe": "1d",  "limit": 1500, "live_limit": 1500},  # position / DCA (~4 ans = 1 cycle BTC)
}

# Paramètres des indicateurs
RSI_PERIOD = 14
EMA_FAST = 12
EMA_SLOW = 26
MACD_SIGNAL = 9
BB_PERIOD = 20
BB_STD = 2
ATR_PERIOD = 14
SMA_LONG = 200
