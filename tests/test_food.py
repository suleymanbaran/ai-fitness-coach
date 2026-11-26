"""FatSecret food search/detail/diary-write, the meals endpoints and the food tool handlers."""

import json
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.db.models import DailyBiometrics, FoodLog, IntegrationAccount
from app.integrations.common import IntegrationError, ReauthRequired, TokenCipher
from app.integrations.fatsecret import (
    FatSecretClient,
    parse_food_detail,
    parse_food_entry_id,
    parse_food_search,
)
from app.integrations.service import Integrations
from app.main import app, get_integrations
from app.rag.tools import build_ask_tools
from tests.test_integrations import FakeFatSecret

TODAY = date.today()


def client_with(fake: FakeFatSecret, premier: bool = False) -> FatSecretClient:
    return FatSecretClient(
        "ck", "cs", http=httpx.Client(transport=httpx.MockTransport(fake.handler)), premier=premier
    )


# ---------- parsing ----------


def test_parse_food_search_handles_single_object_and_lists():
    one = parse_food_search({"foods": {"food": {"food_id": "1", "food_name": "Apple", "food_description": "52kcal"}}})
    assert [(h.food_id, h.name) for h in one] == [("1", "Apple")]

    many = parse_food_search({"foods": {"food": [{"food_id": "1"}, {"food_id": "2"}]}})
    assert [h.food_id for h in many] == ["1", "2"]
    assert parse_food_search({"foods": None}) == []


def test_parse_food_search_raises_on_error_body():
    with pytest.raises(ReauthRequired):
        parse_food_search({"error": {"code": 9, "message": "invalid token"}})
    with pytest.raises(IntegrationError):
        parse_food_search({"error": {"code": 30, "message": "premier only"}})


def test_parse_food_detail_reads_servings():
    detail = parse_food_detail({"food": {
        "food_id": "41963", "food_name": "Cheeseburger", "brand_name": "McDonald's",
        "servings": {"serving": {
            "serving_id": "50321", "serving_description": "1 serving", "calories": "300",
            "carbohydrate": "32.0", "protein": "15.0", "fat": "13.0",
            "metric_serving_amount": "150.000", "metric_serving_unit": "g",
        }},
    }})
    assert detail.name == "Cheeseburger" and detail.brand == "McDonald's"
    serving = detail.servings[0]
    assert (serving.calories, serving.protein_g, serving.carbohydrate_g, serving.fat_g) == (300, 15.0, 32.0, 13.0)
    assert (serving.metric_amount, serving.metric_unit) == (150.0, "g")


def test_parse_food_entry_id_shapes():
    assert parse_food_entry_id({"food_entry_id": {"value": "777"}}) == "777"
    assert parse_food_entry_id({"food_entry_id": "777"}) == "777"


# ---------- client ----------


def test_search_uses_v1_on_basic_and_v6_on_premier():
    basic_fake, premier_fake = FakeFatSecret(), FakeFatSecret()

    hits = client_with(basic_fake).search_foods("acc-tok", "acc-sec", "cheeseburger", max_results=5)
    client_with(premier_fake, premier=True).search_foods("acc-tok", "acc-sec", "cheeseburger")

    assert basic_fake.search_paths == ["/rest/foods/search/v1"]
    assert premier_fake.search_paths == ["/rest/foods/search/v6"]
    assert basic_fake.search_queries == ["cheeseburger"]
    assert [(h.food_id, h.name) for h in hits] == [("41963", "Cheeseburger")]


def test_get_food_and_create_entry_sign_and_map_meals():
    fake = FakeFatSecret()
    client = client_with(fake)

    detail = client.get_food("acc-tok", "acc-sec", "41963")
    assert detail.servings[0].serving_id == "50321"

    entry_id = client.create_food_entry(
        "acc-tok", "acc-sec", food_id="41963", serving_id="50321",
        name="Cheeseburger", meal="snack", day=TODAY, units=1.5,
    )
    created = fake.created_entries[0]
    assert entry_id == "777"
    assert created["meal"] == "other"  # FatSecret has no "snack" slot
    assert created["number_of_units"] == "1.5"
    assert int(created["date"]) == (TODAY - date(1970, 1, 1)).days


# ---------- meals endpoints ----------


