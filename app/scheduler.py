from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import Application

from app.db import Database
from app.settings import AppConfig

LOGGER = logging.getLogger(__name__)


def format_display_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def build_header_text(
    title: str,
    author: str,
    start_date_iso: str,
    finish_date_iso: str | None = None,
) -> str:
    start_display = format_display_date(parse_iso_date(start_date_iso))
    if finish_date_iso:
        finish_display = format_display_date(parse_iso_date(finish_date_iso))
    else:
        finish_display = "..."
    return f"📚 {title} — {author} ({start_display} → {finish_display})"


def build_reminder_text(reminder_date: date, from_page: int, to_page: int) -> str:
    return f"📅 {format_display_date(reminder_date)} — Read pages {from_page}–{to_page}"


def build_reminder_keyboard(
    reminder_id: int,
    book_id: int,
    is_paused: bool,
) -> InlineKeyboardMarkup:
    pause_label = "▶️ Resume" if is_paused else "⏸️ Pause"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Mark as read",
                    callback_data=f"mark_read:{reminder_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "✍️ I read different pages",
                    callback_data=f"read_different:{reminder_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    pause_label,
                    callback_data=f"toggle_pause:{book_id}",
                ),
            ],
        ],
    )


def parse_time_hh_mm(raw_value: str) -> tuple[int, int]:
    parts = raw_value.split(":")
    if len(parts) != 2:
        raise ValueError("Time must be in HH:MM format")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Time must be in HH:MM 24-hour format")
    return hour, minute


