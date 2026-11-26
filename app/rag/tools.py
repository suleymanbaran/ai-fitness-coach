"""Function tools the coach can call during /ask, plus their server-side handlers.

Handlers close over the request's db session and user_id and return short strings that go
back to the model; a failing tool returns "error: ..." instead of raising, so one bad tool
call never takes down the request.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.data_access.biometrics import upsert_daily_metrics
from app.data_access.food import add_food_log, delete_food_log, get_food_log, list_food_logs, sync_day_calories
from app.data_access.measurements import upsert_measurement
from app.data_access.memory import delete_memory, upsert_memory
from app.data_access.runs import add_run
from app.data_access.templates import (
    MealTemplateItem,
    find_meal_template,
    find_workout_template,
    list_meal_templates,
    list_workout_templates,
    meal_template_total,
    upsert_meal_template,
)
from app.data_access.users import get_user, upsert_user
from app.integrations.common import IntegrationError
from app.integrations.service import Integrations, get_account
from app.programs.schemas import WorkoutIn
from app.programs.service import (
    ProgramGenerationError,
    ProgramInputError,
    add_workout,
    exercise_history as exercise_progression,
    generate_weekly_plan,
    get_active_program,
    revise_program as revise_program_service,
    session_text,
    set_adjustment,
    strength_summary as strength_summary_service,
)
from app.rag.embeddings import Embedder
from app.rag.generator import CoachClient
from app.rag.vector_store import ProtocolStore
from app.schemas import DayMetricsIn, RunIn

MEALS = ("breakfast", "lunch", "dinner", "snack")

LOG_MEASUREMENT_TOOL = {
    "type": "function",
    "name": "log_measurement",
    "description": (
        "Log a body measurement the user reports (weight, body fat, waist/chest/arm/thigh). "
        "ALWAYS use this when the user states their weight — it feeds their measurement history "
        "and charts, and refreshes the profile weight automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"},
            "weight_kg": {"type": "number"},
            "body_fat_pct": {"type": "number"},
            "waist_cm": {"type": "number"},
            "chest_cm": {"type": "number"},
            "arm_cm": {"type": "number"},
            "thigh_cm": {"type": "number"},
            "notes": {"type": "string"},
        },
        "additionalProperties": False,
    },
}

UPDATE_PROFILE_TOOL = {
    "type": "function",
    "name": "update_profile",
    "description": (
        "Store static profile facts the user states (age, height in cm, training goal). "
        "For weight and body measurements use log_measurement instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "age": {"type": "integer", "minimum": 10, "maximum": 110},
            "height_cm": {"type": "number", "minimum": 80, "maximum": 250},
            "goal": {
                "type": "string",
                "enum": ["strength", "muscle_gain", "fat_loss", "endurance", "general_fitness"],
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}

SAVE_MEMORY_TOOL = {
    "type": "function",
    "name": "save_memory",
    "description": (
        "Store a durable fact or preference about the user (equipment available, disliked "
        "exercises or foods, injuries, schedule, dietary rules). Use a short snake_case key. "
        "Do not use for one-off daily context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["preference", "fact", "constraint"]},
            "key": {"type": "string", "description": "short snake_case identifier, e.g. disliked_exercises"},
            "value": {"type": "string", "description": "the fact in one short sentence"},
        },
        "required": ["kind", "key", "value"],
        "additionalProperties": False,
    },
}

FORGET_MEMORY_TOOL = {
    "type": "function",
    "name": "forget_memory",
    "description": "Delete a stored memory the user asked to forget or that is no longer true.",
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["preference", "fact", "constraint"]},
            "key": {"type": "string"},
        },
        "required": ["kind", "key"],
        "additionalProperties": False,
    },
}

LOG_DAY_TOOL = {
    "type": "function",
    "name": "log_day",
    "description": (
        "Log the user's biometrics for a day (today unless they say otherwise). "
        "Only send the metrics they actually reported."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "hrv_ms": {"type": "number"},
            "sleep_score": {"type": "integer"},
            "sleep_minutes": {"type": "integer"},
            "resting_hr_bpm": {"type": "integer"},
            "calories_kcal": {"type": "integer"},
            "steps": {"type": "integer"},
            "active_kcal": {"type": "integer", "description": "energy burned by activity"},
        },
        "required": ["date"],
        "additionalProperties": False,
    },
}


SEARCH_FOOD_TOOL = {
    "type": "function",
    "name": "search_food",
    "description": (
        "Search the FatSecret food database (free tier = US foods) for a food. Returns up to 5 "
        "matches with food_id, name and a per-serving nutrition description. Use it before logging "
        "a meal with real data; if the food is not found (common for non-US dishes), search the "
        "web for its calories and log your best estimate."
    ),
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "food name in English, e.g. 'chicken breast'"}},
        "required": ["query"],
        "additionalProperties": False,
    },
}

# Built-in Responses API tool: runs inside OpenAI, needs no handler. The fallback for foods
# FatSecret's US-only free database does not have ($10 per 1k searches).
WEB_SEARCH_TOOL = {"type": "web_search"}

GET_FOOD_NUTRITION_TOOL = {
    "type": "function",
    "name": "get_food_nutrition",
    "description": "Full nutrition per serving size for one food from the FatSecret database.",
    "parameters": {
        "type": "object",
        "properties": {"food_id": {"type": "string"}},
        "required": ["food_id"],
        "additionalProperties": False,
    },
}

LOG_MEAL_TOOL = {
    "type": "function",
    "name": "log_meal",
    "description": (
        "Log something the user ate or explicitly wants to log for a future date. Always fill kcal "
        "(from search_food/get_food_nutrition when "
        "available, otherwise your best estimate — tell the user you estimated). If the food came "
        "from FatSecret, pass food_id and serving_id so the entry is also written to their diary."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD, including future dates; defaults to today"},
            "meal": {"type": "string", "enum": list(MEALS)},
            "description": {"type": "string", "description": "what was eaten, with portions, e.g. '2 eggs + 1 simit'"},
            "kcal": {"type": "integer"},
            "food_id": {"type": "string", "description": "FatSecret food_id when found via search_food"},
            "serving_id": {"type": "string", "description": "FatSecret serving_id from get_food_nutrition"},
            "servings": {"type": "number"},
            "protein_g": {"type": "number"},
            "carbs_g": {"type": "number"},
            "fat_g": {"type": "number"},
            "fiber_g": {"type": "number"},
        },
        "required": ["meal", "description", "kcal"],
        "additionalProperties": False,
    },
}


GET_TODAY_SESSION_TOOL = {
    "type": "function",
    "name": "get_today_session",
    "description": "The training session planned for today from the user's active program, including exercises, sets and reps.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

LOG_WORKOUT_TOOL = {
    "type": "function",
    "name": "log_workout",
    "description": (
        "Log a completed workout the user reports. Include every exercise they mention with the "
        "sets they actually performed (reps, weight, RPE when stated)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"},
            "session_name": {"type": "string"},
            "exercises": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "sets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "reps": {"type": "integer"},
                                    "weight_kg": {"type": "number"},
                                    "rpe": {"type": "number"},
                                    "rir": {"type": "integer", "description": "reps in reserve (RPE = 10 - RIR)"},
                                },
                                "required": [],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["name", "sets"],
                    "additionalProperties": False,
                },
            },
            "duration_min": {"type": "integer"},
            "notes": {"type": "string"},
        },
        "required": ["session_name", "exercises"],
        "additionalProperties": False,
    },
}


LOG_RUN_TOOL = {
    "type": "function",
    "name": "log_run",
    "description": (
        "Log a completed run the user reports (distance, duration, average heart rate if given). "
        "Use this whenever the user mentions running/jogging a distance — it feeds their cardio "
        "capacity tracking (pace and HR trends, 5k estimate)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"},
            "distance_km": {"type": "number", "description": "distance in kilometers"},
            "duration_min": {"type": "number", "description": "total time in minutes (decimals ok, e.g. 27.5)"},
            "avg_hr_bpm": {"type": "integer", "description": "average heart rate, if the user knows it"},
            "notes": {"type": "string"},
        },
        "required": ["distance_km", "duration_min"],
        "additionalProperties": False,
    },
}


EXERCISE_HISTORY_TOOL = {
    "type": "function",
    "name": "exercise_history",
    "description": (
        "The user's logged performance history for one exercise (weights, reps, RPE, volume per "
        "session, last 90 days). Use it to answer questions like 'what did I bench last week?' and "
        "to pick today's loads from actual progression — the prompt only shows the last 14 days."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "exercise": {"type": "string", "description": "exercise name or part of it, e.g. 'bench'"},
            "days": {"type": "integer", "description": "lookback window, default 90"},
        },
        "required": ["exercise"],
        "additionalProperties": False,
    },
}


LIST_MEALS_TOOL = {
    "type": "function",
    "name": "list_meals",
    "description": "List the user's logged meals for a date (default today) with their ids, so one can be deleted or discussed.",
    "parameters": {
        "type": "object",
        "properties": {"date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"}},
        "additionalProperties": False,
    },
}

DELETE_MEAL_TOOL = {
    "type": "function",
    "name": "delete_meal",
    "description": (
        "Delete a logged meal by its id (get ids from list_meals). Recomputes the day's calorie "
        "total and also removes the meal from the user's FatSecret diary when it was synced there."
    ),
    "parameters": {
        "type": "object",
        "properties": {"meal_id": {"type": "integer"}},
        "required": ["meal_id"],
        "additionalProperties": False,
    },
}

ADJUST_SESSION_TOOL = {
    "type": "function",
    "name": "adjust_session",
    "description": (
        "Override the planned training for a specific date. The adjustment shows up in the user's "
        "Today card and /workout on that date. Use it proactively when today's intake or metrics "
        "clearly call for changing a coming day; an empty note restores the original plan. "
        "After a calorie surplus: add cardio and keep the strength session — do not prescribe rest. "
        "Reserve rest for when recovery metrics (HRV, resting HR, sleep) call for it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD, today or a future date"},
            "note": {"type": "string", "description": "what to do instead: modality, duration, intensity; empty string clears"},
            "reason": {"type": "string", "description": "short why, shown to the user"},
        },
        "required": ["date", "note"],
        "additionalProperties": False,
    },
}


SAVE_MEAL_TEMPLATE_TOOL = {
    "type": "function",
    "name": "save_meal_template",
    "description": (
        "Save a meal the user eats regularly as a reusable template (e.g. their usual breakfast). "
        "Each item needs a per-item kcal — look it up with search_food/web search or estimate. "
        "Later the user can log the whole meal with one sentence."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "short name the user will say, e.g. 'oat breakfast'"},
            "meal": {"type": "string", "enum": ["breakfast", "lunch", "dinner", "snack"]},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "item with portion, e.g. '50 g oats'"},
                        "servings": {"type": "number"},
                        "kcal": {"type": "integer", "description": "kcal for ONE serving of this item"},
                        "protein_g": {"type": "number"},
                        "carbs_g": {"type": "number"},
                        "fat_g": {"type": "number"},
                    },
                    "required": ["description", "kcal"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["name", "items"],
        "additionalProperties": False,
    },
}

USE_MEAL_TEMPLATE_TOOL = {
    "type": "function",
    "name": "use_meal_template",
    "description": (
        "Log one of the user's saved meal templates (see <templates> in the context) as a meal "
        "today or on a given date. `factor` scales portions: 0.5 = half, 1.5 = extra hungry."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "factor": {"type": "number", "description": "portion multiplier, default 1"},
            "date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
}

GET_WORKOUT_TEMPLATE_TOOL = {
    "type": "function",
    "name": "get_workout_template",
    "description": (
        "Show the user's workout templates (default exercises, sets, reps). Without a name it lists "
        "all of them; with a name it shows that template's exercises. Use when the user says e.g. "
        "'what am I doing on push day?' — then log the workout with log_workout as they complete it."
    ),
    "parameters": {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    },
}


STRENGTH_SUMMARY_TOOL = {
    "type": "function",
    "name": "strength_summary",
    "description": (
        "Weekly training volume (sets, volume load), top estimated 1RM (Epley) and the all-time PR "
        "for one exercise. Call it before prescribing loads (% of e1RM), judging whether weekly "
        "sets are in the hypertrophy range, or answering 'how much can I lift / am I progressing'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "exercise": {"type": "string", "description": "exercise name or part of it, e.g. 'bench'"},
            "weeks": {"type": "integer", "description": "weeks to aggregate, default 8"},
        },
        "required": ["exercise"],
        "additionalProperties": False,
    },
}


REVISE_PROGRAM_TOOL = {
    "type": "function",
    "name": "revise_program",
    "description": (
        "Revise the user's ACTIVE training or nutrition program when they report they cannot "
        "adhere to it or ask for a structural change (available days, equipment, disliked "
        "exercises/foods, pain, schedule). Creates and activates the next version; old versions "
        "are kept. For a one-off single-day change use adjust_session instead; also save durable "
        "preferences with save_memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["training", "nutrition"]},
            "feedback": {
                "type": "string",
                "description": "what does not work and what should change, in the user's words",
            },
        },
        "required": ["feedback"],
        "additionalProperties": False,
    },
}

CREATE_WEEKLY_PLAN_TOOL = {
    "type": "function",
    "name": "create_weekly_plan",
    "description": (
        "Design and activate a NEW full weekly plan (training + cardio + nutrition) with the plan "
        "model. A nutrition plan requires explicit energy inputs on the web Plan page; direct the "
        "user there rather than guessing them. It is saved to their web Plan page and calendar. Takes about a "
        "minute. For changes to the CURRENT plan use revise_program instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "brief": {
                "type": "string",
                "description": "the user's wishes and constraints for this plan (days available, goal, injuries, food rules), summarized in their words",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class PlanContext:
    """Plan-tier dependencies needed by the revise_program / create_weekly_plan tools inside /ask."""

    coach: CoachClient
    embedder: Embedder
    store: ProtocolStore


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:80]
    if not slug:
        raise ValueError("key is empty after normalization")
    return slug


def build_ask_tools(
    db: Session,
    user_id: str,
    settings: Settings,
    integrations: Integrations | None = None,
    plan_ctx: PlanContext | None = None,
) -> tuple[list[dict], dict[str, Callable[[dict], str]]]:
    def update_profile(args: dict) -> str:
        user = get_user(db, user_id)
        if user is None:
            return "error: no profile exists yet; ask the user to create one"
        fields = {k: v for k, v in args.items() if v is not None}
        if not fields:
            return "error: no profile fields provided"
        if "age" in fields and not 10 <= int(fields["age"]) <= 110:
            return "error: age must be between 10 and 110"
        if "height_cm" in fields and not 80 <= float(fields["height_cm"]) <= 250:
            return "error: height_cm must be between 80 and 250"
        upsert_user(
            db, user_id, name=user.name,
            age=int(fields["age"]) if "age" in fields else user.age,
            weight_kg=user.weight_kg,
            goal=fields.get("goal", user.goal),
            height_cm=float(fields["height_cm"]) if "height_cm" in fields else user.height_cm,
        )
        db.commit()
        return f"profile updated: {', '.join(f'{k}={v}' for k, v in fields.items())}"

    def log_measurement(args: dict) -> str:
        day_str = args.get("date") or date.today().isoformat()
        try:
            day = date.fromisoformat(day_str)
        except ValueError:
            return f"error: invalid date {day_str!r}, expected YYYY-MM-DD"
        if day > date.today():
            return "error: cannot log a measurement for a future date"
        fields = {
            k: args[k]
            for k in ("weight_kg", "body_fat_pct", "waist_cm", "chest_cm", "arm_cm", "thigh_cm")
            if args.get(k) is not None
        }
        if not fields and not args.get("notes"):
            return "error: no measurement values provided"
        upsert_measurement(db, user_id, day, source="assistant", notes=args.get("notes"), **fields)
        db.commit()
        logged = ", ".join(f"{k}={v}" for k, v in fields.items())
        return f"measurement saved for {day}: {logged}" if logged else f"note saved for {day}"

    def save_memory(args: dict) -> str:
        upsert_memory(
            db,
            user_id,
            kind=args["kind"],
            key=_slug(args["key"]),
            value=args["value"].strip()[:500],
            source="assistant",
            max_entries=settings.memory_max_entries,
        )
        db.commit()
        return f"saved memory {args['kind']}/{_slug(args['key'])}"

    def forget_memory(args: dict) -> str:
        deleted = delete_memory(db, user_id, args["kind"], _slug(args["key"]))
        db.commit()
        return f"deleted {args['kind']}/{_slug(args['key'])}" if deleted else f"no memory named {_slug(args['key'])}"

    def log_day(args: dict) -> str:
        try:
            day = date.fromisoformat(args["date"])
        except ValueError:
            return f"error: invalid date {args['date']!r}, expected YYYY-MM-DD"
        if day > date.today():
            return "error: cannot log metrics for a future date"
        try:
            metrics = DayMetricsIn(**{k: v for k, v in args.items() if k != "date"})
        except ValidationError as exc:
            return f"error: invalid metrics: {exc.errors()[0]['msg']}"
        upsert_daily_metrics(db, user_id, day, source="assistant", **metrics.model_dump(exclude_none=True))
        db.commit()
        logged = ", ".join(f"{k}={v}" for k, v in metrics.model_dump(exclude_none=True).items())
        return f"logged {day}: {logged}" if logged else f"error: no metrics provided for {day}"

    def _fatsecret_public():
        """(client, token, secret) for read-only food search/detail. A connected user account is
        optional here: on a free Basic key, search works with just the consumer key/secret.
        token/secret are None when the user has not connected their diary."""
        if integrations is None or integrations.fatsecret is None:
            raise IntegrationError("FatSecret is not configured on this server")
        account = get_account(db, user_id, "fatsecret")
        if account is None:
            return integrations.fatsecret, None, None
        return (
            integrations.fatsecret,
            integrations.cipher.decrypt(account.access_token_enc),
            integrations.cipher.decrypt(account.token_secret_enc),
        )

    def _fatsecret_diary():
        """Like _fatsecret_public but requires the user's connected account (diary read/write)."""
        client, token, secret = _fatsecret_public()
        if token is None:
            raise IntegrationError("the user has not connected their FatSecret account")
        return client, token, secret

    def search_food(args: dict) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "error: empty query"
        try:
            client, token, secret = _fatsecret_public()
            hits = client.search_foods(token, secret, query)
        except IntegrationError as exc:
            return (
                f"unavailable: {exc}. Search the web for the food's calories instead, "
                "then log your best estimate with log_meal."
            )
        if not hits:
            return (
                f"no results for {query!r} (the free database is US-only). Search the web for "
                "the food's calories, then log your best estimate with log_meal."
            )
        return json.dumps([vars(h) for h in hits])

    def get_food_nutrition(args: dict) -> str:
        try:
            client, token, secret = _fatsecret_public()
            food = client.get_food(token, secret, str(args["food_id"]))
        except (IntegrationError, KeyError) as exc:
            return f"unavailable: {exc}"
        return json.dumps({
            "food_id": food.food_id,
            "name": food.name,
            "brand": food.brand,
            "servings": [vars(s) for s in food.servings[:6]],
        })

    def log_meal(args: dict) -> str:
        day_str = args.get("date") or date.today().isoformat()
        try:
            day = date.fromisoformat(day_str)
        except ValueError:
            return f"error: invalid date {day_str!r}, expected YYYY-MM-DD"
        try:
            kcal = int(round(float(args["kcal"])))
        except (KeyError, TypeError, ValueError):
            return "error: kcal is required (estimate it if the food was not in FatSecret)"
        if not 0 <= kcal <= 20000:
            return "error: kcal must be between 0 and 20000"

        def macro(name: str) -> float | None:
            value = args.get(name)
            return round(float(value), 1) if isinstance(value, (int, float)) and value >= 0 else None

        row = add_food_log(
            db, user_id, day, meal=args["meal"], description=str(args["description"]), kcal=kcal,
            source="assistant", fatsecret_food_id=args.get("food_id"),
            servings=float(args.get("servings") or 1.0),
            protein_g=macro("protein_g"), carbs_g=macro("carbs_g"), fat_g=macro("fat_g"), fiber_g=macro("fiber_g"),
        )

        diary_note = ""
        if args.get("food_id") and args.get("serving_id"):
            # Best effort: the local log is the source of truth; the diary write is a bonus.
            try:
                client, token, secret = _fatsecret_diary()
                entry_id = client.create_food_entry(
                    token, secret, food_id=str(args["food_id"]), serving_id=str(args["serving_id"]),
                    name=str(args["description"]), meal=args["meal"], day=day,
                    units=float(args.get("servings") or 1.0),
                )
                row.fatsecret_entry_id = entry_id or None
                diary_note = "; also written to the FatSecret diary"
            except IntegrationError as exc:
                diary_note = f"; FatSecret diary write failed ({exc})"

        total = sync_day_calories(db, user_id, day)
        db.commit()
        return f"logged {args['meal']} on {day}: {kcal} kcal (day total {total} kcal){diary_note}"

    def list_meals(args: dict) -> str:
        day_str = args.get("date") or date.today().isoformat()
        try:
            day = date.fromisoformat(day_str)
        except ValueError:
            return f"error: invalid date {day_str!r}, expected YYYY-MM-DD"
        rows = list_food_logs(db, user_id, day, day)
        if not rows:
            return f"no meals logged for {day}"
        return json.dumps([{"id": r.id, "meal": r.meal, "description": r.description, "kcal": r.kcal} for r in rows])

    def delete_meal(args: dict) -> str:
        try:
            meal_id = int(args["meal_id"])
        except (KeyError, TypeError, ValueError):
            return "error: meal_id is required (get ids from list_meals)"
        row = get_food_log(db, user_id, meal_id)
        if row is None:
            return f"error: no meal with id {meal_id} for this user"
        day, entry_id, description = row.date, row.fatsecret_entry_id, row.description
        delete_food_log(db, user_id, meal_id)
        total = sync_day_calories(db, user_id, day)
        db.commit()

        diary_note = ""
        if entry_id:
            try:
                client, token, secret = _fatsecret_diary()
                client.delete_food_entry(token, secret, entry_id)
                diary_note = "; also deleted from the FatSecret diary"
            except IntegrationError as exc:
                diary_note = f"; could not delete from the FatSecret diary ({exc}) — remove it there manually"
        return f"deleted meal '{description}' from {day}; day total now {total} kcal{diary_note}"

    def save_meal_template(args: dict) -> str:
        name = str(args.get("name") or "").strip()
        raw_items = args.get("items") or []
        if not name or not raw_items:
            return "error: name and at least one item are required"
        try:
            items = [MealTemplateItem.model_validate(i).model_dump(exclude_none=True) for i in raw_items]
        except ValidationError as exc:
            return f"error: invalid items: {exc.errors()[0]['msg']}"
        meal = args.get("meal") if args.get("meal") in MEALS else "breakfast"
        out = upsert_meal_template(db, user_id, name, meal, items)
        db.commit()
        return f"saved meal template '{out.name}' ({out.total_kcal} kcal, {len(items)} items)"

    def use_meal_template(args: dict) -> str:
        name = str(args.get("name") or "").strip()
        template = find_meal_template(db, user_id, name) if name else None
        if template is None:
            available = [t.name for t in list_meal_templates(db, user_id)]
            return f"error: no meal template named {name!r}; available: {available or 'none'}"
        day_str = args.get("date") or date.today().isoformat()
        try:
            day = date.fromisoformat(day_str)
        except ValueError:
            return f"error: invalid date {day_str!r}, expected YYYY-MM-DD"
        try:
            factor = min(max(float(args.get("factor") or 1.0), 0.25), 10.0)
        except (TypeError, ValueError):
            factor = 1.0

        def scaled(key: str) -> float | None:
            values = [float(i[key]) * float(i.get("servings", 1)) for i in template.items if i.get(key) is not None]
            return round(sum(values) * factor, 1) if values else None

        kcal = round(meal_template_total(template.items) * factor)
        description = template.name + (f" (x{factor:g})" if factor != 1 else "")
        add_food_log(
            db, user_id, day, meal=template.meal, description=description, kcal=kcal, source="template",
            protein_g=scaled("protein_g"), carbs_g=scaled("carbs_g"), fat_g=scaled("fat_g"),
            fiber_g=scaled("fiber_g"),
        )
        total = sync_day_calories(db, user_id, day)
        db.commit()
        return f"logged '{description}' ({template.meal}) on {day}: {kcal} kcal (day total {total} kcal)"

    def get_workout_template(args: dict) -> str:
        name = str(args.get("name") or "").strip()
        if not name:
            templates = list_workout_templates(db, user_id)
            if not templates:
                return "no workout templates saved yet"
            return "Workout templates: " + ", ".join(f"{t.name} ({len(t.exercises)} exercises)" for t in templates)
        template = find_workout_template(db, user_id, name)
        if template is None:
            return f"error: no workout template named {name!r}"
        lines = [
            f"- {e['name']}: {e['sets']} x {e['reps']}"
            + (f" @ {float(e['weight_kg']):g} kg" if e.get("weight_kg") else "")
            + (f" (RPE {e['rpe_target']})" if e.get("rpe_target") else "")
            for e in template.exercises
        ]
        return f"{template.name}:\n" + "\n".join(lines)

    def strength_summary(args: dict) -> str:
        name = str(args.get("exercise", "")).strip()
        if not name:
            return "error: exercise name is required"
        weeks = min(max(int(args.get("weeks") or 8), 1), 52)
        s = strength_summary_service(db, user_id, name, weeks)
        if not s.weekly and s.pr_weight_kg is None:
            return f"no logged history for {name!r} in the last {weeks} weeks"
        lines = []
        if s.pr_weight_kg is not None:
            lines.append(
                f"{s.exercise}: PR {s.pr_weight_kg:g} kg x {s.pr_reps} ({s.pr_date}), "
                f"best est. 1RM {s.best_e1rm_kg} kg"
            )
        lines += [
            f"- week of {w.week_start}: {w.sets} sets, volume {w.volume_kg:g} kg"
            + (f", top e1RM {w.top_e1rm_kg:g} kg" if w.top_e1rm_kg is not None else "")
            for w in s.weekly
        ]
        return "\n".join(lines)

    def get_today_session(args: dict) -> str:
        return session_text(db, user_id, date.today())

    def adjust_session(args: dict) -> str:
        day_str = str(args.get("date") or "")
        try:
            day = date.fromisoformat(day_str)
        except ValueError:
            return f"error: invalid date {day_str!r}, expected YYYY-MM-DD"
        if day < date.today():
            return "error: can only adjust today or future dates"
        note = str(args.get("note") or "")
        exists = set_adjustment(db, user_id, day, note, reason=args.get("reason"))
        db.commit()
        if not exists:
            return f"cleared the adjustment for {day}; the original plan applies again"
        return f"adjusted {day}: {note.strip()[:120]}"

    def revise_program(args: dict) -> str:
        if plan_ctx is None:
            return "error: program revision is not available in this context"
        feedback = str(args.get("feedback") or "").strip()
        if not feedback:
            return "error: feedback is required"
        kind = args.get("kind")
        if kind not in ("training", "nutrition"):
            active_kinds = [k for k in ("training", "nutrition") if get_active_program(db, user_id, k) is not None]
            if len(active_kinds) == 1:
                kind = active_kinds[0]
            else:
                return "error: specify kind ('training' or 'nutrition')"
        program = get_active_program(db, user_id, kind)
        if program is None:
            return f"error: no active {kind} program to revise; generate one first"
        user = get_user(db, user_id)
        if user is None:
            return "error: no profile found"
        try:
            revised = revise_program_service(
                db, plan_ctx.coach, plan_ctx.embedder, plan_ctx.store, user, program, feedback, settings
            )
        except ProgramInputError as exc:
            return f"error: {exc}"
        except ProgramGenerationError:
            return "error: revision failed schema validation twice; tell the user to try again in a moment"
        return f"{kind} program revised to v{revised.version} and activated: {revised.summary[:240]}"

    def create_weekly_plan(args: dict) -> str:
        if plan_ctx is None:
            return "error: weekly planning is not available in this context"
        user = get_user(db, user_id)
        if user is None:
            return "error: no profile found"
        brief = str(args.get("brief") or "").strip() or None
        try:
            training, nutrition = generate_weekly_plan(
                db, plan_ctx.coach, plan_ctx.embedder, plan_ctx.store, user, brief, settings
            )
        except ProgramInputError as exc:
            return f"error: {exc}"
        except ProgramGenerationError:
            return "error: plan generation failed validation twice; tell the user to try again in a moment"
        return (
            f"weekly plan created and activated — training v{training.version}, "
            f"nutrition v{nutrition.version}; visible on the web Plan page and calendar. "
            f"Training: {training.summary[:200]} | Nutrition: {nutrition.summary[:200]}"
        )

    def log_workout(args: dict) -> str:
        day_str = args.get("date") or date.today().isoformat()
        try:
            day = date.fromisoformat(day_str)
        except ValueError:
            return f"error: invalid date {day_str!r}, expected YYYY-MM-DD"
        if day > date.today():
            return "error: cannot log a workout for a future date"
        try:
            workout = WorkoutIn(**args)
        except ValidationError as exc:
            return f"error: invalid workout: {exc.errors()[0]['msg']}"
        add_workout(
            db, user_id, day, workout.session_name,
            [e.model_dump(exclude_none=True) for e in workout.exercises],
            duration_min=workout.duration_min, notes=workout.notes,
        )
        db.commit()
        return f"logged workout {day}: {workout.session_name} ({len(workout.exercises)} exercises)"

    def log_run(args: dict) -> str:
        day_str = args.get("date") or date.today().isoformat()
        try:
            day = date.fromisoformat(day_str)
        except ValueError:
            return f"error: invalid date {day_str!r}, expected YYYY-MM-DD"
        if day > date.today():
            return "error: cannot log a run for a future date"
        try:
            run = RunIn(**args)
        except ValidationError as exc:
            return f"error: invalid run: {exc.errors()[0]['msg']}"
        add_run(
            db, user_id, day,
            distance_km=run.distance_km, duration_min=run.duration_min,
            avg_hr_bpm=run.avg_hr_bpm, notes=run.notes, source="assistant",
        )
        db.commit()
        pace = run.duration_min / run.distance_km
        pace_str = f"{int(pace)}:{round((pace - int(pace)) * 60):02d}"
        hr = f", avg HR {run.avg_hr_bpm}" if run.avg_hr_bpm else ""
        est = run.duration_min * (5.0 / run.distance_km) ** 1.06
        return (
            f"run saved for {day}: {run.distance_km:g} km in {run.duration_min:g} min "
            f"({pace_str}/km{hr}); Riegel 5k estimate {int(est)}:{round((est - int(est)) * 60):02d}"
        )

    def exercise_history(args: dict) -> str:
        name = str(args.get("exercise", "")).strip()
        if not name:
            return "error: exercise name is required"
        days = min(max(int(args.get("days") or 90), 1), 365)
        rows = exercise_progression(db, user_id, name, days)
        if not rows:
            return f"no logged history for {name!r} in the last {days} days"
        lines = []
        for r in rows:
            sets = " ".join(
                f"{s.reps if s.reps is not None else '?'}"
                + (f"@{s.weight_kg:g}" if s.weight_kg is not None else "")
                + (f" rpe{s.rpe:g}" if s.rpe is not None else "")
                for s in r.sets
            )
            best = f" | top {r.best_weight_kg:g} kg" if r.best_weight_kg is not None else ""
            lines.append(f"- {r.date} {r.session_name} — {r.exercise}: {sets} | volume {r.volume_kg:g} kg{best}")
        return "\n".join(lines)

    handlers = {
        "update_profile": update_profile,
        "log_measurement": log_measurement,
        "save_memory": save_memory,
        "forget_memory": forget_memory,
        "log_day": log_day,
        "search_food": search_food,
        "get_food_nutrition": get_food_nutrition,
        "log_meal": log_meal,
        "list_meals": list_meals,
        "delete_meal": delete_meal,
        "save_meal_template": save_meal_template,
        "use_meal_template": use_meal_template,
        "get_workout_template": get_workout_template,
        "get_today_session": get_today_session,
        "log_workout": log_workout,
        "log_run": log_run,
        "exercise_history": exercise_history,
        "strength_summary": strength_summary,
        "adjust_session": adjust_session,
        "revise_program": revise_program,
        "create_weekly_plan": create_weekly_plan,
    }
    tools = [
        UPDATE_PROFILE_TOOL, LOG_MEASUREMENT_TOOL, SAVE_MEMORY_TOOL, FORGET_MEMORY_TOOL, LOG_DAY_TOOL,
        SEARCH_FOOD_TOOL, GET_FOOD_NUTRITION_TOOL, LOG_MEAL_TOOL, LIST_MEALS_TOOL, DELETE_MEAL_TOOL,
        SAVE_MEAL_TEMPLATE_TOOL, USE_MEAL_TEMPLATE_TOOL, GET_WORKOUT_TEMPLATE_TOOL,
        GET_TODAY_SESSION_TOOL, LOG_WORKOUT_TOOL, LOG_RUN_TOOL, EXERCISE_HISTORY_TOOL,
        STRENGTH_SUMMARY_TOOL, ADJUST_SESSION_TOOL, REVISE_PROGRAM_TOOL, CREATE_WEEKLY_PLAN_TOOL,
    ]
    if settings.enable_web_search:
        tools.append(WEB_SEARCH_TOOL)
    return tools, handlers
