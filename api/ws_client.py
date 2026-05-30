import asyncio
import json
import logging
from typing import Any, Dict
from urllib.parse import urljoin

from aiohttp import ClientSession, WSMsgType, WSServerHandshakeError

from services.orchestrator import WardenOrchestrator

logger = logging.getLogger(__name__)


class BackendWebSocketClient:
    def __init__(
        self,
        backend_ws_url: str,
        api_key: str,
        reconnect_delay_seconds: int,
        orchestrator: WardenOrchestrator,
    ) -> None:
        self.backend_ws_url = backend_ws_url
        self.api_key = api_key
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.orchestrator = orchestrator

    async def _handle_message(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return await self.orchestrator.route_payload(data)

    async def _assert_ws_authorized(self, session: ClientSession) -> None:
        try:
            ws = await session.ws_connect(self.backend_ws_url, timeout=5)
        except WSServerHandshakeError as exc:
            if exc.status in (401, 403):
                raise RuntimeError(
                    f"AI Warden websocket auth failed ({exc.status}). Check AI_WARDEN_API_KEY and backend ai_warden_api_key setting."
                ) from exc
            raise RuntimeError(f"AI Warden startup check failed (websocket handshake {exc.status}): {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"AI Warden startup check failed (websocket connect): {exc}") from exc

        try:
            try:
                message = await ws.receive(timeout=0.35)
                if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
                    close_code = int(ws.close_code or 0)
                    if close_code == 4001:
                        raise RuntimeError(
                            "AI Warden websocket rejected with close code 4001 (invalid bearer secret)."
                        )
                    raise RuntimeError(
                        f"AI Warden websocket closed during startup check (close_code={close_code})."
                    )
                if message.type == WSMsgType.ERROR:
                    raise RuntimeError(f"AI Warden websocket error during startup check: {ws.exception()}")
            except TimeoutError:
                # No frame received immediately means the connection stayed open.
                pass
        finally:
            await ws.close()

    async def validate_backend_contract(self, backend_http_base_url: str) -> Dict[str, Any]:
        if not backend_http_base_url:
            raise RuntimeError("Backend HTTP base URL is empty; cannot run startup contract checks.")

        headers = {"Authorization": "Bearer " + self.api_key}
        runtime_url = urljoin(backend_http_base_url.rstrip("/") + "/", "api/handler/ai-warden/runtime")
        report_url = urljoin(backend_http_base_url.rstrip("/") + "/", "api/handler/ai-warden/report")

        async with ClientSession(headers=headers) as session:
            await self._assert_ws_authorized(session)

            runtime_response = await session.get(runtime_url, timeout=5)
            if runtime_response.status != 200:
                body = await runtime_response.text()
                if runtime_response.status in (401, 403):
                    raise RuntimeError(
                        "AI Warden startup check failed (runtime endpoint auth). "
                        "Check AI_WARDEN_API_KEY and backend ai_warden_api_key setting."
                    )
                raise RuntimeError(
                    f"AI Warden startup check failed (runtime endpoint {runtime_url} -> {runtime_response.status}): {body[:240]}"
                )

            report_probe_response = await session.post(report_url, json={}, timeout=5)
            if report_probe_response.status in (401, 403):
                body = await report_probe_response.text()
                raise RuntimeError(
                    "AI Warden startup check failed (report endpoint auth). "
                    f"status={report_probe_response.status} body={body[:240]}"
                )
            if report_probe_response.status not in (400, 422):
                body = await report_probe_response.text()
                raise RuntimeError(
                    f"AI Warden startup check failed (report endpoint {report_url} -> {report_probe_response.status}): {body[:240]}"
                )

        return {
            "ok": True,
            "ws_url": self.backend_ws_url,
            "runtime_url": runtime_url,
            "runtime_status": 200,
            "report_url": report_url,
            "report_probe_status": int(report_probe_response.status),
        }

    async def run_forever(self) -> None:
        headers = {"Authorization": "Bearer " + self.api_key}

        while True:
            try:
                async with ClientSession(headers=headers) as session:
                    async with session.ws_connect(self.backend_ws_url) as ws:
                        logger.info("Connected to backend websocket: %s", self.backend_ws_url)
                        async for message in ws:
                            if message.type == WSMsgType.TEXT:
                                data = json.loads(message.data)
                                response = await self._handle_message(data)
                                await ws.send_json(response)
                            elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
                                logger.warning(
                                    "AI Warden websocket closed by backend. close_code=%s",
                                    ws.close_code,
                                )
                                break
                            elif message.type == WSMsgType.ERROR:
                                logger.exception("WebSocket error: %s", ws.exception())
                                break
            except WSServerHandshakeError as exc:
                if exc.status in (401, 403):
                    logger.error(
                        "WebSocket auth failed (%s). Verify AI_WARDEN_API_KEY and backend ai_warden_api_key setting.",
                        exc.status,
                    )
                else:
                    logger.warning(
                        "WebSocket handshake failed (%s). Reconnecting in %s seconds...",
                        exc.status,
                        self.reconnect_delay_seconds,
                    )
                await asyncio.sleep(self.reconnect_delay_seconds)
            except Exception as exc:  # noqa: BLE001
                logger.warning("WebSocket disconnected (%s). Reconnecting in %s seconds...", exc, self.reconnect_delay_seconds)
                await asyncio.sleep(self.reconnect_delay_seconds)
