"""CRUD for run logs (cardiovascular capacity tracking)."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RunLog


def add_run(
    db: Session,
    user_id: str,
    day: date,
    *,
    distance_km: float,
    duration_min: float,
    avg_hr_bpm: int | None = None,
    notes: str | None = None,
    source: str = "manual",
) -> RunLog:
    row = RunLog(
        user_id=user_id,
        date=day,
        distance_km=distance_km,
        duration_min=duration_min,
        avg_hr_bpm=avg_hr_bpm,
        notes=notes,
        source=source,
    )
    db.add(row)
    db.flush()
    return row


def sync_run(
    db: Session,
    user_id: str,
    *,
    day: date,
    distance_km: float,
    duration_min: float,
    avg_hr_bpm: int | None,
    external_id: str | None,
    source: str = "google",
) -> bool:
    """Idempotent insert for provider-synced runs. Returns True when a new row was created."""
    if external_id is not None:
        exists = db.scalar(
            select(RunLog.id).where(RunLog.user_id == user_id, RunLog.external_id == external_id)
        )
        if exists is not None:
            return False
    db.add(RunLog(
        user_id=user_id, date=day, distance_km=distance_km, duration_min=duration_min,
        avg_hr_bpm=avg_hr_bpm, source=source, external_id=external_id,
    ))
    db.flush()
    return True


def list_runs(db: Session, user_id: str, days: int = 90) -> list[RunLog]:
    """Runs within the lookback window, oldest first."""
    since = date.today() - timedelta(days=days - 1)
    rows = db.scalars(
        select(RunLog)
        .where(RunLog.user_id == user_id, RunLog.date >= since)
        .order_by(RunLog.date, RunLog.id)
    ).all()
    return list(rows)


def delete_run(db: Session, user_id: str, run_id: int) -> bool:
    row = db.get(RunLog, run_id)
    if row is None or row.user_id != user_id:
        return False
    db.delete(row)
    return True
