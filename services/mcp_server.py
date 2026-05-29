import itertools
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

TOOL_NAME = "execute_device_command"
POST_SOCIAL_UPDATE_TOOL_NAME = "post_social_update"

LOCK_DEVICE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "const": "LOCK_DEVICE"},
    },
    "required": ["command"],
    "additionalProperties": False,
}

PAVLOK_COMMAND_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "const": "PAVLOK_COMMAND"},
        "mode": {"type": "string", "enum": ["beep", "vibrate", "zap"]},
        "intensity": {"type": "integer", "minimum": 1, "maximum": 100},
    },
    "required": ["command", "mode", "intensity"],
    "additionalProperties": False,
}

TASK_ASSIGNED_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "const": "TASK_ASSIGNED"},
        "task": {"type": "string", "minLength": 1},
    },
    "required": ["command", "task"],
    "additionalProperties": False,
}

SET_BRIGHTNESS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "const": "SET_BRIGHTNESS"},
        "level": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "required": ["command", "level"],
    "additionalProperties": False,
}

DEVICE_COMMAND_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "LOCK_DEVICE": LOCK_DEVICE_SCHEMA,
    "PAVLOK_COMMAND": PAVLOK_COMMAND_SCHEMA,
    "TASK_ASSIGNED": TASK_ASSIGNED_SCHEMA,
    "SET_BRIGHTNESS": SET_BRIGHTNESS_SCHEMA,
}

DEVICE_COMMAND_TOOL: Dict[str, Any] = {
    "name": TOOL_NAME,
    "description": "Execute a supported device command through the MCP device interface.",
    "inputSchema": {
        "oneOf": [{"$ref": f"#/$defs/{name}"} for name in DEVICE_COMMAND_SCHEMAS],
        "$defs": DEVICE_COMMAND_SCHEMAS,
    },
}

POST_SOCIAL_UPDATE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "platform": {"type": "string", "enum": ["twitter", "bluesky"]},
        "message": {"type": "string", "minLength": 1, "maxLength": 280},
    },
    "required": ["platform", "message"],
    "additionalProperties": False,
}

POST_SOCIAL_UPDATE_TOOL: Dict[str, Any] = {
    "name": POST_SOCIAL_UPDATE_TOOL_NAME,
    "description": "Post a public social media update to Twitter or Bluesky.",
    "inputSchema": POST_SOCIAL_UPDATE_SCHEMA,
}


class MCPServer:
    def __init__(self) -> None:
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Any] | Any]] = {}

    def register_tool(
        self,
        tool_definition: Dict[str, Any],
        handler: Callable[[Dict[str, Any]], Awaitable[Any] | Any],
    ) -> None:
        name = str(tool_definition["name"])
        self._tools[name] = tool_definition
        self._handlers[name] = handler

    async def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        method = request.get("method")
        request_id = request.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "wardenai-mcp-server"}},
            }
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": list(self._tools.values())}}
        if method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            if tool_name not in self._tools:
                raise ValueError(f"Unknown MCP tool: {tool_name}")
            tool_definition = self._tools[tool_name]
            _validate_schema(arguments, tool_definition.get("inputSchema", {}))
            handler = self._handlers[tool_name]
            result = await _maybe_await(handler(arguments))
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "json", "json": result}]}}

        raise ValueError(f"Unsupported MCP method: {method}")


class MCPClient:
    def __init__(self, server: MCPServer) -> None:
        self.server = server
        self._request_counter = itertools.count(1)
        self._initialized = False

    async def initialize(self) -> Dict[str, Any]:
        response = await self.server.handle_request(
            {"jsonrpc": "2.0", "id": next(self._request_counter), "method": "initialize"}
        )
        self._initialized = True
        return response["result"]

    async def list_tools(self) -> List[Dict[str, Any]]:
        await self._ensure_initialized()
        response = await self.server.handle_request(
            {"jsonrpc": "2.0", "id": next(self._request_counter), "method": "tools/list"}
        )
        return list(response["result"]["tools"])

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        await self._ensure_initialized()
        response = await self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": next(self._request_counter),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        return response["result"]

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()


