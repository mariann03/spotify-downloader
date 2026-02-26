"""
Module for creating Song objects by parsing a query.
Uses YouTube Music for search and Spotify oEmbed (no API key) for Spotify URL metadata.
"""

import concurrent.futures
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

import requests
from ytmusicapi import YTMusic

from spotdl.types.album import Album
from spotdl.types.artist import Artist
from spotdl.types.playlist import Playlist
from spotdl.types.saved import Saved
from spotdl.types.song import Song, SongList
from spotdl.utils.formatter import parse_duration
from spotdl.utils.metadata import get_file_metadata
from spotdl.utils.spotify import SpotifyClient, SpotifyError
from spotdl.utils.spotify_metadata import get_spotify_metadata, SpotifyMetadataError

__all__ = [
    "QueryError",
    "get_search_results",
    "get_song_from_spotify_url",
    "get_song_from_yt_url",
    "get_song_from_ytm_search",
    "get_songs_from_ytm_search",
    "parse_query",
    "get_simple_songs",
    "reinit_song",
    "get_song_from_file_metadata",
    "gather_known_songs",
    "create_ytm_album",
    "create_ytm_playlist",
    "get_all_user_playlists",
    "get_user_saved_albums",
]

logger = logging.getLogger(__name__)
client = None  # pylint: disable=invalid-name


def get_ytm_client() -> YTMusic:
    """
    Lazily initialize the YTMusic client.

    ### Returns
    - the YTMusic client
    """

    global client  # pylint: disable=global-statement
    if client is None:
        client = YTMusic()

    return client


def _ytm_result_to_song(result: Dict, spotify_url: Optional[str] = None) -> Song:
    """Build a Song from a YTM search result dict."""
    artists = [a["name"] for a in result.get("artists") or []]
    artist = artists[0] if artists else ""
    duration_str = result.get("duration")
    duration_sec = int(parse_duration(duration_str)) if duration_str else 0
    video_id = result.get("videoId") or ""
    ytm_url = f"https://music.youtube.com/watch?v={video_id}" if video_id else ""
    url = spotify_url or ytm_url

    return Song.from_missing_data(
        name=result.get("title") or "",
        artists=artists,
        artist=artist,
        genres=[],
        disc_number=1,
        disc_count=1,
        album_name=result.get("album", {}).get("name") if result.get("album") else "",
        album_artist=artist,
        duration=duration_sec,
        year=0,
        date="",
        track_number=1,
        tracks_count=1,
        song_id=video_id,
        explicit=result.get("isExplicit") or False,
        publisher="",
        url=url,
        cover_url=(
            result.get("thumbnails", [{}])[-1].get("url")
            if result.get("thumbnails")
            else None
        ),
        download_url=ytm_url if video_id else None,
    )


def get_song_from_ytm_search(search_term: str) -> Song:
    """
    Resolve a search term to a single Song using YouTube Music (no Spotify API).

    ### Arguments
    - search_term: Query string (e.g. "Artist - Title").

    ### Returns
    - A Song with metadata and download_url from the first YTM result.

    ### Raises
    - QueryError: If no results found.
    """
    results = get_ytm_client().search(search_term, filter="songs", limit=5)
    for r in results:
        if r.get("videoId") and r.get("artists"):
            return _ytm_result_to_song(r)
    raise QueryError(f"No results found for: {search_term}")


def get_songs_from_ytm_search(search_term: str, limit: int = 50) -> List[Song]:
    """
    Resolve a search term to a list of Songs using YouTube Music (no Spotify API).

    ### Arguments
    - search_term: Query string.
    - limit: Max number of results.

    ### Returns
    - List of Song objects (may be empty).
    """
    results = get_ytm_client().search(search_term, filter="songs", limit=limit)
    songs: List[Song] = []
    for r in results:
        if r.get("videoId") and r.get("artists"):
            songs.append(_ytm_result_to_song(r))
    return songs


def _extract_youtube_video_id(url: str) -> Optional[str]:
    """Extract video ID from YouTube or YouTube Music URL."""
    url = url.strip()
    if "music.youtube.com/watch" in url or "youtube.com/watch" in url:
        if "?v=" in url:
            vid = url.split("?v=", 1)[1].split("&")[0].strip()
            return vid if vid else None
        if "&v=" in url:
            vid = url.split("&v=", 1)[1].split("&")[0].strip()
            return vid if vid else None
    if "youtu.be/" in url:
        path = url.split("youtu.be/", 1)[1].split("?")[0].split("&")[0].strip()
        return path if path else None
    return None


