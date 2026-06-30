"""Мост Telegram <-> VK.

TG -> VK: все входящие сообщения (текст, фото, ссылки, голосовые, видео, файлы),
          адресованные тебе в Telegram, пересылаются в диалог с твоим VK-сообществом.
VK -> TG: твои ответы в этом VK-диалоге уходят обратно в нужный TG-чат.

Маршрутизация ответов: у каждого TG-чата есть короткий токен (#abcd) в заголовке.
- reply в VK на сообщение человека  -> ответ уйдёт именно ему;
- просто текст без reply            -> ответ уйдёт в последний активный чат.
"""
import asyncio
import glob
import logging
import os
import re
import shutil
import sys
import tempfile
import uuid

import aiohttp
from telethon import TelegramClient, events, utils
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.types import (
    InputPeerUser, InputPeerChannel, InputPeerChat,
    UpdateMessageReactions, ReactionEmoji, ReactionCustomEmoji,
    DocumentAttributeCustomEmoji,
    SendMessageTypingAction, SendMessageRecordAudioAction,
    SendMessageRecordRoundAction, SendMessageCancelAction,
)
from telethon.tl.functions.messages import (
    SendReactionRequest, SetTypingRequest, GetUnreadReactionsRequest,
    GetCustomEmojiDocumentsRequest,
)

import config
import reactions
from store import Store
from vk import VK, VKError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bridge")

# --- глобальные объекты, инициализируются в main() ---
http: aiohttp.ClientSession = None
vk: VK = None
me_id: int = None  # id нашего TG-аккаунта (для отсева собственных реакций)
store = Store(os.path.join(config.DATA_DIR, "store.json"))
tg_forward_lock = asyncio.Lock()

if not config.TG_SESSION:
    raise SystemExit(
        "Пустой TG_SESSION. Сначала выполни:  python login.py  — и вставь строку в .env"
    )

tg = TelegramClient(StringSession(config.TG_SESSION), config.TG_API_ID, config.TG_API_HASH)

TOKEN_RE = re.compile(r"#([0-9a-f]{4,})")

# Потолок для скачивания VK-вложений: всё читается потоком на диск, но крупные
# файлы лучше отдать ссылкой, чем съесть память/упереться в лимит Telegram.
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 ГБ (лимит загрузки в Telegram)
VIDEO_MAX_BYTES = config.VK_VIDEO_MAX_MB * 1024 * 1024

# Кэш «document_id премиум-эмодзи -> базовый юникод-эмодзи» (alt).
_custom_emoji_alt: dict = {}


def _vk_message_actor(m: dict):
    return m.get("from_id") or m.get("user_id")


def _vk_message_peer(m: dict):
    return m.get("peer_id", config.VK_OWNER_ID)


def _vk_message_cmid(m: dict):
    return m.get("conversation_message_id") or m.get("cmid")


# =========================== TG -> VK ===========================
@tg.on(events.NewMessage(incoming=True))
async def on_tg_message(event):
    try:
        if event.out:  # наши собственные исходящие — пропускаем (защита от петель)
            return
        if event.message.grouped_id:
            return  # альбом целиком обработает events.Album
        if not event.is_private and not config.FORWARD_GROUPS:
            return
        if not await _allowed_by_whitelist(event):
            return
        async with tg_forward_lock:
            await _forward_tg_messages(event, [event.message])
    except Exception as e:  # один сбой не должен ронять userbot
        log.exception("TG->VK failed: %s", e)


@tg.on(events.Album)
async def on_tg_album(event):
    try:
        if any(message.out for message in event.messages):
            return
        if not event.is_private and not config.FORWARD_GROUPS:
            return
        if not await _allowed_by_whitelist(event):
            return
        async with tg_forward_lock:
            await _forward_tg_messages(event, list(event.messages))
    except Exception as e:
        log.exception("TG album->VK failed: %s", e)


