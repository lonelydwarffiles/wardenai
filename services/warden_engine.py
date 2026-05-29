from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.memory_manager import MemoryManager


class WardenEngine:
    def __init__(self, memory_manager: Optional[MemoryManager] = None, memory_db_path: str = "data/warden_memory.db") -> None:
        self.memory_manager = memory_manager or MemoryManager(db_path=memory_db_path)

    def decide(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        historical_records = self.memory_manager.get_historical_context(payload, limit=5)
        permanent_record_context = self.memory_manager.format_permanent_record_context(historical_records)
        is_repeat_offender = len(historical_records) > 0

        action = "allow"
        if payload.get("risk") == "high":
            action = "deny"
            self.memory_manager.log_infraction(
                timestamp=str(payload.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                context=payload,
                action_taken=action,
            )

        return {
            "action": action,
            "reason": "High risk request blocked" if action == "deny" else "Request allowed",
            "repeat_offender": is_repeat_offender,
            "permanent_record_context": permanent_record_context,
        }

    def maybe_call_tool(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("tool") == "echo":
            return {"tool_result": payload.get("input")}
        return {"tool_result": None}
