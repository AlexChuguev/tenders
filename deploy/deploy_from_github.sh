#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/var/www/tenders}"
REPO_SSH_URL="${REPO_SSH_URL:-git@github.com:AlexChuguev/tenders.git}"
DEPLOY_KEY_PATH="${DEPLOY_KEY_PATH:-/root/.ssh/tenders_github}"
BRANCH="${BRANCH:-main}"

if [[ ! -d "$PROJECT_DIR/.git" ]]; then
  echo "Git repository was not found in $PROJECT_DIR" >&2
  exit 1
fi

if [[ ! -f "$DEPLOY_KEY_PATH" ]]; then
  echo "Deploy key was not found: $DEPLOY_KEY_PATH" >&2
  exit 1
fi

cd "$PROJECT_DIR"

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REPO_SSH_URL"
else
  git remote add origin "$REPO_SSH_URL"
fi

export GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY_PATH -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

git fetch origin "$BRANCH"
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"
