"""Минимальный асинхронный клиент VK API для бота-сообщества.

Реализует то, что нужно мосту: вызов методов API, Bot Long Poll и загрузку
вложений (фото / документы / голосовые) в личные сообщения.
"""
import asyncio
import os
import random

import aiohttp

API_URL = "https://api.vk.com/method/"
API_VERSION = "5.199"


class VKError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class VK:
    def __init__(self, token: str, group_id: int, session: aiohttp.ClientSession):
        self.token = token
        self.group_id = int(group_id)
        self.session = session
        self._lp = None  # {"server", "key", "ts"}

    async def api(self, method: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        params["access_token"] = self.token
        params["v"] = API_VERSION
        for attempt in range(4):
            async with self.session.post(API_URL + method, data=params) as r:
                data = await r.json(content_type=None)
            if "error" not in data:
                return data["response"]
            err = data["error"]
            code = err.get("error_code")
            # 6 — слишком много запросов, 9 — flood control, 10 — временная
            # внутренняя ошибка. Особенно заметно на двух медиа подряд.
            if code in (6, 9, 10) and attempt < 3:
                await asyncio.sleep(0.35 * (2 ** attempt))
                continue
            raise VKError(
                f"{method}: {err.get('error_msg')} (code {code})",
                code=code,
            )
        raise VKError(f"{method}: retry limit exceeded")

    async def send(self, peer_id: int, text: str = None, attachment: str = None):
        return await self.api(
            "messages.send",
            peer_id=peer_id,
            message=text,
            attachment=attachment,
            random_id=random.randint(1, 2 ** 31 - 1),
        )

    async def edit(self, peer_id: int, cmid: int, text: str):
        """Отредактировать текст сообщения по conversation_message_id."""
        return await self.api(
            "messages.edit",
            peer_id=peer_id,
            conversation_message_id=cmid,
            message=text,
            keep_forward_messages=1,
            keep_snippets=1,
        )

    async def get_message_cmid(self, message_id):
        """Получить conversation_message_id по глобальному id сообщения."""
        r = await self.api("messages.getById", message_ids=message_id)
        items = r.get("items") or []
        return items[0].get("conversation_message_id") if items else None

    async def send_reaction(self, peer_id: int, cmid: int, reaction_id: int):
        return await self.api("messages.sendReaction", peer_id=peer_id, cmid=cmid, reaction_id=reaction_id)

    async def delete_reaction(self, peer_id: int, cmid: int):
        return await self.api("messages.deleteReaction", peer_id=peer_id, cmid=cmid)

    async def delete(self, peer_id: int, cmids):
        """Удалить сообщения для всех участников по conversation_message_id."""
        if not cmids:
            return {}
        if not isinstance(cmids, (list, tuple, set)):
            cmids = [cmids]
        return await self.api(
            "messages.delete",
            peer_id=peer_id,
            cmids=",".join(str(x) for x in cmids),
            delete_for_all=1,
        )

    async def mark_as_read(self, peer_id: int):
        """Пометить входящие сообщения владельца в диалоге с сообществом прочитанными."""
        return await self.api("messages.markAsRead", peer_id=peer_id)

    async def get_read_state(self, peer_id: int):
        result = await self.api(
            "messages.getConversationsById",
            peer_ids=peer_id,
        )
        items = result.get("items") or []
        if not items:
            return None
        conversation = items[0]
        return {
            "in_read": conversation.get("in_read"),
            "out_read": conversation.get("out_read"),
        }

    async def set_activity(self, peer_id: int, activity="typing"):
        """Показать владельцу статус набора текста/записи голосового от сообщества."""
        return await self.api(
            "messages.setActivity",
            peer_id=peer_id,
            type=activity,
        )

    async def get_wall_post(self, owner_id: int, post_id: int):
        posts = await self.api("wall.getById", posts=f"{owner_id}_{post_id}")
        if isinstance(posts, dict):
            posts = posts.get("items") or []
        return posts[0] if posts else None

    async def get_video(self, owner_id: int, video_id: int, access_key=None):
        item = f"{owner_id}_{video_id}"
        if access_key:
            item += f"_{access_key}"
        result = await self.api("video.get", videos=item)
        items = result.get("items") or []
        return items[0] if items else None

    # ------------------------- загрузка вложений -------------------------
    async def _upload_file(self, url: str, field: str, path: str):
        with open(path, "rb") as f:
            form = aiohttp.FormData()
            form.add_field(field, f, filename=os.path.basename(path) or "file")
            async with self.session.post(url, data=form) as r:
                return await r.json(content_type=None)

    async def upload_photo(self, peer_id: int, path: str) -> str:
        srv = await self.api("photos.getMessagesUploadServer", peer_id=peer_id)
        up = await self._upload_file(srv["upload_url"], "photo", path)
        saved = await self.api(
            "photos.saveMessagesPhoto",
            photo=up["photo"], server=up["server"], hash=up["hash"],
        )
        p = saved[0]
        return f"photo{p['owner_id']}_{p['id']}"

    async def upload_doc(self, peer_id: int, path: str, doc_type: str = "doc", title: str = None) -> str:
        # doc_type: "doc" для файлов/видео, "audio_message" для голосовых
        srv = await self.api("docs.getMessagesUploadServer", peer_id=peer_id, type=doc_type)
        up = await self._upload_file(srv["upload_url"], "file", path)
        saved = await self.api("docs.save", file=up["file"], title=title)
        t = saved["type"]
        obj = saved[t]
        # голосовые тоже прикрепляются с префиксом doc — клиент VK рендерит их как голос
        return f"doc{obj['owner_id']}_{obj['id']}"

    # ------------------------- Bot Long Poll -------------------------
    async def _get_long_poll_server(self):
        r = await self.api("groups.getLongPollServer", group_id=self.group_id)
        self._lp = {"server": r["server"], "key": r["key"], "ts": r["ts"]}

    async def poll(self):
        """Один цикл long poll. Возвращает список updates (может быть пустым)."""
        if not self._lp:
            await self._get_long_poll_server()
        params = {"act": "a_check", "key": self._lp["key"], "ts": self._lp["ts"], "wait": 25}
        async with self.session.get(self._lp["server"], params=params) as r:
            data = await r.json(content_type=None)
        if "failed" in data:
            code = data["failed"]
            if code == 1:  # ts устарел — обновляем
                self._lp["ts"] = data["ts"]
            else:  # 2/3 — ключ/сервер устарел, перезапрашиваем
                await self._get_long_poll_server()
            return []
        self._lp["ts"] = data["ts"]
        return data.get("updates", [])
