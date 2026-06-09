# temporary stage
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKDIR=/src

WORKDIR $WORKDIR

# install gcc and other build deps for compiling python packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    pkg-config \
    libavformat-dev \
    libavcodec-dev \
    libavdevice-dev \
    libavutil-dev \
    libswscale-dev \
    libswresample-dev \
    libavfilter-dev && \
    rm -rf /var/lib/apt/lists/*


# install uv
COPY --from=ghcr.io/astral-sh/uv:0.5.2 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/venv \
    PATH="/venv/bin:$PATH"

# install dependencies with uv

COPY uv.lock pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# final stage
FROM python:3.12-slim

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH" \
    WORKDIR=/src

ENV PYTHONPATH="${PYTHONPATH}:$WORKDIR"

WORKDIR $WORKDIR
