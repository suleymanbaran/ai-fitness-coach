"""Background memory extraction: after each answer, a cheap structured call distills durable
facts and preferences out of the exchange and upserts them as source="assistant" memories."""

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.data_access.memory import delete_memory, list_memories, record_llm_call, upsert_memory
from app.db.models import UserMemory
from app.rag.generator import CoachClient

logger = logging.getLogger("fitness_coach")

EXTRACTION_INSTRUCTIONS = """You distill durable long-term memories from one coach conversation exchange.

Output memory updates ONLY for information that stays true across days: preferences, facts about
the user's body, equipment, schedule, diet rules, or constraints. Ignore one-off context (today's
mood, a single workout, transient metrics). Keys are short snake_case. If the exchange contains
nothing durable, return an empty updates list. Use op="delete" when the user says a stored memory
is no longer true."""

MEMORY_UPDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "updates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["set", "delete"]},
                    "kind": {"type": "string", "enum": ["preference", "fact", "constraint"]},
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["op", "kind", "key", "value"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["updates"],
    "additionalProperties": False,
}


def run_memory_extraction(
    coach: CoachClient, db: Session, user_id: str, question: str, answer: str, settings: Settings
) -> int:
    """Returns the number of memory rows written or deleted. Never raises: extraction is a
    best-effort side effect and must not surface failures to the user."""
    existing = list_memories(db, user_id)
    volatile = (
        f"Existing memory keys: {[f'{m.kind}/{m.key}' for m in existing] or 'none'}\n\n"
        f"User: {question}\nCoach: {answer[:2000]}"
    )
    try:
        reply = coach.complete(
            EXTRACTION_INSTRUCTIONS,
            [],
            volatile,
            tier="fast",
            effort="low",
            json_schema=MEMORY_UPDATE_SCHEMA,
            schema_name="memory_updates",
        )
        updates = json.loads(reply.text).get("updates", [])
    except (ValueError, AttributeError) as exc:
        logger.warning("memory extraction produced no usable output: %s", type(exc).__name__)
        return 0
    record_llm_call(db, user_id, reply.usage, tier="fast", purpose="extract")

    user_owned = {
        (m.kind, m.key)
        for m in db.execute(
            select(UserMemory.kind, UserMemory.key).where(
                UserMemory.user_id == user_id, UserMemory.source == "user"
            )
        ).all()
    }
    applied = 0
    for update in updates[:10]:
        kind, key = update.get("kind"), update.get("key", "")
        if not kind or not key:
            continue
        if update.get("op") == "delete":
            if (kind, key) not in user_owned and delete_memory(db, user_id, kind, key):
                applied += 1
        else:
            # upsert_memory's SQL guard already refuses to overwrite source="user" rows.
            upsert_memory(
                db, user_id, kind=kind, key=key, value=str(update.get("value", ""))[:500],
                source="assistant", max_entries=settings.memory_max_entries,
            )
            applied += 1
    db.commit()
    return applied
