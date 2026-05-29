"""Main Ollama agent loop.

Wires together the Ollama model swarm, the MCP tool execution bridge, and the
infraction logging store.  :class:`OllamaAgentLoop` exposes a
``route_payload`` method that is drop-in compatible with
:class:`services.orchestrator.WardenOrchestrator`, so it can be passed
directly to :class:`api.ws_client.BackendWebSocketClient`.

Routing logic
-------------
* **UserMessage** → Tone Specialist evaluates for compliance (score 1-10).
  Score < 4 or ``compliant=false`` → LOCK_DEVICE issued immediately; Handler
  Agent is bypassed.  Score ≥ 4 → Handler Agent produces a conversational
  reply.
* **TPE_TELEMETRY** → Sensor Specialist exclusively evaluates the telemetry
  payload for geofence or battery-level rule breaches.

When any model triggers an MCP tool call the infraction is persisted to the
local :class:`services.memory_manager.MemoryManager` SQLite vector store and
the result is returned to the caller so that :class:`api.ws_client` can push
it down the WebSocket tunnel to the Camera-Site backend.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.memory_manager import MemoryManager
from services.orchestrator import TERMINATE_SIGNAL, WardenOrchestrator, create_ollama_orchestrator

logger = logging.getLogger(__name__)


class OllamaAgentLoop:
    """Ollama-backed agent loop with MCP tool execution and infraction logging.

    Parameters
    ----------
    api:
        Device API object that exposes ``execute_device_command`` and
        optionally ``post_social_update``.  Pass ``None`` during local
        development; a no-op device executor will be used automatically.
    ollama_host:
        Base URL of the local Ollama HTTP server (default:
        ``"http://localhost:11434"``).
    memory_db_path:
        Path to the SQLite infraction database used as the local vector store.
    orchestrator:
        Pre-built :class:`~services.orchestrator.WardenOrchestrator`.  When
        omitted, :func:`~services.orchestrator.create_ollama_orchestrator` is
        called with the three configured Ollama models.
    """

    def __init__(
        self,
        api: Any,
        ollama_host: str = "http://localhost:11434",
        memory_db_path: str = "data/warden_memory.db",
        orchestrator: Optional[WardenOrchestrator] = None,
    ) -> None:
        self.api = api
        self.memory = MemoryManager(db_path=memory_db_path)
        self.orchestrator = orchestrator or create_ollama_orchestrator(
            api=api,
            ollama_host=ollama_host,
        )

    async def route_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Route a payload through the Ollama swarm, log infractions, and return the result.

        This method is interface-compatible with
        :meth:`services.orchestrator.WardenOrchestrator.route_payload` and
        can be used anywhere a :class:`~services.orchestrator.WardenOrchestrator`
        is expected.

        When a model triggers an MCP tool call the infraction event is written
        to the local vector store *before* returning, so the caller
        (:class:`api.ws_client.BackendWebSocketClient`) can push the result
        down the WebSocket tunnel immediately.
        """
        result = await self.orchestrator.route_payload(payload)

        if result.get("infraction") or result.get("signal") == TERMINATE_SIGNAL:
            self._log_infraction(payload, result)

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log_infraction(self, payload: Any, result: Any) -> None:
        """Persist an infraction record to the local SQLite vector store."""
        timestamp = datetime.now(timezone.utc).isoformat()
        action_taken = self._summarise_action(result)
        try:
            self.memory.log_infraction(
                timestamp=timestamp,
                context=payload,
                action_taken=action_taken,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to log infraction to memory store: %s", exc)
        logger.info(
            "Infraction logged: agent=%s action=%s",
            result.get("agent"),
            action_taken,
        )

    @staticmethod
    def _summarise_action(result: Dict[str, Any]) -> str:
        """Extract a concise, loggable action string from an orchestrator result."""
        tool_result = result.get("tool_result")
        if isinstance(tool_result, dict):
            content = tool_result.get("content")
            if isinstance(content, list) and content:
                inner = content[0].get("json")
                if inner:
                    return json.dumps(inner, default=str)
        return result.get("signal") or "INFRACTION"
