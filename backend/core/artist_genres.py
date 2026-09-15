"""
Artist genres from MusicBrainz.

Artists are grouped either by album artist (else artist) — a guest on someone
else's album or a compilation track stays with that album — or by the plain
artist tag. The genre cache is keyed by name and shared by both groupings.
MusicBrainz genres (the curated list, with vote counts) are fetched per artist
and cached in `artist_genres`; tagging with them is a separate, explicit step.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

ARTIST_KEY = "COALESCE(NULLIF(album_artist, ''), NULLIF(artist, ''), '')"

# SQL expression naming a track's artist, per grouping.
GROUPINGS = {"album_artist": ARTIST_KEY, "artist": "COALESCE(artist, '')"}

_MB_ROOT = "https://musicbrainz.org/ws/2/"
_USER_AGENT = "tagger/0.2 ( https://github.com/rnhinson/tagger )"
_MIN_INTERVAL = 1.1  # MusicBrainz allows one request per second per client
_lock = threading.Lock()
_last_request = 0.0


class MusicBrainzError(Exception):
    pass


def _mb_get(path: str, params: dict) -> dict:
    """GET a MusicBrainz JSON resource, rate-limited across threads, retrying 503s."""
    global _last_request
    url = _MB_ROOT + path + "?" + urllib.parse.urlencode({**params, "fmt": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    delay = 2.0
    for attempt in range(3):
        with _lock:
            wait = _last_request + _MIN_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last_request = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=15) as res:
                    return json.load(res)
            except urllib.error.HTTPError as exc:
                if exc.code != 503 or attempt == 2:
                    raise MusicBrainzError(f"{exc.code} {exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == 2:
                    raise MusicBrainzError(str(exc)) from exc
        time.sleep(delay)
        delay *= 2
    raise MusicBrainzError("unreachable")


def library_case(name: str, known: list[str]) -> str:
    """Spell a MusicBrainz genre the way the library already does, else Title Case."""
    by_fold = {k.casefold(): k for k in known}
    if name.casefold() in by_fold:
        return by_fold[name.casefold()]
    return " ".join("-".join(p[:1].upper() + p[1:] for p in word.split("-")) for word in name.split(" "))


def pick_match(name: str, candidates: list[dict]) -> dict | None:
    """Best search hit: an exact (case-insensitive) name match, highest score first."""
    exact = [c for c in candidates if c.get("name", "").casefold() == name.casefold()]
    if exact:
        return max(exact, key=lambda c: c.get("score", 0))
    top = max(candidates, key=lambda c: c.get("score", 0), default=None)
    return top if top and top.get("score", 0) >= 90 else None


def _tagged_mbid(conn, artist: str, by: str = "album_artist") -> str | None:
    """Most common MusicBrainz id already present on the artist's tracks."""
    id_col = ("mb_artist_id" if by == "artist" else
              "CASE WHEN COALESCE(album_artist, '') != '' THEN mb_album_artist_id ELSE mb_artist_id END")
    rows = conn.execute(
        f"SELECT {id_col} FROM tracks WHERE {GROUPINGS[by]} = ?",
        (artist,),
    ).fetchall()
    ids = Counter(r[0] for r in rows if r[0])
    return ids.most_common(1)[0][0] if ids else None


def _row(r) -> dict:
    out = dict(r)
    out["genres"] = json.loads(out["genres"] or "[]")
    out["candidates"] = json.loads(out["candidates"] or "[]")
    return out


def cached(conn, artist: str) -> dict | None:
    r = conn.execute("SELECT * FROM artist_genres WHERE artist = ?", (artist,)).fetchone()
    return _row(r) if r else None


def fetch_artist(conn, artist: str, mbid: str | None = None, by: str = "album_artist") -> dict:
    """Resolve the artist on MusicBrainz, fetch its genres, cache and return the result."""
    previous = cached(conn, artist)
    candidates = previous["candidates"] if previous else []
    entry = {"mbid": None, "mb_name": None, "disambiguation": None, "genres": [], "error": None}
    try:
        if not mbid:
            mbid = _tagged_mbid(conn, artist, by)
        if not mbid:
            hits = _mb_get("artist", {"query": f'artist:"{artist}"', "limit": 10}).get("artists", [])
            match = pick_match(artist, hits)
            # Homonyms worth offering as alternatives: same name as the match.
            candidates = [
                {k: c.get(k) for k in ("id", "name", "disambiguation", "score")}
                for c in hits if match and c.get("name", "").casefold() == match["name"].casefold()
            ]
            mbid = match["id"] if match else None
        if mbid:
            data = _mb_get(f"artist/{mbid}", {"inc": "genres"})
            entry.update(
                mbid=mbid,
                mb_name=data.get("name"),
                disambiguation=data.get("disambiguation") or None,
                genres=[{"name": g["name"], "count": g.get("count", 0)}
                        for g in sorted(data.get("genres", []), key=lambda g: -g.get("count", 0))],
            )
    except MusicBrainzError as exc:
        entry.update(mbid=mbid, error=str(exc))

    conn.execute(
        "INSERT OR REPLACE INTO artist_genres "
        "(artist, mbid, mb_name, disambiguation, genres, candidates, fetched_at, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (artist, entry["mbid"], entry["mb_name"], entry["disambiguation"],
         json.dumps(entry["genres"]), json.dumps(candidates), time.time(), entry["error"]),
    )
    return cached(conn, artist)
