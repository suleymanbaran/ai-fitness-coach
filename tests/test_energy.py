import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import current_user_id
from app.auth import RateLimiter, get_ask_limiter
from app.config import get_settings, Settings
from app.data_access.users import UserProfile
from app.db.session import get_db
from app.deps import get_coach, get_embedder, get_store
from app.main import app
from app.programs.schemas import EnergyInputs, NutritionPlanOutput
from app.programs.routes import _program_out
from app.programs.service import (
    ProgramGenerationError, ProgramInputError, _validated_plan_call, calculate_energy,
    check_nutrition_safety, generate_program, generate_weekly_plan, revise_program,
)
from app.rag.energy_safety import energy_answer, is_energy_target_question
from tests.test_programs import PlanCoach, VALID_NUTRITION


def adult(**updates):
    values = dict(user_id="u1", name="Test", age=30, weight_kg=80, height_cm=180, goal="fat_loss", days=[])
    return UserProfile(**(values | updates))


def inputs(**updates):
    values = dict(equation="male", activity="moderate", safety_confirmed=True)
    return EnergyInputs(**(values | updates))


def test_maintenance_calculation_uses_explicit_inputs_and_no_goal_adjustment():
    result = calculate_energy(adult(), inputs())
    assert result["resting_kcal"] == 1780
    assert result["target_kcal"] == 2759  # (800 + 1125 - 150 + 5) * 1.55
    assert result["method"] == "mifflin_st_jeor_maintenance"
    assert calculate_energy(adult(goal="muscle_gain"), inputs())["target_kcal"] == 2759
    assert calculate_energy(adult(), inputs(equation="female", activity="sedentary"))["target_kcal"] == 1937


def test_missing_or_unsafe_inputs_stop_calculation():
    with pytest.raises(ProgramInputError, match="energy estimate form"):
        calculate_energy(adult(), None)
    with pytest.raises(ValidationError):
        inputs(safety_confirmed=False)
    with pytest.raises(ProgramInputError, match="under 18"):
        calculate_energy(adult(age=17), inputs())
    with pytest.raises(ProgramInputError, match="weight"):
        calculate_energy(adult(weight_kg=None), inputs())
    with pytest.raises(ProgramInputError, match="low body weight"):
        calculate_energy(adult(weight_kg=50), inputs())
    with pytest.raises(ProgramInputError, match="invalid"):
        calculate_energy(adult(height_cm=float("nan")), inputs())


def test_plan_model_cannot_override_calculated_target(monkeypatch):
    monkeypatch.setattr("app.programs.service.record_llm_call", lambda *_args, **_kwargs: None)
    incorrect = json.loads(json.dumps(VALID_NUTRITION))
    correct = json.loads(json.dumps(VALID_NUTRITION))
    correct["plan"]["target_kcal"] = 2759
    correct["plan"]["carbs_g"] = 350
    correct["plan"]["fat_g"] = 80
    correct["summary"] = "Estimated maintenance: 2759 kcal."
    coach = PlanCoach(plan_payloads=[json.dumps(incorrect), json.dumps(correct)])
    result = _validated_plan_call(None, coach, adult(), NutritionPlanOutput, "nutrition", "stable", "task", energy_target=2759)
    assert result.plan.target_kcal == 2759
    assert "must equal" in coach.calls[1].volatile
    incorrect["plan"]["target_kcal"] = 2759
    incorrect["plan"]["carbs_g"] = 350
    incorrect["plan"]["fat_g"] = 80
    incorrect["summary"] = "2000 kcal is your goal."
    coach = PlanCoach(plan_payloads=[json.dumps(incorrect), json.dumps(incorrect)])
    with pytest.raises(ProgramGenerationError):
        _validated_plan_call(None, coach, adult(), NutritionPlanOutput, "nutrition", "stable", "task", energy_target=2759)
    incorrect["summary"] = "Daily target 2000."
    coach = PlanCoach(plan_payloads=[json.dumps(incorrect), json.dumps(incorrect)])
    with pytest.raises(ProgramGenerationError):
        _validated_plan_call(None, coach, adult(), NutritionPlanOutput, "nutrition", "stable", "task", energy_target=2759)
    incorrect["summary"] = "Maintenance estimate."
    incorrect["plan"]["protein_g"] = 0
    incorrect["plan"]["carbs_g"] = 0
    incorrect["plan"]["fat_g"] = 0
    coach = PlanCoach(plan_payloads=[json.dumps(incorrect), json.dumps(incorrect)])
    with pytest.raises(ProgramGenerationError):
        _validated_plan_call(None, coach, adult(), NutritionPlanOutput, "nutrition", "stable", "task", energy_target=2759)


