# AGENTS.md

## Project
Repository applications:
- `trading-engine/`: Python 3.14 trading/research engine using PostgreSQL, Upbit, OpenAI, and Telegram.
- `backend/`: future Spring Boot API backend (placeholder only).
- `frontend/`: future React/TypeScript frontend (placeholder only).

Docker Compose, operational shell scripts, documentation, and environment/secret files remain at the repository root.

The production system can execute real Upbit orders after Telegram approval.
Treat live trading and secrets as safety-critical.

## Working Rules
- Work only inside this repository.
- Do not create, switch, merge, or delete Git branches unless explicitly requested.
- Do not commit or push unless explicitly requested.
- Do not modify `.env`, secret files, credentials, or production server files.
- Never execute a real cryptocurrency order.
- Do not weaken existing LIVE trading, Telegram approval, idempotency, reconciliation, or order-limit safety checks.
- Preserve existing behavior unless the requested change explicitly requires otherwise.
- Prefer minimal, focused changes over unrelated refactoring.
- Do not add dependencies unless clearly necessary.

## Development
- Python 3.14
- Run Python development commands in `trading-engine/` using its `uv` environment and locked dependencies.
- Follow the existing project structure and coding style.
- Use `Decimal` for monetary and trading calculations where the project already does so.
- Add or update tests for behavioral changes.
- Tests must not depend on real external APIs or real orders.

## Required Validation
After code changes, run:

```bash
cd trading-engine
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
```

If an Alembic migration is added, also run from `trading-engine/`:

```bash
uv run alembic heads
```

## Before Finishing
Report:
- what was changed
- important design decisions
- tests and validation results
- remaining risks or follow-up work
- `git status --short`

Do not commit or push unless explicitly requested.
