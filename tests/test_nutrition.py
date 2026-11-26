"""Meal compute: FatSecret per-100g parsing, LLM estimate fallback, endpoint wiring."""

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.data_access.food import intake_window
from app.data_access.users import get_user_days
from app.nutrition import PER_100G_RE, MealItemQuery, resolve_items
from app.main import compute_meal, log_from_meal_template, log_meal, update_meal
from app.rag.generator import LLMReply, LLMUsage
from app.schemas import LogFromTemplateIn, MealComputeIn, MealIn, MealUpdate

DESC = "Per 100g - Calories: 389kcal | Fat: 7.20g | Carbs: 66.30g | Protein: 16.90g"


class FakeFS:
    def __init__(self, hits):
        self.hits = hits

    def search_foods(self, token, secret, query, max_results=5):
        return self.hits.get(query.strip().lower(), [])


class EstCoach:
    """Structured-call fake returning per-100g estimates for whatever foods are asked."""

    def __init__(self, table):
        self.table = table
        self.calls = 0

    def complete(self, stable, history, volatile, **kwargs):
        self.calls += 1
        names = [line.strip("- \n") for line in volatile.splitlines() if line.startswith("- ")]
        foods = [
            {
                "name": n,
                **{k: v for k, v in self.table[n.lower()].items()},
            }
            for n in names
        ]
        return LLMReply(text=json.dumps({"foods": foods}), usage=LLMUsage.zero("fake"))


WHEY = {
    "kcal_per_100g": 390, "protein_g_per_100g": 78, "carbs_g_per_100g": 4,
    "fat_g_per_100g": 3.5, "fiber_g_per_100g": 0,
}


def test_per100g_regex():
    m = PER_100G_RE.search(DESC)
    assert m is not None
    kcal, fat, carbs, protein = (float(g) for g in m.groups())
    assert (kcal, fat, carbs, protein) == (389.0, 7.2, 66.3, 16.9)
    assert PER_100G_RE.search("Per 1 serving (30g) - Calories: 120kcal | Fat: 1g | Carbs: 3g | Protein: 24g") is None


def test_resolve_mixes_fatsecret_and_one_estimate_call():
    fs = FakeFS({"oats": [SimpleNamespace(name="oats", description=DESC)]})
    coach = EstCoach({"whey": WHEY, "peanut butter": WHEY})
    items = [
        MealItemQuery("oats", 50),
        MealItemQuery("whey", 30),
        MealItemQuery("peanut butter", 30),
    ]
    out = resolve_items(items, fatsecret=fs, coach=coach)
    assert [r.name for r in out] == ["oats", "whey", "peanut butter"]  # order preserved
    assert out[0].source == "fatsecret"
    assert out[0].kcal == pytest.approx(194.5)
    assert out[0].protein_g == pytest.approx(8.45, abs=0.06)  # rounded to 1 decimal
    assert out[0].fiber_g is None  # search summary does not contain fiber
    assert coach.calls == 1  # both misses batched into ONE estimate call
    assert out[1].source == "estimate"
    assert out[1].kcal == pytest.approx(117.0)


def test_resolve_without_any_source_raises():
    with pytest.raises(ValueError, match="no nutrition source"):
        resolve_items([MealItemQuery("mystery", 100)])


def test_search_match_must_be_exact_and_duplicate_items_are_resolved():
    fs = FakeFS({"oats": [SimpleNamespace(name="oat cookies", description=DESC)]})
    coach = EstCoach({"oats": WHEY})
    result = resolve_items([MealItemQuery("oats", 50), MealItemQuery("oats", 100)], fatsecret=fs, coach=coach)
    assert [r.source for r in result] == ["estimate", "estimate"]
    assert [r.kcal for r in result] == [195, 390]


@pytest.mark.parametrize("value", [-1, float("nan"), 1001])
def test_invalid_model_nutrition_is_not_returned(value):
    coach = EstCoach({"whey": {**WHEY, "kcal_per_100g": value}})
    with pytest.raises(ValueError, match="invalid values"):
        resolve_items([MealItemQuery("whey", 100)], coach=coach)


def test_compute_endpoint_requires_resolvable_items(fakes):
    # The default FakeCoach returns '{"updates": []}' for structured calls — no food estimates,
    # so the endpoint must fail with a clear 422 rather than log garbage.
    r = fakes.client.post(
        "/users/u1/meals/compute",
        json={"items": [{"name": "yulaf", "grams": 50}]},
    )
    assert r.status_code == 422
    assert "yulaf" in r.json()["detail"]
    # validation still applies
    assert fakes.client.post("/users/u1/meals/compute", json={"items": []}).status_code == 422
    assert fakes.client.post("/users/u1/meals/compute", json={"items": [{"name": "x", "grams": 0}]}).status_code == 422


def test_compute_endpoint_does_not_copy_fatsecret_search_data(monkeypatch):
    seen = []
    def fake_resolve(items, **kwargs):
        seen.append(kwargs)
        return resolve_items(items, coach=EstCoach({"whey": WHEY}))
    monkeypatch.setattr("app.main.resolve_items", fake_resolve)
    db = SimpleNamespace(get=lambda *_: object(), commit=lambda: None)
    out = compute_meal(
        MealComputeIn(items=[{"name": "whey", "grams": 100}]),
        user_id="u1", db=db, current=None, coach=object(),
    )
    assert out.items[0].source == "estimate"
    assert "fatsecret" not in seen[0]


