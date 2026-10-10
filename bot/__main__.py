from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from bot.config import load_settings
from bot.handlers import create_router


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )


async def run() -> None:
    settings = load_settings()
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(create_router(settings))

    await bot.set_my_commands(
        [
            BotCommand(command="history", description="Last 10 downloads"),
            BotCommand(command="search", description="Search titles, notes, #tags"),
            BotCommand(command="get", description="Re-send a download by id"),
            BotCommand(command="note", description="Add a note to the latest download"),
            BotCommand(command="audio", description="Extract audio from a link"),
            BotCommand(command="delete", description="Remove an entry by id"),
            BotCommand(command="help", description="What I can do"),
        ]
    )

    logging.getLogger(__name__).info("Starting hoard polling...")
    await dp.start_polling(bot)


def main() -> None:
    configure_logging()
    asyncio.run(run())


if __name__ == "__main__":
    main()
