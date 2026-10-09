from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    max_file_size_mb: float = 49.0

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.max_file_size_mb * 1024 * 1024)


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "BOT_TOKEN is missing. Copy .env.example to .env and set your token."
        )

    max_mb_raw = os.getenv("MAX_FILE_SIZE_MB", "49").strip()
    try:
        max_mb = float(max_mb_raw)
    except ValueError as exc:
        raise RuntimeError("MAX_FILE_SIZE_MB must be a number.") from exc

    return Settings(bot_token=token, max_file_size_mb=max_mb)
