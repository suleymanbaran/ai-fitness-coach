from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    age: Mapped[int | None] = mapped_column(Integer)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    goal: Mapped[str] = mapped_column(String(40), default="general_fitness")
    # SHA-256 of the user's API token; the token itself is never stored.
    api_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    biometrics: Mapped[list["DailyBiometrics"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    integrations: Mapped[list["IntegrationAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    memories: Mapped[list["UserMemory"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    llm_calls: Mapped[list["LLMCall"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class DailyBiometrics(Base):
    __tablename__ = "daily_biometrics"
    # One row per user per day: makes every sync an idempotent upsert on this key.
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_biometrics_user_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    hrv_ms: Mapped[float | None] = mapped_column(Float)
    sleep_score: Mapped[int | None] = mapped_column(Integer)
    sleep_minutes: Mapped[int | None] = mapped_column(Integer)
    resting_hr_bpm: Mapped[int | None] = mapped_column(Integer)
    calories_kcal: Mapped[int | None] = mapped_column(Integer)  # intake (food)
    steps: Mapped[int | None] = mapped_column(Integer)
    active_kcal: Mapped[int | None] = mapped_column(Integer)  # energy burned by activity
    # Stage minutes of the main sleep, e.g. {"deep": 70, "rem": 77, "light": 152, "awake": 6}.
    sleep_stages: Mapped[dict | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(20), default="seed")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="biometrics")


class IntegrationAccount(Base):
    """A user's connection to an external data provider. Tokens are Fernet-encrypted at rest."""

    __tablename__ = "integration_accounts"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_integration_user_provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    # OAuth 1.0 (FatSecret) signs requests with a per-user token secret instead of using refresh tokens.
    token_secret_enc: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="integrations")


class UserMemory(Base):
    """A durable fact or preference about the user, injected into the model's stable prompt prefix.

    Rows with source="user" were stated or edited by the user directly; assistant extraction
    may never overwrite them. `pinned` rows survive eviction when the prefix budget is exceeded.
    """

    __tablename__ = "user_memories"
    __table_args__ = (UniqueConstraint("user_id", "kind", "key", name="uq_memory_user_kind_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # preference | fact | constraint
    key: Mapped[str] = mapped_column(String(80))  # slug, e.g. "disliked_exercises"
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(12), default="assistant")  # user | assistant
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="memories")


class ChatMessage(Base):
    """One turn of coach conversation, persisted server-side and shared across channels
    so web and Telegram see the same history."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(12))  # web | telegram
    role: Mapped[str] = mapped_column(String(12))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="chat_messages")


class FoodLog(Base):
    """One itemized meal entry. The day's calories_kcal in daily_biometrics is the sum of these
    when logged through the coach, or the FatSecret diary total when synced."""

    __tablename__ = "food_logs"
    __table_args__ = (Index("ix_food_logs_user_date", "user_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    meal: Mapped[str] = mapped_column(String(20))  # breakfast | lunch | dinner | snack
    description: Mapped[str] = mapped_column(String(200))
    fatsecret_food_id: Mapped[str | None] = mapped_column(String(32))
    # Diary entry id returned by food_entry.create; enables deleting the meal from the real diary.
    fatsecret_entry_id: Mapped[str | None] = mapped_column(String(32))
    servings: Mapped[float] = mapped_column(Float, default=1.0)
    kcal: Mapped[int] = mapped_column(Integer)
    protein_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    fiber_g: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(12), default="manual")  # assistant | manual | template
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BodyMeasurement(Base):
    """Dated body measurements (weight, body fat, tape measures). One row per user per day;
    a Fitbit scale sync or a manual log upserts only the fields it provides."""

    __tablename__ = "body_measurements"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_measurements_user_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    body_fat_pct: Mapped[float | None] = mapped_column(Float)
    waist_cm: Mapped[float | None] = mapped_column(Float)
    chest_cm: Mapped[float | None] = mapped_column(Float)
    arm_cm: Mapped[float | None] = mapped_column(Float)
    thigh_cm: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MealTemplate(Base):
    """A recurring meal the user eats often (e.g. the same breakfast every morning).
    `items` is a JSONB list: [{description, servings, kcal, protein_g, carbs_g, fat_g}].
    Logging from a template scales the items and writes one food_logs row."""

    __tablename__ = "meal_templates"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_meal_template_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    meal: Mapped[str] = mapped_column(String(20), default="breakfast")
    items: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkoutTemplate(Base):
    """A reusable session blueprint (e.g. Push Day): default exercises/sets/reps the user can
    prefill a workout log from and adjust. `exercises`: [{name, sets, reps, weight_kg, rpe_target}]."""

    __tablename__ = "workout_templates"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_workout_template_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    exercises: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SessionAdjustment(Base):
    """A coach-made, date-scoped change to the planned session (e.g. easy cardio after a big
    surplus day). today_session merges it over the program's day slot; one row per user per date."""

    __tablename__ = "session_adjustments"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_adjustment_user_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(12), default="assistant")  # assistant | user
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMCall(Base):
    """Per-call token accounting so the cost dashboard can show spend and cache-hit rate."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(40))
    tier: Mapped[str] = mapped_column(String(8))  # fast | plan
    purpose: Mapped[str] = mapped_column(String(30))  # ask | extract | plan | review
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="llm_calls")


class Program(Base):
    """An LLM-generated training or nutrition program. The payload is the full plan as
    validated JSON; `summary` is a short version that lives in the model's cached prompt prefix."""

    __tablename__ = "programs"
    __table_args__ = (
        # One active program per user and kind; archiving happens in the same transaction.
        Index("uq_programs_one_active", "user_id", "kind", unique=True, postgresql_where=text("status = 'active'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # training | nutrition
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | active | archived
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(120))
    summary: Mapped[str] = mapped_column(String(500))
    payload: Mapped[dict] = mapped_column(JSONB)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkoutLog(Base):
    """One completed training session as performed (which may deviate from the plan)."""

    __tablename__ = "workout_logs"
    __table_args__ = (Index("ix_workout_logs_user_date", "user_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    program_id: Mapped[int | None] = mapped_column(ForeignKey("programs.id", ondelete="SET NULL"))
    session_name: Mapped[str] = mapped_column(String(120))
    exercises: Mapped[list] = mapped_column(JSONB)  # [{name, sets: [{reps?, weight_kg?, rpe?}]}]
    duration_min: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
