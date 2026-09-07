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
# The substrate's models, baked in so the worker never downloads during a
# request on staging. Before the source copy on purpose: this layer depends on
# the lock file alone, so editing Python does not re-fetch a gigabyte of
# weights. scripts/fetch_models.py imports nothing from the package for the
# same reason, and tests/test_substrate_models_fetch.py holds the two lists
# together.
ENV TEMNIA_MODELS_DIR=/opt/temnia-models
COPY scripts/fetch_models.py ./scripts/
RUN /app/.venv/bin/python scripts/fetch_models.py
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
COPY --from=builder --chown=temnia:temnia /opt/temnia-models /opt/temnia-models
# HF_HUB_OFFLINE turns a missing or renamed model into a loud failure at load
# instead of a silent gigabyte pulled from the internet inside an activity.
ENV PATH=/app/.venv/bin:${PATH} \
    PYTHONUNBUFFERED=1 \
    PIPELINE_WORK_DIR=/var/lib/temnia/work \
    TEMNIA_MODELS_DIR=/opt/temnia-models \
    HF_HOME=/opt/temnia-models \
    HF_HUB_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false
USER temnia
VOLUME ["/var/lib/temnia/work"]
CMD ["python", "-m", "temnia_pipeline.worker"]
