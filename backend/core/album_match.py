"""
Whole-album lookup: tag every track of an album from one MusicBrainz release.

Looking tracks up one by one costs a search (or a fingerprint plus a recording
fetch per match) for every track, and each track may land on a different
edition. Here an album costs two requests: find the release (or use the
tagged mb_album_id), fetch its full tracklist, then pair local tracks with
release tracks on title, position and duration — all locally.
"""
from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from core.album_years import _UUID, _clean, _label_of, _lucene, _norm, _year
from core.artist_genres import _mb_get

# A pair scoring below this is not proposed; the track is left for a per-track lookup.
MIN_MATCH = 0.55


def _credit(credits: list[dict] | None) -> tuple[str | None, str | None]:
    """(display name, first artist id) of a MusicBrainz artist credit."""
    if not credits:
        return None, None
    name = "".join(c.get("name", "") + c.get("joinphrase", "") for c in credits)
    return name or None, (credits[0].get("artist") or {}).get("id")


def _int(value) -> int | None:
    m = re.match(r"\s*(\d+)", str(value or ""))
    return int(m.group(1)) if m else None


def find_release(artist: str, album: str, track_count: int) -> list[dict]:
    """Releases matching the album title and artist, best first: closest track count, official, earliest."""
    query = f'release:"{_lucene(_clean(album))}"'
    if artist:
        query += f' AND artist:"{_lucene(artist)}"'
    hits = _mb_get("release", {"query": query, "limit": 25}).get("releases", [])
    same = [h for h in hits if h.get("score", 0) >= 80 and _norm(h.get("title", "")) == _norm(album)]

    def rank(h):
        count = h.get("track-count") or sum(m.get("track-count", 0) for m in h.get("media", []))
        return (abs(count - track_count), h.get("status") != "Official",
                h.get("title", "").casefold() != album.casefold(), h.get("date") or "9999", -h.get("score", 0))

    return sorted(same, key=rank)


def get_release(release_id: str) -> dict:
    """A release with its whole tracklist, flattened to what tagging needs."""
    rel = _mb_get(f"release/{release_id}", {"inc": "recordings artist-credits release-groups labels"})
    album_artist, album_artist_id = _credit(rel.get("artist-credit"))
    rg = rel.get("release-group") or {}
    tracks = []
    for medium in rel.get("media", []):
        for t in medium.get("tracks", []):
            rec = t.get("recording") or {}
            artist, artist_id = _credit(t.get("artist-credit") or rec.get("artist-credit"))
            tracks.append({
                "title": t.get("title") or rec.get("title"),
                "artist": artist or album_artist,
                "mb_artist_id": artist_id or album_artist_id,
                "mb_track_id": rec.get("id"),
                "track_number": str(t.get("position") or t.get("number") or ""),
                "disc_number": str(medium.get("position") or 1),
                "length": (t.get("length") or rec.get("length") or 0) / 1000 or None,
            })
    return {
        "id": rel.get("id"),
        "title": rel.get("title"),
        "artist": album_artist,
        "artist_id": album_artist_id,
        "date": rel.get("date"),
        "country": rel.get("country"),
        "label": _label_of(rel),
        "year": _year(rg.get("first-release-date")) or _year(rel.get("date")),
        "disc_count": len(rel.get("media", [])),
        "tracks": tracks,
    }


def _local_title(track: dict) -> str:
    if track.get("title"):
        return track["title"]
    # "03 - Song.mp3" / "1-03 Song.flac" → "Song"
    return re.sub(r"^[\d\s._-]+", "", Path(track.get("filename") or "").stem)


def pair_score(local: dict, remote: dict, multi_disc: bool) -> float:
    """How likely a local track is this release track, 0–1."""
    title = SequenceMatcher(None, _norm(_local_title(local)), _norm(remote["title"] or "")).ratio()

    position = 0.5  # unknown
    number = _int(local.get("track_number"))
    if number is not None:
        disc = _int(local.get("disc_number")) or 1
        same_disc = not multi_disc or disc == int(remote["disc_number"])
        position = 1.0 if same_disc and number == int(remote["track_number"] or 0) else 0.0

    duration = 0.5
    if local.get("duration") and remote["length"]:
        diff = abs(local["duration"] - remote["length"])
        duration = 1.0 if diff <= 3 else 0.6 if diff <= 8 else 0.0

    return 0.6 * title + 0.25 * position + 0.15 * duration


def match_tracks(local: list[dict], release: dict) -> tuple[list[dict], list[int]]:
    """Pair each local track with at most one release track (best pairs first)."""
    multi_disc = release["disc_count"] > 1
    pairs = sorted(
        ((pair_score(l, r, multi_disc), i, j) for i, l in enumerate(local) for j, r in enumerate(release["tracks"])),
        reverse=True,
    )
    used_local, used_remote, matches = set(), set(), []
    for score, i, j in pairs:
        if score < MIN_MATCH:
            break
        if i in used_local or j in used_remote:
            continue
        used_local.add(i)
        used_remote.add(j)
        r = release["tracks"][j]
        matches.append({
            "track_id": local[i]["id"],
            "score": round(score, 3),
            "update": {k: v for k, v in {
                "title": r["title"],
                "artist": r["artist"],
                "album": release["title"],
                "album_artist": release["artist"],
                "year": release["year"],
                "label": release["label"],
                "track_number": r["track_number"],
                "disc_number": r["disc_number"],
                "mb_track_id": r["mb_track_id"],
                "mb_artist_id": r["mb_artist_id"],
                "mb_album_id": release["id"],
                "mb_album_artist_id": release["artist_id"],
            }.items() if v},
        })
    unmatched = [t["id"] for i, t in enumerate(local) if i not in used_local]
    return matches, unmatched


def _most_common(values) -> str:
    counts = Counter(v for v in values if v)
    return counts.most_common(1)[0][0] if counts else ""


def lookup_album(local: list[dict], release_id: str | None = None) -> dict:
    """
    Resolve the release of these tracks (one album) and propose tags for each.
    `release_id` forces a release (picked among the candidates); otherwise the
    tagged mb_album_id is used, else the best search hit.
    """
    release_id = release_id or _most_common(t.get("mb_album_id") for t in local)
    candidates: list[dict] = []
    if not (release_id and _UUID.match(release_id)):
        artist = _most_common(t.get("album_artist") or t.get("artist") for t in local)
        album = _most_common(t.get("album") for t in local)
        if not album:
            return {"release": None, "candidates": [], "matches": [], "unmatched": [t["id"] for t in local]}
        candidates = find_release(artist, album, len(local))
        release_id = candidates[0]["id"] if candidates else ""
    if not release_id:
        return {"release": None, "candidates": [], "matches": [], "unmatched": [t["id"] for t in local]}

    release = get_release(release_id)
    matches, unmatched = match_tracks(local, release)
    summary = {k: release[k] for k in ("id", "title", "artist", "date", "country", "year", "label")}
    summary["track_count"] = len(release["tracks"])
    return {
        "release": summary,
        "candidates": [
            {"id": c["id"], "title": c.get("title"), "date": c.get("date"), "country": c.get("country"),
             "track_count": c.get("track-count"), "status": c.get("status")}
            for c in candidates[:10]
        ],
        "matches": matches,
        "unmatched": unmatched,
    }
