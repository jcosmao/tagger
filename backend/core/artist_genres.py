"""
Artist genres from MusicBrainz, with Discogs and iTunes as fallbacks.

Artists are grouped either by album artist (else artist) — a guest on someone
else's album or a compilation track stays with that album — or by the plain
artist tag. The genre cache is keyed by name and shared by both groupings.
MusicBrainz genres (the curated list, with vote counts) are fetched per artist
and cached in `artist_genres`; when MusicBrainz is unreachable or has no genres
for the artist, Discogs (if a token is set) then iTunes are asked instead, and
`source` records which one answered. Tagging is a separate, explicit step.
"""
from __future__ import annotations

import json
import random
import re
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
_DISCOGS_SEARCH = "https://api.discogs.com/database/search"
_ITUNES_ROOT = "https://itunes.apple.com/"
_USER_AGENT = "tagger/0.2 ( https://github.com/rnhinson/tagger )"
_MIN_INTERVAL = 1.1  # MusicBrainz allows one request per second per client
_lock = threading.Lock()
_last_request = 0.0

# Transient HTTP statuses worth retrying (rate limiting, overload, gateway hiccups).
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_ATTEMPTS = 5
_BASE_DELAY = 1.5
_MAX_DELAY = 20.0
# After MusicBrainz exhausts its retries, go straight to the fallbacks for a while
# so a bulk fetch doesn't spend a full backoff cycle on every artist.
_MB_COOLDOWN = 120.0
_mb_down_until = 0.0


SOURCE_LABELS = {"musicbrainz": "MusicBrainz", "discogs": "Discogs", "itunes": "iTunes"}


class MusicBrainzError(Exception):
    pass


class ProviderError(Exception):
    pass


def _get_json(url: str, headers: dict | None = None, *, throttle: bool = False) -> dict:
    """GET JSON, retrying transient failures with exponential backoff and jitter.

    Honours Retry-After. With `throttle`, requests are spaced by _MIN_INTERVAL
    across threads (MusicBrainz rate limit). Raises ProviderError when giving up.
    """
    global _last_request
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json",
                                               **(headers or {})})
    for attempt in range(_ATTEMPTS):
        last = attempt == _ATTEMPTS - 1
        retry_after = None
        if throttle:
            with _lock:
                wait = _last_request + _MIN_INTERVAL - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                return json.load(res)
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRY_STATUSES or last:
                raise ProviderError(f"{exc.code} {exc.reason}") from exc
            retry_after = _retry_after(exc.headers.get("Retry-After"))
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
            if last:
                raise ProviderError(str(getattr(exc, "reason", exc))) from exc
        delay = min(_MAX_DELAY, _BASE_DELAY * 2 ** attempt) * random.uniform(0.75, 1.25)
        time.sleep(min(_MAX_DELAY, max(delay, retry_after or 0)))
    raise ProviderError("unreachable")


def _retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def reset_musicbrainz_cooldown() -> None:
    """Let an explicit, single-artist fetch try MusicBrainz again right away."""
    global _mb_down_until
    _mb_down_until = 0.0


def _mb_get(path: str, params: dict) -> dict:
    """GET a MusicBrainz JSON resource, rate-limited across threads, with retries."""
    global _mb_down_until
    if time.monotonic() < _mb_down_until:
        raise MusicBrainzError("unavailable, retrying later")
    url = _MB_ROOT + path + "?" + urllib.parse.urlencode({**params, "fmt": "json"})
    try:
        return _get_json(url, throttle=True)
    except ProviderError as exc:
        msg = str(exc)
        if not msg.startswith(("400", "404")):
            _mb_down_until = time.monotonic() + _MB_COOLDOWN
        raise MusicBrainzError(msg) from exc


def _same_name(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def _ranked(counts: Counter) -> list[dict]:
    return [{"name": n, "count": c} for n, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))]


def discogs_genres(artist: str, token: str) -> list[dict]:
    """Genres and styles over the artist's Discogs masters, counted per master."""
    params = {"type": "master", "artist": artist, "per_page": 100, "token": token}
    data = _get_json(_DISCOGS_SEARCH + "?" + urllib.parse.urlencode(params))
    counts: Counter = Counter()
    for item in data.get("results", []):
        # Titles read "Artist - Album"; the artist filter is fuzzy, keep exact matches.
        # Discogs suffixes homonyms "Air (2)" and name variations with "*".
        title_artist = re.sub(r"\s*\(\d+\)$", "", item.get("title", "").split(" - ", 1)[0].rstrip("*"))
        if not _same_name(title_artist, artist):
            continue
        counts.update({g for g in (item.get("style") or []) + (item.get("genre") or []) if g})
    return _ranked(counts)


def itunes_genres(artist: str) -> list[dict]:
    """Primary genres of the artist's albums on the iTunes Store (no key needed)."""
    query = urllib.parse.urlencode({"term": artist, "entity": "musicArtist", "limit": 10})
    hits = [h for h in _get_json(_ITUNES_ROOT + "search?" + query).get("results", [])
            if _same_name(h.get("artistName", ""), artist)]
    if not hits:
        return []
    lookup = urllib.parse.urlencode({"id": hits[0]["artistId"], "entity": "album", "limit": 200})
    counts: Counter = Counter(
        r["primaryGenreName"] for r in _get_json(_ITUNES_ROOT + "lookup?" + lookup).get("results", [])
        if r.get("wrapperType") == "collection" and r.get("primaryGenreName")
    )
    if not counts and hits[0].get("primaryGenreName"):
        counts[hits[0]["primaryGenreName"]] = 1
    return _ranked(counts)


def _discogs_token() -> str:
    from api.config import _load  # local import: api imports core
    try:
        return _load().discogs_token
    except Exception:
        return ""


def _fallbacks() -> list[tuple[str, object]]:
    """Genre sources tried, in order, when MusicBrainz fails or has no genres."""
    out = []
    token = _discogs_token()
    if token:
        out.append(("discogs", lambda a: discogs_genres(a, token)))
    out.append(("itunes", itunes_genres))
    return out


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
    """Resolve the artist on MusicBrainz, fetch its genres (else from a fallback), cache and return."""
    previous = cached(conn, artist)
    candidates = previous["candidates"] if previous else []
    entry = {"mbid": None, "mb_name": None, "disambiguation": None, "genres": [], "error": None,
             "source": "musicbrainz"}
    errors = []
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
        entry.update(mbid=mbid)
        errors.append(f"MusicBrainz: {exc}")

    if not entry["genres"]:
        # A name search is meaningless for the "unknown artist" bucket.
        for source, get in (_fallbacks() if artist.strip() else []):
            try:
                genres = get(artist)
            except ProviderError as exc:
                errors.append(f"{SOURCE_LABELS[source]}: {exc}")
                continue
            if genres:
                entry.update(genres=genres, source=source)
                break
    if not entry["genres"] and errors:
        entry["error"] = "; ".join(errors)

    conn.execute(
        "INSERT OR REPLACE INTO artist_genres "
        "(artist, mbid, mb_name, disambiguation, genres, candidates, fetched_at, error, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (artist, entry["mbid"], entry["mb_name"], entry["disambiguation"],
         json.dumps(entry["genres"]), json.dumps(candidates), time.time(), entry["error"], entry["source"]),
    )
    return cached(conn, artist)