async def _forward_tg_messages(event, messages):
    sender = await event.get_sender()
    sender_name = utils.get_display_name(sender) or "Unknown"
    peer = event.chat_id
    token = store.token_for_peer(peer)
    store.set_last_peer(peer)

    try:
        meta = _input_peer_to_meta(await event.get_input_chat())
        if meta:
            store.set_peer_meta(peer, meta)
    except Exception as e:
        log.debug("Не смог сохранить input peer: %s", e)

    header = await _build_header(event, sender_name, token)
    texts = []
    attachments = []
    tmp_dirs = []
    failed = 0
    try:
        for msg in messages:
            text = msg.message or ""
            if text and text not in texts:
                texts.append(text)
            if not msg.media:
                continue
            # Сбой одного вложения не должен терять остальной альбом и текст.
            # У видеокружков обычно одинаковое имя video.mp4. Отдельная
            # директория на сообщение исключает гонку двух быстрых загрузок.
            try:
                tmp_dir = tempfile.mkdtemp(prefix=f"tg-{peer}-{msg.id}-")
                tmp_dirs.append(tmp_dir)
                tmp_path = await msg.download_media(file=tmp_dir)
                attachment = (
                    await _upload_tg_media_to_vk(msg, tmp_path) if tmp_path else None
                )
            except Exception as e:
                log.warning("TG media (chat=%s msg=%s) не переслано: %s", peer, msg.id, e)
                attachment = None
            if attachment:
                attachments.append(attachment)
            else:
                failed += 1

        body = header + (("\n" + "\n".join(texts)) if texts else "")
        if failed:
            body += f"\n⚠️ вложений не переслано: {failed}"
        mid = await vk.send(
            config.VK_OWNER_ID,
            text=body,
            attachment=",".join(attachments) or None,
        )
        try:
            cmid = await vk.get_message_cmid(mid)
            log.info(
                "TG->VK: mid=%s cmid=%s chat=%s messages=%s",
                mid, cmid, peer, [m.id for m in messages],
            )
            if cmid:
                for msg in messages:
                    store.link_messages(
                        peer, msg.id, config.VK_OWNER_ID, cmid,
                        direction="tg_to_vk",
                    )
        except Exception as e:
            log.warning("Не смог получить cmid: %s", e)
    finally:
        for tmp_dir in tmp_dirs:
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def _build_header(event, sender_name, token) -> str:
    header = f"🔵 {sender_name}"
    if not event.is_private:
        chat = await event.get_chat()
        header += f" @ {utils.get_display_name(chat)}"
    header += f"  ·  #{token}"
    return header


# =================== редактирование TG -> VK ===================
@tg.on(events.MessageEdited(incoming=True))
async def on_tg_edit(event):
    try:
        if event.out:
            return
        link = store.vk_for_tg(event.chat_id, event.message.id)
        if not link:
            return  # это сообщение не пересылалось — нечего править
        vk_peer, vk_cmid = link

        sender = await event.get_sender()
        sender_name = utils.get_display_name(sender) or "Unknown"
        token = store.token_for_peer(event.chat_id)
        header = await _build_header(event, sender_name, token)
        text = event.message.message or ""
        body = header + (("\n" + text) if text else "") + "  (ред.)"

        await vk.edit(vk_peer, vk_cmid, body)
    except Exception as e:
        log.warning("TG->VK edit failed: %s", e)


async def _allowed_by_whitelist(event) -> bool:
    """True, если диалог разрешён. Пустой белый список -> разрешено всё."""
    if not config.TG_WHITELIST_ACTIVE:
        return True
    # сначала дешёвая проверка по id (без обращения к сети)
    if event.chat_id in config.TG_WHITELIST_IDS:
        return True
    sid = getattr(event, "sender_id", None)
    if sid is not None and sid in config.TG_WHITELIST_IDS:
        return True
    # затем проверка по @username
    if config.TG_WHITELIST_NAMES:
        names = set()
        try:
            sender = await event.get_sender()
            if getattr(sender, "username", None):
                names.add(sender.username.lower())
        except Exception:
            pass
        if not event.is_private:
            try:
                chat = await event.get_chat()
                if getattr(chat, "username", None):
                    names.add(chat.username.lower())
            except Exception:
                pass
        if names & config.TG_WHITELIST_NAMES:
            return True
    return False


async def _upload_tg_media_to_vk(msg, path: str):
    peer = config.VK_OWNER_ID
    try:
        if msg.photo:
            return await vk.upload_photo(peer, path)
        if msg.voice:  # голосовое (ogg/opus) -> голосовое в VK
            return await vk.upload_doc(peer, path, doc_type="audio_message")
        # видео, кружки, гифки, документы, аудио, стикеры -> документ
        return await vk.upload_doc(peer, path, doc_type="doc")
    except Exception as e:
        # не только VKError: сервер загрузки может вернуть битый JSON (KeyError)
        # или оборвать соединение — это не должно ронять пересылку всего альбома.
        log.warning("VK upload failed: %s", e)
        return None


# =================== реакции TG -> VK ===================
@tg.on(events.Raw(types=UpdateMessageReactions))
async def on_tg_reaction(update):
    try:
        await _process_tg_reaction_update(update)
    except Exception as e:
        log.warning("TG->VK reaction failed: %s", e)


def _extract_other_reaction(message_reactions):
    """Реакция собеседника (исключая нашу). Возвращает (kind, value):

    ('emoji', emoticon)        — стандартная реакция Telegram;
    ('custom', document_id)    — премиум-эмодзи (VK такие реакции не умеет);
    None                       — реакции нет.
    """
    def classify(reaction):
        if isinstance(reaction, ReactionEmoji):
            return ("emoji", reaction.emoticon)
        if isinstance(reaction, ReactionCustomEmoji):
            return ("custom", reaction.document_id)
        return None

    recent = getattr(message_reactions, "recent_reactions", None) or []
    for item in reversed(recent):
        if utils.get_peer_id(item.peer_id) == me_id:
            continue
        found = classify(item.reaction)
        if found:
            return found

    # Telegram иногда не присылает recent_reactions, пока сообщение не открыто.
    # В личном чате из агрегированных счётчиков можно вычесть нашу реакцию.
    for item in getattr(message_reactions, "results", None) or []:
        mine = 1 if getattr(item, "chosen_order", None) is not None else 0
        if getattr(item, "count", 0) > mine:
            found = classify(item.reaction)
            if found:
                return found
    return None


