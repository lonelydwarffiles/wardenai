import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from services.mcp_server import (
    DEVICE_COMMAND_TOOL,
    POST_SOCIAL_UPDATE_TOOL,
    DeviceCommandMCPServer,
    MCPClient,
    TOOL_NAME,
)
from services.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

TERMINATE_SIGNAL = "TERMINATE"
USER_MESSAGE_TYPE = "UserMessage"
TELEMETRY_TYPE = "TPE_TELEMETRY"
DEFAULT_INFRACTION_COMMAND = {"command": "LOCK_DEVICE"}

# ---------------------------------------------------------------------------
# Ollama model names
# ---------------------------------------------------------------------------
HANDLER_MODEL_NAME = "dolphin-llama3:8b"
TONE_MODEL_NAME = "bartowski/Llama-3.2-3B-Instruct-uncensored-GGUF"
SENSOR_MODEL_NAME = "dolphin-phi:2.7b"

# ---------------------------------------------------------------------------
# System prompts with inline JSON tool schemas
# ---------------------------------------------------------------------------
def _tool_schema_block(tools: List[Dict[str, Any]]) -> str:
    """Render tool definitions as a human-readable schema block for injection into system prompts."""
    lines: List[str] = []
    for tool in tools:
        lines.append(f"\nTool name: {tool['name']}")
        lines.append(f"Description: {tool['description']}")
        lines.append("Input schema:")
        lines.append(json.dumps(tool["inputSchema"], indent=2))
    return "\n".join(lines)


_SHARED_TOOL_BLOCK = _tool_schema_block([DEVICE_COMMAND_TOOL, POST_SOCIAL_UPDATE_TOOL])
_TONE_TOOL_BLOCK = _tool_schema_block([DEVICE_COMMAND_TOOL])

HANDLER_SYSTEM_PROMPT = (
    "You are the AI Warden Handler Agent.\n"
    "Your role is authoritative conversation and task assignment.\n"
    "When users are compliant, provide clear instructions and assign tasks.\n"
    "You have access to the following MCP tools:\n"
    + _SHARED_TOOL_BLOCK
    + "\n\nUse these tools when appropriate to enforce rules or communicate publicly."
)


def _build_handler_prompt(effective_phrases: List[str]) -> str:
    if not effective_phrases:
        return HANDLER_SYSTEM_PROMPT
    top_phrases = "\n".join(f"- {phrase}" for phrase in effective_phrases[:3])
    return (
        HANDLER_SYSTEM_PROMPT
        + "\n\nMost Effective Correction Phrases (weekly meta-optimized):\n"
        + top_phrases
        + "\nUse these phrases naturally when providing correction guidance."
    )

TONE_SYSTEM_PROMPT = (
    "You are the AI Warden Tone Specialist.\n"
    "Strictly evaluate text for submissive tone and compliance.\n\n"
    "Respond ONLY with a JSON object:\n"
    '{\n'
    '  "compliance_score": <integer 1-10, where 10=fully compliant/submissive, 1=defiant>,\n'
    '  "compliant": <true if compliance_score >= 4, false otherwise>,\n'
    '  "reason": "<brief explanation>"\n'
    "}\n\n"
    "If compliance_score < 4, you MUST also call the execute_device_command MCP tool with LOCK_DEVICE.\n\n"
    "Available MCP tools:\n"
    + _TONE_TOOL_BLOCK
)

SENSOR_SYSTEM_PROMPT = (
    "You are the AI Warden Sensor Specialist.\n"
    "Strictly process numerical telemetry, geofences, and battery levels.\n\n"
    "Evaluate the telemetry payload for rule breaches:\n"
    "- Battery level below 15 %: call execute_device_command with PAVLOK_COMMAND (mode=\"beep\", intensity=50).\n"
    "- Geofence radius violation: call execute_device_command with LOCK_DEVICE.\n"
    "- Abnormal system state: call execute_device_command with the appropriate command.\n\n"
    "If a breach is detected, call the appropriate MCP tool immediately.\n"
    'If no breach is detected, respond with: {"infraction": false}\n\n'
    "Available MCP tools:\n"
    + _SHARED_TOOL_BLOCK
)


# ---------------------------------------------------------------------------
# Ollama model wrapper
# ---------------------------------------------------------------------------
class OllamaModel:
    """Wraps an Ollama chat model for use as a specialist or handler model.

    Parameters
    ----------
    model_name:
        The name of the model as registered in the local Ollama server
        (e.g. ``"dolphin-llama3:8b"``).
    system_prompt:
        Role-specific system prompt with injected MCP tool schemas.
    host:
        Base URL of the local Ollama HTTP server.
    """

    def __init__(
        self,
        model_name: str,
        system_prompt: str,
        host: str = "http://localhost:11434",
    ) -> None:
        import ollama  # imported lazily so the module loads without ollama installed

        self._model_name = model_name
        self._system_prompt = system_prompt
        self._client = ollama.AsyncClient(host=host)

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt_override: Optional[str] = None,
    ) -> Any:
        # Replace any generic system message with the specialist-specific prompt
        non_system = [m for m in messages if m.get("role") != "system"]
        prompt = system_prompt_override or self._system_prompt
        full_messages = [{"role": "system", "content": prompt}] + non_system

        kwargs: Dict[str, Any] = {"model": self._model_name, "messages": full_messages}
        if tools:
            kwargs["tools"] = tools

        response = await self._client.chat(**kwargs)
        return _parse_ollama_response(response)


