"""Runtime configuration for optional live providers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv()


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    openrouter_api_key: str | None = os.getenv("OPENROUTER_API_KEY")
    openrouter_model: str | None = os.getenv(
        "OPENROUTER_FREE_MODEL", "liquid/lfm-2.5-1.2b-instruct:free"
    )
    openrouter_base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    google_maps_api_key: str | None = os.getenv("GOOGLE_MAPS_API_KEY")

    flight_search_api_url: str | None = os.getenv("FLIGHT_SEARCH_API_URL")
    flight_search_api_key: str | None = os.getenv("FLIGHT_SEARCH_API_KEY")

    hotel_search_api_url: str | None = os.getenv("HOTEL_SEARCH_API_URL")
    hotel_search_api_key: str | None = os.getenv("HOTEL_SEARCH_API_KEY")

    request_timeout_seconds: float = float(os.getenv("TRAVEL_AGENT_TIMEOUT", "15"))


def load_settings() -> Settings:
    return Settings()
