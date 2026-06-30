import asyncio
import os
import sys
import types
import unittest

import reactions


def _install_bridge_import_stubs():
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientSession = object
    sys.modules["aiohttp"] = aiohttp

    telethon = types.ModuleType("telethon")
    events = types.ModuleType("telethon.events")
    utils = types.ModuleType("telethon.utils")
    errors = types.ModuleType("telethon.errors")
    sessions = types.ModuleType("telethon.sessions")
    tl = types.ModuleType("telethon.tl")
    tl_types = types.ModuleType("telethon.tl.types")
    tl_functions = types.ModuleType("telethon.tl.functions")
    messages = types.ModuleType("telethon.tl.functions.messages")

    class TelegramClient:
        def __init__(self, *args, **kwargs):
            pass

        def on(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

    class Event:
        def __init__(self, *args, **kwargs):
            pass

    class StringSession:
        def __init__(self, *args, **kwargs):
            pass

    class FloodWaitError(Exception):
        seconds = 0

    class ReactionEmoji:
        def __init__(self, emoticon=None):
            self.emoticon = emoticon

    class ReactionCustomEmoji:
        def __init__(self, document_id=None):
            self.document_id = document_id

    class DocumentAttributeCustomEmoji:
        def __init__(self, alt=None):
            self.alt = alt

    class InputPeerUser:
        def __init__(self, user_id, access_hash):
            self.user_id = user_id
            self.access_hash = access_hash

    class InputPeerChannel:
        def __init__(self, channel_id, access_hash):
            self.channel_id = channel_id
            self.access_hash = access_hash

    class InputPeerChat:
        def __init__(self, chat_id):
            self.chat_id = chat_id

    class Request:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    for name in (
        "NewMessage", "Album", "MessageEdited", "Raw", "MessageDeleted",
        "MessageRead", "UserUpdate",
    ):
        setattr(events, name, Event)

    def get_peer_id(peer):
        return getattr(peer, "user_id", peer)

    def get_display_name(entity):
        return getattr(entity, "name", "")

    utils.get_peer_id = get_peer_id
    utils.get_display_name = get_display_name
    errors.FloodWaitError = FloodWaitError
    sessions.StringSession = StringSession
    telethon.TelegramClient = TelegramClient
    telethon.events = events
    telethon.utils = utils

    tl_types.InputPeerUser = InputPeerUser
    tl_types.InputPeerChannel = InputPeerChannel
    tl_types.InputPeerChat = InputPeerChat
    tl_types.UpdateMessageReactions = type("UpdateMessageReactions", (), {})
    tl_types.ReactionEmoji = ReactionEmoji
    tl_types.ReactionCustomEmoji = ReactionCustomEmoji
    tl_types.DocumentAttributeCustomEmoji = DocumentAttributeCustomEmoji
    tl_types.SendMessageTypingAction = type("SendMessageTypingAction", (), {})
    tl_types.SendMessageRecordAudioAction = type("SendMessageRecordAudioAction", (), {})
    tl_types.SendMessageRecordRoundAction = type("SendMessageRecordRoundAction", (), {})
    tl_types.SendMessageCancelAction = type("SendMessageCancelAction", (), {})

    messages.SendReactionRequest = Request
    messages.SetTypingRequest = Request
    messages.GetUnreadReactionsRequest = Request
    messages.GetCustomEmojiDocumentsRequest = Request

    sys.modules["telethon"] = telethon
    sys.modules["telethon.events"] = events
    sys.modules["telethon.utils"] = utils
    sys.modules["telethon.errors"] = errors
    sys.modules["telethon.sessions"] = sessions
    sys.modules["telethon.tl"] = tl
    sys.modules["telethon.tl.types"] = tl_types
    sys.modules["telethon.tl.functions"] = tl_functions
    sys.modules["telethon.tl.functions.messages"] = messages


needs_stubs = False
try:
    import aiohttp  # noqa: F401
    import telethon  # noqa: F401
except ModuleNotFoundError:
    needs_stubs = True

required_env = ("TG_API_ID", "TG_API_HASH", "TG_SESSION", "VK_TOKEN", "VK_GROUP_ID", "VK_OWNER_ID")
if not all(os.environ.get(name) for name in required_env):
    needs_stubs = True
    os.environ.setdefault("TG_API_ID", "1")
    os.environ.setdefault("TG_API_HASH", "test")
    os.environ.setdefault("TG_SESSION", "test")
    os.environ.setdefault("VK_TOKEN", "test")
    os.environ.setdefault("VK_GROUP_ID", "1")
    os.environ.setdefault("VK_OWNER_ID", "20")

if needs_stubs:
    _install_bridge_import_stubs()

import bridge
from vk import VKError


class FakeStore:
    def __init__(self):
        self.links = {(10, 101): [20, 7]}
        self.reverse_links = {(20, 7): [10, 101]}
        self.reactions = {}
        self.fallbacks = {}

    def vk_for_tg(self, chat, msg_id):
        return self.links.get((chat, msg_id))

    def tg_for_vk(self, peer, cmid):
        return self.reverse_links.get((peer, cmid))

    def get_tg_reaction(self, chat, msg_id):
        return self.reactions.get((chat, msg_id))

    def set_tg_reaction(self, chat, msg_id, emoji):
        key = (chat, msg_id)
        if emoji is None:
            self.reactions.pop(key, None)
        else:
            self.reactions[key] = emoji

    def get_tg_reaction_fallback(self, chat, msg_id):
        return self.fallbacks.get((chat, msg_id))

    def set_tg_reaction_fallback(self, chat, msg_id, vk_peer=None, vk_cmid=None):
        key = (chat, msg_id)
        if vk_peer is None:
            self.fallbacks.pop(key, None)
        else:
            self.fallbacks[key] = [vk_peer, vk_cmid]


class FailingReactionVK:
    def __init__(self):
        self.sent_reactions = []
        self.sent_messages = []
        self.deleted_reactions = []

    async def send_reaction(self, peer, cmid, reaction_id):
        self.sent_reactions.append((peer, cmid, reaction_id))
        raise VKError("unsupported reaction")

    async def delete_reaction(self, peer, cmid):
        self.deleted_reactions.append((peer, cmid))

    async def send(self, peer, text=None, attachment=None, reply_to_cmid=None):
        self.sent_messages.append((peer, text, attachment, reply_to_cmid))
        return 99

    async def get_message_cmid(self, message_id):
        return 8

    async def delete(self, peer, cmid):
        pass


class FakeTG:
    def __init__(self):
        self.edits = []

    async def edit_message(self, entity, msg_id, text):
        self.edits.append((entity, msg_id, text))


class BridgeHelperTests(unittest.TestCase):
    def test_wall_links(self):
        match = bridge._wall_link_match(
            "https://vk.com/feed?w=wall-123_456"
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.groups(), ("-123", "456"))

    def test_best_video_url(self):
        self.assertEqual(
            bridge._best_vk_video_url({
                "files": {
                    "mp4_360": "small.mp4",
                    "mp4_1080": "large.mp4",
                    "hls": "playlist.m3u8",
                }
            }),
            "large.mp4",
        )

    def test_direct_video_links(self):
        self.assertEqual(
            bridge._video_link_parts("https://vk.com/video-123_456"),
            (-123, 456, None),
        )
        self.assertEqual(
            bridge._video_link_parts(
                "https://vk.com/feed?z=video12_34%2Fabc"
            ),
            (12, 34, None),
        )
        self.assertEqual(
            bridge._video_link_parts("https://vk.com/clip-77_88"),
            (-77, 88, None),
        )

    def test_nail_reaction_uses_community_safe_fallback(self):
        self.assertEqual(reactions.vk_to_emoji(36), "💅")
        self.assertEqual(reactions.emoji_to_vk("💅"), 15)

    def test_emoji_variation_selector_is_normalized(self):
        # ❤ и ❤️ (с U+FE0F) должны давать один и тот же VK id
        self.assertEqual(reactions.emoji_to_vk("❤"), reactions.emoji_to_vk("❤️"))
        self.assertEqual(reactions.emoji_to_vk("❤️"), 1)

    def test_aliases_only_point_to_known_vk_ids(self):
        # любой алиас должен указывать на id, который мы умеем показать обратно
        for emoji, vk_id in reactions.EMOJI_TO_VK.items():
            self.assertIn(
                vk_id, reactions.VK_TO_EMOJI,
                msg=f"{emoji!r} маппится на неизвестный VK id {vk_id}",
            )

    def test_custom_premium_reaction_is_sent_as_vk_reply_message(self):
        old_store, old_vk = bridge.store, bridge.vk
        fake_store = FakeStore()
        fake_vk = FailingReactionVK()
        bridge.store = fake_store
        bridge.vk = fake_vk
        try:
            # премиум-эмодзи: VK реакцией такое не покажет -> ждём сообщение-ответ
            asyncio.run(
                bridge._sync_tg_reaction_emoji(10, 101, "🐸", force_message=True)
            )
        finally:
            bridge.store = old_store
            bridge.vk = old_vk

        self.assertEqual(fake_vk.sent_reactions, [])  # send_reaction не вызывался
        self.assertEqual(fake_vk.sent_messages, [(20, "🐸", None, 7)])
        self.assertEqual(fake_store.get_tg_reaction_fallback(10, 101), [20, 8])

    def test_tg_reaction_falls_back_to_vk_reply_message(self):
        old_store, old_vk = bridge.store, bridge.vk
        fake_store = FakeStore()
        fake_vk = FailingReactionVK()
        bridge.store = fake_store
        bridge.vk = fake_vk
        try:
            asyncio.run(bridge._sync_tg_reaction_emoji(10, 101, "💅"))
        finally:
            bridge.store = old_store
            bridge.vk = old_vk

        self.assertEqual(fake_vk.sent_reactions, [(20, 7, 15)])
        self.assertEqual(fake_vk.sent_messages, [(20, "💅", None, 7)])
        self.assertEqual(fake_store.get_tg_reaction(10, 101), "💅")
        self.assertEqual(fake_store.get_tg_reaction_fallback(10, 101), [20, 8])

    def test_vk_edit_uses_short_cmid_field(self):
        old_store, old_tg, old_tg_entity = bridge.store, bridge.tg, bridge._tg_entity
        fake_store = FakeStore()
        fake_tg = FakeTG()

        async def fake_tg_entity(chat):
            return f"entity:{chat}"

        bridge.store = fake_store
        bridge.tg = fake_tg
        bridge._tg_entity = fake_tg_entity
        try:
            asyncio.run(bridge._handle_vk_edit({
                "from_id": bridge.config.VK_OWNER_ID,
                "peer_id": 20,
                "cmid": 7,
                "text": "edited",
            }))
        finally:
            bridge.store = old_store
            bridge.tg = old_tg
            bridge._tg_entity = old_tg_entity

        self.assertEqual(fake_tg.edits, [("entity:10", 101, "edited")])


if __name__ == "__main__":
    unittest.main()