async def _resolve_custom_emoji(document_id) -> str:
    """Свести премиум-эмодзи к его базовому юникод-эмодзи (alt)."""
    if document_id in _custom_emoji_alt:
        return _custom_emoji_alt[document_id]
    alt = None
    try:
        docs = await tg(GetCustomEmojiDocumentsRequest(document_id=[document_id]))
        for doc in docs or []:
            for attr in getattr(doc, "attributes", None) or []:
                if isinstance(attr, DocumentAttributeCustomEmoji) and attr.alt:
                    alt = attr.alt
                    break
            if alt:
                break
    except Exception as e:
        log.debug("Не удалось раскрыть премиум-эмодзи %s: %s", document_id, e)
    alt = alt or "❤"  # видимый запасной вариант, чтобы реакцию не потерять
    _custom_emoji_alt[document_id] = alt
    return alt


async def _process_tg_reaction_update(update):
    chat_id = utils.get_peer_id(update.peer)
    await _sync_tg_reaction(chat_id, update.msg_id, update.reactions)


async def _sync_tg_reaction(chat_id, msg_id, message_reactions):
    link = store.vk_for_tg(chat_id, msg_id)
    if not link:
        return
    info = _extract_other_reaction(message_reactions) if message_reactions else None
    emoji = None
    force_message = False
    if info:
        kind, value = info
        if kind == "emoji":
            emoji = value
        else:  # премиум-эмодзи: VK не умеет такие реакции — шлём сообщением-ответом
            emoji = await _resolve_custom_emoji(value)
            force_message = True
    await _sync_tg_reaction_emoji(chat_id, msg_id, emoji, force_message=force_message)


async def _sync_tg_reaction_emoji(chat_id, msg_id, emoji, force_message=False):
    link = store.vk_for_tg(chat_id, msg_id)
    if not link:
        return
    vk_peer, vk_cmid = link
    previous = store.get_tg_reaction(chat_id, msg_id)
    if emoji == previous:
        return
    if emoji is None:
        if previous is not None:
            if not await _delete_tg_reaction_fallback(chat_id, msg_id):
                await vk.delete_reaction(vk_peer, vk_cmid)
            store.set_tg_reaction(chat_id, msg_id, None)
        return

    await _delete_tg_reaction_fallback(chat_id, msg_id)
    # force_message=True для премиум-эмодзи: VK-реакции такое не отобразят,
    # поэтому сразу отправляем эмодзи отдельным сообщением-ответом.
    sent_via_reaction = False
    if not force_message:
        try:
            await vk.send_reaction(vk_peer, vk_cmid, reactions.emoji_to_vk(emoji))
            store.set_tg_reaction_fallback(chat_id, msg_id, None)
            sent_via_reaction = True
        except VKError:
            sent_via_reaction = False

    if not sent_via_reaction:
        if previous is not None:
            try:
                await vk.delete_reaction(vk_peer, vk_cmid)
            except VKError:
                pass
        mid = await vk.send(vk_peer, text=emoji, reply_to_cmid=vk_cmid)
        try:
            fallback_cmid = await vk.get_message_cmid(mid)
        except Exception:
            fallback_cmid = None
        store.set_tg_reaction_fallback(chat_id, msg_id, vk_peer, fallback_cmid)
    store.set_tg_reaction(chat_id, msg_id, emoji)
    if sent_via_reaction:
        log.info("TG reaction: chat=%s msg=%s emoji=%s", chat_id, msg_id, emoji)


async def _delete_tg_reaction_fallback(chat_id, msg_id):
    fallback = store.get_tg_reaction_fallback(chat_id, msg_id)
    if not fallback:
        return False
    vk_peer, fallback_cmid = fallback
    if fallback_cmid:
        try:
            await vk.delete(vk_peer, fallback_cmid)
        except VKError:
            pass
    store.set_tg_reaction_fallback(chat_id, msg_id, None)
    return True


async def reaction_poll_loop():
    """Получать только новые непрочитанные реакции без тяжёлого batch-refresh."""
    if config.REACTION_POLL_INTERVAL <= 0:
        return
    while True:
        try:
            chats = []
            for chat, _msg_id, _vk_peer, _vk_cmid in store.recent_tg_links(
                    config.REACTION_POLL_MESSAGES):
                if chat not in chats:
                    chats.append(chat)
            for chat in chats:
                try:
                    entity = await _tg_entity(chat)
                    unread = await tg(GetUnreadReactionsRequest(
                        peer=entity,
                        offset_id=0,
                        add_offset=0,
                        limit=100,
                        max_id=0,
                        min_id=0,
                    ))
                    if getattr(unread, "messages", None):
                        log.info(
                            "TG unread reactions: chat=%s messages=%s",
                            chat, [m.id for m in unread.messages],
                        )
                    for message in getattr(unread, "messages", None) or []:
                        await _sync_tg_reaction(
                            chat, message.id, message.reactions
                        )
                except FloodWaitError as e:
                    log.info(
                        "TG reaction poll: FloodWait %s секунд, делаю паузу",
                        e.seconds,
                    )
                    await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            log.warning("TG reaction poll failed: %s", e)
        await asyncio.sleep(config.REACTION_POLL_INTERVAL)


