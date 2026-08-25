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

Chaque exécution committe ses résultats dans `data/`. Le dashboard lit ces
fichiers **directement depuis le dépôt à chaque requête**, sans base de données
ni API intermédiaire : il reflète donc toujours le dernier scan sans qu'un
redéploiement soit nécessaire. C'est délibéré — faire redéployer Vercel à
chaque scan représenterait 96 déploiements par jour, alors que le plan Hobby en
autorise 100.

L'historique git devient l'historique du bot : chaque signal est horodaté et
non falsifiable a posteriori.

| Composant | Rôle |
|---|---|
| `engine/jimbot/indicators.py` | RSI, MACD, ATR, ADX, Bollinger, Donchian, OBV, VWAP, Keltner, Supertrend, Ichimoku, chandelier, choppiness, ratio de variance, MFI, divergences — réimplémentés en numpy/pandas, sans TA-Lib |
| `engine/jimbot/stats.py` | Régime de marché, exposant de Hurst (analyse R/S), corrélations, Sharpe, drawdown |
| `engine/jimbot/strategy.py` | Scoring sept facteurs, pondéré par le régime détecté |
| `engine/jimbot/levels.py` | Structure de marché et optimisation des niveaux par espérance mathématique |
| `engine/jimbot/risk.py` | Dimensionnement par risque fixe, demi-Kelly, plafonds de portefeuille et de corrélation |
| `engine/jimbot/paper.py` | Exécution simulée avec frais et glissement, statistiques de performance |
| `engine/jimbot/narrator.py` | Rédaction hybride : chiffres calculés en Python, mise en phrases par Claude |
| `engine/jimbot/report.py` | Rapport PDF (ReportLab + matplotlib) |
| `app/` | Dashboard Next.js et routes d'API |
| `metatrader/` | Expert Advisor MQL5 prêt à l'emploi |

## Principe du moteur

Le moteur détermine d'abord **le régime de marché** — tendance, range ou
chaotique — puis applique le jeu de pondérations correspondant. C'est le point
central : un croisement de moyennes mobiles est pertinent en tendance et
trompeur en range ; un RSI en survente signale un achat en range et une
continuation baissière en tendance. Sept facteurs sont notés indépendamment
dans `[-1, +1]` — tendance, momentum, retour à la moyenne, volume, cassure,
structure, sentiment — combinés selon le régime, puis modulés par la qualité de
ce régime et l'accord avec l'unité de temps supérieure.

Chaque point de score est traçable jusqu'à la formule qui l'a produit : le
rapport et le dashboard affichent la contribution de chaque facteur.

### Niveaux optimisés par espérance mathématique

Les stops et objectifs ne sont pas des multiples d'ATR arbitraires. Le moteur
identifie la structure réellement respectée par le marché — points pivots,
zones de congestion, retracements de Fibonacci, point de contrôle du volume —
puis évalue les couples stop/objectif adossés à cette structure et retient
celui qui **maximise l'espérance**.

Le point de départ est un résultat exact : pour une marche aléatoire sans
dérive, la probabilité d'atteindre l'objectif avant le stop vaut
`d_stop / (d_stop + d_objectif)`, ce qui rend l'espérance **rigoureusement
nulle quel que soit le ratio rendement/risque**. Aucun réglage de R/R ne crée
d'avantage à lui seul. Celui-ci ne peut venir que de trois sources, toutes
modélisées explicitement :

1. la capacité du signal à prédire la direction, **décroissante avec la
   distance de l'objectif** — un signal technique a un horizon fini ;
2. la solidité du niveau structurel qui adosse le stop ;
3. l'absence d'obstacle entre le prix et l'objectif.

S'y ajoutent une pénalité pour les stops situés dans le bruit ordinaire de la
bougie, et une pour les objectifs exigeant de franchir une résistance. **Un
plan dont l'espérance reste négative est écarté même à forte conviction** :
c'est le principal apport de cette couche, et elle rejette régulièrement des
signaux que le seul score aurait validés.

### Contexte mondial

Les actualités ne se limitent pas aux marchés. Le moteur suit 18 flux, dont
BBC World, NYT, Al Jazeera, Guardian, AP, France 24, Le Monde et les
communiqués officiels de la Fed et de la BCE, et en extrait deux axes que le
sentiment classique ne peut pas représenter :

- **l'axe risque-on / risque-off** : une escalade géopolitique n'a pas le même
  signe selon l'actif. Chaque instrument porte un *bêta de valeur refuge* — +0.9
  pour l'or, +1.0 pour le VIX, −0.9 pour le Nasdaq, −0.55 pour Bitcoin — qui
  détermine le sens et l'intensité de l'effet ;
- **l'axe monétaire** : l'or ne réagit pas aux bénéfices d'entreprise mais aux
  taux réels. Le moteur détecte les prises de parole officielles (Powell,
  Lagarde, FOMC, Jackson Hole…), en mesure la tonalité accommodante ou
  restrictive, et publie une alerte Discord dédiée avec l'effet attendu chiffré
  par actif.

Les deux lexiques existent en anglais et en français — sans quoi les flux
France 24 et Le Monde seraient entièrement ignorés.

