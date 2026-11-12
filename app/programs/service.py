"""Program generation and review on the plan-tier model, plus workout-log persistence.

Generation returns schema-validated JSON (structured outputs) with exactly one retry that
feeds the validation error back to the model; a second failure raises ProgramGenerationError
so the route can answer 502 instead of storing a broken plan.
"""

import json
import logging
from datetime import UTC, date, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.data_access.food import intake_window
from app.data_access.memory import list_prefix_memories, record_llm_call
from app.data_access.users import UserProfile, get_user_days
from app.db.models import Program, SessionAdjustment, WorkoutLog
from app.programs.schemas import (
    ExerciseHistoryRow,
    ExerciseSetOut,
    NutritionPlanOutput,
    ProgramKind,
    TrainingPlanOutput,
    WorkoutOut,
)
from app.rag.embeddings import Embedder
from app.rag.generator import CoachClient
from app.rag.prompt import format_chunks, format_memories, format_profile, format_user_context
from app.rag.vector_store import ProtocolStore, RetrievedChunk

logger = logging.getLogger("fitness_coach")

PLAN_SYSTEM = """You design safe, evidence-informed training and nutrition programs.

Rules:
- Ground every choice in the protocols provided and in the user's metrics, profile and memories.
- Respect every constraint in <memories>: injuries, disliked exercises or foods, equipment, schedule.
- Follow the recovery and deload protocols; never prescribe more than the user's current metrics support.
- Fill every field of the output schema. Use empty strings only where the schema documents them.
- `summary` must stand alone in at most 500 characters: it is shown to the daily coach in every
  later conversation, so it carries the weekly structure and the key targets.
- If the user's message or metrics contain medical red flags, produce a conservative plan and
  state in `rationale` that they should see a doctor before starting."""


class ProgramGenerationError(Exception):
    """The model failed to produce a schema-valid program after one retry."""


def _strict_schema(schema: dict) -> dict:
    """Make a pydantic-generated JSON schema strict-mode compatible:
    every object gets additionalProperties=false and all properties required."""

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = sorted(node["properties"].keys())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return schema


def _retrieve_program_protocols(
    embedder: Embedder, store: ProtocolStore, kind: ProgramKind, user: UserProfile, brief: str | None
) -> list[RetrievedChunk]:
    query = f"{kind} program design for goal {user.goal}" + (f": {brief}" if brief else "")
    category = "training" if kind == "training" else "nutrition"
    vector = embedder.embed_one(query)
    chunks = store.search(vector, top_k=5, category=category)
    safety = store.search(embedder.embed_one("medical red flags safety"), top_k=1, category="safety")
    seen = {c.protocol_id for c in chunks}
    return chunks + [c for c in safety if c.protocol_id not in seen]


def generate_program(
    db: Session,
    coach: CoachClient,
    embedder: Embedder,
    store: ProtocolStore,
    user: UserProfile,
    kind: ProgramKind,
    brief: str | None,
    settings: Settings,
) -> Program:
    output_model = TrainingPlanOutput if kind == "training" else NutritionPlanOutput
    memories = list_prefix_memories(db, user.user_id, settings.memory_max_entries)
    chunks = _retrieve_program_protocols(embedder, store, kind, user, brief)

    stable = "\n\n".join([PLAN_SYSTEM, format_profile(user), format_memories(memories)])
    volatile_blocks = [format_user_context(user)]
    if kind == "nutrition":
        intake = intake_window(db, user.user_id, days=7)
        if intake:
            volatile_blocks.append(
                "Recent daily intake: "
                + ", ".join(f"{d.date}: {d.kcal} kcal" for d in intake if d.entries)
            )
    volatile_blocks += [
        format_chunks(chunks),
        f"Task: design a {kind} program" + (f". User brief: {brief}" if brief else "") + ".",
    ]
    volatile = "\n\n".join(volatile_blocks)

    schema = _strict_schema(output_model.model_json_schema())
    last_error = ""
    for attempt in range(2):
        prompt = volatile if not last_error else f"{volatile}\n\nYour previous output failed validation: {last_error}\nReturn corrected JSON."
        reply = coach.complete(
            stable, [], prompt,
            tier="plan", effort="medium",
            json_schema=schema, schema_name=f"{kind}_program",
            cache_key=f"coach:{user.user_id}",
        )
        record_llm_call(db, user.user_id, reply.usage, tier="plan", purpose="plan")
        try:
            parsed = output_model.model_validate(json.loads(reply.text))
            break
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)[:800]
            logger.warning("program generation attempt %d failed validation", attempt + 1)
    else:
        raise ProgramGenerationError(last_error)

    version = (
        db.scalar(select(func.max(Program.version)).where(Program.user_id == user.user_id, Program.kind == kind))
        or 0
    ) + 1
    program = Program(
        user_id=user.user_id,
        kind=kind,
        status="draft",
        version=version,
        name=parsed.name[:120],
        summary=parsed.summary[:500],
        payload=parsed.plan.model_dump(),
        rationale=parsed.rationale,
    )
    db.add(program)
    db.commit()
    return program


