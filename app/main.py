import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from openai import APIError, OpenAI
from qdrant_client import QdrantClient
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth import RateLimiter, authorize, current_user_id, get_ask_limiter
from app.config import Settings, get_settings
from app.data_access.biometrics import upsert_daily_metrics
from app.data_access.food import (
    DayIntake,
    FoodLogOut,
    add_food_log,
    day_intake,
    delete_food_log,
    get_food_log,
    intake_window,
    list_food_logs,
    sync_day_calories,
)
from app.data_access.measurements import MeasurementOut, list_measurements, upsert_measurement
from app.data_access.templates import (
    MealTemplateIn,
    MealTemplateOut,
    delete_meal_template,
    find_meal_template,
    list_meal_templates,
    list_workout_templates,
    meal_template_total,
    upsert_meal_template,
)
from app.data_access.memory import (
    ChatMessageOut,
    MemoryEntry,
    append_message,
    clear_history,
    list_memories,
    list_messages,
    list_prefix_memories,
    record_llm_call,
    recent_history,
    upsert_memory,
    usage_daily,
    usage_summary,
    delete_memory,
)
from app.data_access.users import DailyMetrics, UserProfile, get_user, get_user_days, list_users, upsert_user
from app.db.models import User
from app.db.session import get_db
from app.deps import get_coach, get_embedder, get_store
from app.integrations.routes import get_integrations
from app.integrations.routes import router as integrations_router
from app.integrations.service import Integrations
from app.programs.routes import router as programs_router
from app.programs.service import active_programs_summary, list_workouts
from app.rag.demo import DemoCoach, HashingEmbedder
from app.rag.extraction import run_memory_extraction
from app.rag.energy_safety import energy_answer, is_energy_target_question
from app.rag.embeddings import Embedder
from app.rag.generator import CoachClient
from app.rag.ingestion import ingest_protocols
from app.data_access.runs import add_run, delete_run, list_runs
from app.nutrition import MealItemQuery, resolve_items
from app.photo_nutrition import MAX_PHOTO_BYTES, analyze_photo, prepare_photo
from app.rag.prompt import build_prompt, format_intake, format_runs, format_templates, format_workouts
from app.rag.tools import PlanContext, build_ask_tools
from app.rag.vector_store import ProtocolStore, connect_qdrant
from app.schemas import (
    MEMORY_KEY_PATTERN,
    USER_ID_PATTERN,
    AskRequest,
    AskResponse,
    DayMetricsIn,
    LogFromTemplateIn,
    MealComputeIn,
    MealComputeOut,
    MealIn,
    MealUpdate,
    MeasurementIn,
    MemoryIn,
    MemoryKind,
    ProfileIn,
    PhotoEstimate,
    ResolvedItemOut,
    RunIn,
    RunOut,
    Source,
    UserSummary,
)

logger = logging.getLogger("fitness_coach")
photo_limiter = RateLimiter()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()

    if settings.demo_mode:
        qdrant_client = QdrantClient(":memory:")
        app.state.embedder = HashingEmbedder()
        app.state.coach = DemoCoach()
        app.state.store = ProtocolStore(qdrant_client, settings.qdrant_collection)
        ingest_protocols(app.state.embedder, app.state.store)
    else:
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to .env, or set DEMO_MODE=true to run offline."
            )
        openai_client = OpenAI(
            api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds, max_retries=2
        )
        qdrant_client = connect_qdrant(settings)
        app.state.embedder = Embedder(openai_client, settings.embedding_model)
        app.state.coach = CoachClient(
            openai_client,
            settings.fast_model,
            settings.plan_model,
            enable_caching=settings.enable_prompt_caching,
            ttl_stable=settings.cache_ttl_stable,
            ttl_session=settings.cache_ttl_session,
            max_tool_iterations=settings.tool_max_iterations,
            plan_timeout_seconds=settings.plan_timeout_seconds,
        )
        app.state.store = ProtocolStore(qdrant_client, settings.qdrant_collection)

    yield
    qdrant_client.close()