def get_song_from_yt_url(url: str) -> Song:
    """
    Resolve a YouTube or YouTube Music video URL to a Song (no Spotify API).

    ### Arguments
    - url: Full URL (music.youtube.com/watch?v=..., youtube.com/watch?v=..., youtu.be/...).

    ### Returns
    - Song with metadata from YTM and download_url set to the video URL.
    """
    video_id = _extract_youtube_video_id(url)
    if not video_id:
        raise QueryError(f"Invalid YouTube URL: {url}")
    track_data = get_ytm_client().get_song(video_id)
    if not track_data or "videoDetails" not in track_data:
        raise QueryError(f"Could not get song data for: {url}")
    vd = track_data["videoDetails"]
    title = vd.get("title") or ""
    author = vd.get("author") or ""
    length = vd.get("lengthSeconds") or "0"
    duration = int(length) if str(length).isdigit() else 0
    ytm_url = f"https://music.youtube.com/watch?v={video_id}"
    return Song.from_missing_data(
        name=title,
        artists=[author],
        artist=author,
        genres=[],
        disc_number=1,
        disc_count=1,
        album_name="",
        album_artist=author,
        duration=duration,
        year=0,
        date="",
        track_number=1,
        tracks_count=1,
        song_id=video_id,
        explicit=False,
        publisher="",
        url=ytm_url,
        download_url=ytm_url,
        cover_url=(
            vd.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url")
            if vd.get("thumbnail")
            else None
        ),
    )


def get_song_from_spotify_url(spotify_track_url: str) -> Song:
    """
    Resolve a Spotify track URL to a Song using oEmbed + YouTube Music (no Spotify API).

    ### Arguments
    - spotify_track_url: Full Spotify track URL (open.spotify.com/track/...).

    ### Returns
    - Song with metadata from YTM and url set to the original Spotify URL.
    """
    meta = get_spotify_metadata(spotify_track_url)
    title = meta["title"].strip()
    if not title:
        raise QueryError("Could not get title from Spotify URL")
    song = get_song_from_ytm_search(title)
    # Keep original Spotify URL in song.url for compatibility
    return Song.from_missing_data(**{**song.json, "url": spotify_track_url})


def get_album_from_spotify_url(spotify_album_url: str, fetch_songs: bool = True) -> Album:
    """
    Resolve a Spotify album URL using oEmbed + YouTube Music (no Spotify API).
    """
    meta = get_spotify_metadata(spotify_album_url)
    title = meta["title"].strip()
    if not title:
        raise QueryError("Could not get title from Spotify album URL")
    results = get_ytm_client().search(title, filter="albums", limit=3)
    for r in results:
        browse_id = r.get("browseId")
        if not browse_id:
            continue
        album = get_ytm_client().get_album(browse_id)
        if album is None:
            continue
        ytm_url = f"https://music.youtube.com/album/{r.get('albumId', '')}" if r.get("albumId") else spotify_album_url
        artist_name = album["artists"][0]["name"]
        metadata = {
            "artist": {"name": artist_name},
            "name": album["title"],
            "url": spotify_album_url,
        }
        songs = []
        for track in album.get("tracks") or []:
            artists = [a["name"] for a in track.get("artists") or []]
            song = Song.from_missing_data(
                name=track.get("title", ""),
                artists=artists,
                artist=artists[0] if artists else "",
                album_name=metadata["name"],
                album_artist=artist_name,
                duration=track.get("duration_seconds", 0),
                download_url=f"https://music.youtube.com/watch?v={track.get('videoId', '')}" if track.get("videoId") else None,
            )
            if fetch_songs and song.download_url:
                try:
                    song = get_song_from_ytm_search(f"{song.artist} - {song.name}")
                except QueryError:
                    pass
            songs.append(song)
        return Album(**metadata, songs=songs, urls=[s.url for s in songs])
    raise QueryError(f"No YouTube Music album found for: {title}")


def get_playlist_from_spotify_url(spotify_playlist_url: str, fetch_songs: bool = True) -> Playlist:
    """
    Resolve a Spotify playlist URL using oEmbed + YouTube Music (no Spotify API).
    """
    meta = get_spotify_metadata(spotify_playlist_url)
    title = meta["title"].strip()
    if not title:
        raise QueryError("Could not get title from Spotify playlist URL")
    results = get_ytm_client().search(title, filter="playlists", limit=5)
    for r in results:
        browse_id = r.get("browseId")
        if not browse_id or not browse_id.startswith("VL"):
            continue
        playlist_id = browse_id[2:]  # VL + playlistId
        playlist = get_ytm_client().get_playlist(playlist_id, None)  # type: ignore
        if playlist is None:
            continue
        metadata = {
            "description": playlist.get("description") or "",
            "author_url": f"https://music.youtube.com/channel/{playlist['author']['id']}" if playlist.get("author") else "",
            "author_name": playlist["author"]["name"] if playlist.get("author") else "",
            "cover_url": playlist["thumbnails"][0]["url"] if playlist.get("thumbnails") else "",
            "name": playlist["title"],
            "url": spotify_playlist_url,
        }
        songs = []
        for track in playlist.get("tracks") or []:
            if not track.get("videoId") or track.get("isAvailable") is False:
                continue
            song = Song.from_missing_data(
                name=track.get("title", ""),
                artists=[a["name"] for a in track.get("artists") or []],
                artist=track["artists"][0]["name"] if track.get("artists") else "",
                album_name=track.get("album", {}).get("name") if track.get("album") else "",
                duration=track.get("duration_seconds"),
                explicit=track.get("isExplicit"),
                download_url=f"https://music.youtube.com/watch?v={track['videoId']}",
            )
            if fetch_songs:
                try:
                    song = reinit_song(song)
                except Exception:
                    pass
            songs.append(song)
        return Playlist(**metadata, songs=songs, urls=[s.url for s in songs])
    raise QueryError(f"No YouTube Music playlist found for: {title}")


