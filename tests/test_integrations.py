from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet

from app.config import Settings, get_settings
from app.db.models import BodyMeasurement, DailyBiometrics, IntegrationAccount, User
from app.integrations import common
from app.integrations.common import IntegrationError, ReauthRequired, TokenCipher, request_with_retry
from app.integrations.fatsecret import FatSecretClient, parse_food_entries
from app.integrations.google_health import (
    GoogleHealthClient,
    parse_active_energy_rollup,
    parse_body_fat,
    parse_daily_hrv,
    parse_resting_hr,
    parse_sleep_minutes,
    parse_sleep_stages,
    parse_steps_rollup,
    parse_weight,
)
from app.integrations.routes import get_integrations
from app.integrations.service import Integrations
from app.main import app

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


def civil(d: date) -> dict:
    return {"year": d.year, "month": d.month, "day": d.day}


def sleep_point(end: date, minutes: int, main: bool) -> dict:
    # Real API shape: physical UTC end time + offset (civilEndTime is documented but not returned).
    return {
        "sleep": {
            "interval": {"endTime": f"{end}T06:00:00Z", "endUtcOffset": "3600s"},
            "metadata": {"mainSleep": main},
            "summary": {
                "minutesAsleep": str(minutes),
                "stagesSummary": [
                    {"type": "AWAKE", "minutes": "6", "count": "1"},
                    {"type": "LIGHT", "minutes": "152", "count": "7"},
                    {"type": "DEEP", "minutes": "70", "count": "3"},
                    {"type": "REM", "minutes": "77", "count": "3"},
                ],
            },
        }
    }


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(common, "BACKOFF_SECONDS", 0)


# ---------- parsing ----------


def test_parse_google_daily_metrics():
    hrv = [{"dailyHeartRateVariability": {"date": civil(TODAY), "averageHeartRateVariabilityMilliseconds": 48.27}}]
    rhr = [{"dailyRestingHeartRate": {"date": civil(TODAY), "beatsPerMinute": "57"}}]
    assert parse_daily_hrv(hrv) == {TODAY: 48.3}
    assert parse_resting_hr(rhr) == {TODAY: 57}


def test_parse_sleep_prefers_main_sleep_over_nap_and_skips_unprocessed():
    points = [
        sleep_point(TODAY, 45, main=False),  # afternoon nap ending the same day
        sleep_point(TODAY, 412, main=True),
        {"sleep": {"interval": {"civilEndTime": {"date": civil(YESTERDAY)}}, "summary": {}}},  # still processing
    ]
    assert parse_sleep_minutes(points) == {TODAY: 412}


def test_parse_food_entries():
    entries = {"food_entries": {"food_entry": [{"calories": "540"}, {"calories": "310.6"}]}}
    assert parse_food_entries(entries) == 851
    assert parse_food_entries({"food_entries": {"food_entry": {"calories": "200"}}}) == 200  # single entry
    assert parse_food_entries({"food_entries": None}) is None  # nothing logged is not zero eaten
    with pytest.raises(ReauthRequired):
        parse_food_entries({"error": {"code": 9, "message": "Invalid access token"}})
    with pytest.raises(IntegrationError, match="error 14"):
        parse_food_entries({"error": {"code": 14, "message": "Application does not have permission"}})


# ---------- HTTP behaviour ----------


def test_request_with_retry_retries_rate_limits_then_succeeds():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429 if len(calls) < 3 else 200, json={})

    response = request_with_retry(httpx.Client(transport=httpx.MockTransport(handler)), "GET", "https://x.test/")
    assert response.status_code == 200
    assert len(calls) == 3


def test_cipher_round_trip_and_rejects_tampered_state():
    cipher = TokenCipher(Fernet.generate_key().decode())
    assert cipher.decrypt(cipher.encrypt("secret")) == "secret"
    assert cipher.read_state(cipher.make_state({"user_id": "me"})) == {"user_id": "me"}
    with pytest.raises(IntegrationError):
        cipher.read_state("not-a-real-state")
    with pytest.raises(ReauthRequired):
        TokenCipher(Fernet.generate_key().decode()).decrypt(cipher.encrypt("secret"))  # key changed


