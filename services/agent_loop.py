import json
import logging
from typing import Any, AsyncIterator, Dict, Optional

logger = logging.getLogger(__name__)

TOOL_NAME = "execute_device_command"
TOOL_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "enum": [TOOL_NAME]},
        "action": {"type": "string", "minLength": 1},
    },
    "required": ["tool", "action"],
    "additionalProperties": False,
}

SYSTEM_INSTRUCTIONS = (
    "You are the AI Warden action planner.\n"
    "If no action is required, return exactly: null\n"
    "If action is required, return only one JSON object that strictly matches this schema: "
    '{"tool":"execute_device_command","action":"<non-empty string>"}\n'
    "Do not include markdown, prose, code fences, or extra keys."
)


async def evaluate_state(telemetry: Any, model: Any) -> Optional[Dict[str, str]]:
    raw_output = await _call_model(model=model, telemetry=telemetry)
    if raw_output is None:
        return None

    if isinstance(raw_output, dict):
        candidate = raw_output
    else:
        text = str(raw_output).strip()
        if text == "null":
            return None
        candidate = _safe_json_loads(text)

    return _validate_action(candidate)


class AgentLoop:
    def __init__(self, api: Any, model: Any) -> None:
        self.api = api
        self.model = model
        self._running = False

    async def run(self) -> None:
        self._running = True
        websocket_stream = _resolve_websocket_stream(self.api)

        async for packet in websocket_stream:
            if not self._running:
                break

            action_request = await evaluate_state(packet, self.model)
            if not action_request:
                continue

            await self._execute_action(action_request)

    def stop(self) -> None:
        self._running = False

    async def _execute_action(self, action_request: Dict[str, str]) -> None:
        action = action_request["action"]

        if hasattr(self.api, TOOL_NAME):
            executor = getattr(self.api, TOOL_NAME)
            await _maybe_await(executor(action))
            return

        if hasattr(self.api, "tool_executor") and hasattr(self.api.tool_executor, TOOL_NAME):
            executor = getattr(self.api.tool_executor, TOOL_NAME)
            await _maybe_await(executor(action))
            return

        logger.warning("Action generated but no executor found for tool '%s'", TOOL_NAME)


def _resolve_websocket_stream(api: Any) -> AsyncIterator[Any]:
    websocket_client = getattr(api, "websocket_client", None)
    if websocket_client is None:
        raise AttributeError("api.websocket_client is required")

    if hasattr(websocket_client, "__aiter__"):
        return websocket_client

    stream_method = getattr(websocket_client, "stream", None)
    if stream_method and callable(stream_method):
        stream = stream_method()
        if hasattr(stream, "__aiter__"):
            return stream

    raise TypeError("api.websocket_client must be an async iterator or expose stream()")


async def _call_model(model: Any, telemetry: Any) -> Any:
    messages = [
        {"role": "system", "content": SYSTEM_INSTRUCTIONS},
        {"role": "user", "content": json.dumps({"telemetry": telemetry}, ensure_ascii=False)},
    ]

    if hasattr(model, "generate"):
        return await _maybe_await(model.generate(messages=messages, tools=[TOOL_SCHEMA]))
    if hasattr(model, "complete"):
        return await _maybe_await(model.complete(messages=messages, tools=[TOOL_SCHEMA]))
    if callable(model):
        return await _maybe_await(model(messages=messages, tools=[TOOL_SCHEMA]))

    raise TypeError("model must provide generate(), complete(), or be callable")


def _safe_json_loads(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        logger.warning("Model returned invalid JSON for action request")
        return None


def _validate_action(candidate: Any) -> Optional[Dict[str, str]]:
    if candidate is None or not isinstance(candidate, dict):
        return None

    if set(candidate.keys()) != {"tool", "action"}:
        return None

    tool = candidate.get("tool")
    action = candidate.get("action")
    if tool != TOOL_NAME or not isinstance(action, str) or not action.strip():
        return None

    return {"tool": tool, "action": action.strip()}


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
