# syntax=docker/dockerfile:1
# Build context is this directory: `docker build apps/pipeline`.
# The generated contracts module is committed, so the image needs no JS toolchain.
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
# Dependencies first: this layer survives source-only changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.13-slim AS runner
RUN useradd --system --create-home --uid 10001 temnia
WORKDIR /app
COPY --from=builder --chown=temnia:temnia /app /app
ENV PATH=/app/.venv/bin:${PATH} \
    PYTHONUNBUFFERED=1
USER temnia
# S1 adds the pinned ffmpeg static build here (docs/tech-stack.md §8).
CMD ["python", "-m", "temnia_pipeline.worker"]
