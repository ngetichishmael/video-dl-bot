from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yt_dlp

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# Soft hints for user-facing messages; yt-dlp decides actual support.
KNOWN_HOST_HINTS = (
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "facebook.com",
    "fb.watch",
    "reddit.com",
    "v.redd.it",
    "vimeo.com",
    "twitch.tv",
    "streamable.com",
    "pinterest.com",
    "pin.it",
)


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wav"}
MAX_IMAGES = 30


@dataclass(frozen=True, slots=True)
class DownloadResult:
    path: Path
    title: str | None
    webpage_url: str | None
    media_type: Literal["video", "audio", "images"]
    audio_path: Path | None = None
    images: tuple[Path, ...] = ()


class DownloadError(Exception):
    """Raised when media cannot be downloaded or is too large."""


def extract_urls(text: str) -> list[str]:
    urls = URL_PATTERN.findall(text or "")
    # Strip common trailing punctuation from chat messages.
    cleaned: list[str] = []
    for url in urls:
        cleaned.append(url.rstrip(").,]}>\"'"))
    return cleaned


def looks_like_media_url(url: str) -> bool:
    lower = url.lower()
    return any(host in lower for host in KNOWN_HOST_HINTS) or lower.startswith(
        ("http://", "https://")
    )


def _build_options(
    *,
    outtmpl: str,
    audio_only: bool,
    max_filesize: int | None,
    proxy: str | None = None,
) -> dict:
    options: dict = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "retries": 3,
        "fragment_retries": 3,
    }

    if max_filesize is not None:
        options["max_filesize"] = max_filesize

    if proxy:
        options["proxy"] = proxy

    if audio_only:
        options.update(
            {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
        )
    else:
        options.update(
            {
                "format": "bv*+ba/b",
                "merge_output_format": "mp4",
            }
        )

    return options


def _resolve_downloaded_path(info: dict, ydl: yt_dlp.YoutubeDL) -> Path:
    requested = info.get("requested_downloads") or []
    if requested:
        filepath = requested[0].get("filepath")
        if filepath and os.path.exists(filepath):
            return Path(filepath)

    prepared = Path(ydl.prepare_filename(info))
    if prepared.exists():
        return prepared

    # Audio post-process often rewrites extension to .mp3
    mp3 = prepared.with_suffix(".mp3")
    if mp3.exists():
        return mp3

    mp4 = prepared.with_suffix(".mp4")
    if mp4.exists():
        return mp4

    raise DownloadError("Download finished but the media file was not found.")


def _extract_audio(video: Path, max_filesize: int | None) -> Path | None:
    out = video.with_suffix(".mp3")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                "-vn", "-c:a", "libmp3lame", "-b:a", "192k", str(out),
            ],
            check=True,
            timeout=300,
        )
    except (subprocess.SubprocessError, OSError):
        out.unlink(missing_ok=True)
        return None

    if not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        return None
    if max_filesize is not None and out.stat().st_size > max_filesize:
        out.unlink(missing_ok=True)
        return None
    return out


def _download_images(
    url: str, temp_dir: str, proxy: str | None = None
) -> tuple[Path, ...]:
    """Fetch photo posts (e.g. TikTok slideshows) with gallery-dl."""
    image_dir = Path(temp_dir) / "images"
    command = [sys.executable, "-m", "gallery_dl", "-q", "-D", str(image_dir)]
    if proxy:
        command += ["--proxy", proxy]
    try:
        subprocess.run(
            [*command, url],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (subprocess.SubprocessError, OSError):
        return ()

    if not image_dir.is_dir():
        return ()
    images = sorted(
        p
        for p in image_dir.iterdir()
        if p.suffix.lower() in IMAGE_EXTENSIONS and p.stat().st_size > 0
    )
    return tuple(images[:MAX_IMAGES])


def _download_with_ytdlp(
    url: str,
    temp_dir: str,
    *,
    audio_only: bool,
    max_filesize: int | None,
    proxy: str | None = None,
) -> tuple[Path, dict]:
    outtmpl = str(Path(temp_dir) / "%(id)s.%(ext)s")
    options = _build_options(
        outtmpl=outtmpl,
        audio_only=audio_only,
        max_filesize=max_filesize,
        proxy=proxy,
    )

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise DownloadError("No media information returned for that URL.")

            # Playlists are disabled, but some extractors still nest entries.
            if "entries" in info:
                entries = [e for e in info["entries"] if e]
                if not entries:
                    raise DownloadError("No downloadable media found at that URL.")
                info = entries[0]

            path = _resolve_downloaded_path(info, ydl)
    except DownloadError:
        raise
    except yt_dlp.utils.DownloadError as exc:
        message = str(exc)
        if "File is larger than max-filesize" in message:
            raise DownloadError(
                "That file is larger than the configured size limit."
            ) from exc
        raise DownloadError(
            "Could not download that link. It may be private, geo-blocked, "
            "or unsupported."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - surface as user-facing download failure
        raise DownloadError(f"Unexpected download failure: {exc}") from exc

    if not path.exists() or path.stat().st_size == 0:
        raise DownloadError("Downloaded file is empty or missing.")

    if max_filesize is not None and path.stat().st_size > max_filesize:
        path.unlink(missing_ok=True)
        raise DownloadError("That file is larger than the configured size limit.")

    return path, info


def download_media(
    url: str,
    *,
    audio_only: bool = False,
    max_filesize: int | None = None,
    proxy: str | None = None,
) -> DownloadResult:
    temp_dir = tempfile.mkdtemp(prefix="hoard-")

    try:
        try:
            path, info = _download_with_ytdlp(
                url,
                temp_dir,
                audio_only=audio_only,
                max_filesize=max_filesize,
                proxy=proxy,
            )
        except DownloadError:
            images = () if audio_only else _download_images(url, temp_dir, proxy)
            if not images:
                raise
            return DownloadResult(
                path=images[0],
                title=None,
                webpage_url=url,
                media_type="images",
                images=images,
            )

        title = info.get("title")
        webpage_url = info.get("webpage_url") or url

        # Photo slideshows come back from yt-dlp as audio only.
        if not audio_only and path.suffix.lower() in AUDIO_EXTENSIONS:
            images = _download_images(url, temp_dir, proxy)
            if images:
                return DownloadResult(
                    path=images[0],
                    title=title,
                    webpage_url=webpage_url,
                    media_type="images",
                    audio_path=path,
                    images=images,
                )

        return DownloadResult(
            path=path,
            title=title,
            webpage_url=webpage_url,
            media_type="audio" if audio_only else "video",
            audio_path=None if audio_only else _extract_audio(path, max_filesize),
        )
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def cleanup_download(result: DownloadResult | None) -> None:
    if result is None:
        return

    parent = result.path.parent
    if parent.name == "images":
        parent = parent.parent
    if parent.name.startswith("hoard-"):
        shutil.rmtree(parent, ignore_errors=True)
