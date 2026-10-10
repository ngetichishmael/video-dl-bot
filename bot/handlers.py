from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    FSInputFile,
    InputMediaPhoto,
    LinkPreviewOptions,
    Message,
)

from bot.downloader import (
    URL_PATTERN,
    DownloadError,
    cleanup_download,
    download_media,
    extract_urls,
)
from bot.spotify import is_spotify_url, resolve_track
from bot.store import Download, Store

if TYPE_CHECKING:
    from bot.config import Settings

logger = logging.getLogger(__name__)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

HELP_TEXT = (
    "Send a public video link and I'll download it.\n\n"
    "Works with YouTube, TikTok, Instagram, X/Twitter, Facebook, Reddit, Pinterest, "
    "Spotify tracks, and other sites supported by yt-dlp.\n\n"
    "Label things so you can find them later:\n"
    "- Add text after the link: <url> #recipes spicy chicken\n"
    "- Or reply to any video/audio I sent with a note\n"
    "- /note <text> — note on the latest download\n\n"
    "Commands:\n"
    "/audio <url> — extract audio (mp3)\n"
    "/history — last 10 downloads\n"
    "/search <text> — search titles, notes, links, #tags\n"
    "/get <id> — send a past download again\n"
    "/delete <id> — remove an entry from history\n"
    "/help — this message\n"
)


def _site(url: str) -> str:
    if is_spotify_url(url):
        return "spotify"
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _media_host_url(url: str) -> str:
    # Spotify tracks are fetched from YouTube, so they use YouTube's proxy rule.
    return "https://www.youtube.com/" if is_spotify_url(url) else url


def _note_from_text(text: str) -> str | None:
    note = URL_PATTERN.sub("", text or "").strip(" \n\t-:")
    return note[:500] or None


def _format_entries(entries: list[Download]) -> str:
    lines: list[str] = []
    for d in entries:
        title = (d.title or "Untitled").replace("\n", " ")
        if len(title) > 60:
            title = title[:57] + "..."
        lines.append(f"#{d.id} · {d.created_at[:10]} · {d.site or '?'} · {title}")
        if d.note:
            lines.append(f"    note: {d.note}")
        lines.append(f"    {d.url}")
    return "\n".join(lines)


def create_router(settings: Settings) -> Router:
    router = Router(name="hoard")
    store = Store(settings.history_db)

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
            "similar sites and I'll send the media back.\n\n"
            "Add a note after the link to find it later, e.g. "
            "<url> #recipes. /help lists everything."
        )

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(HELP_TEXT)

    @router.message(Command("audio"))
    async def audio_cmd(message: Message, command: CommandObject) -> None:
        args = command.args or ""
        urls = extract_urls(args)
        if not urls and message.reply_to_message:
            urls = extract_urls(message.reply_to_message.text or "")

        if not urls:
            await message.answer("Usage: /audio <url>")
            return

        await _process_url(
            message,
            urls[0],
            store=store,
            audio_only=True,
            max_filesize=settings.max_file_size_bytes,
            proxy=settings.proxy_for(_media_host_url(urls[0])),
            note=_note_from_text(args),
        )

    @router.message(Command("history"))
    async def history_cmd(message: Message) -> None:
        entries = store.history(message.from_user.id)
        if not entries:
            await message.answer("No downloads yet.")
            return
        await message.answer(_format_entries(entries), link_preview_options=NO_PREVIEW)

    @router.message(Command("search"))
    async def search_cmd(message: Message, command: CommandObject) -> None:
        query = (command.args or "").strip()
        if not query:
            await message.answer("Usage: /search <text or #tag>")
            return
        entries = store.search(message.from_user.id, query)
        if not entries:
            await message.answer(f"Nothing found for: {query}")
            return
        await message.answer(_format_entries(entries), link_preview_options=NO_PREVIEW)

    @router.message(Command("note"))
    async def note_cmd(message: Message, command: CommandObject) -> None:
        text = (command.args or "").strip()
        if not text:
            await message.answer("Usage: /note <text> (applies to your latest download)")
            return
        latest = store.latest(message.from_user.id)
        if latest is None:
            await message.answer("No downloads yet.")
            return
        store.set_note(latest.id, message.from_user.id, text[:500])
        await message.answer(f"Note saved on #{latest.id}.")

    @router.message(Command("get"))
    async def get_cmd(message: Message, command: CommandObject) -> None:
        arg = (command.args or "").strip().lstrip("#")
        if not arg.isdigit():
            await message.answer("Usage: /get <id> (ids are in /history)")
            return
        entry = store.get(int(arg), message.from_user.id)
        if entry is None:
            await message.answer("No download with that id.")
            return
        if not entry.file_id:
            await message.answer(
                "That one wasn't saved for re-sending (photo albums aren't). "
                f"Original link: {entry.url}",
                link_preview_options=NO_PREVIEW,
            )
            return
        sent = await _send_cached(message, entry)
        store.map_messages(message.chat.id, entry.id, sent)

    @router.message(Command("delete"))
    async def delete_cmd(message: Message, command: CommandObject) -> None:
        arg = (command.args or "").strip().lstrip("#")
        if not arg.isdigit():
            await message.answer("Usage: /delete <id>")
            return
        removed = store.delete(int(arg), message.from_user.id)
        await message.answer(
            f"Removed #{arg} from history." if removed else "No download with that id."
        )

    @router.message(F.text)
    async def handle_text(message: Message) -> None:
        text = message.text or ""
        urls = extract_urls(text)

        if not urls:
            if message.reply_to_message:
                entry = store.by_message(
                    message.chat.id, message.reply_to_message.message_id
                )
                if entry is not None:
                    store.set_note(entry.id, message.from_user.id, text.strip()[:500])
                    await message.answer(f"Note saved on #{entry.id}.")
                    return
            await message.answer(
                "Send a video URL, or use /help for supported commands."
            )
            return

        await _process_url(
            message,
            urls[0],
            store=store,
            audio_only=False,
            max_filesize=settings.max_file_size_bytes,
            proxy=settings.proxy_for(_media_host_url(urls[0])),
            note=_note_from_text(text),
        )

    return router


