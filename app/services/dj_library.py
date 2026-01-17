from __future__ import annotations

import difflib
import os
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests

from app.services.music_search import get_music_index


def _normalize(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _fallback_title(entry: Dict) -> str:
    path = entry.get("path") or ""
    return os.path.splitext(os.path.basename(path))[0] or "Untitled"


def _build_track_payload(entry: Dict) -> Dict:
    title = entry.get("title") or _fallback_title(entry)
    artist = entry.get("artist") or "Unknown Artist"
    album = entry.get("album") or "Unknown Album"
    genre = entry.get("genre") or "Unknown Genre"
    return {
        "title": title,
        "artist": artist,
        "album": album,
        "genre": genre,
        "path": entry.get("path"),
        "year": entry.get("year"),
    }


def build_dj_library_index() -> Dict:
    index = get_music_index()
    entries = list(index.get("files", {}).values())
    artists_map: Dict[str, Dict[str, Dict]] = {}
    genres_map: Dict[str, Dict] = {}

    for entry in entries:
        track_payload = _build_track_payload(entry)
        artist = track_payload["artist"]
        album = track_payload["album"]
        genre = track_payload["genre"]

        artist_bucket = artists_map.setdefault(artist, {})
        album_bucket = artist_bucket.setdefault(album, {"tracks": []})
        album_bucket["tracks"].append(track_payload)

        genre_bucket = genres_map.setdefault(genre, {"tracks": [], "artists": set()})
        genre_bucket["tracks"].append(track_payload)
        genre_bucket["artists"].add(artist)

    artists_payload = []
    for artist_name in sorted(artists_map.keys(), key=lambda name: name.lower()):
        albums_payload = []
        albums_map = artists_map[artist_name]
        for album_name in sorted(albums_map.keys(), key=lambda name: name.lower()):
            tracks = albums_map[album_name].get("tracks", [])
            tracks.sort(key=lambda track: (track.get("title") or "").lower())
            albums_payload.append({"name": album_name, "tracks": tracks})
        artists_payload.append({"name": artist_name, "albums": albums_payload})

    genres_payload = []
    for genre_name in sorted(genres_map.keys(), key=lambda name: name.lower()):
        genre_payload = genres_map[genre_name]
        tracks = genre_payload.get("tracks", [])
        tracks.sort(
            key=lambda track: (
                (track.get("artist") or "").lower(),
                (track.get("album") or "").lower(),
                (track.get("title") or "").lower(),
            )
        )
        genres_payload.append(
            {
                "name": genre_name,
                "artists": sorted(genre_payload.get("artists", set()), key=lambda name: name.lower()),
                "tracks": tracks,
            }
        )

    return {
        "artists": artists_payload,
        "genres": genres_payload,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def search_dj_library(query: str) -> List[Dict]:
    query_norm = _normalize(query)
    if not query_norm:
        return []
    index = get_music_index()
    entries = list(index.get("files", {}).values())
    results = []
    for entry in entries:
        title = entry.get("title") or _fallback_title(entry)
        artist = entry.get("artist") or ""
        album = entry.get("album") or ""
        if (
            query_norm in _normalize(title)
            or query_norm in _normalize(artist)
            or query_norm in _normalize(album)
        ):
            payload = _build_track_payload(entry)
            results.append(payload)
    results.sort(
        key=lambda track: (
            (track.get("artist") or "").lower(),
            (track.get("album") or "").lower(),
            (track.get("title") or "").lower(),
        )
    )
    return results


def _spotify_playlist_id(playlist_url: str) -> Optional[str]:
    if playlist_url.startswith("spotify:playlist:"):
        return playlist_url.split(":")[-1]
    parsed = urlparse(playlist_url)
    if parsed.netloc.endswith("spotify.com"):
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "playlist":
            return parts[1]
    return None


def _fetch_spotify_playlist(playlist_url: str) -> Dict:
    playlist_id = _spotify_playlist_id(playlist_url)
    if not playlist_id:
        return {"error": "Unable to detect a Spotify playlist ID from that link."}
    api_url = f"https://open.spotify.com/playlist/{playlist_id}?__a=1&__d=discovery"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(api_url, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        return {"error": "Unable to fetch playlist details from Spotify."}

    try:
        data = resp.json()
    except ValueError:
        return {"error": "Spotify playlist response was not JSON."}

    tracks_block = data.get("tracks") or {}
    items = tracks_block.get("items") or []
    playlist_name = data.get("name") or "Spotify Playlist"
    tracks = []
    for item in items:
        track = item.get("track") or {}
        title = track.get("name")
        artists = [artist.get("name") for artist in track.get("artists", []) if artist.get("name")]
        album = (track.get("album") or {}).get("name")
        if not title:
            continue
        tracks.append({"title": title, "artists": artists, "album": album})

    if not tracks:
        return {"error": "Spotify playlist contained no tracks."}

    return {"name": playlist_name, "tracks": tracks, "id": playlist_id}


def _match_score(query: str, candidate: str) -> float:
    return difflib.SequenceMatcher(None, query, candidate).ratio()


def match_spotify_playlist(playlist_payload: Dict) -> Dict:
    if playlist_payload.get("error"):
        return playlist_payload
    tracks = playlist_payload.get("tracks") or []
    index = get_music_index()
    entries = list(index.get("files", {}).values())
    library_tracks = []
    for entry in entries:
        payload = _build_track_payload(entry)
        key = _normalize(f"{payload['title']} {payload['artist']}")
        library_tracks.append({**payload, "key": key})

    matches = []
    missing = []
    for track in tracks:
        title = track.get("title") or ""
        artists = track.get("artists") or []
        artist = ", ".join(artists) if artists else ""
        spotify_key = _normalize(f"{title} {artist}")
        best = None
        best_score = 0.0
        for candidate in library_tracks:
            score = _match_score(spotify_key, candidate.get("key") or "")
            if score > best_score:
                best_score = score
                best = candidate
        if best and best_score >= 0.6:
            matches.append(
                {
                    "spotify_title": title,
                    "spotify_artist": artist,
                    "album": best.get("album"),
                    "library_title": best.get("title"),
                    "library_artist": best.get("artist"),
                    "path": best.get("path"),
                    "score": round(best_score, 3),
                }
            )
        else:
            missing.append({"spotify_title": title, "spotify_artist": artist})

    playlist_text = render_playlist_text(
        playlist_payload.get("name") or "Spotify Playlist",
        playlist_payload.get("id"),
        matches,
        missing,
    )
    return {
        "name": playlist_payload.get("name") or "Spotify Playlist",
        "matches": matches,
        "missing": missing,
        "playlist_text": playlist_text,
    }


def render_playlist_text(
    name: str,
    playlist_id: Optional[str],
    matches: List[Dict],
    missing: List[Dict],
) -> str:
    header = [
        "# RAMS_PLAYLIST_V1",
        f"# Name: {name}",
        f"# Generated: {datetime.utcnow().isoformat()}Z",
    ]
    if playlist_id:
        header.append(f"# Spotify Playlist: https://open.spotify.com/playlist/{playlist_id}")
    header.append("# Fields: path\ttitle\tartist\talbum")
    lines = []
    for match in matches:
        path = match.get("path") or ""
        line = "\t".join(
            [
                path,
                match.get("library_title") or "",
                match.get("library_artist") or "",
                match.get("album") or "",
            ]
        )
        lines.append(line)
    if missing:
        lines.append("# Missing tracks:")
        for track in missing:
            title = track.get("spotify_title") or ""
            artist = track.get("spotify_artist") or ""
            lines.append(f"# - {title} :: {artist}")
    return "\n".join(header + lines) + "\n"
