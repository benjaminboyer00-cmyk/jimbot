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
| `engine/jimbot/calendar.py` | Échéances à venir : règles de calendrier et annonces de presse |
| `engine/jimbot/backtest.py` | Validation walk-forward et calibration du modèle |
| `engine/jimbot/probe.py` | Mesure du pouvoir prédictif de chaque facteur |
| `app/` | Dashboard Next.js, page « Courbes » et routes d'API |
| `lib/chart.ts` | Primitives de tracé : échelles, graduations rondes, splines monotones |
| `lib/series.ts` | Dérivation des séries à partir des fichiers du moteur |
| `components/charts.tsx` | Graphiques SVG rendus côté serveur, sans dépendance |
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

### Le plan fixe l'emporte sur l'optimiseur

Les niveaux sont posés simplement : **stop à 2 ATR, objectif à 2 R**, sans
aucune optimisation. Ce choix vient d'une mesure, et il a coûté la mise au
rebut d'un module de quatre cents lignes.

Comparés sur le même historique :

| | optimiseur | plan fixe |
|---|---|---|
| trades | 340 | 91 |
| taux de réussite | 27.9 % | **40.7 %** |
| espérance réalisée | +0.045 R | **+0.186 R** |
| facteur de profit | 1.073 | **1.309** |
| drawdown maximal | 29.3 R | **11.45 R** |

Le mécanisme est identifié. L'optimiseur retenait un R/R de 3.0 dans 175 cas
sur 340, or c'est précisément la bande qui perd :

| R/R retenu | n | réussite | espérance |
|---|---|---|---|
| < 1.5 | 76 | 48.7 % | +0.145 |
| 1.5 – 2.5 | 50 | 34.0 % | **+0.277** |
| **2.5 – 3.5** | **197** | **19.3 %** | **−0.029** |
| > 3.5 | 17 | 17.6 % | −0.232 |

La cause tient à sa fonction objectif. Maximiser une espérance *estimée*
revient à retenir les estimations les plus flatteuses, qui sont aussi les plus
bruitées : un optimiseur nourri d'une estimation imparfaite sélectionne son
erreur. Le plan fixe ne peut pas tromper son propre estimateur, et c'est
exactement ce qui le protège.

Le réglage n'est pas un point chanceux. Contrôle de robustesse sur les R/R
voisins, en comparant chaque variante à *son propre* seuil de rentabilité —
sans quoi on comparerait des configurations non comparables :

| plan | n | réussite | seuil | écart | espérance |
|---|---|---|---|---|---|
| 2 ATR / 1.5 R | 80 | 41.2 % | 40.0 % | +1.2 pt | +0.001 R |
| 2 ATR / **2.0 R** | 91 | 40.7 % | 33.3 % | **+7.4 pt** | +0.186 R |
| 2 ATR / **2.5 R** | 92 | 34.8 % | 28.6 % | **+6.2 pt** | +0.194 R |

Il existe un plateau de 2.0 à 2.5 R. Seul 1.5 R s'effondre, et pour une raison
identifiable : sa cible trop proche produit un gain qui ne couvre plus les
frais de transaction.

L'optimiseur reste accessible pour la recherche (`JIMBOT_PLAN_MODE=optimise`),
et toute la détection de structure — pivots, congestions, Fibonacci, point de
contrôle du volume — garde sa valeur descriptive dans les rapports. Elle n'entre
simplement plus dans la décision.

### Ce que l'optimiseur cherchait à faire

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

### Échéances à venir