async def _send_cached(message: Message, entry: Download) -> list[int]:
    """Re-send a past download using Telegram's stored file ids."""
    caption = entry.title or "Downloaded media"
    sent: list[Message] = []
    if entry.media_type == "audio":
        sent.append(await message.answer_audio(audio=entry.file_id, caption=caption))
    else:
        sent.append(
            await message.answer_video(
                video=entry.file_id, caption=caption, supports_streaming=True
            )
        )
        if entry.audio_file_id:
            sent.append(
                await message.answer_audio(
                    audio=entry.audio_file_id, title=entry.title
                )
            )
    return [m.message_id for m in sent]


async def _process_url(
    message: Message,
    url: str,
    *,
    store: Store,
    audio_only: bool,
    max_filesize: int,
    proxy: str | None = None,
    note: str | None = None,
) -> None:
    user_id = message.from_user.id if message.from_user else 0
    spotify = is_spotify_url(url)
    audio_only = audio_only or spotify
    mode = "audio" if audio_only else "video"
    logger.info("Request user=%s mode=%s url=%s", user_id, mode, url)

    cached = store.find_cached(user_id, url, mode)
    if cached is not None:
        try:
            sent = await _send_cached(message, cached)
        except Exception:
            logger.warning("Cached send failed for #%s; re-downloading", cached.id)
        else:
            store.map_messages(message.chat.id, cached.id, sent)
            if note:
                store.set_note(cached.id, user_id, note)
            await message.answer(
                f"Already downloaded as #{cached.id} on {cached.created_at[:10]}; "
                "sent again from cache."
            )
            logger.info("Cached user=%s url=%s id=%s", user_id, url, cached.id)
            return

    status = await message.answer(
        "Extracting audio..." if audio_only else "Downloading..."
    )
    result = None

    try:
        source, display_title = url, None
        if spotify:
            track = await asyncio.to_thread(resolve_track, url)
            source, display_title = track.search_query, track.display

        result = await asyncio.to_thread(
            download_media,
            source,
            audio_only=audio_only,
            max_filesize=max_filesize,
            proxy=proxy,
        )
        if display_title:
            result = replace(result, title=display_title)

        await status.edit_text("Uploading...")
        file = FSInputFile(result.path)

        caption = result.title or "Downloaded media"
        if len(caption) > 900:
            caption = caption[:897] + "..."

        sent_ids: list[int] = []
        file_id: str | None = None
        audio_file_id: str | None = None

        if result.media_type == "images":
            for i in range(0, len(result.images), 10):
                group = [
                    InputMediaPhoto(
                        media=FSInputFile(img),
                        caption=caption if i == 0 and n == 0 else None,
                    )
                    for n, img in enumerate(result.images[i : i + 10])
                ]
                sent_ids += [m.message_id for m in await message.answer_media_group(group)]
            if result.audio_path is not None:
                audio_msg = await message.answer_audio(
                    audio=FSInputFile(result.audio_path), title=result.title
                )
                sent_ids.append(audio_msg.message_id)
        elif audio_only or result.media_type == "audio":
            sent = await message.answer_audio(audio=file, caption=caption)
            sent_ids.append(sent.message_id)
            file_id = sent.audio.file_id if sent.audio else None
        else:
            sent = await message.answer_video(
                video=file,
                caption=caption,
                supports_streaming=True,
            )
            sent_ids.append(sent.message_id)
            file_id = sent.video.file_id if sent.video else None
            if result.audio_path is not None:
                audio_msg = await message.answer_audio(
                    audio=FSInputFile(result.audio_path),
                    title=result.title,
                )
                sent_ids.append(audio_msg.message_id)
                audio_file_id = audio_msg.audio.file_id if audio_msg.audio else None

        size_bytes = sum(p.stat().st_size for p in (result.images or (result.path,)))
        download_id = None
        try:
            download_id = store.add(
                user_id=user_id,
                url=url,
                mode=mode,
                title=result.title,
                site=_site(url),
                media_type=result.media_type,
                size_bytes=size_bytes,
                has_audio=result.audio_path is not None,
                file_id=file_id,
                audio_file_id=audio_file_id,
                chat_id=message.chat.id,
                message_ids=sent_ids,
            )
            if note:
                store.set_note(download_id, user_id, note)
        except Exception:
            logger.exception("Could not record download for %s", url)

        logger.info(
            "Sent user=%s id=%s url=%s size_mb=%.1f audio=%s",
            user_id,
            download_id,
            url,
            size_bytes / (1024 * 1024),
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