### Risque

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
d'environnement requise.

Si le dépôt est un fork ou porte un autre nom, définir `JIMBOT_DATA_URL` sur la
base brute de vos données, par exemple
`https://raw.githubusercontent.com/<compte>/<dépôt>/main/data`.

`vercel.json` contient un `ignoreCommand` qui annule la construction lorsque
seuls `data/` et `reports/` ont changé : les commits de scan ne consomment donc
aucun déploiement.

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
.venv/bin/python -m pytest engine/tests -v    # 155 tests, sans réseau
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

## API

Trois routes en lecture seule, sans authentification, avec CORS ouvert — elles
ne servent que des données déjà publiques dans le dépôt.

| Route | Contenu |
|---|---|
| `/api/mt` | Flux au format MetaTrader : symbole, `BUY`/`SELL`, stop, objectif, risque suggéré. Paramètres `mode` (`actionable`, `watchlist`, `all`), `min_score`, `symbol` |
| `/api/signals` | État complet du scan en JSON. Paramètre `actionable=1` pour filtrer |
| `/api/reports` | Index des rapports PDF ; `?file=jimbot-AAAA-MM-JJ.pdf` télécharge le document |

## MetaTrader 5

`metatrader/JimbotConnector.mq5` interroge `/api/mt`, affiche les
configurations sur le graphique et, si l'exécution automatique est activée,
ouvre les positions correspondantes.

**Il est en lecture seule par défaut.** L'exécution doit être activée
explicitement et reste bornée par un risque maximal par position, un risque
cumulé et un nombre de positions. Le volume est toujours déduit de la distance
au stop, jamais d'un nombre de lots fixe ; si le risque voulu correspond à un
volume sous le lot minimal du courtier, rien n'est ouvert plutôt que
d'arrondir vers le haut. Un signal à espérance négative est ignoré même en
mode automatique.

Voir `metatrader/README.md` pour l'installation — notamment l'autorisation des
`WebRequest`, sans laquelle rien ne fonctionne.

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
| Crypto : `data-api.binance.vision`, Binance, Coinbase, Kraken (chaîne de repli) | OHLCV crypto | non |
| Yahoo Finance (endpoint chart) | forex, indices, matières premières | non |
| DexScreener | découverte et criblage des memecoins | non |
| 18 flux RSS (CoinDesk, Cointelegraph, Decrypt, TheBlock, FXStreet, MarketWatch, NYT, BBC, Al Jazeera, Guardian, AP, France 24, Le Monde, Fed, BCE…) | actualités marchés et monde | non |
| API Anthropic | rédaction des analyses | facultative |

## Limites connues

- **Volume forex indisponible** via Yahoo : le facteur volume est neutralisé
  sur ces actifs plutôt que d'inventer une valeur.
- **L'exposant de Hurst est biaisé à la hausse** sur les historiques courts
  (biais d'Anis-Lloyd) : mesuré à ~0.57 sur des marches aléatoires de 400
  bougies là où la théorie donne 0.50. Il ne doit être lu qu'en comparaison.
- **Le sentiment repose sur des lexiques pondérés**, anglais et français. Il ne
  détecte ni l'ironie ni l'implicite. Son influence est bornée à ±1 et pondérée
  par le nombre d'articles et leur fraîcheur, précisément pour qu'une dépêche
  isolée ne puisse pas déplacer un signal.
- **Les bêtas de valeur refuge sont des constantes**, pas des estimations
  glissantes : ils reflètent le comportement historique moyen des actifs, qui
  peut se rompre (Bitcoin s'est déjà comporté à la fois comme actif de risque
  et comme refuge selon les périodes).
- **L'horizon d'avantage du signal est un paramètre**, fixé à 5 ATR. C'est le
  réglage le plus influent du module de niveaux, et le seul qui mériterait
  d'être estimé sur les données plutôt que posé a priori — ce qui exigera
  plusieurs centaines de trades fermés.
- **Les memecoins retenus sont rares** — souvent aucun. C'est le filtre qui
  fonctionne, pas une panne : les exigences de liquidité croissent à mesure que
  le pool est jeune.
- **Le portefeuille papier n'a pas de données infra-bougie** : quand une même
  bougie touche le stop et l'objectif, le stop est retenu. L'hypothèse inverse
  gonflerait artificiellement les résultats.
- **Binance géo-bloque les adresses IP américaines** (HTTP 451), donc les
  runners GitHub. Les données crypto passent par une chaîne de repli —
  `data-api.binance.vision`, puis Binance, Coinbase et Kraken — de sorte
  qu'aucun fournisseur ne constitue un point de défaillance unique. Les prix
  diffèrent légèrement d'une place à l'autre, ce qui est normal.
- **Aucun backtest historique long** n'est fourni : les statistiques se
  construisent en avançant, ce qui évite tout surapprentissage rétrospectif
  mais impose d'attendre avant de juger la stratégie.

## Avertissement

Analyse automatisée à but informatif. Ne constitue pas un conseil en
investissement. Les performances passées, réelles ou simulées, ne préjugent pas
des performances futures.
