"""Run the Telegram coach bot with long polling (no public URL or webhook needed).

Usage: python -m scripts.telegram_bot

Requires in .env: TELEGRAM_BOT_TOKEN (from @BotFather), TELEGRAM_API_TOKEN
(from: python -m scripts.issue_token <user>), TELEGRAM_ALLOWED_CHAT_ID (your chat id,
e.g. from @userinfobot), and optionally TELEGRAM_BOUND_USER_ID / TELEGRAM_API_URL.
"""

import argparse

from telegram import Update
from telegram.ext import ApplicationBuilder

from app.config import get_settings
from app.telegram.api_client import CoachAPI
from app.telegram.bot import CoachBot


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram bot for the fitness coach")
    parser.parse_args()
    settings = get_settings()
    if not settings.telegram_bot_token:
        parser.error("TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather and add it to .env.")
    if not settings.telegram_allowed_chat_id:
        parser.error(
            "TELEGRAM_ALLOWED_CHAT_ID is not set. Without it the bot would serve anyone who finds it; "
            "set it to your own chat id."
        )

    api = CoachAPI(settings.telegram_api_url, settings.telegram_api_token)
    bot = CoachBot(api, settings.telegram_bound_user_id, settings.telegram_allowed_chat_id)
    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    bot.register(application)
    print(f"Polling Telegram for chat {settings.telegram_allowed_chat_id}, bound to user '{settings.telegram_bound_user_id}'...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
