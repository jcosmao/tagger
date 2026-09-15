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


def active_job() -> dict | None:
    """Return the currently running/pending job of any kind, if any."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM scan_jobs WHERE status IN ('pending', 'running') "
            "ORDER BY started_at DESC LIMIT 1"
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


async def run_unify_job(job_id: str, directories: list[str], fields: list[str]) -> None:
    """Rewrite album-level tags so every track of each album agrees."""
    _update_job(job_id, status="running")
    logger.info("unify %s started (%d albums, fields=%s)", job_id, len(directories), fields)
    try:
        changed, failed = await asyncio.to_thread(_unify, job_id, directories, fields)
        _update_job(job_id, status="done", finished_at=time.time(), scanned=changed,
                    error=f"{failed} tracks could not be written" if failed else None)
        logger.info("unify %s done: %d tracks changed, %d failed", job_id, changed, failed)
    except Exception as exc:
        _update_job(job_id, status="error", finished_at=time.time(), error=str(exc))
        logger.exception("unify %s failed: %s", job_id, exc)


def _unify(job_id: str, directories: list[str], fields: list[str]) -> tuple[int, int]:
    from api.tags import _write_track  # local import avoids a router import cycle
    from core.database import get_conn
    from core.history import log_change
    from core.unify import album_groups, propose

    conn = get_conn()
    snaps: list[dict] = []
    failed = 0
    try:
        plans = []
        for rows in album_groups(conn, directories).values():
            changes = propose([dict(r) for r in rows], fields)
            for row in rows:
                updates = {f: c["after"] for f, c in changes.items() if (row[f] or "") != c["after"]}
                if updates:
                    plans.append((row, updates))
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
        log_change(conn, "tag_edit", f"Unified album tags — {len(snaps)} tracks", snaps)
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
