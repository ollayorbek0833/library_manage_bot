from __future__ import annotations

import logging

from telegram import BotCommand, MenuButtonWebApp, WebAppInfo
from telegram.error import TelegramError
from telegram.ext import Application

from app.bot import register_bot
from app.db import Database
from app.scheduler import ReminderScheduler
from app.settings import DEFAULT_RUNTIME_SETTINGS, load_config


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    config = load_config()
    db = Database(config.db_path)

    async def post_init(app: Application) -> None:
        await db.init()
        await db.ensure_default_settings(DEFAULT_RUNTIME_SETTINGS)
        await reminder_scheduler.start()

        try:
            await app.bot.set_my_commands(
                [
                    BotCommand("start", "Show help"),
                    BotCommand("newbook", "Create a new book"),
                    BotCommand("list", "List active and paused books"),
                    BotCommand("settings", "View scheduler settings"),
                    BotCommand("panel", "Open admin mini app"),
                ],
            )
        except TelegramError:
            logging.getLogger(__name__).exception("Failed to set bot commands")

        if config.mini_app_url:
            for admin_user_id in config.admin_user_ids:
                try:
                    await app.bot.set_chat_menu_button(
                        chat_id=admin_user_id,
                        menu_button=MenuButtonWebApp(
                            text="Admin Panel",
                            web_app=WebAppInfo(url=config.mini_app_url),
                        ),
                    )
                except TelegramError:
                    logging.getLogger(__name__).warning(
                        "Failed to set chat menu button for admin_id=%s. "
                        "User may not have started the bot yet.",
                        admin_user_id,
                    )
        else:
            logging.getLogger(__name__).info("MINI_APP_URL not set; /panel command stays disabled.")

        logging.getLogger(__name__).info("Bot initialized")

    async def post_shutdown(_: Application) -> None:
        await reminder_scheduler.shutdown()
        await db.close()
        logging.getLogger(__name__).info("Bot shutdown complete")

    application = (
        Application.builder()
        .token(config.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    reminder_scheduler = ReminderScheduler(
        application=application,
        db=db,
        config=config,
    )
    register_bot(
        application=application,
        db=db,
        config=config,
        reminder_scheduler=reminder_scheduler,
    )

    application.run_polling()


if __name__ == "__main__":
    main()