def get_artist_from_spotify_url(spotify_artist_url: str, fetch_songs: bool = True) -> Artist:
    """
    Resolve a Spotify artist URL using oEmbed + YouTube Music (no Spotify API).
    Returns top tracks and albums from YTM for this artist.
    """
    meta = get_spotify_metadata(spotify_artist_url)
    title = meta["title"].strip()
    if not title:
        raise QueryError("Could not get title from Spotify artist URL")
    results = get_ytm_client().search(title, filter="artists", limit=3)
    for r in results:
        browse_id = r.get("browseId")
        if not browse_id:
            continue
        artist_data = get_ytm_client().get_artist(browse_id)
        if artist_data is None:
            continue
        songs = []
        seen = set()
        # Top tracks
        for track in (artist_data.get("songs", {}).get("results") or [])[:50]:
            if not track.get("videoId") or track.get("videoId") in seen:
                continue
            seen.add(track.get("videoId"))
            artists = [a["name"] for a in track.get("artists") or []]
            song = Song.from_missing_data(
                name=track.get("title", ""),
                artists=artists,
                artist=artists[0] if artists else "",
                album_name=track.get("album", {}).get("name") if track.get("album") else "",
                duration=track.get("duration_seconds"),
                download_url=f"https://music.youtube.com/watch?v={track.get('videoId', '')}",
            )
            if fetch_songs:
                try:
                    song = get_song_from_ytm_search(f"{song.artist} - {song.name}")
                except QueryError:
                    pass
            songs.append(song)
        if not songs:
            continue
        metadata = {
            "name": artist_data.get("name", title),
            "genres": artist_data.get("genres") or [],
            "url": spotify_artist_url,
            "albums": [],
        }
        return Artist(**metadata, songs=songs, urls=[s.url for s in songs])
    raise QueryError(f"No YouTube Music artist found for: {title}")


def get_album_from_search_term(search_term: str, fetch_songs: bool = True) -> Album:
    """Resolve an album by search term using YouTube Music (no Spotify API)."""
    term = search_term.split(":", 1)[-1].strip() if ":" in search_term else search_term
    results = get_ytm_client().search(term, filter="albums", limit=3)
    for r in results:
        browse_id = r.get("browseId")
        if not browse_id:
            continue
        album = get_ytm_client().get_album(browse_id)
        if album is None:
            continue
        url = f"https://music.youtube.com/album/{r.get('albumId', '')}" if r.get("albumId") else ""
        artist_name = album["artists"][0]["name"]
        metadata = {
            "artist": {"name": artist_name},
            "name": album["title"],
            "url": url,
        }
        songs = []
        for track in album.get("tracks") or []:
            artists = [a["name"] for a in track.get("artists") or []]
            song = Song.from_missing_data(
                name=track.get("title", ""),
                artists=artists,
                artist=artists[0] if artists else "",
                album_name=metadata["name"],
                album_artist=artist_name,
                duration=track.get("duration_seconds", 0),
                download_url=f"https://music.youtube.com/watch?v={track.get('videoId', '')}" if track.get("videoId") else None,
            )
            if fetch_songs and song.download_url:
                try:
                    song = get_song_from_ytm_search(f"{song.artist} - {song.name}")
                except QueryError:
                    pass
            songs.append(song)
        return Album(**metadata, songs=songs, urls=[s.url for s in songs])
    raise QueryError(f"No album found for: {term}")