# ================= удаления / read / typing TG -> VK =================
@tg.on(events.MessageDeleted)
async def on_tg_deleted(event):
    try:
        links = store.vk_links_for_deleted_tg(
            event.deleted_ids,
            chat_id=getattr(event, "chat_id", None),
        )
        grouped = {}
        for vk_peer, vk_cmid in links:
            grouped.setdefault(vk_peer, set()).add(vk_cmid)
        for vk_peer, cmids in grouped.items():
            await vk.delete(vk_peer, sorted(cmids))
            for cmid in cmids:
                store.unlink_vk(vk_peer, cmid)
    except Exception as e:
        log.warning("TG->VK delete failed: %s", e)


@tg.on(events.MessageRead)
async def on_tg_read(event):
    """Когда адресат прочитал наш ответ в TG, пометить ответ в VK прочитанным ботом."""
    if not config.SYNC_READS or not event.outbox:
        return
    try:
        if store.has_tg_messages_upto(event.chat_id, event.max_id, "vk_to_tg"):
            await vk.mark_as_read(config.VK_OWNER_ID)
    except Exception as e:
        log.warning("TG->VK read sync failed: %s", e)


@tg.on(events.UserUpdate)
async def on_tg_user_update(event):
    if not config.SYNC_TYPING or not event.action or event.user_id == me_id:
        return
    chat_id = getattr(event, "chat_id", None)
    if chat_id is None or not store.get_peer_meta(chat_id):
        return
    try:
        if event.typing:
            activity = "typing"
        elif event.recording and (event.audio or event.round or event.video):
            # VK API различает только typing и audiomessage.
            activity = "audiomessage"
        else:
            return
        await vk.set_activity(config.VK_OWNER_ID, activity)
    except Exception as e:
        log.debug("TG->VK typing sync failed: %s", e)


# =========================== VK -> TG ===========================
async def vk_loop():
    while True:
        try:
            updates = await vk.poll()
            msg_news = [u for u in updates if u.get("type") == "message_new"]
            if len(msg_news) > 1:
                log.info("VK [DIAG]: за один poll пришло message_new x%d", len(msg_news))
            for upd in updates:
                t = upd.get("type")
                if t == "message_new":
                    await _handle_vk_message(upd["object"]["message"])
                elif t == "message_edit":
                    await _handle_vk_edit(upd["object"])
                elif t == "message_reaction_event":
                    await _handle_vk_reaction(upd["object"])
                elif t in ("message_delete", "message_deleted"):
                    await _handle_vk_delete(upd["object"])
                elif t == "message_read":
                    await _handle_vk_read(upd["object"])
                elif t == "message_typing_state":
                    await _handle_vk_typing(upd["object"])
        except Exception as e:
            log.exception("VK poll error: %s", e)
            await asyncio.sleep(3)


async def _handle_vk_message(m: dict):
    # принимаем только от владельца (тебя), игнорим прочих писавших в сообщество
    if _vk_message_actor(m) != config.VK_OWNER_ID:
        return

    target = None
    reply = m.get("reply_message")
    if reply and reply.get("text"):
        match = TOKEN_RE.search(reply["text"])
        if match:
            target = store.peer_for_token(match.group(1))
    if target is None:
        target = store.get_last_peer()
    if target is None:
        await vk.send(config.VK_OWNER_ID,
                      text="⚠️ Не понял, кому отправить. Ответь (reply) на сообщение нужного человека.")
        return

    log.info("VK->TG [DIAG]: cmid=%s reply=%s -> target=%s (last_peer=%s) text=%r",
             _vk_message_cmid(m), bool(reply), target,
             store.get_last_peer(), (m.get("text") or "")[:40])

    text = m.get("text") or ""
    attachments = m.get("attachments", [])
    files, extra = await _download_vk_attachments(attachments)
    if not any(a.get("type") in ("wall", "link") for a in attachments):
        text = await _expand_vk_links(text, files, extra)
    if extra:
        text = (text + "\n" + "\n".join(extra)).strip()

    cmid = _vk_message_cmid(m)
    sent_messages = []
    try:
        if files:
            sent_messages = await _tg_send_files(target, files, text)
        elif text:
            sent_messages = [await _tg_send_message(target, text)]
    except Exception as e:
        log.exception("VK->TG send failed: %s", e)
        await vk.send(config.VK_OWNER_ID, text=f"⚠️ Не удалось отправить в Telegram: {e}")
        return
    finally:
        for fpath, _kind in files:
            if os.path.exists(fpath):
                os.remove(fpath)

    if cmid:
        for sent in sent_messages:
            store.link_messages(
                target, sent.id, config.VK_OWNER_ID, cmid,
                direction="vk_to_tg",
            )