def _parse_ollama_response(response: Any) -> Dict[str, Any]:
    """Normalise a raw Ollama ChatResponse into the internal tool-call / reply format."""
    if isinstance(response, dict):
        message: Any = response.get("message", {})
    else:
        message = getattr(response, "message", {})

    if isinstance(message, dict):
        content: str = message.get("content", "") or ""
        tool_calls_raw: Any = message.get("tool_calls") or []
    else:
        content = getattr(message, "content", "") or ""
        tool_calls_raw = getattr(message, "tool_calls", None) or []

    if tool_calls_raw:
        parsed_calls: List[Dict[str, Any]] = []
        for call in tool_calls_raw:
            if isinstance(call, dict):
                fn = call.get("function", call)
                name = fn.get("name") if isinstance(fn, dict) else None
                arguments = fn.get("arguments", {}) if isinstance(fn, dict) else {}
            else:
                fn = getattr(call, "function", call)
                name = getattr(fn, "name", None)
                arguments = getattr(fn, "arguments", {}) or {}

            if name:
                parsed_calls.append(
                    {"name": name, "arguments": arguments if isinstance(arguments, dict) else {}}
                )
        if parsed_calls:
            return {"tool_calls": parsed_calls}

    if content:
        try:
            return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return {"reply": content}

    return {}


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------
def create_ollama_orchestrator(
    api: Any,
    mcp_client: Optional[MCPClient] = None,
    memory_manager: Optional[MemoryManager] = None,
    ollama_host: str = "http://localhost:11434",
) -> "WardenOrchestrator":
    """Create a :class:`WardenOrchestrator` backed by local Ollama models.

    Parameters
    ----------
    api:
        Device API object that exposes ``execute_device_command``.  May be
        ``None`` during local development; a no-op executor will be used.
    mcp_client:
        Pre-built :class:`MCPClient`.  Created automatically when omitted.
    ollama_host:
        Base URL of the local Ollama HTTP server.
    """
    handler_model = OllamaModel(HANDLER_MODEL_NAME, HANDLER_SYSTEM_PROMPT, host=ollama_host)
    tone_model = OllamaModel(TONE_MODEL_NAME, TONE_SYSTEM_PROMPT, host=ollama_host)
    sensor_model = OllamaModel(SENSOR_MODEL_NAME, SENSOR_SYSTEM_PROMPT, host=ollama_host)
    return WardenOrchestrator(
        api=api,
        handler_model=handler_model,
        tone_model=tone_model,
        sensor_model=sensor_model,
        mcp_client=mcp_client,
        memory_manager=memory_manager,
    )


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
        memory_manager: Optional[MemoryManager] = None,
    ) -> None:
        shared_mcp_client = mcp_client or MCPClient(DeviceCommandMCPServer(api or _NoOpDeviceExecutor()))
        self.handler_agent = HandlerAgent(model=handler_model, memory_manager=memory_manager)
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
    memory_manager: Optional[MemoryManager] = None
    name: str = "Handler_Agent"
    optimization_interval_days: int = 7
    _last_optimization_at: Optional[datetime] = None
    _effective_phrases: List[str] = field(default_factory=list)

    async def respond(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        user_text = _extract_user_text(payload)
        if self.model is None:
            return {"reply": user_text}

        self._refresh_effective_phrases_if_due()
        system_prompt_override = _build_handler_prompt(self._effective_phrases)
        result = await _call_model(
            self.model,
            payload={"text": user_text, "payload": payload},
            system_prompt_override=system_prompt_override,
        )
        if isinstance(result, str):
            return {"reply": result}
        if isinstance(result, dict):
            if "reply" in result:
                return {"reply": result["reply"]}
            return result
        return {"reply": str(result)}

    def _refresh_effective_phrases_if_due(self) -> None:
        if self.memory_manager is None:
            return

        now = datetime.now(timezone.utc)
        if self._last_optimization_at and (now - self._last_optimization_at) < timedelta(
            days=self.optimization_interval_days
        ):
            return

        highest_impact = self.memory_manager.get_highest_impact_patterns(limit=3)
        self._effective_phrases = [item["correction_pattern"] for item in highest_impact]
        self._last_optimization_at = now


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
        score = model_output.get("compliance_score")
        score_below_threshold = (
            isinstance(score, int)
            and not isinstance(score, bool)
            and score < 4
        )
        infraction = (
            bool(model_output.get("infraction"))
            or model_output.get("compliant") is False
            or score_below_threshold
        )
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


async def _call_model(
    model: Any,
    payload: Dict[str, Any],
    tools: Optional[List[Dict[str, Any]]] = None,
    system_prompt_override: Optional[str] = None,
) -> Any:
    if model is None:
        return {}

    messages = [
        {"role": "system", "content": "You are an AI Warden specialist."},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    model_kwargs = {"messages": messages}
    if tools is not None:
        model_kwargs["tools"] = tools
    if system_prompt_override is not None:
        model_kwargs["system_prompt_override"] = system_prompt_override

    if hasattr(model, "generate"):
        try:
            return await _maybe_await(model.generate(**model_kwargs))
        except TypeError:
            if "system_prompt_override" in model_kwargs:
                model_kwargs.pop("system_prompt_override", None)
                return await _maybe_await(model.generate(**model_kwargs))
            raise
    if hasattr(model, "complete"):
        try:
            return await _maybe_await(model.complete(**model_kwargs))
        except TypeError:
            if "system_prompt_override" in model_kwargs:
                model_kwargs.pop("system_prompt_override", None)
                return await _maybe_await(model.complete(**model_kwargs))
            raise
    if callable(model):
        try:
            return await _maybe_await(model(**model_kwargs))
        except TypeError:
            if "system_prompt_override" in model_kwargs:
                model_kwargs.pop("system_prompt_override", None)
                return await _maybe_await(model(**model_kwargs))
            raise
    return model


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


class _NoOpDeviceExecutor:
    async def execute_device_command(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": False, "error": "Device command executor is not configured", "arguments": arguments}
