from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import FSInputFile, InputMediaPhoto, Message

from bot.downloader import (
    DownloadError,
    cleanup_download,
    download_media,
    extract_urls,
)

if TYPE_CHECKING:
    from bot.config import Settings

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Send a public video link and I'll download it.\n\n"
    "Works with YouTube, TikTok, Instagram, X/Twitter, Facebook, Reddit, Pinterest, "
    "and other sites supported by yt-dlp.\n\n"
    "Commands:\n"
    "/start — intro\n"
    "/help — this message\n"
    "/audio <url> — extract audio (mp3)\n"
)


def create_router(settings: Settings) -> Router:
    router = Router(name="hoard")

    @router.message.outer_middleware()
    async def allow_listed_only(handler, event: Message, data):
        user_id = event.from_user.id if event.from_user else None
        if user_id not in settings.allowed_user_ids:
            logger.warning("Ignored message from unauthorized user id=%s", user_id)
            return None
        return await handler(event, data)

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "Send me a video link from TikTok, Instagram, YouTube, X, or "
            "similar sites and I'll send the media back."
        )

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(HELP_TEXT)

    @router.message(Command("audio"))
    async def audio_cmd(message: Message, command: CommandObject) -> None:
        urls = extract_urls(command.args or "")
        if not urls and message.reply_to_message:
            urls = extract_urls(message.reply_to_message.text or "")

        if not urls:
            await message.answer("Usage: /audio <url>")
            return

        await _process_url(
            message,
            urls[0],
            audio_only=True,
            max_filesize=settings.max_file_size_bytes,
            proxy=settings.proxy_for(urls[0]),
        )

    @router.message(F.text)
    async def handle_text(message: Message) -> None:
        urls = extract_urls(message.text or "")
        if not urls:
            await message.answer(
                "Send a video URL, or use /help for supported commands."
            )
            return

        await _process_url(
            message,
            urls[0],
            audio_only=False,
            max_filesize=settings.max_file_size_bytes,
            proxy=settings.proxy_for(urls[0]),
        )

    return router


async def _process_url(
    message: Message,
    url: str,
    *,
    audio_only: bool,
    max_filesize: int,
    proxy: str | None = None,
) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info(
        "Request user=%s mode=%s url=%s",
        user_id,
        "audio" if audio_only else "video",
        url,
    )
    status = await message.answer(
        "Extracting audio..." if audio_only else "Downloading..."
    )
    result = None

    try:
        result = await asyncio.to_thread(
            download_media,
            url,
            audio_only=audio_only,
            max_filesize=max_filesize,
            proxy=proxy,
        )

        await status.edit_text("Uploading...")
        file = FSInputFile(result.path)

        caption = result.title or "Downloaded media"
        if len(caption) > 900:
            caption = caption[:897] + "..."

        if result.media_type == "images":
            for i in range(0, len(result.images), 10):
                group = [
                    InputMediaPhoto(
                        media=FSInputFile(img),
                        caption=caption if i == 0 and n == 0 else None,
                    )
                    for n, img in enumerate(result.images[i : i + 10])
                ]
                await message.answer_media_group(group)
            if result.audio_path is not None:
                await message.answer_audio(
                    audio=FSInputFile(result.audio_path), title=result.title
                )
        elif audio_only or result.media_type == "audio":
            await message.answer_audio(audio=file, caption=caption)
        else:
            await message.answer_video(
                video=file,
                caption=caption,
                supports_streaming=True,
            )
            if result.audio_path is not None:
                await message.answer_audio(
                    audio=FSInputFile(result.audio_path),
                    title=result.title,
                )

        logger.info(
            "Sent user=%s url=%s size_mb=%.1f audio=%s",
            user_id,
            url,
            sum(p.stat().st_size for p in (result.images or (result.path,)))
            / (1024 * 1024),
            result.audio_path is not None,
        )
        await status.delete()
    except DownloadError as exc:
        await status.edit_text(str(exc))
        logger.info("Failed user=%s url=%s reason=%s", user_id, url, exc)
    except Exception:
        logger.exception("Error user=%s url=%s", user_id, url)
        await status.edit_text(
            "Something went wrong while processing that link. Try again later."
        )
    finally:
        cleanup_download(result)
