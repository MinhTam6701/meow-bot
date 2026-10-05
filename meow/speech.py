"""Voice notes to text with Groq's Whisper (handles English, Vietnamese and a mix of both)."""
from __future__ import annotations

import time
from typing import Callable, Optional

import httpx

from .parser_llm import LLMCallLog

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# A hint of the words people say when logging money; Whisper uses it to pick spellings.
PROMPT = "phở 65 nghìn, grab 12 dollars, cà phê 30 nghìn, lunch 14, tiền nhà 2 triệu, kopi, hawker, Shopee."

Transcriber = Callable[[bytes, str], tuple[Optional[str], LLMCallLog]]


def groq_transcriber(api_key: str, model: str = "whisper-large-v3", timeout: float = 30.0) -> Transcriber:
    http = httpx.Client(timeout=timeout)

    def transcribe(audio: bytes, filename: str) -> tuple[Optional[str], LLMCallLog]:
        log = LLMCallLog(model=model, input_tokens=None, output_tokens=None, latency_ms=0, ok=False,
                         input_text=f"[voice {len(audio) // 1024} KB]", output_json=None)
        start = time.monotonic()
        try:
            resp = http.post(GROQ_URL, headers={"Authorization": f"Bearer {api_key}"},
                             files={"file": (filename, audio)},
                             data={"model": model, "prompt": PROMPT, "response_format": "json", "temperature": "0"})
            if resp.status_code != 200:
                log.error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                return None, log
            text = (resp.json().get("text") or "").strip()
            log.output_json = {"text": text}
            log.ok = True
            return text, log
        except Exception as exc:
            log.error = f"{type(exc).__name__}: {exc}"[:500]
            return None, log
        finally:
            log.latency_ms = int((time.monotonic() - start) * 1000)

    return transcribe