class ReminderScheduler:
    JOB_ID = "daily-reading-reminders"

    def __init__(
        self,
        application: Application,
        db: Database,
        config: AppConfig,
    ) -> None:
        self.application = application
        self.db = db
        self.config = config
        self.scheduler = AsyncIOScheduler(timezone=config.timezone)
        self._started = False

    async def start(self) -> None:
        await self.refresh_schedule()
        self.scheduler.start()
        self._started = True
        await self.run_for_today()
        LOGGER.info("Scheduler started")

    async def shutdown(self) -> None:
        if self._started:
            self.scheduler.shutdown(wait=False)
            self._started = False
            LOGGER.info("Scheduler stopped")

    async def refresh_schedule(self) -> None:
        reminder_time = await self.db.get_setting("reminder_time") or "08:00"
        try:
            hour, minute = parse_time_hh_mm(reminder_time)
        except ValueError:
            LOGGER.warning("Invalid reminder_time=%s, falling back to 08:00", reminder_time)
            hour, minute = 8, 0

        self.scheduler.add_job(
            self.run_for_today,
            trigger="cron",
            hour=hour,
            minute=minute,
            id=self.JOB_ID,
            replace_existing=True,
            misfire_grace_time=3600,
        )
        LOGGER.info("Reminder schedule loaded at %02d:%02d %s", hour, minute, self.config.timezone_name)

    async def run_for_today(self) -> None:
        today = datetime.now(self.config.timezone).date()
        await self.run_for_date(today)

    async def run_for_date(self, target_date: date) -> None:
        channel_id_raw = await self.db.get_setting("channel_id")
        if not channel_id_raw:
            LOGGER.warning("Skipping reminders: channel_id is not configured")
            return

        try:
            channel_id = int(channel_id_raw)
        except ValueError:
            LOGGER.error("Skipping reminders: channel_id is invalid (%s)", channel_id_raw)
            return

        algo_settings = await self._get_algorithm_settings()
        books = await self.db.get_active_books()
        LOGGER.info("Running reminders for %s. Active books: %d", target_date.isoformat(), len(books))

        for book in books:
            try:
                await self._process_book_for_date(
                    book=book,
                    target_date=target_date,
                    channel_id=channel_id,
                    algo_settings=algo_settings,
                )
            except Exception:
                LOGGER.exception("Failed processing book_id=%s", book["id"])

    async def _get_algorithm_settings(self) -> dict[str, int]:
        values = await self.db.get_settings(
            keys=("start_pages", "weekly_increment", "increment_every_days"),
        )
        start_pages = self._parse_positive_int(values.get("start_pages"), fallback=10)
        weekly_increment = self._parse_positive_int(values.get("weekly_increment"), fallback=5)
        increment_every_days = self._parse_positive_int(values.get("increment_every_days"), fallback=7)
        return {
            "start_pages": start_pages,
            "weekly_increment": weekly_increment,
            "increment_every_days": increment_every_days,
        }

    def _parse_positive_int(self, raw_value: str | None, fallback: int) -> int:
        if raw_value is None:
            return fallback
        try:
            parsed = int(raw_value)
        except ValueError:
            LOGGER.warning("Invalid integer setting value '%s', using %s", raw_value, fallback)
            return fallback
        if parsed <= 0:
            LOGGER.warning("Non-positive setting value '%s', using %s", raw_value, fallback)
            return fallback
        return parsed

    async def _process_book_for_date(
        self,
        book: dict[str, Any],
        target_date: date,
        channel_id: int,
        algo_settings: dict[str, int],
    ) -> None:
        start_date = parse_iso_date(str(book["start_date"]))
        if target_date < start_date:
            return

        if int(book["last_read_page"]) >= int(book["total_pages"]):
            await self._finish_book_if_needed(book, target_date.isoformat(), channel_id)
            return

        from_page, to_page, pages_for_date = self._calculate_daily_range(
            book=book,
            target_date=target_date,
            start_pages=algo_settings["start_pages"],
            weekly_increment=algo_settings["weekly_increment"],
            increment_every_days=algo_settings["increment_every_days"],
        )

        if from_page > int(book["total_pages"]):
            await self._finish_book_if_needed(book, target_date.isoformat(), channel_id)
            return

        reminder, _created = await self.db.create_or_get_reminder(
            book_id=int(book["id"]),
            reminder_date=target_date.isoformat(),
            from_page=from_page,
            to_page=to_page,
            pages_planned=pages_for_date,
        )

        if reminder["status"] != "pending":
            return
        if reminder["channel_message_id"] is not None:
            return

        message_text = build_reminder_text(target_date, from_page, to_page)
        keyboard = build_reminder_keyboard(
            reminder_id=int(reminder["id"]),
            book_id=int(book["id"]),
            is_paused=False,
        )
        sent_message = await self.application.bot.send_message(
            chat_id=channel_id,
            text=message_text,
            reply_markup=keyboard,
        )
        await self.db.set_reminder_channel_message(
            reminder_id=int(reminder["id"]),
            message_id=int(sent_message.message_id),
        )
        LOGGER.info(
            "Posted reminder book_id=%s reminder_id=%s message_id=%s",
            book["id"],
            reminder["id"],
            sent_message.message_id,
        )

    def _calculate_daily_range(
        self,
        book: dict[str, Any],
        target_date: date,
        start_pages: int,
        weekly_increment: int,
        increment_every_days: int,
    ) -> tuple[int, int, int]:
        start_date = parse_iso_date(str(book["start_date"]))
        delta_days = (target_date - start_date).days
        week_index = max(0, delta_days // increment_every_days)
        pages_for_date = start_pages + weekly_increment * week_index
        from_page = int(book["last_read_page"]) + 1
        to_page = min(from_page + pages_for_date - 1, int(book["total_pages"]))
        return from_page, to_page, pages_for_date

    async def _finish_book_if_needed(
        self,
        book: dict[str, Any],
        finish_date_iso: str,
        channel_id: int,
    ) -> None:
        changed = await self.db.finish_book(
            book_id=int(book["id"]),
            finish_date=finish_date_iso,
            last_read_page=int(book["total_pages"]),
        )
        if not changed:
            return

        header_message_id = book["header_message_id"]
        if header_message_id is not None:
            try:
                await self.application.bot.edit_message_text(
                    chat_id=channel_id,
                    message_id=int(header_message_id),
                    text=build_header_text(
                        title=str(book["title"]),
                        author=str(book["author"]),
                        start_date_iso=str(book["start_date"]),
                        finish_date_iso=finish_date_iso,
                    ),
                )
            except TelegramError:
                LOGGER.exception("Failed to edit header for book_id=%s", book["id"])

        try:
            await self.application.bot.send_message(
                chat_id=channel_id,
                text=f"✅ Finished: {book['title']} — {book['author']}",
            )
        except TelegramError:
            LOGGER.exception("Failed to post finish message for book_id=%s", book["id"])
