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
4. If using Cloudflare Tunnel private hostnames, set `CLOUDFLARED_TUNNEL_TOKEN`.

### Required Integration Variables (Camera-Site)

- `BACKEND_WS_URL`
  - Base URL of Camera-Site backend.
  - Examples:
    - `https://site.example.com`
    - `http://backend:8000`
    - `wss://site.example.com/ws/ai-warden`
  - The app normalizes this into both:
    - websocket endpoint: `/ws/ai-warden`
    - HTTP base for startup probes: `/api/handler/ai-warden/runtime` and `/api/handler/ai-warden/report`
- `AI_WARDEN_API_KEY`
  - Bearer token used for websocket and API auth to Camera-Site.
  - Must match the key configured in Camera-Site AI Warden settings.

### Startup Contract Checks

On startup, wardenai validates Camera-Site integration before entering the long-running websocket loop.

Validated contracts:
- websocket auth/connectivity to `/ws/ai-warden`
- `GET /api/handler/ai-warden/runtime` returns `200`
- `POST /api/handler/ai-warden/report` probe with invalid payload returns `400` or `422`

If any check fails, startup exits fast with an explicit error.

### Health and Readiness Endpoints

- `GET /health`
  - Liveness endpoint with embedded readiness metadata.
  - Returns JSON with `status`, `startup_check_ok`, and `startup_check`.
- `GET /startup-check`
  - Readiness endpoint for orchestrators.
  - Returns `200` when startup contract checks pass, otherwise `503`.

Kubernetes guidance:
- Liveness probe -> `/health`
- Readiness probe -> `/startup-check`

### Reverse Proxy / Path Prefix Notes

- If Camera-Site is served behind a path prefix (for example `/camera-site`), include that prefix in `BACKEND_WS_URL`.
- Examples:
  - `https://example.com/camera-site`
  - `wss://example.com/camera-site/ws/ai-warden`
- Ensure proxy rules forward websocket upgrade requests for `/ws/ai-warden` (or prefixed equivalent).

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

- Start stack with Cloudflare Tunnel connector (private hostname):

  ```bash
  docker compose --profile tunnel up -d
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
- `cloudflared` (profile: `tunnel`)
  - image: `cloudflare/cloudflared:latest`
  - runs tunnel connector using `CLOUDFLARED_TUNNEL_TOKEN`
  - expose `warden-backend:8080` through a Cloudflare Tunnel private hostname

## Cloudflare Tunnel (Private Hostname)

1. Create a tunnel in Cloudflare Zero Trust and copy the connector token.
2. Set `CLOUDFLARED_TUNNEL_TOKEN` in `.env`.
3. In the tunnel configuration, add a **private hostname** that routes to `http://warden-backend:8080`.
4. Start with:
   ```bash
   docker compose --profile tunnel up -d
   ```

This keeps `warden-backend` and `ollama-server` off direct host port exposure.

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
