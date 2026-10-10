from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

DEFAULT_HISTORY_DB = Path(__file__).resolve().parent.parent / "data" / "hoard.db"


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    allowed_user_ids: frozenset[int]
    max_file_size_mb: float = 49.0
    proxy_url: str = ""
    proxy_hosts: tuple[str, ...] = ()
    history_db: Path = DEFAULT_HISTORY_DB

    def proxy_for(self, url: str) -> str | None:
        if not self.proxy_url:
            return None
        host = (urlparse(url).hostname or "").lower()
        if any(host == h or host.endswith("." + h) for h in self.proxy_hosts):
            return self.proxy_url
        return None

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.max_file_size_mb * 1024 * 1024)


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "BOT_TOKEN is missing. Copy .env.example to .env and set your token."
        )

    ids_raw = os.getenv("ALLOWED_USER_IDS", "").strip()
    try:
        allowed = frozenset(int(i) for i in ids_raw.split(",") if i.strip())
    except ValueError as exc:
        raise RuntimeError(
            "ALLOWED_USER_IDS must be comma-separated numeric Telegram user IDs."
        ) from exc
    if not allowed:
        raise RuntimeError(
            "ALLOWED_USER_IDS is missing. Set your numeric Telegram user ID "
            "(message @userinfobot to find it)."
        )

    max_mb_raw = os.getenv("MAX_FILE_SIZE_MB", "49").strip()
    try:
        max_mb = float(max_mb_raw)
    except ValueError as exc:
        raise RuntimeError("MAX_FILE_SIZE_MB must be a number.") from exc

    proxy_hosts = tuple(
        h.strip().lower()
        for h in os.getenv("PROXY_HOSTS", "reddit.com,redd.it,youtube.com,youtu.be").split(",")
        if h.strip()
    )

    return Settings(
        bot_token=token,
        allowed_user_ids=allowed,
        max_file_size_mb=max_mb,
        proxy_url=os.getenv("PROXY_URL", "").strip(),
        proxy_hosts=proxy_hosts,
        history_db=Path(os.getenv("HISTORY_DB", "").strip() or DEFAULT_HISTORY_DB),
    )
