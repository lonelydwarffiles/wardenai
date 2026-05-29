# AI Warden

Modular, containerized microservice for AI-assisted Warden decisions and backend WebSocket integration.

## Repository Structure

- `/api`: WebSocket client logic for connecting to the backend
- `/services`: Warden decision engine, tool-calling logic, and memory retrieval (`memory_manager.py`)
- `/core`: Environment loading and shared constants

## Getting Started

1. Clone the repo.
2. Update `.env` (copy from `.env.example`).
3. Run `docker-compose up --build`.
