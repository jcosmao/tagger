"""
Original release years for albums, one lookup per album.

An album is a directory of tracks sharing an album tag. Its year comes from
the MusicBrainz release group's first release date: read straight from the
tagged release (mb_album_id) when there is one, else from a release-group
search. Discogs masters (with a token) then the iTunes Store are asked when
MusicBrainz has nothing. Results are cached in `album_years` keyed by album,
so a rerun only asks about new albums; writing the year is a separate step.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.parse
from collections import Counter

from core.artist_genres import (
    _DISCOGS_SEARCH, _ITUNES_ROOT, MusicBrainzError, ProviderError, SOURCE_LABELS,
    _discogs_token, _get_json, _mb_get, _same_name,
)

_YEAR = re.compile(r"\s*(\d{4})")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Discogs allows 60 requests a minute, iTunes about 20: space fallback calls
# so they stay polite even while MusicBrainz is down and never paces them.
_FALLBACK_INTERVAL = 3.0
_fallback_lock = threading.Lock()
_last_fallback = 0.0


def _year(value: str | None) -> str | None:
    m = _YEAR.match(value or "")
    return m.group(1) if m else None


_EDITION = re.compile(r"\s*[(\[][^)\]]*(edition|remaster|deluxe|version|expanded|anniversary)[^)\]]*[)\]]", re.I)


def _clean(title: str) -> str:
    """Album title without edition suffixes: "Abbey Road (2019 Remaster)" → "Abbey Road"."""
    return _EDITION.sub("", title).strip() or title


def _norm(title: str) -> str:
    """Album title for comparison: case, punctuation and edition suffixes ignored."""
    return re.sub(r"[^\w]+", " ", _clean(title).casefold()).strip()


def _lucene(text: str) -> str:
    return re.sub(r'([\\"])', r"\\\1", text)


def _pace_fallback() -> None:
    global _last_fallback
    with _fallback_lock:
        wait = _last_fallback + _FALLBACK_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_fallback = time.monotonic()


def musicbrainz_year(artist: str, album: str, release_id: str | None) -> tuple[str | None, str | None]:
    """(year, release-group id) of the album's first release on MusicBrainz."""
    if release_id and _UUID.match(release_id):
        rg = _mb_get(f"release/{release_id}", {"inc": "release-groups"}).get("release-group") or {}
        if _year(rg.get("first-release-date")):
            return _year(rg["first-release-date"]), rg.get("id")
    query = f'releasegroup:"{_lucene(_clean(album))}"'
    if artist:
        query += f' AND artist:"{_lucene(artist)}"'
    hits = _mb_get("release-group", {"query": query, "limit": 10}).get("release-groups", [])
    same = [h for h in hits if h.get("score", 0) >= 80 and _norm(h.get("title", "")) == _norm(album)
            and _year(h.get("first-release-date"))]
    if not same:
        return None, None
    # Several groups by the same name (album, single, live): the earliest is the original.
    best = min(same, key=lambda h: (_year(h["first-release-date"]), -h.get("score", 0)))
    return _year(best["first-release-date"]), best.get("id")


def discogs_year(artist: str, album: str, token: str) -> str | None:
    """Year of the album's Discogs master release."""
    _pace_fallback()
    params = {"type": "master", "release_title": _clean(album), "per_page": 10, "token": token}
    if artist:
        params["artist"] = artist
    years = []
    for item in _get_json(_DISCOGS_SEARCH + "?" + urllib.parse.urlencode(params)).get("results", []):
        # Titles read "Artist - Album"; homonyms are suffixed "Air (2)", variations "*".
        head, _, title = item.get("title", "").partition(" - ")
        head = re.sub(r"\s*\(\d+\)$", "", head.rstrip("*"))
        if _norm(title) == _norm(album) and (not artist or _same_name(head, artist)) and item.get("year"):
            years.append(str(item["year"]))
    return min(years) if years else None


def itunes_year(artist: str, album: str) -> str | None:
    """Earliest release year of matching albums on the iTunes Store (often a reissue date)."""
    _pace_fallback()
    query = urllib.parse.urlencode({"term": f"{artist} {_clean(album)}".strip(), "entity": "album", "limit": 25})
    years = [
        _year(r.get("releaseDate"))
        for r in _get_json(_ITUNES_ROOT + "search?" + query).get("results", [])
        if _norm(r.get("collectionName", "")) == _norm(album)
        and (not artist or _same_name(r.get("artistName", ""), artist))
    ]
    years = [y for y in years if y]
    return min(years) if years else None


