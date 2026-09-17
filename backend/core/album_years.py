"""
Original release year and record label of an album, in one lookup.

An album is a directory of tracks sharing an album tag. The year comes from
the MusicBrainz release group's first release date and the label from the
release's label info: one request when the album is tagged with a release id
(mb_album_id), otherwise a release-group search plus a browse of that group's
releases for the label. Discogs masters (with a token) carry both; iTunes is
a last resort and only knows the year. Results are cached in `album_years`
keyed by album, so a rerun only asks about new albums; writing the tags is a
separate step.
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


def _label_of(release: dict) -> str | None:
    """First named label of a release."""
    for info in release.get("label-info") or []:
        name = (info.get("label") or {}).get("name")
        if name:
            return name
    return None


def _group_label(release_group_id: str) -> str | None:
    """Label of the earliest release in a release group — the original issue."""
    releases = _mb_get("release", {"release-group": release_group_id, "inc": "labels", "limit": 50}).get("releases", [])
    labelled = [r for r in releases if _label_of(r)]
    if not labelled:
        return None
    return _label_of(min(labelled, key=lambda r: r.get("date") or "9999"))


def musicbrainz_album(artist: str, album: str, release_id: str | None) -> tuple[str | None, str | None, str | None]:
    """(year, label, release-group id) of the album's first release on MusicBrainz."""
    if release_id and _UUID.match(release_id):
        rel = _mb_get(f"release/{release_id}", {"inc": "release-groups labels"})
        rg = rel.get("release-group") or {}
        if _year(rg.get("first-release-date")):
            return _year(rg["first-release-date"]), _label_of(rel), rg.get("id")
    query = f'releasegroup:"{_lucene(_clean(album))}"'
    if artist:
        query += f' AND artist:"{_lucene(artist)}"'
    hits = _mb_get("release-group", {"query": query, "limit": 10}).get("release-groups", [])
    same = [h for h in hits if h.get("score", 0) >= 80 and _norm(h.get("title", "")) == _norm(album)
            and _year(h.get("first-release-date"))]
    if not same:
        return None, None, None
    # Several groups by the same name (album, single, live): the earliest is the original.
    best = min(same, key=lambda h: (_year(h["first-release-date"]), -h.get("score", 0)))
    group_id = best.get("id")
    return _year(best["first-release-date"]), (_group_label(group_id) if group_id else None), group_id


def discogs_album(artist: str, album: str, token: str) -> tuple[str | None, str | None]:
    """(year, label) of the album's Discogs master release."""
    _pace_fallback()
    params = {"type": "master", "release_title": _clean(album), "per_page": 10, "token": token}
    if artist:
        params["artist"] = artist
    matches = []
    for item in _get_json(_DISCOGS_SEARCH + "?" + urllib.parse.urlencode(params)).get("results", []):
        # Titles read "Artist - Album"; homonyms are suffixed "Air (2)", variations "*".
        head, _, title = item.get("title", "").partition(" - ")
        head = re.sub(r"\s*\(\d+\)$", "", head.rstrip("*"))
        if _norm(title) == _norm(album) and (not artist or _same_name(head, artist)) and item.get("year"):
            matches.append((str(item["year"]), next(iter(item.get("label") or []), None)))
    if not matches:
        return None, None
    return min(matches, key=lambda m: m[0])


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


def fetch_album(artist: str, album: str, release_id: str | None) -> dict:
    """Ask MusicBrainz, then the fallbacks; returns {year, label, source, mbid, error}."""
    entry = {"year": None, "label": None, "source": None, "mbid": None, "error": None}
    errors = []
    try:
        entry["year"], entry["label"], entry["mbid"] = musicbrainz_album(artist, album, release_id)
        entry["source"] = "musicbrainz" if entry["year"] else None
    except MusicBrainzError as exc:
        errors.append(f"MusicBrainz: {exc}")

    if not entry["year"]:
        token = _discogs_token()
        fallbacks = ([("discogs", lambda: discogs_album(artist, album, token))] if token else []) + \
                    [("itunes", lambda: (itunes_year(artist, album), None))]
        for source, get in fallbacks:
            try:
                year, label = get()
            except ProviderError as exc:
                errors.append(f"{SOURCE_LABELS[source]}: {exc}")
                continue
            if year:
                entry.update(year=year, label=label, source=source)
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
        f"SELECT directory, artist, album_artist, album, year, label, mb_album_id FROM tracks {where} ORDER BY directory",
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
            "labels": dict(Counter(r["label"] or "" for r in tracks).most_common()),
            "key": album_key(artist, album, release_id),
        })
    return out


def cached(conn) -> dict[str, dict]:
    return {r["key"]: dict(r) for r in conn.execute("SELECT * FROM album_years")}


def store(conn, key: str, entry: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO album_years (key, year, label, source, mbid, error, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (key, entry["year"], entry["label"], entry["source"], entry["mbid"], entry["error"], time.time()),
    )


FIELDS = ("year", "label")


def _track_updates(track, hit: dict, fields: tuple[str, ...]) -> dict:
    """Fields of one track that the fetched album differs on."""
    updates = {}
    if "year" in fields and hit.get("year") and _year(track["year"]) != hit["year"]:
        updates["year"] = hit["year"]
    if "label" in fields and hit.get("label") and (track["label"] or "") != hit["label"]:
        updates["label"] = hit["label"]
    return updates


def proposals(conn) -> list[dict]:
    """Every tagged album with its fetched year and label, and how many tracks each would change."""
    cache = cached(conn)
    out = []
    for a in library_albums(conn):
        hit = cache.get(a.pop("key")) or {}
        year, label = hit.get("year"), hit.get("label")
        a.update(
            fetched=bool(hit),
            found_year=year,
            found_label=label,
            source=hit.get("source"),
            error=hit.get("error"),
            changed_year=sum(n for y, n in a["years"].items() if _year(y) != year) if year else 0,
            changed_label=sum(n for v, n in a["labels"].items() if v != label) if label else 0,
        )
        a["changed"] = max(a["changed_year"], a["changed_label"])
        out.append(a)
    return out


def album_updates(conn, directories: list[str], fields: tuple[str, ...] = FIELDS) -> list[tuple]:
    """
    [(track row, updates)] for tracks differing from their album's fetched data.
    Year and label are applied independently: `fields` picks which are written.
    """
    fields = tuple(f for f in fields if f in FIELDS)
    cache = cached(conn)
    found = {}
    for a in library_albums(conn, directories):
        hit = cache.get(a["key"])
        if hit and any(hit[f] for f in fields):
            found[a["directory"]] = hit
    if not found:
        return []
    rows = conn.execute(
        f"SELECT * FROM tracks WHERE album IS NOT NULL AND album != '' "
        f"AND directory IN ({','.join('?' * len(found))})",
        list(found),
    ).fetchall()
    plans = [(r, _track_updates(r, found[r["directory"]], fields)) for r in rows]
    return [(r, u) for r, u in plans if u]
