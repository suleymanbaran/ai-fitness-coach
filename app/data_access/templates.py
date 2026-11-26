"""Reusable meal and workout templates: the user's recurring breakfast, their Push Day blueprint.

Templates are JSONB documents keyed by (user, name); logging from a meal template scales the
items and writes a normal food_logs row, so day totals and the coach's context work unchanged.
"""

from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import MealTemplate, WorkoutTemplate


class MealTemplateItem(BaseModel):
    description: str = Field(..., min_length=1, max_length=200)
    servings: float = Field(1.0, gt=0, le=50)
    kcal: int = Field(..., ge=0, le=20000)
    protein_g: float | None = Field(None, ge=0, le=1000)
    carbs_g: float | None = Field(None, ge=0, le=1000)
    fat_g: float | None = Field(None, ge=0, le=1000)
    fiber_g: float | None = Field(None, ge=0, le=300)


class MealTemplateOut(BaseModel):
    id: int
    name: str
    meal: str
    items: list[MealTemplateItem]
    total_kcal: int
    updated_at: datetime


class MealTemplateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120, examples=["Oat breakfast"])
    meal: str = Field("breakfast", pattern="^(breakfast|lunch|dinner|snack)$")
    items: list[MealTemplateItem] = Field(..., min_length=1)


class WorkoutTemplateExercise(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    sets: int = Field(3, ge=1, le=20)
    reps: str = Field("8", max_length=20, description="rep target or range, e.g. '8' or '6-8'")
    weight_kg: float | None = Field(None, ge=0, le=1000)
    rpe_target: str = Field("", max_length=10)


class WorkoutTemplateOut(BaseModel):
    id: int
    name: str
    exercises: list[WorkoutTemplateExercise]
    updated_at: datetime


class WorkoutTemplateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120, examples=["Push Day"])
    exercises: list[WorkoutTemplateExercise] = Field(..., min_length=1)


DEFAULT_WORKOUT_TEMPLATES: dict[str, list[dict]] = {
    "Push Day": [
        {"name": "Bench press", "sets": 4, "reps": "6-8", "weight_kg": None, "rpe_target": "7-8"},
        {"name": "Incline dumbbell press", "sets": 3, "reps": "8-10", "weight_kg": None, "rpe_target": "7-8"},
        {"name": "Overhead press", "sets": 3, "reps": "8-10", "weight_kg": None, "rpe_target": "7"},
        {"name": "Cable fly", "sets": 3, "reps": "12-15", "weight_kg": None, "rpe_target": "8"},
        {"name": "Triceps pushdown", "sets": 3, "reps": "10-12", "weight_kg": None, "rpe_target": "8"},
        {"name": "Lateral raise", "sets": 3, "reps": "12-15", "weight_kg": None, "rpe_target": "8"},
    ],
    "Pull Day": [
        {"name": "Lat pulldown", "sets": 4, "reps": "8-10", "weight_kg": None, "rpe_target": "7-8"},
        {"name": "Chest supported row", "sets": 3, "reps": "8-10", "weight_kg": None, "rpe_target": "7-8"},
        {"name": "Seated cable row", "sets": 3, "reps": "10-12", "weight_kg": None, "rpe_target": "8"},
        {"name": "Face pull", "sets": 3, "reps": "15", "weight_kg": None, "rpe_target": "8"},
        {"name": "Barbell curl", "sets": 3, "reps": "10-12", "weight_kg": None, "rpe_target": "8"},
        {"name": "Hammer curl", "sets": 3, "reps": "12", "weight_kg": None, "rpe_target": "8"},
    ],
    "Leg Day": [
        {"name": "Leg press", "sets": 4, "reps": "8-10", "weight_kg": None, "rpe_target": "7-8"},
        {"name": "Leg curl", "sets": 3, "reps": "10-12", "weight_kg": None, "rpe_target": "8"},
        {"name": "Leg extension", "sets": 3, "reps": "12-15", "weight_kg": None, "rpe_target": "8"},
        {"name": "Hip thrust", "sets": 3, "reps": "10-12", "weight_kg": None, "rpe_target": "8"},
        {"name": "Calf raise", "sets": 4, "reps": "12-15", "weight_kg": None, "rpe_target": "8"},
    ],
}


