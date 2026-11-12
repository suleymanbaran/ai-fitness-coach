"""Thin HTTP client for the coach API, used by the Telegram bot.

Talking to the API over HTTP (instead of importing app code) means the bot reuses token auth,
rate limiting and every endpoint, and can run as a separate process or on another machine.
"""

from datetime import date
from typing import Any

import httpx


class CoachAPIError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class CoachAPI:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 120.0,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport)

    def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise CoachAPIError(0, f"cannot reach the coach API ({type(exc).__name__})") from exc
        if response.status_code == 204:
            return None
        if response.is_error:
            try:
                detail = response.json().get("detail", "")
            except ValueError:
                detail = ""
            raise CoachAPIError(response.status_code, str(detail) or response.text[:200])
        return response.json()

    def ask(self, user_id: str, question: str, category: str | None = None) -> dict:
        body: dict[str, Any] = {"user_id": user_id, "question": question, "channel": "telegram"}
        if category:
            body["category"] = category
        return self._request("POST", "/ask", json=body)

    def profile(self, user_id: str) -> dict:
        return self._request("GET", f"/users/{user_id}")

    def log_day(self, user_id: str, day: date, **metrics) -> dict:
        return self._request("PUT", f"/users/{user_id}/days/{day.isoformat()}", json=metrics)

    def sync(self, user_id: str, days: int = 7) -> list[dict]:
        return self._request("POST", f"/users/{user_id}/sync", params={"days": days})

    def intake(self, user_id: str, days: int = 1) -> list[dict]:
        return self._request("GET", f"/users/{user_id}/intake", params={"days": days})

    def meals(self, user_id: str, day: date | None = None) -> list[dict]:
        params = {"day": day.isoformat()} if day else {}
        return self._request("GET", f"/users/{user_id}/meals", params=params)

    def memories(self, user_id: str) -> list[dict]:
        return self._request("GET", f"/users/{user_id}/memories")

    def forget_memory(self, user_id: str, kind: str, key: str) -> None:
        self._request("DELETE", f"/users/{user_id}/memories/{kind}/{key}")

    def programs(self, user_id: str, status: str | None = None) -> list[dict]:
        params = {"status": status} if status else {}
        return self._request("GET", f"/users/{user_id}/programs", params=params)

    def generate_program(self, user_id: str, kind: str, brief: str | None = None) -> dict:
        return self._request("POST", f"/users/{user_id}/programs/generate", json={"kind": kind, "brief": brief})

    def plan_week(self, user_id: str, brief: str | None = None) -> dict:
        return self._request("POST", f"/users/{user_id}/plans/weekly", json={"brief": brief})

    def activate_program(self, user_id: str, program_id: int) -> dict:
        return self._request("POST", f"/users/{user_id}/programs/{program_id}/activate")

    def review_program(self, user_id: str, program_id: int, regenerate: bool = False) -> dict:
        return self._request(
            "POST", f"/users/{user_id}/programs/{program_id}/review", params={"regenerate": str(regenerate).lower()}
        )

    def today_session(self, user_id: str) -> dict:
        return self._request("GET", f"/users/{user_id}/session/today")

    def workouts(self, user_id: str, days: int = 7) -> list[dict]:
        return self._request("GET", f"/users/{user_id}/workouts", params={"days": days})
