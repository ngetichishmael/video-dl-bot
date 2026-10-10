# hoard

Telegram bot that downloads public videos from links (YouTube, TikTok, Instagram, X/Twitter, Facebook, Reddit, Pinterest, Spotify tracks (audio matched via YouTube), and other [yt-dlp](https://github.com/yt-dlp/yt-dlp)-supported sites).

## Requirements

- Python 3.11+
- [FFmpeg](https://ffmpeg.org/) (`brew install ffmpeg`)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Setup

```bash
cd hoard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and set BOT_TOKEN and ALLOWED_USER_IDS
```

## Run

```bash
source .venv/bin/activate
python -m bot
```

Send any supported video URL in chat. Commands: `/start`, `/help`, `/audio <url>`.

### History and notes

Every download is recorded in a local SQLite file (`data/hoard.db`, override with `HISTORY_DB`).

- Add a note when you download: `<url> #recipes spicy chicken`
- Reply to any video/audio Hoard sent with text to attach a note, or use `/note <text>` for the latest one
- `/history` lists the last 10, `/search <text or #tag>` finds by title, note, link or site
- `/get <id>` re-sends a past download instantly (via Telegram's stored file), `/delete <id>` removes an entry
- Sending a link you've already downloaded re-sends it from cache instead of downloading again

## Notes

- Private bot: only user IDs listed in `ALLOWED_USER_IDS` get a response; everyone else is ignored (and logged).
- Public media only — does not bypass logins, private accounts, or DRM.
- Telegram's cloud Bot API caps uploads at ~50MB. Sources up to `MAX_FILE_SIZE_MB` (default 100) are downloaded and re-encoded to fit; keeping originals above 50MB needs a local Bot API server.