async def _handle_vk_edit(obj: dict):
    # объект события message_edit — это само сообщение (без вложенного "message")
    m = obj.get("message", obj)
    if _vk_message_actor(m) != config.VK_OWNER_ID:
        return  # правки самого сообщества (наши же) игнорируем -> нет петли
    peer_id = _vk_message_peer(m)
    cmid = _vk_message_cmid(m)
    if not cmid:
        return
    link = store.tg_for_vk(peer_id, cmid)
    if not link:
        return
    chat, msg_id = link
    text = m.get("text") or ""
    try:
        entity = await _tg_entity(chat)
        await tg.edit_message(entity, msg_id, text)
    except Exception as e:
        log.warning("VK->TG edit failed: %s", e)


async def _handle_vk_reaction(obj: dict):
    # только реакции владельца; реакции самого сообщества (наши же) сюда не попадут -> нет петли
    if obj.get("reacted_id") != config.VK_OWNER_ID:
        return
    link = store.tg_for_vk(
        obj.get("peer_id", config.VK_OWNER_ID),
        obj.get("cmid") or obj.get("conversation_message_id"),
    )
    if not link:
        return
    chat, msg_id = link
    reaction_id = obj.get("reaction_id")
    try:
        entity = await _tg_entity(chat)
        if not reaction_id:  # реакцию сняли
            await _tg_set_reaction(entity, msg_id, None)
            return
        emoji = reactions.vk_to_emoji(reaction_id)
        if emoji is None:
            log.info("VK reaction: неизвестный id=%s — ставлю дефолт. Допиши reactions.py", reaction_id)
            emoji = reactions.DEFAULT_TG_EMOJI
        await _tg_set_reaction(entity, msg_id, emoji)
    except Exception as e:
        log.warning("VK->TG reaction failed: %s", e)


async def _handle_vk_delete(obj: dict):
    m = obj.get("message", obj)
    peer_id = _vk_message_peer(m)
    cmid = _vk_message_cmid(m)
    if peer_id != config.VK_OWNER_ID or not cmid:
        return
    links = store.tg_all_for_vk(peer_id, cmid)
    grouped = {}
    for chat, msg_id in links:
        grouped.setdefault(chat, []).append(msg_id)
    try:
        for chat, msg_ids in grouped.items():
            await tg.delete_messages(
                await _tg_entity(chat),
                sorted(set(msg_ids)),
                revoke=True,
            )
        store.unlink_vk(peer_id, cmid)
    except Exception as e:
        log.warning("VK->TG delete failed: %s", e)


async def _handle_vk_read(obj: dict):
    if not config.SYNC_READS:
        return
    data = obj.get("message", obj)
    actor = _vk_message_actor(data)
    peer_id = _vk_message_peer(data)
    if actor not in (None, config.VK_OWNER_ID) or peer_id != config.VK_OWNER_ID:
        return
    cmid = (
        data.get("conversation_message_id")
        or data.get("read_cmid")
        or data.get("cmid")
    )
    if not cmid and data.get("read_message_id"):
        cmid = await vk.get_message_cmid(data["read_message_id"])
    if not cmid:
        return
    await _sync_vk_read_cmid(peer_id, cmid)


async def _sync_vk_read_cmid(peer_id, cmid):
    try:
        for chat, max_id in store.tg_messages_upto_vk_cmid(
                peer_id, cmid, "tg_to_vk").items():
            entity = await _tg_entity(chat)
            await tg.send_read_acknowledge(entity, max_id=max_id)
    except Exception as e:
        log.warning("VK->TG read sync failed: %s", e)


async def read_poll_loop():
    """Резервный опрос read-state VK, если Long Poll не отдаёт message_read."""
    if not config.SYNC_READS or config.READ_POLL_INTERVAL <= 0:
        return
    while True:
        try:
            state = await vk.get_read_state(config.VK_OWNER_ID)
            out_read = (state or {}).get("out_read")
            previous = store.get_sync_state("vk_out_read", 0) or 0
            if out_read and out_read > previous:
                cmid = await vk.get_message_cmid(out_read)
                if cmid:
                    await _sync_vk_read_cmid(config.VK_OWNER_ID, cmid)
                store.set_sync_state("vk_out_read", out_read)
        except Exception as e:
            log.warning("VK read poll failed: %s", e)
        await asyncio.sleep(config.READ_POLL_INTERVAL)


async def _handle_vk_typing(obj: dict):
    if not config.SYNC_TYPING:
        return
    data = obj.get("message", obj)
    actor = _vk_message_actor(data)
    if actor != config.VK_OWNER_ID:
        return
    target = store.get_last_peer()
    if target is None:
        return
    state = (data.get("state") or data.get("type") or "typing").lower()
    if state in ("audiomessage", "recording_audio", "voice"):
        action = SendMessageRecordAudioAction()
    elif state in ("videomessage", "recording_video", "round"):
        action = SendMessageRecordRoundAction()
    elif state in ("cancel", "none"):
        action = SendMessageCancelAction()
    else:
        action = SendMessageTypingAction()
    try:
        await tg(SetTypingRequest(peer=await _tg_entity(target), action=action))
    except Exception as e:
        log.debug("VK->TG typing sync failed: %s", e)


