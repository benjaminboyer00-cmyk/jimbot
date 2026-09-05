#!/usr/bin/env bash
# Active les crochets versionnés de ce dépôt.
#
# `core.hooksPath` est une configuration *locale* : elle ne voyage pas avec le
# dépôt. Un clone frais n'a donc aucune protection tant que ceci n'a pas été
# lancé une fois — c'est la limite de tous les crochets git, et la raison pour
# laquelle ce script existe plutôt qu'une ligne perdue dans un README.
set -e
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit
echo "Crochets activés. Un commit contenant un jeton sera désormais refusé."
echo "Vérification :  git config core.hooksPath   ->  $(git config core.hooksPath)"
