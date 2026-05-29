import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from services.mcp_server import DEVICE_COMMAND_SCHEMAS, DeviceCommandMCPServer, MCPClient, TOOL_NAME

logger = logging.getLogger(__name__)
SYSTEM_INSTRUCTIONS = (
    "You are the AI Warden action planner.\n"
    "Use only the provided MCP tools.\n"
    "If no action is required, do not call any tool.\n"
    f"If action is required, call the MCP tool named '{TOOL_NAME}' using one of these exact commands: "
    + ", ".join(DEVICE_COMMAND_SCHEMAS)
    + ".\n"
    "Do not invent tools or arguments."
)


async def evaluate_state(telemetry: Any, model: Any, mcp_client: MCPClient) -> Optional[Dict[str, Any]]:
    tools = await mcp_client.list_tools()
    raw_output = await _call_model(model=model, telemetry=telemetry, tools=tools)
    return _extract_tool_call(raw_output=raw_output, available_tools=tools)


class AgentLoop:
    def __init__(self, api: Any, model: Any, mcp_client: Optional[MCPClient] = None) -> None:
        self.api = api
        self.model = model
        self.mcp_client = mcp_client or MCPClient(DeviceCommandMCPServer(api))
        self._running = False

    async def run(self) -> None:
        self._running = True
        websocket_stream = _resolve_websocket_stream(self.api)

        async for packet in websocket_stream:
            if not self._running:
                break

            action_request = await evaluate_state(packet, self.model, self.mcp_client)
            if not action_request:
                continue

            await self._execute_action(action_request)

    def stop(self) -> None:
        self._running = False

    async def _execute_action(self, action_request: Dict[str, Any]) -> None:
        await self.mcp_client.call_tool(action_request["name"], action_request["arguments"])


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


async def _call_model(model: Any, telemetry: Any, tools: List[Dict[str, Any]]) -> Any:
    messages = [
        {"role": "system", "content": SYSTEM_INSTRUCTIONS},
        {"role": "user", "content": json.dumps({"telemetry": telemetry}, ensure_ascii=False)},
    ]

    if hasattr(model, "generate"):
        return await _maybe_await(model.generate(messages=messages, tools=tools))
    if hasattr(model, "complete"):
        return await _maybe_await(model.complete(messages=messages, tools=tools))
    if callable(model):
        return await _maybe_await(model(messages=messages, tools=tools))

    raise TypeError("model must provide generate(), complete(), or be callable")


def _extract_tool_call(raw_output: Any, available_tools: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    allowed_tool_names = {tool["name"] for tool in available_tools}
    for candidate in _iter_tool_candidates(raw_output):
        tool_name = candidate.get("name")
        arguments = candidate.get("arguments")
        if tool_name not in allowed_tool_names:
            continue
        if not isinstance(arguments, dict):
            continue
        return {"name": tool_name, "arguments": arguments}

    if raw_output is not None and not isinstance(raw_output, str):
        logger.warning("Model response did not contain a usable MCP tool call")
    if isinstance(raw_output, str) and raw_output.strip():
        logger.warning("Ignoring text model output because MCP tool calls are required")
    return None


def _iter_tool_candidates(raw_output: Any) -> List[Dict[str, Any]]:
    if raw_output is None:
        return []
    if isinstance(raw_output, list):
        return [candidate for item in raw_output for candidate in _iter_tool_candidates(item)]
    if not isinstance(raw_output, dict):
        return []

    if {"name", "arguments"} <= set(raw_output.keys()):
        return [raw_output]
    if {"tool", "input"} <= set(raw_output.keys()):
        return [{"name": raw_output["tool"], "arguments": raw_output["input"]}]
    if {"tool_name", "arguments"} <= set(raw_output.keys()):
        return [{"name": raw_output["tool_name"], "arguments": raw_output["arguments"]}]

    if "tool_calls" in raw_output and isinstance(raw_output["tool_calls"], list):
        return [candidate for item in raw_output["tool_calls"] for candidate in _iter_tool_candidates(item)]
    if "content" in raw_output and isinstance(raw_output["content"], list):
        candidates: List[Dict[str, Any]] = []
        for item in raw_output["content"]:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                candidates.append({"name": item.get("name"), "arguments": item.get("input")})
            else:
                candidates.extend(_iter_tool_candidates(item))
        return candidates
    if "function" in raw_output and isinstance(raw_output["function"], dict):
        function = raw_output["function"]
        return [{"name": function.get("name"), "arguments": function.get("arguments")}]

    return []


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
