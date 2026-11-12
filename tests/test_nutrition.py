"""Meal compute: FatSecret per-100g parsing, LLM estimate fallback, endpoint wiring."""

import json
from types import SimpleNamespace

import pytest

from app.nutrition import PER_100G_RE, MealItemQuery, resolve_items
from app.rag.generator import LLMReply, LLMUsage

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
    fs = FakeFS({"oats": [SimpleNamespace(description=DESC)]})
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
    assert coach.calls == 1  # both misses batched into ONE estimate call
    assert out[1].source == "estimate"
    assert out[1].kcal == pytest.approx(117.0)


def test_resolve_without_any_source_raises():
    with pytest.raises(ValueError, match="no nutrition source"):
        resolve_items([MealItemQuery("mystery", 100)])


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
