"""Settings come from environment variables, optionally loaded from a .env file in the cwd."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    text_model: str
    vision_model: str
    temperature: float
    max_tokens: int
    timeout: float
    max_repairs: int

    @classmethod
    def load(cls) -> "Settings":
        _load_dotenv(Path.cwd() / ".env")
        e = os.environ.get
        return cls(
            base_url=e("QWEN_BASE_URL", "http://localhost:11434/v1").rstrip("/"),
            api_key=e("QWEN_API_KEY", "ollama"),
            text_model=e("QWEN_TEXT_MODEL", "qwen3-coder:30b"),
            vision_model=e("QWEN_VISION_MODEL", "qwen3-vl:8b"),
            temperature=float(e("QWEN_TEMPERATURE", "0.2")),
            max_tokens=int(e("QWEN_MAX_TOKENS", "16000")),
            timeout=float(e("QWEN_TIMEOUT", "900")),
            max_repairs=int(e("SITEAGENT_MAX_REPAIRS", "3")),
        )