class FakeGoogle:
    """Mimics Google's OAuth token endpoint and the Health API list endpoint (with pagination)."""

    def __init__(self) -> None:
        self.token_grants: list[str] = []
        self.filters: list[str] = []
        self.reject_access = False
        self.deny_activity = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            self.token_grants.append(form["grant_type"][0])
            body = {"access_token": f"access-{len(self.token_grants)}", "expires_in": 3600}
            if form["grant_type"][0] == "authorization_code":
                body["refresh_token"] = "refresh-1"
            return httpx.Response(200, json=body)

        if self.reject_access:
            return httpx.Response(401, json={})
        data_type = request.url.path.split("/")[-2]
        if request.url.path.endswith(":dailyRollUp"):
            assert request.method == "POST"
            if self.deny_activity:
                return httpx.Response(403, json={})
            if data_type == "steps":
                return httpx.Response(200, json={"rollupDataPoints": [
                    {"civilStartTime": {"date": civil(TODAY)}, "steps": {"countSum": "8432"}},
                    {"civilStartTime": {"date": civil(YESTERDAY)}, "steps": {"countSum": "6210"}},
                ]})
            if data_type == "active-energy-burned":
                return httpx.Response(200, json={"rollupDataPoints": [
                    {"civilStartTime": {"date": civil(TODAY)}, "activeEnergyBurned": {"kcalSum": 612.7}},
                ]})
            return httpx.Response(200, json={"rollupDataPoints": []})
        self.filters.append(request.url.params["filter"])
        if data_type == "daily-heart-rate-variability":
            if "pageToken" not in request.url.params:  # first page, more to come
                point = {"dailyHeartRateVariability": {"date": civil(TODAY), "averageHeartRateVariabilityMilliseconds": 44.0}}
                return httpx.Response(200, json={"dataPoints": [point], "nextPageToken": "p2"})
            point = {"dailyHeartRateVariability": {"date": civil(YESTERDAY), "averageHeartRateVariabilityMilliseconds": 61.0}}
            return httpx.Response(200, json={"dataPoints": [point]})
        if data_type == "daily-resting-heart-rate":
            return httpx.Response(200, json={"dataPoints": [{"dailyRestingHeartRate": {"date": civil(TODAY), "beatsPerMinute": "62"}}]})
        if data_type == "weight":
            # Two samples the same day: the later one must win.
            return httpx.Response(200, json={"dataPoints": [
                {"weight": {"sampleTime": {"physicalTime": f"{TODAY}T06:00:00Z", "civilTime": {"date": civil(TODAY)}}, "weightGrams": 84000}},
                {"weight": {"sampleTime": {"physicalTime": f"{TODAY}T20:00:00Z", "civilTime": {"date": civil(TODAY)}}, "weightGrams": 83600}},
            ]})
        if data_type == "body-fat":
            return httpx.Response(200, json={"dataPoints": [
                {"bodyFat": {"sampleTime": {"physicalTime": f"{TODAY}T06:00:00Z", "civilTime": {"date": civil(TODAY)}}, "percentage": 21.4}},
            ]})
        return httpx.Response(200, json={"dataPoints": [sleep_point(TODAY, 330, main=True)]})


class FakeFatSecret:
    """Mimics FatSecret's 3-legged OAuth 1.0 endpoints, the food diary, search and food detail."""

    def __init__(self) -> None:
        self.diary_dates: list[int] = []
        self.search_paths: list[str] = []
        self.search_queries: list[str] = []
        self.created_entries: list[dict] = []
        self.deleted_entries: list[str] = []
        self.app_only_paths: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.content:
            for key, values in parse_qs(request.content.decode()).items():
                params[key] = values[0]
        assert "oauth_signature" in params, "every FatSecret request must be signed"
        path = request.url.path
        if path == "/oauth/request_token":
            assert request.method == "POST"
            assert "oauth_signature" not in request.url.params
            assert params["oauth_callback"].endswith("/integrations/fatsecret/callback")
            return httpx.Response(200, text="oauth_token=req-tok&oauth_token_secret=req-sec&oauth_callback_confirmed=true")
        if path == "/oauth/access_token":
            assert params["oauth_verifier"] == "verif"
            return httpx.Response(200, text="oauth_token=acc-tok&oauth_token_secret=acc-sec")
        if "oauth_token" in params:
            assert params["oauth_token"] == "acc-tok"
        else:
            # App-only signing (consumer key, no connected user): public food endpoints only.
            assert path.startswith("/rest/foods/search/") or path == "/rest/food/v6", \
                "diary endpoints require the user's token"
            self.app_only_paths.append(path)
        if path.startswith("/rest/foods/search/"):
            self.search_paths.append(path)
            self.search_queries.append(params["search_expression"])
            # A single result: FatSecret returns an object where a list is expected.
            return httpx.Response(200, json={"foods": {"food": {
                "food_id": "41963", "food_name": "Cheeseburger", "food_type": "Brand",
                "food_description": "Per 1 serving - Calories: 300kcal | Fat: 13.00g | Carbs: 32.00g | Protein: 15.00g",
                "food_url": "https://foods.fatsecret.com/calories-nutrition/mcdonalds/cheeseburger",
            }, "max_results": "20", "total_results": "1", "page_number": "0"}})
        if path == "/rest/food/v6":
            return httpx.Response(200, json={"food": {
                "food_id": "41963", "food_name": "Cheeseburger", "brand_name": "McDonald's",
                "servings": {"serving": [{
                    "serving_id": "50321", "serving_description": "1 serving",
                    "metric_serving_amount": "150.000", "metric_serving_unit": "g",
                    "number_of_units": "1.000", "calories": "300",
                    "carbohydrate": "32.00", "protein": "15.00", "fat": "13.00",
                }]},
            }})
        if path == "/rest/food-entries/v1" and request.method == "POST":
            self.created_entries.append(params)
            return httpx.Response(200, json={"food_entry_id": {"value": "777"}})
        if path == "/rest/food-entries/v1" and request.method == "DELETE":
            self.deleted_entries.append(params["food_entry_id"])
            return httpx.Response(200, json={"success": {"value": "1"}})
        day = int(params["date"])
        self.diary_dates.append(day)
        if day == (TODAY - date(1970, 1, 1)).days:
            return httpx.Response(200, json={"food_entries": {"food_entry": [{"calories": "1200"}, {"calories": "650"}]}})
        return httpx.Response(200, json={"food_entries": None})


