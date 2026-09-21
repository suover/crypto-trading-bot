FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"

COPY pyproject.toml uv.lock .python-version ./

RUN uv sync --locked --no-dev --no-install-project

COPY . .

CMD ["python", "main.py"]
