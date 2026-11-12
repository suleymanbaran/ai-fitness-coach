"""Telegram handlers for the coach.

Design notes:
- The bot is a thin client over the REST API (see api_client.py), so web and Telegram share
  history, memories, rate limits and auth.
- Only the chat id in TELEGRAM_ALLOWED_CHAT_ID is served; anyone else is rejected. Without it
  the bot serves nobody — a stranger who finds the bot must not reach the user's health data.
- Replies use HTML parse mode (only & < > need escaping) and are chunked at Telegram's 4096-char
  limit on paragraph boundaries.
"""

import asyncio
import html
from collections.abc import Callable

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.telegram.api_client import CoachAPI, CoachAPIError

TELEGRAM_MESSAGE_LIMIT = 4096


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split long answers on paragraph, then line boundaries; hard-cut only as a last resort."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


class CoachBot:
    def __init__(self, api: CoachAPI, user_id: str, allowed_chat_id: int | None) -> None:
        self.api = api
        self.user_id = user_id
        self.allowed_chat_id = allowed_chat_id

    def register(self, application: Application) -> None:
        application.add_handler(CommandHandler("start", self.cmd_start))
        application.add_handler(CommandHandler("today", self.cmd_today))
        application.add_handler(CommandHandler("sync", self.cmd_sync))
        application.add_handler(CommandHandler("meals", self.cmd_meals))
        application.add_handler(CommandHandler("workout", self.cmd_workout))
        application.add_handler(CommandHandler("plan", self.cmd_plan))
        application.add_handler(CommandHandler("review", self.cmd_review))
        application.add_handler(CommandHandler("memory", self.cmd_memory))
        application.add_handler(CommandHandler("forget", self.cmd_forget))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))

    # ---------- plumbing ----------

    async def _reply(self, update: Update, text: str) -> None:
        for chunk in split_message(text):
            await update.effective_message.reply_text(chunk, parse_mode=ParseMode.HTML)

    async def _authorized(self, update: Update) -> bool:
        if self.allowed_chat_id is not None and update.effective_chat.id == self.allowed_chat_id:
            return True
        await self._reply(
            update,
            escape_html(
                "Not authorized. This bot serves a single Telegram chat: set TELEGRAM_ALLOWED_CHAT_ID "
                "to your chat id in the bot's environment."
            ),
        )
        return False

    async def _call(self, update: Update, fn: Callable[..., object], *args, **kwargs) -> object | None:
        """Run a blocking API call off the event loop and turn failures into a reply."""
        await update.effective_chat.send_action(ChatAction.TYPING)
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except CoachAPIError as exc:
            hint = " Check TELEGRAM_API_TOKEN." if exc.status_code == 401 else ""
            await self._reply(update, escape_html(f"Coach API error {exc.status_code}: {exc.detail}.{hint}"))
        except Exception as exc:  # unreachable API, bugs: report, never crash the poller
            await self._reply(update, escape_html(f"Something went wrong: {type(exc).__name__}: {exc}"))
        return None

    # ---------- handlers ----------

    async def cmd_start(self, update: Update, context) -> None:
        if not await self._authorized(update):
            return
        profile = await self._call(update, self.api.profile, self.user_id)
        if profile is None:
            return
        await self._reply(update, escape_html(
            f"Coach for {profile['name']} (goal: {profile['goal']}, user id '{self.user_id}').\n\n"
            "Just write or speak your mind — I log metrics, meals and workouts from plain text "
            "(\"slept 7h, HRV 55\", \"2 eggs + simit for breakfast\", \"benched 80x5x3\").\n\n"
            "Commands:\n"
            "/today — latest metrics, intake and how the day compares to baseline\n"
            "/meals — today's food log\n"
            "/workout — today's planned session\n"
            "/plan — full weekly plan (training + cardio + nutrition); /plan training|nutrition [brief] for one\n"
            "/review [regenerate] — review the active training program\n"
            "/sync — pull the last 7 days from Fitbit/FatSecret\n"
            "/memory — what I remember about you\n"
            "/forget <kind> <key> — delete one memory"
        ))

    async def handle_text(self, update: Update, context) -> None:
        if not await self._authorized(update):
            return
        question = (update.effective_message.text or "").strip()
        if len(question) < 3:
            await self._reply(update, escape_html("Tell me a bit more — what would you like to know or log?"))
            return
        result = await self._call(update, self.api.ask, self.user_id, question[:2000])
        if result is None:
            return
        text = result["answer"]
        if result.get("sources"):
            text += "\n\nSources: " + ", ".join(
                f"[{s['protocol_id']}] {s['title']} ({s['score']:.2f})" for s in result["sources"]
            )
        await self._reply(update, escape_html(text))

    async def cmd_today(self, update: Update, context) -> None:
        if not await self._authorized(update):
            return
        profile = await self._call(update, self.api.profile, self.user_id)
        if profile is None:
            return
        intake = await self._call(update, self.api.intake, self.user_id, 1)
        if intake is None:
            return
        lines: list[str] = []
        days = profile.get("days") or []
        if days:
            latest = days[-1]
            lines.append(
                f"Latest day on file ({latest['date']}):\n"
                f"  HRV {latest['hrv_ms'] if latest['hrv_ms'] is not None else '—'} ms"
                f" (7-day baseline {latest['hrv_baseline_ms'] if latest['hrv_baseline_ms'] is not None else '—'})\n"
                f"  Resting HR {latest['resting_hr_bpm'] if latest['resting_hr_bpm'] is not None else '—'} bpm\n"
                f"  Sleep score {latest['sleep_score'] if latest['sleep_score'] is not None else '—'}"
                + (f", slept {latest['sleep_minutes'] // 60}h{latest['sleep_minutes'] % 60:02d}m" if latest.get("sleep_minutes") else "")
                + f"\n  Calories {latest['calories_kcal'] if latest['calories_kcal'] is not None else '—'} kcal"
            )
        else:
            lines.append("No metrics on file yet — sync from your wearable or just tell me how you slept.")
        todays = [d for d in intake if d.get("entries")]
        lines.append(
            f"Today's intake: {todays[0]['kcal']} kcal across {todays[0]['entries']} entries"
            if todays else "Today's intake: nothing logged yet."
        )
        await self._reply(update, escape_html("\n\n".join(lines)))

    async def cmd_sync(self, update: Update, context) -> None:
        if not await self._authorized(update):
            return
        results = await self._call(update, self.api.sync, self.user_id, 7)
        if results is None:
            return
        if not results:
            await self._reply(update, escape_html("No connected providers. Connect Google Health / FatSecret in the web UI first."))
            return
        lines = []
        for r in results:
            status = f"{r['days_updated']} days updated" if not r.get("error") else r["error"]
            if r.get("reconnect"):
                status += " (reconnect in the web UI)"
            lines.append(f"{r['provider']}: {status}")
        await self._reply(update, escape_html("Sync (last 7 days)\n" + "\n".join(lines)))

    async def cmd_meals(self, update: Update, context) -> None:
        if not await self._authorized(update):
            return
        meals = await self._call(update, self.api.meals, self.user_id)
        if meals is None:
            return
        if not meals:
            await self._reply(update, escape_html("Nothing logged to eat today. Tell me what you had — e.g. \"2 eggs + simit for breakfast\"."))
            return
        total = sum(m["kcal"] for m in meals)
        lines = [f"  {m['meal']}: {m['description']} — {m['kcal']} kcal ({m['source']})" for m in meals]
        await self._reply(update, escape_html(f"Today's meals ({total} kcal total)\n" + "\n".join(lines)))

    async def cmd_workout(self, update: Update, context) -> None:
        if not await self._authorized(update):
            return
        session = await self._call(update, self.api.today_session, self.user_id)
        if session is None:
            return
        await self._reply(update, escape_html(session["text"]))

    async def cmd_plan(self, update: Update, context) -> None:
        if not await self._authorized(update):
            return
        args = list(context.args or [])
        kind = args[0] if args and args[0] in ("training", "nutrition") else None
        rest = args[1:] if kind else args
        brief = " ".join(rest) or None

        if kind is None:
            # No kind given: one coherent weekly plan (training + cardio + nutrition), auto-activated.
            result = await self._call(update, self.api.plan_week, self.user_id, brief)
            if result is None:
                return
            training, nutrition = result["training"], result["nutrition"]
            await self._reply(update, escape_html(
                f"Weekly plan ready!\n\n"
                f"TRAINING — {training['name']} (v{training['version']})\n{training['summary']}\n\n"
                f"NUTRITION — {nutrition['name']} (v{nutrition['version']})\n{nutrition['summary']}\n\n"
                "Both are active. If something doesn't fit your week, just tell me and I'll revise the plan."
            ))
            return

        program = await self._call(update, self.api.generate_program, self.user_id, kind, brief)
        if program is None:
            return
        activated = await self._call(update, self.api.activate_program, self.user_id, program["id"])
        if activated is None:
            await self._reply(update, escape_html(f"Generated '{program['name']}' (v{program['version']}) but activation failed."))
            return
        await self._reply(update, escape_html(
            f"New {kind} program active: {activated['name']} (v{activated['version']})\n\n{activated['summary']}"
        ))

    async def cmd_review(self, update: Update, context) -> None:
        if not await self._authorized(update):
            return
        args = [a.lower() for a in (context.args or [])]
        regenerate = "regenerate" in args
        kind = "nutrition" if "nutrition" in args else "training"
        programs = await self._call(update, self.api.programs, self.user_id, "active")
        if programs is None:
            return
        matching = [p for p in programs if p["kind"] == kind]
        if not matching:
            await self._reply(update, escape_html(f"No active {kind} program. Create one with /plan {kind}."))
            return
        review = await self._call(update, self.api.review_program, self.user_id, matching[0]["id"], regenerate)
        if review is None:
            return
        text = review["assessment"]
        if review.get("regenerated_version"):
            text += f"\n\nRegenerated as v{review['regenerated_version']} (draft). Activate it in the web UI or with /plan."
        await self._reply(update, escape_html(text))

    async def cmd_memory(self, update: Update, context) -> None:
        if not await self._authorized(update):
            return
        memories = await self._call(update, self.api.memories, self.user_id)
        if memories is None:
            return
        if not memories:
            await self._reply(update, escape_html("I don't have any durable memories about you yet."))
            return
        lines = [
            f"  [{m['kind']}] {m['key']}: {m['value']}" + (" (pinned)" if m["pinned"] else "") + f" ({m['source']})"
            for m in memories
        ]
        await self._reply(update, escape_html("What I remember:\n" + "\n".join(lines)))

    async def cmd_forget(self, update: Update, context) -> None:
        if not await self._authorized(update):
            return
        args = list(context.args or [])
        if len(args) != 2:
            await self._reply(update, escape_html("Usage: /forget <kind> <key> — see /memory for what exists."))
            return
        await self._call(update, self.api.forget_memory, self.user_id, args[0], args[1])
        await self._reply(update, escape_html(f"Forgot {args[0]}/{args[1]}."))