def test_update_meal_refreshes_totals_and_rejects_external_diary_entries(monkeypatch):
    day = date.today() + timedelta(days=1)
    row = SimpleNamespace(
        id=1,
        date=day,
        meal="breakfast",
        description="Oats",
        fatsecret_food_id=None,
        fatsecret_entry_id=None,
        servings=1,
        kcal=400,
        protein_g=20,
        carbs_g=None,
        fat_g=None,
        fiber_g=None,
        source="manual",
    )
    refreshed_days = []

    class FakeDB:
        commits = 0

        def commit(self):
            self.commits += 1

    db = FakeDB()
    monkeypatch.setattr("app.main.authorize", lambda *_: None)
    monkeypatch.setattr("app.main.get_food_log", lambda *_: row)
    monkeypatch.setattr("app.main.sync_day_calories", lambda _db, _user, changed_day: refreshed_days.append(changed_day))

    updated = update_meal(
        meal_id=1,
        meal=MealUpdate(meal="lunch", description="Chicken and rice", kcal=650, protein_g=45),
        user_id="u1",
        db=db,
        current=None,
    )
    assert updated.description == "Chicken and rice"
    assert updated.kcal == 650
    assert updated.date == day
    assert refreshed_days == [day]
    assert db.commits == 1

    row.fatsecret_entry_id = "777"
    with pytest.raises(HTTPException) as exc:
        update_meal(
            meal_id=1,
            meal=MealUpdate(meal="dinner", description="Changed", kcal=800),
            user_id="u1",
            db=db,
            current=None,
        )
    assert exc.value.status_code == 409
    assert row.description == "Chicken and rice"


def test_future_manual_and_template_logs_use_selected_day(monkeypatch):
    tomorrow = date.today() + timedelta(days=1)
    entries = []
    refreshed = []

    def add(_db, _user, day, **values):
        defaults = {
            "id": len(entries) + 1, "date": day, "fatsecret_food_id": None,
            "fatsecret_entry_id": None, "servings": 1, "protein_g": None, "carbs_g": None,
            "fat_g": None, "fiber_g": None,
        }
        row = SimpleNamespace(**(defaults | values))
        entries.append(row)
        return row

    template = SimpleNamespace(
        name="Oats", meal="breakfast",
        items=[{"description": "Oats", "kcal": 100, "protein_g": 5}],
    )
    db = SimpleNamespace(get=lambda *_: object(), commit=lambda: None)
    monkeypatch.setattr("app.main.authorize", lambda *_: None)
    monkeypatch.setattr("app.main.add_food_log", add)
    monkeypatch.setattr("app.main.find_meal_template", lambda *_: template)
    monkeypatch.setattr("app.main.sync_day_calories", lambda _db, _user, day: refreshed.append(day))

    manual = log_meal(
        MealIn(date=tomorrow, meal="lunch", description="Lunch", kcal=300),
        user_id="u1", db=db, current=None,
    )
    templated = log_from_meal_template(
        "Oats", LogFromTemplateIn(date=tomorrow, factor=2),
        user_id="u1", db=db, current=None,
    )
    assert manual.date == templated.date == tomorrow
    assert (manual.kcal, templated.kcal, templated.protein_g) == (300, 200, 10)
    assert refreshed == [tomorrow, tomorrow]


def test_future_biometrics_do_not_replace_current_profile_day(monkeypatch):
    today = date.today()
    tomorrow = today + timedelta(days=1)
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE daily_biometrics (
                user_id TEXT, date DATE, hrv_ms FLOAT, sleep_score INTEGER,
                sleep_minutes INTEGER, resting_hr_bpm INTEGER, calories_kcal INTEGER,
                steps INTEGER, active_kcal INTEGER, total_kcal INTEGER, sleep_stages JSON
            )
        """))
        conn.execute(text(
            "INSERT INTO daily_biometrics (user_id, date, hrv_ms, calories_kcal) "
            "VALUES ('u1', :day, :hrv, :kcal)"
        ), [
            {"day": today.isoformat(), "hrv": 55, "kcal": 200},
            {"day": tomorrow.isoformat(), "hrv": None, "kcal": 500},
        ])
    with Session(engine) as db:
        assert [(d.date, d.hrv_ms, d.calories_kcal) for d in get_user_days(db, "u1")] == [(today, 55, 200)]

        class Tomorrow(date):
            @classmethod
            def today(cls):
                return tomorrow

        monkeypatch.setattr("app.data_access.users.date", Tomorrow)
        assert [d.date for d in get_user_days(db, "u1")] == [today, tomorrow]


def test_future_meals_stay_out_of_recent_intake_until_their_day(monkeypatch):
    today = date.today()
    tomorrow = today + timedelta(days=1)
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE food_logs (
                user_id TEXT, date DATE, kcal INTEGER, protein_g FLOAT,
                carbs_g FLOAT, fat_g FLOAT, fiber_g FLOAT
            )
        """))
        conn.execute(text(
            "INSERT INTO food_logs (user_id, date, kcal) VALUES ('u1', :day, :kcal)"
        ), [{"day": today.isoformat(), "kcal": 200}, {"day": tomorrow.isoformat(), "kcal": 500}])
    with Session(engine) as db:
        assert [(d.date, d.kcal) for d in intake_window(db, "u1", 1)] == [(today, 200)]

        class Tomorrow(date):
            @classmethod
            def today(cls):
                return tomorrow

        monkeypatch.setattr("app.data_access.food.date", Tomorrow)
        assert [(d.date, d.kcal) for d in intake_window(db, "u1", 1)] == [(tomorrow, 500)]
