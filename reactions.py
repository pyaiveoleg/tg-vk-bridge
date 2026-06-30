"""Соответствие числовых reaction_id VK и эмодзи Telegram.

ВНИМАНИЕ: VK не публикует официальную таблицу id->эмодзи, поэтому карта ниже —
наилучшее предположение. Если какая-то реакция маппится неверно, поправь словарь:
бот логирует неизвестные reaction_id из VK (см. лог "VK reaction: неизвестный id"),
по ним легко выяснить реальное соответствие и дописать сюда.

Эмодзи в TG_REACTIONS должны входить в набор стандартных реакций Telegram,
иначе send_reaction вернёт ошибку.
"""

# reaction_id VK -> эмодзи Telegram
VK_TO_EMOJI = {
    1: "❤",  # done
    2: "🔥",  # done
    4: "👍",  # done
    7: "😭",  # done
    9: "👎",  # done
    10: "👌",
    11: "😁",  # done
    13: "🙏",  # done
    14: "🤩",
    15: "😍",  # done
    17: "🤡",  # done
    18: "🤝",  # done
    20: "😐",  # done
    21: "🗿",  # done
    23: "💔",  # done
    24: "😎",  # done
    27: "😢",  # done
    28: "✅",  # done
    30: "😱",  # done
    32: "👀",  # done
    34: "🌚",  # done
    35: "💯",  # done
    36: "💅",  # done
    38: "💤",  # done
    39: "😈",  # done
    64: "⚡",  # done
}

def _normalize(emoji: str) -> str:
    """Убрать variation selector (U+FE0F), которым отличаются ❤️ и ❤."""
    return (emoji or "").replace("️", "")


# обратное соответствие эмодзи -> reaction_id VK
EMOJI_TO_VK = {_normalize(v): k for k, v in VK_TO_EMOJI.items()}
# Алиасы для эмодзи, которых нет в основной карте, но которые встречаются как
# реакции Telegram. ВАЖНО: маппим только на id, которые ЕСТЬ в VK_TO_EMOJI —
# иначе VK поставит неожиданную реакцию или вернёт ошибку 1009.
EMOJI_TO_VK.update({
    _normalize(k): v for k, v in {
        "💅": 15,  # точный id=36 недоступен для отправки сообществом -> 😍
        "🤣": 11,  # ещё один «смех» -> 😁
        "🥰": 1,   # -> ❤
        "🙂": 11,  # -> 😁
        "🥳": 2,   # -> 🔥
    }.items()
})

# на что заменять, если точного соответствия нет
DEFAULT_VK_REACTION = 4     # 👍
DEFAULT_TG_EMOJI = "👍"


def emoji_to_vk(emoji: str) -> int:
    return EMOJI_TO_VK.get(_normalize(emoji), DEFAULT_VK_REACTION)


def vk_to_emoji(reaction_id: int):
    return VK_TO_EMOJI.get(reaction_id)  # None -> вызывающий подставит дефолт
