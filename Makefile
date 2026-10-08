.PHONY: setup dev test eval
setup:
	node scripts/dev.mjs --setup
dev:
	node scripts/dev.mjs
test:
	cd backend && uv run pytest && uv run ruff check app tests
	cd frontend && npm run lint && npm run build
eval:
	cd backend && uv run python -m app.eval.run