def test_google_client_paginates_and_builds_daily_filters():
    fake = FakeGoogle()
    client = GoogleHealthClient("cid", "csecret", "http://api/cb", http=httpx.Client(transport=httpx.MockTransport(fake.handler)))

    days = client.fetch_days("token", YESTERDAY, TODAY)

    assert days == {
        TODAY: {
            "hrv_ms": 44.0, "resting_hr_bpm": 62, "sleep_minutes": 330,
            "sleep_stages": {"awake": 6, "light": 152, "deep": 70, "rem": 77},
            "steps": 8432, "active_kcal": 613,
        },
        YESTERDAY: {"hrv_ms": 61.0, "steps": 6210},
    }
    assert f'daily_heart_rate_variability.date >= "{YESTERDAY}"' in fake.filters[0]
    assert any(f.startswith("sleep.interval.civil_end_time") for f in fake.filters)

    # Activity scope denied (connected before activity_and_fitness was requested):
    # steps/energy degrade away, the other metrics still sync.
    fake.deny_activity = True
    days = client.fetch_days("token", TODAY, TODAY)
    assert days == {
        TODAY: {
            "hrv_ms": 44.0, "resting_hr_bpm": 62, "sleep_minutes": 330,
            "sleep_stages": {"awake": 6, "light": 152, "deep": 70, "rem": 77},
        }
    }

    fake.deny_activity = False
    fake.reject_access = True
    with pytest.raises(ReauthRequired):
        client.fetch_days("token", TODAY, TODAY)


def test_google_authorization_url_requests_offline_read_only_access():
    url = GoogleHealthClient("cid", "csecret", "http://api/cb").authorization_url("STATE")
    query = parse_qs(urlparse(url).query)
    assert query["access_type"] == ["offline"]
    assert query["state"] == ["STATE"]
    assert all(scope.endswith(".readonly") for scope in query["scope"][0].split())


def test_parse_weight_latest_sample_wins_and_falls_back_to_utc():
    points = [
        {"weight": {"sampleTime": {"physicalTime": f"{YESTERDAY}T21:00:00Z", "civilTime": {"date": civil(YESTERDAY)}}, "weightGrams": 84200}},
        {"weight": {"sampleTime": {"physicalTime": f"{YESTERDAY}T06:00:00Z", "civilTime": {"date": civil(YESTERDAY)}}, "weightGrams": 85000}},
        # No civil time: falls back to the UTC date of physicalTime.
        {"weight": {"sampleTime": {"physicalTime": f"{TODAY}T05:00:00Z"}, "weightGrams": 83600}},
        {"weight": {"sampleTime": {"physicalTime": f"{TODAY}T05:30:00Z"}}},  # no value: skipped
    ]
    assert parse_weight(points) == {YESTERDAY: 84.2, TODAY: 83.6}


def test_parse_body_fat():
    points = [
        {"bodyFat": {"sampleTime": {"physicalTime": f"{TODAY}T06:00:00Z", "civilTime": {"date": civil(TODAY)}}, "percentage": 21.46}},
    ]
    assert parse_body_fat(points) == {TODAY: 21.5}
    assert parse_body_fat([]) == {}


