from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.db import Database, utc_now_iso
from app.scheduler import (
    ReminderScheduler,
    build_header_text,
    build_reminder_keyboard,
    format_display_date,
    parse_iso_date,
    parse_time_hh_mm,
)
from app.settings import AppConfig

LOGGER = logging.getLogger(__name__)

(
    NEWBOOK_TITLE,
    NEWBOOK_AUTHOR,
    NEWBOOK_TOTAL_PAGES,
    NEWBOOK_START_DATE,
    NEWBOOK_START_PAGE,
) = range(5)


def parse_date_input(raw: str, default_date: date) -> date:
    value = raw.strip()
    if value in {"-", "skip", "today"}:
        return default_date
    for parser in (date.fromisoformat,):
        try:
            return parser(value)
        except ValueError:
            continue
    try:
        return datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError as exc:
        raise ValueError("Use YYYY-MM-DD or DD.MM.YYYY") from exc


def parse_page_range(raw: str) -> tuple[int, int]:
    cleaned = raw.strip().replace("–", "-")
    hyphen_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", cleaned)
    if hyphen_match:
        return int(hyphen_match.group(1)), int(hyphen_match.group(2))
    parts = cleaned.split()
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return int(parts[0]), int(parts[1])
    raise ValueError("Use format like 80-89 or '80 89'")


