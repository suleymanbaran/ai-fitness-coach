"""Program generation/activation/review, workout logging and the program tools."""

import json
from datetime import date, timedelta

import pytest

from app.config import Settings
from app.programs.service import _strict_schema, today_session
from app.programs.schemas import TrainingPlanOutput
from app.rag.tools import build_ask_tools
from tests.fakes import FakeCoach

TODAY = date.today()
ENERGY = {"equation": "male", "activity": "sedentary", "safety_confirmed": True}

VALID_TRAINING = {
    "name": "4-Day Strength",
    "summary": "Upper/lower split, 4 sessions/week, RPE 7-8, deload in week 4.",
    "rationale": "Grounded in [rec-hrv-autoregulation] and the user's knee constraint.",
    "plan": {
        "weeks": [
            {
                "focus": "base",
                "days": [
                    {"name": "Lower A", "focus": "squat", "exercises": [
                        {"name": "Leg press", "sets": 4, "reps": "8-10", "weight_guidance": "RPE 7", "rpe_target": "7"}]},
                    {"name": "Upper A", "focus": "press", "day_type": "mixed", "cardio": "20 min zone 2 row", "exercises": [
                        {"name": "Bench press", "sets": 4, "reps": "6", "weight_guidance": "RPE 7", "rpe_target": "7"}]},
                    {"name": "Lower B", "focus": "hinge", "exercises": [
                        {"name": "RDL", "sets": 3, "reps": "8", "weight_guidance": "RPE 7", "rpe_target": ""}]},
                    {"name": "Upper B", "focus": "pull", "exercises": [
                        {"name": "Row", "sets": 3, "reps": "10", "weight_guidance": "RPE 7", "rpe_target": ""}]},
                ],
            }
        ]
    },
}

VALID_NUTRITION = {
    "name": "Maintenance",
    "summary": "2163 kcal, 150 g protein, 3 meals + 1 snack.",
    "rationale": "Estimated maintenance.",
    "plan": {
        "target_kcal": 2163, "protein_g": 150, "carbs_g": 250, "fat_g": 62,
        "meals": [{"name": "Breakfast", "items": "Oats + whey", "kcal": 500}],
        "guidelines": ["2.5 L water", "Protein at every meal"],
    },
}


VALID_WEEKLY = {
    "training_name": "Weekly Strength + Cardio",
    "training_summary": "4 strength days + 2 zone-2 cardio sessions, RPE 7-8.",
    "nutrition_name": "Balanced maintenance",
    "nutrition_summary": "2163 kcal, 150 g protein, carbs around training.",
    "rationale": "Coherent plan per goal and metrics.",
    "training": VALID_TRAINING["plan"],
    "nutrition": VALID_NUTRITION["plan"],
}


class PlanCoach(FakeCoach):
    """Answers plan-tier calls with scripted program JSON / review markdown; fast calls normally."""

    def __init__(self, plan_payloads=None, assessment="## Assessment\nOn track.\n## Adjustments\n- Keep going."):
        super().__init__()
        self.plan_payloads = list(plan_payloads) if plan_payloads is not None else None
        self.assessment = assessment

    def complete(self, stable, history, volatile, **kwargs):
        from types import SimpleNamespace

        from app.rag.generator import LLMReply, LLMUsage
        self.calls.append(SimpleNamespace(stable=stable, history=list(history), volatile=volatile, kwargs=kwargs))
        if kwargs.get("json_schema") is not None:
            if self.plan_payloads is not None:
                payload = self.plan_payloads.pop(0)
            else:
                schema_name = str(kwargs.get("schema_name"))
                if schema_name == "weekly_plan":
                    payload = json.dumps(VALID_WEEKLY)
                elif "training" in schema_name:
                    payload = json.dumps(VALID_TRAINING)
                else:
                    payload = json.dumps(VALID_NUTRITION)
            return LLMReply(text=payload, usage=LLMUsage.zero("fake-plan"))
        if kwargs.get("tier") == "plan":
            return LLMReply(text=self.assessment, usage=LLMUsage.zero("fake-plan"))
        return LLMReply(text=self.answer, usage=self.usage)


@pytest.fixture
def plan_app(fakes):
    from app.main import get_coach
    coach = PlanCoach()
    fakes.client.app.dependency_overrides[get_coach] = lambda: coach
    fakes.coach = coach
    yield fakes


def generate(client, kind="training", brief=None):
    body = {"kind": kind}
    if kind == "nutrition":
        body["energy"] = ENERGY
    if brief:
        body["brief"] = brief
    return client.post("/users/u1/programs/generate", json=body)


