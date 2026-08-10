# Crypto analysis scripts

Analyse technique multi-horizon des cryptomonnaies en live, basée sur `ccxt`. Approche dérivée de `../CryptoViz/services/data-collector` (mêmes exchanges, même librairie) mais en **mode scripts** synchrones — pas de Kafka/Redis. Trois stratégies (court/moyen/long terme), un moteur de backtest walk-forward, une CLI lisible et un dashboard web avec alertes mail.

**Aucune clé API requise** : tout passe par les endpoints publics des exchanges. Les seuls secrets éventuels sont ceux du SMTP si vous activez les alertes mail.

> ⚠️ Outil d'expérimentation technique. **Ce ne sont pas des conseils financiers** — voir [Limitations & disclaimer](#limitations--disclaimer).

---

## Sommaire

1. [Démarrage rapide](#démarrage-rapide)
2. [Installation](#installation)
3. [Utilisation](#utilisation) (dont [référence CLI](#référence-cli))
4. [Stratégies](#stratégies)
5. [Événements spéciaux](#événements-spéciaux)
6. [Modèle de Signal](#modèle-de-signal)
7. [Backtest](#backtest)
8. [Dashboard web + alertes mail](#dashboard-web--alertes-mail) (dont [Docker](#lancement-en-docker) et [variables d'environnement](#variables-denvironnement))
9. [Structure du projet](#structure-du-projet)
10. [Provenance des analyses](#provenance-des-analyses)
11. [Dépannage](#dépannage)
12. [Historique des audits / corrections](#historique-des-audits--corrections)
13. [Exemple d'analyse live](#exemple-danalyse-live)
14. [Limitations & disclaimer](#limitations--disclaimer)

---

## Démarrage rapide

```powershell
git clone https://github.com/scarlanathan/crypto_help.git
cd crypto_help
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python analyze.py --symbol BTC/USDT
```

Trois usages possibles selon ce que vous cherchez :

| Objectif | Commande | Besoin d'un `.env` ? |
|---|---|---|
| Voir les signaux actuels en une fois | `python analyze.py` | Non |
| Évaluer une stratégie sur l'historique | `python backtest.py --symbol BTC/USDT --horizon long` | Non |
| Surveiller en continu + recevoir des mails | `python webserver.py` | Oui (SMTP) |

---

## Installation

### Prérequis

- **Python 3.13** (version utilisée en dev et dans l'image Docker ; les versions plus anciennes ne sont pas testées).
- Un accès réseau sortant en HTTPS vers l'exchange et vers `alternative.me` (Fear & Greed).
- Optionnel : **Docker** si vous préférez lancer le dashboard en conteneur.

### Windows (PowerShell)

```powershell
git clone https://github.com/scarlanathan/crypto_help.git
cd crypto_help
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Linux / macOS

```bash
git clone https://github.com/scarlanathan/crypto_help.git
cd crypto_help
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Dépendances

Toutes les versions sont **pinnées** dans `requirements.txt` (audit v5) pour que le comportement soit reproductible :

| Paquet | Version | Rôle |
|---|---|---|
| `ccxt` | 4.5.54 | Accès unifié aux API publiques des exchanges |
| `pandas` | 3.0.3 | Manipulation des séries OHLCV |
| `numpy` | 2.4.6 | Calcul vectoriel sous-jacent |
| `rich` | 15.0.0 | Rendu des tables en console |
| `fastapi` | 0.136.3 | API + dashboard web |
| `uvicorn[standard]` | 0.48.0 | Serveur ASGI |

Aucune dépendance à TA-Lib : les indicateurs sont réimplémentés en pandas/numpy purs (`indicators.py`).

Aucune clé API requise pour l'analyse : on utilise les **endpoints publics** de l'exchange (par défaut Binance, fallback automatique Kraken/Coinbase/OKX si KO).

## Utilisation

```powershell
# Toutes les paires suivies, 3 horizons (court / moyen / long)
python analyze.py

# Une paire spécifique
python analyze.py --symbol BTC/USDT

# Plusieurs paires : répéter le flag
python analyze.py --symbol BTC/USDT --symbol ETH/USDT

# Un seul horizon (répétable aussi)
python analyze.py --horizon short
python analyze.py --horizon short --horizon medium

# Sortie JSON (pour piper vers jq, dashboard, etc.)
python analyze.py --json > snapshot.json

# Changer d'exchange
python analyze.py --exchange kraken
```

### Référence CLI

**`analyze.py`** — snapshot des signaux courants.

| Flag | Type | Défaut | Description |
|---|---|---|---|
| `--symbol` | str, **répétable** | `SYMBOLS` de `config.py` (12 paires) | Symbole(s) à analyser, ex. `BTC/USDT` |
| `--horizon` | `short` \| `medium` \| `long`, **répétable** | les 3 | Horizon(s) à calculer |
| `--exchange` | str | `binance` | Exchange ccxt (`kraken`, `coinbase`, `okx`, …) |
| `--json` | flag | off | Sortie JSON brute au lieu de la table `rich` |

**`backtest.py`** — évaluation walk-forward d'une stratégie sur une paire.

| Flag | Type | Défaut | Description |
|---|---|---|---|
| `--symbol` | str | **requis** | Paire unique, ex. `BTC/USDT` |
| `--horizon` | `short` \| `medium` \| `long` | **requis** | Stratégie à tester |
| `--exchange` | str | `binance` | Exchange ccxt |
| `--fee` | float | `0.001` (0.10 %) | Frais **par côté** — comptés à l'entrée *et* à la sortie |
| `--slippage` | float | `0.0005` (0.05 %) | Slippage par côté, toujours hostile |
| `--max-bars` | int | défaut de la stratégie | Durée max d'un trade avant sortie `TIMEOUT` |
| `--allow-shorts` | flag | off | Simule aussi les `SELL` en long terme (long-only par défaut depuis l'audit v6) |

> Contrairement à `analyze.py`, `backtest.py` ne prend **qu'une paire et qu'un horizon** par exécution — bouclez en shell pour comparer plusieurs paires.

> **Note Windows** : si la table `rich` plante sur certains caractères Unicode (`•`, `≤`, `⚠`) sur cmd.exe legacy, utilisez `--json` ou Windows Terminal moderne.

## Stratégies

Chaque stratégie applique des règles **pondérées et signées** sur plusieurs indicateurs, puis produit un score ∈ [-1, +1]. Un score >= seuil → `BUY`, ≤ -seuil → `SELL`, sinon `HOLD`.

| Horizon | Timeframe | Paradigme | Indicateurs (poids) | Seuil | Stop / TP |
|---|---|---|---|---|---|
| **Court** *scalp/day* | 15m | **Mean-reversion** : on parie sur le retour à la moyenne, **dans le sens du régime SMA200** | RSI extrême < 30 ou > 70 (**0.40**) + position dans les Bollinger Bands (**0.30**) + MACD croisement confirmation dans fenêtre 3 bougies (**0.30**) + régime SMA200 (**±0.25**) | ±0.70 | 1.5×ATR / 3×ATR |
| **Moyen** *swing* | 4h | **Suivi de tendance** : on entre quand la tendance bascule ou se confirme | Croisement EMA12/EMA26 fenêtre 8 bougies (**0.40**) + histogramme MACD signe + momentum (**0.25**) + pente SMA50 sur 10 bougies (**0.20**) + filtre RSI overshoot (**0.15**) + régime SMA200 (**±0.15**) | ±0.60 | 2×ATR / 4×ATR |
| **Long** *position/DCA* | 1d | **Cycle macro** : on regarde où on est dans le cycle multi-années. **Long-only en backtest** (le SELL = alerte d'allègement, pas un short) | Régime SMA50/200 ± Golden/Death cross fenêtre 50 bougies (**0.35**) + position vs SMA200 hors zone [0.90, 1.10] (**0.20**) + drawdown vs ATH 4Y *si prix > SMA50* (**0.25**) + RSI lissé 14j (**0.10**) | ±0.30 | 5×ATR / 10×ATR |

**Filtre de régime SMA200** (court et moyen terme) : le backtest montre que les trades à contre-régime (acheter sous la SMA200, vendre au-dessus) sont le scénario perdant type — sur 15m, 44 des 46 trades générés sans ce filtre étaient à contre-régime et perdants en moyenne. Le biais ±0.25 (court) / ±0.15 (moyen) combiné aux seuils relevés fait qu'un trade aligné avec le régime exige 2 indicateurs, et un trade contre-régime un alignement quasi total.

Audit 2026-06 (backtests BTC/ETH/SOL, frais 0.10% + slippage 0.05%) :
- **Court** : avant correction, le backtest était impossible (warmup 250 > 200 bougies chargées) ; une fois corrigé, PF 0.39 sur BTC (46 trades, -12.9%). Avec filtre de régime + seuil 0.70 + stop 1.5×ATR : le flux perdant disparaît (BTC/ETH ~1 trade, SOL PF 3.47).
- **Moyen** : PF 0.71-1.14 avant ; avec régime + seuil 0.60 + `max_bars` 30→45 (les TIMEOUT coupaient ~30% des trades avant leur TP) : BTC PF 2.61 (+57%), ETH/SOL ~0.92.
- **Long** : inchangé, PF 1.39-2.00 sur les 3 paires.

### Indicateurs techniques (calculés en pandas/numpy purs, pas de TA-Lib)

| Indicateur | Implémentation |
|---|---|
| **SMA** | Moyenne mobile simple |
| **EMA** | Moyenne mobile exponentielle |
| **RSI** | Wilder (smoothing exponentiel, période 14) |
| **MACD** | EMA12 - EMA26, signal EMA9, histogramme = MACD - signal |
| **Bollinger Bands** | SMA20 ± 2σ |
| **ATR** | Average True Range avec smoothing exponentiel période 14 (utilisé pour calibrer SL/TP) |

### Pourquoi 3 paradigmes différents ?

Un même indicateur n'a pas le même sens selon l'horizon. **RSI < 30** sur du 15m signifie "le marché a chuté trop vite, rebond probable" (mean-reversion). Sur du 1d, ça signifie "tendance baissière confirmée" (momentum). Les paradigmes sont donc **isolés par stratégie** pour éviter qu'un indicateur n'envoie deux signaux opposés à la même seconde.

## Événements spéciaux

En plus des indicateurs principaux, chaque stratégie peut détecter des **événements de marché** qui contribuent au score (avec un poids volontairement plus faible) et apparaissent comme badges visuels.

### Catégories

**A. Événements techniques** (calculés sur OHLCV, aucune dépendance externe, `events.py`) :

| Détecteur | Description | Direction | Horizons |
|---|---|---|---|
| `vol_spike` 🔥 | ATR courant ≥ 2× ATR moyen (30b) | 0 (neutre) | medium, long |
| `volume_anomaly` 📊 | Volume ≥ 2× moyenne, dans le sens du dernier mouvement | ±1 | short, medium |
| `bb_squeeze` 🤏 | Bollinger Bands compressées (bottom percentile 15% sur 50b) | 0 | short, medium |
| `breakout_up` 🚀 / `breakout_down` 📉 | Close sort du range high/low des 20 dernières bougies | ±1 | tous |
| `gap` ⚠️ | Écart open vs close précédent > 3% | ±1 | short |
| `rsi_divergence_bull` 🔄 / `_bear` | Prix fait nouveau low/high mais RSI fait l'inverse (14b) | ±1 | medium, long |

**B. Sentiment global** (API publique [alternative.me/fng](https://alternative.me/crypto/fear-and-greed-index/), `sentiment.py`) :

| Détecteur | Seuil | Direction | Effet |
|---|---|---|---|
| `fear_greed_extreme_fear` 😱 | F&G ≤ 25 | +1 strong | Achat contrarian historiquement payant |
| `fear_greed_fear` 😨 | F&G ≤ 40 | +1 light | Léger biais haussier |
| `fear_greed_greed` 😏 | F&G ≥ 60 | -1 light | Léger biais baissier |
| `fear_greed_extreme_greed` 🤑 | F&G ≥ 75 | -1 strong | Marché euphorique, risque de retournement |

Le F&G est **caché 1h** (l'index ne bouge qu'1× par jour) et appliqué uniquement à l'horizon **long** (cycle macro). Si l'API tombe, l'event est simplement absent — pas de plantage.

**Désactivé en backtest** (`sentiment.BACKTEST_MODE`) : le F&G est la valeur *du jour* ; l'injecter dans des slices historiques appliquerait le sentiment d'aujourd'hui à des bougies vieilles de plusieurs années (look-ahead) — corrigé à l'audit v5.

### Intégration

Les events alimentent le `ScoreBuilder` via `add_event_contributions` avec des poids volontairement faibles pour ne pas dominer les indicateurs principaux :

- Indicateurs : 0.10 à 0.40 par contribution
- Events directionnels : 0.08 à 0.15 par contribution (× direction × strength)
- Events neutres (vol_spike, bb_squeeze) : pas de poids, juste affichage

Effet réel observé : sur BTC long (régime baissier SMA50 < SMA200 = -0.10), le F&G à 25 (Extreme Fear) ajoute +0.15 → score remonte à +0.05 (proche du seuil BUY +0.30), confidence chute à 0.2 (indicateurs en désaccord). Cela traduit fidèlement la situation : "régime de marché baissier MAIS sentiment de capitulation extrême".

### Affichage

Sur le dashboard, chaque cellule signal montre des **badges colorés** sous le score (vert pour bullish, rouge pour bearish, gris pour neutre) avec emoji + nom + tooltip de description complète. Visible aussi dans le JSON `/api/snapshot` sous `signals[horizon].events`.

## Modèle de Signal

```python
@dataclass
class Signal:
    horizon: str                 # "short" | "medium" | "long"
    action: Action               # BUY / SELL / HOLD
    score: float                 # somme des poids signés des indicateurs
    confidence: float            # 0.0 ... 1.0 — accord entre indicateurs actifs
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    reasons: list[str]           # triés par poids absolu décroissant
```

- **`score`** : somme algébrique des contributions des indicateurs. Négatif = bearish, positif = bullish.
- **`confidence`** : `|score| / Σ|poids|`. **1.0** = tous les indicateurs alignés. **0.0** = ils se neutralisent (un BUY +0.30 et un SELL -0.30 → score 0, confidence 0). À distinguer d'un score nul "absence de signal" (où aucun indicateur n'a contribué).
- **`stop_loss` / `take_profit`** : multiples de l'ATR (volatilité réelle de la paire), pas un % arbitraire.
- **`reasons`** : trace lisible des indicateurs qui ont contribué et avec quel poids.

## Backtest

```powershell
python backtest.py --symbol BTC/USDT --horizon medium
python backtest.py --symbol ETH/USDT --horizon long --fee 0.001 --slippage 0.0005
python backtest.py --symbol SOL/USDT --horizon short --max-bars 30
```

### Algorithme (walk-forward strict, sans look-ahead)

1. Enrichit le DataFrame OHLCV avec tous les indicateurs **une seule fois** (les indicateurs sont causaux, le résultat est identique au recalcul à chaque bougie mais ~100× plus rapide).
2. Pour chaque bougie `i ≥ warmup` : passe `enriched.iloc[:i+1]` à la stratégie.
3. Si signal `BUY`/`SELL` et pas de position ouverte : **entrée à l'open de la bougie `i+1`** (le signal n'est connu qu'après la clôture de `i`).
4. Sortie au SL, au TP, ou TIMEOUT après `max_bars` bougies.

### Conventions

- **Slippage hostile** : appliqué à l'entrée *et* à la sortie, toujours en défaveur du trader (achat plus cher, vente moins cher).
- **SL et TP touchés dans la même bougie** → hypothèse pessimiste : on assume SL touché en premier.
- **Frais** : `2 × fee` par trade (entrée + sortie). Pas de coût de financement modélisé.
- **Défauts CLI** : `--fee 0.001` (0.10%), `--slippage 0.0005` (0.05%) — réalistes pour spot retail.
- **Défauts fonction `backtest()`** : `fee=0`, `slippage=0` — test "frictionless" pur.

### Warmup adaptatif par horizon

| Horizon | Warmup | Raison |
|---|---|---|
| short  | 250 bougies | Stabilise SMA200 + indicateurs |
| medium | 250 bougies | idem |
| long   | 500 bougies | Stabilise + l'ATH 4Y a besoin de plus d'historique |

### Pagination automatique

`fetch_ohlcv(limit=1500)` sur du daily Binance : Binance plafonne à 1000 bougies par requête, donc `fetch.py` paginate automatiquement via le paramètre `since`. Cela permet d'avoir réellement 4 ans d'historique daily pour la stratégie long terme (vs ~2.7 ans avant la correction).

### Métriques rapportées

- Nombre de trades, win rate, PnL moyen
- Best / worst trade
- Profit factor = `Σ gains / Σ pertes` (∞ si que des gains)
- Retour total compounding
- Max drawdown
- Répartition des sorties (SL / TP / TIMEOUT)

## Dashboard web + alertes mail

Une app FastAPI sert un dashboard temps réel avec :

- **Tableau live** des 12 paires × 3 horizons (prix, Δ 24h, signal coloré, score, confidence visuelle, raisons).
- **Liste des transitions** récentes (changements `HOLD ↔ BUY/SELL`).
- **Worker en background** qui analyse le marché toutes les `POLL_INTERVAL_SECONDS` (défaut 5 min).
- **Alertes mail Gmail SMTP** dès qu'une transition est détectée (anti-spam : batching configurable).

### Configuration (`.env`)

```powershell
# Windows — crée ton .env à partir du template
Copy-Item .env.example .env
notepad .env
```

```bash
# Linux / macOS
cp .env.example .env
```

Le `.env` est **exclu du dépôt** par `.gitignore` et du contexte de build par `.dockerignore` — il ne doit jamais être commité. Seul `.env.example` est versionné.

Le chargement est fait par `webapp/settings.py` sans dépendance à `python-dotenv`, via `os.environ.setdefault` : **une variable déjà présente dans l'environnement a la priorité sur le `.env`**.

#### Variables d'environnement

| Variable | Défaut (code) | Description |
|---|---|---|
| `POLL_INTERVAL_SECONDS` | `300` | Intervalle entre deux cycles d'analyse. La stratégie la plus courte étant en 15m, descendre sous 60 s est inutile. |
| `WATCHED_SYMBOLS` | `SYMBOLS` de `config.py` | Paires à suivre, séparées par des virgules. Vide → valeurs de `config.py`. |
| `EXCHANGE_ID` | `binance` | Exchange ccxt de départ (fallback automatique si indisponible). |
| `SMTP_HOST` | `smtp.gmail.com` | Serveur SMTP d'envoi. |
| `SMTP_PORT` | `587` | Port SMTP (STARTTLS). |
| `SMTP_USER` | *(vide)* | Adresse expéditrice. |
| `SMTP_PASSWORD` | *(vide)* | **App password** Gmail de 16 caractères — pas le mot de passe du compte. À générer sur https://myaccount.google.com/apppasswords après activation de la validation en 2 étapes. |
| `ALERT_TO` | *(vide)* | Destinataire(s) des alertes, séparés par des virgules. |
| `ALERT_MIN_INTERVAL_SECONDS` | `60` | Anti-spam : délai minimum entre 2 mails ; les transitions arrivant dans la fenêtre sont regroupées. |
| `ALERT_DRY_RUN` | `0` | `1` → les mails sont loggés au lieu d'être envoyés. |
| `HTTP_HOST` | `127.0.0.1` | Interface d'écoute. **`0.0.0.0` obligatoire en Docker** (voir plus bas). |
| `HTTP_PORT` | `8000` | Port d'écoute. |

Les alertes ne sont actives que si `SMTP_USER`, `SMTP_PASSWORD` et `ALERT_TO` sont tous renseignés — ou si `ALERT_DRY_RUN=1`. Sinon le dashboard fonctionne normalement, simplement sans mail.

### Lancement

```powershell
.\venv\Scripts\Activate.ps1
python webserver.py
```

Le dashboard est servi sur **http://127.0.0.1:8000** (ou `HTTP_PORT` personnalisé). Le premier cycle s'exécute immédiatement, les suivants au rythme de `POLL_INTERVAL_SECONDS`.

### Lancement en Docker

```powershell
docker compose up -d --build
docker compose logs -f
```

Dashboard sur **http://127.0.0.1:8000**. Pour arrêter : `docker compose down`.

Sans compose :

```powershell
docker build -t crypto-analyzer .
docker run -d --name crypto-analyzer -p 127.0.0.1:8000:8000 `
  --env-file .env -e HTTP_HOST=0.0.0.0 crypto-analyzer
```

> ⚠️ **Le `-e HTTP_HOST=0.0.0.0` n'est pas optionnel** avec `--env-file`. Le `.env` contient `HTTP_HOST=127.0.0.1` (correct pour un lancement local), et `--env-file` écrase la valeur de l'image. Le conteneur démarre alors, le healthcheck **passe** (il teste depuis l'intérieur), mais le port publié ne répond jamais — panne particulièrement pénible à diagnostiquer. `docker compose` force déjà la bonne valeur via sa section `environment` (prioritaire sur `env_file`), et `webserver.py` affiche un avertissement explicite au démarrage si le cas se produit quand même.

**Caractéristiques de l'image** :

| Aspect | Choix |
|---|---|
| Base | `python:3.13-slim` (même version que le venv de dev) |
| Build | Multi-stage : les deps sont installées dans un venv puis copiées — pas de cache pip dans l'image finale |
| Taille | ~600 Mo (pandas + numpy + ccxt + cryptography ; incompressible sans retirer des deps) |
| Sécurité | Tourne en **non-root** (`appuser`, uid 10001) ; le `.env` n'est **jamais** copié dans l'image (cf. `.dockerignore`) |
| Certificats | `ca-certificates` installé — sans lui, tous les appels HTTPS (ccxt, Fear & Greed) échouent sur `slim` |
| Healthcheck | `GET /api/health` toutes les 30 s |
| Arrêt | SIGTERM → arrêt propre du worker via le lifespan FastAPI (mesuré : ~1 s) |

L'état (paires, transitions) vit **en mémoire** : aucun volume n'est nécessaire, mais un redémarrage repart d'un snapshot vide et le premier cycle ne produit aucune transition (rien à comparer).

**Vérifié au build du 2026-08-07** : image construite, conteneur démarré, premier cycle en 6.2 s sur 2 paires, 0 erreur, healthcheck `healthy`, signaux identiques au run local.

### Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Dashboard HTML auto-rafraîchi toutes les 5 s |
| `GET /api/snapshot` | État courant complet en JSON (paires + transitions) |
| `GET /api/transitions?limit=50` | N dernières transitions |
| `POST /api/refresh` | Force un cycle d'analyse maintenant |
| `GET /api/health` | Diagnostic (settings, nb de cycles, dernière erreur) |

### Logique d'alerte mail

- Une transition est détectée quand le signal d'un couple `(symbole, horizon)` **change** entre deux cycles (ex: BTC long `HOLD → BUY`).
- Les transitions du même cycle sont **regroupées dans un seul mail** (un tableau HTML avec toutes les paires/horizons concernés).
- Si plusieurs transitions arrivent dans une fenêtre de `ALERT_MIN_INTERVAL_SECONDS` (défaut 60 s), elles sont **mises en file** et envoyées au prochain flush — évite le spam si le marché bouge vite.
- En cas d'échec SMTP (timeout, auth), les transitions sont **réenfilées** pour le prochain cycle.
- `ALERT_DRY_RUN=1` : les mails sont **loggés** au lieu d'être envoyés (utile pour tester la config).

### Test rapide en dry-run

```powershell
$env:ALERT_DRY_RUN="1"; python webserver.py
```

Puis dans un autre terminal :

```powershell
# Force un cycle pour peupler le state initial
curl -X POST http://127.0.0.1:8000/api/refresh
# Voir l'état actuel
curl http://127.0.0.1:8000/api/snapshot
```

Les transitions n'apparaissent qu'à partir du **2e cycle** (au 1er, il n'y a pas d'état précédent à comparer).

## Structure du projet

```
crypto_help/
├── config.py                  # paires suivies, timeframes, paramètres des indicateurs
├── fetch.py                   # accès live ccxt (fallback exchange + pagination + drop bougie en cours)
├── indicators.py              # RSI, MACD, EMA/SMA, Bollinger Bands, ATR (pandas pur)
├── strategies/
│   ├── __init__.py            # Signal, StrategyMeta, get_strategies() (source unique)
│   ├── _utils.py              # ScoreBuilder, crossed_above/below, build_signal
│   ├── short_term.py          # mean-reversion 15m
│   ├── medium_term.py         # suivi de tendance 4h
│   └── long_term.py           # cycle macro 1d
├── analyze.py                 # CLI principal (table rich + sortie JSON)
├── backtest.py                # walk-forward + métriques
├── events.py                  # détecteurs d'événements techniques (vol-spike, breakout, …)
├── sentiment.py               # Fear & Greed Index (alternative.me, cache 1h)
├── webapp/
│   ├── settings.py            # config (lecture .env)
│   ├── state.py               # Store thread-safe + détection de transitions
│   ├── mailer.py              # Gmail SMTP + templates HTML + anti-spam batching
│   ├── worker.py              # boucle asyncio d'analyse périodique
│   ├── app.py                 # FastAPI : endpoints REST + lifespan
│   └── static/index.html      # dashboard HTML/CSS/JS auto-refresh
├── webserver.py               # entry point uvicorn
├── Dockerfile                 # image du dashboard (multi-stage, non-root)
├── docker-compose.yml         # lancement recommandé (gère le .env + HTTP_HOST)
├── .dockerignore              # exclut venv/ et .env du contexte de build
├── .gitignore                 # exclut venv/, __pycache__/ et .env du dépôt
├── .env.example               # template de configuration (le .env réel n'est pas versionné)
├── requirements.txt           # dépendances pinnées
└── README.md
```

**Sens de lecture conseillé** si vous découvrez le code : `config.py` (ce qui est suivi) → `indicators.py` (les briques de calcul) → `strategies/_utils.py` (le `ScoreBuilder` qui agrège) → une stratégie concrète (`strategies/long_term.py` est la plus lisible) → `backtest.py` (comment tout est évalué).

## Provenance des analyses

Les signaux **ne viennent d'aucune IA, d'aucun service tiers d'analyse**. Tout est du calcul technique déterministe local, dont la chaîne est :

1. **Données brutes** via `ccxt` (endpoints publics, mêmes exchanges que CryptoViz).
2. **Indicateurs** calculés localement avec formules standards (`indicators.py`).
3. **Décision** = somme pondérée et signée d'un petit nombre de règles déterministes (`strategies/*.py`).

Le code des règles est lisible en quelques minutes — pas de boîte noire. Pour comprendre un signal, lire `sig.reasons` (la liste triée des indicateurs qui ont contribué).

**Ce que les stratégies ignorent volontairement** : news, on-chain, funding rate, open interest, carnet d'ordres, macro. Outil essentiellement chartiste — la **seule** entrée non-chartiste est le Fear & Greed Index, appliqué au seul horizon long et avec un poids faible (voir [Événements spéciaux](#événements-spéciaux)).

## Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| `RuntimeError: Aucun exchange disponible: …` | Binance **et** les 3 fallbacks (Kraken/Coinbase/OKX) ont échoué : réseau coupé, proxy, géo-restriction | Vérifier la connectivité HTTPS sortante ; forcer un exchange atteignable avec `--exchange kraken` ou `EXCHANGE_ID=kraken`. |
| `RuntimeError: L'exchange X ne supporte pas fetchOHLCV` | Exchange choisi sans endpoint OHLCV public | Repasser sur `binance` / `kraken`. |
| `ValueError: Timeframe X non reconnu` | Timeframe absent du mapping de `fetch.py` | Utiliser les timeframes de `STRATEGY_TIMEFRAMES` (`15m`, `4h`, `1d`). |
| Erreur de symbole sur une paire | La paire n'existe pas sur cet exchange (les cross type `SOL/ETH` ne sont pas cotés partout) | Choisir un exchange qui la cote, ou retirer la paire de `WATCHED_SYMBOLS`. |
| Table `rich` illisible / `UnicodeEncodeError` | Console Windows legacy (cmd.exe) | Utiliser Windows Terminal, ou `--json`. |
| `Aucun trade généré (signal toujours HOLD ou warmup insuffisant)` | Soit l'historique est trop court (warmup 250, ou 500 en long), soit aucun signal n'a franchi le seuil | Vérifier que l'exchange fournit assez d'historique sur ce timeframe — les paires récentes n'ont pas 4 ans de daily. En **court terme**, 0 trade est le comportement **attendu** : le filtre de régime SMA200 + seuil 0.70 rejettent la quasi-totalité des signaux (cf. audit v6). |
| Aucun mail reçu | `SMTP_USER`/`SMTP_PASSWORD`/`ALERT_TO` incomplets, ou `ALERT_DRY_RUN=1` | Vérifier `GET /api/health` ; tester d'abord en `ALERT_DRY_RUN=1` et lire les logs. |
| SMTP `535 Authentication failed` | Mot de passe de compte utilisé au lieu d'un App password | Générer un App password Gmail (16 caractères, 2FA requise). |
| Aucune transition au 1er cycle | Comportement **attendu** | Une transition est un *changement* entre deux cycles ; il faut au moins 2 cycles. |
| Conteneur `healthy` mais port publié muet | `HTTP_HOST=127.0.0.1` hérité du `.env` | Forcer `-e HTTP_HOST=0.0.0.0` (cf. [Docker](#lancement-en-docker)) ou utiliser `docker compose`. |

Le point de diagnostic le plus utile est `GET /api/health` : il expose les settings effectifs, le nombre de cycles effectués et la dernière erreur rencontrée par le worker.

## Historique des audits / corrections

Le code a été développé en 6 itérations audit → correction. Trace des décisions :

### Audit v1 — issues structurelles initiales

- **Logique court terme contradictoire** : mélangeait RSI extrême (mean-reversion) et croisement MACD (trend-following) qui s'annulaient.
- **Détection de croisement sur 2 bougies seulement** : `prev` vs `last` → signal raté dès qu'on lance le script 3 bougies après l'événement.
- **Dernière bougie incluse dans le calcul des indicateurs** : RSI/MACD instables sur la bougie en cours.
- **`confidence = abs(score)`** : un signal solo à -0.4 donnait confidence 0.4 sans aucune confirmation.

→ Corrigés : paradigmes isolés par horizon, helpers `crossed_above/below` avec fenêtre, `drop_incomplete=True` sur `fetch_ohlcv`, `ScoreBuilder.confidence = |score|/Σ|poids|`.

### Audit v2 — précision et réalisme

- **`medium_term` : NaN traités comme baissier** (chaîne `if/elif/else` sans guard `pd.notna`).
- **Backtest O(n²)** : recalcul complet des indicateurs à chaque bougie.
- **Warmup uniforme insuffisant pour le long terme** (ATH 4Y dégénéré sur les premières itérations).
- **Slippage non appliqué au déclenchement SL/TP** → biais favorable à la stratégie.

→ Corrigés : `pd.notna` guards, enrichissement unique + slicing, warmup par horizon, `_apply_slippage` hostile.

### Audit v3 — corrections subtiles

- **Pagination ccxt manquante** : `limit=1500` daily Binance était silencieusement tronqué à 1000.
- **Entrée backtest au close de la bougie de signal** au lieu de l'open de la suivante → look-ahead implicite.
- **`STRATEGIES` dupliquée** entre `analyze.py` et `backtest.py` → risque de divergence.
- **Docstring backtest obsolète** (disait "recalcule" alors qu'on enrichit une fois).

→ Corrigés : pagination via `since` avec déduplication, entrée à `open[i+1]`, `get_strategies()` source unique, docs alignées.

### Audit v4 — typing & lisibilité

- `StrategyMeta.analyze: Callable[[pd.DataFrame], Signal]`
- `df_raw.index.get_loc()` → `searchsorted()` (robuste aux doublons)
- `_apply_slippage` réécrite avec `is_buying_action` (xor implicite remplacé par expression nommée)
- `Optional` → `| None` (PEP 604)
- `from __future__ import annotations` cohérent partout
- Colonne `ts_ms` redondante supprimée

### Audit v5 — 2026-08-07 : look-ahead sentiment & réalisme

- **Fear & Greed contaminait le backtest long** : `long_term.analyze` appelle `sentiment.fear_greed_event()`, donc chaque slice historique du backtest recevait le F&G *du jour du run* (ex: F&G=29 en août 2026 → biais haussier +0.04 appliqué à 4 ans de bougies) + un appel réseau pendant le backtest. → Flag `sentiment.BACKTEST_MODE` activé/désactivé par `backtest()` (try/finally).
- **RSI = NaN quand `avg_loss == 0`** (aucune baisse depuis le début de la fenêtre EWM) : l'indicateur était muet précisément dans les uptrends les plus purs. → RSI saturé à 100 (50 si flat), NaN préservé pendant le warmup.
- **`/api/refresh` créait une task asyncio sans référence** → risque de GC avant exécution. → Référence gardée sur `app.state`.
- **`live_limit` par horizon** : l'analyse live chargait la fenêtre backtest complète (1500 bougies 15m = 2 requêtes paginées par paire par cycle) alors que 450 suffisent (warmup 200 + lookbacks). Long garde 1500 (ATH 4Y).
- **`requirements.txt` pinné** (ccxt 4.5.54, pandas 3.0.3, numpy 2.4.6, rich 15.0.0, fastapi 0.136.3, uvicorn 0.48.0).
- README resynchronisé : 12 paires (pas 8), exemple live d'août 2026.

**Backtests BTC re-lancés après le fix (fenêtre incluant la jambe baissière oct. 2025 → juin 2026)** : long PF 1.21 (13 trades, -8.5% total, max DD -66%) ; medium PF 1.00 (31 trades, breakeven). La baisse vs l'audit v4 vient (1) du retrait du biais F&G haussier artificiel, (2) de la fenêtre qui couvre désormais le crash de 126k$ à 59k$. Ces chiffres sont moins flatteurs mais honnêtes.

### Audit v6 — 2026-08-07 : révision des stratégies (banc d'essai comparatif)

Méthode : chaque hypothèse de changement a été backtestée contre la baseline sur plusieurs paires (frais 0.10% + slippage 0.05%), avec split du PnL par côté BUY/SELL. **Adopté seulement si amélioration robuste sur la majorité des paires** — deux hypothèses sur quatre ont été rejetées.

| Hypothèse | Verdict | Constat |
|---|---|---|
| **Long-only pour le long terme** (ne plus simuler les SELL) | ✅ **Adopté** | Le côté SELL perdait sur les 3 paires (BTC -21%, ETH -40%, SOL -31% de PnL cumulé) alors que le côté BUY gagnait partout. Un short spot multi-mois est de toute façon irréalisable. Le SELL reste émis en live comme alerte d'allègement ; `--allow-shorts` pour comparer. |
| **Gate "reprise amorcée"** : bonus drawdown/DCA seulement si close > SMA50 | ✅ **Adopté** | Côté BUY : neutre sur BTC/ETH, nettement meilleur sur SOL (+86% vs +56%). Encode l'intention réelle : accumuler la reprise, pas la chute libre. |
| **Medium : croisement EMA récent obligatoire** (suppression de la voie "tendance établie") | ❌ Rejeté | Meilleur sur SOL/BNB, égal sur ETH, mais bien pire sur BTC (PF 0.56 vs 1.00). Pas robuste — en marché haché le trend-following 4h perd dans les deux sens, le gating d'entrée n'y change rien. |
| **Short : seuil 0.65 et/ou passage en 1h** | ❌ Rejeté | Seuil 0.65 : strictement aucun trade supplémentaire. 1h : échantillons de 2-6 trades, aucune amélioration. Le short reste une couche de timing quasi inerte — comportement sain vu que les rares trades qui passent le filtre perdent encore. |

**Backtests long après adoption (frais inclus, fenêtre couvrant le cycle 126k$ → 59k$ → 65k$)** :

| Paire | Avant (v5) | Après (v6) |
|---|---|---|
| BTC | PF 1.21, -8.5%, DD -66% | **PF 1.76, +34.5%, DD -35%** |
| ETH | PF 0.82, -56.9%, DD -81% | PF 1.16, -17.7%, DD -64% |
| SOL | PF 1.12, -52.0%, DD -88% | PF 1.39, -21.5%, DD -75% |

ETH/SOL restent négatifs en retour composé (grosses pertes tôt dans la fenêtre) mais l'amélioration est uniforme sur PF, retour et drawdown. Piste future la plus impactante : **position sizing** (risque fixe par trade au lieu de 100% du capital) — c'est lui qui transformerait un PF > 1 en courbe d'equity exploitable ; les stratégies ne produisent aujourd'hui que des signaux, pas des tailles.

### État final

- 🔴 Critiques : **0**
- 🟠 Majeurs : **0**
- 🟡 Mineurs restants : pas de tests pytest, pas de cache disque, F&G historique non modélisé en backtest (l'event est simplement absent), transitions potentiellement parasites lors d'un fallback d'exchange en cours de session

## Exemple d'analyse live

Snapshot du 2026-08-07 sur les 12 paires suivies (`python analyze.py --json`). Contexte : BTC a fait son ATH à 126k$ en oct. 2025, plongé à ~59k$ en juin 2026, et se stabilise dans les 60-65k$. Fear & Greed = 29 (Fear).

| Symbole | Prix | Δ24h | Court (15m) | Moyen (4h) | Long (1d) | Drawdown ATH 4Y |
|---|---:|---:|---|---|---|---:|
| BTC/USDT  | 65 224   | +1.5% | HOLD `-0.35` RSI 75 + BB sup | **BUY `+0.75`** breakout | HOLD `+0.09` | **-49%** DCA |
| ETH/USDT  | 1 929    | +1.7% | HOLD `-0.45` RSI 72 + BB sup | HOLD `+0.35`             | HOLD `+0.09` | **-62%** capitulation |
| SOL/USDT  | 73.82    | +1.2% | HOLD `+0.25`                 | **SELL `-0.75`** EMA↘    | HOLD `-0.01` | **-75%** capitulation |
| BNB/USDT  | 591.76   | -0.1% | HOLD `-0.25`                 | HOLD `+0.50` proche BUY  | HOLD `+0.09` | **-57%** DCA |
| ADA/USDT  | 0.2005   | +5.0% | HOLD `+0.25`                 | HOLD `+0.40`             | HOLD `+0.02` | **-85%** capitulation |
| XRP/USDT  | 1.038    | -0.7% | HOLD `-0.25`                 | HOLD `-0.50`             | HOLD `-0.04` | **-72%** capitulation |
| DOGE/USDT | 0.0700   | +2.1% | HOLD `-0.05`                 | HOLD `-0.50`             | HOLD `+0.10` | **-86%** capitulation |
| AVAX/USDT | 6.481    | +1.1% | HOLD `-0.25`                 | HOLD `-0.35` EMA↘        | HOLD `+0.10` | **-90%** capitulation |
| SUSHI/USDT| 0.165    | -0.8% | HOLD `+0.25`                 | **BUY `+0.75`** EMA↗     | HOLD `-0.01` | **-94%** capitulation |
| ETH/BTC   | 0.02957  | +0.2% | HOLD `+0.25`                 | HOLD `+0.46`             | HOLD `+0.09` | -65% |
| BNB/BTC   | 0.009073 | -1.6% | HOLD `+0.45` RSI 29 + BB inf | HOLD `-0.30`             | **BUY `+0.54`** golden cross | -53% |
| SOL/ETH   | 0.03828  | -0.5% | HOLD `-0.21`                 | HOLD `-0.50`             | **BUY `+0.42`** golden cross | -59% |

**Lecture (2026-08-07)** :
- **Court terme** : BTC/ETH collés à leur BB supérieure avec RSI > 70 après le rebond → repli technique probable, mais sous le seuil ±0.70. BNB/BTC en survente (RSI 29, BB inf) mais bloqué par le régime baissier — exactement le comportement voulu du filtre.
- **Moyen terme** : BTC **BUY +0.75** (breakout au-dessus du range 20 bougies + histo MACD croissant + SMA50 en hausse) — première tentative de reprise 4h depuis le creux de juin. SOL à contre-courant (**SELL -0.75**, croisement EMA baissier sous SMA200). SUSHI BUY mais paire à liquidité faible et -94% vs ATH : signal fragile, à pondérer.
- **Long terme** : tous les /USDT encore en régime baissier daily (SMA50 < SMA200) — le bonus capitulation + F&G Fear ne suffit pas à franchir +0.30, cohérent avec un marché qui se cherche un plancher. Les deux **BUY** long sont des paires *cross* (BNB/BTC, SOL/ETH) sur golden cross : force relative vs BTC/ETH, pas une hausse en dollars.

**Backtests BTC après l'audit v5** (fenêtre couvrant le cycle 126k$ → 59k$ → 65k$) : long PF 1.21 (-8.5% total, max DD -66%), medium PF 1.00 (breakeven). Ces stratégies restent des **squelettes pédagogiques** : les chiffres proviennent d'une seule fenêtre temporelle par horizon, pas d'une validation walk-forward multi-périodes.

## Limitations & disclaimer

- Pas de tests pytest automatisés (smoke-tests manuels seulement).
- Pas de cache disque : chaque exécution refait les appels API (≈ 60 requêtes pour les 12 paires × 3 horizons + ticker ; les `live_limit` évitent la pagination sur short/medium).
- Sur short/medium, les `SELL` sont encore traités comme un **short parfait** (faux sur spot, imprécis sur futures). Le long terme est long-only en backtest depuis l'audit v6 (`--allow-shorts` pour comparer).
- Stratégies **purement chartistes** : ignorent news, on-chain, macro, sentiment, funding, open interest.
- Backtest sans optimisation walk-forward des hyperparamètres (poids, seuils, périodes des indicateurs).
- Le slippage modélisé est **uniforme** alors qu'en réalité il dépend de la liquidité de la paire et de la taille du trade.

**Ces signaux ne sont pas des conseils financiers**. Outil d'expérimentation et de réflexion technique. Ne pas utiliser tel quel en trading réel sans backtest sérieux, optimisation des paramètres, gestion de position adaptée, et compréhension fine de chaque ligne de code.
