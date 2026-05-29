# AI Warden

Modular, containerized backend for AI-assisted Warden decisions, Ollama model orchestration, and backend WebSocket integration.

## Repository Structure

- `/api`: WebSocket client logic for connecting to the backend
- `/services`: Warden decision engine, tool-calling logic, and memory retrieval (`memory_manager.py`)
- `/core`: Environment loading and shared constants

## Prerequisites

- Docker
- Docker Compose (`docker compose` plugin or `docker-compose`)

## Configuration

1. Clone the repo.
2. Copy `.env.example` to `.env`.
3. Update environment variables as needed.

## One-Click Setup

Use the setup target to initialize the full backend ecosystem:

```bash
make setup
```

This runs `./init_setup.sh`, which:

- verifies Docker and Docker Compose availability
- starts `ollama-server`
- waits for Ollama API readiness
- pulls required models:
  - `dolphin-llama3:8b`
  - `bartowski/Llama-3.2-3B-Instruct-uncensored-GGUF`
  - `dolphin-phi:2.7b`
- starts `warden-backend`

## Runtime Commands

- Start stack in background:

  ```bash
  make up
  ```

- Stop containers (preserves volumes):

  ```bash
  make down
  ```

- Stream logs:

  ```bash
  make logs
  ```

## Docker Compose Services

- `ollama-server`
  - image: `ollama/ollama:latest`
  - CPU tuning:
    - `OLLAMA_NUM_PARALLEL=4`
    - `OLLAMA_MAX_LOADED_MODELS=3`
  - persistent model cache volume: `ollama_cache`
- `warden-backend`
  - built from local Dockerfile
  - connects to Ollama via `http://ollama-server:11434`
  - persistent SQLite volume: `sqlite_data` (`/app/data/sqlite`)
  - persistent Chroma volume: `chroma_data` (`/app/data/chroma`)

## Troubleshooting

- **`make setup` fails with Docker not found**
  - Ensure Docker is installed and running.
  - Verify with:
    ```bash
    docker --version
    ```

- **Docker Compose command not available**
  - Install either the `docker compose` plugin or `docker-compose`.
  - Verify with:
    ```bash
    docker compose version
    ```
    or
    ```bash
    docker-compose --version
    ```

- **Ollama health check timeout during setup**
  - Check Ollama container logs:
    ```bash
    docker compose logs ollama-server
    ```
  - Retry setup after confirming container startup:
    ```bash
    make setup
    ```

- **Model pulls are slow or fail intermittently**
  - Re-run setup to retry pulls:
    ```bash
    make setup
    ```
  - Confirm available disk space for model cache volume.

- **Backend cannot connect to Ollama**
  - Ensure both services are running:
    ```bash
    docker compose ps
    ```
  - Verify `warden-backend` uses `OLLAMA_HOST=http://ollama-server:11434` from `docker-compose.yml`.

- **Need a clean container restart**
  - Restart stack while preserving volumes:
    ```bash
    make down
    make up
    ```
