"""Постоянное хранилище маршрутизации ответов VK -> TG.

Каждому TG-чату сопоставляется короткий токен (#abcd), который виден в заголовке
сообщения в VK. Чтобы ответить конкретному человеку — делаешь reply на его
сообщение в VK (токен берётся из процитированного текста). Если просто пишешь без
reply — ответ уходит в последний активный чат (last_peer).
"""
import hashlib
import json
import os
import threading


class Store:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.data = {
            "token_to_peer": {}, "peer_to_token": {}, "last_peer": None, "peer_meta": {},
            "msg_t2v": {}, "msg_v2t": {},
            "msg_dirs": {}, "tg_reactions": {}, "sync_state": {},
        }
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    loaded = json.load(f)
                self.data.update(loaded)
            except Exception:
                pass

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    def token_for_peer(self, peer) -> str:
        """Вернуть (создав при необходимости) стабильный короткий токен чата."""
        peer = str(peer)
        with self._lock:
            tok = self.data["peer_to_token"].get(peer)
            if tok:
                return tok
            base = hashlib.md5(peer.encode()).hexdigest()
            n = 4
            tok = base[:n]
            while tok in self.data["token_to_peer"] and self.data["token_to_peer"][tok] != peer:
                n += 1
                tok = base[:n]
            self.data["token_to_peer"][tok] = peer
            self.data["peer_to_token"][peer] = tok
            self._save()
            return tok

    def peer_for_token(self, tok: str):
        p = self.data["token_to_peer"].get(tok)
        return int(p) if p is not None else None

    def set_peer_meta(self, peer, meta: dict):
        """Сохранить тип/id/access_hash диалога для надёжной отправки после рестарта."""
        with self._lock:
            if self.data.setdefault("peer_meta", {}).get(str(peer)) != meta:
                self.data["peer_meta"][str(peer)] = meta
                self._save()

    def get_peer_meta(self, peer):
        return self.data.get("peer_meta", {}).get(str(peer))

    @staticmethod
    def _normalize_tg_links(value):
        """Привести старый [chat, msg] и новый [[chat, msg], ...] к списку."""
        if not value:
            return []
        if len(value) == 2 and not isinstance(value[0], (list, tuple)):
            return [value]
        return value

    def link_messages(self, tg_chat, tg_msg_id, vk_peer, vk_cmid,
                      direction=None, limit=4000):
        """Связать сообщения TG и VK.

        Несколько сообщений Telegram (альбом) могут соответствовать одному
        сообщению VK. direction: tg_to_vk или vk_to_tg.
        """
        with self._lock:
            t2v = self.data.setdefault("msg_t2v", {})
            v2t = self.data.setdefault("msg_v2t", {})
            tg_key = f"{tg_chat}:{tg_msg_id}"
            vk_key = f"{vk_peer}:{vk_cmid}"
            t2v[tg_key] = [vk_peer, vk_cmid]
            links = self._normalize_tg_links(v2t.get(vk_key))
            link = [tg_chat, tg_msg_id]
            if link not in links:
                links.append(link)
            v2t[vk_key] = links
            if direction:
                self.data.setdefault("msg_dirs", {})[tg_key] = direction
            for d in (t2v, v2t):  # ограничиваем рост, удаляя самые старые записи
                while len(d) > limit:
                    old_key = next(iter(d))
                    d.pop(old_key)
            self._save()

    def vk_for_tg(self, tg_chat, tg_msg_id):
        return self.data.get("msg_t2v", {}).get(f"{tg_chat}:{tg_msg_id}")

    def tg_for_vk(self, vk_peer, vk_cmid):
        links = self.tg_all_for_vk(vk_peer, vk_cmid)
        return links[0] if links else None

    def tg_all_for_vk(self, vk_peer, vk_cmid):
        value = self.data.get("msg_v2t", {}).get(f"{vk_peer}:{vk_cmid}")
        return self._normalize_tg_links(value)

    def direction_for_tg(self, tg_chat, tg_msg_id):
        return self.data.get("msg_dirs", {}).get(f"{tg_chat}:{tg_msg_id}")

    def vk_links_for_deleted_tg(self, msg_ids, chat_id=None):
        """Найти VK-сообщения для удалённых TG id.

        MessageDeleted не всегда содержит chat_id, поэтому при его отсутствии
        ищем по всем сохранённым диалогам.
        """
        wanted = {str(x) for x in msg_ids}
        result = set()
        for key, value in self.data.get("msg_t2v", {}).items():
            chat, msg = key.rsplit(":", 1)
            if msg not in wanted:
                continue
            if chat_id is not None and chat != str(chat_id):
                continue
            if self.data.get("msg_dirs", {}).get(key) not in (None, "tg_to_vk"):
                continue
            result.add(tuple(value))
        return sorted(result)

    def recent_tg_links(self, limit=200):
        """Последние связанные TG-сообщения обоих направлений.

        Реакция собеседника может быть поставлена как на его входящее сообщение
        (tg_to_vk), так и на наш ответ, отправленный из VK (vk_to_tg).
        """
        result = []
        items = list(self.data.get("msg_t2v", {}).items())
        for key, value in reversed(items):
            chat, msg = key.rsplit(":", 1)
            result.append((int(chat), int(msg), value[0], value[1]))
            if len(result) >= limit:
                break
        return result

    # Обратная совместимость для внешних вызовов старого имени.
    def recent_tg_to_vk(self, limit=200):
        return self.recent_tg_links(limit)

    def set_tg_reaction(self, tg_chat, tg_msg_id, emoji):
        key = f"{tg_chat}:{tg_msg_id}"
        with self._lock:
            reactions = self.data.setdefault("tg_reactions", {})
            if emoji is None:
                reactions.pop(key, None)
            else:
                reactions[key] = emoji
            self._save()

    def get_tg_reaction(self, tg_chat, tg_msg_id):
        return self.data.get("tg_reactions", {}).get(f"{tg_chat}:{tg_msg_id}")

    def get_sync_state(self, key, default=None):
        return self.data.get("sync_state", {}).get(key, default)

    def set_sync_state(self, key, value):
        with self._lock:
            state = self.data.setdefault("sync_state", {})
            if state.get(key) != value:
                state[key] = value
                self._save()

    def tg_messages_upto_vk_cmid(self, vk_peer, vk_cmid, direction):
        """Сгруппировать связанные TG id до прочитанного cmid по чатам."""
        result = {}
        for key, value in self.data.get("msg_t2v", {}).items():
            if value[0] != vk_peer or value[1] > vk_cmid:
                continue
            if self.data.get("msg_dirs", {}).get(key) != direction:
                continue
            chat, msg = key.rsplit(":", 1)
            result[int(chat)] = max(result.get(int(chat), 0), int(msg))
        return result

    def has_tg_messages_upto(self, tg_chat, max_id, direction):
        for key in self.data.get("msg_t2v", {}):
            chat, msg = key.rsplit(":", 1)
            if chat == str(tg_chat) and int(msg) <= max_id:
                if self.data.get("msg_dirs", {}).get(key) == direction:
                    return True
        return False

    def unlink_vk(self, vk_peer, vk_cmid):
        with self._lock:
            vk_key = f"{vk_peer}:{vk_cmid}"
            links = self._normalize_tg_links(
                self.data.setdefault("msg_v2t", {}).pop(vk_key, None)
            )
            for chat, msg in links:
                tg_key = f"{chat}:{msg}"
                self.data.setdefault("msg_t2v", {}).pop(tg_key, None)
                self.data.setdefault("msg_dirs", {}).pop(tg_key, None)
                self.data.setdefault("tg_reactions", {}).pop(tg_key, None)
            self._save()

    def set_last_peer(self, peer):
        with self._lock:
            self.data["last_peer"] = str(peer)
            self._save()

    def get_last_peer(self):
        p = self.data.get("last_peer")
        return int(p) if p is not None else None
