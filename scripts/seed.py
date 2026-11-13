"""Load the demo profiles from data/users.json into Postgres. Safe to run repeatedly.

Usage:
    python -m scripts.seed          # profiles + a 7-day biometrics window
    python -m scripts.seed --rich   # also seed meals, runs, workouts, body measures,
                                    # programs, memories and token usage, so every page
                                    # of the dashboard has something to show

Every date is generated relative to today. A seed with hardcoded dates silently rots:
a few weeks after writing it the demo profile has no current data and the dashboard
renders empty, which is exactly what the README screenshots must not show.
"""

import argparse
import json
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.data_access.biometrics import upsert_daily_metrics
from app.data_access.food import add_food_log
from app.data_access.measurements import upsert_measurement
from app.data_access.memory import upsert_memory
from app.data_access.runs import add_run
from app.data_access.templates import seed_workout_templates, upsert_meal_template
from app.db.models import (
    BodyMeasurement,
    DailyBiometrics,
    FoodLog,
    LLMCall,
    MealTemplate,
    Program,
    RunLog,
    SessionAdjustment,
    User,
    UserMemory,
    WorkoutLog,
    WorkoutTemplate,
)
from app.db.session import get_sessionmaker

USERS_PATH = Path(__file__).resolve().parents[1] / "data" / "users.json"

# The short window `seed_demo_users` writes. Tests assert on this exact length.
WINDOW_DAYS = 7
# How far back `--rich` extends the same series.
HISTORY_DAYS = 28
MEAL_DAYS = 14
USAGE_DAYS = 14

# Plausible daily shape per goal. weight_kg and height_cm come from data/users.json.
SHAPES = {
    "strength": {
        "hrv": 66.0, "rhr": 55, "sleep": 425, "intake": 3000, "burn": 3200,
        "steps": 8200, "bf": 15.4, "waist": 84.0, "chest": 104.0, "arm": 35.5, "thigh": 57.0,
        "weight_trend": 1.8, "bf_trend": -1.1, "waist_trend": -1.0,
    },
    "fat_loss": {
        "hrv": 52.0, "rhr": 59, "sleep": 405, "intake": 1680, "burn": 2050,
        "steps": 9400, "bf": 27.5, "waist": 72.0, "chest": 88.0, "arm": 28.0, "thigh": 54.0,
        "weight_trend": -2.4, "bf_trend": -2.2, "waist_trend": -2.5,
    },
}
DEFAULT_GOAL = "strength"


def _rng(*parts: object) -> random.Random:
    """A Random seeded by identity, so the same input always gives the same demo numbers."""
    return random.Random("|".join(str(p) for p in parts))


def day_metrics(user_id: str, goal: str, offset: int) -> dict:
    """Metrics for `offset` days before today.

    A pure function of (user, goal, offset) with no running RNG stream, so the 7-day window
    and the 28-day history produce identical values for the days they share.
    """
    rng = _rng(user_id, offset)
    shape = SHAPES.get(goal, SHAPES[DEFAULT_GOAL])
    # 0.0 on the oldest day of the history, 1.0 today: a slow fitness gain across the month.
    progress = (HISTORY_DAYS - 1 - offset) / (HISTORY_DAYS - 1)
    weekly = (-2.0, 1.0, 3.0, 0.0, -3.0, 4.0, 6.0)[offset % 7]
    # Two rough nights, so recovery-driven advice has something real to react to.
    rough = offset in (3, 12)

    sleep_minutes = int(shape["sleep"] + 20 * progress + 8 * weekly - (65 if rough else 0) + rng.uniform(-12, 12))
    deep = int(sleep_minutes * 0.17)
    rem = int(sleep_minutes * 0.21)
    awake = max(2, int(sleep_minutes * 0.02))
    return {
        "hrv_ms": round(shape["hrv"] + 5 * progress + weekly + rng.uniform(-2, 2) - (13 if rough else 0), 1),
        "sleep_score": int(min(98, max(35, 76 + 8 * progress + 2 * weekly - (24 if rough else 0) + rng.uniform(-3, 3)))),
        "sleep_minutes": sleep_minutes,
        "resting_hr_bpm": int(round(shape["rhr"] - 3 * progress - weekly / 3 + (5 if rough else 0) + rng.uniform(-1, 1))),
        "calories_kcal": int(shape["intake"] * (1.12 if offset % 7 in (5, 6) else 1.0) + rng.uniform(-110, 110)),
        "steps": int(shape["steps"] * (1 + 0.22 * rng.uniform(-1, 1))),
        "active_kcal": int(shape["burn"] * 0.19 * (1 + 0.3 * rng.uniform(-1, 1))),
        "total_kcal": int(shape["burn"] + shape["burn"] * 0.06 + rng.uniform(-90, 90)),
        "sleep_stages": {"deep": deep, "rem": rem, "light": sleep_minutes - deep - rem - awake, "awake": awake},
    }