def get_playlist_from_search_term(search_term: str, fetch_songs: bool = True) -> Playlist:
    """Resolve a playlist by search term using YouTube Music (no Spotify API)."""
    term = search_term.split(":", 1)[-1].strip() if ":" in search_term else search_term
    results = get_ytm_client().search(term, filter="playlists", limit=5)
    for r in results:
        browse_id = r.get("browseId")
        if not browse_id or not browse_id.startswith("VL"):
            continue
        playlist_id = browse_id[2:]
        playlist = get_ytm_client().get_playlist(playlist_id, None)  # type: ignore
        if playlist is None:
            continue
        metadata = {
            "description": playlist.get("description") or "",
            "author_url": f"https://music.youtube.com/channel/{playlist['author']['id']}" if playlist.get("author") else "",
            "author_name": playlist["author"]["name"] if playlist.get("author") else "",
            "cover_url": playlist["thumbnails"][0]["url"] if playlist.get("thumbnails") else "",
            "name": playlist["title"],
            "url": f"https://music.youtube.com/playlist?list={playlist_id}",
        }
        songs = []
        for track in playlist.get("tracks") or []:
            if not track.get("videoId") or track.get("isAvailable") is False:
                continue
            song = Song.from_missing_data(
                name=track.get("title", ""),
                artists=[a["name"] for a in track.get("artists") or []],
                artist=track["artists"][0]["name"] if track.get("artists") else "",
                album_name=track.get("album", {}).get("name") if track.get("album") else "",
                duration=track.get("duration_seconds"),
                explicit=track.get("isExplicit"),
                download_url=f"https://music.youtube.com/watch?v={track['videoId']}",
            )
            if fetch_songs:
                try:
                    song = reinit_song(song)
                except Exception:
                    pass
            songs.append(song)
        return Playlist(**metadata, songs=songs, urls=[s.url for s in songs])
    raise QueryError(f"No playlist found for: {term}")


def get_artist_from_search_term(search_term: str, fetch_songs: bool = True) -> Artist:
    """Resolve an artist by search term using YouTube Music (no Spotify API)."""
    term = search_term.split(":", 1)[-1].strip() if ":" in search_term else search_term
    results = get_ytm_client().search(term, filter="artists", limit=3)
    for r in results:
        browse_id = r.get("browseId")
        if not browse_id:
            continue
        artist_data = get_ytm_client().get_artist(browse_id)
        if artist_data is None:
            continue
        songs = []
        seen = set()
        for track in (artist_data.get("songs", {}).get("results") or [])[:50]:
            if not track.get("videoId") or track.get("videoId") in seen:
                continue
            seen.add(track.get("videoId"))
            artists = [a["name"] for a in track.get("artists") or []]
            song = Song.from_missing_data(
                name=track.get("title", ""),
                artists=artists,
                artist=artists[0] if artists else "",
                album_name=track.get("album", {}).get("name") if track.get("album") else "",
                duration=track.get("duration_seconds"),
                download_url=f"https://music.youtube.com/watch?v={track.get('videoId', '')}",
            )
            if fetch_songs:
                try:
                    song = get_song_from_ytm_search(f"{song.artist} - {song.name}")
                except QueryError:
                    pass
            songs.append(song)
        if not songs:
            continue
        metadata = {
            "name": artist_data.get("name", term),
            "genres": artist_data.get("genres") or [],
            "url": f"https://music.youtube.com/channel/{browse_id}",
            "albums": [],
        }
        return Artist(**metadata, songs=songs, urls=[s.url for s in songs])
    raise QueryError(f"No artist found for: {term}")


class QueryError(Exception):
    """
    Base class for all exceptions related to query.
    """


def get_search_results(search_term: str) -> List[Song]:
    """
    Creates a list of Song objects from a search term (uses YouTube Music, no Spotify API).

    ### Arguments
    - search_term: the search term to use

    ### Returns
    - a list of Song objects
    """

    return get_songs_from_ytm_search(search_term)


def parse_query(
    query: List[str],
    threads: int = 1,
    use_ytm_data: bool = False,
    playlist_numbering: bool = False,
    album_type=None,
    playlist_retain_track_cover: bool = False,
) -> List[Song]:
    """
    Parse query and return list containing song object

    ### Arguments
    - query: List of strings containing query
    - threads: Number of threads to use

    ### Returns
    - List of song objects
    """

    songs: List[Song] = get_simple_songs(
        query,
        use_ytm_data=use_ytm_data,
        playlist_numbering=playlist_numbering,
        album_type=album_type,
        playlist_retain_track_cover=playlist_retain_track_cover,
    )

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        future_to_song = {executor.submit(reinit_song, song): song for song in songs}
        for future in concurrent.futures.as_completed(future_to_song):
            song = future_to_song[future]
            try:
                results.append(future.result())
            except Exception as exc:
                logger.error("%s generated an exception: %s", song.display_name, exc)

    return results


