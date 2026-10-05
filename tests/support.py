"""Test doubles and helpers shared by the end-to-end tests (the `env` fixture lives in conftest.py)."""
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


DB_URL = os.getenv("TEST_DATABASE_URL")

ME = 1001
STRANGER = 2002
SCHEMA = "\n".join(p.read_text() for p in sorted((Path(__file__).parent.parent / "supabase/migrations").glob("*.sql")))
# 22:30 in Singapore on 29 Sep 2026
NOW = datetime(2026, 9, 29, 14, 30, tzinfo=timezone.utc)


class FakeTelegram:
    def __init__(self):
        self.sent, self.edits, self.answers, self.next_id = [], [], [], 500
        self.reactions = []

    def send_message(self, chat_id, text, reply_markup=None, silent=False):
        self.next_id += 1
        if REACTION in text:  # persona reactions are kept apart so tests can read the card as sent[-1]
            self.reactions.append(text)
            return {"message_id": self.next_id}
        self.sent.append(SimpleNamespace(chat_id=chat_id, text=text, markup=reply_markup, id=self.next_id, silent=silent))
        return {"message_id": self.next_id}

    def send_photo(self, chat_id, photo, caption=None, filename="photo.jpg", silent=False):
        """Records the picture like a message (caption as text), so tests can read it as sent[-1]."""
        if getattr(self, "photo_fails", False):
            raise RuntimeError("Bad Request: wrong file")
        self.next_id += 1
        self.photos = getattr(self, "photos", []) + [(photo if isinstance(photo, str) else f"<upload {filename}>")]
        self.sent.append(SimpleNamespace(chat_id=chat_id, text=caption or "", markup=None, id=self.next_id,
                                         silent=silent, photo=photo if isinstance(photo, str) else filename))
        return {"message_id": self.next_id, "photo": [{"file_id": "small"}, {"file_id": f"tg-{filename}"}]}

    def pin_chat_message(self, chat_id, message_id):
        self.pinned = message_id

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edits.append(SimpleNamespace(kind="text", message_id=message_id, text=text, markup=reply_markup))

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edits.append(SimpleNamespace(kind="markup", message_id=message_id, text=None, markup=reply_markup))

    def answer_callback_query(self, cq_id, text=None):
        self.answers.append(text)

    def send_chat_action(self, *a, **k):
        pass

    def download_file(self, file_id, max_bytes=None):
        self.downloads = getattr(self, "downloads", []) + [file_id]
        return b"\xff\xd8fake-jpeg"


REACTION = "[persona]"


class FakeLLM:
    """Parsing: a canned tool call, or a question if nothing is queued.
    Persona (no tools): a canned text reply, recorded in .chats."""

    def __init__(self):
        self.queue, self.calls = [], []
        self.chats, self.replies, self.fail_chat = [], [], False
        self.messages = self

    def create(self, **kwargs):
        if "tools" not in kwargs:
            self.chats.append(kwargs)
            if self.fail_chat:
                raise RuntimeError("overloaded")
            text = self.replies.pop(0) if self.replies else "Nice one."
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"{REACTION} {text}")],
                                   usage=SimpleNamespace(input_tokens=800, output_tokens=40))
        self.calls.append(kwargs)
        data = self.queue.pop(0) if self.queue else {"entries": [], "question": "How much was it?"}
        block = SimpleNamespace(type="tool_use", input=data)
        return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=1000, output_tokens=100))


def buttons(markup):
    return [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