def test_parse_activity_rollups():
    steps_points = [
        {"civilStartTime": {"date": civil(TODAY)}, "steps": {"countSum": "8432"}},
        {"civilStartTime": {"date": civil(YESTERDAY)}},  # unworn day: value field omitted
        {"civilStartTime": {"date": civil(YESTERDAY)}, "steps": {"countSum": "0"}},  # worn but idle: real zero
    ]
    assert parse_steps_rollup(steps_points) == {TODAY: 8432, YESTERDAY: 0}

    energy_points = [{"civilStartTime": {"date": civil(TODAY)}, "activeEnergyBurned": {"kcalSum": 612.7}}]
    assert parse_active_energy_rollup(energy_points) == {TODAY: 613}
    assert parse_active_energy_rollup([]) == {}


def test_parse_sleep_handles_utc_offset_and_midnight_crossing():
    points = [
        # Real shape: 23:30Z + 1h offset ends 00:30 local the NEXT day.
        {"sleep": {"interval": {"endTime": "2026-11-12T23:30:00Z", "endUtcOffset": "3600s"},
                   "metadata": {"mainSleep": True}, "summary": {"minutesAsleep": "420"}}},
        # Documented shape: civilEndTime wins when present.
        {"sleep": {"interval": {"civilEndTime": {"date": civil(TODAY)}, "endTime": "2020-01-01T00:00:00Z"},
                   "metadata": {"mainSleep": True}, "summary": {"minutesAsleep": "400"}}},
        # No usable time: skipped.
        {"sleep": {"metadata": {"mainSleep": True}, "summary": {"minutesAsleep": "100"}}},
    ]
    parsed = parse_sleep_minutes(points)
    assert parsed[date(2026, 11, 13)] == 420
    assert parsed[TODAY] == 400
    assert len(parsed) == 2


def test_parse_sleep_stages():
    points = [sleep_point(TODAY, 330, main=True), {"sleep": {"summary": {"minutesAsleep": "100"}}}]  # second: no interval, skipped
    assert parse_sleep_stages(points) == {TODAY: {"awake": 6, "light": 152, "deep": 70, "rem": 77}}
    assert parse_sleep_stages([]) == {}


# ---------- full flow through the API ----------


@pytest.fixture
def connected_app(fakes):
    google, fatsecret = FakeGoogle(), FakeFatSecret()
    integrations = Integrations(
        cipher=TokenCipher(Fernet.generate_key().decode()),
        google=GoogleHealthClient(
            "cid", "csecret", "http://testserver/integrations/google/callback",
            http=httpx.Client(transport=httpx.MockTransport(google.handler)),
        ),
        fatsecret=FatSecretClient("ck", "cs", http=httpx.Client(transport=httpx.MockTransport(fatsecret.handler))),
        public_api_url="http://testserver",
    )
    app.dependency_overrides[get_integrations] = lambda: integrations
    fakes.google, fakes.fatsecret, fakes.integrations = google, fatsecret, integrations
    yield fakes
    app.dependency_overrides.pop(get_integrations, None)