def test_generate_stores_validated_draft(plan_app):
    r = generate(plan_app.client)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "draft" and body["version"] == 1 and body["kind"] == "training"
    assert body["name"] == "4-Day Strength"
    assert len(body["payload"]["weeks"][0]["days"]) == 4

    # Versions increment per kind.
    assert generate(plan_app.client).json()["version"] == 2
    assert generate(plan_app.client, kind="nutrition", brief="cut").json()["version"] == 1


def test_plan_tier_and_schema_are_used(plan_app):
    generate(plan_app.client)
    call = plan_app.coach.calls[0]
    assert call.kwargs["tier"] == "plan" and call.kwargs["effort"] == "medium"
    assert call.kwargs["schema_name"] == "training_program"
    assert "design a training program" in call.volatile
    assert "<protocols>" in call.volatile
    assert "You design safe, evidence-informed" in call.stable


def test_invalid_json_retries_once_then_502(fakes):
    from app.main import get_coach
    coach = PlanCoach(plan_payloads=["not json", '{"name": 1}'])
    fakes.client.app.dependency_overrides[get_coach] = lambda: coach

    r = generate(fakes.client)
    assert r.status_code == 502
    # Both attempts hit the model; the second carries the validation error.
    assert len(coach.calls) == 2
    assert "failed validation" in coach.calls[1].volatile


def test_activate_archives_previous_and_reaches_the_prefix(plan_app):
    client = plan_app.client
    first = generate(client).json()
    second = generate(client).json()

    r = client.post(f"/users/u1/programs/{first['id']}/activate")
    assert r.json()["status"] == "active"
    r = client.post(f"/users/u1/programs/{second['id']}/activate")
    assert r.json()["status"] == "active" and r.json()["version"] == 2

    statuses = {p["id"]: p["status"] for p in client.get("/users/u1/programs").json()}
    assert statuses == {first["id"]: "archived", second["id"]: "active"}

    client.post("/ask", json={"user_id": "u1", "question": "What should I do today?"})
    stable = plan_app.coach.answer_call.stable
    assert "<active_program>" in stable
    assert "Training program v2 — 4-Day Strength" in stable


def test_review_returns_assessment_and_can_regenerate(plan_app):
    client = plan_app.client
    program = generate(client).json()
    client.post(f"/users/u1/programs/{program['id']}/activate")

    r = client.post(f"/users/u1/programs/{program['id']}/review")
    assert r.status_code == 200
    assert r.json()["assessment"].startswith("## Assessment")
    assert r.json()["regenerated_version"] is None

    r = client.post(f"/users/u1/programs/{program['id']}/review?regenerate=true")
    assert r.json()["regenerated_version"] == 2
    programs = client.get("/users/u1/programs").json()
    assert {(p["version"], p["status"]) for p in programs} == {(1, "active"), (2, "draft")}

    review_calls = [c for c in plan_app.coach.calls if c.kwargs.get("tier") == "plan" and c.kwargs.get("json_schema") is None]
    assert "Sessions planned through week" in review_calls[0].volatile


