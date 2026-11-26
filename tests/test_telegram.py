"""Telegram layer: CoachAPI over MockTransport, message chunking, and bot handlers with a fake API."""

import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.telegram.api_client import CoachAPI, CoachAPIError
from app.telegram.bot import CoachBot, escape_html, split_message

TODAY = date.today()


# ---------- CoachAPI ----------


def make_api(handler) -> CoachAPI:
    return CoachAPI("http://coach", token="tok-123", transport=httpx.MockTransport(handler))


def test_ask_sends_telegram_channel_and_auth():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        seen["path"] = request.url.path
        return httpx.Response(200, json={"answer": "Rest.", "sources": [], "cached_tokens": 10})

    result = make_api(handler).ask("me", "Should I train?")
    assert seen["auth"] == "Bearer tok-123"
    assert seen["path"] == "/ask"
    assert seen["body"] == {"user_id": "me", "question": "Should I train?", "channel": "telegram"}
    assert result["answer"] == "Rest."


def test_errors_become_coach_api_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/nobody":
            return httpx.Response(404, json={"detail": "User nobody not found"})
        return httpx.Response(204)

    api = make_api(handler)
    with pytest.raises(CoachAPIError) as exc:
        api.profile("nobody")
    assert exc.value.status_code == 404 and "not found" in exc.value.detail
    assert api.forget_memory("me", "fact", "x") is None  # 204 -> None


def test_transport_error_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(CoachAPIError) as exc:
        make_api(handler).profile("me")
    assert exc.value.status_code == 0


def test_query_and_json_shapes():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(200, json=[])

    api = make_api(handler)
    api.sync("me", days=7)
    api.intake("me", days=1)
    api.meals("me", day=TODAY)
    api.review_program("me", 3, regenerate=True)
    assert seen == [
        ("POST", "/users/me/sync", {"days": "7"}),
        ("GET", "/users/me/intake", {"days": "1"}),
        ("GET", "/users/me/meals", {"day": TODAY.isoformat()}),
        ("POST", "/users/me/programs/3/review", {"regenerate": "true"}),
    ]


# ---------- message chunking ----------


def test_short_messages_pass_through():
    assert split_message("hello") == ["hello"]
    assert split_message("x" * 4096) == ["x" * 4096]


def test_long_messages_split_on_paragraphs_within_limit():
    text = "\n\n".join(f"paragraph {i} " + "y" * 300 for i in range(20))
    chunks = split_message(text)
    assert all(len(c) <= 4096 for c in chunks)
    assert len(chunks) > 1
    # no content lost (modulo boundary whitespace)
    assert "".join(chunks).replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(" ", "")


def test_unbreakable_text_is_hard_cut():
    chunks = split_message("z" * 9000)
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks) == "z" * 9000


def test_escape_html():
    assert escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


# ---------- bot handlers ----------


