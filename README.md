# video-dl-bot

Telegram bot that downloads public videos from links (YouTube, TikTok, Instagram, X/Twitter, Facebook, Reddit, and other [yt-dlp](https://github.com/yt-dlp/yt-dlp)-supported sites).

## Requirements

- Python 3.11+
- [FFmpeg](https://ffmpeg.org/) (`brew install ffmpeg`)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Setup

```bash
cd /Users/ish/codes/pycharm/video-dl-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and set BOT_TOKEN
```

## Run

```bash
source .venv/bin/activate
python -m bot
```

Send any supported video URL in chat. Commands: `/start`, `/help`, `/audio <url>`.

## Notes

- Public media only — does not bypass logins, private accounts, or DRM.
- Default Telegram Bot API upload limit is ~50MB. Raise it with a local Bot API server if needed.
