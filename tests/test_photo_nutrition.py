import io
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import column, create_engine, select, table

from app.auth import current_user_id
from app.config import Settings, get_settings
from app.data_access.food import _complete_sum
from app.db.session import get_db
from app.deps import get_coach
from app.main import app, photo_limiter
from app.photo_nutrition import MAX_PHOTO_BYTES, analyze_photo, prepare_photo
from app.data_access.users import UserProfile
from app.programs.service import ProgramInputError, require_nutrition_profile
from app.rag.prompt import format_profile
from app.rag.generator import CoachClient


def image_bytes():
    out = io.BytesIO()
    image = Image.new("RGB", (20, 10), "red")
    exif = Image.Exif()
    exif[0x010E] = "private metadata"
    image.save(out, format="JPEG", exif=exif)
    return out.getvalue()


def test_image_validation_and_metadata_removal():
    clean = prepare_photo(image_bytes(), "image/jpeg")
    with Image.open(io.BytesIO(clean)) as image:
        assert not image.getexif()
    with pytest.raises(ValueError):
        prepare_photo(image_bytes(), "image/png")
    with pytest.raises(ValueError):
        prepare_photo(b"x" * (MAX_PHOTO_BYTES + 1), "image/jpeg")


def test_photo_request_is_transient_and_validates_output():
    captured = []
    result = {
        "items": [{"name": "Pilav", "grams": 150, "kcal": 190, "protein_g": 4,
                   "carbs_g": 40, "fat_g": None, "fiber_g": None}],
        "uncertainty": "Yağ ve porsiyon belirsiz.",
    }

    def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(result), usage=None)

    coach = CoachClient(SimpleNamespace(responses=SimpleNamespace(create=create)), "fast", "plan")
    estimate, _ = analyze_photo(coach, prepare_photo(image_bytes(), "image/jpeg"))
    assert estimate.items[0].fiber_g is None
    assert captured[0]["store"] is False
    assert captured[0]["input"][0]["content"][1]["image_url"].startswith("data:image/jpeg;base64,")
    result["items"][0]["grams"] = -1
    with pytest.raises(ValueError):
        analyze_photo(coach, prepare_photo(image_bytes(), "image/jpeg"))


def test_photo_endpoint_auth_limits_and_no_meal_write():
    calls = []
    db = SimpleNamespace(get=lambda *_: object(), add=lambda row: calls.append(row), commit=lambda: None)
    response = SimpleNamespace(
        output_text='{"items": [], "uncertainty": "Yemek görünmüyor."}', usage=None
    )
    coach = CoachClient(
        SimpleNamespace(responses=SimpleNamespace(create=lambda **_: response)), "fast", "plan"
    )
    overrides = {
        get_db: lambda: db,
        current_user_id: lambda: "u1",
        get_coach: lambda: coach,
        get_settings: lambda: Settings(_env_file=None),
    }
    app.dependency_overrides.update(overrides)
    photo_limiter._hits.clear()
    try:
        client = TestClient(app)
        assert client.post("/users/u2/meals/photo", content=image_bytes(), headers={"content-type": "image/jpeg"}).status_code == 403
        assert client.post("/users/u1/meals/photo", content=b"bad", headers={"content-type": "image/gif"}).status_code == 415
        assert client.post("/users/u1/meals/photo", content=b"x" * (MAX_PHOTO_BYTES + 1), headers={"content-type": "image/jpeg"}).status_code == 413
        result = client.post("/users/u1/meals/photo", content=image_bytes(), headers={"content-type": "image/jpeg"})
        assert result.status_code == 200 and result.json()["items"] == []
        assert len(calls) == 1 and calls[0].purpose == "photo"
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)
        photo_limiter._hits.clear()


def test_unknown_nutrient_makes_daily_total_unknown():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE meals (fiber REAL)")
        conn.exec_driver_sql("INSERT INTO meals VALUES (4), (NULL)")
        meals = table("meals", column("fiber"))
        assert conn.scalar(select(_complete_sum(meals.c.fiber)).select_from(meals)) is None


def test_nutrition_profile_requires_adult_and_known_measurements():
    user = UserProfile(user_id="u", name="Test", age=None, goal="general_fitness", days=[])
    with pytest.raises(ProgramInputError, match="age"):
        require_nutrition_profile(user)
    user.age = 17
    with pytest.raises(ProgramInputError, match="under 18"):
        require_nutrition_profile(user)
    user.age = 30
    with pytest.raises(ProgramInputError, match="weight and height"):
        require_nutrition_profile(user)
    user.weight_kg = 80
    user.height_cm = 180
    require_nutrition_profile(user)
    assert "128-176 g/day" in format_profile(user)