class FakeAPI:
    """Stands in for CoachAPI with canned answers and call recording."""

    def __init__(self):
        self.calls = []
        self.error = None
        self.ask_result = {"answer": "Take it easy <b>today</b>.", "sources": [
            {"protocol_id": "rec-hrv-autoregulation", "title": "HRV autoregulation", "score": 0.91}
        ]}
        self.profile_result = {"name": "Suleyman", "goal": "strength", "days": [{
            "date": str(TODAY), "hrv_ms": 55.0, "hrv_baseline_ms": 62.0, "resting_hr_bpm": 56,
            "sleep_score": 71, "sleep_minutes": 430, "calories_kcal": 2400,
        }]}
        self.program_result = {"id": 7, "name": "4-Day Strength", "version": 3, "summary": "Upper/lower split.", "kind": "training"}

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if self.error:
            raise self.error
        return getattr(self, f"{name}_result", None)

    def ask(self, user_id, question, category=None):
        return self._record("ask", user_id, question) or self.ask_result

    def profile(self, user_id):
        self._record("profile", user_id)
        return self.profile_result

    def intake(self, user_id, days=1):
        self._record("intake", user_id, days)
        return [{"date": str(TODAY), "kcal": 1170, "entries": 2}]

    def sync(self, user_id, days=7):
        self._record("sync", user_id, days)
        return [{"provider": "google", "days_updated": 5, "error": None, "reconnect": False}]

    def meals(self, user_id, day=None):
        self._record("meals", user_id)
        return [{"meal": "breakfast", "description": "2 eggs", "kcal": 300, "source": "assistant"}]

    def today_session(self, user_id):
        self._record("today_session", user_id)
        return {"program_id": 7, "text": "Week 1: Lower A"}

    def generate_program(self, user_id, kind, brief=None):
        self._record("generate_program", user_id, kind, brief)
        return self.program_result

    def plan_week(self, user_id, brief=None):
        self._record("plan_week", user_id, brief)
        return {
            "training": self.program_result,
            "nutrition": {**self.program_result, "kind": "nutrition", "name": "Balanced 2400", "version": 2},
        }

    def activate_program(self, user_id, program_id):
        self._record("activate_program", user_id, program_id)
        return self.program_result

    def programs(self, user_id, status=None):
        self._record("programs", user_id, status)
        return [self.program_result]

    def review_program(self, user_id, program_id, regenerate=False):
        self._record("review_program", user_id, program_id, regenerate)
        return {"program_id": program_id, "assessment": "## Assessment\nOn track.", "regenerated_version": 4 if regenerate else None}

    def photo_estimate(self, user_id, image, content_type="image/jpeg"):
        self._record("photo_estimate", user_id, image, content_type)
        return {"items": [
            {"name": "Pilav", "grams": 200, "kcal": 260, "protein_g": 5, "carbs_g": 50, "fat_g": 4, "fiber_g": None},
            {"name": "Tavuk", "grams": 100, "kcal": 165, "protein_g": 31, "carbs_g": None, "fat_g": 4, "fiber_g": 0},
        ], "uncertainty": "Yağ miktarı belirsiz."}

    def log_meal(self, user_id, **meal):
        self._record("log_meal", user_id, meal)
        return {"id": 1, **meal}

    def memories(self, user_id):
        self._record("memories", user_id)
        return [{"kind": "constraint", "key": "bad_knee", "value": "No squats", "source": "user", "pinned": True}]

    def forget_memory(self, user_id, kind, key):
        self._record("forget_memory", user_id, kind, key)


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, parse_mode=None):
        self.replies.append(text)


class FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id
        self.actions = []

    async def send_action(self, action):
        self.actions.append(action)


def make_update(text="", chat_id=42):
    message = FakeMessage(text)
    chat = FakeChat(chat_id)
    return SimpleNamespace(effective_message=message, effective_chat=chat), message


def run(coro):
    return asyncio.run(coro)


def test_unauthorized_chat_is_rejected():
    api = FakeAPI()
    bot = CoachBot(api, "me", allowed_chat_id=999)
    update, message = make_update(chat_id=1)

    run(bot.handle_text(update, SimpleNamespace(args=[])))

    assert "Not authorized" in message.replies[0]
    assert api.calls == []


def test_missing_allowed_chat_id_serves_nobody():
    bot = CoachBot(FakeAPI(), "me", allowed_chat_id=None)
    update, message = make_update()
    run(bot.cmd_today(update, SimpleNamespace(args=[])))
    assert "Not authorized" in message.replies[0]


def test_text_goes_to_ask_with_sources_and_escaping():
    api = FakeAPI()
    bot = CoachBot(api, "me", allowed_chat_id=42)
    update, message = make_update(text="Should I train?")

    run(bot.handle_text(update, SimpleNamespace(args=[])))

    assert api.calls[0][0] == "ask" and api.calls[0][1] == ("me", "Should I train?")
    reply = message.replies[0]
    assert "&lt;b&gt;today&lt;/b&gt;" in reply  # HTML escaped
    assert "[rec-hrv-autoregulation] HRV autoregulation (0.91)" in reply
    assert update.effective_chat.actions  # typing indicator sent