def seed_demo_users(db: Session) -> int:
    raw_users = json.loads(USERS_PATH.read_text())
    today = date.today()
    for raw in raw_users:
        db.merge(
            User(
                id=raw["user_id"],
                name=raw["name"],
                age=raw.get("age"),
                weight_kg=raw.get("weight_kg"),
                height_cm=raw.get("height_cm"),
                goal=raw["goal"],
            )
        )
        db.flush()
        for offset in range(WINDOW_DAYS - 1, -1, -1):
            day = today - timedelta(days=offset)
            upsert_daily_metrics(
                db, raw["user_id"], day, source="seed", **day_metrics(raw["user_id"], raw["goal"], offset)
            )
    db.commit()
    return len(raw_users)


# ---------- rich demo history (--rich) ----------

MEALS = {
    "strength": (
        ("breakfast", "Oats with whey, banana and peanut butter", 720, 45, 86, 22, 9),
        ("lunch", "Chicken rice bowl with roasted vegetables", 850, 55, 95, 22, 11),
        ("dinner", "Salmon, potatoes and side salad", 900, 50, 80, 35, 8),
        ("snack", "Greek yogurt with honey and granola", 320, 20, 40, 8, 3),
    ),
    "fat_loss": (
        ("breakfast", "Greek yogurt with berries and oats", 380, 28, 45, 8, 7),
        ("lunch", "Turkey and quinoa salad", 480, 40, 38, 16, 9),
        ("dinner", "Cod with roasted vegetables and bulgur", 520, 45, 48, 14, 10),
        ("snack", "Apple and a handful of almonds", 210, 6, 22, 12, 5),
    ),
}

# name, sets, reps, load guidance, RPE target — the shape the Plan page renders.
WEEK_TEMPLATE = (
    ("Push (strength)", "strength", "", (
        ("Bench Press", "4", "5", "RPE 7-8, add 2.5 kg when all sets hit 5", "7-8"),
        ("Overhead Press", "3", "6-8", "RPE 7", "7"),
        ("Incline Dumbbell Press", "3", "8-12", "RPE 8", "8"),
        ("Triceps Pushdown", "3", "10-12", "RPE 8", "8"),
        ("Lateral Raise", "3", "12-15", "RPE 8, light and controlled", "8"),
    )),
    ("Zone 2 cardio + core", "cardio", "35-40 min zone 2 cycle or brisk incline walk, conversational pace", (
        ("Plank", "3", "45 s", "Bodyweight", ""),
        ("Dead Bug", "3", "10 each side", "Bodyweight, slow tempo", ""),
    )),
    ("Pull (strength)", "strength", "", (
        ("Deadlift", "3", "5", "RPE 7-8, reset each rep", "7-8"),
        ("Barbell Row", "4", "6-8", "RPE 8", "8"),
        ("Pull-Up", "3", "6-10", "Bodyweight, add load above 10 reps", "8"),
        ("Face Pull", "3", "12-15", "RPE 7", "7"),
        ("Barbell Curl", "3", "10-12", "RPE 8", "8"),
    )),
    ("Legs (strength)", "strength", "", (
        ("Back Squat", "4", "5", "RPE 7-8, add 2.5 kg when all sets hit 5", "7-8"),
        ("Romanian Deadlift", "3", "8", "RPE 7", "7"),
        ("Leg Press", "3", "10-12", "RPE 8", "8"),
        ("Leg Curl", "3", "12", "RPE 8", "8"),
        ("Standing Calf Raise", "4", "12-15", "RPE 8, full stretch", "8"),
    )),
    ("Active recovery", "rest", "20-30 min easy walk, optional mobility work", ()),
    ("Mixed: upper + intervals", "mixed", "6 x 400 m at 5k pace, 90 s easy jog between", (
        ("Bench Press", "3", "6", "RPE 7", "7"),
        ("Barbell Row", "3", "8", "RPE 7", "7"),
        ("Overhead Press", "3", "8", "RPE 7", "7"),
    )),
    ("Rest", "rest", "", ()),
)