def get_simple_songs(
    query: List[str],
    use_ytm_data: bool = False,
    playlist_numbering: bool = False,
    albums_to_ignore=None,
    album_type=None,
    playlist_retain_track_cover: bool = False,
) -> List[Song]:
    """
    Parse query and return list containing simple song objects

    ### Arguments
    - query: List of strings containing query

    ### Returns
    - List of simple song objects
    """

    songs: List[Song] = []
    lists: List[SongList] = []
    for request in query:
        logger.info("Processing query: %s", request)

        # Remove /intl-xxx/ from Spotify URLs with regex
        request = re.sub(r"\/intl-\w+\/", "/", request)

        if (
            (  # pylint: disable=too-many-boolean-expressions
                "watch?v=" in request
                or "youtu.be/" in request
                or "soundcloud.com/" in request
                or "bandcamp.com/" in request
            )
            and "open.spotify.com" in request
            and "track" in request
            and "|" in request
        ):
            split_urls = request.split("|")
            if (
                len(split_urls) <= 1
                or not (
                    "watch?v=" in split_urls[0]
                    or "youtu.be" in split_urls[0]
                    or "soundcloud.com/" in split_urls[0]
                    or "bandcamp.com/" in split_urls[0]
                )
                or "spotify" not in split_urls[1]
            ):
                raise QueryError(
                    'Incorrect format used, please use "YouTubeURL|SpotifyURL"'
                )

            songs.append(
                Song.from_missing_data(url=split_urls[1], download_url=split_urls[0])
            )
        elif "music.youtube.com/watch?v" in request:
            track_data = get_ytm_client().get_song(request.split("?v=", 1)[1])

            yt_song = Song.from_search_term(
                f"{track_data['videoDetails']['author']} - {track_data['videoDetails']['title']}"
            )

            if use_ytm_data:
                yt_song.name = track_data["title"]
                yt_song.artist = track_data["author"]
                yt_song.artists = [track_data["author"]]
                yt_song.duration = track_data["lengthSeconds"]

            yt_song.download_url = request
            songs.append(yt_song)
        elif (
            "youtube.com/playlist?list=" in request
            or "youtube.com/browse/VLPL" in request
        ):
            request = request.replace(
                "https://www.youtube.com/", "https://music.youtube.com/"
            )
            request = request.replace(
                "https://youtube.com/", "https://music.youtube.com/"
            )

            split_urls = request.split("|")
            if len(split_urls) == 1:
                if "?list=OLAK5uy_" in request:
                    lists.append(create_ytm_album(request, fetch_songs=False))
                elif "?list=PL" in request or "browse/VLPL" in request:
                    lists.append(create_ytm_playlist(request, fetch_songs=False))
            else:
                if ("spotify" not in split_urls[1]) or not any(
                    x in split_urls[0]
                    for x in ["?list=PL", "?list=OLAK5uy_", "browse/VLPL"]
                ):
                    raise QueryError(
                        'Incorrect format used, please use "YouTubeMusicURL|SpotifyURL". '
                        "Currently only supports YouTube Music playlists and albums."
                    )

                if ("open.spotify.com" in request and "album" in request) and (
                    "?list=OLAK5uy_" in request
                ):
                    ytm_list: SongList = create_ytm_album(
                        split_urls[0], fetch_songs=False
                    )
                    spot_list = get_album_from_spotify_url(split_urls[1], fetch_songs=False)
                elif ("open.spotify.com" in request and "playlist" in request) and (
                    "?list=PL" in request or "browse/VLPL" in request
                ):
                    ytm_list = create_ytm_playlist(split_urls[0], fetch_songs=False)
                    spot_list = get_playlist_from_spotify_url(split_urls[1], fetch_songs=False)
                else:
                    raise QueryError(
                        f"URLs are not of the same type, {split_urls[0]} is not "
                        f"the same type as {split_urls[1]}."
                    )

                if ytm_list.length != spot_list.length:
                    raise QueryError(
                        f"The YouTube Music ({ytm_list.length}) "
                        f"and Spotify ({spot_list.length}) lists have different lengths. "
                    )

                if use_ytm_data:
                    for index, song in enumerate(ytm_list.songs):
                        song.url = spot_list.songs[index].url

                    lists.append(ytm_list)
                else:
                    for index, song in enumerate(spot_list.songs):
                        song.download_url = ytm_list.songs[index].download_url

                    lists.append(spot_list)
        elif "open.spotify.com" in request and "track" in request:
            songs.append(get_song_from_spotify_url(request))
        elif "https://spotify.link/" in request:
            resp = requests.head(request, allow_redirects=True, timeout=10)
            full_url = resp.url
            full_lists = get_simple_songs(
                [full_url],
                use_ytm_data=use_ytm_data,
                playlist_numbering=playlist_numbering,
                album_type=album_type,
                playlist_retain_track_cover=playlist_retain_track_cover,
            )
            songs.extend(full_lists)
        elif "open.spotify.com" in request and "playlist" in request:
            lists.append(get_playlist_from_spotify_url(request, fetch_songs=False))
        elif "open.spotify.com" in request and "album" in request:
            lists.append(get_album_from_spotify_url(request, fetch_songs=False))
        elif "open.spotify.com" in request and "artist" in request:
            lists.append(get_artist_from_spotify_url(request, fetch_songs=False))
        elif "open.spotify.com" in request and "user" in request:
            lists.extend(get_all_user_playlists(request))
        elif "album:" in request:
            lists.append(get_album_from_search_term(request, fetch_songs=False))
        elif "playlist:" in request:
            lists.append(get_playlist_from_search_term(request, fetch_songs=False))
        elif "artist:" in request:
            lists.append(get_artist_from_search_term(request, fetch_songs=False))
        elif request == "saved":
            lists.append(Saved.from_url(request, fetch_songs=False))
        elif request == "all-user-playlists":
            lists.extend(get_all_user_playlists())
        elif request == "all-user-followed-artists":
            lists.extend(get_user_followed_artists())
        elif request == "all-user-saved-albums":
            lists.extend(get_user_saved_albums())
        elif request == "all-saved-playlists":
            lists.extend(get_all_saved_playlists())
        elif request.endswith(".spotdl"):
            with open(request, "r", encoding="utf-8") as save_file:
                for track in json.load(save_file):
                    # Append to songs
                    songs.append(Song.from_dict(track))
        else:
            songs.append(Song.from_search_term(request))

    for song_list in lists:
        logger.info(
            "Found %s songs in %s (%s)",
            len(song_list.urls),
            song_list.name,
            song_list.__class__.__name__,
        )

        for index, song in enumerate(song_list.songs):
            song_data = song.json
            song_data["list_name"] = song_list.name
            song_data["list_url"] = song_list.url
            song_data["list_position"] = song.list_position
            song_data["list_length"] = song_list.length

            if playlist_numbering:
                song_data["track_number"] = song_data["list_position"]
                song_data["tracks_count"] = song_data["list_length"]
                song_data["album_name"] = song_data["list_name"]
                song_data["disc_number"] = 1
                song_data["disc_count"] = 1
                if isinstance(song_list, Playlist):
                    song_data["album_artist"] = song_list.author_name
                    song_data["cover_url"] = song_list.cover_url

            if playlist_retain_track_cover:
                song_data["track_number"] = song_data["list_position"]
                song_data["tracks_count"] = song_data["list_length"]
                song_data["album_name"] = song_data["list_name"]
                song_data["disc_number"] = 1
                song_data["disc_count"] = 1
                song_data["cover_url"] = song_data["cover_url"]
                if isinstance(song_list, Playlist):
                    song_data["album_artist"] = song_list.author_name

            songs.append(Song.from_dict(song_data))

    # removing songs for --ignore-albums
    original_length = len(songs)
    if albums_to_ignore:
        songs = [
            song
            for song in songs
            if all(
                keyword not in song.album_name.lower() for keyword in albums_to_ignore
            )
        ]
        logger.info("Skipped %s songs (Ignored albums)", (original_length - len(songs)))

    if album_type:
        songs = [song for song in songs if song.album_type == album_type]

        logger.info(
            "Skipped %s songs for Album Type %s",
            (original_length - len(songs)),
            album_type,
        )

    logger.debug("Found %s songs in %s lists", len(songs), len(lists))

    return songs