def test_too_short_text_is_not_sent_to_the_llm():
    api = FakeAPI()
    bot = CoachBot(api, "me", allowed_chat_id=42)
    update, message = make_update(text="hi")
    run(bot.handle_text(update, SimpleNamespace(args=[])))
    assert api.calls == [] and "a bit more" in message.replies[0]


def test_today_formats_metrics_and_intake():
    api = FakeAPI()
    bot = CoachBot(api, "me", allowed_chat_id=42)
    update, message = make_update()

    run(bot.cmd_today(update, SimpleNamespace(args=[])))

    reply = message.replies[0]
    assert "HRV 55.0 ms" in reply and "7h10m" in reply and "1170 kcal" in reply


def test_plan_generates_and_activates():
    api = FakeAPI()
    bot = CoachBot(api, "me", allowed_chat_id=42)
    update, message = make_update()

    run(bot.cmd_plan(update, SimpleNamespace(args=["training", "home", "gym"])))

    assert api.calls[0] == ("generate_program", ("me", "training", "home gym"), {})
    assert api.calls[1][0] == "activate_program"
    assert "4-Day Strength (v3)" in message.replies[0]


def test_plan_without_kind_creates_unified_weekly_plan():
    api = FakeAPI()
    bot = CoachBot(api, "me", allowed_chat_id=42)
    update, message = make_update()

    run(bot.cmd_plan(update, SimpleNamespace(args=[])))

    assert api.calls[0] == ("plan_week", ("me", None), {})
    reply = message.replies[0]
    assert "Weekly plan ready" in reply
    assert "4-Day Strength" in reply and "Balanced 2400" in reply

    # A brief without a kind still goes to the weekly flow.
    api2 = FakeAPI()
    bot2 = CoachBot(api2, "me", allowed_chat_id=42)
    update2, _ = make_update()
    run(bot2.cmd_plan(update2, SimpleNamespace(args=["fat", "loss"])))
    assert api2.calls[0] == ("plan_week", ("me", "fat loss"), {})


def test_review_with_regenerate():
    api = FakeAPI()
    bot = CoachBot(api, "me", allowed_chat_id=42)
    update, message = make_update()

    run(bot.cmd_review(update, SimpleNamespace(args=["regenerate"])))

    assert api.calls[-1] == ("review_program", ("me", 7, True), {})
    assert "Regenerated as v4" in message.replies[0]


def test_memory_and_forget():
    api = FakeAPI()
    bot = CoachBot(api, "me", allowed_chat_id=42)

    update, message = make_update()
    run(bot.cmd_memory(update, SimpleNamespace(args=[])))
    assert "[constraint] bad_knee: No squats (pinned) (user)" in message.replies[0]

    update, message = make_update()
    run(bot.cmd_forget(update, SimpleNamespace(args=["constraint", "bad_knee"])))
    assert api.calls[-1] == ("forget_memory", ("me", "constraint", "bad_knee"), {})

    update, message = make_update()
    run(bot.cmd_forget(update, SimpleNamespace(args=["only-one-arg"])))
    assert "Usage:" in message.replies[0]


def test_api_errors_are_replied_not_raised():
    api = FakeAPI()
    api.error = CoachAPIError(401, "Missing or invalid token")
    bot = CoachBot(api, "me", allowed_chat_id=42)
    update, message = make_update(text="Should I train?")

    run(bot.handle_text(update, SimpleNamespace(args=[])))

    assert "Coach API error 401" in message.replies[0]
    assert "TELEGRAM_API_TOKEN" in message.replies[0]


def test_long_answers_are_chunked():
    api = FakeAPI()
    api.ask_result = {"answer": "word " * 2000, "sources": []}  # 10,000 chars
    bot = CoachBot(api, "me", allowed_chat_id=42)
    update, message = make_update(text="tell me everything")

    run(bot.handle_text(update, SimpleNamespace(args=[])))

    assert len(message.replies) >= 3
    assert all(len(r) <= 4096 for r in message.replies)


class FakePhoto:
    def __init__(self, size=1000):
        self.file_size = size

    async def get_file(self):
        return SimpleNamespace(download_as_bytearray=self._download)

    async def _download(self):
        return bytearray(b"jpegbytes")


