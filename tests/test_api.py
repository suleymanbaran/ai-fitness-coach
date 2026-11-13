from datetime import date, timedelta

import httpx
from fastapi.testclient import TestClient
from openai import APIConnectionError
from qdrant_client import QdrantClient

from app.config import get_settings
from app.main import app, get_store
from app.rag.demo import DemoCoach
from app.rag.vector_store import ProtocolStore
from scripts.seed import day_metrics


def test_health(fakes):
    assert fakes.client.get("/health").json() == {"status": "ok"}


def test_ask_returns_answer_and_ranked_sources(fakes):
    r = fakes.client.post("/ask", json={"user_id": "u1", "question": "My HRV dropped, should I train legs?"})

    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "Fake answer [rec-hrv-autoregulation]"
    assert body["cached_tokens"] == 40
    assert len(body["sources"]) == 3
    scores = [s["score"] for s in body["sources"]]
    assert scores == sorted(scores, reverse=True)
    assert body["sources"][0]["protocol_id"] == "rec-hrv-autoregulation"


def test_prompt_splits_stable_prefix_from_volatile_context(fakes):
    fakes.client.post("/ask", json={"user_id": "u1", "question": "My HRV dropped, should I train legs?"})

    call = fakes.coach.answer_call
    # Stable prefix: rules + profile + memories block. Volatile: metrics + protocols + question.
    assert "long-term memory" in call.stable
    assert "User: Ali" in call.stable
    assert "<memories>" in call.stable
    # Seeded days are generated relative to today, so derive the expected value from the same
    # generator rather than freezing a number that goes stale.
    today_hrv = day_metrics("u1", "strength", 0)["hrv_ms"]
    assert f"HRV {today_hrv} ms vs " in call.volatile
    assert "compared with the previous 6-day baseline" in call.volatile
    assert "[rec-hrv-autoregulation]" in call.volatile
    assert call.volatile.endswith("Question: My HRV dropped, should I train legs?")
    # Today's metrics must NOT leak into the cached prefix or every request misses the cache.
    assert f"HRV {today_hrv}" not in call.stable
    assert call.kwargs["cache_key"] == "coach:u1"
    function_tools = {t["name"] for t in call.kwargs["tools"] if t["type"] == "function"}
    assert function_tools == {
        "update_profile", "log_measurement", "save_memory", "forget_memory", "log_day",
        "search_food", "get_food_nutrition", "log_meal", "list_meals", "delete_meal",
        "save_meal_template", "use_meal_template", "get_workout_template",
        "get_today_session", "log_workout", "log_run", "exercise_history", "strength_summary",
        "adjust_session", "revise_program", "create_weekly_plan",
    }
    assert {"type": "web_search"} in call.kwargs["tools"]


def test_category_filter_restricts_sources(fakes):
    r = fakes.client.post(
        "/ask", json={"user_id": "u2", "question": "How much protein do I need?", "category": "nutrition"}
    )

    assert r.status_code == 200
    assert {s["category"] for s in r.json()["sources"]} == {"nutrition"}


def test_unknown_user_returns_404(fakes):
    r = fakes.client.post("/ask", json={"user_id": "nobody", "question": "Hello coach"})
    assert r.status_code == 404


def test_invalid_input_returns_422(fakes):
    assert fakes.client.post("/ask", json={"user_id": "u1", "question": "hi"}).status_code == 422
    assert (
        fakes.client.post("/ask", json={"user_id": "u1", "question": "Hello", "category": "magic"}).status_code
        == 422
    )


def test_empty_knowledge_base_returns_503(fakes):
    app.dependency_overrides[get_store] = lambda: ProtocolStore(QdrantClient(":memory:"), "protocols")
    r = fakes.client.post("/ask", json={"user_id": "u1", "question": "Hello coach"})
    assert r.status_code == 503


def test_openai_failure_returns_502(fakes, monkeypatch):
    def fail(*args, **kwargs):
        raise APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))

    monkeypatch.setattr(fakes.coach, "complete", fail)
    r = fakes.client.post("/ask", json={"user_id": "u1", "question": "Hello coach"})
    assert r.status_code == 502
    assert r.json()["detail"] == "The AI service failed. Try again in a moment."


