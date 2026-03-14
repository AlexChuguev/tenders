#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/var/www/tenders}"
REPO_SSH_URL="${REPO_SSH_URL:-git@github.com:AlexChuguev/tenders.git}"
DEPLOY_KEY_PATH="${DEPLOY_KEY_PATH:-/root/.ssh/tenders_github}"
BRANCH="${BRANCH:-main}"
KNOWN_HOSTS_PATH="${KNOWN_HOSTS_PATH:-/root/.ssh/known_hosts}"
GITHUB_KNOWN_HOSTS='github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=
github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk='

if [[ ! -d "$PROJECT_DIR/.git" ]]; then
  echo "Git repository was not found in $PROJECT_DIR" >&2
  exit 1
fi

if [[ ! -f "$DEPLOY_KEY_PATH" ]]; then
  echo "Deploy key was not found: $DEPLOY_KEY_PATH" >&2
  exit 1
fi

mkdir -p /root/.ssh
touch "$KNOWN_HOSTS_PATH"
chmod 600 "$DEPLOY_KEY_PATH" "$KNOWN_HOSTS_PATH"
if ! ssh-keygen -F github.com -f "$KNOWN_HOSTS_PATH" >/dev/null 2>&1; then
  printf '%s\n' "$GITHUB_KNOWN_HOSTS" >> "$KNOWN_HOSTS_PATH"
fi

cd "$PROJECT_DIR"

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REPO_SSH_URL"
else
  git remote add origin "$REPO_SSH_URL"
fi

export GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY_PATH -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$KNOWN_HOSTS_PATH"

git fetch origin "$BRANCH"
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"
