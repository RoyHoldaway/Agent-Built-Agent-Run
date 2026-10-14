"""Thin OpenAI-compatible chat client. Works with Ollama, LM Studio, vLLM, llama.cpp, DashScope."""
from __future__ import annotations

import json
import re
import sys
import threading
import time

import httpx

from .config import Settings


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class LLMError(RuntimeError):
    pass


def strip_thinking(text: str) -> str:
    """Remove Qwen-style thinking blocks, including an unfinished block."""
    text = _THINK_RE.sub("", text)
    if "<think>" in text.lower():
        text = re.split(r"<think>", text, flags=re.IGNORECASE)[0]
    return text.strip()


def image_message(text: str, data_urls: list[str]) -> dict:
    parts: list[dict] = [
        {"type": "image_url", "image_url": {"url": url}}
        for url in data_urls
    ]
    parts.append({"type": "text", "text": text})
    return {"role": "user", "content": parts}


class LLM:
    def __init__(self, settings: Settings):
        self.s = settings
        self._http = httpx.Client(
            base_url=settings.base_url,
            headers={"Authorization": f"Bearer {settings.api_key}"},
            timeout=httpx.Timeout(settings.timeout, connect=10),
        )

    def _ollama_vision_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Send image-bearing requests through Ollama's native chat API."""
        base = self.s.base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]

        native_messages = []

        for message in messages:
            content = message.get("content", "")

            if not isinstance(content, list):
                native_messages.append({
                    "role": message["role"],
                    "content": content,
                })
                continue

            text_parts = []
            images = []

            for part in content:
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))

                elif part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")

                    if url.startswith("data:") and "," in url:
                        images.append(url.split(",", 1)[1])
                    else:
                        raise LLMError(
                            "Vision input must be a base64 data URL."
                        )

            item = {
                "role": message["role"],
                "content": "\n".join(text_parts),
            }

            if images:
                item["images"] = images

            native_messages.append(item)

        payload = {
            "model": self.s.vision_model,
            "messages": native_messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": (
                    self.s.temperature
                    if temperature is None
                    else temperature
                ),
                "num_predict": max_tokens or 2048,
                "num_ctx": 16384,
            },
        }

        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.s.timeout, connect=15)
            ) as client:
                response = client.post(
                    f"{base}/api/chat",
                    json=payload,
                )

            if response.status_code >= 400:
                raise LLMError(
                    f"Ollama vision HTTP {response.status_code}: "
                    f"{response.text[:1500]}"
                )

            result = response.json()

            if result.get("error"):
                raise LLMError(
                    f"Ollama vision error: {result['error']}"
                )

        except LLMError:
            raise
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise LLMError(
                f"Ollama native vision request failed: {exc}"
            ) from exc

        message = result.get("message") or {}
        content = message.get("content") or ""
        answer = strip_thinking(content)

        if not answer:
            thinking = message.get("thinking") or ""
            raise LLMError(
                f"{self.s.vision_model} returned empty content. "
                f"done={result.get('done')!r}, "
                f"done_reason={result.get('done_reason')!r}, "
                f"prompt_eval_count={result.get('prompt_eval_count')!r}, "
                f"eval_count={result.get('eval_count')!r}, "
                f"thinking_chars={len(thinking)}, "
                f"message_fields={list(message.keys())!r}, "
                f"model={result.get('model')!r}"
            )

        return answer

    def chat(
        self,
        messages: list[dict],
        *,
        vision: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        retries: int = 2,
    ) -> str:
        if vision and "11434" in self.s.base_url:
            return self._ollama_vision_chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        model = self.s.vision_model if vision else self.s.text_model

        payload = {
            "model": model,
            "messages": messages,
            "temperature": (
                self.s.temperature if temperature is None else temperature
            ),
            "max_tokens": max_tokens or self.s.max_tokens,
            "stream": True,
            "think": False,
        }

        last: Exception | None = None

        for attempt in range(retries + 1):
            started = time.time()
            stop = threading.Event()
            stats = {"chunks": 0}
            frames = ["|", "/", "-", "\\"]

            def show_progress():
                i = 0
                while not stop.wait(0.2):
                    elapsed = time.time() - started
                    frame = frames[i % len(frames)]
                    sys.stdout.write(
                        f"\r  {model} {frame} "
                        f"{stats['chunks']} chunks received | "
                        f"{elapsed:.0f}s elapsed"
                    )
                    sys.stdout.flush()
                    i += 1

            worker = threading.Thread(
                target=show_progress,
                daemon=True,
            )
            worker.start()

            try:
                pieces = []

                with self._http.stream(
                    "POST",
                    "/chat/completions",
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        body = response.read().decode(
                            "utf-8",
                            errors="replace",
                        )
                        raise LLMError(
                            f"{response.status_code} from model server: "
                            f"{body[:400]}"
                        )

                    for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue

                        data = line[5:].strip()

                        if data == "[DONE]":
                            break

                        try:
                            event = json.loads(data)
                        except ValueError:
                            continue

                        choices = event.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})
                        piece = (
                            delta.get("content")
                            or delta.get("reasoning_content")
                            or ""
                        )

                        if isinstance(piece, str) and piece:
                            pieces.append(piece)
                            stats["chunks"] += 1

                text = strip_thinking("".join(pieces))

                if not text:
                    raise LLMError(
                        "Model returned an empty answer."
                    )

                return text

            except (
                httpx.TransportError,
                LLMError,
                KeyError,
                ValueError,
            ) as exc:
                last = exc

                if (
                    isinstance(exc, LLMError)
                    and "model server" in str(exc)
                ):
                    break

                if attempt < retries:
                    time.sleep(2 * (attempt + 1))

            finally:
                stop.set()
                worker.join()

                sys.stdout.write(
                    f"\r  {model}: {stats['chunks']} chunks received "
                    f"in {time.time() - started:.1f}s                    \n"
                )
                sys.stdout.flush()

        raise LLMError(f"{model}: {last}")

    def list_models(self) -> list[str]:
        response = self._http.get("/models")
        response.raise_for_status()
        return [
            model["id"]
            for model in response.json().get("data", [])
        ]

    def chat_json(
        self,
        messages: list[dict],
        *,
        vision: bool = False,
    ) -> dict:
        """Request JSON and retry once with feedback if parsing fails."""
        text = self.chat(
            messages,
            vision=vision,
            temperature=0.1,
        )

        for _ in range(2):
            try:
                return extract_json(text)
            except ValueError as exc:
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            f"That was not valid JSON ({exc}). "
                            "Reply again with ONLY the JSON object."
                        ),
                    },
                ]
                text = self.chat(
                    messages,
                    vision=False,
                    temperature=0.0,
                )

        return extract_json(text)


def extract_json(text: str) -> dict:
    """Extract the first balanced JSON object from model output."""
    start = text.find("{")

    if start < 0:
        raise ValueError("no JSON object found")

    depth = 0
    in_str = False
    esc = False

    for i in range(start, len(text)):
        ch = text[i]

        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False

        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1

            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise ValueError(str(exc)) from exc

                if not isinstance(obj, dict):
                    raise ValueError(
                        "top-level JSON is not an object"
                    )

                return obj

    raise ValueError("unterminated JSON object")