def fetch_year(artist: str, album: str, release_id: str | None) -> dict:
    """Ask MusicBrainz, then the fallbacks; returns {year, source, mbid, error}."""
    entry = {"year": None, "source": None, "mbid": None, "error": None}
    errors = []
    try:
        entry["year"], entry["mbid"] = musicbrainz_year(artist, album, release_id)
        entry["source"] = "musicbrainz" if entry["year"] else None
    except MusicBrainzError as exc:
        errors.append(f"MusicBrainz: {exc}")

    if not entry["year"]:
        token = _discogs_token()
        fallbacks = ([("discogs", lambda: discogs_year(artist, album, token))] if token else []) + \
                    [("itunes", lambda: itunes_year(artist, album))]
        for source, get in fallbacks:
            try:
                year = get()
            except ProviderError as exc:
                errors.append(f"{SOURCE_LABELS[source]}: {exc}")
                continue
            if year:
                entry.update(year=year, source=source)
                break
    if not entry["year"] and errors:
        entry["error"] = "; ".join(errors)
    return entry


# ─── Library albums ───────────────────────────────────────────────────────────

def _most_common(values) -> str:
    counts = Counter(v for v in values if v)
    return counts.most_common(1)[0][0] if counts else ""


def album_key(artist: str, album: str, release_id: str) -> str:
    return f"mb:{release_id.lower()}" if release_id else f"{artist.casefold()}\x1f{album.casefold()}"


def library_albums(conn, directories: list[str] | None = None) -> list[dict]:
    """Tagged albums (one per directory) with their track years and cache key."""
    where, params = "WHERE album IS NOT NULL AND album != ''", []
    if directories is not None:
        where += f" AND directory IN ({','.join('?' * len(directories))})"
        params = directories
    rows = conn.execute(
        f"SELECT directory, artist, album_artist, album, year, mb_album_id FROM tracks {where} ORDER BY directory",
        params,
    ).fetchall()
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["directory"], []).append(r)
    out = []
    for directory, tracks in groups.items():
        artist = _most_common(r["album_artist"] or r["artist"] for r in tracks)
        album = _most_common(r["album"] for r in tracks)
        release_id = _most_common(r["mb_album_id"] for r in tracks)
        out.append({
            "directory": directory,
            "artist": artist,
            "album": album,
            "mb_album_id": release_id or None,
            "track_count": len(tracks),
            "years": dict(Counter(r["year"] or "" for r in tracks).most_common()),
            "key": album_key(artist, album, release_id),
        })
    return out


def cached(conn) -> dict[str, dict]:
    return {r["key"]: dict(r) for r in conn.execute("SELECT * FROM album_years")}


def store(conn, key: str, entry: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO album_years (key, year, source, mbid, error, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
        (key, entry["year"], entry["source"], entry["mbid"], entry["error"], time.time()),
    )


def proposals(conn) -> list[dict]:
    """Every tagged album with its fetched year, if any, and how many tracks it would change."""
    cache = cached(conn)
    out = []
    for a in library_albums(conn):
        hit = cache.get(a.pop("key"))
        found = hit["year"] if hit else None
        a.update(
            fetched=hit is not None,
            found_year=found,
            source=hit["source"] if hit else None,
            error=hit["error"] if hit else None,
            changed=sum(n for y, n in a["years"].items() if _year(y) != found) if found else 0,
        )
        out.append(a)
    return out


def year_updates(conn, directories: list[str]) -> list[tuple]:
    """[(track row, {"year": found})] for tracks whose year differs from their album's fetched year."""
    cache = cached(conn)
    found = {}
    for a in library_albums(conn, directories):
        hit = cache.get(a["key"])
        if hit and hit["year"]:
            found[a["directory"]] = hit["year"]
    if not found:
        return []
    rows = conn.execute(
        f"SELECT * FROM tracks WHERE album IS NOT NULL AND album != '' "
        f"AND directory IN ({','.join('?' * len(found))})",
        list(found),
    ).fetchall()
    return [(r, {"year": found[r["directory"]]}) for r in rows if _year(r["year"]) != found[r["directory"]]]
