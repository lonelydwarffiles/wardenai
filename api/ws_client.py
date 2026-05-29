import asyncio
import json
import logging
from typing import Any, Dict

from aiohttp import ClientSession, WSMsgType

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
                            elif message.type == WSMsgType.ERROR:
                                logger.exception("WebSocket error: %s", ws.exception())
                                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("WebSocket disconnected (%s). Reconnecting in %s seconds...", exc, self.reconnect_delay_seconds)
                await asyncio.sleep(self.reconnect_delay_seconds)
