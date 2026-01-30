from __future__ import annotations

import difflib
import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests

from app.services.library.music_search import get_music_index


def _normalize(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _strip_brackets(value: str) -> str:
    return re.sub(r"[\(\[\{].*?[\)\]\}]", "", value).strip()


def _normalize_title(value: Optional[str]) -> str:
    cleaned = _strip_brackets(value or "")
    cleaned = re.sub(r"\bfeat\.?\b.*$", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    return cleaned.lower().strip()


def _normalize_artist(value: Optional[str]) -> str:
    cleaned = _strip_brackets(value or "")
    cleaned = re.sub(r"&", "and", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    return cleaned.lower().strip()


def _title_similarity(query: str, candidate: str) -> float:
    query_norm = _normalize_title(query)
    candidate_norm = _normalize_title(candidate)
    if not query_norm or not candidate_norm:
        return 0.0
    if query_norm == candidate_norm:
        return 1.0
    return difflib.SequenceMatcher(None, query_norm, candidate_norm).ratio()


def _artist_similarity(query: str, candidate: str) -> float:
    query_norm = _normalize_artist(query)
    candidate_norm = _normalize_artist(candidate)
    if not query_norm or not candidate_norm:
        return 0.0
    if query_norm == candidate_norm:
        return 1.0
    return difflib.SequenceMatcher(None, query_norm, candidate_norm).ratio()


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


def parse_text_playlist(text: str) -> List[Dict]:
    entries = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if " - " in line:
            artist, title = line.split(" - ", 1)
        elif " – " in line:
            artist, title = line.split(" – ", 1)
        else:
            artist, title = "", line
        entries.append({"artist": artist.strip(), "title": title.strip()})
    return entries


def _extract_youtube_titles(initial_data: Dict) -> List[str]:
    titles = []
    tabs = (
        initial_data.get("contents", {})
        .get("twoColumnBrowseResultsRenderer", {})
        .get("tabs", [])
    )
    for tab in tabs:
        content = tab.get("tabRenderer", {}).get("content", {})
        section_list = content.get("sectionListRenderer", {}).get("contents", [])
        for section in section_list:
            item_section = section.get("itemSectionRenderer", {}).get("contents", [])
            for item in item_section:
                playlist = item.get("playlistVideoListRenderer", {})
                for video in playlist.get("contents", []):
                    renderer = video.get("playlistVideoRenderer", {})
                    runs = renderer.get("title", {}).get("runs", [])
                    if runs and runs[0].get("text"):
                        titles.append(runs[0]["text"])
    return titles


def _fetch_youtube_playlist(playlist_url: str) -> Dict:
    parsed = urlparse(playlist_url)
    if "youtube.com" not in parsed.netloc and "youtu.be" not in parsed.netloc:
        return {"error": "Please provide a YouTube playlist URL."}
    if "list=" not in playlist_url:
        return {"error": "YouTube playlist URL is missing the list parameter."}

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(playlist_url, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        return {"error": "Unable to fetch YouTube playlist details."}

    match = re.search(r"ytInitialData\\s*=\\s*(\\{.*?\\});", resp.text, re.DOTALL)
    if not match:
        return {"error": "Unable to parse YouTube playlist response."}
    try:
        initial_data = json.loads(match.group(1))
    except ValueError:
        return {"error": "Unable to decode YouTube playlist response."}

    titles = _extract_youtube_titles(initial_data)
    if not titles:
        return {"error": "YouTube playlist contained no tracks."}
    entries = []
    for title in titles:
        parsed_entries = parse_text_playlist(title)
        if parsed_entries:
            entries.append(parsed_entries[0])
    return {"name": "YouTube Playlist", "entries": entries}


def _match_playlist_entries(name: str, entries: List[Dict], source_url: Optional[str] = None) -> Dict:
    index = get_music_index()
    library_tracks = []
    for entry in list(index.get("files", {}).values()):
        payload = _build_track_payload(entry)
        library_tracks.append({
            **payload,
            "title_norm": _normalize_title(payload.get("title")),
            "artist_norm": _normalize_artist(payload.get("artist")),
        })

    matches = []
    missing = []
    for entry in entries:
        title = entry.get("title") or ""
        artist = entry.get("artist") or ""
        if not title:
            continue
        title_score_best = 0.0
        artist_score_best = 0.0
        best = None
        best_score = 0.0
        for candidate in library_tracks:
            title_score = _title_similarity(title, candidate.get("title") or "")
            artist_score = _artist_similarity(artist, candidate.get("artist") or "")
            score = (title_score * 0.7) + (artist_score * 0.3)
            if score > best_score:
                best_score = score
                title_score_best = title_score
                artist_score_best = artist_score
                best = candidate
        title_threshold = 0.82
        artist_threshold = 0.72 if artist else 0.0
        overall_threshold = 0.78 if artist else 0.7
        if (
            best
            and title_score_best >= title_threshold
            and artist_score_best >= artist_threshold
            and best_score >= overall_threshold
        ):
            matches.append(
                {
                    "input_title": title,
                    "input_artist": artist,
                    "album": best.get("album"),
                    "library_title": best.get("title"),
                    "library_artist": best.get("artist"),
                    "path": best.get("path"),
                    "score": round(best_score, 3),
                }
            )
        else:
            missing.append({"input_title": title, "input_artist": artist})

    playlist_text = render_playlist_text(name, source_url, matches, missing)
    return {
        "name": name,
        "matches": matches,
        "missing": missing,
        "playlist_text": playlist_text,
    }


def match_text_playlist(name: str, text: str) -> Dict:
    entries = parse_text_playlist(text)
    if not entries:
        return {"error": "No tracks were found in the provided text."}
    return _match_playlist_entries(name, entries)


def match_youtube_playlist(playlist_url: str) -> Dict:
    payload = _fetch_youtube_playlist(playlist_url)
    if payload.get("error"):
        return payload
    entries = payload.get("entries") or []
    return _match_playlist_entries(payload.get("name") or "YouTube Playlist", entries, playlist_url)


def render_playlist_text(
    name: str,
    source_url: Optional[str],
    matches: List[Dict],
    missing: List[Dict],
) -> str:
    header = [
        "# RAMS_PLAYLIST_V1",
        f"# Name: {name}",
        f"# Generated: {datetime.utcnow().isoformat()}Z",
    ]
    if source_url:
        header.append(f"# Source: {source_url}")
    header.append("# Fields: path\ttitle\tartist\talbum")
    lines = []
    for match in matches:
        path = match.get("path") or ""
        line = "\t".join(
            [
                path,
                match.get("library_title") or match.get("title") or "",
                match.get("library_artist") or match.get("artist") or "",
                match.get("album") or "",
            ]
        )
        lines.append(line)
    if missing:
        lines.append("# Missing tracks:")
        for track in missing:
            title = track.get("input_title") or track.get("title") or ""
            artist = track.get("input_artist") or track.get("artist") or ""
            lines.append(f"# - {title} :: {artist}")
    return "\n".join(header + lines) + "\n"
