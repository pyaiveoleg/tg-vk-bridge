"""Загрузка конфигурации из переменных окружения / .env."""
import os
import re

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(
            f"Не задана переменная окружения {name}. "
            f"Скопируй .env.example в .env и заполни (см. README)."
        )
    return val


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _parse_whitelist(raw: str):
    """Разобрать список диалогов в (множество id, множество @username).

    Элементы разделяются запятой/пробелом/точкой с запятой. Поддерживаются:
    числовые id (123456, -1001234567) и юзернеймы (@user или user).
    """
    ids, names = set(), set()
    for part in re.split(r"[,;\s]+", raw.strip()):
        if not part:
            continue
        p = part.lstrip("@")
        if p.lstrip("-").isdigit():
            ids.add(int(p))
        else:
            names.add(p.lower())
    return ids, names


# --- Telegram (userbot, твой личный аккаунт) ---
TG_API_ID = int(_require("TG_API_ID"))
TG_API_HASH = _require("TG_API_HASH")
TG_SESSION = os.environ.get("TG_SESSION", "")  # StringSession, получить через login.py

# --- VK (бот-сообщество) ---
VK_TOKEN = _require("VK_TOKEN")          # access_token сообщества
VK_GROUP_ID = int(_require("VK_GROUP_ID"))  # числовой id сообщества
VK_OWNER_ID = int(_require("VK_OWNER_ID"))  # твой личный VK user id (куда слать и от кого принимать)

# --- Поведение ---
FORWARD_GROUPS = _flag("FORWARD_GROUPS", False)  # пересылать ли групповые чаты/каналы TG
DATA_DIR = os.environ.get("DATA_DIR", ".")        # где хранить store.json
REACTION_POLL_INTERVAL = float(os.environ.get("REACTION_POLL_INTERVAL", "10"))
REACTION_POLL_MESSAGES = int(os.environ.get("REACTION_POLL_MESSAGES", "50"))
READ_POLL_INTERVAL = float(os.environ.get("READ_POLL_INTERVAL", "3"))
SYNC_READS = _flag("SYNC_READS", True)
SYNC_TYPING = _flag("SYNC_TYPING", True)
VK_VIDEO_MAX_MB = int(os.environ.get("VK_VIDEO_MAX_MB", "200"))

# Белый список диалогов: если задан — обрабатываются ТОЛЬКО эти чаты,
# если пуст — обрабатываются все (прежнее поведение).
TG_WHITELIST_IDS, TG_WHITELIST_NAMES = _parse_whitelist(os.environ.get("TG_WHITELIST", ""))
TG_WHITELIST_ACTIVE = bool(TG_WHITELIST_IDS or TG_WHITELIST_NAMES)
