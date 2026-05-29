import unittest

from services.agent_loop import AgentLoop, evaluate_state
from services.mcp_server import MCPClient, DeviceCommandMCPServer


class _AsyncStream:
    def __init__(self, items):
        self.items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


class _FakeAPI:
    def __init__(self):
        self.executed = []
        self.websocket_client = _AsyncStream([{"risk": "medium", "device_id": "screen-1"}])

    async def execute_device_command(self, arguments):
        self.executed.append(arguments)
        return {"ok": True, "arguments": arguments}


class _StructuredToolModel:
    def __init__(self):
        self.captured_tools = None

    async def generate(self, messages, tools):
        self.captured_tools = tools
        return {
            "tool_calls": [
                {
                    "name": "execute_device_command",
                    "arguments": {"command": "SET_BRIGHTNESS", "level": 75},
                }
            ]
        }


class _TextOnlyModel:
    async def generate(self, messages, tools):
        return '{"tool":"execute_device_command","action":"LOCK_DEVICE"}'


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_evaluate_state_uses_mcp_tool_discovery(self) -> None:
        api = _FakeAPI()
        model = _StructuredToolModel()
        client = MCPClient(DeviceCommandMCPServer(api))

        action_request = await evaluate_state({"risk": "medium"}, model, client)

        self.assertEqual(
            action_request,
            {"name": "execute_device_command", "arguments": {"command": "SET_BRIGHTNESS", "level": 75}},
        )
        self.assertIsNotNone(model.captured_tools)
        self.assertEqual(model.captured_tools[0]["name"], "execute_device_command")
        self.assertEqual(
            sorted(model.captured_tools[0]["inputSchema"]["$defs"]),
            ["LOCK_DEVICE", "PAVLOK_COMMAND", "SET_BRIGHTNESS", "TASK_ASSIGNED"],
        )

    async def test_agent_loop_executes_device_command_through_mcp_client(self) -> None:
        api = _FakeAPI()
        model = _StructuredToolModel()
        loop = AgentLoop(api=api, model=model)

        await loop.run()

        self.assertEqual(api.executed, [{"command": "SET_BRIGHTNESS", "level": 75}])

    async def test_text_output_is_ignored_when_mcp_tool_call_is_required(self) -> None:
        api = _FakeAPI()
        client = MCPClient(DeviceCommandMCPServer(api))

        action_request = await evaluate_state({"risk": "medium"}, _TextOnlyModel(), client)

        self.assertIsNone(action_request)


if __name__ == "__main__":
    unittest.main()
