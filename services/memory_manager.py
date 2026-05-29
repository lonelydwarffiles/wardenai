import hashlib
import json
import math
import os
import re
import sqlite3
from typing import Any, Dict, List


class MemoryManager:
    def __init__(self, db_path: str = "data/warden_memory.db", embedding_dimensions: int = 128) -> None:
        self.db_path = db_path
        self.embedding_dimensions = embedding_dimensions
        self._initialize_database()

    def _initialize_database(self) -> None:
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS infractions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    action_taken TEXT NOT NULL,
                    searchable_text TEXT NOT NULL,
                    embedding_json TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _to_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, sort_keys=True, default=str)

    def _embed_text(self, text: str) -> List[float]:
        vector = [0.0] * self.embedding_dimensions
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            index = int(digest, 16) % self.embedding_dimensions
            vector[index] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    def _cosine_similarity(self, first: List[float], second: List[float]) -> float:
        return sum(a * b for a, b in zip(first, second))

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def log_infraction(self, timestamp: str, context: Any, action_taken: str) -> None:
        context_text = self._to_text(context)
        searchable_text = f"{timestamp} {context_text} {action_taken}".strip()
        embedding = self._embed_text(searchable_text)

        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO infractions (timestamp, context_json, action_taken, searchable_text, embedding_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (timestamp, context_text, action_taken, searchable_text, json.dumps(embedding)),
            )
            connection.commit()

    def get_historical_context(self, user_input_or_telemetry: Any, limit: int = 5) -> List[Dict[str, Any]]:
        query_text = self._to_text(user_input_or_telemetry).strip()
        if not query_text or limit <= 0:
            return []

        query_embedding = self._embed_text(query_text)
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT timestamp, context_json, action_taken, searchable_text, embedding_json
                FROM infractions
                ORDER BY id DESC
                """
            ).fetchall()

        scored: List[Dict[str, Any]] = []
        query_tokens = set(self._tokenize(query_text))
        for timestamp, context_json, action_taken, searchable_text, embedding_json in rows:
            semantic_score = self._cosine_similarity(query_embedding, json.loads(embedding_json))
            row_tokens = set(self._tokenize(searchable_text))
            token_overlap = len(query_tokens.intersection(row_tokens)) / max(1, len(query_tokens))
            score = semantic_score + token_overlap
            try:
                parsed_context = json.loads(context_json)
            except json.JSONDecodeError:
                parsed_context = context_json
            scored.append(
                {
                    "timestamp": timestamp,
                    "context": parsed_context,
                    "action_taken": action_taken,
                    "similarity": score,
                }
            )

        scored.sort(key=lambda item: item["similarity"], reverse=True)
        return scored[:limit]

    def format_permanent_record_context(self, records: List[Dict[str, Any]]) -> str:
        if not records:
            return "Permanent Record Context:\n- No similar historical infractions found."

        lines = ["Permanent Record Context:"]
        for record in records:
            lines.append(
                "- "
                + f"{record['timestamp']} | action={record['action_taken']} | "
                + f"context={self._to_text(record['context'])}"
            )
        return "\n".join(lines)
