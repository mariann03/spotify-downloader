#!/usr/bin/env python3
"""
Regenerate metadata and lyrics for already-downloaded audio files.

Uses the file name (e.g. "Artist - Title.mp3") or existing tags to search
YouTube Music, then embeds metadata and fetches/embeds lyrics.

Usage:
  python scripts/regen_metadata.py /path/to/music
  python scripts/regen_metadata.py /path/to/folder
  python scripts/regen_metadata.py song.mp3
  python scripts/regen_metadata.py --force /path/to/music   # update even if metadata exists
  python scripts/regen_metadata.py --skip-album-art /path   # don't download cover art
  python scripts/regen_metadata.py --generate-lrc /path    # also create .lrc files

You can also use the built-in command (same behavior):
  spotdl meta /path/to/music
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path when run as script
if __name__ == "__main__":
    _root = Path(__file__).resolve().parents[1]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from spotdl.console.meta import meta
from spotdl.download.downloader import Downloader
from spotdl.utils.config import (
    DOWNLOADER_OPTIONS,
    create_settings_type,
    get_config,
    get_config_file,
)
from spotdl.utils.ffmpeg import FFMPEG_FORMATS
from spotdl.utils.logging import init_logging

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate metadata and lyrics for audio files based on filename or existing tags."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Paths to audio files or folders containing audio files",
    )
    parser.add_argument(
        "--force-update-metadata",
        "--force",
        action="store_true",
        help="Update metadata even if the file already has tags",
    )
    parser.add_argument(
        "--skip-album-art",
        action="store_true",
        help="Do not download or embed album art",
    )
    parser.add_argument(
        "--generate-lrc",
        action="store_true",
        help="Generate .lrc lyric files alongside audio files",
    )
    parser.add_argument(
        "--id3-separator",
        default="/",
        help="ID3 tag separator (default: /)",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level",
    )
    args = parser.parse_args()

    init_logging(args.log_level, None)

    # Build settings from defaults (no Spotify required; uses YTM for search)
    config = {}
    if get_config_file().exists():
        try:
            config = get_config()
        except Exception:
            pass

    downloader_options = create_settings_type(
        argparse.Namespace(
            force_update_metadata=args.force_update_metadata,
            skip_album_art=args.skip_album_art,
            generate_lrc=args.generate_lrc,
            id3_separator=args.id3_separator,
        ),
        config,
        DOWNLOADER_OPTIONS,
    )
    downloader_options["force_update_metadata"] = args.force_update_metadata
    downloader_options["skip_album_art"] = args.skip_album_art
    downloader_options["generate_lrc"] = args.generate_lrc
    downloader_options["id3_separator"] = args.id3_separator

    downloader = Downloader(settings=downloader_options)

    # Resolve paths to files
    paths_to_process: list[Path] = []
    for p in args.paths:
        path = Path(p).resolve()
        if not path.exists():
            logger.error("Path does not exist: %s", p)
            continue
        if path.is_file():
            if path.suffix.lower().lstrip(".") in FFMPEG_FORMATS:
                paths_to_process.append(path)
            else:
                logger.error("Not a supported audio format: %s", p)
        else:
            for fmt in FFMPEG_FORMATS:
                paths_to_process.extend(path.glob(f"*.{fmt}"))

    if not paths_to_process:
        logger.error("No audio files found in the given path(s).")
        sys.exit(1)

    logger.info("Processing %d file(s) for metadata/lyrics.", len(paths_to_process))
    meta(query=[str(p) for p in paths_to_process], downloader=downloader)
    logger.info("Done.")


if __name__ == "__main__":
    main()