def list_programs(db: Session, user_id: str, status: str | None = None) -> list[Program]:
    stmt = select(Program).where(Program.user_id == user_id)
    if status:
        stmt = stmt.where(Program.status == status)
    return list(db.scalars(stmt.order_by(Program.created_at.desc(), Program.id.desc())))


def get_program(db: Session, user_id: str, program_id: int) -> Program | None:
    program = db.get(Program, program_id)
    return program if program is not None and program.user_id == user_id else None


def get_active_program(db: Session, user_id: str, kind: ProgramKind) -> Program | None:
    return db.scalar(
        select(Program).where(Program.user_id == user_id, Program.kind == kind, Program.status == "active")
    )


def active_programs_summary(db: Session, user_id: str) -> str:
    """Concatenated summaries of the user's active programs, for the stable prompt prefix."""
    programs = db.scalars(
        select(Program).where(Program.user_id == user_id, Program.status == "active").order_by(Program.kind)
    )
    return "\n\n".join(f"{p.kind.capitalize()} program v{p.version} — {p.name}: {p.summary}" for p in programs)


def activate_program(db: Session, user_id: str, program_id: int) -> Program | None:
    program = get_program(db, user_id, program_id)
    if program is None:
        return None
    db.execute(
        update(Program)
        .where(Program.user_id == user_id, Program.kind == program.kind, Program.status == "active")
        .values(status="archived")
    )
    program.status = "active"
    program.activated_at = datetime.now(UTC)
    db.commit()
    return program


