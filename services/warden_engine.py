from typing import Any, Dict


class WardenEngine:
    def decide(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        action = "allow"
        if payload.get("risk") == "high":
            action = "deny"

        return {
            "action": action,
            "reason": "High risk request blocked" if action == "deny" else "Request allowed",
        }

    def maybe_call_tool(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("tool") == "echo":
            return {"tool_result": payload.get("input")}
        return {"tool_result": None}