def test_workout_history_reaches_the_prompt(fakes):
    fakes.client.post("/users/u1/workouts", json={
        "session_name": "Upper A",
        "exercises": [{"name": "Bench press", "sets": [{"reps": 6, "weight_kg": 80, "rpe": 7}]}],
        "duration_min": 50,
    })
    fakes.client.post("/ask", json={"user_id": "u1", "question": "What should I train today?"})

    volatile = fakes.coach.answer_call.volatile
    assert "Workout history (last 14 days" in volatile
    assert "Upper A (50 min): Bench press (6 reps, @ 80 kg, rpe 7)" in volatile


def test_users_list_and_detail(fakes):
    users = fakes.client.get("/users").json()
    assert {u["user_id"] for u in users} == {"u1", "u2"}
    assert set(users[0]) == {"user_id", "name", "goal"}

    detail = fakes.client.get("/users/u1").json()
    assert detail["name"] == "Ali"
    assert len(detail["days"]) == 7
    assert fakes.client.get("/users/nobody").status_code == 404


def test_create_profile_and_log_days(fakes):
    r = fakes.client.put("/users/me", json={"name": "Suleyman", "age": 30, "weight_kg": 78.5, "goal": "strength"})
    assert r.status_code == 200
    assert r.json()["days"] == []
    assert "me" in {u["user_id"] for u in fakes.client.get("/users").json()}

    yesterday, today = date.today() - timedelta(days=1), date.today()
    fakes.client.put(f"/users/me/days/{yesterday}", json={"hrv_ms": 70, "sleep_score": 85, "resting_hr_bpm": 52})
    r = fakes.client.put(f"/users/me/days/{today}", json={"hrv_ms": 56})
    assert r.status_code == 200
    assert r.json()["hrv_ms"] == 56 and r.json()["hrv_baseline_ms"] == 70

    # A second, partial log for the same day fills in calories without erasing HRV.
    r = fakes.client.put(f"/users/me/days/{today}", json={"calories_kcal": 2400})
    assert (r.json()["hrv_ms"], r.json()["calories_kcal"]) == (56, 2400)

    fakes.client.post("/ask", json={"user_id": "me", "question": "Should I train hard today?"})
    volatile = fakes.coach.answer_call.volatile
    assert f"Today ({today}) compared with the previous 1-day baseline" in volatile
    assert "HRV 56.0 ms vs 70.0 ms (-20.0%)" in volatile


def test_profile_update_keeps_logged_days(fakes):
    r = fakes.client.put("/users/u1", json={"name": "Ali", "age": 28, "weight_kg": 83, "height_cm": 178, "goal": "muscle_gain"})
    assert r.json()["goal"] == "muscle_gain"
    assert r.json()["height_cm"] == 178
    assert len(r.json()["days"]) == 7


def test_update_profile_tool_stores_height(fakes):
    from app.config import Settings
    from app.rag.tools import build_ask_tools

    _, handlers = build_ask_tools(fakes.db, "u1", Settings(_env_file=None))
    assert handlers["update_profile"]({"height_cm": 181}) == "profile updated: height_cm=181"
    assert fakes.client.get("/users/u1").json()["height_cm"] == 181
    assert handlers["update_profile"]({"height_cm": 30}).startswith("error:")


def test_log_day_validation(fakes):
    today = date.today()
    assert fakes.client.put(f"/users/nobody/days/{today}", json={"hrv_ms": 50}).status_code == 404
    assert fakes.client.put(f"/users/u1/days/{today}", json={}).status_code == 422
    assert fakes.client.put(f"/users/u1/days/{today}", json={"sleep_score": 140}).status_code == 422
    assert fakes.client.put(f"/users/u1/days/{today + timedelta(days=1)}", json={"hrv_ms": 50}).status_code == 422
    assert fakes.client.put("/users/Bad%20Id", json={"name": "x"}).status_code == 422


def test_demo_mode_runs_full_app_without_openai(monkeypatch, db_session):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:  # `with` runs the lifespan, so startup is tested too
            r = client.post("/ask", json={"user_id": "u1", "question": "My HRV dropped, should I squat?"})
    finally:
        get_settings.cache_clear()

    assert r.status_code == 200
    assert r.json()["answer"].startswith("**Demo mode:**")
    assert f"HRV {day_metrics('u1', 'strength', 0)['hrv_ms']} ms vs " in r.json()["answer"]
    assert len(r.json()["sources"]) == 3


def test_demo_coach_echoes_prompt_parts():
    reply = DemoCoach().complete("rules and memories", [], "metrics and question")
    assert "```text\nrules and memories\n\nmetrics and question\n```" in reply.text
    assert reply.usage.input_tokens == 0