def connect_google(client) -> httpx.Response:
    url = client.post("/users/u1/integrations/google/connect").json()["authorization_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    return client.get("/integrations/google/callback", params={"code": "auth-code", "state": state}, follow_redirects=False)


def test_connect_google_then_sync_fills_daily_metrics(connected_app):
    client, db = connected_app.client, connected_app.db

    r = connect_google(client)
    assert r.status_code == 303
    assert r.headers["location"].endswith("?connected=google")

    account = db.query(IntegrationAccount).filter_by(user_id="u1", provider="google").one()
    assert "access-1" not in account.access_token_enc  # stored encrypted
    assert connected_app.integrations.cipher.decrypt(account.refresh_token_enc) == "refresh-1"

    results = client.post("/users/u1/sync", params={"days": 2}).json()
    assert results == [{"provider": "google", "days_updated": 2, "error": None, "reconnect": False}]

    row = db.query(DailyBiometrics).filter_by(user_id="u1", date=TODAY).one()
    assert (row.hrv_ms, row.resting_hr_bpm, row.sleep_minutes, row.source) == (44.0, 62, 330, "google")
    assert (row.steps, row.active_kcal) == (8432, 613)
    assert row.sleep_stages == {"awake": 6, "light": 152, "deep": 70, "rem": 77}

    # Weight/body-fat samples land in body_measurements and refresh the profile weight.
    measurement = db.query(BodyMeasurement).filter_by(user_id="u1", date=TODAY).one()
    assert (measurement.weight_kg, measurement.body_fat_pct, measurement.source) == (83.6, 21.4, "google")
    assert db.get(User, "u1").weight_kg == 83.6

    status = {s["provider"]: s for s in client.get("/users/u1/integrations").json()}
    assert status["google"]["connected"] and status["google"]["last_synced_at"]
    assert not status["fatsecret"]["connected"]


def test_expired_google_token_is_refreshed_before_sync(connected_app):
    connect_google(connected_app.client)
    account = connected_app.db.query(IntegrationAccount).filter_by(provider="google").one()
    account.expires_at = datetime.now(UTC) - timedelta(minutes=5)
    connected_app.db.commit()

    connected_app.client.post("/users/u1/sync", params={"days": 1})

    assert connected_app.google.token_grants == ["authorization_code", "refresh_token"]
    assert connected_app.integrations.cipher.decrypt(account.access_token_enc) == "access-2"


def test_connect_fatsecret_then_sync_merges_calories_without_erasing_google(connected_app):
    client, db = connected_app.client, connected_app.db
    connect_google(client)
    url = client.post("/users/u1/integrations/fatsecret/connect").json()["authorization_url"]
    assert parse_qs(urlparse(url).query)["oauth_token"] == ["req-tok"]

    r = client.get(
        "/integrations/fatsecret/callback",
        params={"oauth_token": "req-tok", "oauth_verifier": "verif"},
        follow_redirects=False,
    )
    assert r.headers["location"].endswith("?connected=fatsecret")

    results = {x["provider"]: x for x in client.post("/users/u1/sync", params={"days": 3}).json()}
    assert results["fatsecret"]["days_updated"] == 1  # only today has diary entries
    assert len(connected_app.fatsecret.diary_dates) == 3

    row = db.query(DailyBiometrics).filter_by(user_id="u1", date=TODAY).one()
    assert (row.calories_kcal, row.hrv_ms) == (1850, 44.0)
    # Days without diary entries keep their seeded calories instead of being zeroed.
    two_days_ago = db.query(DailyBiometrics).filter_by(user_id="u1", date=TODAY - timedelta(days=2)).one_or_none()
    assert two_days_ago is None or two_days_ago.calories_kcal != 0


def test_sync_reports_reconnect_when_google_rejects_tokens(connected_app):
    connect_google(connected_app.client)
    connected_app.google.reject_access = True

    [result] = connected_app.client.post("/users/u1/sync").json()

    assert result["reconnect"] is True
    status = connected_app.client.get("/users/u1/integrations").json()
    assert "Connect Google Health again" in status[0]["last_error"]


def test_tampered_state_and_unknown_request_token_are_rejected(connected_app):
    client = connected_app.client
    r = client.get("/integrations/google/callback", params={"code": "c", "state": "forged"}, follow_redirects=False)
    assert "integration_error" in r.headers["location"]
    r = client.get(
        "/integrations/fatsecret/callback", params={"oauth_token": "never-issued", "oauth_verifier": "v"},
        follow_redirects=False,
    )
    assert "integration_error" in r.headers["location"]
    assert connected_app.db.query(IntegrationAccount).count() == 0


def test_disconnect_removes_stored_tokens(connected_app):
    connect_google(connected_app.client)
    assert connected_app.client.delete("/users/u1/integrations/google").status_code == 204
    assert connected_app.db.query(IntegrationAccount).count() == 0


def test_unconfigured_provider_returns_503(fakes):
    app.dependency_overrides[get_integrations] = lambda: Integrations(None, None, None, "http://testserver")
    try:
        r = fakes.client.post("/users/u1/integrations/google/connect")
        assert r.status_code == 503
        assert all(not s["configured"] for s in fakes.client.get("/users/u1/integrations").json())
    finally:
        app.dependency_overrides.pop(get_integrations, None)


def test_with_auth_users_cannot_connect_or_sync_someone_else(connected_app):
    from app.auth import hash_token
    from app.db.models import User

    connected_app.db.get(User, "u2").api_token_hash = hash_token("elif-token")
    connected_app.db.commit()
    app.dependency_overrides[get_settings] = lambda: Settings(auth_enabled=True)
    try:
        headers = {"Authorization": "Bearer elif-token"}
        client = connected_app.client
        assert client.post("/users/u1/integrations/google/connect", headers=headers).status_code == 403
        assert client.post("/users/u1/sync", headers=headers).status_code == 403
        assert client.get("/users/u1/integrations", headers=headers).status_code == 403
        assert client.post("/users/u2/integrations/google/connect", headers=headers).status_code == 200
    finally:
        app.dependency_overrides.pop(get_settings, None)
