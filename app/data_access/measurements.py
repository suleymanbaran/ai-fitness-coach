"""Dated body measurements with idempotent partial upserts (same contract as biometrics).

A new weight on the latest measurement date also refreshes users.weight_kg, so the profile
the coach sees never lags behind the scale.
"""

from datetime import date

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import BodyMeasurement, User

MEASUREMENT_FIELDS = ("weight_kg", "body_fat_pct", "waist_cm", "chest_cm", "arm_cm", "thigh_cm")


class MeasurementOut(BaseModel):
    date: date
    weight_kg: float | None = None
    body_fat_pct: float | None = None
    waist_cm: float | None = None
    chest_cm: float | None = None
    arm_cm: float | None = None
    thigh_cm: float | None = None
    source: str
    notes: str | None = None


def upsert_measurement(
    db: Session, user_id: str, day: date, source: str, notes: str | None = None, **measurements
) -> None:
    values = {k: v for k, v in measurements.items() if k in MEASUREMENT_FIELDS and v is not None}
    if notes is not None:
        values["notes"] = notes
    if not values:
        return
    stmt = (
        insert(BodyMeasurement)
        .values(user_id=user_id, date=day, source=source, **values)
        .on_conflict_do_update(
            constraint="uq_measurements_user_date",
            set_={**values, "source": source, "updated_at": func.now()},
        )
    )
    db.execute(stmt)
    db.flush()
    if values.get("weight_kg") is not None:
        newest = db.scalar(select(func.max(BodyMeasurement.date)).where(BodyMeasurement.user_id == user_id))
        if newest is not None and day >= newest:
            user = db.get(User, user_id)
            if user is not None:
                user.weight_kg = values["weight_kg"]


def list_measurements(db: Session, user_id: str, start: date | None = None, end: date | None = None) -> list[MeasurementOut]:
    stmt = select(BodyMeasurement).where(BodyMeasurement.user_id == user_id)
    if start:
        stmt = stmt.where(BodyMeasurement.date >= start)
    if end:
        stmt = stmt.where(BodyMeasurement.date <= end)
    rows = db.scalars(stmt.order_by(BodyMeasurement.date))
    return [
        MeasurementOut(
            date=r.date, weight_kg=r.weight_kg, body_fat_pct=r.body_fat_pct,
            waist_cm=r.waist_cm, chest_cm=r.chest_cm, arm_cm=r.arm_cm, thigh_cm=r.thigh_cm,
            source=r.source, notes=r.notes,
        )
        for r in rows
    ]