async def _tg_set_reaction(entity, msg_id, emoji):
    """Поставить (emoji) или снять (emoji=None) реакцию на сообщение TG."""
    reaction = [ReactionEmoji(emoticon=emoji)] if emoji else []
    await tg(SendReactionRequest(peer=entity, msg_id=msg_id, reaction=reaction))


def _input_peer_to_meta(ip):
    if isinstance(ip, InputPeerUser):
        return {"type": "user", "id": ip.user_id, "access_hash": ip.access_hash}
    if isinstance(ip, InputPeerChannel):
        return {"type": "channel", "id": ip.channel_id, "access_hash": ip.access_hash}
    if isinstance(ip, InputPeerChat):
        return {"type": "chat", "id": ip.chat_id}
    return None


def _meta_to_input_peer(meta):
    t = meta.get("type")
    if t == "user":
        return InputPeerUser(meta["id"], meta["access_hash"])
    if t == "channel":
        return InputPeerChannel(meta["id"], meta["access_hash"])
    if t == "chat":
        return InputPeerChat(meta["id"])
    return None


async def _tg_entity(target):
    """Сначала пробуем сохранённый input peer (переживает рестарт), затем кэш Telethon."""
    meta = store.get_peer_meta(target)
    if meta:
        ip = _meta_to_input_peer(meta)
        if ip is not None:
            return ip
    try:
        return await tg.get_input_entity(target)
    except Exception:
        return await tg.get_entity(target)


async def _tg_send_message(target, text):
    entity = await _tg_entity(target)
    return await tg.send_message(entity, text)


async def _tg_send_file(target, path, kind, caption):
    entity = await _tg_entity(target)
    return await tg.send_file(
        entity,
        path,
        voice_note=(kind == "voice"),
        video_note=(kind == "round"),
        caption=caption or None,
    )


async def _tg_send_files(target, files, caption):
    """Отправить несколько VK-вложений, сохраняя фото/видео как альбом."""
    entity = await _tg_entity(target)
    sent = []
    if caption and len(caption) > 1000:
        sent.append(await tg.send_message(entity, caption))
        caption = None
    batch_paths = []

    async def flush_batch(first_caption=None):
        nonlocal batch_paths
        if not batch_paths:
            return
        if len(batch_paths) == 1:
            result = await tg.send_file(
                entity, batch_paths[0], caption=first_caption or None
            )
            sent.append(result)
        else:
            result = await tg.send_file(
                entity, batch_paths, caption=first_caption or None
            )
            sent.extend(result if isinstance(result, list) else [result])
        batch_paths = []

    caption_pending = caption
    for path, kind in files:
        if kind in ("photo", "video"):
            batch_paths.append(path)
            continue
        await flush_batch(caption_pending)
        if sent:
            caption_pending = None
        result = await tg.send_file(
            entity,
            path,
            voice_note=(kind == "voice"),
            video_note=(kind == "round"),
            caption=caption_pending or None,
        )
        sent.append(result)
        caption_pending = None
    await flush_batch(caption_pending)
    return sent


async def _download_vk_attachments(atts):
    """Скачать вложения VK во временные файлы. Возвращает (files, extra_text)."""
    files = []
    extra = []
    await _collect_vk_attachments(atts, files, extra)
    return files, list(dict.fromkeys(x for x in extra if x))


