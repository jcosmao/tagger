"""Artist view: album artists, their MusicBrainz genres, and genre retagging."""
from __future__ import annotations

from collections import Counter
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from core.artist_genres import ARTIST_KEY, cached, fetch_artist, library_case
from core.database import db
from core.genres import split_genres
from core.tasks import active_job, create_job, run_fetch_genres_job, run_retag_artist_job

router = APIRouter()


class FetchArtist(BaseModel):
    artist: str
    mbid: Optional[str] = None


class FetchAll(BaseModel):
    refresh: bool = False


class RetagArtist(BaseModel):
    artist: str
    genres: list[str]
    mode: Literal["replace", "add"]


def _conflict(running: dict):
    raise HTTPException(409, {"detail": f"A {running['kind']} job is already running", "job_id": running["id"]})


def _library_genres(conn) -> list[str]:
    rows = conn.execute("SELECT DISTINCT genre FROM tracks WHERE genre != ''").fetchall()
    return list({g for r in rows for g in split_genres(r[0])})


def _detail(conn, artist: str) -> dict:
    rows = conn.execute(f"SELECT genre FROM tracks WHERE {ARTIST_KEY} = ?", (artist,)).fetchall()
    counts = Counter(g for r in rows for g in split_genres(r[0]))
    mb = cached(conn, artist)
    if mb:
        known = _library_genres(conn)
        for g in mb["genres"]:
            g["label"] = library_case(g["name"], known)
    return {
        "artist": artist,
        "track_count": len(rows),
        "current_genres": [{"genre": g, "track_count": n}
                           for g, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))],
        "mb": mb,
    }


@router.get("")
def list_artists():
    """Album artists (else artists) with track counts and whether genres were fetched."""
    with db() as conn:
        # ARTIST_KEY names unqualified columns (`artist` also exists in
        # artist_genres), so it can't go in a subquery on that table.
        fetched = {r[0] for r in conn.execute("SELECT artist FROM artist_genres")}
        rows = conn.execute(
            f"SELECT {ARTIST_KEY} AS artist, COUNT(*) AS track_count "
            f"FROM tracks GROUP BY 1 ORDER BY 1 COLLATE NOCASE"
        ).fetchall()
    return [{"artist": r["artist"], "track_count": r["track_count"], "fetched": r["artist"] in fetched}
            for r in rows]


@router.get("/detail")
def artist_detail(name: str):
    with db() as conn:
        return _detail(conn, name)


@router.post("/fetch")
def fetch(req: FetchArtist):
    """Fetch (or re-fetch, optionally for a chosen MusicBrainz artist) one artist's genres."""
    with db() as conn:
        fetch_artist(conn, req.artist, req.mbid)
        return _detail(conn, req.artist)


@router.post("/fetch-all")
async def fetch_all(req: FetchAll, background_tasks: BackgroundTasks):
    running = active_job("fetch")
    if running:
        _conflict(running)
    job_id = create_job("fetch")
    background_tasks.add_task(run_fetch_genres_job, job_id, req.refresh)
    return {"job_id": job_id}


@router.post("/retag")
async def retag(req: RetagArtist, background_tasks: BackgroundTasks):
    """Replace, or add to, the genres of every track of an artist (background job)."""
    genres = split_genres(req.genres)
    if not genres:
        raise HTTPException(400, "No genres given")
    running = active_job()
    if running:
        _conflict(running)
    job_id = create_job("retag")
    background_tasks.add_task(run_retag_artist_job, job_id, req.artist, genres, req.mode)
    return {"job_id": job_id}
