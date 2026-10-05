"""A thin Telegram Bot API client (only the methods the bot uses)."""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    pass


class TelegramAPI:
    def __init__(self, token: str, timeout: float = 15.0):
        self.base = f"https://api.telegram.org/bot{token}"
        self.file_base = f"https://api.telegram.org/file/bot{token}"
        self.http = httpx.Client(timeout=timeout)

    def call(self, method: str, **params: Any) -> Any:
        payload = {k: v for k, v in params.items() if v is not None}
        resp = self.http.post(f"{self.base}/{method}", json=payload)
        data = resp.json()
        if not data.get("ok"):
            desc = data.get("description", "")
            # Editing a message to identical content is harmless.
            if "message is not modified" in desc:
                return None
            raise TelegramError(f"{method}: {desc}")
        return data.get("result")

    def send_message(self, chat_id: int, text: str, reply_markup: Optional[dict] = None, silent: bool = False) -> dict:
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                         reply_markup=reply_markup, link_preview_options={"is_disabled": True},
                         disable_notification=silent or None)

    def pin_chat_message(self, chat_id: int, message_id: int):
        return self.call("pinChatMessage", chat_id=chat_id, message_id=message_id, disable_notification=True)

    def edit_message_text(self, chat_id: int, message_id: int, text: str, reply_markup: Optional[dict] = None):
        return self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text,
                         parse_mode="HTML", reply_markup=reply_markup or {"inline_keyboard": []})

    def edit_message_reply_markup(self, chat_id: int, message_id: int, reply_markup: Optional[dict] = None):
        return self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                         reply_markup=reply_markup or {"inline_keyboard": []})

    def answer_callback_query(self, callback_query_id: str, text: Optional[str] = None):
        return self.call("answerCallbackQuery", callback_query_id=callback_query_id, text=text)

    def download_file(self, file_id: str, max_bytes: int = 20 * 1024 * 1024) -> bytes:
        """Fetch a photo or voice note the user sent (Telegram serves files up to 20 MB to bots)."""
        info = self.call("getFile", file_id=file_id)
        resp = self.http.get(f"{self.file_base}/{info['file_path']}")
        resp.raise_for_status()
        if len(resp.content) > max_bytes:
            raise TelegramError("file too large")
        return resp.content

    def send_chat_action(self, chat_id: int, action: str = "typing"):
        try:
            return self.call("sendChatAction", chat_id=chat_id, action=action)
        except Exception:  # cosmetic only
            return None