BASE_WEIGHT_KG = {
    "Bench Press": 62.5, "Overhead Press": 37.5, "Incline Dumbbell Press": 22.0,
    "Triceps Pushdown": 25.0, "Lateral Raise": 9.0, "Deadlift": 100.0,
    "Barbell Row": 55.0, "Pull-Up": 0.0, "Face Pull": 20.0, "Barbell Curl": 22.5,
    "Back Squat": 80.0, "Romanian Deadlift": 70.0, "Leg Press": 120.0,
    "Leg Curl": 40.0, "Standing Calf Raise": 45.0, "Plank": 0.0, "Dead Bug": 0.0,
}

# days ago, distance_km, duration_min, avg_hr — pace holds while HR falls, i.e. aerobic gain.
RUNS = ((24, 6.8, 42.5, 154, "google"), (17, 5.2, 33.0, 153, "manual"),
        (10, 7.4, 45.0, 150, "google"), (4, 5.0, 29.5, 147, "assistant"))

MEMORIES = {
    "u1": (
        ("preference", "training_schedule", "Trains Monday, Wednesday and Thursday evenings; weekends are family time.", "user", True),
        ("constraint", "injuries", "Mild left shoulder impingement — no behind-the-neck pressing, keep elbows tucked.", "user", True),
        ("preference", "equipment", "Commercial gym: racks, dumbbells to 40 kg, full machine set.", "assistant", False),
        ("fact", "protein_target", "Targets 1.8 g/kg of protein per day.", "assistant", False),
    ),
    "u2": (
        ("preference", "training_schedule", "Lunch breaks only, 45-minute sessions, three times a week.", "user", True),
        ("constraint", "diet", "No red meat; eats fish, dairy and eggs.", "user", True),
        ("preference", "cardio", "Prefers walking and running outdoors to cycling.", "assistant", False),
    ),
}

MEAL_TEMPLATES = {
    "u1": ("Standard breakfast", "breakfast", [
        {"description": "Oats with whey, banana and peanut butter", "servings": 1.0, "kcal": 720, "protein_g": 45, "carbs_g": 86, "fat_g": 22},
    ]),
    "u2": ("Standard breakfast", "breakfast", [
        {"description": "Greek yogurt with berries and oats", "servings": 1.0, "kcal": 380, "protein_g": 28, "carbs_g": 45, "fat_g": 8},
    ]),
}

# (purpose, tier, model, input, output) — cached tokens are derived as a share of input.
CALL_MIX = (
    ("ask", "fast", "gpt-6-luna", 3400, 420),
    ("extract", "fast", "gpt-6-luna", 1200, 180),
    ("plan", "plan", "gpt-6.1-terra", 5200, 1800),
    ("review", "plan", "gpt-6.1-terra", 4600, 1200),
)

HISTORY_MODELS = (
    DailyBiometrics, FoodLog, RunLog, WorkoutLog, BodyMeasurement, SessionAdjustment,
    MealTemplate, WorkoutTemplate, LLMCall, Program, UserMemory,
)


def _reset_history(db: Session, user_id: str) -> None:
    """Delete this demo user's generated rows so re-seeding stays idempotent.

    Only ever called with an id from data/users.json — never a real profile.
    """
    for model in HISTORY_MODELS:
        db.execute(delete(model).where(model.user_id == user_id))
    db.flush()


