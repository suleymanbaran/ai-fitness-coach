"""Resolve meal items (name + grams) into kcal/macros for the web "Add meal" form.

Resolution order per item: a FatSecret per-100g hit (free database, US/English only) first;
everything left over goes into ONE batched structured LLM estimate, so a five-item breakfast
costs a single cheap luna call when FatSecret misses (which it often does for non-US foods).
"""

import json
import logging
import math
import re
from dataclasses import dataclass

from app.data_access.memory import record_llm_call
from app.rag.generator import CoachClient

logger = logging.getLogger("fitness_coach")

PER_100G_RE = re.compile(
    r"per\s+100\s*g.*?calories:\s*([\d.]+)\s*kcal.*?fat:\s*([\d.]+)\s*g"
    r".*?carbs?:\s*([\d.]+)\s*g.*?protein:\s*([\d.]+)\s*g",
    re.IGNORECASE | re.DOTALL,
)

ESTIMATOR_INSTRUCTIONS = """You are a nutrition database. For every food listed, give typical
values PER 100 GRAMS: kcal, protein, carbs, fat and fiber in grams. For branded products use
typical label values; for dishes use a standard recipe. Return one entry per food, same names,
numbers only — no commentary."""

ESTIMATE_SCHEMA = {
    "type": "object",
    "properties": {
        "foods": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kcal_per_100g": {"type": "number"},
                    "protein_g_per_100g": {"type": "number"},
                    "carbs_g_per_100g": {"type": "number"},
                    "fat_g_per_100g": {"type": "number"},
                    "fiber_g_per_100g": {"type": "number"},
                },
                "required": [
                    "name", "kcal_per_100g", "protein_g_per_100g",
                    "carbs_g_per_100g", "fat_g_per_100g", "fiber_g_per_100g",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["foods"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class MealItemQuery:
    name: str
    grams: float


@dataclass(frozen=True)
class ResolvedItem:
    name: str
    grams: float
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float | None
    source: str  # "fatsecret" | "estimate"


def _scale(name: str, grams: float, per100: tuple[float, float, float, float, float | None], source: str) -> ResolvedItem:
    kcal, protein, carbs, fat, fiber = per100
    f = grams / 100.0
    return ResolvedItem(
        name=name, grams=grams,
        kcal=round(kcal * f, 1), protein_g=round(protein * f, 1), carbs_g=round(carbs * f, 1),
        fat_g=round(fat * f, 1), fiber_g=round(fiber * f, 1) if fiber is not None else None, source=source,
    )


def _from_fatsecret(fatsecret, query: MealItemQuery) -> ResolvedItem | None:
    if fatsecret is None:
        return None
    try:
        hits = fatsecret.search_foods(None, None, query.name)
    except Exception as exc:  # The estimate remains available when the external service fails.
        logger.warning("food database lookup failed: %s", type(exc).__name__)
        return None
    for hit in hits:
        if " ".join(hit.name.casefold().split()) != " ".join(query.name.casefold().split()):
            continue
        m = PER_100G_RE.search(hit.description or "")
        if not m:
            continue
        kcal, fat, carbs, protein = (float(g) for g in m.groups())
        return _scale(query.name, query.grams, (kcal, protein, carbs, fat, None), "fatsecret")
    return None


def _estimate_batch(
    coach: CoachClient, queries: list[MealItemQuery], db, user_id: str
) -> dict[str, tuple[float, float, float, float, float]]:
    """One structured call covering all unresolved foods; keyed by lowercase name."""
    volatile = "Foods (give per-100g values for each):\n" + "\n".join(f"- {q.name}" for q in queries)
    reply = coach.complete(
        ESTIMATOR_INSTRUCTIONS, [], volatile,
        tier="fast", effort="low",
        json_schema=ESTIMATE_SCHEMA, schema_name="food_estimates",
    )
    if db is not None and user_id:
        record_llm_call(db, user_id, reply.usage, tier="fast", purpose="estimate")
    out: dict[str, tuple[float, float, float, float, float]] = {}
    for food in json.loads(reply.text).get("foods", []):
        values = (
            float(food["kcal_per_100g"]), float(food["protein_g_per_100g"]),
            float(food["carbs_g_per_100g"]), float(food["fat_g_per_100g"]),
            float(food["fiber_g_per_100g"]),
        )
        if any(not math.isfinite(value) or value < 0 or value > maximum
               for value, maximum in zip(values, (1000, 100, 100, 100, 100))):
            raise ValueError("Nutrition estimation returned invalid values; try again.")
        out[str(food["name"]).strip().lower()] = values
    return out


def resolve_items(
    items: list[MealItemQuery],
    *,
    fatsecret=None,
    coach: CoachClient | None = None,
    db=None,
    user_id: str = "",
) -> list[ResolvedItem]:
    """Resolve every item, preserving input order. Raises ValueError when an item cannot be
    resolved at all (no FatSecret hit and no usable estimate)."""
    resolved: dict[int, ResolvedItem] = {}
    missing: list[tuple[int, MealItemQuery]] = []
    for i, q in enumerate(items):
        hit = _from_fatsecret(fatsecret, q)
        if hit is not None:
            resolved[i] = hit
        else:
            missing.append((i, q))

    if missing:
        if coach is None:
            raise ValueError("no nutrition source available for: " + ", ".join(q.name for _, q in missing))
        queries = [q for _, q in missing]
        estimates = _estimate_batch(coach, queries, db, user_id)
        unmatched: list[tuple[int, MealItemQuery]] = []
        for i, q in missing:
            per100 = estimates.get(q.name.strip().lower())
            if per100 is None:
                unmatched.append((i, q))
            else:
                resolved[i] = _scale(q.name, q.grams, per100, "estimate")
        # Never assign a different food's estimate to an unresolved item.
        unresolved = [q.name for i, q in enumerate(items) if i not in resolved]
        if unresolved:
            raise ValueError("could not estimate nutrition for: " + ", ".join(unresolved))

    return [resolved[i] for i in range(len(items))]
