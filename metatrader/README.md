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
| `MinScore` | 68 | Conviction minimale retenue |
| `RefreshSeconds` | 300 | Intervalle d'interrogation |
| `AutoTrade` | **false** | Exécution réelle — désactivée par défaut |
| `MaxRiskPercent` | 1.0 | Risque maximal par position, en % du solde |
| `MaxTotalRisk` | 5.0 | Risque cumulé maximal |
| `MaxPositions` | 5 | Positions simultanées maximales |
| `MagicNumber` | 20260825 | Identifiant des ordres de cet EA |

## Ce que fait l'EA

- il interroge l'API à intervalle régulier et ignore un scan déjà traité,
  pour ne pas rouvrir la même position à chaque cycle ;
- il affiche les configurations sur le graphique et les journalise ;
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