def photo_update(size=1000):
    update, message = make_update()
    message.photo = [FakePhoto(10), FakePhoto(size)]
    return update, message


def test_photo_is_estimated_then_fixed_and_saved_only_on_confirmation():
    api = FakeAPI()
    bot = CoachBot(api, "me", allowed_chat_id=42)
    update, message = photo_update()
    run(bot.handle_photo(update, SimpleNamespace(args=[])))
    assert api.calls[0][:2] == ("photo_estimate", ("me", b"jpegbytes", "image/jpeg"))
    assert "Henüz kaydedilmedi" in message.replies[0] and "Yağ miktarı belirsiz" in message.replies[0]
    assert [c[0] for c in api.calls] == ["photo_estimate"]  # nothing logged yet

    fix, fix_message = make_update()
    run(bot.cmd_fix(fix, SimpleNamespace(args=["1", "100"])))
    assert "130 kcal" in fix_message.replies[0]

    save, save_message = make_update()
    run(bot.cmd_save(save, SimpleNamespace(args=["ogle"])))
    name, (user, meal), _ = api.calls[-1]
    assert name == "log_meal" and meal["meal"] == "lunch" and meal["kcal"] == 295
    assert meal["carbs_g"] is None and meal["fiber_g"] is None  # unknown stays unknown, not 0
    assert meal["protein_g"] == 33.5 and bot.pending_photo is None


def test_photo_rejects_oversized_and_unauthorized():
    api = FakeAPI()
    bot = CoachBot(api, "me", allowed_chat_id=42)
    update, message = photo_update(size=6 * 1024 * 1024)
    run(bot.handle_photo(update, SimpleNamespace(args=[])))
    assert "5 MB" in message.replies[0] and api.calls == []

    other, other_message = photo_update()
    other.effective_chat.id = 1
    run(bot.handle_photo(other, SimpleNamespace(args=[])))
    assert "Not authorized" in other_message.replies[0] and api.calls == []


@pytest.mark.parametrize("date_arg", ["yarın", "yarin", (TODAY + timedelta(days=1)).isoformat()])
def test_photo_save_accepts_future_date(date_arg):
    api = FakeAPI()
    bot = CoachBot(api, "me", allowed_chat_id=42)
    photo, _ = photo_update()
    run(bot.handle_photo(photo, SimpleNamespace(args=[])))
    save, message = make_update()
    run(bot.cmd_save(save, SimpleNamespace(args=["ogle", date_arg])))
    meal = api.calls[-1][1][1]
    assert meal["date"] == (TODAY + timedelta(days=1)).isoformat()
    assert meal["date"] in message.replies[0]
    assert bot.pending_photo is None


def test_photo_save_invalid_date_retains_pending_estimate():
    api = FakeAPI()
    bot = CoachBot(api, "me", allowed_chat_id=42)
    photo, _ = photo_update()
    run(bot.handle_photo(photo, SimpleNamespace(args=[])))
    save, message = make_update()
    run(bot.cmd_save(save, SimpleNamespace(args=["ogle", "2026-02-30"])))
    assert "Geçersiz tarih" in message.replies[0]
    assert bot.pending_photo is not None
    assert all(call[0] != "log_meal" for call in api.calls)


def test_save_and_fix_without_pending_or_bad_args():
    api = FakeAPI()
    bot = CoachBot(api, "me", allowed_chat_id=42)
    update, message = make_update()
    run(bot.cmd_save(update, SimpleNamespace(args=["ogle"])))
    assert "bekleyen" in message.replies[0] and api.calls == []

    run(bot.handle_photo(*(lambda u_m: (u_m[0], SimpleNamespace(args=[])))(photo_update())))
    update, message = make_update()
    run(bot.cmd_save(update, SimpleNamespace(args=[])))
    run(bot.cmd_fix(update, SimpleNamespace(args=["9", "50"])))
    assert "Öğünü seç" in message.replies[0] and "Kullanım" in message.replies[1]
    assert all(c[0] != "log_meal" for c in api.calls)
