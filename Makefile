.PHONY: setup dev web api migrate seed-demo train rescore-demo sync seed-sandbox record-payment check smoke browser-smoke clean

AS_OF ?= today

setup:
	[ -f .env ] || cp .env.example .env
	uv sync --dev
	bun install --frozen-lockfile
	docker compose up -d db
	until docker compose exec -T db pg_isready -U latepayer -d latepayer >/dev/null 2>&1; do sleep 1; done
	uv run alembic upgrade head
	$(MAKE) seed-demo AS_OF=$(AS_OF)

dev:
	@trap 'kill 0' EXIT INT TERM; \
	  (PYTHONPATH=.:services/api uv run uvicorn app.main:app --app-dir services/api --host 127.0.0.1 --port 8000 --reload) & \
	  (bun run dev) & wait

web:
	bun run dev

api:
	PYTHONPATH=.:services/api uv run uvicorn app.main:app --app-dir services/api --host 127.0.0.1 --port 8000 --reload

migrate:
	uv run alembic upgrade head

seed-demo:
	AS_OF="$(AS_OF)" PYTHONPATH=.:services/api uv run python scripts/seed_demo.py
	AS_OF="$(AS_OF)" PYTHONPATH=. uv run python scripts/train_model.py
	AS_OF="$(AS_OF)" PYTHONPATH=.:services/api uv run python scripts/rescore_demo.py

train:
	AS_OF="$(AS_OF)" PYTHONPATH=. uv run python scripts/train_model.py

rescore-demo:
	PYTHONPATH=.:services/api uv run python scripts/rescore_demo.py

sync:
	PYTHONPATH=.:services/api uv run python scripts/sync_paypal.py

seed-sandbox:
	PYTHONPATH=.:services/api uv run python scripts/seed_sandbox.py

record-payment:
	PYTHONPATH=.:services/api uv run python scripts/record_payment.py --invoice-id "$(INVOICE_ID)" --amount "$(AMOUNT)" --currency "$(CURRENCY)" --method "$(if $(METHOD),$(METHOD),BANK_TRANSFER)" --note "$(NOTE)"

check:
	uv run ruff check services/api ml scripts tests
	uv run pytest
	bun run lint
	bun run typecheck
	bun run build

smoke:
	PYTHONPATH=.:services/api uv run python scripts/smoke.py

browser-smoke:
	bun run test:browser

clean:
	docker compose down -v
	rm -rf .venv node_modules .next late_payer.db
