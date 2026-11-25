"""Itemized meal logs and per-day intake totals."""

from datetime import date, timedelta

from pydantic import BaseModel
from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from app.data_access.biometrics import upsert_daily_metrics
from app.db.models import DailyBiometrics, FoodLog


class FoodLogOut(BaseModel):
    id: int
    date: date
    meal: str
    description: str
    fatsecret_food_id: str | None
    fatsecret_entry_id: str | None = None
    servings: float
    kcal: int
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None
    fiber_g: float | None = None
    source: str


class DayIntake(BaseModel):
    date: date
    kcal: int
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fiber_g: float | None = None
    entries: int = 0


def add_food_log(
    db: Session,
    user_id: str,
    day: date,
    meal: str,
    description: str,
    kcal: int,
    source: str = "manual",
    fatsecret_food_id: str | None = None,
    fatsecret_entry_id: str | None = None,
    servings: float = 1.0,
    protein_g: float | None = None,
    carbs_g: float | None = None,
    fat_g: float | None = None,
    fiber_g: float | None = None,
) -> FoodLog:
    row = FoodLog(
        user_id=user_id, date=day, meal=meal, description=description[:200], kcal=round(kcal),
        source=source, fatsecret_food_id=fatsecret_food_id, fatsecret_entry_id=fatsecret_entry_id,
        servings=servings, protein_g=protein_g, carbs_g=carbs_g, fat_g=fat_g, fiber_g=fiber_g,
    )
    db.add(row)
    return row


def get_food_log(db: Session, user_id: str, log_id: int) -> FoodLog | None:
    row = db.get(FoodLog, log_id)
    return row if row is not None and row.user_id == user_id else None


def list_food_logs(db: Session, user_id: str, start: date, end: date) -> list[FoodLogOut]:
    rows = db.scalars(
        select(FoodLog)
        .where(FoodLog.user_id == user_id, FoodLog.date >= start, FoodLog.date <= end)
        .order_by(FoodLog.date, FoodLog.id)
    )
    return [FoodLogOut.model_validate(r, from_attributes=True) for r in rows]


def delete_food_log(db: Session, user_id: str, log_id: int) -> date | None:
    """Returns the deleted row's date (so the caller can recompute that day), or None."""
    row = db.get(FoodLog, log_id)
    if row is None or row.user_id != user_id:
        return None
    day = row.date
    db.delete(row)
    return day


def _complete_sum(column):
    return case((func.count(column) == func.count(), func.sum(column)), else_=None)


def day_intake(db: Session, user_id: str, day: date) -> DayIntake:
    totals = db.execute(
        select(
            func.coalesce(func.sum(FoodLog.kcal), 0),
            _complete_sum(FoodLog.protein_g),
            _complete_sum(FoodLog.carbs_g),
            _complete_sum(FoodLog.fat_g),
            _complete_sum(FoodLog.fiber_g),
            func.count(),
        ).where(FoodLog.user_id == user_id, FoodLog.date == day)
    ).one()
    kcal, protein, carbs, fat, fiber, entries = totals
    return DayIntake(
        date=day, kcal=int(kcal),
        protein_g=round(protein, 1) if protein is not None else None,
        carbs_g=round(carbs, 1) if carbs is not None else None,
        fat_g=round(fat, 1) if fat is not None else None,
        fiber_g=round(fiber, 1) if fiber is not None else None,
        entries=entries,
    )


def sync_day_calories(db: Session, user_id: str, day: date) -> int:
    """Recompute the day's calories_kcal in daily_biometrics from the itemized logs.
    Keeps the coach-logged meals and the single daily number consistent. When the last log
    is deleted, only an assistant-owned total is cleared; a FatSecret-synced number stays."""
    intake = day_intake(db, user_id, day)
    if intake.entries:
        upsert_daily_metrics(db, user_id, day, source="assistant", calories_kcal=intake.kcal)
    else:
        db.execute(
            update(DailyBiometrics)
            .where(
                DailyBiometrics.user_id == user_id,
                DailyBiometrics.date == day,
                DailyBiometrics.source == "assistant",
            )
            .values(calories_kcal=None)
        )
    return intake.kcal


def intake_window(db: Session, user_id: str, days: int = 14) -> list[DayIntake]:
    """Per-day totals for the last `days` days; days without entries are omitted."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    rows = db.execute(
        select(
            FoodLog.date,
            func.sum(FoodLog.kcal),
            _complete_sum(FoodLog.protein_g),
            _complete_sum(FoodLog.carbs_g),
            _complete_sum(FoodLog.fat_g),
            _complete_sum(FoodLog.fiber_g),
            func.count(),
        )
        .where(FoodLog.user_id == user_id, FoodLog.date >= start, FoodLog.date <= end)
        .group_by(FoodLog.date)
        .order_by(FoodLog.date)
    ).all()
    return [
        DayIntake(
            date=day, kcal=int(kcal),
            protein_g=round(protein, 1) if protein is not None else None,
            carbs_g=round(carbs, 1) if carbs is not None else None,
            fat_g=round(fat, 1) if fat is not None else None,
            fiber_g=round(fiber, 1) if fiber is not None else None,
            entries=entries,
        )
        for day, kcal, protein, carbs, fat, fiber, entries in rows
    ]
