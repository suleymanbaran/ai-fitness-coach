"""Meal & workout templates: API endpoints, LLM tools, scaling, fiber, and prompt injection."""

import json
from datetime import date

from app.config import Settings, get_settings
from app.rag.tools import build_ask_tools

TODAY = date.today()

OAT_BREAKFAST = {
    "name": "Oat breakfast",
    "meal": "breakfast",
    "items": [
        {"description": "50 g oats", "servings": 1, "kcal": 190, "protein_g": 6.5, "carbs_g": 33, "fat_g": 3.5, "fiber_g": 5},
        {"description": "1 scoop whey", "servings": 1, "kcal": 120, "protein_g": 24, "carbs_g": 3, "fat_g": 1.5},
        {"description": "1 banana", "servings": 1, "kcal": 105, "protein_g": 1.3, "carbs_g": 27, "fat_g": 0.4, "fiber_g": 3.1},
    ],
}


def test_meal_template_crud_and_scaled_log(fakes):
    client = fakes.client

    r = client.put("/users/u1/meal-templates", json=OAT_BREAKFAST)
    assert r.status_code == 200
    body = r.json()
    assert body["total_kcal"] == 415  # 190 + 120 + 105
    assert len(body["items"]) == 3

    # Upsert on the same name updates in place.
    edited = {**OAT_BREAKFAST, "items": OAT_BREAKFAST["items"][:2]}
    r = client.put("/users/u1/meal-templates", json=edited)
    assert r.json()["total_kcal"] == 310
    assert len(client.get("/users/u1/meal-templates").json()) == 1

    # Log with factor 1.5: everything scales, day total follows, fiber included.
    r = client.post("/users/u1/meal-templates/Oat%20breakfast/log", json={"factor": 1.5})
    assert r.status_code == 200
    meal = r.json()
    assert meal["description"] == "Oat breakfast (x1.5)"
    assert meal["kcal"] == 465  # round(310 * 1.5)
    assert meal["protein_g"] == 45.8  # (6.5+24) * 1.5
    assert meal["fiber_g"] == 7.5  # 5 * 1.5
    assert meal["source"] == "template"

    intake = client.get("/users/u1/intake?days=1").json()
    assert intake[-1]["kcal"] == 465 and intake[-1]["fiber_g"] == 7.5

    assert client.delete("/users/u1/meal-templates/Oat%20breakfast").status_code == 204
    assert client.delete("/users/u1/meal-templates/Oat%20breakfast").status_code == 404
    assert client.post("/users/u1/meal-templates/gone/log", json={}).status_code == 404
    assert client.put("/users/nobody/meal-templates", json=OAT_BREAKFAST).status_code == 404


def test_meal_template_validation(fakes):
    r = fakes.client.put("/users/u1/meal-templates", json={"name": "x", "items": []})
    assert r.status_code == 422
    r = fakes.client.put("/users/u1/meal-templates", json={"name": "x", "meal": "brunch", "items": [{"description": "a", "kcal": 1}]})
    assert r.status_code == 422


def test_meal_template_tools(fakes):
    _, handlers = build_ask_tools(fakes.db, "u1", Settings(_env_file=None))

    # Save via tool (as the LLM would after the user describes their usual breakfast)
    result = handlers["save_meal_template"]({
        "name": "yulaflı kahvaltı", "meal": "breakfast",
        "items": [
            {"description": "50 g yulaf", "kcal": 190, "fiber_g": 5},
            {"description": "1 ölçek whey", "kcal": 120, "protein_g": 24},
        ],
    })
    assert result == "saved meal template 'yulaflı kahvaltı' (310 kcal, 2 items)"
    assert handlers["save_meal_template"]({"name": "", "items": []}).startswith("error:")
    assert handlers["save_meal_template"]({"name": "x", "items": [{"description": "a", "kcal": -5}]}).startswith("error:")

    # Use via tool, case-insensitive name, scaled
    result = handlers["use_meal_template"]({"name": "Yulaflı Kahvaltı", "factor": 2})
    assert result == f"logged 'yulaflı kahvaltı (x2)' (breakfast) on {TODAY}: 620 kcal (day total 620 kcal)"
    assert handlers["use_meal_template"]({"name": "yok"}) == "error: no meal template named 'yok'; available: ['yulaflı kahvaltı']"
    assert handlers["use_meal_template"]({"name": "yulaflı kahvaltı", "date": "2099-01-01"}).startswith("error:")

    # The logged row carries scaled macros incl. fiber
    meals = fakes.client.get("/users/u1/meals").json()
    assert meals[-1]["kcal"] == 620 and meals[-1]["fiber_g"] == 10.0 and meals[-1]["source"] == "template"


def test_workout_templates_seed_crud_and_tool(fakes):
    client = fakes.client

    assert client.post("/users/u1/workout-templates/seed").json() == {"created": 3}
    names = [t["name"] for t in client.get("/users/u1/workout-templates").json()]
    assert names == ["Leg Day", "Pull Day", "Push Day"]
    assert client.post("/users/u1/workout-templates/seed").json() == {"created": 0}  # idempotent

    push = client.put("/users/u1/workout-templates", json={
        "name": "Bench Focus",
        "exercises": [{"name": "Bench press", "sets": 5, "reps": "5", "weight_kg": 80, "rpe_target": "8"}],
    })
    assert push.status_code == 200 and push.json()["exercises"][0]["sets"] == 5

    _, handlers = build_ask_tools(fakes.db, "u1", Settings(_env_file=None))
    listing = handlers["get_workout_template"]({})
    assert "Push Day (6 exercises)" in listing and "Bench Focus" in listing

    detail = handlers["get_workout_template"]({"name": "bench focus"})
    assert detail == "Bench Focus:\n- Bench press: 5 x 5 @ 80 kg (RPE 8)"
    assert handlers["get_workout_template"]({"name": "nope"}).startswith("error:")

    assert client.delete("/users/u1/workout-templates/Bench%20Focus").status_code == 204
    assert client.delete("/users/u1/workout-templates/Bench%20Focus").status_code == 404


def test_templates_appear_in_the_prompt(fakes):
    client = fakes.client
    client.put("/users/u1/meal-templates", json=OAT_BREAKFAST)
    client.post("/users/u1/workout-templates/seed")

    client.post("/ask", json={"user_id": "u1", "question": "What should I eat tomorrow morning?"})

    volatile = fakes.coach.answer_call.volatile
    assert "<templates>" in volatile
    assert "Oat breakfast (breakfast, 415 kcal)" in volatile
    assert "Push Day (6 exercises)" in volatile


def test_log_day_tool_accepts_fiber_via_meal_path(fakes):
    # fiber flows through the manual meals endpoint too
    r = fakes.client.post("/users/u1/meals", json={
        "meal": "lunch", "description": "Lentil soup", "kcal": 350, "fiber_g": 14.9, "protein_g": 18,
    })
    assert r.json()["fiber_g"] == 14.9
    intake = fakes.client.get("/users/u1/intake?days=1").json()
    assert intake[-1]["fiber_g"] == 14.9
