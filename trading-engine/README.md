# Trading Engine

Python 3.14 AI trading and strategy research engine for crypto-trading-bot.
The Python package remains `crypto_trading_bot`; CLI modules remain `scripts.*`.

Run Python development commands from this directory:

```bash
uv sync --dev --locked
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run alembic heads
```

Host configuration stays in the repository root `.env` and `.secrets/`.
When running from this directory, settings resolve those paths at the repository
root. Container runtime layout stays at `/app`, with environment values and
`/run/secrets/` files supplied by Compose.

Run Docker Compose and the three operational shell scripts from the repository
root. See the [repository README](../README.md), [operations guide](../docs/OPERATIONS.md),
the [architecture guide](../docs/ARCHITECTURE.md), and
[LIVE runbook](../docs/LIVE_RUNBOOK.md) before any operational command. Strategy
research and policy activation are documented separately in
[STRATEGY_RESEARCH.md](../docs/STRATEGY_RESEARCH.md) and
[POLICY_PROMOTION.md](../docs/POLICY_PROMOTION.md).

Windows engine launchers are in `scripts/*.ps1`; existing Task Scheduler entries
must be reviewed for the moved script and virtual-environment paths.