def test_api_requires_explicit_inputs_before_model_call(monkeypatch):
    monkeypatch.setattr("app.programs.routes.get_user", lambda *_: adult())
    monkeypatch.setattr("app.programs.routes.list_memories", lambda *_: [])
    invoked = []
    coach = SimpleNamespace(complete=lambda *args, **kwargs: invoked.append(True))
    overrides = {
        get_db: lambda: object(),
        current_user_id: lambda: "u1",
        get_settings: lambda: Settings(_env_file=None),
        get_store: lambda: SimpleNamespace(is_ready=lambda: True),
        get_embedder: lambda: object(),
        get_coach: lambda: coach,
    }
    app.dependency_overrides.update(overrides)
    try:
        client = TestClient(app)
        result = client.post("/users/u1/plans/weekly", json={"brief": "fat loss"})
        assert result.status_code == 422 and "energy estimate form" in result.json()["detail"]
        assert not invoked
        response = client.post("/users/u1/energy-estimate", json=inputs().model_dump())
        assert response.status_code == 200 and response.json()["target_kcal"] == 2759
        assert client.post("/users/u1/energy-estimate", json=inputs().model_dump() | {"safety_confirmed": False}).status_code == 422
        assert client.post("/users/u2/energy-estimate", json=inputs().model_dump()).status_code == 403
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)


@pytest.mark.parametrize("brief,memories", [
    ("Hamileyim", []),
    (None, [SimpleNamespace(key="health", value="diabetes")]),
    (None, [SimpleNamespace(key="böbrek", value="geçmiş durum")]),
    ("Diyabetim yok", []),
])
def test_safety_screen_is_conservative_for_brief_and_saved_context(brief, memories):
    with pytest.raises(ProgramInputError, match="qualified professional"):
        check_nutrition_safety(brief, memories)


@pytest.mark.parametrize("operation", ["nutrition", "weekly", "revision"])
def test_safety_screen_checks_all_memories_before_retrieval(monkeypatch, operation):
    monkeypatch.setattr("app.programs.service.list_prefix_memories", lambda *_: [])
    monkeypatch.setattr(
        "app.programs.service.list_memories",
        lambda *_: [SimpleNamespace(key="health", value="pregnant")],
    )
    def forbidden(*_args, **_kwargs):
        pytest.fail("Unsafe nutrition request reached retrieval or model")
    monkeypatch.setattr("app.programs.service._retrieve_program_protocols", forbidden)
    settings = Settings(_env_file=None)
    with pytest.raises(ProgramInputError, match="qualified professional"):
        if operation == "nutrition":
            generate_program(None, None, None, None, adult(), "nutrition", None, settings, inputs())
        elif operation == "weekly":
            generate_weekly_plan(None, None, None, None, adult(), None, settings, inputs())
        else:
            revise_program(None, None, None, None, adult(), SimpleNamespace(kind="nutrition"), "", settings, inputs())


def test_preview_rejects_risk_in_saved_memory(monkeypatch):
    monkeypatch.setattr("app.programs.routes.get_user", lambda *_: adult())
    monkeypatch.setattr(
        "app.programs.routes.list_memories",
        lambda *_: [SimpleNamespace(key="health", value="pregnancy")],
    )
    overrides = {get_db: lambda: object(), current_user_id: lambda: "u1"}
    app.dependency_overrides.update(overrides)
    try:
        response = TestClient(app).post("/users/u1/energy-estimate", json=inputs().model_dump())
        assert response.status_code == 422 and "qualified professional" in response.json()["detail"]
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)


def test_legacy_nutrition_target_not_presented_as_calculated():
    from datetime import UTC, datetime

    program = SimpleNamespace(
        id=1, kind="nutrition", status="active", version=1, name="Cut 2000",
        summary="Eat 2000 kcal", rationale="2000 kcal", payload={"target_kcal": 2000, "protein_g": 100},
        created_at=datetime.now(UTC), activated_at=None,
    )
    out = _program_out(program)
    assert out.payload["target_kcal"] is None
    assert "unverified" in out.summary
    assert "2000" not in out.name and out.rationale is None


@pytest.mark.parametrize("question", [
    "Günlük kaç kalori almalıyım?", "Kalori ihtiyacım nedir?", "What is my daily calorie target?"
])
def test_target_question_asks_for_inputs_without_guessing(question):
    assert is_energy_target_question(question)
    answer = energy_answer(adult(weight_kg=None), question)
    assert "Plan" in answer and not any(char.isdigit() for char in answer)


def test_food_log_question_is_not_routed_to_energy_form():
    assert not is_energy_target_question("Bugün kaç kalori yedim?")
    assert "diyetisyen" in energy_answer(adult(), "Hamileyim, günlük kalori hedefim nedir?")


def test_ask_target_does_not_call_model_and_preserves_history(monkeypatch):
    monkeypatch.setattr("app.main.get_user", lambda *_: adult(weight_kg=None))
    messages = []
    monkeypatch.setattr("app.main.append_message", lambda _db, _id, _channel, role, content: messages.append((role, content)))
    calls = []
    overrides = {
        get_db: lambda: SimpleNamespace(commit=lambda: None),
        current_user_id: lambda: "u1",
        get_settings: lambda: Settings(_env_file=None),
        get_ask_limiter: lambda: RateLimiter(),
        get_store: lambda: SimpleNamespace(is_ready=lambda: False),
        get_embedder: lambda: object(),
        get_coach: lambda: SimpleNamespace(complete=lambda *args, **kwargs: calls.append(True)),
    }
    app.dependency_overrides.update(overrides)
    try:
        response = TestClient(app).post("/ask", json={"user_id": "u1", "question": "Günlük kaç kalori almalıyım?"})
        assert response.status_code == 200 and "Plan" in response.json()["answer"]
        assert not calls and [role for role, _ in messages] == ["user", "assistant"]
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)
