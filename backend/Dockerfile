# ForgeFlow control-plane (dev image). Source is bind-mounted by docker-compose for hot reload.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# uv for fast, reproducible dependency management.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first for better layer caching (lockfile optional on first run).
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-install-project --extra openai

COPY . .
RUN uv sync --extra openai

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
