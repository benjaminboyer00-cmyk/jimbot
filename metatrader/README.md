# Connecteur MetaTrader 5

`JimbotConnector.mq5` lit le flux de signaux Jimbot et l'affiche dans
MetaTrader. **Par défaut, il ne transmet aucun ordre.**

## Installation

1. Copier `JimbotConnector.mq5` dans
   `MQL5/Experts/` de votre terminal (menu *Fichier → Ouvrir le dossier de
   données*).
2. Dans MetaEditor, ouvrir le fichier et compiler (F7).
3. Dans MetaTrader : **Outils → Options → Expert Advisors** → cocher
   **« Autoriser les WebRequest pour les URL listées »** et ajouter le domaine
   de votre déploiement, par exemple `https://jimbot-seven.vercel.app`.
   Sans cette autorisation, `WebRequest` échoue avec l'erreur 4014.
4. Glisser l'EA sur un graphique, n'importe lequel : il interroge l'API pour
   tous les instruments, pas seulement celui du graphique.

## Paramètres

| Paramètre | Défaut | Rôle |
|---|---|---|
| `ApiUrl` | `.../api/mt` | Point d'entrée du flux |
| `Mode` | `actionable` | `actionable`, `watchlist` ou `all` |
| `MinScore` | **0** | 0 = suivre le seuil publié par l'API. Voir ci-dessous |
| `RefreshSeconds` | 300 | Intervalle d'interrogation |
| `AutoTrade` | **false** | Exécution réelle — désactivée par défaut |
| `MaxRiskPercent` | 1.0 | Risque maximal par position, en % du solde |
| `MaxTotalRisk` | 5.0 | Risque cumulé maximal |
| `MaxPositions` | 5 | Positions simultanées maximales |
| `MagicNumber` | 20260825 | Identifiant des ordres de cet EA |
| `NotifyMobile` | true | Pousser chaque signal vers l'application mobile |
| `SelfTestOnDemo` | **false** | Auto-test de bout en bout, compte démo uniquement |

## Le seuil, et pourquoi il valait 68

`MinScore` valait 68 dans les versions précédentes. C'était le seuil
d'**alerte Discord**, pas le seuil de **signal**, et les deux ne servent pas à
la même chose.

Mesuré sur les 3 182 relevés de `data/history.json` :

| Seuil | Part des relevés qui le franchissent |
|---|---|
| 58 — seuil de signal | 5,6 % |
| 68 — seuil d'alerte | **0,06 %**, soit 2 relevés sur 3 182 |

Un EA réglé sur 68 ne prenait donc pratiquement jamais de position. Le
comportement ressemblait à une panne alors que c'était un réglage.

`MinScore = 0` fait suivre `thresholds.signal`, que l'API publie et qui vient
du scan lui-même : si le seuil du moteur bouge, l'EA suit sans recompilation.
Une valeur strictement positive continue de forcer un seuil manuel.

## Vérifier que tout fonctionne, sur un compte démo

Le moteur ne franchit son seuil que 5,6 % du temps, et l'EA ignore les plans à
espérance négative. Conséquence : vous installez tout correctement, et il ne se
passe **rien** pendant deux jours. Impossible de distinguer « ça marche et il
n'y a rien à prendre » de « c'est cassé ».

`SelfTestOnDemo` répond à cette question en une minute. Il prend le meilleur
plan disponible, ouvre une position au **volume minimal du courtier**, et
journalise les cinq étapes :

```
AUTO-TEST 1/5 : API joignable, 4213 octets reçus.
AUTO-TEST 2/5 : plan lu — XAUUSD BUY  SL 4436.96  TP 4555.88
AUTO-TEST 3/5 : symbole reconnu chez ce courtier — XAUUSD
AUTO-TEST 4/5 : volume minimal du courtier — 0.01 lot(s).
AUTO-TEST 5/5 : RÉUSSI — position ouverte sur XAUUSD, ticket 12345678.
```

Chaque étape qui échoue dit laquelle et pourquoi : domaine non autorisé,
instrument absent chez le courtier, marché fermé.

**Il refuse de s'exécuter sur un compte réel.** La vérification porte sur
`ACCOUNT_TRADE_MODE`, que renseigne le serveur du courtier — ce n'est pas une
case à cocher côté client. Sur un compte réel, il journalise le refus et
n'ouvre rien.

La position ouverte n'est **pas** surveillée par l'auto-test : elle porte son
stop et son objectif, mais fermez-la à la main quand vous avez vu ce que vous
vouliez voir. Repassez `SelfTestOnDemo` à `false` ensuite, sinon il rouvre une
position à chaque rechargement de l'EA.

## Trader depuis le téléphone, sans aucune machine allumée

C'est le montage qui répond vraiment à « je veux que les trades du bot arrivent
sur mon téléphone ». Il n'utilise pas l'Expert Advisor du tout.

Le problème est structurel : l'app mobile n'exécute aucun code, et GitHub
Actions ne peut pas faire tourner un terminal MetaTrader. Il manque la pièce
qui passe les ordres. **MetaApi** est cette pièce — un service qui tient une
connexion permanente à un compte MetaTrader et l'expose en REST.

```
GitHub Actions  ──POST /trade──>  MetaApi  ──>  compte MT5  ──>  app mobile
   (le moteur)                    (le pont)      (le courtier)    (vous)
```

### Mise en place

1. **Un compte MT5 de démonstration.** Chez un courtier, ou créé par l'API de
   provisionnement de MetaApi, qui renvoie login, mot de passe et serveur.
2. **Un compte MetaApi** sur `app.metaapi.cloud` : y ajouter le compte MT5,
   relever le jeton et l'identifiant de compte. Service payant.