class ReadingTrackerBot:
    def __init__(
        self,
        db: Database,
        config: AppConfig,
        reminder_scheduler: ReminderScheduler,
    ) -> None:
        self.db = db
        self.config = config
        self.reminder_scheduler = reminder_scheduler

    def register_handlers(self, application: Application) -> None:
        newbook_conv = ConversationHandler(
            entry_points=[CommandHandler("newbook", self.newbook_start)],
            states={
                NEWBOOK_TITLE: [
                    MessageHandler(
                        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
                        self.newbook_title,
                    ),
                ],
                NEWBOOK_AUTHOR: [
                    MessageHandler(
                        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
                        self.newbook_author,
                    ),
                ],
                NEWBOOK_TOTAL_PAGES: [
                    MessageHandler(
                        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
                        self.newbook_total_pages,
                    ),
                ],
                NEWBOOK_START_DATE: [
                    MessageHandler(
                        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
                        self.newbook_start_date,
                    ),
                ],
                NEWBOOK_START_PAGE: [
                    MessageHandler(
                        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
                        self.newbook_start_page,
                    ),
                ],
            },
            fallbacks=[CommandHandler("cancel", self.newbook_cancel)],
            allow_reentry=True,
        )
        application.add_handler(newbook_conv)
        application.add_handler(CommandHandler("start", self.command_start))
        application.add_handler(CommandHandler("setchannel", self.command_setchannel))
        application.add_handler(CommandHandler("list", self.command_list))
        application.add_handler(CommandHandler("progress", self.command_progress))
        application.add_handler(CommandHandler("pause", self.command_pause))
        application.add_handler(CommandHandler("resume", self.command_resume))
        application.add_handler(CommandHandler("settings", self.command_settings))
        application.add_handler(CommandHandler("cancel", self.command_cancel_pending))

        application.add_handler(
            CallbackQueryHandler(self.callback_mark_read, pattern=r"^mark_read:\d+$"),
        )
        application.add_handler(
            CallbackQueryHandler(
                self.callback_read_different,
                pattern=r"^read_different:\d+$",
            ),
        )
        application.add_handler(
            CallbackQueryHandler(self.callback_toggle_pause, pattern=r"^toggle_pause:\d+$"),
        )
        application.add_handler(
            CallbackQueryHandler(
                self.callback_settings_edit,
                pattern=r"^settings_edit:[a-z_]+$",
            ),
        )
        application.add_handler(
            MessageHandler(
                filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
                self.handle_pending_input,
            ),
            group=1,
        )
        application.add_error_handler(self.error_handler)

    async def _ensure_owner_private(self, update: Update) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        if user is None or user.id != self.config.owner_user_id:
            if update.message:
                await update.message.reply_text("Access denied.")
            return False
        if chat is None or chat.type != ChatType.PRIVATE:
            if update.message:
                await update.message.reply_text("Use this command in private chat.")
            return False
        return True

    async def _ensure_owner_callback(self, update: Update) -> bool:
        query = update.callback_query
        if query is None:
            return False
        if query.from_user.id != self.config.owner_user_id:
            await query.answer("Not allowed.", show_alert=True)
            return False
        return True

    async def _get_channel_id(self) -> int | None:
        channel_raw = await self.db.get_setting("channel_id")
        if channel_raw is None:
            return None
        try:
            return int(channel_raw)
        except ValueError:
            return None

    def _help_text(self) -> str:
        return (
            "Reading Tracker Bot commands:\n"
            "/start - Show this help\n"
            "/setchannel <channel_id> - Set target channel ID\n"
            "/newbook - Start new book wizard\n"
            "/list - List active/paused books\n"
            "/progress <book_id> - Show detailed progress\n"
            "/pause <book_id> - Pause reminders for a book\n"
            "/resume <book_id> - Resume reminders for a book\n"
            "/settings - Show/edit reminder settings\n"
            "/cancel - Cancel pending input"
        )

    async def command_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_owner_private(update):
            return
        assert update.message is not None
        await update.message.reply_text(self._help_text())

    async def command_setchannel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_owner_private(update):
            return
        assert update.message is not None

        if not context.args:
            await update.message.reply_text("Usage: /setchannel -1001234567890")
            return
        raw_channel_id = context.args[0].strip()
        try:
            channel_id = int(raw_channel_id)
        except ValueError:
            await update.message.reply_text("channel_id must be a numeric ID like -100...")
            return

        await self.db.set_setting("channel_id", str(channel_id))
        await update.message.reply_text(f"Channel saved: {channel_id}")

    async def command_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_owner_private(update):
            return
        assert update.message is not None

        books = await self.db.list_books(statuses=("active", "paused"))
        if not books:
            await update.message.reply_text("No active or paused books.")
            return

        lines = ["Books:"]
        for book in books:
            start_display = format_display_date(parse_iso_date(str(book["start_date"])))
            lines.append(
                f"#{book['id']} [{book['status']}] {book['title']} — {book['author']}\n"
                f"Progress: {book['last_read_page']}/{book['total_pages']} | Start: {start_display}"
            )
        await update.message.reply_text("\n\n".join(lines))

    async def command_progress(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_owner_private(update):
            return
        assert update.message is not None

        if not context.args:
            await update.message.reply_text("Usage: /progress <book_id>")
            return
        try:
            book_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("book_id must be an integer.")
            return

        book = await self.db.get_book(book_id)
        if not book:
            await update.message.reply_text("Book not found.")
            return

        today_iso = datetime.now(self.config.timezone).date().isoformat()
        today_reminder = await self.db.get_reminder_by_book_and_date(book_id, today_iso)
        latest_reminder = await self.db.get_latest_reminder(book_id)

        start_display = format_display_date(parse_iso_date(str(book["start_date"])))
        lines = [
            f"Book #{book['id']}: {book['title']} — {book['author']}",
            f"Status: {book['status']}",
            f"Progress: {book['last_read_page']}/{book['total_pages']}",
            f"Start: {start_display}",
        ]
        if book["last_read_date"]:
            lines.append(f"Last read date: {format_display_date(parse_iso_date(str(book['last_read_date'])))}")
        if today_reminder:
            lines.append(
                "Today reminder: "
                f"{today_reminder['from_page']}-{today_reminder['to_page']} "
                f"[{today_reminder['status']}]"
            )
        if latest_reminder:
            lines.append(
                "Latest reminder: "
                f"{latest_reminder['date']} "
                f"{latest_reminder['from_page']}-{latest_reminder['to_page']} "
                f"[{latest_reminder['status']}]"
            )
        await update.message.reply_text("\n".join(lines))

    async def command_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_book_status_command(update, context, target_status="paused")

    async def command_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_book_status_command(update, context, target_status="active")

    async def _set_book_status_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        target_status: str,
    ) -> None:
        if not await self._ensure_owner_private(update):
            return
        assert update.message is not None

        if not context.args:
            command_name = "pause" if target_status == "paused" else "resume"
            await update.message.reply_text(f"Usage: /{command_name} <book_id>")
            return

        try:
            book_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("book_id must be an integer.")
            return

        book = await self.db.get_book(book_id)
        if not book:
            await update.message.reply_text("Book not found.")
            return
        if book["status"] == "finished":
            await update.message.reply_text("Book is already finished.")
            return
        if book["status"] == target_status:
            await update.message.reply_text(f"Book is already {target_status}.")
            return

        await self.db.set_book_status(book_id, target_status)
        await update.message.reply_text(f"Book #{book_id} is now {target_status}.")

    async def command_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_owner_private(update):
            return
        assert update.message is not None

        text = await self._build_settings_text()
        await update.message.reply_text(text, reply_markup=self._settings_keyboard())

    async def callback_settings_edit(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._ensure_owner_callback(update):
            return
        query = update.callback_query
        assert query is not None

        if query.message is None or query.message.chat.type != ChatType.PRIVATE:
            await query.answer("Open private chat with bot for settings.", show_alert=True)
            return

        key = query.data.split(":", maxsplit=1)[1]
        context.user_data["pending_setting_key"] = key
        await query.answer()
        await query.message.reply_text(
            f"Send new value for {key}.\n"
            "Examples:\n"
            "- reminder_time: 08:00\n"
            "- start_pages: 10\n"
            "- weekly_increment: 5\n"
            "- increment_every_days: 7\n"
            "Use /cancel to abort.",
        )

    async def callback_mark_read(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._ensure_owner_callback(update):
            return
        query = update.callback_query
        assert query is not None

        reminder_id = int(query.data.split(":")[1])
        reminder = await self.db.get_reminder(reminder_id)
        if reminder is None:
            await query.answer("Reminder not found.", show_alert=True)
            return
        if reminder["status"] == "done":
            await query.answer("Already marked.", show_alert=True)
            return

        book = await self.db.get_book(int(reminder["book_id"]))
        if book is None:
            await query.answer("Book not found.", show_alert=True)
            return

        await self.db.mark_reminder_done(reminder_id=reminder_id, done_at=utc_now_iso())
        await self.db.update_book_progress(
            book_id=int(book["id"]),
            last_read_page=int(reminder["to_page"]),
            last_read_date=str(reminder["date"]),
        )
        await self._delete_channel_reminder_message(context, reminder)
        await self._finish_book_if_completed(
            context=context,
            book=book,
            finish_date_iso=str(reminder["date"]),
            new_last_read_page=int(reminder["to_page"]),
        )
        await query.answer("Marked as read.")

    async def callback_read_different(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._ensure_owner_callback(update):
            return
        query = update.callback_query
        assert query is not None

        reminder_id = int(query.data.split(":")[1])
        reminder = await self.db.get_reminder(reminder_id)
        if reminder is None:
            await query.answer("Reminder not found.", show_alert=True)
            return
        if reminder["status"] == "done":
            await query.answer("Reminder already done.", show_alert=True)
            return

        context.user_data["pending_read_different"] = reminder_id
        try:
            await context.bot.send_message(
                chat_id=self.config.owner_user_id,
                text=(
                    f"Reminder #{reminder_id}: send actual page range.\n"
                    "Example: 83-95\n"
                    "Use /cancel to abort."
                ),
            )
        except TelegramError:
            context.user_data.pop("pending_read_different", None)
            LOGGER.exception("Failed to send read_different prompt to owner")
            await query.answer("Open private chat with the bot first.", show_alert=True)
            return

        await query.answer("Send actual pages in private chat.", show_alert=True)

    async def callback_toggle_pause(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._ensure_owner_callback(update):
            return
        query = update.callback_query
        assert query is not None

        book_id = int(query.data.split(":")[1])
        book = await self.db.get_book(book_id)
        if not book:
            await query.answer("Book not found.", show_alert=True)
            return
        if book["status"] == "finished":
            await query.answer("Book is finished.", show_alert=True)
            return

        new_status = "paused" if book["status"] == "active" else "active"
        await self.db.set_book_status(book_id, new_status)

        reminder_id = self._extract_reminder_id_from_markup(query.message.reply_markup if query.message else None)
        if reminder_id is not None and query.message is not None:
            try:
                await query.edit_message_reply_markup(
                    reply_markup=build_reminder_keyboard(
                        reminder_id=reminder_id,
                        book_id=book_id,
                        is_paused=(new_status == "paused"),
                    ),
                )
            except BadRequest:
                LOGGER.debug("Reply markup unchanged for reminder_id=%s", reminder_id)
            except TelegramError:
                LOGGER.exception("Failed to edit inline keyboard for reminder_id=%s", reminder_id)

        await query.answer(f"Book {new_status}.")

    async def handle_pending_input(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._ensure_owner_private(update):
            return
        assert update.message is not None

        text = update.message.text.strip()
        pending_reminder_id = context.user_data.get("pending_read_different")
        if pending_reminder_id:
            await self._handle_pending_read_different(update, context, text, int(pending_reminder_id))
            return

        pending_setting_key = context.user_data.get("pending_setting_key")
        if pending_setting_key:
            await self._handle_pending_setting(update, context, str(pending_setting_key), text)
            return

    async def _handle_pending_read_different(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        raw_text: str,
        reminder_id: int,
    ) -> None:
        assert update.message is not None

        try:
            from_page, to_page = parse_page_range(raw_text)
        except ValueError as exc:
            await update.message.reply_text(f"{exc}. Try again or /cancel.")
            return

        reminder = await self.db.get_reminder(reminder_id)
        if reminder is None:
            context.user_data.pop("pending_read_different", None)
            await update.message.reply_text("Reminder no longer exists.")
            return
        if reminder["status"] == "done":
            context.user_data.pop("pending_read_different", None)
            await update.message.reply_text("Reminder already completed.")
            return

        book = await self.db.get_book(int(reminder["book_id"]))
        if book is None:
            context.user_data.pop("pending_read_different", None)
            await update.message.reply_text("Book no longer exists.")
            return

        if from_page > to_page:
            await update.message.reply_text("Invalid range: from_page cannot be greater than to_page.")
            return
        if from_page < int(book["start_page"]):
            await update.message.reply_text(f"from_page must be >= {book['start_page']}.")
            return
        if to_page > int(book["total_pages"]):
            await update.message.reply_text(f"to_page must be <= {book['total_pages']}.")
            return
        if to_page <= int(book["last_read_page"]):
            await update.message.reply_text(
                f"to_page must be > current progress ({book['last_read_page']}).",
            )
            return

        await self.db.mark_reminder_done(
            reminder_id=reminder_id,
            done_at=utc_now_iso(),
            from_page=from_page,
            to_page=to_page,
        )
        await self.db.update_book_progress(
            book_id=int(book["id"]),
            last_read_page=to_page,
            last_read_date=str(reminder["date"]),
        )
        await self._delete_channel_reminder_message(context, reminder)
        await self._finish_book_if_completed(
            context=context,
            book=book,
            finish_date_iso=str(reminder["date"]),
            new_last_read_page=to_page,
        )
        context.user_data.pop("pending_read_different", None)
        await update.message.reply_text(
            f"Saved actual pages: {from_page}-{to_page} for reminder #{reminder_id}.",
        )

    async def _handle_pending_setting(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        key: str,
        raw_text: str,
    ) -> None:
        assert update.message is not None
        value = raw_text.strip()

        try:
            if key == "reminder_time":
                parse_time_hh_mm(value)
                normalized_value = value
            elif key in {"start_pages", "weekly_increment", "increment_every_days"}:
                as_int = int(value)
                if as_int <= 0:
                    raise ValueError("Value must be > 0")
                normalized_value = str(as_int)
            else:
                raise ValueError("Unsupported setting key")
        except ValueError as exc:
            await update.message.reply_text(f"{exc}. Try again or /cancel.")
            return

        await self.db.set_setting(key, normalized_value)
        if key == "reminder_time":
            await self.reminder_scheduler.refresh_schedule()
        context.user_data.pop("pending_setting_key", None)
        await update.message.reply_text(
            f"Updated {key} = {normalized_value}",
        )
        await update.message.reply_text(
            await self._build_settings_text(),
            reply_markup=self._settings_keyboard(),
        )

    async def command_cancel_pending(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._ensure_owner_private(update):
            return
        assert update.message is not None

        had_pending = False
        for key in ("pending_read_different", "pending_setting_key"):
            if key in context.user_data:
                context.user_data.pop(key, None)
                had_pending = True
        if had_pending:
            await update.message.reply_text("Cancelled pending input.")
        else:
            await update.message.reply_text("Nothing to cancel.")

    async def newbook_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not await self._ensure_owner_private(update):
            return ConversationHandler.END
        assert update.message is not None

        channel_id = await self._get_channel_id()
        if channel_id is None:
            await update.message.reply_text("Set channel first using /setchannel <channel_id>.")
            return ConversationHandler.END

        context.user_data["newbook"] = {}
        await update.message.reply_text("Enter book title:")
        return NEWBOOK_TITLE

    async def newbook_title(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        assert update.message is not None
        title = update.message.text.strip()
        if not title:
            await update.message.reply_text("Title cannot be empty. Enter book title:")
            return NEWBOOK_TITLE
        context.user_data["newbook"]["title"] = title
        await update.message.reply_text("Enter author:")
        return NEWBOOK_AUTHOR

    async def newbook_author(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        assert update.message is not None
        author = update.message.text.strip()
        if not author:
            await update.message.reply_text("Author cannot be empty. Enter author:")
            return NEWBOOK_AUTHOR
        context.user_data["newbook"]["author"] = author
        await update.message.reply_text("Enter total pages (integer):")
        return NEWBOOK_TOTAL_PAGES

    async def newbook_total_pages(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        assert update.message is not None
        try:
            total_pages = int(update.message.text.strip())
            if total_pages <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("total_pages must be a positive integer.")
            return NEWBOOK_TOTAL_PAGES

        context.user_data["newbook"]["total_pages"] = total_pages
        await update.message.reply_text(
            "Enter start date (YYYY-MM-DD or DD.MM.YYYY), or '-' to use today:",
        )
        return NEWBOOK_START_DATE

    async def newbook_start_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        assert update.message is not None
        today = datetime.now(self.config.timezone).date()
        try:
            parsed = parse_date_input(update.message.text, default_date=today)
        except ValueError as exc:
            await update.message.reply_text(f"{exc}. Enter start date again:")
            return NEWBOOK_START_DATE

        context.user_data["newbook"]["start_date"] = parsed.isoformat()
        await update.message.reply_text("Enter start page, or '-' to use 1:")
        return NEWBOOK_START_PAGE

    async def newbook_start_page(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        assert update.message is not None
        payload = context.user_data.get("newbook", {})
        total_pages = int(payload["total_pages"])
        raw_value = update.message.text.strip()
        if raw_value in {"-", "skip"}:
            start_page = 1
        else:
            try:
                start_page = int(raw_value)
            except ValueError:
                await update.message.reply_text("start_page must be an integer or '-'.")
                return NEWBOOK_START_PAGE

        if start_page <= 0 or start_page > total_pages:
            await update.message.reply_text(f"start_page must be between 1 and {total_pages}.")
            return NEWBOOK_START_PAGE

        payload["start_page"] = start_page
        book_id = await self.db.create_book(
            title=str(payload["title"]),
            author=str(payload["author"]),
            total_pages=total_pages,
            start_page=start_page,
            start_date=str(payload["start_date"]),
        )

        channel_id = await self._get_channel_id()
        if channel_id is None:
            await self.db.delete_book(book_id)
            await update.message.reply_text("Channel is not configured. Run /setchannel first.")
            context.user_data.pop("newbook", None)
            return ConversationHandler.END

        try:
            sent = await context.bot.send_message(
                chat_id=channel_id,
                text=build_header_text(
                    title=str(payload["title"]),
                    author=str(payload["author"]),
                    start_date_iso=str(payload["start_date"]),
                    finish_date_iso=None,
                ),
            )
        except TelegramError:
            LOGGER.exception("Failed to send header message for book_id=%s", book_id)
            await self.db.delete_book(book_id)
            await update.message.reply_text(
                "Failed to post header to channel. Ensure bot is admin with post permission.",
            )
            context.user_data.pop("newbook", None)
            return ConversationHandler.END

        await self.db.set_book_header_message(book_id=book_id, message_id=int(sent.message_id))
        context.user_data.pop("newbook", None)
        await update.message.reply_text(
            f"Book created.\n"
            f"ID: {book_id}\n"
            f"{payload['title']} — {payload['author']}\n"
            f"Start: {format_display_date(parse_iso_date(payload['start_date']))}\n"
            f"Progress: {start_page - 1}/{total_pages}",
        )
        return ConversationHandler.END

    async def newbook_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not await self._ensure_owner_private(update):
            return ConversationHandler.END
        assert update.message is not None
        context.user_data.pop("newbook", None)
        await update.message.reply_text("New book wizard cancelled.")
        return ConversationHandler.END

    async def _build_settings_text(self) -> str:
        values = await self.db.get_settings(
            keys=("reminder_time", "start_pages", "weekly_increment", "increment_every_days", "channel_id"),
        )
        reminder_time = values.get("reminder_time", "08:00")
        start_pages = values.get("start_pages", "10")
        weekly_increment = values.get("weekly_increment", "5")
        increment_every_days = values.get("increment_every_days", "7")
        channel_id = values.get("channel_id", "not set")
        return (
            "Current settings:\n"
            f"- channel_id: {channel_id}\n"
            f"- reminder_time: {reminder_time}\n"
            f"- start_pages: {start_pages}\n"
            f"- weekly_increment: {weekly_increment}\n"
            f"- increment_every_days: {increment_every_days}"
        )

    def _settings_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Edit reminder_time", callback_data="settings_edit:reminder_time")],
                [InlineKeyboardButton("Edit start_pages", callback_data="settings_edit:start_pages")],
                [InlineKeyboardButton("Edit weekly_increment", callback_data="settings_edit:weekly_increment")],
                [
                    InlineKeyboardButton(
                        "Edit increment_every_days",
                        callback_data="settings_edit:increment_every_days",
                    ),
                ],
            ],
        )

    async def _delete_channel_reminder_message(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        reminder: dict[str, Any],
    ) -> None:
        channel_message_id = reminder.get("channel_message_id")
        if channel_message_id is None:
            return
        channel_id = await self._get_channel_id()
        if channel_id is None:
            return
        try:
            await context.bot.delete_message(
                chat_id=channel_id,
                message_id=int(channel_message_id),
            )
        except TelegramError:
            LOGGER.exception(
                "Failed to delete reminder message_id=%s",
                channel_message_id,
            )

    async def _finish_book_if_completed(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        book: dict[str, Any],
        finish_date_iso: str,
        new_last_read_page: int,
    ) -> None:
        if new_last_read_page < int(book["total_pages"]):
            return
        changed = await self.db.finish_book(
            book_id=int(book["id"]),
            finish_date=finish_date_iso,
            last_read_page=new_last_read_page,
        )
        if not changed:
            return

        channel_id = await self._get_channel_id()
        if channel_id is None:
            return

        if book["header_message_id"] is not None:
            try:
                await context.bot.edit_message_text(
                    chat_id=channel_id,
                    message_id=int(book["header_message_id"]),
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
            await context.bot.send_message(
                chat_id=channel_id,
                text=f"✅ Finished: {book['title']} — {book['author']}",
            )
        except TelegramError:
            LOGGER.exception("Failed to send completion message for book_id=%s", book["id"])

    def _extract_reminder_id_from_markup(self, markup: InlineKeyboardMarkup | None) -> int | None:
        if markup is None:
            return None
        for row in markup.inline_keyboard:
            for button in row:
                callback_data = button.callback_data
                if callback_data and callback_data.startswith("mark_read:"):
                    try:
                        return int(callback_data.split(":")[1])
                    except ValueError:
                        return None
        return None

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        LOGGER.exception("Unhandled exception", exc_info=context.error)
        try:
            await context.bot.send_message(
                chat_id=self.config.owner_user_id,
                text=f"⚠️ Bot error: {context.error}",
            )
        except TelegramError:
            LOGGER.exception("Failed to notify owner about error")


def register_bot(
    application: Application,
    db: Database,
    config: AppConfig,
    reminder_scheduler: ReminderScheduler,
) -> ReadingTrackerBot:
    bot = ReadingTrackerBot(db=db, config=config, reminder_scheduler=reminder_scheduler)
    bot.register_handlers(application)
    return bot
