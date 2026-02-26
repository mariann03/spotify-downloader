"""
Fetch basic Spotify metadata without API credentials using the public oEmbed endpoint.
Supports track, album, playlist, and artist URLs.
"""

import logging
from typing import Any, Dict, Optional

import requests

__all__ = ["get_spotify_metadata", "SpotifyMetadataError"]

logger = logging.getLogger(__name__)

OEMBED_URL = "https://open.spotify.com/oembed"


class SpotifyMetadataError(Exception):
    """Raised when oEmbed metadata cannot be fetched."""


def get_spotify_metadata(url: str, timeout: int = 10) -> Dict[str, Any]:
    """
    Get title and thumbnail for a Spotify URL using the public oEmbed API (no auth).

    ### Arguments
    - url: Spotify track, album, playlist, or artist URL.
    - timeout: Request timeout in seconds.

    ### Returns
    - Dict with at least "title" and optionally "thumbnail_url", "thumbnail_width", "thumbnail_height".

    ### Raises
    - SpotifyMetadataError: If the request fails or returns an error.
    """
    if "open.spotify.com" not in url and "spotify.link" not in url:
        raise SpotifyMetadataError(f"Not a Spotify URL: {url}")

    try:
        resp = requests.get(
            OEMBED_URL,
            params={"url": url},
            timeout=timeout,
            headers={"User-Agent": "SpotDL/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise SpotifyMetadataError(f"Failed to fetch Spotify metadata: {exc}") from exc

    title = data.get("title") or data.get("provider_name", "").strip()
    if not title:
        raise SpotifyMetadataError("oEmbed response had no title")

    return {
        "title": title,
        "thumbnail_url": data.get("thumbnail_url"),
        "thumbnail_width": data.get("thumbnail_width"),
        "thumbnail_height": data.get("thumbnail_height"),
    }
