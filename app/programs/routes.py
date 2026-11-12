"""HTTP surface for programs and workout logs."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.auth import authorize, current_user_id
from app.config import Settings, get_settings
from app.data_access.templates import (
    WorkoutTemplateIn,
    WorkoutTemplateOut,
    delete_workout_template,
    list_workout_templates,
    seed_workout_templates,
    upsert_workout_template,
)
from app.data_access.users import UserProfile, get_user
from app.db.session import get_db
from app.deps import get_coach, get_embedder, get_store
from app.programs import service
from app.programs.schemas import (
    ExerciseHistoryRow,
    GenerateProgramIn,
    ProgramOut,
    ReviewOut,
    TodaySessionOut,
    WorkoutIn,
    WorkoutOut,
)
from app.rag.embeddings import Embedder
from app.rag.generator import CoachClient
from app.rag.vector_store import ProtocolStore
from app.schemas import USER_ID_PATTERN

router = APIRouter(tags=["programs"])


def _require_user(db: Session, user_id: str) -> UserProfile:
    user = get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return user


@router.post("/users/{user_id}/programs/generate", response_model=ProgramOut)
def generate(
    body: GenerateProgramIn,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current: str | None = Depends(current_user_id),
    embedder: Embedder = Depends(get_embedder),
    store: ProtocolStore = Depends(get_store),
    coach: CoachClient = Depends(get_coach),
) -> ProgramOut:
    authorize(user_id, current)
    user = _require_user(db, user_id)
    if not store.is_ready():
        raise HTTPException(status_code=503, detail="Knowledge base is empty. Run: python -m scripts.ingest")
    try:
        program = service.generate_program(db, coach, embedder, store, user, body.kind, body.brief, settings)
    except service.ProgramGenerationError:
        raise HTTPException(
            status_code=502, detail="Program generation failed schema validation twice. Try again in a moment."
        ) from None
    return ProgramOut.model_validate(program, from_attributes=True)


@router.get("/users/{user_id}/programs", response_model=list[ProgramOut])
def programs(
    status: str | None = Query(None, pattern="^(draft|active|archived)$"),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> list[ProgramOut]:
    authorize(user_id, current)
    _require_user(db, user_id)
    return [ProgramOut.model_validate(p, from_attributes=True) for p in service.list_programs(db, user_id, status)]


@router.get("/users/{user_id}/programs/{program_id}", response_model=ProgramOut)
def program_detail(
    program_id: int,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> ProgramOut:
    authorize(user_id, current)
    program = service.get_program(db, user_id, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    return ProgramOut.model_validate(program, from_attributes=True)


@router.post("/users/{user_id}/programs/{program_id}/activate", response_model=ProgramOut)
def activate(
    program_id: int,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> ProgramOut:
    authorize(user_id, current)
    program = service.activate_program(db, user_id, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    return ProgramOut.model_validate(program, from_attributes=True)


@router.post("/users/{user_id}/programs/{program_id}/review", response_model=ReviewOut)
def review(
    program_id: int,
    regenerate: bool = Query(False, description="Also generate the next version as a draft"),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current: str | None = Depends(current_user_id),
    embedder: Embedder = Depends(get_embedder),
    store: ProtocolStore = Depends(get_store),
    coach: CoachClient = Depends(get_coach),
) -> ReviewOut:
    authorize(user_id, current)
    user = _require_user(db, user_id)
    program = service.get_program(db, user_id, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    assessment = service.review_program(db, coach, program, user, settings)

    new_version = None
    if regenerate:
        brief = f"Revision of v{program.version}. Review findings:\n{assessment[:1500]}"
        try:
            new_program = service.generate_program(db, coach, embedder, store, user, program.kind, brief, settings)
            new_version = new_program.version
        except service.ProgramGenerationError:
            raise HTTPException(
                status_code=502, detail="Review succeeded but regeneration failed validation. Try again."
            ) from None
    db.commit()
    return ReviewOut(program_id=program_id, assessment=assessment, regenerated_version=new_version)


@router.get("/users/{user_id}/workout-templates", response_model=list[WorkoutTemplateOut])
def workout_templates(
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> list[WorkoutTemplateOut]:
    authorize(user_id, current)
    _require_user(db, user_id)
    return list_workout_templates(db, user_id)


@router.put("/users/{user_id}/workout-templates", response_model=WorkoutTemplateOut)
def save_workout_template(
    template: WorkoutTemplateIn,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> WorkoutTemplateOut:
    authorize(user_id, current)
    _require_user(db, user_id)
    result = upsert_workout_template(
        db, user_id, template.name, [e.model_dump(exclude_none=True) for e in template.exercises]
    )
    db.commit()
    return result


@router.post("/users/{user_id}/workout-templates/seed")
def seed_templates(
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> dict:
    """Create the starter Push/Pull/Legs templates; existing ones are kept."""
    authorize(user_id, current)
    _require_user(db, user_id)
    created = seed_workout_templates(db, user_id)
    db.commit()
    return {"created": created}


@router.delete("/users/{user_id}/workout-templates/{name}", status_code=204)
def remove_workout_template(
    name: str,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
):
    authorize(user_id, current)
    if not delete_workout_template(db, user_id, name):
        raise HTTPException(status_code=404, detail="Workout template not found")
    db.commit()


@router.get("/users/{user_id}/session/today", response_model=TodaySessionOut)
def todays_session(
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> TodaySessionOut:
    authorize(user_id, current)
    _require_user(db, user_id)
    program = service.get_active_program(db, user_id, "training")
    return TodaySessionOut(
        program_id=program.id if program else None,
        text=service.session_text(db, user_id, date.today()),
    )


@router.post("/users/{user_id}/workouts", response_model=WorkoutOut)
def log_workout(
    workout: WorkoutIn,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> WorkoutOut:
    authorize(user_id, current)
    _require_user(db, user_id)
    day = workout.day or date.today()
    if day > date.today():
        raise HTTPException(status_code=422, detail="Cannot log a workout for a future date")
    row = service.add_workout(
        db, user_id, day, workout.session_name,
        [e.model_dump(exclude_none=True) for e in workout.exercises],
        duration_min=workout.duration_min, notes=workout.notes,
    )
    db.commit()
    db.refresh(row)
    return WorkoutOut.model_validate(row, from_attributes=True)


@router.get("/users/{user_id}/workouts", response_model=list[WorkoutOut])
def workouts(
    days: int = Query(14, ge=1, le=365),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> list[WorkoutOut]:
    authorize(user_id, current)
    _require_user(db, user_id)
    return service.list_workouts(db, user_id, since=date.today() - timedelta(days=days - 1))


@router.get("/users/{user_id}/exercises", response_model=list[str])
def exercises(
    days: int = Query(90, ge=1, le=365),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> list[str]:
    authorize(user_id, current)
    _require_user(db, user_id)
    return service.exercise_names(db, user_id, days)


@router.get("/users/{user_id}/exercises/history", response_model=list[ExerciseHistoryRow])
def exercise_history(
    name: str = Query(..., min_length=1, max_length=120, description="Substring match on the exercise name"),
    days: int = Query(90, ge=1, le=365),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
) -> list[ExerciseHistoryRow]:
    authorize(user_id, current)
    _require_user(db, user_id)
    return service.exercise_history(db, user_id, name, days)


@router.delete("/users/{user_id}/workouts/{workout_id}", status_code=204)
def remove_workout(
    workout_id: int,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
    current: str | None = Depends(current_user_id),
):
    authorize(user_id, current)
    if not service.delete_workout(db, user_id, workout_id):
        raise HTTPException(status_code=404, detail="Workout not found")
    db.commit()