def songs_from_albums(albums: List[str]):
    """
    Get all songs from albums ids/urls/etc.

    ### Arguments
    - albums: List of albums ids

    ### Returns
    - List of songs
    """

    songs: List[Song] = []
    for album_id in albums:
        if "open.spotify.com" in str(album_id) and "album" in str(album_id):
            album = get_album_from_spotify_url(album_id, fetch_songs=False)
        else:
            album = Album.from_url(album_id, fetch_songs=False)
        songs.extend([Song.from_missing_data(**song.json) for song in album.songs])

    return songs


def get_all_user_playlists(user_url: str = "") -> List[Playlist]:
    """
    Get all user playlists.

    ### Args (optional)
    - user_url: Spotify user profile url.
        If a url is mentioned, get all public playlists of that specific user.

    ### Returns
    - List of all user playlists
    """

    spotify_client = SpotifyClient()
    if spotify_client.user_auth is False:  # type: ignore
        raise SpotifyError("You must be logged in to use this function")

    if user_url and not user_url.startswith("https://open.spotify.com/user/"):
        raise ValueError(f"Invalid user profile url: {user_url}")

    user_id = user_url.split("https://open.spotify.com/user/")[-1].replace("/", "")

    if user_id:
        user_playlists_response = spotify_client.user_playlists(user_id)
    else:
        user_playlists_response = spotify_client.current_user_playlists()
        user_resp = spotify_client.current_user()
        if user_resp is None:
            raise SpotifyError("Couldn't get user info")

        user_id = user_resp["id"]

    if user_playlists_response is None:
        raise SpotifyError("Couldn't get user playlists")

    user_playlists = user_playlists_response["items"]

    # Fetch all saved tracks
    while user_playlists_response and user_playlists_response["next"]:
        response = spotify_client.next(user_playlists_response)
        if response is None:
            break

        user_playlists_response = response
        user_playlists.extend(user_playlists_response["items"])

    return [
        Playlist.from_url(playlist["external_urls"]["spotify"], fetch_songs=False)
        for playlist in user_playlists
        if playlist["owner"]["id"] == user_id
    ]


