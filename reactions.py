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

# обратное соответствие эмодзи -> reaction_id VK
EMOJI_TO_VK = {v: k for k, v in VK_TO_EMOJI.items()}
# Некоторые реакции пользователь VK может поставить на сообщение сообщества,
# но messages.sendReaction с токеном сообщества отвергает тот же id (code 1009).
# Для TG -> VK используем ближайший доступный аналог, не меняя обратную карту.
EMOJI_TO_VK.update({
    "💅": 15,  # точный id=36 недоступен для отправки сообществом -> 😍
})
# распространённые варианты/алиасы эмодзи, приводим к тем же id
EMOJI_TO_VK.update({
    "❤️": 1,
    "😂": 4,
    "😮": 5,
    "😭": 6,
    "🤬": 7,
})

# на что заменять, если точного соответствия нет
DEFAULT_VK_REACTION = 4     # 👍
DEFAULT_TG_EMOJI = "👍"


def emoji_to_vk(emoji: str) -> int:
    return EMOJI_TO_VK.get(emoji, DEFAULT_VK_REACTION)


def vk_to_emoji(reaction_id: int):
    return VK_TO_EMOJI.get(reaction_id)  # None -> вызывающий подставит дефолт
