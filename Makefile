SHELL := /bin/bash

.PHONY: install install-backend install-frontend dev dev-backend dev-frontend lint typecheck test build check

install: install-backend install-frontend

install-backend:
	cd backend && python3.13 -m venv .venv && source .venv/bin/activate && python -m pip install --upgrade pip && pip install -r requirements-dev.txt

install-frontend:
	cd frontend && npm install

dev:
	@bash -c 'set -euo pipefail; \
	( cd backend && source .venv/bin/activate && uvicorn app.main:app --reload ) & \
	BACKEND_PID=$$!; \
	trap "kill $$BACKEND_PID" EXIT INT TERM; \
	cd frontend && npm run dev'

dev-backend:
	cd backend && source .venv/bin/activate && uvicorn app.main:app --reload

dev-frontend:
	cd frontend && npm run dev

lint:
	cd backend && source .venv/bin/activate && ruff check . && ruff format --check .
	cd frontend && npm run lint

typecheck:
	cd backend && source .venv/bin/activate && mypy .
	cd frontend && npm run typecheck

test:
	cd backend && source .venv/bin/activate && pytest --cov=app --cov-report=term-missing

build:
	cd frontend && npm run build

check: lint typecheck test build