Deux sources, et deux seulement, parce que ce sont les deux qu'on peut
garantir. Les **échéances mécaniques** se déduisent du calendrier par une
règle — le rapport sur l'emploi américain tombe le premier vendredi du mois,
les options expirent le troisième, les fins de trimestre déclenchent des
rééquilibrages — et sont donc exactes par construction. Les **annonces de
presse** sont extraites des dépêches qui signalent explicitement un événement
à venir (« ahead of Thursday's CPI », « the Fed meets next week »), et citées
avec leur source.

Aucun calendrier de réunions de banques centrales n'est inscrit en dur : ces
dates changent, et une date fausse présentée comme certaine est pire que pas
de date du tout. Le moteur signale l'échéance quand la presse la mentionne, et
se tait sinon.

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
.venv/bin/python -m pytest engine/tests -v    # 210 tests, sans réseau
.venv/bin/python engine/backtest_run.py       # validation walk-forward (~10 min)
.venv/bin/python engine/probe_run.py          # pouvoir prédictif des facteurs (~5 min)
npm run dev                                   # dashboard sur localhost:3000
```

## Cadence réelle, et pourquoi elle n'est pas celle qu'on déclare

**L'ordonnanceur de GitHub Actions ne tient pas les planifications
rapprochées.** La documentation le présente comme un service « au mieux », et
la mesure le confirme sans ambiguïté : avec un cron `*/15`, la couverture
observée sur 71 heures a été de **4,2 %** — 12 scans au lieu de 285, avec un
écart médian de **7 heures** et des trous allant jusqu'à 11 heures. Pendant ce
temps, le rapport quotidien se déclenchait normalement : ce sont bien les
crons rapides qui sont visés.

Le workflow déclare donc une cadence **horaire** et enchaîne quatre scans
espacés de 15 minutes à chaque exécution, publiés au fil de l'eau. Même
déclenchée avec deux heures de retard, une exécution couvre alors quatre
points de marché au lieu d'un seul.

Le dashboard affiche un avertissement dès que le dernier scan remonte à plus
de 90 minutes : mieux vaut annoncer le trou que laisser croire à une
surveillance continue.

Sur un dépôt public, Actions reste gratuit et illimité. Sur un dépôt privé,
compter environ 50 minutes par exécution horaire — soit bien au-delà du quota
gratuit : passer alors le cron à `0 */4 * * *`.

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

Quatre routes en lecture seule, sans authentification, avec CORS ouvert — elles
ne servent que des données déjà publiques dans le dépôt.

| Route | Contenu |
|---|---|
| `/api/mt` | Flux au format MetaTrader : symbole, `BUY`/`SELL`, stop, objectif, risque suggéré. Paramètres `mode` (`actionable`, `watchlist`, `all`), `min_score`, `symbol` |
| `/api/signals` | État complet du scan en JSON. Paramètre `actionable=1` pour filtrer |
| `/api/reports` | Index des rapports PDF ; `?file=jimbot-AAAA-MM-JJ.pdf` télécharge le document |
| `/api/curves` | Séries prêtes à tracer, et rendu SVG. Paramètres `serie`, `format`, `w`, `h` |

### Service de courbes

`/api/curves` expose les séries dérivées des mêmes fichiers que le dashboard :
capital du portefeuille et repli, R cumulés (réels et rejoués), distribution
des résultats, calibration par tranche de score, excursions MFE/MAE,
coefficients d'information par facteur, score signé de l'univers suivi.

```
/api/curves                        index et toutes les séries
/api/curves?serie=capital          une série en JSON
/api/curves?serie=capital&format=svg&w=860&h=240
```

Le rendu `format=svg` produit une image **autonome** : elle embarque ses
couleurs et sa propre requête `prefers-color-scheme`, donc elle s'affiche
correctement dans un `README`, un message ou une page tierce, sans feuille de
style ni JavaScript.

```markdown
![capital](https://votre-domaine/api/curves?serie=capital&format=svg)
```

Les séries sont dérivées par `lib/series.ts`, le même module que les pages :
une courbe servie par l'API ne peut pas diverger de celle qui est affichée sur
le site.

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

## Ce que dit la validation historique

Le moteur est rejoué bougie par bougie sur l'historique, sans qu'aucune donnée
future ne puisse entrer dans la décision (`engine/backtest_run.py`, relancé
chaque dimanche par GitHub Actions). Les résultats sont affichés sur le
dashboard plutôt que rangés dans un fichier.

**Aucun avantage n'est démontré à ce jour.** Sur 207 trades simulés,
l'espérance réalisée est de +0.057 R avec un intervalle de confiance à 95 %
de [-0.126 ; +0.240] R : il contient zéro. Il faudrait environ 2 200 trades
pour trancher.

Trois enseignements ont directement modifié le code :

1. **Les frais étaient absents du calcul d'espérance.** Un stop touché coûte
   -1.046 R et non -1.000 R, et le coût rapporté au risque explose quand le
   stop est serré : à frais constants, un stop à 0.3 % du prix consomme 0.33 R.
   C'est ce qui rendait le forex et les indices structurellement perdants —
   leurs ATR de 0.05 à 0.2 % imposent des stops que les frais dévorent. Une
   fois les coûts intégrés, le modèle est calibré : il prédit 38.0 % de
   réussite, il en réalise 38.2 %.

2. **L'avantage supposé du signal n'existait pas.** `MAX_EDGE` valait 0.18,
   posé a priori. Mesuré : pour un R/R moyen de 1.78, le seuil de rentabilité
   sans aucun avantage est de 35.9 % de réussite et le moteur en réalisait
   35.6 %. La constante est ramenée à 0.02.

3. **Le VIX est retiré de l'univers négociable** — 171 trades, 10.5 % de
   réussite, -0.735 R. Un indice de volatilité alterne pics violents et
   affaissements lents : un moteur de tendance y achète les sommets. Il reste
   suivi pour le contexte et les corrélations.

## Ce que la sonde a révélé

Le backtest disait que le score ne discriminait pas, sans dire pourquoi. Le
module `probe.py` répond à la question qui vient avant : **qu'est-ce qui, dans
ce moteur, porte réellement de l'information ?**

Il enregistre à chaque pas la valeur de chaque facteur — sans aucun filtre,
contrairement au backtest qui n'observe que les trades retenus — puis le
rendement effectivement réalisé, normalisé par l'ATR. La corrélation de rang
entre les deux est le coefficient d'information.

Mesuré sur 17 070 observations et 15 actifs :

| facteur | 6 bougies | 12 | 24 | 48 |
|---|---|---|---|---|
| trend | −0.025* | −0.029* | −0.045* | **−0.065*** |
| structure | −0.021* | −0.027* | −0.042* | **−0.064*** |
| breakout | −0.007 | −0.009 | −0.020* | −0.037* |
| mean_reversion | +0.010 | +0.009 | +0.019* | **+0.033*** |
| volume | −0.015 | −0.009 | −0.001 | −0.012 |
| momentum | −0.004 | +0.006 | −0.003 | −0.005 |

`*` = |t| > 2.

**Les trois facteurs de suivi de tendance prédisent à l'envers**, et le signe
se confirme sur les quatre horizons. Une lecture haussière du prix est suivie,
en moyenne, d'un rendement négatif. Le seul facteur dont le signe était
correct est le retour à la moyenne. Voilà pourquoi le score ne discriminait
pas : son facteur dominant était inversé.

Quatre changements en découlent, tous mesurés plutôt que supposés :

1. **Les pondérations sont dérivées des coefficients**, avec les signes
   mesurés et un poids nul pour les facteurs non significatifs. Le score
   composite passe d'un pouvoir prédictif nul à **+0.063 à 48 bougies
   (t = 8.30)**.
2. **Un seul jeu de pondérations remplace les quatre jeux par régime.** Seuls
   « chaotique » et « range » présentaient des coefficients significatifs, et
   avec les mêmes signes que la mesure globale : quatre jeux revenaient à
   ajuster des paramètres sur du bruit. Le régime module toujours la
   confiance, mais plus la hiérarchie des facteurs.
3. **`SCORE_SCALE` est recalibré** sur le 90e percentile mesuré (0.628).
   Conservé après l'inversion des poids, l'ancien calibrage plaçait 73,5 % des
   lectures au-dessus du seuil.
4. **`MAX_EDGE` passe à 0.12**, dérivé de l'excès de rendement observé
   au-delà du seuil (+0.155 ATR sur 24 bougies, soit ~3.1 points de
   probabilité), avec une décote de moitié.

**La limite qui subsiste est de nature économique, pas statistique.**
L'avantage mesuré (~3 points de probabilité) est du même ordre que les frais
de transaction sur un stop de 2 ATR. Le moteur ne retient donc que les rares
configurations où l'avantage les dépasse — 35 trades en sept mois sur quinze
actifs. C'est peu, mais c'est honnête : abaisser la barre reviendrait à
échanger un avantage réel contre des commissions.

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
- **Le backtest ne rejoue pas l'actualité.** Le flux de presse historique
  n'est pas reconstituable, donc le facteur de sentiment y est neutralisé :
  seule la partie technique est mesurée.
- **L'échantillon est petit et récent.** 207 trades sur quelques mois à
  quelques années, dans un seul contexte de marché. Sur-ajuster les constantes
  sur cet échantillon serait exactement l'erreur que le backtest sert à éviter,
  d'où des corrections volontairement prudentes.

## Avertissement

Analyse automatisée à but informatif. Ne constitue pas un conseil en
investissement. Les performances passées, réelles ou simulées, ne préjugent pas
des performances futures.
