from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None


DEFAULT_TIMEZONE = "Asia/Tashkent"
DEFAULT_DB_PATH = Path("data/bot.sqlite3")
DEFAULT_RUNTIME_SETTINGS = {
    "reminder_time": "08:00",
    "timezone": DEFAULT_TIMEZONE,
    "start_pages": "10",
    "weekly_increment": "5",
    "increment_every_days": "7",
}


@dataclass(slots=True)
class AppConfig:
    bot_token: str
    owner_user_id: int
    admin_user_ids: tuple[int, ...]
    db_path: Path
    timezone_name: str
    timezone: ZoneInfo
    channel_link: str | None = None
    mini_app_url: str | None = None


def _parse_admin_ids(raw: str) -> tuple[int, ...]:
    values: set[int] = set()
    for part in (raw or "").split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        try:
            values.add(int(cleaned))
        except ValueError:
            continue
    return tuple(sorted(values))


def load_config() -> AppConfig:
    if load_dotenv is not None:
        load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    owner_value = os.getenv("OWNER_USER_ID", "").strip()
    if not owner_value:
        raise RuntimeError("OWNER_USER_ID is required")
    try:
        owner_user_id = int(owner_value)
    except ValueError as exc:
        raise RuntimeError("OWNER_USER_ID must be an integer") from exc

    db_path_str = os.getenv("DB_PATH", "").strip()
    db_path = Path(db_path_str) if db_path_str else DEFAULT_DB_PATH

    timezone_name = os.getenv("TZ", "").strip() or DEFAULT_TIMEZONE
    timezone = ZoneInfo(timezone_name)

    channel_link = os.getenv("CHANNEL_link", "").strip() or os.getenv(
        "CHANNEL_LINK",
        "",
    ).strip()
    if not channel_link:
        channel_link = None

    admin_user_ids = set(_parse_admin_ids(os.getenv("ADMIN_TELEGRAM_IDS", "")))
    admin_user_ids.add(owner_user_id)

    mini_app_url = os.getenv("MINI_APP_URL", "").strip() or None

    return AppConfig(
        bot_token=token,
        owner_user_id=owner_user_id,
        admin_user_ids=tuple(sorted(admin_user_ids)),
        db_path=db_path,
        timezone_name=timezone_name,
        timezone=timezone,
        channel_link=channel_link,
        mini_app_url=mini_app_url,
    )
