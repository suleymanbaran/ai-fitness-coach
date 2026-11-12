"""Durable user memories, server-side chat history and LLM token accounting.

None of these functions commit; callers own the transaction, matching the rest of data_access.
"""

from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import ChatMessage, LLMCall, UserMemory
from app.rag.generator import LLMUsage

MEMORY_KINDS = ("preference", "fact", "constraint")
MEMORY_SOURCES = ("user", "assistant")
# Total characters of memory text allowed into the model's stable prompt prefix.
MEMORY_PREFIX_CHAR_BUDGET = 6000


class MemoryEntry(BaseModel):
    id: int
    kind: str
    key: str
    value: str
    source: str
    pinned: bool
    updated_at: datetime


class ChatMessageOut(BaseModel):
    id: int
    channel: str
    role: str
    content: str
    created_at: datetime


def _to_entry(row: UserMemory) -> MemoryEntry:
    return MemoryEntry(
        id=row.id, kind=row.kind, key=row.key, value=row.value,
        source=row.source, pinned=row.pinned, updated_at=row.updated_at,
    )


def list_memories(db: Session, user_id: str) -> list[MemoryEntry]:
    rows = db.scalars(
        select(UserMemory)
        .where(UserMemory.user_id == user_id)
        .order_by(UserMemory.pinned.desc(), UserMemory.updated_at.desc())
    )
    return [_to_entry(row) for row in rows]


def list_prefix_memories(
    db: Session, user_id: str, max_entries: int, max_chars: int = MEMORY_PREFIX_CHAR_BUDGET
) -> list[MemoryEntry]:
    """Pinned first, then most recently updated, truncated to the entry and character budgets.
    This is the block that goes into the cached prompt prefix, so it must stay bounded."""
    entries = list_memories(db, user_id)[:max_entries]
    result: list[MemoryEntry] = []
    used = 0
    for entry in entries:
        used += len(entry.key) + len(entry.value)
        if used > max_chars:
            break
        result.append(entry)
    return result


def upsert_memory(
    db: Session,
    user_id: str,
    kind: str,
    key: str,
    value: str,
    source: str,
    pinned: bool | None = None,
    max_entries: int = 40,
) -> None:
    """Idempotent write on (user_id, kind, key).

    Assistant extraction never overwrites a row the user stated or edited (source="user"):
    the ON CONFLICT WHERE guard silently skips those. After an assistant write, the oldest
    non-pinned assistant rows are evicted past max_entries.
    """
    values: dict = {"value": value, "source": source, "updated_at": func.now()}
    if pinned is not None:
        values["pinned"] = pinned
    stmt = insert(UserMemory).values(
        user_id=user_id, kind=kind, key=key, pinned=bool(pinned), value=value, source=source, updated_at=func.now()
    )
    if source == "assistant":
        stmt = stmt.on_conflict_do_update(
            constraint="uq_memory_user_kind_key",
            set_={"value": value, "updated_at": func.now()},
            where=UserMemory.source == "assistant",
        )
    else:
        stmt = stmt.on_conflict_do_update(constraint="uq_memory_user_kind_key", set_=values)
    db.execute(stmt)
    if source == "assistant":
        _evict(db, user_id, max_entries)


def _evict(db: Session, user_id: str, max_entries: int) -> None:
    count = db.scalar(select(func.count()).select_from(UserMemory).where(UserMemory.user_id == user_id)) or 0
    overflow = count - max_entries
    if overflow <= 0:
        return
    victims = db.scalars(
        select(UserMemory.id)
        .where(
            UserMemory.user_id == user_id,
            UserMemory.source == "assistant",
            UserMemory.pinned.is_(False),
        )
        .order_by(UserMemory.updated_at.asc(), UserMemory.id.asc())
        .limit(overflow)
    ).all()
    if victims:
        db.execute(delete(UserMemory).where(UserMemory.id.in_(victims)))


def delete_memory(db: Session, user_id: str, kind: str, key: str) -> bool:
    result = db.execute(
        delete(UserMemory).where(
            UserMemory.user_id == user_id, UserMemory.kind == kind, UserMemory.key == key
        )
    )
    return result.rowcount > 0


def append_message(db: Session, user_id: str, channel: str, role: str, content: str) -> None:
    db.add(ChatMessage(user_id=user_id, channel=channel, role=role, content=content))


def recent_history(db: Session, user_id: str, limit: int) -> list[dict]:
    """The last `limit` messages across all channels, oldest first, as chat items."""
    rows = db.execute(
        select(ChatMessage.role, ChatMessage.content)
        .where(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(limit)
    ).all()
    return [{"role": role, "content": content} for role, content in reversed(rows)]


def list_messages(db: Session, user_id: str, limit: int) -> list[ChatMessageOut]:
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(limit)
    )
    return [
        ChatMessageOut(id=r.id, channel=r.channel, role=r.role, content=r.content, created_at=r.created_at)
        for r in reversed(list(rows))
    ]


def clear_history(db: Session, user_id: str) -> int:
    result = db.execute(delete(ChatMessage).where(ChatMessage.user_id == user_id))
    return result.rowcount


class UsageRow(BaseModel):
    model: str
    purpose: str
    calls: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int


class UsageDay(BaseModel):
    day: date
    input_tokens: int
    cached_tokens: int
    output_tokens: int


def usage_summary(db: Session, user_id: str, days: int) -> list[UsageRow]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.execute(
        select(
            LLMCall.model, LLMCall.purpose, func.count(),
            func.sum(LLMCall.input_tokens), func.sum(LLMCall.cached_tokens), func.sum(LLMCall.output_tokens),
        )
        .where(LLMCall.user_id == user_id, LLMCall.created_at >= since)
        .group_by(LLMCall.model, LLMCall.purpose)
        .order_by(LLMCall.model, LLMCall.purpose)
    ).all()
    return [
        UsageRow(model=model, purpose=purpose, calls=calls,
                 input_tokens=int(inp), cached_tokens=int(cached), output_tokens=int(out))
        for model, purpose, calls, inp, cached, out in rows
    ]


def usage_daily(db: Session, user_id: str, days: int) -> list[UsageDay]:
    since = datetime.now(UTC) - timedelta(days=days)
    day_col = func.date_trunc("day", LLMCall.created_at).label("day")
    rows = db.execute(
        select(
            day_col,
            func.sum(LLMCall.input_tokens), func.sum(LLMCall.cached_tokens), func.sum(LLMCall.output_tokens),
        )
        .where(LLMCall.user_id == user_id, LLMCall.created_at >= since)
        .group_by(day_col)
        .order_by(day_col)
    ).all()
    return [
        UsageDay(day=day.date(), input_tokens=int(inp), cached_tokens=int(cached), output_tokens=int(out))
        for day, inp, cached, out in rows
    ]


def record_llm_call(db: Session, user_id: str, usage: LLMUsage, tier: str, purpose: str) -> None:
    db.add(
        LLMCall(
            user_id=user_id,
            model=usage.model,
            tier=tier,
            purpose=purpose,
            input_tokens=usage.input_tokens,
            cached_tokens=usage.cached_tokens,
            output_tokens=usage.output_tokens,
        )
    )
