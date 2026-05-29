import asyncio
import logging

from aiohttp import web

from api.ws_client import BackendWebSocketClient
from core.config import load_settings
from services.warden_engine import WardenEngine

logging.basicConfig(level=logging.INFO)


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def on_startup(app: web.Application) -> None:
    settings = load_settings()
    client = BackendWebSocketClient(
        backend_ws_url=settings.backend_ws_url,
        api_key=settings.ai_warden_api_key,
        reconnect_delay_seconds=settings.reconnect_delay_seconds,
        engine=WardenEngine(),
    )
    app["ws_task"] = asyncio.create_task(client.run_forever())


async def on_cleanup(app: web.Application) -> None:
    ws_task = app.get("ws_task")
    if ws_task:
        ws_task.cancel()
        await asyncio.gather(ws_task, return_exceptions=True)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    settings = load_settings()
    web.run_app(create_app(), host=settings.health_host, port=settings.health_port)
