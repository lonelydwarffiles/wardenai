import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from services.mcp_server import DeviceCommandMCPServer, MCPClient, TOOL_NAME

TERMINATE_SIGNAL = "TERMINATE"
USER_MESSAGE_TYPE = "UserMessage"
TELEMETRY_TYPE = "TPE_TELEMETRY"
DEFAULT_INFRACTION_COMMAND = {"command": "LOCK_DEVICE"}


async def route_payload(
    payload: Dict[str, Any],
    handler_agent: "HandlerAgent",
    tone_specialist: "ToneSpecialist",
    sensor_specialist: "SensorSpecialist",
) -> Dict[str, Any]:
    payload_type = _resolve_payload_type(payload)

    if payload_type == USER_MESSAGE_TYPE:
        tone_result = await tone_specialist.evaluate(payload)
        if tone_result.get("signal") == TERMINATE_SIGNAL:
            return tone_result
        handler_result = await handler_agent.respond(payload)
        return {"agent": handler_agent.name, "tone": tone_result, **handler_result}

    if payload_type == TELEMETRY_TYPE:
        return await sensor_specialist.evaluate(payload)

    return {"error": "Unsupported payload type", "payload_type": payload_type}


class WardenOrchestrator:
    def __init__(
        self,
        api: Any,
        handler_model: Any = None,
        tone_model: Any = None,
        sensor_model: Any = None,
        mcp_client: Optional[MCPClient] = None,
    ) -> None:
        shared_mcp_client = mcp_client or MCPClient(DeviceCommandMCPServer(api or _NoOpDeviceExecutor()))
        self.handler_agent = HandlerAgent(model=handler_model)
        self.tone_specialist = ToneSpecialist(model=tone_model, mcp_client=shared_mcp_client)
        self.sensor_specialist = SensorSpecialist(model=sensor_model, mcp_client=shared_mcp_client)

    async def route_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await route_payload(
            payload=payload,
            handler_agent=self.handler_agent,
            tone_specialist=self.tone_specialist,
            sensor_specialist=self.sensor_specialist,
        )


@dataclass
class HandlerAgent:
    model: Any = None
    name: str = "Handler_Agent"

    async def respond(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        user_text = _extract_user_text(payload)
        if self.model is None:
            return {"reply": user_text}

        result = await _call_model(self.model, payload={"text": user_text, "payload": payload})
        if isinstance(result, str):
            return {"reply": result}
        if isinstance(result, dict):
            if "reply" in result:
                return {"reply": result["reply"]}
            return result
        return {"reply": str(result)}


@dataclass
class ToneSpecialist:
    model: Any = None
    mcp_client: Optional[MCPClient] = None
    name: str = "Tone_Specialist"

    async def evaluate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.mcp_client is None:
            raise ValueError("Tone_Specialist requires an MCP client")

        user_text = _extract_user_text(payload)
        tools = await self.mcp_client.list_tools()
        model_output = await _call_model(self.model, payload={"text": user_text, "payload": payload}, tools=tools)
        return await _evaluate_specialist_result(
            specialist_name=self.name,
            mcp_client=self.mcp_client,
            model_output=model_output,
            tools=tools,
        )


@dataclass
class SensorSpecialist:
    model: Any = None
    mcp_client: Optional[MCPClient] = None
    name: str = "Sensor_Specialist"

    async def evaluate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.mcp_client is None:
            raise ValueError("Sensor_Specialist requires an MCP client")

        tools = await self.mcp_client.list_tools()
        telemetry = payload.get("telemetry", payload)
        model_output = await _call_model(self.model, payload={"telemetry": telemetry, "payload": payload}, tools=tools)
        return await _evaluate_specialist_result(
            specialist_name=self.name,
            mcp_client=self.mcp_client,
            model_output=model_output,
            tools=tools,
        )


async def _evaluate_specialist_result(
    specialist_name: str,
    mcp_client: MCPClient,
    model_output: Any,
    tools: List[Dict[str, Any]],
) -> Dict[str, Any]:
    tool_call = _extract_tool_call(model_output, tools)
    if tool_call:
        tool_result = await mcp_client.call_tool(tool_call["name"], tool_call["arguments"])
        return {
            "agent": specialist_name,
            "signal": TERMINATE_SIGNAL,
            "infraction": True,
            "tool_result": tool_result,
        }

    if isinstance(model_output, dict):
        infraction = bool(model_output.get("infraction")) or model_output.get("compliant") is False
        if infraction:
            arguments = model_output.get("arguments")
            if not isinstance(arguments, dict):
                arguments = DEFAULT_INFRACTION_COMMAND
            tool_result = await mcp_client.call_tool(TOOL_NAME, arguments)
            return {
                "agent": specialist_name,
                "signal": TERMINATE_SIGNAL,
                "infraction": True,
                "tool_result": tool_result,
            }
        return {"agent": specialist_name, "infraction": False, **model_output}

    return {"agent": specialist_name, "infraction": False, "result": model_output}


def _resolve_payload_type(payload: Dict[str, Any]) -> str:
    payload_type = str(payload.get("type") or payload.get("event_type") or "")
    if payload_type in {USER_MESSAGE_TYPE, TELEMETRY_TYPE}:
        return payload_type
    if any(field in payload for field in ("text", "message", "user_text")):
        return USER_MESSAGE_TYPE
    if "telemetry" in payload or any(field in payload for field in ("battery", "gps", "system_state")):
        return TELEMETRY_TYPE
    return payload_type or "UNKNOWN"


def _extract_user_text(payload: Dict[str, Any]) -> str:
    candidate = payload.get("text") or payload.get("message") or payload.get("user_text")
    if isinstance(candidate, str):
        return candidate
    return json.dumps(candidate or "", ensure_ascii=False)


def _extract_tool_call(raw_output: Any, available_tools: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    allowed_tool_names = {tool["name"] for tool in available_tools}
    candidates = _iter_tool_candidates(raw_output)
    for candidate in candidates:
        tool_name = candidate.get("name")
        arguments = candidate.get("arguments")
        if tool_name not in allowed_tool_names:
            continue
        if not isinstance(arguments, dict):
            continue
        return {"name": tool_name, "arguments": arguments}
    return None


def _iter_tool_candidates(raw_output: Any) -> List[Dict[str, Any]]:
    if raw_output is None:
        return []
    if isinstance(raw_output, list):
        return [candidate for item in raw_output for candidate in _iter_tool_candidates(item)]
    if not isinstance(raw_output, dict):
        return []

    if {"name", "arguments"} <= set(raw_output):
        return [raw_output]
    if {"tool", "input"} <= set(raw_output):
        return [{"name": raw_output["tool"], "arguments": raw_output["input"]}]
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
    return []


async def _call_model(model: Any, payload: Dict[str, Any], tools: Optional[List[Dict[str, Any]]] = None) -> Any:
    if model is None:
        return {}

    messages = [
        {"role": "system", "content": "You are an AI Warden specialist."},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    model_kwargs = {"messages": messages}
    if tools is not None:
        model_kwargs["tools"] = tools

    if hasattr(model, "generate"):
        return await _maybe_await(model.generate(**model_kwargs))
    if hasattr(model, "complete"):
        return await _maybe_await(model.complete(**model_kwargs))
    if callable(model):
        return await _maybe_await(model(**model_kwargs))
    return model


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


class _NoOpDeviceExecutor:
    async def execute_device_command(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": False, "error": "Device command executor is not configured", "arguments": arguments}