def test_workout_endpoints(plan_app):
    client = plan_app.client
    program = generate(client).json()
    client.post(f"/users/u1/programs/{program['id']}/activate")

    r = client.post("/users/u1/workouts", json={
        "session_name": "Lower A",
        "exercises": [{"name": "Leg press", "sets": [{"reps": 10, "weight_kg": 120, "rpe": 7}, {"reps": 9, "weight_kg": 120}]}],
        "duration_min": 55,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["program_id"] == program["id"]
    assert body["exercises"][0]["sets"][0] == {"reps": 10, "weight_kg": 120.0, "rpe": 7.0, "rir": None}
    assert str(body["date"]) == str(TODAY)

    workouts = client.get("/users/u1/workouts?days=7").json()
    assert len(workouts) == 1

    assert client.post("/users/u1/workouts", json={"session_name": "x", "exercises": [], "date": "2099-01-01"}).status_code == 422
    assert client.post("/users/u1/workouts", json={"session_name": "x", "exercises": []}).status_code == 422
    assert client.delete(f"/users/u1/workouts/{body['id']}").status_code == 204
    assert client.delete(f"/users/u1/workouts/{body['id']}").status_code == 404


def test_program_tools(plan_app):
    client = plan_app.client
    program = generate(client).json()
    assert build_ask_tools(plan_app.db, "u1", Settings(_env_file=None))[1]["get_today_session"]({}) \
        == "No active training program. Ask the coach to generate one."
    client.post(f"/users/u1/programs/{program['id']}/activate")

    _, handlers = build_ask_tools(plan_app.db, "u1", Settings(_env_file=None))
    session = handlers["get_today_session"]({})
    assert session.startswith("Week 1 (base)")
    # 4 sessions map to Mon-Thu; the tool serves whatever today's weekday slot is, or a rest day.
    assert ("Lower A" in session) or ("Upper" in session) or ("Lower B" in session) or ("rest day" in session)

    result = handlers["log_workout"]({
        "session_name": "Upper A",
        "exercises": [{"name": "Bench press", "sets": [{"reps": 6, "weight_kg": 80}]}],
        "duration_min": 50,
    })
    assert result == f"logged workout {TODAY}: Upper A (1 exercises)"
    assert handlers["log_workout"]({"session_name": "x", "exercises": [{"name": "", "sets": []}]}).startswith("error:")
    assert handlers["log_workout"]({"session_name": "x", "exercises": [{"name": "y", "sets": [{}]}], "date": "2099-01-01"}).startswith("error:")

    workouts = client.get("/users/u1/workouts").json()
    assert [w["session_name"] for w in workouts] == ["Upper A"]


def test_today_session_weeks_and_rest_days(plan_app):
    from datetime import timedelta

    from app.db.models import Program as ProgramRow
    client = plan_app.client
    program = generate(client).json()
    client.post(f"/users/u1/programs/{program['id']}/activate")
    row = plan_app.db.get(ProgramRow, program["id"])

    assert today_session(row, TODAY).startswith("Week 1 (base):")
    saturday = TODAY + timedelta(days=(5 - TODAY.weekday()) % 7 or 7)
    assert "rest day" in today_session(row, saturday)  # only 4 planned day slots: weekends rest
    assert today_session(row, TODAY + timedelta(days=70)).startswith("Week 1")  # clamped to the last week


def test_exercise_history_endpoint_and_tool(plan_app):
    client = plan_app.client
    client.post("/users/u1/workouts", json={
        "date": str(TODAY),
        "session_name": "Push A",
        "exercises": [{"name": "Bench press", "sets": [
            {"reps": 5, "weight_kg": 80, "rpe": 7}, {"reps": 5, "weight_kg": 80, "rpe": 8}]}],
    })
    client.post("/users/u1/workouts", json={
        "session_name": "Push B",
        "exercises": [
            {"name": "Incline bench press", "sets": [{"reps": 8, "weight_kg": 60}]},
            {"name": "Row", "sets": [{"reps": 10, "weight_kg": 40}]},
        ],
    })

    names = client.get("/users/u1/exercises").json()
    assert names == ["Bench press", "Incline bench press", "Row"]

    # Substring, case-insensitive: both bench variations match.
    history = client.get("/users/u1/exercises/history", params={"name": "bench"}).json()
    assert [(h["exercise"], h["best_weight_kg"], h["total_reps"], h["volume_kg"]) for h in history] == [
        ("Bench press", 80.0, 10, 800.0),
        ("Incline bench press", 60.0, 8, 480.0),
    ]
    assert history[0]["sets"][1] == {"reps": 5, "weight_kg": 80.0, "rpe": 8.0, "rir": None}

    # The LLM tool renders the same data as compact text lines.
    from app.config import Settings
    _, handlers = build_ask_tools(plan_app.db, "u1", Settings(_env_file=None))
    text = handlers["exercise_history"]({"exercise": "Bench press"})
    assert f"- {TODAY} Push A — Bench press: 5@80 rpe7 5@80 rpe8 | volume 800 kg | top 80 kg" in text
    assert handlers["exercise_history"]({"exercise": "deadlift"}).startswith("no logged history")
    assert handlers["exercise_history"]({"exercise": ""}).startswith("error:")

    assert client.get("/users/nobody/exercises").status_code == 404
    assert client.get("/users/u1/exercises/history").status_code == 422  # name is required


def test_weekly_plan_generates_and_activates_both(plan_app):
    client = plan_app.client
    r = client.post("/users/u1/plans/weekly", json={"brief": "4 days/week, fat loss", "energy": ENERGY})
    assert r.status_code == 200
    body = r.json()
    assert body["training"]["kind"] == "training" and body["training"]["status"] == "active"
    assert body["nutrition"]["kind"] == "nutrition" and body["nutrition"]["status"] == "active"
    assert body["training"]["version"] == 1 and body["nutrition"]["version"] == 1
    assert body["nutrition"]["payload"]["target_kcal"] == 2163
    assert body["nutrition"]["payload"]["energy_calculation"]["method"] == "mifflin_st_jeor_maintenance"

    # The weekly task instructions and both protocol categories reached the model.
    call = plan_app.coach.calls[0]
    assert call.kwargs["schema_name"] == "weekly_plan"
    assert "coherent weekly plan" in call.volatile
    assert "4 days/week, fat loss" in call.volatile

    # Both summaries land in the cached stable prefix of subsequent chats.
    client.post("/ask", json={"user_id": "u1", "question": "What is my plan?"})
    stable = plan_app.coach.answer_call.stable
    assert "Weekly Strength + Cardio" in stable
    assert "Estimated maintenance" in stable


def test_revise_endpoint_creates_and_activates_next_version(plan_app):
    client = plan_app.client
    program = generate(client).json()
    client.post(f"/users/u1/programs/{program['id']}/activate")

    r = client.post(f"/users/u1/programs/{program['id']}/revise", json={"feedback": "Mondays no longer work, move to Saturday"})
    assert r.status_code == 200
    assert r.json()["version"] == 2 and r.json()["status"] == "active"

    statuses = {p["version"]: p["status"] for p in client.get("/users/u1/programs").json() if p["kind"] == "training"}
    assert statuses == {1: "archived", 2: "active"}

    # The revision call carries the current plan and the feedback.
    revise_call = [c for c in plan_app.coach.calls if c.kwargs.get("json_schema") is not None][-1]
    assert "Mondays no longer work" in revise_call.volatile
    assert "4-Day Strength" in revise_call.volatile


def test_revise_program_tool(plan_app):
    from app.config import Settings
    from app.rag.tools import PlanContext, build_ask_tools
    from tests.fakes import FakeEmbedder

    client = plan_app.client
    program = generate(client).json()
    client.post(f"/users/u1/programs/{program['id']}/activate")

    ctx = PlanContext(coach=plan_app.coach, embedder=FakeEmbedder(), store=plan_app.store)
    _, handlers = build_ask_tools(plan_app.db, "u1", Settings(_env_file=None), None, plan_ctx=ctx)

    result = handlers["revise_program"]({"kind": "training", "feedback": "no barbell work, knee hurts"})
    assert result.startswith("training program revised to v2 and activated")

    # kind inference when only one program is active
    result = handlers["revise_program"]({"feedback": "make it 3 days"})
    assert "revised to v3" in result

    # Without plan context the tool degrades gracefully.
    _, no_ctx = build_ask_tools(plan_app.db, "u1", Settings(_env_file=None))
    assert no_ctx["revise_program"]({"feedback": "x"}).startswith("error: program revision is not available")


def test_today_session_renders_day_type_and_cardio(plan_app):
    from datetime import datetime, timedelta, UTC

    from app.db.models import Program as ProgramRow
    from app.programs.service import today_session

    this_monday = TODAY - timedelta(days=TODAY.weekday())
    program = ProgramRow(
        user_id="u1", kind="training", status="active", version=1, name="t", summary="s",
        payload=VALID_TRAINING["plan"], activated_at=datetime(this_monday.year, this_monday.month, this_monday.day, tzinfo=UTC),
    )
    # Slot 1 (Tuesday) = "Upper A" with day_type mixed + cardio
    tuesday = this_monday + timedelta(days=1)
    text = today_session(program, tuesday)
    assert "Upper A — press [mixed]" in text
    assert "Cardio: 20 min zone 2 row" in text
    assert "Bench press: 4 x 6" in text


def test_strength_summary_weekly_volume_and_pr(plan_app):
    from app.config import Settings
    from app.rag.tools import build_ask_tools

    client = plan_app.client
    client.post("/users/u1/workouts", json={
        "date": str(TODAY - timedelta(days=7)),
        "session_name": "Push A",
        "exercises": [{"name": "Bench press", "sets": [
            {"reps": 5, "weight_kg": 75, "rir": 3}, {"reps": 5, "weight_kg": 75, "rir": 2}]}],
    })
    client.post("/users/u1/workouts", json={
        "session_name": "Push B",
        "exercises": [{"name": "Bench press", "sets": [
            {"reps": 5, "weight_kg": 80, "rpe": 8}, {"reps": 4, "weight_kg": 80, "rir": 1}]}],
    })

    history = client.get("/users/u1/exercises/history", params={"name": "bench"}).json()
    # Epley: 80 x (1 + 5/30) = 93.3 for the best set of the second session
    assert history[1]["est_1rm_kg"] == 93.3
    assert history[0]["sets"][0]["rir"] == 3

    strength = client.get("/users/u1/exercises/strength", params={"name": "bench"}).json()
    assert strength["pr_weight_kg"] == 80.0 and strength["pr_reps"] == 5
    assert strength["best_e1rm_kg"] == 93.3
    assert len(strength["weekly"]) == 2  # two different ISO weeks
    assert strength["weekly"][0]["sets"] == 2 and strength["weekly"][0]["volume_kg"] == 750.0

    _, handlers = build_ask_tools(plan_app.db, "u1", Settings(_env_file=None))
    text = handlers["strength_summary"]({"exercise": "bench"})
    assert "PR 80 kg x 5" in text and "best est. 1RM 93.3 kg" in text
    assert "2 sets, volume 750 kg" in text
    assert handlers["strength_summary"]({"exercise": "squat"}).startswith("no logged history")


def test_adjust_session_overrides_the_planned_day(plan_app):
    from datetime import timedelta

    from app.config import Settings
    from app.programs.service import session_text
    from app.rag.tools import build_ask_tools

    client = plan_app.client
    program = generate(client).json()
    client.post(f"/users/u1/programs/{program['id']}/activate")
    _, handlers = build_ask_tools(plan_app.db, "u1", Settings(_env_file=None))

    tomorrow = TODAY + timedelta(days=1)
    result = handlers["adjust_session"]({
        "date": tomorrow.isoformat(), "note": "30 min zone 2 bike, easy pace", "reason": "big calorie surplus today",
    })
    assert result == f"adjusted {tomorrow}: 30 min zone 2 bike, easy pace"
    assert handlers["adjust_session"]({"date": "2020-01-01", "note": "x"}).startswith("error: can only adjust")

    text = session_text(plan_app.db, "u1", tomorrow)
    assert "COACH ADJUSTMENT" in text and "zone 2" in text and "big calorie surplus today" in text

    # An empty note clears the adjustment; the original plan applies again.
    assert handlers["adjust_session"]({"date": tomorrow.isoformat(), "note": ""}).startswith("cleared")
    assert "COACH ADJUSTMENT" not in session_text(plan_app.db, "u1", tomorrow)


def test_strict_schema_marks_every_object():
    schema = _strict_schema(TrainingPlanOutput.model_json_schema())

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node["additionalProperties"] is False
                assert sorted(node["required"]) == sorted(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)


def test_create_weekly_plan_tool(plan_app):
    from app.config import Settings
    from app.rag.tools import PlanContext, build_ask_tools
    from tests.fakes import FakeEmbedder

    ctx = PlanContext(coach=plan_app.coach, embedder=FakeEmbedder(), store=plan_app.store)
    _, handlers = build_ask_tools(plan_app.db, "u1", Settings(_env_file=None), None, plan_ctx=ctx)
    result = handlers["create_weekly_plan"]({"brief": "4 days, knee-friendly"})
    assert "energy estimate form" in result

    active = plan_app.client.get("/users/u1/programs?status=active").json()
    assert not active

    _, no_ctx = build_ask_tools(plan_app.db, "u1", Settings(_env_file=None))
    assert no_ctx["create_weekly_plan"]({}).startswith("error: weekly planning is not available")


def test_today_session_matches_named_dates():
    from datetime import date as _date

    from app.db.models import Program
    from app.programs.service import today_session

    def day(name, focus, day_type, cardio="", exercises=None):
        return {"name": name, "focus": focus, "day_type": day_type, "cardio": cardio,
                "exercises": exercises or []}

    bench = [{"name": "Bench press", "sets": 3, "reps": "8", "weight_guidance": "moderate"}]
    days = [
        day("Perşembe 13 Kasım — 1. gün", "Push", "strength", exercises=bench),
        day("Cuma 14 Kasım — 2. gün", "Cardio", "cardio", cardio="30 min zone 2 row"),
        day("Cumartesi 15 Kasım — 3. gün", "Rest", "rest"),
        day("Pazar 16 Kasım — 4. gün", "Pull", "strength"),
        day("Pazartesi 17 Kasım — 5. gün", "Rest", "rest"),
        day("Salı 18 Kasım — 6. gün", "Legs", "strength"),
        day("Çarşamba 19 Kasım — 7. gün", "Upper", "mixed"),
    ]
    program = Program(
        user_id="u1", kind="training", status="active", version=1,
        name="dated", summary="s", payload={"weeks": [{"focus": "test", "days": days}]},
    )
    # Thursday Nov 13 must hit day 1 (weekday-slot fallback would wrongly pick days[3]).
    assert "Push" in today_session(program, _date(2026, 11, 13))
    assert "Bench press" in today_session(program, _date(2026, 11, 13))
    assert "30 min zone 2" in today_session(program, _date(2026, 11, 14))
    assert "does not cover" in today_session(program, _date(2026, 11, 25))
