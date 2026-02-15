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

echo "[1/7] Backing up local-only files"
if [[ -f .env ]]; then
  cp .env ".env.backup.$stamp"
  echo "  - Backed up .env -> .env.backup.$stamp"
else
  echo "  - Skipped .env backup (.env not found)"
fi

if [[ -f data/bot.sqlite3 ]]; then
  cp data/bot.sqlite3 "data/bot.sqlite3.backup.$stamp"
  echo "  - Backed up DB -> data/bot.sqlite3.backup.$stamp"
else
  echo "  - Skipped DB backup (data/bot.sqlite3 not found)"
fi

echo "[2/7] Fetch latest git refs"
git fetch --all --prune

echo "[3/7] Pull latest changes"
git pull --ff-only

echo "[4/7] Install/update Python dependencies"
"$VENV_DIR/bin/pip" install -r requirements.txt

echo "[5/7] Enforce bot-only rollout (Mini App disabled)"
if [[ -f .env ]] && grep -Eq '^[[:space:]]*MINI_APP_URL[[:space:]]*=[[:space:]]*[^[:space:]#]+' .env; then
  echo "ERROR: MINI_APP_URL is set to a non-empty value in .env." >&2
  echo "For bot-only rollout, remove/comment MINI_APP_URL first, then rerun." >&2
  exit 1
fi

echo "[6/7] Restart systemd service: $SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "[7/7] Verify service health"
sudo systemctl status "$SERVICE_NAME" --no-pager
sudo journalctl -u "$SERVICE_NAME" -n 120 --no-pager

echo "Done. Bot-only update completed."