def test_meal_endpoints_log_and_total_the_day(fakes):
    r = fakes.client.post("/users/u1/meals", json={"meal": "breakfast", "description": "2 eggs + simit", "kcal": 550, "protein_g": 24})
    assert r.status_code == 200
    meal_id = r.json()["id"]
    assert r.json()["source"] == "manual"

    fakes.client.post("/users/u1/meals", json={"meal": "lunch", "description": "Chicken wrap", "kcal": 620, "carbs_g": 70})

    meals = fakes.client.get("/users/u1/meals").json()
    assert [m["description"] for m in meals] == ["2 eggs + simit", "Chicken wrap"]

    # The day's calories_kcal tracks the itemized logs.
    day_row = fakes.db.query(DailyBiometrics).filter_by(user_id="u1", date=TODAY).one()
    assert (day_row.calories_kcal, day_row.source) == (1170, "assistant")

    intake = fakes.client.get("/users/u1/intake?days=7").json()
    assert intake[-1] == {"date": str(TODAY), "kcal": 1170, "protein_g": 24.0, "carbs_g": 70.0, "fat_g": None, "fiber_g": None, "entries": 2}

    assert fakes.client.delete(f"/users/u1/meals/{meal_id}").status_code == 204
    assert fakes.client.delete(f"/users/u1/meals/{meal_id}").status_code == 404

    assert fakes.client.post("/users/u1/meals", json={"meal": "dinner", "description": "x", "kcal": 100, "date": str(date(2020, 1, 1))}).status_code == 200
    assert fakes.client.post("/users/nobody/meals", json={"meal": "dinner", "description": "x", "kcal": 100}).status_code == 404


def test_future_meals_and_templates_do_not_change_today(fakes):
    client = fakes.client
    tomorrow = TODAY + timedelta(days=1)
    today_meal = client.post("/users/u1/meals", json={
        "date": str(TODAY), "meal": "breakfast", "description": "Today", "kcal": 200,
    })
    assert today_meal.status_code == 200
    today_profile = client.get("/users/u1").json()["days"]
    today_intake = client.get("/users/u1/intake?days=1").json()

    template = {"name": "Oats", "meal": "breakfast", "items": [
        {"description": "Oats", "kcal": 100, "servings": 1, "protein_g": 5},
    ]}
    assert client.put("/users/u1/meal-templates", json=template).status_code == 200
    manual = client.post("/users/u1/meals", json={
        "date": str(tomorrow), "meal": "lunch", "description": "Tomorrow", "kcal": 300,
    })
    templated = client.post("/users/u1/meal-templates/Oats/log", json={"date": str(tomorrow), "factor": 2})
    assert manual.status_code == templated.status_code == 200
    assert manual.json()["date"] == templated.json()["date"] == str(tomorrow)
    assert [row["kcal"] for row in client.get(f"/users/u1/meals?day={tomorrow}").json()] == [300, 200]
    assert fakes.db.query(DailyBiometrics).filter_by(user_id="u1", date=tomorrow).one().calories_kcal == 500
    assert client.get("/users/u1/intake?days=1").json() == today_intake
    assert client.get("/users/u1").json()["days"] == today_profile

    meal_id = manual.json()["id"]
    assert client.put(f"/users/u1/meals/{meal_id}", json={
        "meal": "dinner", "description": "Updated", "kcal": 400,
    }).status_code == 200
    assert fakes.db.query(DailyBiometrics).filter_by(user_id="u1", date=tomorrow).one().calories_kcal == 600
    assert client.delete(f"/users/u1/meals/{meal_id}").status_code == 204
    assert fakes.db.query(DailyBiometrics).filter_by(user_id="u1", date=tomorrow).one().calories_kcal == 200
    assert client.get("/users/u1/intake?days=1").json() == today_intake


def test_meal_update_preserves_day_and_refreshes_daily_totals(fakes):
    created = fakes.client.post("/users/u1/meals", json={
        "meal": "breakfast", "description": "Oats", "kcal": 400, "protein_g": 20,
    }).json()

    updated = fakes.client.put(f"/users/u1/meals/{created['id']}", json={
        "meal": "lunch", "description": "Chicken and rice", "kcal": 650, "protein_g": 45,
    })
    assert updated.status_code == 200
    assert updated.json()["date"] == str(TODAY)
    assert updated.json()["description"] == "Chicken and rice"
    assert updated.json()["protein_g"] == 45
    assert fakes.client.get("/users/u1/intake?days=1").json()[0]["kcal"] == 650

    # Omitted optional macros are preserved rather than silently erased.
    updated = fakes.client.put(f"/users/u1/meals/{created['id']}", json={
        "meal": "dinner", "description": "Dinner", "kcal": 700,
    })
    assert updated.status_code == 200
    assert updated.json()["protein_g"] == 45
    assert fakes.client.get("/users/u1/intake?days=1").json()[0]["kcal"] == 700

    row = fakes.db.get(FoodLog, created["id"])
    row.fatsecret_entry_id = "777"
    fakes.db.commit()
    blocked = fakes.client.put(f"/users/u1/meals/{created['id']}", json={
        "meal": "dinner", "description": "Changed", "kcal": 800,
    })
    assert blocked.status_code == 409
    assert fakes.db.get(FoodLog, created["id"]).description == "Dinner"


