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
# ffmpeg 8.1.2, the exact release, as a static binary from an image pinned by
# digest (docs/tech-stack.md §8). The same minor PyAV's wheels bundle, so probe
# and transcode agree on durations. Bumping is this one line plus the pin check
# in tests/test_ffmpeg_pin.py.
COPY --from=mwader/static-ffmpeg:8.1.2@sha256:33f770f812cbfc3de96c547157fc9faf8bd95a36481753439ffa761045167585 \
    /ffmpeg /ffprobe /usr/local/bin/
RUN useradd --system --create-home --uid 10001 temnia \
  && mkdir -p /var/lib/temnia/work && chown temnia:temnia /var/lib/temnia/work
WORKDIR /app
COPY --from=builder --chown=temnia:temnia /app /app
ENV PATH=/app/.venv/bin:${PATH} \
    PYTHONUNBUFFERED=1 \
    PIPELINE_WORK_DIR=/var/lib/temnia/work
USER temnia
VOLUME ["/var/lib/temnia/work"]
CMD ["python", "-m", "temnia_pipeline.worker"]
