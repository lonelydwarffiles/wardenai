import asyncio
import logging

from aiohttp import web

from api.ws_client import BackendWebSocketClient
from core.agent_loop import OllamaAgentLoop
from core.config import load_settings

logging.basicConfig(level=logging.INFO)


async def health(request: web.Request) -> web.Response:
    startup = request.app.get("startup_check") or {}
    startup_ok = bool(startup.get("ok"))
    return web.json_response(
        {
            "status": "ok" if startup_ok else "starting",
            "startup_check_ok": startup_ok,
            "startup_check": startup,
        }
    )


async def startup_check(request: web.Request) -> web.Response:
    status = request.app.get("startup_check") or {"ok": False, "detail": "startup check has not run"}
    http_status = 200 if status.get("ok") else 503
    return web.json_response(status, status=http_status)


async def on_startup(app: web.Application) -> None:
    settings = load_settings()
    app["startup_check"] = {"ok": False, "detail": "startup check running"}
    agent_loop = OllamaAgentLoop(
        api=None,
        ollama_host=settings.ollama_host,
        memory_db_path=settings.memory_db_path,
    )
    client = BackendWebSocketClient(
        backend_ws_url=settings.backend_ws_url,
        api_key=settings.ai_warden_api_key,
        reconnect_delay_seconds=settings.reconnect_delay_seconds,
        orchestrator=agent_loop,  # type: ignore[arg-type]
    )
    startup_status = await client.validate_backend_contract(settings.backend_http_base_url)
    app["startup_check"] = startup_status
    app["ws_task"] = asyncio.create_task(client.run_forever())


async def on_cleanup(app: web.Application) -> None:
    ws_task = app.get("ws_task")
    if ws_task:
        ws_task.cancel()
        await asyncio.gather(ws_task, return_exceptions=True)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/startup-check", startup_check)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    settings = load_settings()
    web.run_app(create_app(), host=settings.health_host, port=settings.health_port)
