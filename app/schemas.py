from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.data_access.protocols import Category

Goal = Literal["strength", "muscle_gain", "fat_loss", "endurance", "general_fitness"]
MemoryKind = Literal["preference", "fact", "constraint"]
Meal = Literal["breakfast", "lunch", "dinner", "snack"]
USER_ID_PATTERN = r"^[a-z0-9_-]{1,64}$"
MEMORY_KEY_PATTERN = r"^[a-z0-9_]{1,80}$"


class AskRequest(BaseModel):
    user_id: str = Field(..., examples=["u1"])
    question: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        examples=["My HRV dropped and my CNS feels fried. How should I adjust today's heavy leg day?"],
    )
    category: Category | None = Field(None, description="Restrict retrieval to one protocol category")
    channel: Literal["web", "telegram"] = Field("web", description="Where the question was asked; stored with the turn")


class UserSummary(BaseModel):
    user_id: str
    name: str
    goal: str


class ProfileIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120, examples=["Suleyman"])
    age: int | None = Field(None, ge=10, le=110)
    weight_kg: float | None = Field(None, gt=20, lt=400)
    goal: Goal = "general_fitness"


class DayMetricsIn(BaseModel):
    """Only the metrics you send are written; omitted ones keep their stored value."""

    hrv_ms: float | None = Field(None, gt=0, lt=400)
    sleep_score: int | None = Field(None, ge=0, le=100)
    sleep_minutes: int | None = Field(None, ge=0, le=1440)
    resting_hr_bpm: int | None = Field(None, ge=20, le=250)
    calories_kcal: int | None = Field(None, ge=0, le=20000)
    steps: int | None = Field(None, ge=0, le=200000, examples=[8432])
    active_kcal: int | None = Field(None, ge=0, le=20000, description="Energy burned by activity", examples=[620])

    @model_validator(mode="after")
    def at_least_one_metric(self) -> "DayMetricsIn":
        if all(v is None for v in self.model_dump().values()):
            raise ValueError("Send at least one metric")
        return self


class MemoryIn(BaseModel):
    """A user-stated memory. Written with source="user", so assistant extraction can't overwrite it."""

    kind: MemoryKind = Field(..., examples=["preference"])
    key: str = Field(..., pattern=MEMORY_KEY_PATTERN, examples=["disliked_exercises"])
    value: str = Field(..., min_length=1, max_length=500, examples=["No barbell squats — bad knee"])
    pinned: bool = False


class MealIn(BaseModel):
    """A manually logged meal. kcal is required — the FatSecret diary total is recomputed from these."""

    model_config = ConfigDict(populate_by_name=True)

    # Attribute is `day` because a field literally named `date` shadows the date type in
    # pydantic's annotation resolution; the JSON field stays "date" via the alias.
    day: date | None = Field(None, alias="date", description="Defaults to today", examples=["2026-11-12"])
    meal: Meal = Field(..., examples=["breakfast"])
    description: str = Field(..., min_length=1, max_length=200, examples=["2 eggs + 1 simit"])
    kcal: int = Field(..., ge=0, le=20000, examples=[550])
    fatsecret_food_id: str | None = Field(None, max_length=32)
    servings: float = Field(1.0, gt=0, le=50)
    protein_g: float | None = Field(None, ge=0, le=1000)
    carbs_g: float | None = Field(None, ge=0, le=1000)
    fat_g: float | None = Field(None, ge=0, le=1000)
    fiber_g: float | None = Field(None, ge=0, le=300)


class MeasurementIn(BaseModel):
    """Only the measures you send are written; omitted ones keep their stored value."""

    weight_kg: float | None = Field(None, gt=20, lt=400, examples=[78.5])
    body_fat_pct: float | None = Field(None, ge=2, le=70)
    waist_cm: float | None = Field(None, ge=30, le=300)
    chest_cm: float | None = Field(None, ge=30, le=300)
    arm_cm: float | None = Field(None, ge=10, le=100)
    thigh_cm: float | None = Field(None, ge=20, le=200)
    notes: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def at_least_one_field(self) -> "MeasurementIn":
        fields = self.model_dump(exclude={"notes"})
        if all(v is None for v in fields.values()) and not self.notes:
            raise ValueError("Send at least one measurement or a note")
        return self


class LogFromTemplateIn(BaseModel):
    """Log a saved meal template; factor scales every item (0.5 = half portion, 1.5 = big hunger)."""

    model_config = ConfigDict(populate_by_name=True)

    day: date | None = Field(None, alias="date", description="Defaults to today")
    factor: float = Field(1.0, ge=0.25, le=10)


class IntegrationStatus(BaseModel):
    provider: str
    configured: bool
    connected: bool
    last_synced_at: datetime | None = None
    last_error: str | None = None


class ConnectResponse(BaseModel):
    authorization_url: str


class SyncResultOut(BaseModel):
    provider: str
    days_updated: int
    error: str | None = None
    reconnect: bool = False


class Source(BaseModel):
    protocol_id: str
    title: str
    category: str
    score: float


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]
    cached_tokens: int = 0
