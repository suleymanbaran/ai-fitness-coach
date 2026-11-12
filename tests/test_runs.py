"""Run logging: REST endpoints, the log_run chat tool, and prompt placement."""

from datetime import date

from app.config import Settings
from app.data_access.runs import list_runs
from app.data_access.users import get_user
from app.rag.prompt import build_prompt, format_runs
from app.rag.tools import LOG_RUN_TOOL, build_ask_tools


def test_run_endpoints_crud_and_derived_fields(fakes):
    r = fakes.client.post("/users/u1/runs", json={"distance_km": 5.0, "duration_min": 27.5, "avg_hr_bpm": 152})
    assert r.status_code == 200
    body = r.json()
    assert body["pace_min_per_km"] == 5.5
    assert body["avg_hr_bpm"] == 152
    assert body["est_5k_min"] == 27.5  # same distance -> Riegel factor 1

    ten_k = fakes.client.post(
        "/users/u1/runs", json={"distance_km": 10, "duration_min": 50, "date": str(date.today())}
    ).json()
    assert abs(ten_k["est_5k_min"] - 24.0) < 0.15  # Riegel: 50 * 0.5^1.06

    runs = fakes.client.get("/users/u1/runs?days=7").json()
    assert [x["id"] for x in runs] == sorted(x["id"] for x in runs)
    assert len(runs) == 2

    assert fakes.client.delete(f"/users/u1/runs/{body['id']}").status_code == 204
    assert fakes.client.delete(f"/users/u1/runs/{body['id']}").status_code == 404
    assert len(fakes.client.get("/users/u1/runs").json()) == 1


def test_run_endpoint_validation(fakes):
    future = fakes.client.post("/users/u1/runs", json={"distance_km": 5, "duration_min": 25, "date": "2099-01-01"})
    assert future.status_code == 422
    assert fakes.client.post("/users/u1/runs", json={"distance_km": 0, "duration_min": 25}).status_code == 422
    assert fakes.client.post("/users/u1/runs", json={"distance_km": 5, "duration_min": 25, "avg_hr_bpm": 15}).status_code == 422
    assert fakes.client.post("/users/nobody/runs", json={"distance_km": 5, "duration_min": 25}).status_code == 404


def test_log_run_tool_writes_row(fakes):
    _, handlers = build_ask_tools(fakes.db, "u1", Settings(_env_file=None))
    out = handlers["log_run"]({"distance_km": 3, "duration_min": 18, "avg_hr_bpm": 145, "date": str(date.today())})
    assert "run saved" in out
    assert "6:00/km" in out
    assert "avg HR 145" in out
    rows = list_runs(fakes.db, "u1", days=1)
    assert len(rows) == 1 and rows[0].avg_hr_bpm == 145

    assert handlers["log_run"]({"distance_km": 0, "duration_min": 10}).startswith("error:")
    assert handlers["log_run"]({"distance_km": 5, "duration_min": 25, "date": "2099-01-01"}).startswith("error:")
    assert handlers["log_run"]({"distance_km": 5, "duration_min": 25, "date": "nope"}).startswith("error:")
    assert LOG_RUN_TOOL["name"] in handlers


def test_runs_reach_the_prompt_as_volatile(fakes):
    fakes.client.post("/users/u1/runs", json={"distance_km": 5, "duration_min": 26, "avg_hr_bpm": 158})
    fakes.client.post("/ask", json={"user_id": "u1", "question": "Is my 5k getting faster?"})
    # The last recorded call is the post-answer memory extraction; pick the /ask call.
    ask_call = next(c for c in fakes.coach.calls if "Question:" in c.volatile)
    assert "Run history" in ask_call.volatile
    assert "5:12/km" in ask_call.volatile
    assert "Run history" not in ask_call.stable


def test_format_runs_empty_and_pace(fakes):
    assert format_runs([]) == ""
    user = get_user(fakes.db, "u1")
    parts = build_prompt("q", user, [], [])
    assert "Run history" not in parts.volatile


def test_parse_runs_real_shapes():
    from app.integrations.google_health import parse_runs

    points = [
        # local date crosses midnight: 22:30Z + 3h offset -> next day
        {"name": "u/1/dp/run-9", "exercise": {
            "interval": {"startTime": "2026-11-12T22:30:00Z", "startUtcOffset": "10800s"},
            "exerciseType": "RUNNING",
            "metricsSummary": {"distanceMillimeters": 5000000, "averageHeartRateBeatsPerMinute": "158"},
            "activeDuration": "1500s",
        }},
        # no activeDuration -> falls back to interval length
        {"name": "u/1/dp/run-10", "exercise": {
            "interval": {"startTime": "2026-11-10T07:00:00Z", "startUtcOffset": "3600s",
                         "endTime": "2026-11-10T07:42:00Z", "endUtcOffset": "3600s"},
            "exerciseType": "TRAIL_RUNNING",
            "metricsSummary": {"distanceMillimeters": 8000000},
        }},
        # walk pace (2 km in 26 min) -> skipped
        {"name": "u/1/dp/walk", "exercise": {
            "interval": {"startTime": "2026-11-09T07:00:00Z", "startUtcOffset": "0s"},
            "exerciseType": "TREADMILL",
            "metricsSummary": {"distanceMillimeters": 2000000},
            "activeDuration": "1560s",
        }},
        # cycling -> skipped
        {"name": "u/1/dp/bike", "exercise": {
            "interval": {"startTime": "2026-11-08T07:00:00Z", "startUtcOffset": "0s"},
            "exerciseType": "CYCLING",
            "metricsSummary": {"distanceMillimeters": 20000000},
            "activeDuration": "3600s",
        }},
    ]
    runs = parse_runs(points)
    assert [(r["day"].isoformat(), r["distance_km"], r["duration_min"], r["avg_hr_bpm"]) for r in runs] == [
        ("2026-11-13", 5.0, 25.0, 158),
        ("2026-11-10", 8.0, 42.0, None),
    ]
    assert runs[0]["external_id"] == "u/1/dp/run-9"


def test_fetch_exercises_stops_paging_past_start():
    from datetime import timedelta

    import httpx

    from app.integrations.google_health import GoogleHealthClient

    today = date.today()
    old_day = (today - timedelta(days=90)).isoformat()

    def point(day_iso: str, name: str) -> dict:
        return {"name": name, "exercise": {
            "interval": {"startTime": f"{day_iso}T08:00:00Z", "startUtcOffset": "0s"},
            "exerciseType": "RUNNING",
            "metricsSummary": {"distanceMillimeters": 5000000},
            "activeDuration": "1500s",
        }}

    calls = {"pages": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["pages"] += 1
        if "pageToken" not in request.url.params:
            return httpx.Response(200, json={
                "dataPoints": [point(today.isoformat(), "dp/recent"), point(old_day, "dp/old")],
                "nextPageToken": "p2",
            })
        return httpx.Response(200, json={"dataPoints": [point(old_day, "dp/older")]})

    client = GoogleHealthClient("c", "s", "http://x", http=httpx.Client(transport=httpx.MockTransport(handler)))
    out = client.fetch_exercises("tok", today - timedelta(days=7), today)
    assert calls["pages"] == 1  # page 1 already reached past the window; page 2 never fetched
    assert [p["name"] for p in out] == ["dp/recent"]