def get_user_saved_albums() -> List[Album]:
    """
    Get all user saved albums

    ### Returns
    - List of all user saved albums
    """

    spotify_client = SpotifyClient()
    if spotify_client.user_auth is False:  # type: ignore
        raise SpotifyError("You must be logged in to use this function")

    user_saved_albums_response = spotify_client.current_user_saved_albums()
    if user_saved_albums_response is None:
        raise SpotifyError("Couldn't get user saved albums")

    user_saved_albums = user_saved_albums_response["items"]

    # Fetch all saved tracks
    while user_saved_albums_response and user_saved_albums_response["next"]:
        response = spotify_client.next(user_saved_albums_response)
        if response is None:
            break

        user_saved_albums_response = response
        user_saved_albums.extend(user_saved_albums_response["items"])

    return [
        Album.from_url(item["album"]["external_urls"]["spotify"], fetch_songs=False)
        for item in user_saved_albums
    ]


def get_user_followed_artists() -> List[Artist]:
    """
    Get all user playlists

    ### Returns
    - List of all user playlists
    """

    spotify_client = SpotifyClient()
    if spotify_client.user_auth is False:  # type: ignore
        raise SpotifyError("You must be logged in to use this function")

    user_followed_response = spotify_client.current_user_followed_artists()
    if user_followed_response is None:
        raise SpotifyError("Couldn't get user followed artists")

    user_followed_response = user_followed_response["artists"]
    user_followed = user_followed_response["items"]

    # Fetch all artists
    while user_followed_response and user_followed_response["next"]:
        response = spotify_client.next(user_followed_response)
        if response is None:
            break

        user_followed_response = response["artists"]
        user_followed.extend(user_followed_response["items"])

    return [
        Artist.from_url(followed_artist["external_urls"]["spotify"], fetch_songs=False)
        for followed_artist in user_followed
    ]


def get_all_saved_playlists() -> List[Playlist]:
    """
    Get all user playlists.

    ### Args (optional)
    - user_url: Spotify user profile url.
        If a url is mentioned, get all public playlists of that specific user.

    ### Returns
    - List of all user playlists
    """

    spotify_client = SpotifyClient()
    if spotify_client.user_auth is False:  # type: ignore
        raise SpotifyError("You must be logged in to use this function")

    user_playlists_response = spotify_client.current_user_playlists()

    if user_playlists_response is None:
        raise SpotifyError("Couldn't get user playlists")

    user_playlists = user_playlists_response["items"]
    user_id = user_playlists_response["href"].split("users/")[-1].split("/")[0]

    # Fetch all saved tracks
    while user_playlists_response and user_playlists_response["next"]:
        response = spotify_client.next(user_playlists_response)
        if response is None:
            break

        user_playlists_response = response
        user_playlists.extend(user_playlists_response["items"])

    return [
        Playlist.from_url(playlist["external_urls"]["spotify"], fetch_songs=False)
        for playlist in user_playlists
        if playlist["owner"]["id"] != user_id
    ]


def reinit_song(song: Song) -> Song:
    """
    Update song object with new data
    from Spotify

    ### Arguments
    - song: Song object

    ### Returns
    - Updated song object
    """

    data = song.json
    if data.get("url"):
        new_data = Song.from_url(data["url"]).json
    elif data.get("song_id"):
        new_data = Song.from_url(
            "https://open.spotify.com/track/" + data["song_id"]
        ).json
    elif data.get("name") and data.get("artist"):
        new_data = Song.from_search_term(f"{data['artist']} - {data['name']}").json
    else:
        raise QueryError("Song object is missing required data to be reinitialized")

    for key in Song.__dataclass_fields__:  # type: ignore # pylint: disable=E1101
        val = data.get(key)
        new_val = new_data.get(key)
        if new_val is not None and val is None:
            data[key] = new_val
        elif new_val is not None and val is not None:
            data[key] = val

    # return reinitialized song object
    return Song(**data)


def get_song_from_file_metadata(file: Path, id3_separator: str = "/") -> Optional[Song]:
    """
    Get song based on the file metadata or file name

    ### Arguments
    - file: Path to file

    ### Returns
    - Song object
    """

    file_metadata = get_file_metadata(file, id3_separator)

    if file_metadata is None:
        return None

    return Song.from_missing_data(**file_metadata)