def review_program(
    db: Session,
    coach: CoachClient,
    program: Program,
    user: UserProfile,
    settings: Settings,
) -> str:
    """Adherence + trend analysis of an active/past program, as markdown from the plan tier."""
    start = (program.activated_at or program.created_at).date()
    today = date.today()
    lines = [f"Program: {program.name} (v{program.version}, {program.kind}, started {start}).", program.summary]

    workouts = list_workouts(db, program.user_id, since=start)
    if program.kind == "training":
        weeks = program.payload.get("weeks", [])
        week_idx = min((today - start).days // 7, len(weeks) - 1) if weeks else 0
        planned = sum(len(w.get("days", [])) for w in weeks[: week_idx + 1])
        lines.append(f"Sessions planned through week {week_idx + 1}: {planned}; completed: {len(workouts)}.")
        for w in workouts:
            lines.append(
                f"  {w.date} {w.session_name}: "
                + "; ".join(f"{e.name} {len(e.sets)}x" for e in w.exercises[:6])
            )
    else:
        target = program.payload.get("target_kcal")
        intake = [d for d in intake_window(db, program.user_id, days=max((today - start).days, 1)) if d.entries]
        if intake:
            avg_kcal = sum(d.kcal for d in intake) / len(intake)
            avg_protein = [d.protein_g for d in intake if d.protein_g is not None]
            lines.append(
                f"Days with intake logs: {len(intake)}; average {avg_kcal:.0f} kcal/day"
                + (f", average protein {sum(avg_protein) / len(avg_protein):.0f} g/day" if avg_protein else "")
                + (f" vs target {target} kcal" if target else "")
                + "."
            )
        else:
            lines.append("No intake logs since the program started.")

    days = get_user_days(db, user.user_id)
    metric_days = [d for d in days if d.date >= start]
    if len(metric_days) >= 4:
        half = len(metric_days) // 2

        def avg(values):
            known = [v for v in values if v is not None]
            return sum(known) / len(known) if known else None

        lines.append(
            "Biometrics since start (first half vs second half): "
            f"HRV {avg([d.hrv_ms for d in metric_days[:half]])} vs {avg([d.hrv_ms for d in metric_days[half:]])}, "
            f"resting HR {avg([d.resting_hr_bpm for d in metric_days[:half]])} vs {avg([d.resting_hr_bpm for d in metric_days[half:]])}, "
            f"sleep minutes {avg([d.sleep_minutes for d in metric_days[:half]])} vs {avg([d.sleep_minutes for d in metric_days[half:]])}."
        )

    memories = list_prefix_memories(db, user.user_id, settings.memory_max_entries)
    stable = "\n\n".join([PLAN_SYSTEM, format_profile(user), format_memories(memories)])
    volatile = (
        "Review this program against the user's adherence and metrics. Return markdown with sections "
        "Assessment, Adherence, Adjustments (concrete bullet points). Do not return a new full program.\n\n"
        + "\n".join(lines)
    )
    reply = coach.complete(
        stable, [], volatile, tier="plan", effort="medium",
        cache_key=f"coach:{user.user_id}",
    )
    record_llm_call(db, user.user_id, reply.usage, tier="plan", purpose="review")
    db.commit()
    return reply.text


def add_workout(
    db: Session,
    user_id: str,
    day: date,
    session_name: str,
    exercises: list[dict],
    duration_min: int | None = None,
    notes: str | None = None,
) -> WorkoutLog:
    program = get_active_program(db, user_id, "training")
    row = WorkoutLog(
        user_id=user_id, date=day, program_id=program.id if program else None,
        session_name=session_name[:120], exercises=exercises, duration_min=duration_min, notes=notes,
    )
    db.add(row)
    return row


def list_workouts(db: Session, user_id: str, since: date | None = None, until: date | None = None) -> list[WorkoutOut]:
    stmt = select(WorkoutLog).where(WorkoutLog.user_id == user_id)
    if since:
        stmt = stmt.where(WorkoutLog.date >= since)
    if until:
        stmt = stmt.where(WorkoutLog.date <= until)
    rows = db.scalars(stmt.order_by(WorkoutLog.date, WorkoutLog.id)).all()
    return [
        WorkoutOut(
            id=r.id, date=r.date, program_id=r.program_id, session_name=r.session_name,
            exercises=r.exercises, duration_min=r.duration_min, notes=r.notes, created_at=r.created_at,
        )
        for r in rows
    ]


def delete_workout(db: Session, user_id: str, workout_id: int) -> bool:
    row = db.get(WorkoutLog, workout_id)
    if row is None or row.user_id != user_id:
        return False
    db.delete(row)
    return True


def exercise_names(db: Session, user_id: str, days: int = 90) -> list[str]:
    """Distinct exercise names logged in the window, deduped case-insensitively."""
    since = date.today() - timedelta(days=days - 1)
    rows = db.scalars(
        select(WorkoutLog)
        .where(WorkoutLog.user_id == user_id, WorkoutLog.date >= since)
        .order_by(WorkoutLog.date, WorkoutLog.id)
    )
    canonical: dict[str, str] = {}
    for row in rows:
        for exercise in row.exercises or []:
            name = str(exercise.get("name") or "").strip()
            if name:
                canonical.setdefault(name.lower(), name)
    return sorted(canonical.values(), key=str.lower)


def exercise_history(db: Session, user_id: str, name: str, days: int = 90) -> list[ExerciseHistoryRow]:
    """Per-session performance for exercises whose name contains `name` (case-insensitive),
    with progression aggregates: best weight, total reps and volume."""
    since = date.today() - timedelta(days=days - 1)
    needle = name.strip().lower()
    rows = db.scalars(
        select(WorkoutLog)
        .where(WorkoutLog.user_id == user_id, WorkoutLog.date >= since)
        .order_by(WorkoutLog.date, WorkoutLog.id)
    )
    history: list[ExerciseHistoryRow] = []
    for row in rows:
        for exercise in row.exercises or []:
            exercise_name = str(exercise.get("name") or "").strip()
            if not exercise_name or needle not in exercise_name.lower():
                continue
            sets = [
                ExerciseSetOut(reps=s.get("reps"), weight_kg=s.get("weight_kg"), rpe=s.get("rpe"))
                for s in exercise.get("sets", [])
            ]
            weights = [s.weight_kg for s in sets if s.weight_kg is not None]
            history.append(
                ExerciseHistoryRow(
                    date=row.date,
                    session_name=row.session_name,
                    exercise=exercise_name,
                    sets=sets,
                    best_weight_kg=max(weights) if weights else None,
                    total_reps=sum(s.reps or 0 for s in sets),
                    volume_kg=round(sum((s.reps or 0) * (s.weight_kg or 0) for s in sets), 1),
                )
            )
    return history


def today_session(program: Program, day: date) -> str:
    """The planned session for `day`: week index from the start date, day-of-week slot within the week."""
    weeks = program.payload.get("weeks", [])
    if not weeks:
        return "The active program has no weeks in its payload."
    start = (program.activated_at or program.created_at).date()
    week_idx = min(max((day - start).days // 7, 0), len(weeks) - 1)
    week = weeks[week_idx]
    days_list = week.get("days", [])
    slot = day.weekday()
    header = f"Week {week_idx + 1} ({week.get('focus', '')})"
    if slot >= len(days_list):
        return f"{header}: rest day — no session planned for today."
    session = days_list[slot]
    exercises = "\n".join(
        f"  - {e.get('name')}: {e.get('sets')} x {e.get('reps')} @ {e.get('weight_guidance', '')}"
        + (f" (RPE {e.get('rpe_target')})" if e.get("rpe_target") else "")
        for e in session.get("exercises", [])
    )
    return f"{header}: {session.get('name')} — {session.get('focus')}\n{exercises}"


def set_adjustment(
    db: Session, user_id: str, day: date, note: str, reason: str | None = None, source: str = "assistant"
) -> bool:
    """Date-scoped override of the planned session (e.g. easy cardio after a surplus day).
    An empty note clears the adjustment. Returns True when an adjustment row exists afterwards."""
    if not note.strip():
        db.execute(delete(SessionAdjustment).where(SessionAdjustment.user_id == user_id, SessionAdjustment.date == day))
        return False
    stmt = (
        insert(SessionAdjustment)
        .values(user_id=user_id, date=day, note=note.strip()[:500], reason=(reason or "")[:300] or None, source=source)
        .on_conflict_do_update(
            constraint="uq_adjustment_user_date",
            set_={"note": note.strip()[:500], "reason": (reason or "")[:300] or None, "source": source},
        )
    )
    db.execute(stmt)
    return True


def get_adjustment(db: Session, user_id: str, day: date) -> SessionAdjustment | None:
    return db.scalar(
        select(SessionAdjustment).where(SessionAdjustment.user_id == user_id, SessionAdjustment.date == day)
    )


def session_text(db: Session, user_id: str, day: date) -> str:
    """What the user should actually do on `day`: the planned session merged with any adjustment."""
    program = get_active_program(db, user_id, "training")
    if program is None:
        base = "No active training program. Ask the coach to generate one."
    else:
        base = today_session(program, day)
    adjustment = get_adjustment(db, user_id, day)
    if adjustment is not None:
        reason = f" (reason: {adjustment.reason})" if adjustment.reason else ""
        base += f"\n\nCOACH ADJUSTMENT for {day} — this takes precedence over the plan above{reason}:\n{adjustment.note}"
    return base
