"""Shared HTTP helpers for the Streamlit UI. The UI is a plain API client, like the Telegram bot.

Home (streamlit_app.py) stores the signed-in token and selected profile in st.session_state;
every page reads them through require_user().
"""

import os

import httpx
import streamlit as st

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")


def token() -> str:
    return st.session_state.get("token", "")


def auth_headers(t: str | None = None) -> dict[str, str]:
    t = token() if t is None else t
    return {"Authorization": f"Bearer {t}"} if t else {}


def api_call(method: str, path: str, **kwargs) -> httpx.Response:
    return httpx.request(method, f"{API_URL}{path}", headers=auth_headers(), timeout=90, **kwargs)


def error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return response.text[:200]
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(f"{'.'.join(map(str, e['loc'][1:]))}: {e['msg']}" for e in detail)
    return str(detail)


def fail(response: httpx.Response, action: str) -> None:
    st.error(f"{action} failed ({response.status_code}): {error_detail(response)}")


# The token is part of every cache key, so one browser session never sees another's cached data.
@st.cache_data(ttl=60)
def fetch_users(t: str) -> list[dict]:
    response = httpx.get(f"{API_URL}/users", headers=auth_headers(t), timeout=10)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=60)
def fetch_user(user_id: str, t: str) -> dict:
    response = httpx.get(f"{API_URL}/users/{user_id}", headers=auth_headers(t), timeout=10)
    response.raise_for_status()
    return response.json()


def refresh_caches() -> None:
    fetch_users.clear()
    fetch_user.clear()


def require_user() -> str:
    """Pages other than Home depend on the profile chosen there (session_state spans pages)."""
    user_id = st.session_state.get("user_id")
    if not user_id:
        st.info("Choose your profile on the Home page first.")
        st.stop()
    return user_id


def ask_coach(user_id: str, question: str, category: str = "all") -> dict:
    payload: dict = {"user_id": user_id, "question": question, "channel": "web"}
    if category != "all":
        payload["category"] = category
    response = api_call("POST", "/ask", json=payload)
    response.raise_for_status()
    return response.json()