def test_intake_appears_in_the_prompt(fakes):
    fakes.client.post("/users/u1/meals", json={"meal": "breakfast", "description": "Oats", "kcal": 400, "protein_g": 12})
    fakes.client.post("/ask", json={"user_id": "u1", "question": "How is my diet today?"})

    assert "Today's logged intake so far: 400 kcal across 1 entries (protein 12 g)" in fakes.coach.answer_call.volatile


# ---------- food tools ----------


@pytest.fixture
def fatsecret_env(fakes):
    """u1 with a connected FatSecret account, wired through MockTransport."""
    fake = FakeFatSecret()
    cipher = TokenCipher(Fernet.generate_key().decode())
    integrations = Integrations(
        cipher=cipher,
        google=None,
        fatsecret=FatSecretClient("ck", "cs", http=httpx.Client(transport=httpx.MockTransport(fake.handler))),
        public_api_url="http://testserver",
    )
    fakes.db.add(IntegrationAccount(
        user_id="u1", provider="fatsecret",
        access_token_enc=cipher.encrypt("acc-tok"), token_secret_enc=cipher.encrypt("acc-sec"),
    ))
    fakes.db.commit()
    app.dependency_overrides[get_integrations] = lambda: integrations
    yield SimpleNamespace(fakes=fakes, fake=fake, integrations=integrations)
    app.dependency_overrides.pop(get_integrations, None)


def test_food_tools_happy_path(fatsecret_env):
    fakes, fake = fatsecret_env.fakes, fatsecret_env.fake
    tools, handlers = build_ask_tools(fakes.db, "u1", Settings(_env_file=None), fatsecret_env.integrations)
    assert {t["name"] for t in tools if t["type"] == "function"} == {
        "update_profile", "log_measurement", "save_memory", "forget_memory", "log_day",
        "search_food", "get_food_nutrition", "log_meal", "list_meals", "delete_meal",
        "save_meal_template", "use_meal_template", "get_workout_template",
        "get_today_session", "log_workout", "log_run", "exercise_history", "strength_summary",
        "adjust_session", "revise_program", "create_weekly_plan",
    }

    hits = json.loads(handlers["search_food"]({"query": "cheeseburger"}))
    assert hits[0]["food_id"] == "41963"

    detail = json.loads(handlers["get_food_nutrition"]({"food_id": "41963"}))
    assert detail["servings"][0]["calories"] == 300

    result = handlers["log_meal"]({
        "meal": "lunch", "description": "Cheeseburger", "kcal": 300,
        "food_id": "41963", "serving_id": "50321", "protein_g": 15, "carbs_g": 32, "fat_g": 13,
    })
    assert result == f"logged lunch on {TODAY}: 300 kcal (day total 300 kcal); also written to the FatSecret diary"
    assert fake.created_entries[0]["food_id"] == "41963"

    # A second meal adds up; the daily calories follow.
    handlers["log_meal"]({"meal": "snack", "description": "Apple", "kcal": 80})
    day_row = fakes.db.query(DailyBiometrics).filter_by(user_id="u1", date=TODAY).one()
    assert day_row.calories_kcal == 380


def test_search_works_app_only_without_connected_account(fakes):
    """A free Basic consumer key is enough for public food search — no diary OAuth needed."""
    fake = FakeFatSecret()
    integrations = Integrations(
        cipher=TokenCipher(Fernet.generate_key().decode()),
        google=None,
        fatsecret=FatSecretClient("ck", "cs", http=httpx.Client(transport=httpx.MockTransport(fake.handler))),
        public_api_url="http://testserver",
    )
    _, handlers = build_ask_tools(fakes.db, "u1", Settings(_env_file=None), integrations)

    hits = json.loads(handlers["search_food"]({"query": "apple"}))
    assert hits[0]["food_id"] == "41963"
    assert fake.app_only_paths == ["/rest/foods/search/v1"]

    detail = json.loads(handlers["get_food_nutrition"]({"food_id": "41963"}))
    assert detail["servings"][0]["calories"] == 300

    # Diary write-through needs the user's account: it must fail soft while the local log succeeds.
    result = handlers["log_meal"]({
        "meal": "snack", "description": "Apple", "kcal": 80, "food_id": "41963", "serving_id": "50321",
    })
    assert "diary write failed" in result and "day total 80 kcal" in result


