#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-$HOME/library_manage_bot}"
SERVICE_NAME="${SERVICE_NAME:-librarybot.service}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/venv}"
stamp="$(date +%F_%H%M%S)"

if [[ ! -d "$ROOT_DIR" ]]; then
  echo "ERROR: Root directory not found: $ROOT_DIR" >&2
  exit 1
fi

if [[ ! -d "$ROOT_DIR/.git" ]]; then
  echo "ERROR: Not a git repository: $ROOT_DIR" >&2
  exit 1
fi

if [[ ! -x "$VENV_DIR/bin/pip" ]]; then
  echo "ERROR: pip not found in virtualenv: $VENV_DIR/bin/pip" >&2
  exit 1
fi

cd "$ROOT_DIR"

echo "[1/6] Backup local files"
if [[ -f .env ]]; then
  cp .env ".env.backup.$stamp"
  echo "  - Backed up .env -> .env.backup.$stamp"
else
  echo "ERROR: .env not found in $ROOT_DIR" >&2
  exit 1
fi

if [[ -f data/bot.sqlite3 ]]; then
  cp data/bot.sqlite3 "data/bot.sqlite3.backup.$stamp"
  echo "  - Backed up DB -> data/bot.sqlite3.backup.$stamp"
fi

echo "[2/6] Pull latest code"
git fetch --all --prune
git pull --ff-only

echo "[3/6] Install/update dependencies"
"$VENV_DIR/bin/pip" install -r requirements.txt

echo "[4/6] Validate MINI_APP_URL"
if ! grep -Eq '^[[:space:]]*MINI_APP_URL[[:space:]]*=[[:space:]]*https://[^[:space:]#]+' .env; then
  echo "ERROR: MINI_APP_URL is missing or invalid in .env" >&2
  echo "Expected example: MINI_APP_URL=https://cheggo.uz/library/mini/" >&2
  exit 1
fi

echo "[5/6] Restart bot service"
sudo systemctl restart "$SERVICE_NAME"

echo "[6/6] Verify bot service"
sudo systemctl status "$SERVICE_NAME" --no-pager
sudo journalctl -u "$SERVICE_NAME" -n 120 --no-pager

echo "Done. Bot update with Mini App enabled completed."
