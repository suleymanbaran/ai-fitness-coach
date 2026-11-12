"""Function tools the coach can call during /ask, plus their server-side handlers.

Handlers close over the request's db session and user_id and return short strings that go
back to the model; a failing tool returns "error: ..." instead of raising, so one bad tool
call never takes down the request.
"""

import json
import re
from collections.abc import Callable
from datetime import date

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.data_access.biometrics import upsert_daily_metrics
from app.data_access.food import add_food_log, sync_day_calories
from app.data_access.memory import delete_memory, upsert_memory
from app.data_access.users import get_user, upsert_user
from app.integrations.common import IntegrationError
from app.integrations.service import Integrations, get_account
from app.programs.schemas import WorkoutIn
from app.programs.service import add_workout, get_active_program, today_session
from app.schemas import DayMetricsIn

MEALS = ("breakfast", "lunch", "dinner", "snack")

UPDATE_PROFILE_TOOL = {
    "type": "function",
    "name": "update_profile",
    "description": (
        "Store profile numbers the user states about themselves (age, weight, training goal). "
        "Only send fields they actually gave; omitted fields keep their stored value."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "age": {"type": "integer", "minimum": 10, "maximum": 110},
            "weight_kg": {"type": "number", "exclusiveMinimum": 20, "exclusiveMaximum": 400},
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
        "Log something the user ate. Always fill kcal (from search_food/get_food_nutrition when "
        "available, otherwise your best estimate — tell the user you estimated). If the food came "
        "from FatSecret, pass food_id and serving_id so the entry is also written to their diary."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"},
            "meal": {"type": "string", "enum": list(MEALS)},
            "description": {"type": "string", "description": "what was eaten, with portions, e.g. '2 eggs + 1 simit'"},
            "kcal": {"type": "integer"},
            "food_id": {"type": "string", "description": "FatSecret food_id when found via search_food"},
            "serving_id": {"type": "string", "description": "FatSecret serving_id from get_food_nutrition"},
            "servings": {"type": "number"},
            "protein_g": {"type": "number"},
            "carbs_g": {"type": "number"},
            "fat_g": {"type": "number"},
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


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:80]
    if not slug:
        raise ValueError("key is empty after normalization")
    return slug


def build_ask_tools(
    db: Session, user_id: str, settings: Settings, integrations: Integrations | None = None
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
        if "weight_kg" in fields and not 20 < float(fields["weight_kg"]) < 400:
            return "error: weight_kg must be between 20 and 400"
        upsert_user(
            db, user_id, name=user.name,
            age=int(fields["age"]) if "age" in fields else user.age,
            weight_kg=float(fields["weight_kg"]) if "weight_kg" in fields else user.weight_kg,
            goal=fields.get("goal", user.goal),
        )
        db.commit()
        return f"profile updated: {', '.join(f'{k}={v}' for k, v in fields.items())}"

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
        if day > date.today():
            return "error: cannot log a meal for a future date"
        try:
            kcal = int(round(float(args["kcal"])))
        except (KeyError, TypeError, ValueError):
            return "error: kcal is required (estimate it if the food was not in FatSecret)"
        if not 0 <= kcal <= 20000:
            return "error: kcal must be between 0 and 20000"

        def macro(name: str) -> float | None:
            value = args.get(name)
            return round(float(value), 1) if isinstance(value, (int, float)) and value >= 0 else None

        add_food_log(
            db, user_id, day, meal=args["meal"], description=str(args["description"]), kcal=kcal,
            source="assistant", fatsecret_food_id=args.get("food_id"),
            servings=float(args.get("servings") or 1.0),
            protein_g=macro("protein_g"), carbs_g=macro("carbs_g"), fat_g=macro("fat_g"),
        )
        total = sync_day_calories(db, user_id, day)
        db.commit()

        diary_note = ""
        if args.get("food_id") and args.get("serving_id"):
            # Best effort: the local log is the source of truth; the diary write is a bonus.
            try:
                client, token, secret = _fatsecret_diary()
                client.create_food_entry(
                    token, secret, food_id=str(args["food_id"]), serving_id=str(args["serving_id"]),
                    name=str(args["description"]), meal=args["meal"], day=day,
                    units=float(args.get("servings") or 1.0),
                )
                diary_note = "; also written to the FatSecret diary"
            except IntegrationError as exc:
                diary_note = f"; FatSecret diary write failed ({exc})"
        return f"logged {args['meal']} on {day}: {kcal} kcal (day total {total} kcal){diary_note}"

    def get_today_session(args: dict) -> str:
        program = get_active_program(db, user_id, "training")
        if program is None:
            return "no active training program; the user can ask you to generate one"
        return today_session(program, date.today())

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

    handlers = {
        "update_profile": update_profile,
        "save_memory": save_memory,
        "forget_memory": forget_memory,
        "log_day": log_day,
        "search_food": search_food,
        "get_food_nutrition": get_food_nutrition,
        "log_meal": log_meal,
        "get_today_session": get_today_session,
        "log_workout": log_workout,
    }
    tools = [
        UPDATE_PROFILE_TOOL, SAVE_MEMORY_TOOL, FORGET_MEMORY_TOOL, LOG_DAY_TOOL,
        SEARCH_FOOD_TOOL, GET_FOOD_NUTRITION_TOOL, LOG_MEAL_TOOL,
        GET_TODAY_SESSION_TOOL, LOG_WORKOUT_TOOL,
    ]
    if settings.enable_web_search:
        tools.append(WEB_SEARCH_TOOL)
    return tools, handlers
