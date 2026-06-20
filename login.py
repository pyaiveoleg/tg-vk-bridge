"""Одноразовый вход в Telegram-аккаунт: печатает строку TG_SESSION.

Запусти один раз локально:  python login.py
Введи номер телефона и код из Telegram (и пароль 2FA, если включён).
Скопируй полученную строку в .env как TG_SESSION=...
"""
import asyncio
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = os.environ.get("TG_API_ID")
API_HASH = os.environ.get("TG_API_HASH")

if not API_ID or not API_HASH:
    raise SystemExit("Сначала задай TG_API_ID и TG_API_HASH в .env (получить на https://my.telegram.org).")


async def main():
    async with TelegramClient(
        StringSession(), 
        int(API_ID), 
        API_HASH,
        lang_code="ru", # видимо стали обязательными
        system_lang_code="ru-RU" #  видимо стали обязательными
    ) as client:
        me = await client.get_me()
        print("\nУспешный вход как:", me.first_name, "(id", str(me.id) + ")")
        print("\n=== Вставь это в .env как TG_SESSION ===\n")
        print(client.session.save())
        print("\n========================================\n")


if __name__ == "__main__":
    asyncio.run(main())