def gather_known_songs(output: str, output_format: str) -> Dict[str, List[Path]]:
    """
    Gather all known songs from the output directory

    ### Arguments
    - output: Output path template
    - output_format: Output format

    ### Returns
    - Dictionary containing all known songs and their paths
    """

    # Get the base directory from the path template
    # Path("/Music/test/{artist}/{artists} - {title}.{output-ext}") -> "/Music/test"
    base_dir = output.split("{", 1)[0]
    paths = Path(base_dir).glob(f"**/*.{output_format}")

    known_songs: Dict[str, List[Path]] = {}
    for path in paths:
        # Try to get the song from the metadata
        song = get_song_from_file_metadata(path)

        # If the songs doesn't have metadata, try to get it from the filename
        if song is None or song.url is None:
            search_results = get_search_results(path.stem)
            if len(search_results) == 0:
                continue

            song = search_results[0]

        known_paths = known_songs.get(song.url)
        if known_paths is None:
            known_songs[song.url] = [path]
        else:
            known_songs[song.url].append(path)

    return known_songs


def create_ytm_album(url: str, fetch_songs: bool = True) -> Album:
    """
    Creates a list of Song objects from an album query.

    ### Arguments
    - album_query: the url of the album

    ### Returns
    - a list of Song objects
    """

    if "?list=" not in url or not url.startswith("https://music.youtube.com/"):
        raise ValueError(f"Invalid album url: {url}")

    browse_id = get_ytm_client().get_album_browse_id(
        url.split("?list=")[1].split("&")[0]
    )
    if browse_id is None:
        raise ValueError(f"Invalid album url: {url}")

    album = get_ytm_client().get_album(browse_id)

    if album is None:
        raise ValueError(f"Couldn't fetch album: {url}")

    metadata = {
        "artist": album["artists"][0]["name"],
        "name": album["title"],
        "url": url,
    }

    songs = []
    for track in album["tracks"]:
        artists = [artist["name"] for artist in track["artists"]]

        song = Song.from_missing_data(
            name=track["title"],
            artists=artists,
            artist=artists[0],
            album_name=metadata["name"],
            album_artist=metadata["artist"],
            duration=track["duration_seconds"],
            download_url=f"https://music.youtube.com/watch?v={track['videoId']}",
        )

        if fetch_songs:
            song = Song.from_search_term(f"{song.artist} - {song.name}")

        songs.append(song)

    return Album(**metadata, songs=songs, urls=[song.url for song in songs])


def create_ytm_playlist(url: str, fetch_songs: bool = True) -> Playlist:
    """
    Returns a playlist object from a youtube playlist url

    ### Arguments
    - url: the url of the playlist

    ### Returns
    - a Playlist object
    """

    if not ("?list=" in url or "/browse/VLPL" in url) or not url.startswith(
        "https://music.youtube.com/"
    ):
        raise ValueError(f"Invalid playlist url: {url}")

    if "/browse/VLPL" in url:
        playlist_id = url.split("/browse/")[1]
    else:
        playlist_id = url.split("?list=")[1]
    playlist = get_ytm_client().get_playlist(playlist_id, None)  # type: ignore

    if playlist is None:
        raise ValueError(f"Couldn't fetch playlist: {url}")

    metadata = {
        "description": (
            playlist["description"] if playlist["description"] is not None else ""
        ),
        "author_url": (
            f"https://music.youtube.com/channel/{playlist['author']['id']}"
            if playlist.get("author") is not None
            else "Missing author url"
        ),
        "author_name": (
            playlist["author"]["name"]
            if playlist.get("author") is not None
            else "Missing author"
        ),
        "cover_url": (
            playlist["thumbnails"][0]["url"]
            if playlist.get("thumbnails") is not None
            else "Missing thumbnails"
        ),
        "name": playlist["title"],
        "url": url,
    }

    songs = []
    for track in playlist["tracks"]:
        if track["videoId"] is None or track["isAvailable"] is False:
            continue

        song = Song.from_missing_data(
            name=track["title"],
            artists=(
                [artist["name"] for artist in track["artists"]]
                if track.get("artists") is not None
                else []
            ),
            artist=(
                track["artists"][0]["name"]
                if track.get("artists") is not None
                else None
            ),
            album_name=(
                track.get("album", {}).get("name")
                if track.get("album") is not None
                else None
            ),
            duration=track.get("duration_seconds"),
            explicit=track.get("isExplicit"),
            download_url=f"https://music.youtube.com/watch?v={track['videoId']}",
        )

        if fetch_songs:
            song = reinit_song(song)

        songs.append(song)

    return Playlist(**metadata, songs=songs, urls=[song.url for song in songs])
