import tempfile
import unittest

from services.memory_manager import MemoryManager
from services.warden_engine import WardenEngine


class MemoryManagerTests(unittest.TestCase):
    def test_log_infraction_and_retrieve_similar_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemoryManager(db_path=f"{temp_dir}/memory.db")
            manager.log_infraction(
                timestamp="2026-05-29T12:00:00Z",
                context={"user_id": "u-1", "event": "policy violation on login"},
                action_taken="deny",
            )
            manager.log_infraction(
                timestamp="2026-05-29T12:05:00Z",
                context={"user_id": "u-2", "event": "harmless request"},
                action_taken="allow",
            )

            records = manager.get_historical_context({"event": "login violation"}, limit=1)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["action_taken"], "deny")
            self.assertIn("policy violation on login", str(records[0]["context"]))

    def test_engine_injects_permanent_record_context_and_logs_denials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemoryManager(db_path=f"{temp_dir}/memory.db")
            manager.log_infraction(
                timestamp="2026-05-29T11:59:00Z",
                context={"user_id": "repeat-user", "event": "high risk policy breach"},
                action_taken="deny",
            )

            engine = WardenEngine(memory_manager=manager)
            payload = {
                "timestamp": "2026-05-29T12:10:00Z",
                "user_id": "repeat-user",
                "risk": "high",
                "event": "high risk policy breach detected again",
            }
            decision = engine.decide(payload)

            self.assertEqual(decision["action"], "deny")
            self.assertTrue(decision["repeat_offender"])
            self.assertIn("Permanent Record Context:", decision["permanent_record_context"])

            refreshed = manager.get_historical_context(payload, limit=5)
            denial_count = sum(1 for record in refreshed if record["action_taken"] == "deny")
            self.assertGreaterEqual(denial_count, 2)

    def test_reaction_registry_tracks_highest_impact_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemoryManager(db_path=f"{temp_dir}/memory.db")
            manager.record_reaction_outcome("Pattern A", 0.2)
            manager.record_reaction_outcome("Pattern B", 1.2)
            manager.record_reaction_outcome("Pattern C", 0.7)
            manager.record_reaction_outcome("Pattern B", 1.6)

            top_patterns = manager.get_highest_impact_patterns(limit=3)

            self.assertEqual(top_patterns[0]["correction_pattern"], "Pattern B")
            self.assertEqual(top_patterns[0]["compliance_improvement_delta"], 1.6)
            self.assertEqual(
                [item["correction_pattern"] for item in top_patterns],
                ["Pattern B", "Pattern C", "Pattern A"],
            )


if __name__ == "__main__":
    unittest.main()
