"""Shared FastAPI dependencies for app.state singletons.

Lives outside app.main so feature routers (programs, integrations) can depend on them
without a circular import. app.main re-exports these names; tests override them there.
"""

from fastapi import Request

from app.rag.embeddings import Embedder
from app.rag.generator import CoachClient
from app.rag.vector_store import ProtocolStore


def get_embedder(request: Request) -> Embedder:
    return request.app.state.embedder


def get_coach(request: Request) -> CoachClient:
    return request.app.state.coach


def get_store(request: Request) -> ProtocolStore:
    return request.app.state.store