def meal_template_total(items: list[dict], factor: float = 1) -> int:
    return round(sum(float(i.get("kcal", 0)) * float(i.get("servings", 1)) for i in items) * factor)


def meal_template_nutrient(items: list[dict], key: str, factor: float) -> float | None:
    if not items or any(item.get(key) is None for item in items):
        return None
    return round(sum(float(item[key]) * float(item.get("servings", 1)) for item in items) * factor, 1)


def _meal_out(row: MealTemplate) -> MealTemplateOut:
    items = [MealTemplateItem.model_validate(i) for i in row.items]
    return MealTemplateOut(
        id=row.id, name=row.name, meal=row.meal, items=items,
        total_kcal=meal_template_total(row.items), updated_at=row.updated_at,
    )


def _workout_out(row: WorkoutTemplate) -> WorkoutTemplateOut:
    return WorkoutTemplateOut(
        id=row.id, name=row.name,
        exercises=[WorkoutTemplateExercise.model_validate(e) for e in row.exercises],
        updated_at=row.updated_at,
    )


def list_meal_templates(db: Session, user_id: str) -> list[MealTemplateOut]:
    rows = db.scalars(select(MealTemplate).where(MealTemplate.user_id == user_id).order_by(MealTemplate.name))
    return [_meal_out(r) for r in rows]


def find_meal_template(db: Session, user_id: str, name: str) -> MealTemplate | None:
    return db.scalar(
        select(MealTemplate).where(
            MealTemplate.user_id == user_id, func.lower(MealTemplate.name) == name.strip().lower()
        )
    )


def upsert_meal_template(db: Session, user_id: str, name: str, meal: str, items: list[dict]) -> MealTemplateOut:
    name = name.strip()[:120]
    stmt = (
        insert(MealTemplate)
        .values(user_id=user_id, name=name, meal=meal, items=items)
        .on_conflict_do_update(
            constraint="uq_meal_template_user_name",
            set_={"meal": meal, "items": items, "updated_at": func.now()},
        )
    )
    db.execute(stmt)
    db.flush()
    return _meal_out(find_meal_template(db, user_id, name))


def delete_meal_template(db: Session, user_id: str, name: str) -> bool:
    result = db.execute(
        delete(MealTemplate).where(
            MealTemplate.user_id == user_id, func.lower(MealTemplate.name) == name.strip().lower()
        )
    )
    return result.rowcount > 0


def list_workout_templates(db: Session, user_id: str) -> list[WorkoutTemplateOut]:
    rows = db.scalars(select(WorkoutTemplate).where(WorkoutTemplate.user_id == user_id).order_by(WorkoutTemplate.name))
    return [_workout_out(r) for r in rows]


def find_workout_template(db: Session, user_id: str, name: str) -> WorkoutTemplate | None:
    return db.scalar(
        select(WorkoutTemplate).where(
            WorkoutTemplate.user_id == user_id, func.lower(WorkoutTemplate.name) == name.strip().lower()
        )
    )


def upsert_workout_template(db: Session, user_id: str, name: str, exercises: list[dict]) -> WorkoutTemplateOut:
    name = name.strip()[:120]
    stmt = (
        insert(WorkoutTemplate)
        .values(user_id=user_id, name=name, exercises=exercises)
        .on_conflict_do_update(
            constraint="uq_workout_template_user_name",
            set_={"exercises": exercises, "updated_at": func.now()},
        )
    )
    db.execute(stmt)
    db.flush()
    return _workout_out(find_workout_template(db, user_id, name))


def delete_workout_template(db: Session, user_id: str, name: str) -> bool:
    result = db.execute(
        delete(WorkoutTemplate).where(
            WorkoutTemplate.user_id == user_id, func.lower(WorkoutTemplate.name) == name.strip().lower()
        )
    )
    return result.rowcount > 0


def seed_workout_templates(db: Session, user_id: str) -> int:
    """Create the starter Push/Pull/Legs templates; existing names are kept. Returns created count."""
    created = 0
    for name, exercises in DEFAULT_WORKOUT_TEMPLATES.items():
        if find_workout_template(db, user_id, name) is None:
            db.add(WorkoutTemplate(user_id=user_id, name=name, exercises=exercises))
            created += 1
    return created
