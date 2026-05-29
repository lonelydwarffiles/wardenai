import unittest

from services.orchestrator import (
    HANDLER_MODEL_NAME,
    HANDLER_SYSTEM_PROMPT,
    SENSOR_MODEL_NAME,
    SENSOR_SYSTEM_PROMPT,
    TERMINATE_SIGNAL,
    TONE_MODEL_NAME,
    TONE_SYSTEM_PROMPT,
    WardenOrchestrator,
)


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
        self.last_kwargs = None

    async def generate(self, messages, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return {"reply": "handler-response"}


class _FakeMemoryManager:
    def get_highest_impact_patterns(self, limit=3):
        self.last_limit = limit
        return [
            {"correction_pattern": "Pattern Alpha", "compliance_improvement_delta": 1.8, "updated_at": "now"},
            {"correction_pattern": "Pattern Beta", "compliance_improvement_delta": 1.3, "updated_at": "now"},
            {"correction_pattern": "Pattern Gamma", "compliance_improvement_delta": 0.9, "updated_at": "now"},
            {"correction_pattern": "Pattern Delta", "compliance_improvement_delta": 0.5, "updated_at": "now"},
        ]


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

    async def test_handler_injects_weekly_top_phrases_into_system_prompt(self):
        api = _FakeAPI()
        tone_model = _CaptureToneModel({"compliant": True, "compliance_score": 8})
        handler_model = _CaptureHandlerModel()
        memory_manager = _FakeMemoryManager()
        orchestrator = WardenOrchestrator(
            api=api,
            handler_model=handler_model,
            tone_model=tone_model,
            memory_manager=memory_manager,
        )

        await orchestrator.route_payload({"type": "UserMessage", "text": "Acknowledged."})

        self.assertIn("system_prompt_override", handler_model.last_kwargs)
        prompt = handler_model.last_kwargs["system_prompt_override"]
        self.assertIn("Reinforcement Learning Directives", prompt)
        self.assertIn("Pattern Alpha", prompt)
        self.assertIn("Pattern Beta", prompt)
        self.assertIn("Pattern Gamma", prompt)
        self.assertNotIn("Pattern Delta", prompt)

    def test_orchestrator_uses_configured_model_names(self):
        self.assertEqual(HANDLER_MODEL_NAME, "Qwen3-8B")
        self.assertEqual(TONE_MODEL_NAME, "Llama-3.2-1B")
        self.assertEqual(SENSOR_MODEL_NAME, "Qwen3-1.7B")

    def test_system_prompts_are_loaded_from_prompt_files(self):
        self.assertIn("no name-calling", HANDLER_SYSTEM_PROMPT)
        self.assertIn("strict JSON only", TONE_SYSTEM_PROMPT)
        self.assertIn("hard operational boundaries", SENSOR_SYSTEM_PROMPT.lower())


if __name__ == "__main__":
    unittest.main()
