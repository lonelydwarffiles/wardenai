#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: Docker is not installed or not available in PATH."
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Error: Docker Compose is not installed (docker compose plugin or docker-compose)."
  exit 1
fi

echo "Starting ollama-server..."
"${COMPOSE_CMD[@]}" up -d ollama-server

echo "Waiting for Ollama API health..."
for attempt in $(seq 1 60); do
  if "${COMPOSE_CMD[@]}" exec -T ollama-server ollama list >/dev/null 2>&1; then
    echo "Ollama API is healthy."
    break
  fi

  if [[ "$attempt" -eq 60 ]]; then
    echo "Error: Ollama API did not become healthy in time."
    exit 1
  fi

  sleep 2
done

MODELS=(
  "dolphin-llama3:8b"
  "bartowski/Llama-3.2-3B-Instruct-uncensored-GGUF"
  "dolphin-phi:2.7b"
)

for model in "${MODELS[@]}"; do
  echo "Pulling model: $model"
  "${COMPOSE_CMD[@]}" exec -T ollama-server ollama pull "$model"
done

echo "Model pulls complete. Starting warden-backend..."
"${COMPOSE_CMD[@]}" up -d warden-backend

echo "Setup complete."
