"""Pydantic models for LLM-generated programs and logged workouts.

The plan models are deliberately strict-structured-output friendly: every field is required,
no Optional/default values, so model_json_schema() converts cleanly to OpenAI strict mode
(all properties required, additionalProperties false).
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProgramKind = Literal["training", "nutrition"]


class Exercise(BaseModel):
    name: str
    sets: int = Field(..., ge=1, le=12)
    reps: str = Field(..., description="rep target or range, e.g. '5' or '8-12'")
    weight_guidance: str = Field(..., description="how to pick the load, e.g. 'RPE 7, ~70% 1RM'")
    rpe_target: str = Field("", description="target RPE or range; empty string when not applicable")


class SessionDay(BaseModel):
    name: str
    focus: str
    exercises: list[Exercise]


class PlanWeek(BaseModel):
    focus: str
    days: list[SessionDay]


class TrainingPlan(BaseModel):
    weeks: list[PlanWeek] = Field(..., min_length=1, max_length=12)


class MealTemplate(BaseModel):
    name: str
    items: str
    kcal: int = Field(..., ge=0, le=5000)


class NutritionPlan(BaseModel):
    target_kcal: int = Field(..., ge=800, le=8000)
    protein_g: int = Field(..., ge=0, le=600)
    carbs_g: int = Field(..., ge=0, le=1200)
    fat_g: int = Field(..., ge=0, le=600)
    meals: list[MealTemplate]
    guidelines: list[str]


class TrainingPlanOutput(BaseModel):
    name: str = Field(..., max_length=120)
    summary: str = Field(..., max_length=500, description="standalone recap used in the coach's daily context")
    rationale: str
    plan: TrainingPlan


class NutritionPlanOutput(BaseModel):
    name: str = Field(..., max_length=120)
    summary: str = Field(..., max_length=500)
    rationale: str
    plan: NutritionPlan


class GenerateProgramIn(BaseModel):
    kind: ProgramKind
    brief: str | None = Field(None, max_length=500, examples=["4 days/week, home gym, focus on squat strength"])


class ProgramOut(BaseModel):
    id: int
    kind: str
    status: str
    version: int
    name: str
    summary: str
    rationale: str | None
    payload: dict
    created_at: datetime
    activated_at: datetime | None


class LoggedSet(BaseModel):
    reps: int | None = Field(None, ge=0, le=200)
    weight_kg: float | None = Field(None, ge=0, le=1000)
    rpe: float | None = Field(None, ge=1, le=10)


class LoggedExercise(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    sets: list[LoggedSet] = Field(..., min_length=1)


class WorkoutIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # `day` (JSON: "date") avoids shadowing the date type in annotation resolution.
    day: date | None = Field(None, alias="date", description="Defaults to today")
    session_name: str = Field(..., min_length=1, max_length=120, examples=["Push day"])
    exercises: list[LoggedExercise] = Field(..., min_length=1)
    duration_min: int | None = Field(None, ge=0, le=1440)
    notes: str | None = Field(None, max_length=2000)


class WorkoutOut(BaseModel):
    id: int
    date: date
    program_id: int | None
    session_name: str
    exercises: list[LoggedExercise]
    duration_min: int | None
    notes: str | None
    created_at: datetime


class ReviewOut(BaseModel):
    program_id: int
    assessment: str
    regenerated_version: int | None = None


class TodaySessionOut(BaseModel):
    program_id: int | None
    text: str


class ExerciseSetOut(BaseModel):
    reps: int | None = None
    weight_kg: float | None = None
    rpe: float | None = None


class ExerciseHistoryRow(BaseModel):
    """One exercise's performance in one logged session, with progression aggregates."""

    date: date
    session_name: str
    exercise: str
    sets: list[ExerciseSetOut]
    best_weight_kg: float | None = None
    total_reps: int = 0
    volume_kg: float = 0.0
