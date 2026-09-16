"""
Background job management — scan, album-unify and undo jobs persisted to
SQLite. Only one job runs at a time: they all rewrite the library.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from core.database import db

logger = logging.getLogger("tagger.scan")


def create_job(kind: str) -> str:
    job_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            "INSERT INTO scan_jobs (id, kind, status, started_at) VALUES (?, ?, 'pending', ?)",
            (job_id, kind, time.time()),
        )
    return job_id


def create_scan_job() -> str:
    return create_job("scan")


def get_job(job_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM scan_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def active_job(kind: str | None = None) -> dict | None:
    """
    Return the running/pending job that writes the library (scan, unify, retag,
    undo), or with `kind`, the running job of that kind. Genre fetch jobs only
    fill a cache, so they don't count as library jobs.
    """
    clause, params = ("kind = ?", (kind,)) if kind else ("kind != 'fetch'", ())
    with db() as conn:
        row = conn.execute(
            f"SELECT * FROM scan_jobs WHERE status IN ('pending', 'running') AND {clause} "
            "ORDER BY started_at DESC LIMIT 1",
            params,
        ).fetchone()
    return dict(row) if row else None


def list_jobs(limit: int = 20) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM scan_jobs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def _update_job(job_id: str, **kwargs: Any) -> None:
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    with db() as conn:
        conn.execute(
            f"UPDATE scan_jobs SET {sets} WHERE id = ?",
            (*kwargs.values(), job_id),
        )


async def auto_scan_loop() -> None:
    """
    Background freshness loop. When auto_scan_minutes > 0, runs a full scan on
    that interval (skipping if one is already running). Re-reads settings each
    cycle, so it can be toggled without a restart. Off by default.
    """
    from api.config import _load as load_app_settings

    while True:
        mins = 0
        try:
            mins = load_app_settings().auto_scan_minutes
        except Exception:
            pass
        # When disabled, idle in short hops so re-enabling takes effect quickly.
        await asyncio.sleep(max(1, mins) * 60 if mins else 60)
        if mins and not active_job():
            try:
                await run_scan_job(create_scan_job())
            except Exception:
                pass


async def run_scan_job(job_id: str, directory: str | None = None) -> None:
    """
    Run a library scan as a background task, updating job status in DB.

    With `directory`, only that subtree is scanned and pruned; otherwise the
    full set of configured music directories is scanned.
    """
    from core.scanner import scan_library
    from api.config import _load as load_app_settings, get_music_dirs

    _update_job(job_id, status="running")

    app_settings = load_app_settings()
    if directory:
        music_dirs = [directory]
        prune_under: list[str] | None = [directory]
    else:
        music_dirs = get_music_dirs()
        prune_under = None

    def progress(scanned: int, total: int) -> None:
        _update_job(job_id, scanned=scanned, total=total)

    logger.info("scan %s started (%s)", job_id, directory or "full library")
    started = time.time()
    try:
        total, upserted = await asyncio.to_thread(
            scan_library, progress, app_settings.scan_tags, music_dirs,
            prune_under, app_settings.scan_exclude, app_settings.genre_separators,
        )
        _update_job(
            job_id,
            status="done",
            finished_at=time.time(),
            total=total,
            scanned=upserted,
        )
        logger.info("scan %s done: %d found, %d upserted in %.1fs",
                    job_id, total, upserted, time.time() - started)
    except Exception as exc:
        _update_job(
            job_id,
            status="error",
            finished_at=time.time(),
            error=str(exc),
        )
        logger.exception("scan %s failed: %s", job_id, exc)


async def run_write_job(job_id: str, plan, summary: str) -> None:
    """
    Run a tag-writing job: `plan(conn)` returns [(track row, updates)], each is
    written to file + DB, and the whole run is logged as one undoable change
    titled "<summary> — N tracks".
    """
    kind = (get_job(job_id) or {}).get("kind", "job")
    _update_job(job_id, status="running")
    logger.info("%s %s started: %s", kind, job_id, summary)
    try:
        changed, failed = await asyncio.to_thread(_apply_plans, job_id, plan, summary)
        _update_job(job_id, status="done", finished_at=time.time(), scanned=changed,
                    error=f"{failed} tracks could not be written" if failed else None)
        logger.info("%s %s done: %d tracks changed, %d failed", kind, job_id, changed, failed)
    except Exception as exc:
        _update_job(job_id, status="error", finished_at=time.time(), error=str(exc))
        logger.exception("%s %s failed: %s", kind, job_id, exc)


async def run_unify_job(job_id: str, directories: list[str], fields: list[str]) -> None:
    """Rewrite album-level tags so every track of each album agrees."""
    from core.unify import album_groups, propose

    def plan(conn):
        plans = []
        for rows in album_groups(conn, directories).values():
            changes = propose([dict(r) for r in rows], fields)
            for row in rows:
                updates = {f: c["after"] for f, c in changes.items() if (row[f] or "") != c["after"]}
                if updates:
                    plans.append((row, updates))
        return plans

    await run_write_job(job_id, plan, "Unified album tags")


async def run_retag_artist_job(job_id: str, artist: str, genres: list[str], mode: str,
                               by: str = "album_artist") -> None:
    """Set (replace), merge in (add) or take out (remove) genres on every track of an artist."""
    from core.artist_genres import GROUPINGS
    from core.genres import edit_genres, join_genres

    def plan(conn):
        rows = conn.execute(f"SELECT * FROM tracks WHERE {GROUPINGS[by]} = ?", (artist,)).fetchall()
        plans = []
        for row in rows:
            if mode == "replace":
                new = join_genres(genres)
            elif mode == "add":
                new = edit_genres(row["genre"], add=genres)
            else:
                new = edit_genres(row["genre"], remove=genres)
            if new != (row["genre"] or ""):
                plans.append((row, {"genre": new}))
        return plans

    verb = {"replace": "Set", "add": "Added", "remove": "Removed"}[mode]
    await run_write_job(job_id, plan, f"{verb} genres of {artist}")


async def run_fetch_genres_job(job_id: str, refresh: bool = False, by: str = "album_artist") -> None:
    """Fetch genres for every artist not cached yet or that failed (or all, with refresh)."""
    from core.artist_genres import GROUPINGS, fetch_artist
    from core.database import get_conn

    def work() -> int:
        conn = get_conn()
        try:
            # Rows that failed (every source errored) are retried, not skipped.
            known = {r[0] for r in conn.execute("SELECT artist FROM artist_genres WHERE error IS NULL")}
            key = GROUPINGS[by]
            artists = [r[0] for r in conn.execute(
                f"SELECT DISTINCT {key} FROM tracks WHERE {key} != '' ORDER BY 1 COLLATE NOCASE")]
            todo = [a for a in artists if refresh or a not in known]
            _update_job(job_id, total=len(todo), scanned=0)
            for i, artist in enumerate(todo, start=1):
                fetch_artist(conn, artist, by=by)
                conn.commit()
                _update_job(job_id, scanned=i)
            return len(todo)
        finally:
            conn.close()

    _update_job(job_id, status="running")
    try:
        done = await asyncio.to_thread(work)
        _update_job(job_id, status="done", finished_at=time.time(), scanned=done)
    except Exception as exc:
        _update_job(job_id, status="error", finished_at=time.time(), error=str(exc))
        logger.exception("fetch %s failed: %s", job_id, exc)


def _apply_plans(job_id: str, plan, summary: str) -> tuple[int, int]:
    from api.tags import _write_track  # local import avoids a router import cycle
    from core.database import get_conn
    from core.history import log_change

    conn = get_conn()
    snaps: list[dict] = []
    failed = 0
    try:
        plans = plan(conn)
        conn.commit()
        _update_job(job_id, total=len(plans), scanned=0)

        # Commit about once a second: frequent enough that tag edits made in the
        # UI meanwhile aren't stuck behind this job's write lock, and progress
        # (written on another connection) can only be recorded between commits.
        last = time.time()
        for i, (row, updates) in enumerate(plans, start=1):
            try:
                snaps.append(_write_track(conn, row, updates))
            except Exception:
                failed += 1
            if time.time() - last >= 1 or i == len(plans):
                conn.commit()
                _update_job(job_id, scanned=i)
                last = time.time()
    finally:
        # Log whatever was written, even if the job died part-way.
        log_change(conn, "tag_edit", f"{summary} — {len(snaps)} tracks", snaps)
        conn.commit()
        conn.close()
    return len(snaps), failed


async def run_undo_job(job_id: str, change_id: int, size: int) -> None:
    """Undo a large change in the background (it rewrites every file it touched)."""
    from core.history import undo_change

    def _undo() -> dict:
        with db() as conn:
            return undo_change(conn, change_id)

    _update_job(job_id, status="running", total=size)
    try:
        res = await asyncio.to_thread(_undo)
        _update_job(job_id, status="done", finished_at=time.time(), scanned=res["restored"])
    except Exception as exc:
        _update_job(job_id, status="error", finished_at=time.time(), error=str(exc))
        logger.exception("undo %s failed: %s", job_id, exc)
