#!/usr/bin/env python3
"""
Check what metadata (especially lyrics) is actually stored inside an MP3 file.
Use this to verify that lyrics were embedded correctly, independent of the player app.

Usage:
  python scripts/check_mp3_metadata.py path/to/song.mp3
  python scripts/check_mp3_metadata.py path/to/song.mp3 --verbose   # show full lyrics
"""

import argparse
import sys
from pathlib import Path

# Add project root when run as script
if __name__ == "__main__":
    _root = Path(__file__).resolve().parents[1]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from mutagen.id3 import ID3
from mutagen.mp3 import MP3


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect ID3 metadata (including lyrics) stored in an MP3 file."
    )
    parser.add_argument("file", type=Path, help="Path to the MP3 file")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print full lyrics content",
    )
    args = parser.parse_args()

    path = args.file.resolve()
    if not path.exists():
        print(f"Error: file not found: {path}")
        sys.exit(1)
    if path.suffix.lower() != ".mp3":
        print("Warning: file is not .mp3, trying anyway.")

    try:
        audio = MP3(str(path))
    except Exception as e:
        print(f"Error reading file: {e}")
        sys.exit(1)

    if audio.tags is None:
        print("No ID3 tags in file.")
        sys.exit(0)

    id3: ID3 = audio.tags
    print(f"File: {path.name}")
    print(f"Length: {audio.info.length:.1f}s")
    print("-" * 50)

    # Common text tags
    text_frames = [
        ("TIT2", "Title"),
        ("TPE1", "Artist"),
        ("TALB", "Album"),
        ("TRCK", "Track"),
        ("TDRC", "Date"),
        ("TCON", "Genre"),
        ("COMM", "Comment"),
        ("WOAS", "URL"),
        ("APIC", "Picture (cover)"),
    ]
    for frame_id, label in text_frames:
        try:
            if frame_id == "APIC":
                frames = id3.getall("APIC")
                if frames:
                    print(f"  {label}: present ({len(frames)} image(s), e.g. {len(frames[0].data)} bytes)")
                continue
            if frame_id == "COMM":
                frames = id3.getall("COMM")
                if frames:
                    print(f"  {label}: {frames[0].text[0][:80]!r}...")
                continue
            frame = id3.get(frame_id)
            if frame is not None:
                if hasattr(frame, "text"):
                    val = frame.text[0] if frame.text else ""
                else:
                    val = str(frame)
                print(f"  {label}: {val[:80]}{'...' if len(str(val)) > 80 else ''}")
        except Exception as e:
            print(f"  {label}: (error: {e})")

    # Lyrics: USLT (unsynchronized) and SYLT (synchronized)
    print("-" * 50)
    uslt_frames = id3.getall("USLT")
    sylt_frames = id3.getall("SYLT")

    if uslt_frames:
        print(f"  Lyrics (USLT): {len(uslt_frames)} frame(s)")
        for i, frame in enumerate(uslt_frames):
            lang = getattr(frame, "lang", "???")
            desc = getattr(frame, "desc", "") or ""
            text = (frame.text or "") if hasattr(frame, "text") else str(frame)
            print(f"    [{i+1}] lang={lang!r} desc={desc!r} length={len(text)} chars")
            if text:
                preview = text[:200].replace("\n", " ")
                print(f"        Preview: {preview}...")
                if args.verbose:
                    print("    Full lyrics:")
                    print("    " + "\n    ".join(text.splitlines()))
    else:
        print("  Lyrics (USLT): NOT FOUND")

    if sylt_frames:
        print(f"  Synced lyrics (SYLT): {len(sylt_frames)} frame(s)")
        for i, frame in enumerate(sylt_frames):
            if hasattr(frame, "text") and frame.text:
                count = len(frame.text)
                print(f"    [{i+1}] {count} timed lines")
                if args.verbose and frame.text:
                    for line_text, ts in frame.text[:15]:
                        print(f"        {ts}ms: {line_text!r}")
                    if len(frame.text) > 15:
                        print(f"        ... and {len(frame.text) - 15} more")
            else:
                print(f"    [{i+1}] (no text)")
    else:
        print("  Synced lyrics (SYLT): NOT FOUND")

    if not uslt_frames and not sylt_frames:
        print("\n  => No lyrics tags in file. Run: spotdl meta --force-update-metadata <file>")
    else:
        print("\n  => Lyrics are present in the file. If your player does not show them,")
        print("     it may not support USLT/SYLT or may look for a specific language/description.")


if __name__ == "__main__":
    main()