3. **Vérifier avant d'armer**, en local :

   ```bash
   export METAAPI_TOKEN=... METAAPI_ACCOUNT_ID=...
   python engine/broker_run.py --check     # lit le compte, ne touche à rien
   python engine/broker_run.py --dry-run   # calcule les ordres, n'en passe aucun
   ```

   `--check` dit si le jeton fonctionne, si le compte est bien en démonstration,
   et **lesquels de vos instruments ce courtier connaît** — la nomenclature
   varie, et un instrument absent est ignoré sans que les autres en souffrent.
4. **Armer**, dans les réglages du dépôt GitHub :
   - secrets `METAAPI_TOKEN`, `METAAPI_ACCOUNT_ID`
   - variable `JIMBOT_BROKER` à `1`

   `JIMBOT_BROKER` est une *variable* et non un secret, délibérément : c'est un
   interrupteur, et l'on doit pouvoir lire dans les réglages si l'exécution est
   armée.
5. **Sur le téléphone**, se connecter à ce compte dans l'app MT5. Les positions
   ouvertes par le moteur y apparaissent.

### Ce qui protège le compte

| Garde-fou | Comportement |
|---|---|
| Compte réel | **Refus**, sauf `JIMBOT_BROKER_ALLOW_LIVE` explicite. Le type vient du serveur du courtier, pas d'un réglage local |
| Interrupteur éteint | Rien ne part, même avec un jeton valide |
| Espérance négative | Signal ignoré |
| Signal déjà ouvert | Un `clientId` stable par scan empêche de rouvrir la même position tous les quarts d'heure |
| Volume sous le lot minimal | On n'ouvre rien, plutôt que d'arrondir vers le haut et dépasser le risque |
| Plafond de positions | `JIMBOT_BROKER_MAX_POSITIONS`, 5 par défaut |
| Courtier injoignable | Journalisé, le scan se termine normalement |

### Ce à quoi s'attendre sur un petit compte

Avec 10 000 EUR, l'or à 4 000 et un stop à 40 points, le moteur dimensionne
0,6 once. Sur un contrat de 100 onces, cela fait 0,006 lot — **sous le lot
minimal de 0,01**, donc aucun ordre. Ce n'est pas une panne : c'est le
dimensionnement par le risque qui refuse de prendre seize fois la somme prévue.
Les instruments à petit contrat (CFD) passent, les gros contrats non.

## Recevoir les signaux sur un téléphone

**L'application mobile MetaTrader 5 n'exécute pas d'Expert Advisor.** C'est une
limite de la plateforme sur iOS comme sur Android : l'application affiche les
graphiques, les positions et permet de passer des ordres à la main, mais elle
ne fait tourner ni EA, ni indicateur, ni script. Aucun réglage ne le change.

Trois montages possibles, du plus simple au plus complet :

1. **Sans MetaTrader du tout.** Jimbot publie déjà ses alertes sur Discord, qui
   fonctionne sur téléphone. C'est le seul montage qui ne demande aucune
   machine allumée.
2. **Notifications poussées.** L'EA tourne sur un terminal de bureau et pousse
   chaque signal vers l'application mobile (`NotifyMobile`). Relever
   l'identifiant MetaQuotes dans l'application (*Réglages → Messages*), le
   coller dans *Outils → Options → Notifications* du terminal de bureau, et
   cocher l'activation. Le téléphone reçoit le signal ; c'est vous qui passez
   l'ordre, depuis l'application.
3. **Exécution automatique.** L'EA tourne avec `AutoTrade` sur un VPS Windows
   allumé en permanence, connecté au même compte courtier. L'application mobile
   voit alors les positions ouvertes par l'EA et permet de les fermer ou de les
   modifier. Un ordinateur personnel éteint la nuit ne convient pas : l'EA ne
   tourne que quand le terminal tourne.

Dans les trois cas, c'est le terminal de bureau ou le VPS qui décide ; le
téléphone n'est qu'une fenêtre sur ce qu'il fait.

## Ce que fait l'EA

- il interroge l'API à intervalle régulier et ignore un scan déjà traité,
  pour ne pas rouvrir la même position à chaque cycle ;
- il affiche les configurations sur le graphique et les journalise ;
- si `NotifyMobile` est activé, il pousse chaque configuration vers
  l'application mobile, une seule fois par instrument et par sens — le scan
  réémet la même configuration tant qu'elle tient, et sans cette mémoire le
  téléphone sonnerait à chaque cycle pour la même chose ;
- si `AutoTrade` est activé, il ouvre les positions correspondantes avec le
  stop et l'objectif fournis.

Le volume est **toujours** déduit de la distance au stop et du risque accepté,
jamais d'un nombre de lots fixe : c'est la seule façon de garantir qu'une
perte coûte le montant prévu quel que soit l'instrument. Si le risque voulu
correspond à un volume inférieur au lot minimal du courtier, l'EA n'ouvre
rien plutôt que d'arrondir vers le haut.

Un signal dont l'espérance est négative est ignoré même en mode automatique.

## Noms d'instruments

Il n'existe aucune nomenclature standard : le S&P 500 est `US500` chez un
courtier, `SPX500` ou `USA500` chez un autre, et les paires portent souvent un
suffixe (`.r`, `m`, `_i`). L'API renvoie plusieurs alias par instrument et
l'EA retient le premier que votre courtier reconnaît. Si aucun ne correspond,
le signal est journalisé puis ignoré.

## Avertissement

Ce connecteur transmet des ordres sur votre compte lorsque vous l'activez.
Testez-le d'abord sur un compte de démonstration. Les signaux sont
informatifs et ne constituent pas un conseil en investissement ; l'exécution
relève entièrement de votre responsabilité.