async def _collect_vk_attachments(atts, files, extra, depth=0):
    if depth > 4:
        return
    for a in atts:
        t = a.get("type")
        obj = a.get(t, {})
        url = None
        kind = "doc"
        suffix = ""
        if t == "photo":
            sizes = obj.get("sizes", [])
            if sizes:
                url = max(sizes, key=lambda s: s["width"] * s["height"])["url"]
            kind, suffix = "photo", ".jpg"
        elif t == "audio_message":
            url = obj.get("link_ogg") or obj.get("link_mp3")
            kind, suffix = "voice", ".ogg"
        elif t == "doc":
            url = obj.get("url")
            suffix = "." + obj.get("ext", "bin")
        elif t == "sticker":
            imgs = obj.get("images") or obj.get("images_with_background") or []
            if imgs:
                url = imgs[-1]["url"]
            kind, suffix = "photo", ".png"
        elif t in ("video_message", "video_note"):
            url = (
                obj.get("link_mp4")
                or obj.get("video_url")
                or obj.get("url")
            )
            kind, suffix = "round", ".mp4"
        elif t == "video":
            owner_id, video_id = obj.get("owner_id"), obj.get("id")
            # Прямой mp4 (если VK его уже отдал в long poll) — скачиваем сразу,
            # иначе уходим в _append_vk_video с фолбэком на video.get + yt-dlp.
            direct = _best_vk_video_url(obj)
            if direct:
                kind, suffix = (
                    ("round", ".mp4") if obj.get("is_round") else ("video", ".mp4")
                )
                try:
                    files.append((await _download_url(direct, suffix, VIDEO_MAX_BYTES), kind))
                    continue
                except Exception as e:
                    log.debug("Прямой VK mp4 не скачался, пробую video.get/yt-dlp: %s", e)
            if owner_id is not None and video_id is not None and await _append_vk_video(
                    owner_id, video_id, obj.get("access_key"), files, extra):
                continue
            extra.append(f"📹 https://vk.com/video{owner_id}_{video_id}")
            continue
        elif t == "link":
            link_url = obj.get("url", "")
            match = _wall_link_match(link_url)
            if match and await _append_wall_post(
                    int(match.group(1)), int(match.group(2)),
                    files, extra, depth + 1):
                continue
            video = _video_link_parts(link_url)
            if video and await _append_vk_video(
                    *video, files, extra, source_url=link_url):
                continue
            extra.append(link_url)
            continue
        elif t == "wall":
            await _collect_wall_post(obj, files, extra, depth + 1)
            continue
        elif t == "wall_reply":
            if obj.get("text"):
                extra.append(f"💬 {obj['text']}")
            await _collect_vk_attachments(
                obj.get("attachments", []), files, extra, depth + 1
            )
            continue
        elif t == "poll":
            question = obj.get("question") or "Опрос"
            answers = [
                f"{answer.get('text', '')} — {answer.get('votes', 0)}"
                for answer in obj.get("answers", [])
            ]
            extra.append("📊 " + question + (
                "\n" + "\n".join(answers) if answers else ""
            ))
            continue
        elif t == "audio":
            title = " — ".join(
                x for x in (obj.get("artist"), obj.get("title")) if x
            )
            extra.append(f"🎵 {title}" if title else "[аудиозапись]")
            continue
        else:
            extra.append(f"[вложение: {t}]")
            continue

        if url:
            cap = VIDEO_MAX_BYTES if kind in ("video", "round") else MAX_DOWNLOAD_BYTES
            try:
                files.append((await _download_url(url, suffix, cap), kind))
            except Exception as e:
                log.warning("Не удалось скачать VK-вложение %s: %s", t, e)
                if t in ("video", "video_message", "video_note"):
                    extra.append(
                        f"📹 https://vk.com/video{obj.get('owner_id')}_{obj.get('id')}"
                    )
                else:
                    extra.append(f"[не удалось скачать вложение: {t}]")


def _best_vk_video_url(obj):
    candidates = {}
    for key, value in (obj.get("files") or {}).items():
        match = re.match(r"mp4_(\d+)", key)
        if match and value:
            candidates[int(match.group(1))] = value
    for key, value in obj.items():
        match = re.match(r"mp4_(\d+)", key)
        if match and value:
            candidates[int(match.group(1))] = value
    return candidates[max(candidates)] if candidates else None


async def _collect_wall_post(post, files, extra, depth):
    if depth > 4:
        return
    text = (post.get("text") or "").strip()
    if text:
        extra.append("📝 " + text)
    await _collect_vk_attachments(
        post.get("attachments", []), files, extra, depth
    )
    for original in post.get("copy_history", []) or []:
        await _collect_wall_post(original, files, extra, depth + 1)


async def _append_wall_post(owner_id, post_id, files, extra, depth):
    try:
        post = await vk.get_wall_post(owner_id, post_id)
        if not post:
            return False
        await _collect_wall_post(post, files, extra, depth)
        return True
    except Exception as e:
        log.debug("Не удалось раскрыть VK wall-ссылку: %s", e)
        return False


async def _append_vk_video(
        owner_id, video_id, access_key, files, extra, source_url=None):
    """Загрузить VK-видео через API, затем через yt-dlp как fallback."""
    item = {}
    try:
        item = await vk.get_video(owner_id, video_id, access_key) or {}
    except Exception as e:
        # community-токену часто недоступен video.get (нет scope video). Это
        # не повод сдаваться: ниже пробуем yt-dlp по публичной/embed-ссылке.
        log.debug("video.get %s_%s не сработал: %s", owner_id, video_id, e)
    try:
        direct_url = _best_vk_video_url(item)
        if direct_url:
            path = await _download_url(direct_url, ".mp4", VIDEO_MAX_BYTES)
            info = item
        else:
            # video.get обычно не отдаёт files, но возвращает player с oid/id/hash.
            # Embed URL заметно надёжнее обычной страницы для yt-dlp.
            download_url = (
                item.get("player")
                or source_url
                or f"https://vk.com/video{owner_id}_{video_id}"
            )
            path, info = await _download_vk_video(download_url)
        files.append((path, "round" if item.get("is_round") else "video"))
        title = (item.get("title") or info.get("title") or "").strip()
        description = (
            item.get("description") or info.get("description") or ""
        ).strip()
        if title:
            extra.append("🎬 " + title)
        if description:
            extra.append(description)
        return True
    except Exception as e:
        log.warning(
            "Не удалось загрузить VK video %s_%s: %s",
            owner_id, video_id, e,
        )
        return False


