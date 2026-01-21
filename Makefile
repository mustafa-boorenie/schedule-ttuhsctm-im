.PHONY: help install dev run test lint format docker-build docker-up docker-down clean db-migrate

# Default target
help:
	@echo "Residency Rotation Calendar - Development Commands"
	@echo ""
	@echo "Development:"
	@echo "  make install     Install dependencies"
	@echo "  make dev         Run development server with hot reload"
	@echo "  make run         Run production server"
	@echo "  make test        Run tests"
	@echo "  make lint        Run linter"
	@echo "  make format      Format code"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build   Build Docker image"
	@echo "  make docker-up      Start all services"
	@echo "  make docker-down    Stop all services"
	@echo "  make docker-dev     Start development environment"
	@echo "  make docker-logs    View container logs"
	@echo ""
	@echo "Database:"
	@echo "  make db-migrate     Run database migrations"
	@echo "  make db-reset       Reset database (WARNING: deletes data)"
	@echo ""
	@echo "Utilities:"
	@echo "  make clean          Remove cache and build artifacts"

# Development
install:
	pip install -r requirements.txt

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2

test:
	pytest tests/ -v

lint:
	ruff check app/

format:
	ruff format app/

# Docker
docker-build:
	docker build -t rotation-calendar:latest .

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

docker-dev:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml up

docker-logs:
	docker-compose logs -f

docker-clean:
	docker-compose down -v --rmi local

# Database
db-migrate:
	alembic upgrade head

db-reset:
	@echo "WARNING: This will delete all data!"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	docker-compose exec db psql -U postgres -d rotation_calendar -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
	alembic upgrade head

# Utilities
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/ .eggs/ 2>/dev/null || true
