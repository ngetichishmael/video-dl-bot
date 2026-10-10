from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from bot.downloader import DownloadError

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TRACK_PATH = re.compile(r"/track/([A-Za-z0-9]{22})")
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
SPOTIFY_HOSTS = ("open.spotify.com", "spotify.link", "spotify.app.link")


@dataclass(frozen=True, slots=True)
class SpotifyTrack:
    title: str
    artists: str

    @property
    def display(self) -> str:
        return f"{self.artists} - {self.title}"

    @property
    def search_query(self) -> str:
        return f"ytsearch1:{self.display} audio"


def is_spotify_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in SPOTIFY_HOSTS


def _open(url: str, timeout: int = 15):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310


def resolve_track(url: str) -> SpotifyTrack:
    """Look up track metadata so the audio can be found elsewhere."""
    try:
        match = TRACK_PATH.search(url)
        if not match:
            # Short links (spotify.link) redirect to the real open.spotify.com URL.
            with _open(url) as response:
                match = TRACK_PATH.search(response.geturl())
        if not match:
            raise DownloadError("Only Spotify track links are supported.")

        with _open(f"https://open.spotify.com/embed/track/{match.group(1)}") as page:
            html = page.read().decode("utf-8", errors="replace")
        data = json.loads(NEXT_DATA.search(html).group(1))
        entity = data["props"]["pageProps"]["state"]["data"]["entity"]
        title = entity["name"]
        artists = ", ".join(a["name"] for a in entity.get("artists", []))
    except DownloadError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as user-facing failure
        raise DownloadError("Could not read that Spotify link.") from exc

    return SpotifyTrack(title=title, artists=artists or "Unknown artist")