class DeviceCommandMCPServer(MCPServer):
    def __init__(self, api: Any) -> None:
        super().__init__()
        self.api = api
        self.register_tool(DEVICE_COMMAND_TOOL, self._execute_device_command)
        self.register_tool(POST_SOCIAL_UPDATE_TOOL, self._post_social_update)

    async def _execute_device_command(self, arguments: Dict[str, Any]) -> Any:
        executor = _resolve_device_executor(self.api)
        return await _maybe_await(executor(arguments))

    async def _post_social_update(self, arguments: Dict[str, Any]) -> Any:
        executor = _resolve_social_executor(self.api)
        if executor is None:
            logger.warning("post_social_update called but no executor configured: %s", arguments)
            return {"ok": False, "error": "Social update executor not configured", "arguments": arguments}
        return await _maybe_await(executor(arguments))


def _resolve_device_executor(api: Any) -> Callable[[Dict[str, Any]], Awaitable[Any] | Any]:
    if hasattr(api, TOOL_NAME):
        return getattr(api, TOOL_NAME)
    if hasattr(api, "tool_executor") and hasattr(api.tool_executor, TOOL_NAME):
        return getattr(api.tool_executor, TOOL_NAME)
    raise AttributeError(f"Device command executor '{TOOL_NAME}' is required")


def _resolve_social_executor(api: Any) -> Optional[Callable[[Dict[str, Any]], Awaitable[Any] | Any]]:
    if hasattr(api, POST_SOCIAL_UPDATE_TOOL_NAME):
        return getattr(api, POST_SOCIAL_UPDATE_TOOL_NAME)
    if hasattr(api, "tool_executor") and hasattr(api.tool_executor, POST_SOCIAL_UPDATE_TOOL_NAME):
        return getattr(api.tool_executor, POST_SOCIAL_UPDATE_TOOL_NAME)
    return None


def _validate_schema(value: Any, schema: Dict[str, Any], root_schema: Optional[Dict[str, Any]] = None) -> None:
    root = root_schema or schema

    if "$ref" in schema:
        ref = schema["$ref"]
        prefix = "#/$defs/"
        if not ref.startswith(prefix):
            raise ValueError(f"Unsupported schema reference: {ref}")
        definition_name = ref[len(prefix) :]
        _validate_schema(value, root["$defs"][definition_name], root)
        return

    if "oneOf" in schema:
        for option in schema["oneOf"]:
            try:
                _validate_schema(value, option, root)
                return
            except ValueError:
                continue
        raise ValueError("Arguments did not match any registered MCP schema")

    if schema.get("type") == "object":
        if not isinstance(value, dict):
            raise ValueError("Tool arguments must be an object")

        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for field in required:
            if field not in value:
                raise ValueError(f"Missing required field: {field}")

        if schema.get("additionalProperties") is False:
            extra_fields = set(value) - set(properties)
            if extra_fields:
                raise ValueError(f"Unexpected fields: {sorted(extra_fields)}")

        for field, field_schema in properties.items():
            if field in value:
                _validate_schema(value[field], field_schema, root)
        return

    expected_type = schema.get("type")
    if expected_type == "string" and not isinstance(value, str):
        raise ValueError("Expected string value")
    if expected_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError("Expected integer value")

    if "const" in schema and value != schema["const"]:
        raise ValueError(f"Expected constant value: {schema['const']}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"Expected one of: {schema['enum']}")
    if "minLength" in schema and len(value) < schema["minLength"]:
        raise ValueError(f"String too short: {value}")
    if "minimum" in schema and value < schema["minimum"]:
        raise ValueError(f"Value below minimum: {value}")
    if "maximum" in schema and value > schema["maximum"]:
        raise ValueError(f"Value above maximum: {value}")


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