def _logged_exercises(session_name: str, week: int, rng: random.Random) -> list[dict]:
    """Sets as performed, with progressive overload across weeks."""
    template = next((t for t in WEEK_TEMPLATE if t[0] == session_name), None)
    if template is None:
        return []
    out = []
    for name, sets, _reps, _guidance, rpe in template[3]:
        base = BASE_WEIGHT_KG[name]
        bodyweight = base == 0.0
        count = int(sets.split()[0]) if sets.split()[0].isdigit() else 3
        performed = []
        for i in range(count):
            reps = max(3, 8 - i // 2 + rng.randint(-1, 1)) if not bodyweight else max(6, 12 - i + rng.randint(-1, 1))
            weight = None if bodyweight else round(base + 2.5 * week + (0.0 if i < 2 else -2.5), 1)
            performed.append({
                "reps": reps,
                "weight_kg": weight,
                "rpe": min(10.0, round(float(rpe.split("-")[-1]) + 0.25 * i, 2)) if rpe else None,
                "rir": max(0, int(round(10 - (float(rpe.split("-")[-1]) + 0.25 * i)))) if rpe else None,
            })
        out.append({"name": name, "sets": performed})
    return out


def seed_demo_history(db: Session) -> int:
    """Fill the demo profiles with a month of plausible, entirely fictional data."""
    raw_users = json.loads(USERS_PATH.read_text())
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    for raw in raw_users:
        uid, goal = raw["user_id"], raw["goal"]
        shape = SHAPES.get(goal, SHAPES[DEFAULT_GOAL])
        rng = _rng(uid, "history")
        _reset_history(db, uid)

        for offset in range(HISTORY_DAYS - 1, -1, -1):
            day = today - timedelta(days=offset)
            upsert_daily_metrics(db, uid, day, source="seed", **day_metrics(uid, goal, offset))

        # Weekly weigh-ins, trending with the goal. Each *_trend is the change from the oldest
        # point to today, so the oldest value is today's minus the trend.
        for i, offset in enumerate((27, 20, 13, 6, 0)):
            frac = i / 4
            upsert_measurement(
                db, uid, today - timedelta(days=offset), source="seed",
                weight_kg=round(raw["weight_kg"] - shape["weight_trend"] * (1 - frac) + rng.uniform(-0.2, 0.2), 1),
                body_fat_pct=round(shape["bf"] - shape["bf_trend"] * (1 - frac), 1),
                waist_cm=round(shape["waist"] - shape["waist_trend"] * (1 - frac), 1),
                chest_cm=round(shape["chest"] + rng.uniform(-0.5, 0.5), 1),
                arm_cm=round(shape["arm"] + rng.uniform(-0.3, 0.3), 1),
                thigh_cm=round(shape["thigh"] + rng.uniform(-0.4, 0.4), 1),
            )

        for offset in range(MEAL_DAYS - 1, -1, -1):
            day = today - timedelta(days=offset)
            pool = MEALS.get(goal, MEALS[DEFAULT_GOAL])
            for meal, description, kcal, protein, carbs, fat, fiber in pool:
                if meal == "snack" and offset % 3:
                    continue
                scale = 1.0 + rng.uniform(-0.08, 0.08)
                add_food_log(
                    db, uid, day, meal, description, kcal=round(kcal * scale), source="template" if meal == "breakfast" else "manual",
                    protein_g=round(protein * scale, 1), carbs_g=round(carbs * scale, 1),
                    fat_g=round(fat * scale, 1), fiber_g=round(fiber * scale, 1),
                )

        for days_ago, km, minutes, hr, source in RUNS:
            day = today - timedelta(days=days_ago)
            if goal == "strength" and source == "assistant":
                continue
            add_run(db, uid, day, distance_km=km, duration_min=minutes, avg_hr_bpm=hr, source=source)

        for kind, key, value, source, pinned in MEMORIES.get(uid, ()):
            upsert_memory(db, uid, kind, key, value, source=source, pinned=pinned)

        name, meal, items = MEAL_TEMPLATES.get(uid, MEAL_TEMPLATES["u1"])
        upsert_meal_template(db, uid, name, meal, items)
        seed_workout_templates(db, uid)

        # Programs first: logged sessions link to the active training program.
        training = Program(
            user_id=uid, kind="training", status="active", version=3,
            name="Four-week strength block" if goal == "strength" else "Three-day recomposition week",
            summary=(
                f"{monday:%d %b}–{monday + timedelta(days=6):%d %b}: three strength days, one zone 2 "
                "session, one mixed day with intervals, two rest days. Loads progress 2.5 kg per week "
                "on the main lifts; deload if HRV drops more than 10% below baseline."
            ),
            payload={
                "weeks": [{
                    "focus": "Accumulation: add volume before intensity, keep zone 2 easy",
                    "days": [
                        {
                            "name": f"{(monday + timedelta(days=i)):%A} {(monday + timedelta(days=i)).day} "
                                    f"{(monday + timedelta(days=i)):%b} — day {i + 1}",
                            "focus": focus, "day_type": day_type, "cardio": cardio,
                            "exercises": [
                                {"name": n, "sets": int(s.split()[0]) if s.split()[0].isdigit() else 3,
                                 "reps": r, "weight_guidance": g, "rpe_target": rpe}
                                for n, s, r, g, rpe in exercises
                            ],
                        }
                        for i, (focus, day_type, cardio, exercises) in enumerate(WEEK_TEMPLATE)
                    ],
                }],
            },
            rationale=(
                "Recovery is trending up over the last four weeks and resting heart rate is down 3 bpm, "
                "so the block accumulates volume on the main lifts while keeping cardio in zone 2 to "
                "protect leg recovery."
            ),
            activated_at=datetime.now(UTC) - timedelta(days=today.weekday(), hours=6),
        )
        nutrition = Program(
            user_id=uid, kind="nutrition", status="active", version=2,
            name="Maintenance protein-first targets" if goal == "strength" else "Moderate deficit, protein first",
            summary=(
                f"{shape['intake']} kcal/day, {round(raw['weight_kg'] * (1.8 if goal == 'strength' else 2.0))} g protein, "
                f"{320 if goal == 'strength' else 150} g carbs, {75 if goal == 'strength' else 55} g fat, "
                "35 g fiber, sodium under 2300 mg. Shift carbs to training days."
            ),
            payload={
                "target_kcal": shape["intake"],
                "protein_g": round(raw["weight_kg"] * (1.8 if goal == "strength" else 2.0)),
                "carbs_g": 320 if goal == "strength" else 150,
                "fat_g": 75 if goal == "strength" else 55,
                "fiber_g": 35,
                "sodium_mg": 2300,
                "meals": [],
                "guidelines": [
                    "Put most carbs on training days and around the session.",
                    "Hit the protein target at every meal rather than in one large dose.",
                    "Weigh weekly as an average, not day to day.",
                ],
            },
            rationale="Intake over the last two weeks already sits close to target, so the plan tightens protein distribution rather than cutting calories further.",
            activated_at=datetime.now(UTC) - timedelta(days=today.weekday(), hours=5),
        )
        db.add_all([training, nutrition])
        db.flush()

        # Logged sessions on this week's and previous weeks' training days, never in the future.
        week_index = 0
        for week in range(-3, 1):
            for slot in (0, 2, 3):
                day = monday + timedelta(days=7 * week + slot)
                if day > today:
                    continue
                session_name = WEEK_TEMPLATE[slot][0]
                add_workout_row(
                    db, uid, day, training.id, session_name,
                    _logged_exercises(session_name, week_index, _rng(uid, day.isoformat())),
                    duration_min=55 + slot * 5,
                )
            week_index += 1

        for offset in range(USAGE_DAYS - 1, -1, -1):
            for i, (purpose, tier, model, inp, out) in enumerate(CALL_MIX):
                if purpose == "plan" and offset % 7 != 1:
                    continue
                if purpose == "review" and offset != 2:
                    continue
                jitter = _rng(uid, offset, i)
                input_tokens = int(inp * jitter.uniform(0.85, 1.15))
                db.add(LLMCall(
                    user_id=uid, model=model, tier=tier, purpose=purpose,
                    input_tokens=input_tokens,
                    cached_tokens=int(input_tokens * jitter.uniform(0.62, 0.86)),
                    output_tokens=int(out * jitter.uniform(0.8, 1.2)),
                    created_at=datetime.now(UTC) - timedelta(days=offset, hours=9 + i * 3),
                ))

    db.commit()
    return len(raw_users)


def add_workout_row(
    db: Session, user_id: str, day: date, program_id: int, session_name: str,
    exercises: list[dict], duration_min: int | None = None,
) -> WorkoutLog:
    row = WorkoutLog(
        user_id=user_id, date=day, program_id=program_id, session_name=session_name,
        exercises=exercises, duration_min=duration_min,
    )
    db.add(row)
    db.flush()
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Seed even when AUTH_ENABLED=true")
    parser.add_argument(
        "--rich", action="store_true",
        help="Also seed meals, runs, workouts, body measures, programs, memories and token usage",
    )
    args = parser.parse_args()
    if get_settings().auth_enabled and not args.force:
        parser.error("AUTH_ENABLED=true looks like a deployment; demo users do not belong there (use --force)")

    with get_sessionmaker()() as db:
        count = seed_demo_users(db)
        if args.rich:
            count = seed_demo_history(db)
    print(f"Seeded {count} demo users" + (" with full history" if args.rich else ""))


if __name__ == "__main__":
    main()