def test_list_and_delete_meal_tools(fatsecret_env):
    fakes, fake = fatsecret_env.fakes, fatsecret_env.fake
    _, handlers = build_ask_tools(fakes.db, "u1", Settings(_env_file=None), fatsecret_env.integrations)

    handlers["log_meal"]({
        "meal": "lunch", "description": "Cheeseburger", "kcal": 300, "food_id": "41963", "serving_id": "50321",
    })
    listing = json.loads(handlers["list_meals"]({}))
    assert [(m["description"], m["kcal"]) for m in listing] == [("Cheeseburger", 300)]
    meal_id = listing[0]["id"]

    result = handlers["delete_meal"]({"meal_id": meal_id})
    assert result == (
        f"deleted meal 'Cheeseburger' from {TODAY}; day total now 0 kcal; also deleted from the FatSecret diary"
    )
    assert fake.deleted_entries == ["777"]  # the diary entry created by log_meal
    assert handlers["list_meals"]({}) == f"no meals logged for {TODAY}"
    assert handlers["delete_meal"]({"meal_id": meal_id}).startswith("error: no meal")
    assert handlers["delete_meal"]({}).startswith("error: meal_id")


def test_food_tools_degrade_without_fatsecret(fakes):
    tools, handlers = build_ask_tools(fakes.db, "u1", Settings(_env_file=None), None)

    assert handlers["search_food"]({"query": "apple"}).startswith("unavailable: FatSecret is not configured")
    assert handlers["get_food_nutrition"]({"food_id": "1"}).startswith("unavailable:")

    # log_meal still works locally, just without the diary write.
    result = handlers["log_meal"]({"meal": "dinner", "description": "Homemade soup", "kcal": 350.6})
    assert result == f"logged dinner on {TODAY}: 351 kcal (day total 351 kcal)"

    assert handlers["log_meal"]({"meal": "dinner", "description": "x"}).startswith("error: kcal is required")
    assert handlers["log_meal"]({"meal": "dinner", "description": "x", "kcal": 99999}).startswith("error: kcal must be")
    tomorrow = TODAY + timedelta(days=1)
    result = handlers["log_meal"]({"meal": "dinner", "description": "x", "kcal": 1, "date": str(tomorrow)})
    assert result == f"logged dinner on {tomorrow}: 1 kcal (day total 1 kcal)"
    assert handlers["log_meal"]({"meal": "dinner", "description": "x", "kcal": 1, "date": "not-a-date"}).startswith("error:")


def test_diary_write_failure_never_blocks_the_local_log(fatsecret_env, monkeypatch):
    def broken(*args, **kwargs):
        raise IntegrationError("FatSecret is down")

    monkeypatch.setattr(fatsecret_env.integrations.fatsecret, "create_food_entry", broken)
    _, handlers = build_ask_tools(
        fatsecret_env.fakes.db, "u1", Settings(_env_file=None), fatsecret_env.integrations
    )

    result = handlers["log_meal"]({
        "meal": "lunch", "description": "Cheeseburger", "kcal": 300, "food_id": "41963", "serving_id": "50321",
    })
    assert "FatSecret diary write failed" in result
    assert "day total 300 kcal" in result


def test_future_meal_attempts_fatsecret_write_for_selected_day(fatsecret_env):
    tomorrow = TODAY + timedelta(days=1)
    _, handlers = build_ask_tools(
        fatsecret_env.fakes.db, "u1", Settings(_env_file=None), fatsecret_env.integrations
    )
    result = handlers["log_meal"]({
        "date": str(tomorrow), "meal": "lunch", "description": "Cheeseburger", "kcal": 300,
        "food_id": "41963", "serving_id": "50321",
    })
    assert f"logged lunch on {tomorrow}" in result
    assert "also written to the FatSecret diary" in result
    assert int(fatsecret_env.fake.created_entries[-1]["date"]) == (tomorrow - date(1970, 1, 1)).days
