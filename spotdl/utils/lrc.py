"""
LRC related functions
"""

import logging
import re
from pathlib import Path

from syncedlyrics import search as syncedlyrics_search
from syncedlyrics.utils import Lyrics, TargetType, has_translation

from spotdl.types.song import Song

logger = logging.getLogger(__name__)

__all__ = ["generate_lrc", "get_lrc_for_song", "remomve_lrc"]


def get_lrc_for_song(song: Song) -> str | None:
    """
    Return LRC lyrics string for the song (for embedding in audio).
    Uses song.lyrics if already synced, otherwise fetches via syncedlyrics.
    """
    if song.lyrics and has_translation(song.lyrics):
        return song.lyrics
    try:
        return syncedlyrics_search(song.display_name)
    except Exception:
        return None


def generate_lrc(song: Song, output_file: Path):
    """
    Generates an LRC file for the current song

    ### Arguments
    - song: Song object
    - output_file: Path to the output file
    """
    lrc_data = get_lrc_for_song(song)

    if lrc_data:
        Lyrics(lrc_data).save_lrc_file(
            str(output_file.with_suffix(".lrc")), TargetType.PREFER_SYNCED
        )
        logger.debug("Saved lrc file for %s", song.display_name)
    else:
        logger.debug("No lrc file found for %s", song.display_name)


def remomve_lrc(lyrics: str) -> str:
    """
    Removes lrc tags from lyrics

    ### Arguments
    - lyrics: Lyrics string

    ### Returns
    - Lyrics string without lrc tags
    """

    return re.sub(r"\[.*?\]", "", lyrics)
