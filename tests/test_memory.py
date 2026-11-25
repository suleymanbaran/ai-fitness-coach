"""Memory, chat-history and extraction tests: API level with fakes, plus direct handler tests."""

import json
from datetime import date, timedelta

from sqlalchemy import func, select

from app.config import get_settings
from app.data_access.memory import list_memories, upsert_memory
from app.db.models import LLMCall, UserMemory
from app.main import get_coach
from app.rag.generator import LLMReply, LLMUsage
from app.rag.tools import build_ask_tools
from tests.fakes import FakeCoach


class ExtractingCoach(FakeCoach):
    """FakeCoach whose extraction call returns real memory updates."""

    def __init__(self, updates: list[dict]) -> None:
        super().__init__()
        self.updates_json = json.dumps({"updates": updates})

    def complete(self, stable, history, volatile, **kwargs) -> LLMReply:
        if kwargs.get("json_schema") is not None:
            return LLMReply(text=self.updates_json, usage=LLMUsage.zero("fake-model"))
        return super().complete(stable, history, volatile, **kwargs)


def test_memory_crud_flow(fakes):
    r = fakes.client.put(
        "/users/u1/memories",
        json={"kind": "preference", "key": "no_running", "value": "Hates running", "pinned": True},
    )
    assert r.status_code == 200
    entries = r.json()
    assert entries[0]["source"] == "user" and entries[0]["pinned"] is True

    # Upsert on the same (kind, key) updates in place.
    r = fakes.client.put("/users/u1/memories", json={"kind": "preference", "key": "no_running", "value": "Still hates running"})
    assert len(r.json()) == 1
    assert r.json()[0]["value"] == "Still hates running"

    assert fakes.client.delete("/users/u1/memories/preference/no_running").status_code == 204
    assert fakes.client.delete("/users/u1/memories/preference/no_running").status_code == 404
    assert fakes.client.get("/users/u1/memories").json() == []
    assert fakes.client.put("/users/nobody/memories", json={"kind": "fact", "key": "x", "value": "y"}).status_code == 404
    assert fakes.client.put("/users/u1/memories", json={"kind": "fact", "key": "Bad Key!", "value": "y"}).status_code == 422


def test_memories_reach_the_stable_prefix(fakes):
    fakes.client.put("/users/u1/memories", json={"kind": "constraint", "key": "bad_knee", "value": "No barbell squats"})
    fakes.client.post("/ask", json={"user_id": "u1", "question": "Plan my leg day"})

    assert "[constraint] bad_knee: No barbell squats" in fakes.coach.answer_call.stable