app = FastAPI(title="AI Fitness Coach", version="0.4.0", lifespan=lifespan)
app.include_router(integrations_router)
app.include_router(programs_router)


@app.middleware("http")
async def log_requests(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    # Logs method, path, status and timing only: bodies hold health metrics and stay out of logs.
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "request_id=%s %s %s status=%d %.0fms",
        request_id, request.method, request.url.path, response.status_code, elapsed_ms,
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/users", response_model=list[UserSummary])
def users(db: Session = Depends(get_db), current: str | None = Depends(current_user_id)) -> list[UserSummary]:
    return [
        UserSummary(user_id=u.id, name=u.name, goal=u.goal)
        for u in list_users(db)
        if current is None or u.id == current
    ]


@app.get("/users/{user_id}", response_model=UserProfile)
def user_detail(
    user_id: str, db: Session = Depends(get_db), current: str | None = Depends(current_user_id)
) -> UserProfile:
    authorize(user_id, current)
    user = get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return user


@app.put("/users/{user_id}", response_model=UserProfile)
def save_profile(
    profile: ProfileIn,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> UserProfile:
    authorize(user_id, current)
    upsert_user(db, user_id, **profile.model_dump())
    db.commit()
    return get_user(db, user_id)


@app.get("/users/{user_id}/days", response_model=list[DailyMetrics])
def day_history(
    days: int = Query(14, ge=1, le=365),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> list[DailyMetrics]:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return get_user_days(db, user_id, limit=days)


@app.put("/users/{user_id}/days/{day}", response_model=DailyMetrics)
def log_day(
    day: date,
    metrics: DayMetricsIn,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> DailyMetrics:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    if day > date.today():
        raise HTTPException(status_code=422, detail="Cannot log metrics for a future date")
    upsert_daily_metrics(db, user_id, day, source="manual", **metrics.model_dump(exclude_none=True))
    db.commit()
    return next(d for d in get_user_days(db, user_id) if d.date == day)


@app.post("/ask", response_model=AskResponse)
def ask(
    payload: AskRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current: str | None = Depends(current_user_id),
    limiter: RateLimiter = Depends(get_ask_limiter),
    embedder: Embedder = Depends(get_embedder),
    store: ProtocolStore = Depends(get_store),
    coach: CoachClient = Depends(get_coach),
    integrations: Integrations = Depends(get_integrations),
) -> AskResponse:
    authorize(payload.user_id, current)
    limiter.check(payload.user_id, settings.ask_rate_limit_per_minute)
    user = get_user(db, payload.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {payload.user_id} not found")
    if is_energy_target_question(payload.question):
        answer = energy_answer(user, payload.question)
        append_message(db, payload.user_id, payload.channel, "user", payload.question)
        append_message(db, payload.user_id, payload.channel, "assistant", answer)
        db.commit()
        return AskResponse(answer=answer, sources=[])
    if not store.is_ready():
        raise HTTPException(status_code=503, detail="Knowledge base is empty. Run: python -m scripts.ingest")

    memories = list_prefix_memories(db, payload.user_id, settings.memory_max_entries)
    history = recent_history(db, payload.user_id, settings.chat_history_turns)
    try:
        query_vector = embedder.embed_one(payload.question)
        chunks = store.search(query_vector, top_k=settings.rag_top_k, category=payload.category)
        parts = build_prompt(
            payload.question, user, chunks, memories,
            program_summary=active_programs_summary(db, payload.user_id),
            intake=format_intake(day_intake(db, payload.user_id, date.today())),
            workouts=format_workouts(
                list_workouts(db, payload.user_id, since=date.today() - timedelta(days=13))
            ),
            runs=format_runs(list_runs(db, payload.user_id, days=90)),
            templates=format_templates(
                list_meal_templates(db, payload.user_id), list_workout_templates(db, payload.user_id)
            ),
        )
        tools, handlers = build_ask_tools(
            db, payload.user_id, settings, integrations,
            plan_ctx=PlanContext(coach=coach, embedder=embedder, store=store),
        )
        reply = coach.complete(
            parts.stable,
            history,
            parts.volatile,
            tier="fast",
            effort="low",
            tools=tools,
            tool_handlers=handlers,
            cache_key=f"coach:{payload.user_id}",
        )
    except APIError as exc:
        logger.warning("OpenAI call failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="The AI service failed. Try again in a moment.") from exc

    append_message(db, payload.user_id, payload.channel, "user", payload.question)
    append_message(db, payload.user_id, payload.channel, "assistant", reply.text)
    record_llm_call(db, payload.user_id, reply.usage, tier="fast", purpose="ask")
    db.commit()
    if not settings.demo_mode:
        # Inline on purpose: a BackgroundTask would run after this db session closes, and a
        # fresh session would escape the caller's transaction. One cheap low-effort call.
        run_memory_extraction(coach, db, payload.user_id, payload.question, reply.text, settings)

    return AskResponse(
        answer=reply.text,
        sources=[
            Source(protocol_id=c.protocol_id, title=c.title, category=c.category, score=round(c.score, 4))
            for c in chunks
        ],
        cached_tokens=reply.usage.cached_tokens,
    )


@app.get("/users/{user_id}/messages", response_model=list[ChatMessageOut])
def messages(
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> list[ChatMessageOut]:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return list_messages(db, user_id, limit)


@app.delete("/users/{user_id}/messages", status_code=204)
def clear_messages(
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> Response:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    clear_history(db, user_id)
    db.commit()
    return Response(status_code=204)


@app.get("/users/{user_id}/usage")
def usage(
    days: int = Query(30, ge=1, le=365),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> dict:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return {
        "days": days,
        "by_model_purpose": [r.model_dump() for r in usage_summary(db, user_id, days)],
        "daily": [r.model_dump(mode="json") for r in usage_daily(db, user_id, days)],
    }


@app.get("/users/{user_id}/memories", response_model=list[MemoryEntry])
def memories(
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> list[MemoryEntry]:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return list_memories(db, user_id)


@app.put("/users/{user_id}/memories", response_model=list[MemoryEntry])
def save_memory(
    memory: MemoryIn,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current: str | None = Depends(current_user_id),
) -> list[MemoryEntry]:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    upsert_memory(
        db, user_id, kind=memory.kind, key=memory.key, value=memory.value,
        source="user", pinned=memory.pinned, max_entries=settings.memory_max_entries,
    )
    db.commit()
    return list_memories(db, user_id)


@app.delete("/users/{user_id}/memories/{kind}/{key}", status_code=204)
def forget_memory(
    kind: MemoryKind = Path(...),
    key: str = Path(..., pattern=MEMORY_KEY_PATTERN),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> Response:
    authorize(user_id, current)
    if not delete_memory(db, user_id, kind, key):
        raise HTTPException(status_code=404, detail="Memory not found")
    db.commit()
    return Response(status_code=204)


@app.post("/users/{user_id}/meals", response_model=FoodLogOut)
def log_meal(
    meal: MealIn,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> FoodLogOut:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    day = meal.day or date.today()
    if day > date.today():
        raise HTTPException(status_code=422, detail="Cannot log a meal for a future date")
    row = add_food_log(
        db, user_id, day, meal=meal.meal, description=meal.description, kcal=meal.kcal,
        source="manual", fatsecret_food_id=meal.fatsecret_food_id, servings=meal.servings,
        protein_g=meal.protein_g, carbs_g=meal.carbs_g, fat_g=meal.fat_g, fiber_g=meal.fiber_g,
    )
    sync_day_calories(db, user_id, day)
    db.commit()
    return FoodLogOut.model_validate(row, from_attributes=True)


@app.post("/users/{user_id}/meals/compute", response_model=MealComputeOut)
def compute_meal(
    payload: MealComputeIn,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
    coach: CoachClient = Depends(get_coach),
) -> MealComputeOut:
    """Estimate kcal/macros for name+grams item lines in one batched model call."""
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    items = [MealItemQuery(name=i.name.strip(), grams=i.grams) for i in payload.items]
    try:
        resolved = resolve_items(
            items,
            coach=coach, db=db, user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except APIError as exc:
        logger.warning("meal compute LLM call failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Nutrition estimate failed; try again in a moment.") from exc
    db.commit()  # persist the estimate's llm_calls accounting row, if one was recorded
    return MealComputeOut(
        items=[ResolvedItemOut(**vars(r)) for r in resolved],
        total_kcal=round(sum(r.kcal for r in resolved), 1),
        total_protein_g=round(sum(r.protein_g for r in resolved), 1),
        total_carbs_g=round(sum(r.carbs_g for r in resolved), 1),
        total_fat_g=round(sum(r.fat_g for r in resolved), 1),
        total_fiber_g=round(sum(r.fiber_g for r in resolved if r.fiber_g is not None), 1)
        if all(r.fiber_g is not None for r in resolved) else None,
    )


@app.post("/users/{user_id}/meals/photo", response_model=PhotoEstimate)
async def photo_meal(
    request: Request,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
    coach: CoachClient = Depends(get_coach),
    settings: Settings = Depends(get_settings),
) -> PhotoEstimate:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    if settings.demo_mode or not isinstance(coach, CoachClient):
        raise HTTPException(status_code=503, detail="Photo analysis is unavailable in demo mode.")
    photo_limiter.check(user_id, settings.photo_rate_limit_per_minute)
    content_type = request.headers.get("content-type", "").split(";")[0].lower()
    if content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=415, detail="Use JPEG, PNG or WebP.")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(status_code=413, detail="Image exceeds 5 MB.")
    try:
        image = await run_in_threadpool(prepare_photo, bytes(data), content_type)
        del data
        estimate, reply = await run_in_threadpool(analyze_photo, coach, image)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except APIError as exc:
        logger.warning("photo analysis failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Photo analysis failed; try again.") from exc
    record_llm_call(db, user_id, reply.usage, tier="fast", purpose="photo")
    db.commit()
    return estimate


@app.get("/users/{user_id}/meals", response_model=list[FoodLogOut])
def meals(
    day: date | None = Query(None, description="Defaults to today"),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> list[FoodLogOut]:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return list_food_logs(db, user_id, day or date.today(), day or date.today())


@app.put("/users/{user_id}/meals/{meal_id}", response_model=FoodLogOut)
def update_meal(
    meal_id: int,
    meal: MealUpdate,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> FoodLogOut:
    authorize(user_id, current)
    row = get_food_log(db, user_id, meal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Meal not found")
    if row.fatsecret_entry_id:
        raise HTTPException(
            status_code=409,
            detail="This meal is linked to a FatSecret diary entry and cannot be edited here.",
        )

    updates = meal.model_dump(exclude_unset=True)
    for field in ("meal", "description", "kcal", "servings", "protein_g", "carbs_g", "fat_g", "fiber_g"):
        if field in updates:
            setattr(row, field, updates[field])

    sync_day_calories(db, user_id, row.date)
    db.commit()
    return FoodLogOut.model_validate(row, from_attributes=True)


@app.delete("/users/{user_id}/meals/{meal_id}", status_code=204)
def remove_meal(
    meal_id: int,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> Response:
    authorize(user_id, current)
    day = delete_food_log(db, user_id, meal_id)
    if day is None:
        raise HTTPException(status_code=404, detail="Meal not found")
    sync_day_calories(db, user_id, day)
    db.commit()
    return Response(status_code=204)


@app.get("/users/{user_id}/intake", response_model=list[DayIntake])
def intake(
    days: int = Query(14, ge=1, le=365),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> list[DayIntake]:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return intake_window(db, user_id, days)


@app.post("/users/{user_id}/runs", response_model=RunOut)
def log_run(
    run: RunIn,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> RunOut:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    day = run.day or date.today()
    if day > date.today():
        raise HTTPException(status_code=422, detail="Cannot log a run for a future date")
    row = add_run(
        db, user_id, day,
        distance_km=run.distance_km, duration_min=run.duration_min,
        avg_hr_bpm=run.avg_hr_bpm, notes=run.notes,
    )
    db.commit()
    return RunOut.model_validate(row, from_attributes=True)


@app.get("/users/{user_id}/runs", response_model=list[RunOut])
def runs(
    days: int = Query(90, ge=1, le=365),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> list[RunOut]:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return [RunOut.model_validate(r, from_attributes=True) for r in list_runs(db, user_id, days)]


@app.delete("/users/{user_id}/runs/{run_id}", status_code=204)
def remove_run(
    run_id: int,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> Response:
    authorize(user_id, current)
    if not delete_run(db, user_id, run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    db.commit()
    return Response(status_code=204)


@app.get("/users/{user_id}/meal-templates", response_model=list[MealTemplateOut])
def meal_templates(
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> list[MealTemplateOut]:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return list_meal_templates(db, user_id)


@app.put("/users/{user_id}/meal-templates", response_model=MealTemplateOut)
def save_meal_template(
    template: MealTemplateIn,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> MealTemplateOut:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    result = upsert_meal_template(
        db, user_id, template.name, template.meal, [i.model_dump(exclude_none=True) for i in template.items]
    )
    db.commit()
    return result


@app.delete("/users/{user_id}/meal-templates/{name}", status_code=204)
def remove_meal_template(
    name: str,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> Response:
    authorize(user_id, current)
    if not delete_meal_template(db, user_id, name):
        raise HTTPException(status_code=404, detail="Meal template not found")
    db.commit()
    return Response(status_code=204)


@app.post("/users/{user_id}/meal-templates/{name}/log", response_model=FoodLogOut)
def log_from_meal_template(
    name: str,
    body: LogFromTemplateIn,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> FoodLogOut:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    template = find_meal_template(db, user_id, name)
    if template is None:
        raise HTTPException(status_code=404, detail=f"No meal template named {name!r}")
    day = body.day or date.today()
    if day > date.today():
        raise HTTPException(status_code=422, detail="Cannot log a meal for a future date")

    factor = body.factor
    description = template.name + (f" (x{factor:g})" if factor != 1 else "")

    def scaled(key: str) -> float | None:
        values = [float(i[key]) * float(i.get("servings", 1)) for i in template.items if i.get(key) is not None]
        return round(sum(values) * factor, 1) if values else None

    row = add_food_log(
        db, user_id, day, meal=template.meal, description=description,
        kcal=round(meal_template_total(template.items) * factor), source="template",
        protein_g=scaled("protein_g"), carbs_g=scaled("carbs_g"), fat_g=scaled("fat_g"),
        fiber_g=scaled("fiber_g"),
    )
    sync_day_calories(db, user_id, day)
    db.commit()
    return FoodLogOut.model_validate(row, from_attributes=True)


@app.put("/users/{user_id}/measurements/{day}", response_model=MeasurementOut)
def save_measurement(
    day: date,
    measurement: MeasurementIn,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> MeasurementOut:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    if day > date.today():
        raise HTTPException(status_code=422, detail="Cannot log measurements for a future date")
    upsert_measurement(db, user_id, day, source="manual", **measurement.model_dump(exclude_none=True))
    db.commit()
    return next(m for m in list_measurements(db, user_id, start=day, end=day))


@app.get("/users/{user_id}/measurements", response_model=list[MeasurementOut])
def measurements(
    days: int = Query(90, ge=1, le=3650),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> list[MeasurementOut]:
    authorize(user_id, current)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return list_measurements(db, user_id, start=date.today() - timedelta(days=days - 1))
