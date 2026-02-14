from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Telegram channel ID using Bot API getChat.",
    )
    parser.add_argument(
        "--chat",
        default="@masharipovs_notes",
        help="Channel username (e.g. @my_channel) or channel ID (e.g. -100...).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP timeout in seconds (default: 20).",
    )
    return parser.parse_args()


def load_env() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    dotenv_path = repo_root / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)


def build_get_chat_url(token: str, chat: str) -> str:
    query = urllib.parse.urlencode({"chat_id": chat})
    return f"https://api.telegram.org/bot{token}/getChat?{query}"


def main() -> int:
    args = parse_args()
    load_env()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print(
            "ERROR: TELEGRAM_BOT_TOKEN is missing. Set it in .env or environment.",
            file=sys.stderr,
        )
        return 1

    url = build_get_chat_url(token=token, chat=args.chat)

    try:
        with urllib.request.urlopen(url, timeout=args.timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
            description = data.get("description") or str(exc)
        except json.JSONDecodeError:
            description = str(exc)
        print(f"ERROR: {description}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"ERROR: Network issue: {exc.reason}", file=sys.stderr)
        return 1
    except TimeoutError:
        print("ERROR: Request timed out.", file=sys.stderr)
        return 1

    if not payload.get("ok"):
        description = payload.get("description", "Unknown Telegram API error")
        print(f"ERROR: {description}", file=sys.stderr)
        return 1

    result = payload.get("result", {})
    chat_id = result.get("id")
    if not isinstance(chat_id, int):
        print("ERROR: Unexpected Telegram API response format.", file=sys.stderr)
        return 1

    print(chat_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

