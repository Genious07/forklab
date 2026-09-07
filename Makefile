.PHONY: install demo verify test lint web dev worker clean

install:
	uv sync
	cd apps/web && npm install

demo:
	uv run forklab demo

verify:
	uv run forklab verify

test:
	uv run pytest tests/ -q

lint:
	uv run ruff check .
	uv run ruff format --check .
	cd apps/web && npm run typecheck

web:
	cd apps/web && npm run build

dev: web
	uv run uvicorn forklab_api.app:app --reload --port 8000

worker:
	uv run forklab-worker

clean:
	rm -f forklab.db
	rm -rf apps/web/dist .pytest_cache .ruff_cache
