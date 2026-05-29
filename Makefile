.PHONY: setup up down logs

setup:
./init_setup.sh

up:
docker compose up -d

down:
docker compose down

logs:
docker compose logs -f