def test_ask_persists_history_and_shares_it_across_channels(fakes):
    fakes.client.post("/ask", json={"user_id": "u1", "question": "First question about HRV"})
    fakes.client.post("/ask", json={"user_id": "u1", "question": "Second question about sleep", "channel": "telegram"})

    messages = fakes.client.get("/users/u1/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert [m["channel"] for m in messages] == ["web", "web", "telegram", "telegram"]

    # The second call saw the first exchange as history.
    history = fakes.coach.calls[2].history
    assert history[0] == {"role": "user", "content": "First question about HRV"}
    assert history[1]["role"] == "assistant"
    assert fakes.client.get("/users/nobody/messages").status_code == 404


def test_llm_usage_is_recorded(fakes):
    fakes.client.post("/ask", json={"user_id": "u1", "question": "Should I train today?"})

    rows = fakes.db.execute(select(LLMCall.purpose, LLMCall.model, LLMCall.cached_tokens)).all()
    assert ("ask", "fake-model", 40) in rows
    assert ("extract", "fake-model", 0) in rows


def test_usage_endpoint_aggregates(fakes):
    fakes.client.post("/ask", json={"user_id": "u1", "question": "Should I train today?"})

    usage = fakes.client.get("/users/u1/usage?days=30").json()
    ask_rows = [r for r in usage["by_model_purpose"] if r["purpose"] == "ask"]
    assert ask_rows == [{
        "model": "fake-model", "purpose": "ask", "calls": 1,
        "input_tokens": 100, "cached_tokens": 40, "output_tokens": 10,
    }]
    assert usage["daily"][0]["cached_tokens"] >= 40
    assert fakes.client.get("/users/nobody/usage").status_code == 404


def test_clear_history(fakes):
    fakes.client.post("/ask", json={"user_id": "u1", "question": "Should I train today?"})
    assert len(fakes.client.get("/users/u1/messages").json()) == 2

    assert fakes.client.delete("/users/u1/messages").status_code == 204
    assert fakes.client.get("/users/u1/messages").json() == []
    assert fakes.client.delete("/users/nobody/messages").status_code == 404


def test_extraction_writes_assistant_memories(fakes):
    coach = ExtractingCoach([{"op": "set", "kind": "preference", "key": "loves_zone2", "value": "Enjoys zone 2 rides"}])
    fakes.client.app.dependency_overrides[get_coach] = lambda: coach

    fakes.client.post("/ask", json={"user_id": "u1", "question": "I love zone 2 rides"})

    entries = fakes.client.get("/users/u1/memories").json()
    assert [(m["key"], m["value"], m["source"]) for m in entries] == [("loves_zone2", "Enjoys zone 2 rides", "assistant")]


def test_extraction_cannot_overwrite_or_delete_user_memories(fakes):
    fakes.client.put("/users/u1/memories", json={"kind": "preference", "key": "no_running", "value": "User stated"})
    coach = ExtractingCoach([
        {"op": "set", "kind": "preference", "key": "no_running", "value": "Assistant overwrite"},
        {"op": "delete", "kind": "preference", "key": "no_running", "value": ""},
    ])
    fakes.client.app.dependency_overrides[get_coach] = lambda: coach

    fakes.client.post("/ask", json={"user_id": "u1", "question": "Whatever"})

    entries = fakes.client.get("/users/u1/memories").json()
    assert len(entries) == 1
    assert entries[0]["value"] == "User stated" and entries[0]["source"] == "user"


def test_extraction_garbage_is_swallowed(fakes):
    class GarbageCoach(FakeCoach):
        def complete(self, stable, history, volatile, **kwargs):
            if kwargs.get("json_schema") is not None:
                return LLMReply(text="not json at all", usage=LLMUsage.zero("fake-model"))
            return super().complete(stable, history, volatile, **kwargs)

    fakes.client.app.dependency_overrides[get_coach] = lambda: GarbageCoach()
    r = fakes.client.post("/ask", json={"user_id": "u1", "question": "Should I train today?"})
    assert r.status_code == 200


def test_eviction_keeps_memory_count_bounded(fakes):
    for i in range(5):
        upsert_memory(fakes.db, "u1", kind="fact", key=f"f{i}", value=f"v{i}", source="assistant", max_entries=3)
    fakes.db.commit()

    entries = list_memories(fakes.db, "u1")
    assert len(entries) == 3
    assert {m.key for m in entries} == {"f2", "f3", "f4"}  # oldest evicted first


def test_ask_tools_log_days_and_memories(fakes):
    settings = get_settings()
    tools, handlers = build_ask_tools(fakes.db, "u1", settings)
    assert {t["name"] for t in tools if t["type"] == "function"} == {
        "update_profile", "log_measurement", "save_memory", "forget_memory", "log_day",
        "search_food", "get_food_nutrition", "log_meal", "list_meals", "delete_meal",
        "save_meal_template", "use_meal_template", "get_workout_template",
        "get_today_session", "log_workout", "log_run", "exercise_history", "strength_summary",
        "adjust_session", "revise_program", "create_weekly_plan",
    }
    assert {"type": "web_search"} not in tools

    today = date.today()
    assert handlers["log_day"]({"date": str(today), "hrv_ms": 55, "sleep_score": 70}) == f"logged {today}: hrv_ms=55.0, sleep_score=70"
    row = fakes.db.scalar(
        select(func.count()).select_from(UserMemory).where(UserMemory.user_id == "u1")
    )
    assert row == 0

    assert handlers["log_day"]({"date": str(today + timedelta(days=1)), "hrv_ms": 55}).startswith("error:")
    assert handlers["log_day"]({"date": "yesterday", "hrv_ms": 55}).startswith("error: invalid date")
    assert handlers["log_day"]({"date": str(today)}).startswith("error:")  # no metrics
    assert handlers["log_day"]({"date": str(today), "sleep_score": 140}).startswith("error: invalid metrics")

    assert handlers["save_memory"]({"kind": "preference", "key": "Disliked Exercises!", "value": " No deadlifts "}) \
        == "saved memory preference/disliked_exercises"
    assert handlers["forget_memory"]({"kind": "preference", "key": "disliked_exercises"}) == "deleted preference/disliked_exercises"
    assert handlers["forget_memory"]({"kind": "preference", "key": "disliked_exercises"}) == "no memory named disliked_exercises"

    logged = fakes.client.get(f"/users/u1").json()["days"]
    assert any(d["date"] == str(today) and d["hrv_ms"] == 55 for d in logged)


def test_update_profile_and_measurement_tools(fakes):
    _, handlers = build_ask_tools(fakes.db, "u1", get_settings())

    assert handlers["update_profile"]({"age": 31}) == "profile updated: age=31"
    profile = fakes.client.get("/users/u1").json()
    assert (profile["age"], profile["goal"]) == (31, "strength")

    # Weight is NOT an update_profile field anymore; log_measurement owns it.
    assert handlers["update_profile"]({"goal": "fat_loss"}).startswith("profile updated")
    profile = fakes.client.get("/users/u1").json()
    assert (profile["age"], profile["weight_kg"], profile["goal"]) == (31, 82.0, "fat_loss")

    result = handlers["log_measurement"]({"weight_kg": 81.4})
    assert result == f"measurement saved for {date.today()}: weight_kg=81.4"
    # Profile weight follows the latest measurement, and history is stored.
    assert fakes.client.get("/users/u1").json()["weight_kg"] == 81.4
    measures = fakes.client.get("/users/u1/measurements?days=7").json()
    assert [(m["date"], m["weight_kg"], m["source"]) for m in measures] == [(str(date.today()), 81.4, "assistant")]

    assert handlers["log_measurement"]({}).startswith("error:")
    assert handlers["log_measurement"]({"date": "2099-01-01", "weight_kg": 80}).startswith("error:")
    assert handlers["update_profile"]({}).startswith("error:")
    assert handlers["update_profile"]({"age": 200}).startswith("error:")
    _, unknown_handlers = build_ask_tools(fakes.db, "nobody", get_settings())
    assert unknown_handlers["update_profile"]({"age": 30}).startswith("error:")
