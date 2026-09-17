"""Album metadata: fetch original release year and label per album, review, then write them."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from core.album_years import FIELDS, proposals
from core.database import db
from core.tasks import active_job, create_job, run_album_years_job, run_apply_album_years_job

router = APIRouter()


class FetchYears(BaseModel):
    directories: Optional[list[str]] = None  # None = every album
    refresh: bool = False


class ApplyYears(BaseModel):
    directories: list[str]
    fields: list[str] = list(FIELDS)   # year and label are applied independently


@router.get("/years")
def album_years():
    """Every tagged album with its current years and labels, and the fetched ones."""
    with db() as conn:
        return proposals(conn)


@router.post("/years/fetch")
async def fetch_years(req: FetchYears, background_tasks: BackgroundTasks):
    running = active_job("years")
    if running:
        raise HTTPException(409, {"detail": "Album years are already being fetched", "job_id": running["id"]})
    job_id = create_job("years")
    background_tasks.add_task(run_album_years_job, job_id, req.directories, req.refresh)
    return {"job_id": job_id}


@router.post("/years/apply")
async def apply_years(req: ApplyYears, background_tasks: BackgroundTasks):
    if not req.directories:
        raise HTTPException(400, "No albums selected")
    fields = tuple(f for f in req.fields if f in FIELDS)
    if not fields:
        raise HTTPException(400, f"fields must be among {list(FIELDS)}")
    running = active_job()
    if running:
        raise HTTPException(409, {"detail": f"A {running['kind']} job is already running", "job_id": running["id"]})
    job_id = create_job("retag")
    background_tasks.add_task(run_apply_album_years_job, job_id, req.directories, fields)
    return {"job_id": job_id}
