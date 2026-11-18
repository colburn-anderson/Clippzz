#!/usr/bin/env bash
# Keeps the Pi on the latest main. Run by cron every 5 minutes (see below).
# Does nothing, and writes nothing, when main has not changed.
#   Install:  (crontab -l 2>/dev/null; echo '*/5 * * * * $HOME/clippzz/scripts/pi-update.sh') | crontab -
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

remote=$(git ls-remote origin refs/heads/main | cut -f1)
local_head=$(git rev-parse HEAD)
[ -n "$remote" ] || exit 0
[ "$remote" = "$local_head" ] && exit 0

git fetch -q origin main
changed=$(git diff --name-only HEAD origin/main)
git merge -q --ff-only origin/main

if echo "$changed" | grep -qE '^(Dockerfile|requirements\.txt)$'; then
  docker compose up -d --build
else
  docker compose up -d
  docker compose restart
fi
echo "$(date -Is) updated $local_head -> $remote"
