"""Program generation and review on the plan-tier model, plus workout-log persistence.

Generation returns schema-validated JSON (structured outputs) with exactly one retry that
feeds the validation error back to the model; a second failure raises ProgramGenerationError
so the route can answer 502 instead of storing a broken plan.
"""

import json
import logging
import math
import re
from datetime import UTC, date, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.data_access.food import intake_window
from app.data_access.memory import list_memories, list_prefix_memories, record_llm_call
from app.data_access.users import UserProfile, get_user_days
from app.db.models import Program, SessionAdjustment, WorkoutLog
from app.programs.schemas import (
    ExerciseHistoryRow,
    ExerciseSetOut,
    EnergyInputs,
    NutritionPlanOutput,
    ProgramKind,
    StrengthOut,
    StrengthWeek,
    TrainingPlanOutput,
    WeeklyPlanOutput,
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
- Follow the user's brief, explicitly distinguish unknowns from known facts, and never invent
  injuries, activity, body measurements or dietary restrictions. State assumptions in rationale.
- Training plans must provide actionable sessions and progression; nutrition plans must include
  concrete meals and portions when the user asks for them.
- Do not guess missing sex, activity, expenditure or medical history. For minors, pregnancy,
  eating disorders or relevant medical conditions, avoid restrictive targets and recommend
  qualified professional guidance.
- Treat the server-provided energy number as estimated maintenance only. Do not prescribe or
  describe a calorie deficit or surplus. Protein/carbs/fat energy (4/4/9 kcal per gram) should
  approximate that maintenance number. Do not cite other total calorie targets in text.
- Respect every constraint in <memories>: injuries, disliked exercises or foods, equipment, schedule.
- Follow the recovery and deload protocols; never prescribe more than the user's current metrics support.
- Fill every field of the output schema. Use empty strings only where the schema documents them.
- `summary` must stand alone in at most 500 characters: it is shown to the daily coach in every
  later conversation, so it carries the weekly structure and the key targets.
- Leave `meals` empty unless the user asks for meal ideas; otherwise honor their requested format.
- If the user's message or metrics contain medical red flags, produce a conservative plan and
  state in `rationale` that they should see a doctor before starting."""


class ProgramGenerationError(Exception):
    """The model failed to produce a schema-valid program after one retry."""


class ProgramInputError(Exception):
    """A numeric nutrition plan needs more information or professional care."""


def require_nutrition_profile(user: UserProfile) -> None:
    if user.age is None:
        raise ProgramInputError("Please provide your age before creating a nutrition plan.")
    if user.age < 18:
        raise ProgramInputError("For users under 18, consult a qualified health professional for nutrition plans.")
    missing = [label for value, label in ((user.weight_kg, "weight"), (user.height_cm, "height")) if value is None]
    if missing:
        raise ProgramInputError("Please provide your " + " and ".join(missing) + " before creating a nutrition plan.")


def calculate_energy(user: UserProfile, inputs: EnergyInputs | None) -> dict:
    """Estimate adult maintenance, not a medical prescription or weight-change target."""
    require_nutrition_profile(user)
    if inputs is None:
        raise ProgramInputError(
            "A nutrition plan needs your equation choice, activity level and safety confirmation. "
            "Complete the energy estimate form on the web Plan page."
        )
    weight, height, age = user.weight_kg, user.height_cm, user.age
    assert weight is not None and height is not None and age is not None
    if not (math.isfinite(weight) and math.isfinite(height) and 20 < weight < 400 and 80 < height < 250 and age <= 110):
        raise ProgramInputError("Profile measurements are invalid; update them before calculating energy.")
    if inputs.safety_confirmed is not True:
        raise ProgramInputError("Confirm the safety conditions before calculating energy.")
    if weight / (height / 100) ** 2 < 18.5:
        raise ProgramInputError("For low body weight, consult a qualified health professional before setting a target.")
    factor = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "very_active": 1.725}[inputs.activity]
    resting = 10 * weight + 6.25 * height - 5 * age + (5 if inputs.equation == "male" else -161)
    target = round(resting * factor)
    if not 800 <= target <= 8000:
        raise ProgramInputError("The estimated energy value is outside the supported range; consult a professional.")
    return {
        "method": "mifflin_st_jeor_maintenance",
        "equation": inputs.equation,
        "activity": inputs.activity,
        "activity_factor": factor,
        "age": age,
        "weight_kg": weight,
        "height_cm": height,
        "resting_kcal": round(resting),
        "target_kcal": target,
    }


def check_nutrition_safety(brief: str | None, memories) -> None:
    text = " ".join([brief or "", *(f"{m.key} {m.value}" for m in memories)])
    if re.search(
        r"hamile|gebey|gebelik|emzir|yeme bozuk|anoreksi|bulimi|diyabet|böbrek|"
        r"pregnan|breastfeed|eating disorder|anorexi|bulimi|diabet|kidney",
        text, re.IGNORECASE,
    ):
        raise ProgramInputError(
            "The request or saved context mentions a condition needing individualized care; "
            "consult a qualified professional. Correct any inaccurate saved information."
        )


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


def _validated_plan_call(
    db: Session,
    coach: CoachClient,
    user: UserProfile,
    output_model,
    schema_name: str,
    stable: str,
    volatile: str,
    purpose: str = "plan",
    energy_target: int | None = None,
):
    """Plan-tier structured call with exactly one retry that feeds the validation error back."""
    schema = _strict_schema(output_model.model_json_schema())
    last_error = ""
    for attempt in range(2):
        prompt = volatile if not last_error else f"{volatile}\n\nYour previous output failed validation: {last_error}\nReturn corrected JSON."
        reply = coach.complete(
            stable, [], prompt,
            tier="plan", effort="medium",
            json_schema=schema, schema_name=schema_name,
            cache_key=f"coach:{user.user_id}",
        )
        record_llm_call(db, user.user_id, reply.usage, tier="plan", purpose=purpose)
        try:
            parsed = output_model.model_validate(json.loads(reply.text))
            if energy_target is not None:
                nutrition = parsed.nutrition if isinstance(parsed, WeeklyPlanOutput) else parsed.plan
                if nutrition.target_kcal != energy_target:
                    raise ValueError(f"target_kcal must equal the calculated maintenance estimate {energy_target}")
                macro_kcal = 4 * (nutrition.protein_g + nutrition.carbs_g) + 9 * nutrition.fat_g
                if abs(macro_kcal - energy_target) > energy_target * 0.15:
                    raise ValueError("Protein, carbohydrate and fat must approximately match the maintenance estimate")
                text = " ".join(
                    [parsed.rationale, *nutrition.guidelines]
                    + ([parsed.nutrition_name, parsed.nutrition_summary, parsed.training_name, parsed.training_summary]
                       if isinstance(parsed, WeeklyPlanOutput)
                       else [parsed.name, parsed.summary])
                )
                amounts = re.findall(r"(?<![\d.,])(\d{1,2}[.,]\d{3}|\d{3,5})\s*(?:kcal|calories|kalori)\b", text, re.IGNORECASE)
                amounts += re.findall(
                    r"(?:target|hedef(?:i|iniz)?|daily|günlük)\D{0,15}(\d{1,2}[.,]\d{3}|\d{4,5})\b",
                    text, re.IGNORECASE,
                )
                if any(int(amount.replace(",", "").replace(".", "")) != energy_target for amount in amounts):
                    raise ValueError("Nutrition summary, rationale and guidelines must not introduce another calorie target")
                if re.search(r"\b(?:deficit|surplus)\b|kalori açığı|kalorik açık|kalori fazlası", text, re.IGNORECASE):
                    raise ValueError("Use the maintenance estimate only; do not prescribe a deficit or surplus")
            return parsed
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = str(exc)[:800]
            logger.warning("%s attempt %d failed validation", schema_name, attempt + 1)
    raise ProgramGenerationError(last_error)


def _next_version(db: Session, user_id: str, kind: str) -> int:
    return (db.scalar(select(func.max(Program.version)).where(Program.user_id == user_id, Program.kind == kind)) or 0) + 1


def _activate(db: Session, program: Program) -> None:
    db.execute(
        update(Program)
        .where(Program.user_id == program.user_id, Program.kind == program.kind, Program.status == "active")
        .values(status="archived")
    )
    program.status = "active"
    program.activated_at = datetime.now(UTC)


def generate_program(
    db: Session,
    coach: CoachClient,
    embedder: Embedder,
    store: ProtocolStore,
    user: UserProfile,
    kind: ProgramKind,
    brief: str | None,
    settings: Settings,
    energy: EnergyInputs | None = None,
) -> Program:
    calculation = calculate_energy(user, energy) if kind == "nutrition" else None
    output_model = TrainingPlanOutput if kind == "training" else NutritionPlanOutput
    memories = list_prefix_memories(db, user.user_id, settings.memory_max_entries)
    if calculation:
        check_nutrition_safety(brief, list_memories(db, user.user_id))
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
    task = f"Task: design a {kind} program" + (f". User brief: {brief}" if brief else "") + "."
    if kind == "nutrition":
        task += (
            f" The server-calculated estimated MAINTENANCE is {calculation['target_kcal']} kcal/day;"
            f" the user's explicit activity choice is {calculation['activity']} (factor {calculation['activity_factor']})."
            " use exactly this as target_kcal. It is an estimate, not a deficit or medical prescription."
            " Do not invent a different calorie target. Include meals if requested."
        )
    volatile_blocks += [format_chunks(chunks), task]

    parsed = _validated_plan_call(
        db, coach, user, output_model, f"{kind}_program", stable, "\n\n".join(volatile_blocks),
        energy_target=calculation["target_kcal"] if calculation else None,
    )
    payload = parsed.plan.model_dump()
    if calculation:
        payload["energy_calculation"] = calculation
    program = Program(
        user_id=user.user_id,
        kind=kind,
        status="draft",
        version=_next_version(db, user.user_id, kind),
        name="Nutrition — estimated maintenance" if calculation else parsed.name[:120],
        summary=(f"Estimated maintenance: {calculation['target_kcal']} kcal/day. "
                 "This is not a weight-loss prescription.") if calculation else parsed.summary[:500],
        payload=payload,
        rationale=parsed.rationale,
    )
    db.add(program)
    db.commit()
    return program


WEEKLY_TASK = """Task: design ONE coherent weekly plan covering training, cardio and nutrition together.

Requirements:
- training.weeks[0] must lay out 7 CONSECUTIVE days starting from the plan's start date: if the
  user's brief names a start date, use exactly that date; otherwise start TOMORROW — do NOT snap
  to next Monday. Each day's `name` must carry the real weekday and calendar date plus its number
  counting 1..7 from the start, in the user's language, e.g. "Thursday 13 Nov — day 1". Each
  day gets a day_type (strength / cardio / mixed / rest). Include 1-3 dedicated or appended cardio
  sessions with modality, duration and intensity in the cardio field (zone 2 for recovery/fat loss,
  intervals only when recovery supports it).
- nutrition must match the training load and the user's goal: a single daily kcal target plus
  protein/carbs/fat/fiber grams and a sodium cap in mg. Include meals and portions when the brief
  requests them; otherwise leave `meals` empty. Note uncertain assumptions and adjustments.
- The two summaries must stand alone: each is shown to the daily coach in later conversations.
- Respect <memories> (schedule, equipment, injuries, food rules) and the metrics; poor recovery
  can require rest. Do not prescribe compensatory exercise for a high-calorie day."""


def generate_weekly_plan(
    db: Session,
    coach: CoachClient,
    embedder: Embedder,
    store: ProtocolStore,
    user: UserProfile,
    brief: str | None,
    settings: Settings,
    energy: EnergyInputs | None = None,
) -> tuple[Program, Program]:
    """Generate training+cardio+nutrition as one coherent plan; both halves activate immediately."""
    calculation = calculate_energy(user, energy)
    memories = list_prefix_memories(db, user.user_id, settings.memory_max_entries)
    check_nutrition_safety(brief, list_memories(db, user.user_id))
    training_chunks = _retrieve_program_protocols(embedder, store, "training", user, brief)
    nutrition_chunks = _retrieve_program_protocols(embedder, store, "nutrition", user, brief)
    seen = {c.protocol_id for c in training_chunks}
    chunks = training_chunks + [c for c in nutrition_chunks if c.protocol_id not in seen]

    stable = "\n\n".join([PLAN_SYSTEM, format_profile(user), format_memories(memories)])
    volatile_blocks = [format_user_context(user)]
    intake = intake_window(db, user.user_id, days=7)
    if intake:
        volatile_blocks.append(
            "Recent daily intake: " + ", ".join(f"{d.date}: {d.kcal} kcal" for d in intake if d.entries)
        )
    workouts = list_workouts(db, user.user_id, since=date.today() - timedelta(days=13))
    if workouts:
        volatile_blocks.append(
            "Recent training: " + ", ".join(f"{w.date} {w.session_name}" for w in workouts)
        )
    volatile_blocks += [
        format_chunks(chunks),
        WEEKLY_TASK + (f"\nUser brief: {brief}" if brief else "")
        + f"\nThe server-calculated estimated MAINTENANCE is {calculation['target_kcal']} kcal/day. "
        + f"The user's activity choice is {calculation['activity']} (factor {calculation['activity_factor']}). "
        "Use exactly this as nutrition.target_kcal; do not invent another calorie target or deficit.",
    ]

    parsed = _validated_plan_call(
        db, coach, user, WeeklyPlanOutput, "weekly_plan", stable, "\n\n".join(volatile_blocks),
        energy_target=calculation["target_kcal"],
    )
    training = Program(
        user_id=user.user_id, kind="training", status="draft",
        version=_next_version(db, user.user_id, "training"),
        name=parsed.training_name[:120], summary=parsed.training_summary[:500],
        payload=parsed.training.model_dump(), rationale=parsed.rationale,
    )
    nutrition = Program(
        user_id=user.user_id, kind="nutrition", status="draft",
        version=_next_version(db, user.user_id, "nutrition"),
        name="Nutrition — estimated maintenance",
        summary=f"Estimated maintenance: {calculation['target_kcal']} kcal/day. This is not a weight-loss prescription.",
        payload={**parsed.nutrition.model_dump(), "energy_calculation": calculation}, rationale=parsed.rationale,
    )
    db.add(training)
    db.add(nutrition)
    db.flush()
    _activate(db, training)
    _activate(db, nutrition)
    db.commit()
    return training, nutrition


def revise_program(
    db: Session,
    coach: CoachClient,
    embedder: Embedder,
    store: ProtocolStore,
    user: UserProfile,
    program: Program,
    feedback: str,
    settings: Settings,
    energy: EnergyInputs | None = None,
) -> Program:
    """Apply the user's adherence feedback: produces version N+1 and activates it.
    History is kept — old versions stay archived."""
    calculation = calculate_energy(user, energy) if program.kind == "nutrition" else None
    output_model = TrainingPlanOutput if program.kind == "training" else NutritionPlanOutput
    memories = list_prefix_memories(db, user.user_id, settings.memory_max_entries)
    if calculation:
        check_nutrition_safety(feedback, list_memories(db, user.user_id))
    chunks = _retrieve_program_protocols(embedder, store, program.kind, user, feedback)

    stable = "\n\n".join([PLAN_SYSTEM, format_profile(user), format_memories(memories)])
    current = json.dumps({"name": program.name, "summary": program.summary, "plan": program.payload})[:12000]
    volatile = "\n\n".join([
        format_user_context(user),
        format_chunks(chunks),
        f"The user could not adhere to this {program.kind} plan (v{program.version}). Current plan JSON:\n{current}",
        f"User feedback: {feedback}",
        "Revise the plan so the feedback is accommodated while keeping everything that already works. "
        "Change only what the feedback requires; return the FULL updated plan with an updated summary.",
    ])
    if calculation:
        volatile += (
            f"\nThe server-calculated estimated MAINTENANCE is {calculation['target_kcal']} kcal/day. "
            f"The user's activity choice is {calculation['activity']} (factor {calculation['activity_factor']}). "
            "Use exactly this as target_kcal; do not reuse an old target or invent a deficit."
        )

    parsed = _validated_plan_call(
        db, coach, user, output_model, f"{program.kind}_program", stable, volatile, purpose="review",
        energy_target=calculation["target_kcal"] if calculation else None,
    )
    payload = parsed.plan.model_dump()
    if calculation:
        payload["energy_calculation"] = calculation
    revised = Program(
        user_id=user.user_id, kind=program.kind, status="draft",
        version=_next_version(db, user.user_id, program.kind),
        name="Nutrition — estimated maintenance" if calculation else parsed.name[:120],
        summary=(f"Estimated maintenance: {calculation['target_kcal']} kcal/day. "
                 "This is not a weight-loss prescription.") if calculation else parsed.summary[:500],
        payload=payload, rationale=parsed.rationale,
    )
    db.add(revised)
    db.flush()
    _activate(db, revised)
    db.commit()
    return revised


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
    return "\n\n".join(
        "Nutrition program: older energy target unverified; do not use it as a calorie target."
        if p.kind == "nutrition" and not p.payload.get("energy_calculation")
        else f"{p.kind.capitalize()} program v{p.version} — {p.name}: {p.summary}"
        for p in programs
    )


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


def est_1rm(weight_kg: float, reps: int) -> float:
    """Epley estimate: w x (1 + reps/30). Only meaningful up to ~10 reps."""
    return round(weight_kg * (1 + reps / 30), 1)


def _row_e1rm(sets: list[ExerciseSetOut]) -> float | None:
    candidates = [
        est_1rm(s.weight_kg, s.reps)
        for s in sets
        if s.weight_kg is not None and s.reps is not None and s.reps > 0
    ]
    return max(candidates) if candidates else None


def exercise_history(db: Session, user_id: str, name: str, days: int = 90) -> list[ExerciseHistoryRow]:
    """Per-session performance for exercises whose name contains `name` (case-insensitive),
    with progression aggregates: best weight, total reps, volume and estimated 1RM."""
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
                ExerciseSetOut(
                    reps=s.get("reps"), weight_kg=s.get("weight_kg"), rpe=s.get("rpe"), rir=s.get("rir")
                )
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
                    est_1rm_kg=_row_e1rm(sets),
                )
            )
    return history


def strength_summary(db: Session, user_id: str, name: str, weeks: int = 8) -> StrengthOut:
    """Weekly sets/volume/e1RM aggregation plus the all-time PR — the hypertrophy dashboard
    numbers (weekly set count vs MEV/MAV/MRV landmarks, volume load trend, top-set e1RM)."""
    rows = exercise_history(db, user_id, name, days=weeks * 7 + 7)
    exercise_label = rows[-1].exercise if rows else name.strip()

    by_week: dict[date, list[ExerciseHistoryRow]] = {}
    for r in rows:
        week_start = r.date - timedelta(days=r.date.weekday())
        by_week.setdefault(week_start, []).append(r)
    weekly = [
        StrengthWeek(
            week_start=week_start,
            sets=sum(len(r.sets) for r in week_rows),
            total_reps=sum(r.total_reps for r in week_rows),
            volume_kg=round(sum(r.volume_kg for r in week_rows), 1),
            top_e1rm_kg=max((r.est_1rm_kg for r in week_rows if r.est_1rm_kg is not None), default=None),
        )
        for week_start, week_rows in sorted(by_week.items())
    ]

    pr_weight = pr_reps = None
    pr_date = None
    best_e1rm = None
    for r in rows:
        for s in r.sets:
            if s.weight_kg is None:
                continue
            if pr_weight is None or s.weight_kg > pr_weight:
                pr_weight, pr_reps, pr_date = s.weight_kg, s.reps, r.date
        if r.est_1rm_kg is not None and (best_e1rm is None or r.est_1rm_kg > best_e1rm):
            best_e1rm = r.est_1rm_kg
    return StrengthOut(
        exercise=exercise_label, weekly=weekly,
        pr_weight_kg=pr_weight, pr_reps=pr_reps, pr_date=pr_date, best_e1rm_kg=best_e1rm,
    )


_MONTHS_TR = ("oca", "şub", "mar", "nis", "may", "haz", "tem", "ağu", "eyl", "eki", "kas", "ara")
_MONTHS_EN = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_NAME_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-zçğıöşüÇĞİÖŞÜ]{3,9})")
_NAME_DATE_RE_EN = re.compile(r"([A-Za-zçğıöşüÇĞİÖŞÜ]{3,9})\.?\s+(\d{1,2})")


def _day_name_date(name: str) -> tuple[int, int] | None:
    """(month, day) when a plan day's name carries a calendar date ("Perşembe 13 Kasım — 1. gün")."""
    for regex, pick in ((_NAME_DATE_RE, lambda m: (m.group(2), m.group(1))), (_NAME_DATE_RE_EN, lambda m: (m.group(1), m.group(2)))):
        m = regex.search(name)
        if not m:
            continue
        month_token, day_token = pick(m)
        mon = month_token.lower()[:3]
        for table in (_MONTHS_TR, _MONTHS_EN):
            if mon in table:
                return table.index(mon) + 1, int(day_token)
    return None


def today_session(program: Program, day: date) -> str:
    """The planned session for `day`. Plans whose day names carry real calendar dates are matched
    by date (a plan can start any weekday); otherwise falls back to the Monday-first slot index."""
    weeks = program.payload.get("weeks", [])
    if not weeks:
        return "The active program has no weeks in its payload."
    anchor = program.activated_at or program.created_at or datetime.now(UTC)
    start = anchor.date()
    week_idx = min(max((day - start).days // 7, 0), len(weeks) - 1)
    week = weeks[week_idx]
    days_list = week.get("days", [])
    header = f"Week {week_idx + 1} ({week.get('focus', '')})"

    named_dates = [_day_name_date(str(s.get("name", ""))) for s in days_list]
    if any(named_dates):
        session = next((s for s, md in zip(days_list, named_dates) if md == (day.month, day.day)), None)
        if session is None:
            return (
                f"{header}: the active plan does not cover {day.isoformat()} — it spans "
                f"{len(days_list)} dated days. Treat as rest/light activity unless the user asks to adjust."
            )
    else:
        slot = day.weekday()
        if slot >= len(days_list):
            return f"{header}: rest day — no session planned for today."
        session = days_list[slot]
    day_type = session.get("day_type", "strength")
    lines = [f"{header}: {session.get('name')} — {session.get('focus')} [{day_type}]"]
    if session.get("cardio"):
        lines.append(f"  Cardio: {session['cardio']}")
    lines.extend(
        f"  - {e.get('name')}: {e.get('sets')} x {e.get('reps')} @ {e.get('weight_guidance', '')}"
        + (f" (RPE {e.get('rpe_target')})" if e.get("rpe_target") else "")
        for e in session.get("exercises", [])
    )
    return "\n".join(lines)


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
