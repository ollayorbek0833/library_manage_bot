from __future__ import annotations

import logging

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

    async def post_init(_: Application) -> None:
        await db.init()
        await db.ensure_default_settings(DEFAULT_RUNTIME_SETTINGS)
        await reminder_scheduler.start()
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
