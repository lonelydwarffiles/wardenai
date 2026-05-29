import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

from core.constants import (
    DEFAULT_HEALTH_HOST,
    DEFAULT_HEALTH_PORT,
    WS_ENDPOINT_PATH,
)

load_dotenv()


@dataclass(frozen=True)
class Settings:
    backend_ws_url: str
    ai_warden_api_key: str
    reconnect_delay_seconds: int = 5
    health_host: str = DEFAULT_HEALTH_HOST
    health_port: int = DEFAULT_HEALTH_PORT
    memory_db_path: str = "data/warden_memory.db"


def build_backend_ws_endpoint(base_url: str) -> str:
    parsed = urlsplit(base_url)
    scheme = parsed.scheme or "ws"

    path = parsed.path.rstrip("/")
    if not path.endswith(WS_ENDPOINT_PATH):
        path = f"{path}{WS_ENDPOINT_PATH}" if path else WS_ENDPOINT_PATH

    return urlunsplit((scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def load_settings() -> Settings:
    raw_backend_url = os.getenv("BACKEND_WS_URL", "").strip()
    api_key = os.getenv("AI_WARDEN_API_KEY", "").strip()

    if not raw_backend_url:
        raise ValueError("BACKEND_WS_URL is required")
    if not api_key:
        raise ValueError("AI_WARDEN_API_KEY is required")

    reconnect_delay_seconds = int(os.getenv("RECONNECT_DELAY_SECONDS", "5"))
    health_host = os.getenv("HEALTH_HOST", DEFAULT_HEALTH_HOST)
    health_port = int(os.getenv("HEALTH_PORT", str(DEFAULT_HEALTH_PORT)))
    memory_db_path = os.getenv("MEMORY_DB_PATH", "data/warden_memory.db").strip() or "data/warden_memory.db"

    return Settings(
        backend_ws_url=build_backend_ws_endpoint(raw_backend_url),
        ai_warden_api_key=api_key,
        reconnect_delay_seconds=reconnect_delay_seconds,
        health_host=health_host,
        health_port=health_port,
        memory_db_path=memory_db_path,
    )
