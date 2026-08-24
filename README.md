# Jimbot

Moteur d'analyse de marché multi-actifs : crypto, forex, indices et memecoins.
Il calcule des signaux à partir d'indicateurs recalculés à la main, publie des
alertes sur Discord, tient un portefeuille papier et produit un rapport PDF
quotidien. Aucun outil payant, aucune source de données payante.

**Le portefeuille est simulé. Aucun ordre réel n'est transmis à un exchange.**

## Architecture

```
GitHub Actions            Vercel
──────────────            ──────
scan.py    (15 min)  ──┐
daily_report.py (1j) ──┼─→  data/*.json  ─→  dashboard Next.js
                       │    reports/*.pdf
                       └─→  Discord (webhook)
```

Chaque exécution committe ses résultats dans `data/`, ce qui déclenche un
redéploiement Vercel : le dashboard reflète toujours le dernier scan, sans base
de données ni API intermédiaire. L'historique git devient l'historique du bot —
chaque signal est horodaté et non falsifiable a posteriori.

| Composant | Rôle |
|---|---|
| `engine/jimbot/indicators.py` | RSI, MACD, ATR, ADX, Bollinger, Donchian, OBV, VWAP, régression glissante — réimplémentés en numpy/pandas, sans TA-Lib |
| `engine/jimbot/stats.py` | Régime de marché, exposant de Hurst (analyse R/S), corrélations, Sharpe, drawdown |
| `engine/jimbot/strategy.py` | Scoring six facteurs, pondéré par le régime détecté |
| `engine/jimbot/risk.py` | Dimensionnement par risque fixe, demi-Kelly, plafonds de portefeuille et de corrélation |
| `engine/jimbot/paper.py` | Exécution simulée avec frais et glissement, statistiques de performance |
| `engine/jimbot/narrator.py` | Rédaction hybride : chiffres calculés en Python, mise en phrases par Claude |
| `engine/jimbot/report.py` | Rapport PDF (ReportLab + matplotlib) |
| `app/` | Dashboard Next.js |

## Principe du moteur

Le moteur détermine d'abord **le régime de marché** — tendance, range ou
chaotique — puis applique le jeu de pondérations correspondant. C'est le point
central : un croisement de moyennes mobiles est pertinent en tendance et
trompeur en range ; un RSI en survente signale un achat en range et une
continuation baissière en tendance. Six facteurs sont notés indépendamment
dans `[-1, +1]`, combinés selon le régime, puis modulés par la qualité de ce
régime et l'accord avec l'unité de temps supérieure.

Chaque point de score est traçable jusqu'à la formule qui l'a produit : le
rapport et le dashboard affichent la contribution de chaque facteur.

Les niveaux d'invalidation sont calés sur l'ATR, pas sur un pourcentage fixe.
Le dimensionnement part du risque accepté, pas du montant investi : la taille
découle de la distance au stop, et plusieurs plafonds — risque total, exposition
unitaire par classe, risque cumulé entre actifs corrélés — priment sur le score.

## Installation

### 1. Dépôt

```bash
git clone <votre-dépôt> jimbot && cd jimbot
python3 -m venv .venv && .venv/bin/pip install -r engine/requirements.txt
npm install
```

### 2. Discord

Salon → Paramètres → Intégrations → Webhooks → **Nouveau webhook** → copier
l'URL. Puis, dans GitHub : **Settings → Secrets and variables → Actions**

- onglet *Secrets* : `DISCORD_WEBHOOK_URL`, éventuellement `DISCORD_ROLE_ID` et
  `ANTHROPIC_API_KEY` ;
