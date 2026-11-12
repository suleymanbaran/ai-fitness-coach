"""Body measurements: idempotent upserts, profile-weight refresh, endpoints."""

from datetime import date, timedelta

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


def test_measurement_partial_upsert_and_profile_refresh(fakes):
    client = fakes.client

    r = client.put(f"/users/u1/measurements/{YESTERDAY}", json={"weight_kg": 84.2, "waist_cm": 92})
    assert r.status_code == 200
    assert r.json()["weight_kg"] == 84.2 and r.json()["source"] == "manual"

    # The latest measurement date refreshes the profile weight the coach sees.
    assert client.get("/users/u1").json()["weight_kg"] == 84.2

    # A partial re-log fills fields without erasing the others (same contract as days).
    r = client.put(f"/users/u1/measurements/{YESTERDAY}", json={"body_fat_pct": 21.5})
    assert (r.json()["weight_kg"], r.json()["body_fat_pct"], r.json()["waist_cm"]) == (84.2, 21.5, 92.0)

    # A newer date moves the profile weight forward; an older one does not.
    client.put(f"/users/u1/measurements/{TODAY}", json={"weight_kg": 83.6})
    assert client.get("/users/u1").json()["weight_kg"] == 83.6
    client.put(f"/users/u1/measurements/{YESTERDAY}", json={"weight_kg": 90.0})
    assert client.get("/users/u1").json()["weight_kg"] == 83.6


def test_measurement_listing_and_validation(fakes):
    client = fakes.client
    client.put(f"/users/u1/measurements/{YESTERDAY}", json={"weight_kg": 84.2, "notes": "morning, fasted"})
    client.put(f"/users/u1/measurements/{TODAY}", json={"weight_kg": 83.6})

    rows = client.get("/users/u1/measurements?days=7").json()
    assert [r["date"] for r in rows] == [str(YESTERDAY), str(TODAY)]
    assert rows[0]["notes"] == "morning, fasted"

    assert client.put(f"/users/u1/measurements/{TODAY}", json={}).status_code == 422
    assert client.put(f"/users/u1/measurements/{TODAY}", json={"weight_kg": 500}).status_code == 422
    assert client.put(f"/users/u1/measurements/{TODAY + timedelta(days=1)}", json={"weight_kg": 80}).status_code == 422
    assert client.put("/users/nobody/measurements/2026-01-01", json={"weight_kg": 80}).status_code == 404
    assert client.get("/users/nobody/measurements").status_code == 404

    # Notes-only entries are allowed (e.g. "felt bloated").
    r = client.put(f"/users/u1/measurements/{TODAY}", json={"notes": "bloated"})
    assert r.status_code == 200 and r.json()["notes"] == "bloated"
