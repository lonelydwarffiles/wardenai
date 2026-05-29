import unittest

from services.orchestrator import TERMINATE_SIGNAL, WardenOrchestrator


class _FakeAPI:
    def __init__(self):
        self.commands = []

    async def execute_device_command(self, arguments):
        self.commands.append(arguments)
        return {"ok": True, "arguments": arguments}


class _CaptureToneModel:
    def __init__(self, output):
        self.output = output
        self.captured_tools = None

    async def generate(self, messages, tools):
        self.captured_tools = tools
        return self.output


class _CaptureSensorModel:
    def __init__(self, output):
        self.output = output
        self.captured_tools = None

    async def generate(self, messages, tools):
        self.captured_tools = tools
        return self.output


class _CaptureHandlerModel:
    def __init__(self):
        self.calls = 0

    async def generate(self, messages):
        self.calls += 1
        return {"reply": "handler-response"}


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_message_routes_tone_then_handler(self):
        api = _FakeAPI()
        tone_model = _CaptureToneModel({"compliant": True, "compliance_score": 0.94})
        handler_model = _CaptureHandlerModel()
        orchestrator = WardenOrchestrator(api=api, handler_model=handler_model, tone_model=tone_model)

        result = await orchestrator.route_payload({"type": "UserMessage", "text": "I completed the task."})

        self.assertEqual(result["agent"], "Handler_Agent")
        self.assertEqual(result["reply"], "handler-response")
        self.assertEqual(result["tone"]["agent"], "Tone_Specialist")
        self.assertFalse(result["tone"]["infraction"])
        self.assertEqual(handler_model.calls, 1)
        self.assertEqual(api.commands, [])
        self.assertEqual(tone_model.captured_tools[0]["name"], "execute_device_command")

    async def test_tone_infraction_executes_command_and_terminates_chain(self):
        api = _FakeAPI()
        tone_model = _CaptureToneModel(
            {"tool_calls": [{"name": "execute_device_command", "arguments": {"command": "LOCK_DEVICE"}}]}
        )
        handler_model = _CaptureHandlerModel()
        orchestrator = WardenOrchestrator(api=api, handler_model=handler_model, tone_model=tone_model)

        result = await orchestrator.route_payload({"type": "UserMessage", "text": "I refuse to comply."})

        self.assertEqual(result["signal"], TERMINATE_SIGNAL)
        self.assertEqual(result["agent"], "Tone_Specialist")
        self.assertTrue(result["infraction"])
        self.assertEqual(api.commands, [{"command": "LOCK_DEVICE"}])
        self.assertEqual(handler_model.calls, 0)

    async def test_telemetry_routes_only_to_sensor_specialist(self):
        api = _FakeAPI()
        tone_model = _CaptureToneModel({"compliant": True})
        handler_model = _CaptureHandlerModel()
        sensor_model = _CaptureSensorModel(
            {"tool_calls": [{"name": "execute_device_command", "arguments": {"command": "SET_BRIGHTNESS", "level": 15}}]}
        )
        orchestrator = WardenOrchestrator(
            api=api,
            handler_model=handler_model,
            tone_model=tone_model,
            sensor_model=sensor_model,
        )

        result = await orchestrator.route_payload({"type": "TPE_TELEMETRY", "telemetry": {"battery": 4}})

        self.assertEqual(result["signal"], TERMINATE_SIGNAL)
        self.assertEqual(result["agent"], "Sensor_Specialist")
        self.assertEqual(api.commands, [{"command": "SET_BRIGHTNESS", "level": 15}])
        self.assertEqual(handler_model.calls, 0)
        self.assertIsNone(tone_model.captured_tools)
        self.assertEqual(sensor_model.captured_tools[0]["name"], "execute_device_command")


if __name__ == "__main__":
    unittest.main()