- onglet *Variables* : les réglages `JIMBOT_*` de `.env.example` (facultatif,
  des valeurs par défaut s'appliquent).

Dans **Settings → Actions → General**, section *Workflow permissions*,
sélectionner **Read and write permissions** — sans quoi les workflows ne
pourront pas committer leurs résultats.

### 3. Vercel

Importer le dépôt sur [vercel.com/new](https://vercel.com/new). Framework
détecté automatiquement (Next.js), répertoire racine `/`, aucune variable
d'environnement requise pour le dashboard. Chaque commit du bot redéclenche un
déploiement.

### 4. Premier lancement

```bash
.venv/bin/python engine/scan.py --dry-run     # analyse sans publier
.venv/bin/python engine/daily_report.py --no-send
```

Puis, sur GitHub, onglet **Actions** → *Scan de marché* → **Run workflow**.

## Utilisation locale

```bash
.venv/bin/python engine/scan.py               # scan complet + alertes
.venv/bin/python engine/scan.py --no-alert    # analyse seule
.venv/bin/python engine/scan.py --dry-run     # simule aussi les envois
.venv/bin/python engine/daily_report.py       # rapport PDF + publication
.venv/bin/python -m pytest engine/tests -v    # 96 tests, sans réseau
npm run dev                                   # dashboard sur localhost:3000
```

## Consommation GitHub Actions

Un scan complet prend environ **75 secondes** en CI. À raison de 4 par heure :

| Cadence | Exécutions / mois | Minutes / mois |
|---|---|---|
| 15 min (par défaut) | ~2 880 | ~3 600 |
| 30 min | ~1 440 | ~1 800 |
| 1 h | ~720 | ~900 |

**Sur un dépôt public, Actions est gratuit et illimité** — c'est la
configuration recommandée, d'autant que le dépôt ne contient aucun secret (ils
vivent dans les *Secrets* GitHub).

Sur un dépôt **privé**, le quota gratuit est de 2 000 minutes par mois : la
cadence de 15 minutes le dépasse. Passer à 30 minutes en modifiant une ligne
dans `.github/workflows/scan.yml` :

```yaml
- cron: "*/30 * * * *"
```

## Réglages

Tout se règle par variable d'environnement, sans toucher au code :

| Variable | Défaut | Effet |
|---|---|---|
| `JIMBOT_SIGNAL_THRESHOLD` | 58 | En dessous, l'actif reste neutre |
| `JIMBOT_ALERT_THRESHOLD` | 68 | Seuil de publication Discord |
| `JIMBOT_PING_THRESHOLD` | 80 | Seuil de mention du rôle |
| `JIMBOT_ALERT_COOLDOWN_MIN` | 180 | Anti-spam par actif et par sens |
| `JIMBOT_PAPER_CAPITAL` | 10000 | Capital de départ simulé |
| `JIMBOT_DRY_RUN` | — | `1` simule tous les envois Discord |

L'univers suivi et les profils de risque par classe d'actif sont dans
`engine/jimbot/config.py`.

## Sources de données

| Source | Usage | Clé requise |
|---|---|---|
| Binance API publique | OHLCV crypto | non |
| Yahoo Finance (endpoint chart) | forex, indices, matières premières | non |
| DexScreener | découverte et criblage des memecoins | non |
| Flux RSS (CoinDesk, Cointelegraph, Decrypt, TheBlock, FXStreet, CNBC, Yahoo) | actualités | non |
| API Anthropic | rédaction des analyses | facultative |

## Limites connues

- **Volume forex indisponible** via Yahoo : le facteur volume est neutralisé
  sur ces actifs plutôt que d'inventer une valeur.
- **L'exposant de Hurst est biaisé à la hausse** sur les historiques courts
  (biais d'Anis-Lloyd) : mesuré à ~0.57 sur des marches aléatoires de 400
  bougies là où la théorie donne 0.50. Il ne doit être lu qu'en comparaison.
- **Le sentiment repose sur un lexique anglophone** : il ne couvre ni les
  sources francophones ni l'ironie. Son influence est bornée à ±1 et pondérée
  par le nombre d'articles et leur fraîcheur, précisément pour qu'une dépêche
  isolée ne puisse pas déplacer un signal.
- **Les memecoins retenus sont rares** — souvent aucun. C'est le filtre qui
  fonctionne, pas une panne : les exigences de liquidité croissent à mesure que
  le pool est jeune.
- **Le portefeuille papier n'a pas de données infra-bougie** : quand une même
  bougie touche le stop et l'objectif, le stop est retenu. L'hypothèse inverse
  gonflerait artificiellement les résultats.
- **Aucun backtest historique long** n'est fourni : les statistiques se
  construisent en avançant, ce qui évite tout surapprentissage rétrospectif
  mais impose d'attendre avant de juger la stratégie.

## Avertissement

Analyse automatisée à but informatif. Ne constitue pas un conseil en
investissement. Les performances passées, réelles ou simulées, ne préjugent pas
des performances futures.
