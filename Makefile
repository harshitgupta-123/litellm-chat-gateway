# Detect docker compose v2 (plugin) or the legacy docker-compose binary.
COMPOSE := $(shell if docker compose version >/dev/null 2>&1; then echo "docker compose"; else echo "docker-compose"; fi)

.PHONY: up down logs build restart ps test test-docker clean help

## up: build and start backend (:8000) and frontend (:3000)
up: .env
	$(COMPOSE) up --build -d
	@echo ""
	@echo "  backend   -> http://localhost:8000/health"
	@echo "  api docs  -> http://localhost:8000/docs"
	@echo "  frontend  -> http://localhost:3000"
	@echo ""

## down: stop and remove the containers (the data volume is kept)
down:
	$(COMPOSE) down

## logs: follow the logs of both services
logs:
	$(COMPOSE) logs -f

## build: rebuild the images without starting them
build:
	$(COMPOSE) build

## restart: restart both services
restart: down up

## ps: show container status
ps:
	$(COMPOSE) ps

## test: run the backend test suite on this machine
test:
	cd backend && python3 -m pip install -q -r requirements-dev.txt && python3 -m pytest

## test-docker: run the backend test suite in a throwaway python container
test-docker:
	docker run --rm -v "$(PWD)/backend:/app" -w /app \
		-e DATABASE_URL=sqlite:////tmp/test.db -e CACHE_TYPE=memory \
		-e LLM_API_KEYS=test-key-one,test-key-two \
		python:3.12-slim sh -c "pip install -q -r requirements-dev.txt && pytest"

## clean: stop everything and delete the SQLite volume
clean:
	$(COMPOSE) down -v

## help: list the targets
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## //'

# create .env from the template on first run so `make up` always works
.env:
	cp .env.example .env
	@echo ">> created .env from .env.example - put your real API keys in it"