async def _download_vk_video(url):
    """Скачать публичное VK-видео через extractor yt-dlp без ffmpeg."""
    base = os.path.join(
        tempfile.gettempdir(),
        f"vk-video-{uuid.uuid4().hex}",
    )
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "yt_dlp",
            "--quiet",
            "--no-warnings",
            "--no-playlist",
            "--no-part",
            "--format", "best[ext=mp4]/best",
            "--max-filesize", f"{config.VK_VIDEO_MAX_MB}M",
            "--socket-timeout", "30",
            "--retries", "3",
            "--print", "after_move:filepath",
            "--output", base + ".%(ext)s",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=120
        )
        if process.returncode:
            message = stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(message or f"yt-dlp code {process.returncode}")
        candidates = [
            p for p in glob.glob(base + ".*")
            if not p.endswith((".part", ".ytdl"))
        ]
        if not candidates:
            raise RuntimeError("yt-dlp не создал видеофайл")
        path = max(candidates, key=os.path.getsize)
        return path, {}
    except asyncio.TimeoutError:
        if process and process.returncode is None:
            process.kill()
            await process.communicate()
        for path in glob.glob(base + ".*"):
            try:
                os.remove(path)
            except OSError:
                pass
        raise RuntimeError("yt-dlp превысил таймаут 120 секунд")
    except Exception:
        for path in glob.glob(base + ".*"):
            try:
                os.remove(path)
            except OSError:
                pass
        raise


WALL_LINK_RE = re.compile(
    r"(?:https?://)?(?:m\.)?vk\.com/"
    r"(?:wall|[^?\s]+\?(?:z|w)=wall)(-?\d+)_(\d+)",
    re.IGNORECASE,
)

VIDEO_LINK_RE = re.compile(
    r"(?:https?://)?(?:(?:m\.)?vk\.com|vkvideo\.ru)/"
    r"[^\s]*?(?:video|clip)(-?\d+)_(\d+)"
    r"(?:_([A-Za-z0-9_-]+))?",
    re.IGNORECASE,
)


def _wall_link_match(url):
    return WALL_LINK_RE.search(url or "")


def _video_link_parts(url):
    match = VIDEO_LINK_RE.search(url or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), match.group(3)


async def _expand_vk_links(text, files, extra):
    """Раскрыть голые ссылки на посты и видео в тексте сообщения VK."""
    result = text
    for match in list(WALL_LINK_RE.finditer(text or "")):
        if await _append_wall_post(
                int(match.group(1)), int(match.group(2)), files, extra, 0):
            result = result.replace(match.group(0), "").strip()
    for match in list(VIDEO_LINK_RE.finditer(result or "")):
        parts = (
            int(match.group(1)),
            int(match.group(2)),
            match.group(3),
        )
        if await _append_vk_video(
                *parts, files, extra, source_url=match.group(0)):
            result = result.replace(match.group(0), "").strip()
    return result


async def _download_url(url: str, suffix: str = "", max_bytes: int = MAX_DOWNLOAD_BYTES) -> str:
    """Скачать файл потоком на диск (без чтения целиком в память).

    Если файл крупнее max_bytes — бросаем ошибку, чтобы вызывающий оставил
    ссылку, а не пытался залить гиганта (и не словил OOM).
    """
    timeout = aiohttp.ClientTimeout(total=600)
    async with http.get(url, timeout=timeout) as r:
        r.raise_for_status()
        declared = r.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise ValueError(f"файл слишком большой: {declared} байт > {max_bytes}")
        if not suffix:
            suffix = os.path.splitext(url.split("?")[0])[1] or ""
        fd, path = tempfile.mkstemp(suffix=suffix)
        size = 0
        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in r.content.iter_chunked(1 << 16):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError(f"файл превысил лимит {max_bytes} байт")
                    f.write(chunk)
        except BaseException:
            try:
                os.remove(path)
            except OSError:
                pass
            raise
    return path


# =========================== запуск ===========================
async def main():
    global http, vk, me_id
    http = aiohttp.ClientSession()
    vk = VK(config.VK_TOKEN, config.VK_GROUP_ID, http)

    await tg.start()
    me = await tg.get_me()
    me_id = me.id
    log.info("Telegram: вошёл как %s (id %s)", utils.get_display_name(me), me.id)
    if config.TG_WHITELIST_ACTIVE:
        log.info("Белый список активен: ids=%s, usernames=%s",
                 sorted(config.TG_WHITELIST_IDS), sorted(config.TG_WHITELIST_NAMES))
    else:
        log.info("Белый список не задан — обрабатываю все диалоги.")

    try:
        await vk.send(config.VK_OWNER_ID, text="✅ Мост Telegram↔VK запущен.")
    except VKError as e:
        log.warning("Не смог отправить стартовое сообщение в VK (напиши сообществу хотя бы раз?): %s", e)

    log.info("Мост работает. Жду сообщения…")
    try:
        await asyncio.gather(
            vk_loop(),
            reaction_poll_loop(),
            read_poll_loop(),
            tg.run_until_disconnected(),
        )
    finally:
        await http.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
