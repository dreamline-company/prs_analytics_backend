# temporary stage
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKDIR=/src

WORKDIR $WORKDIR

# install gcc and other build deps for compiling python packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
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

# add custom CA
COPY deploy/usia-ca.crt /usr/local/share/ca-certificates/usia-ca.crt
RUN update-ca-certificates

# install uv
COPY --from=ghcr.io/astral-sh/uv:0.5.2 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/venv \
    UV_NATIVE_TLS=true \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    PATH="/venv/bin:$PATH"

# install dependencies with uv
COPY uv.lock pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# final stage
FROM python:3.12-slim

ENV WORKDIR=/src

WORKDIR $WORKDIR

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY deploy/usia-ca.crt /usr/local/share/ca-certificates/usia-ca.crt
RUN update-ca-certificates

COPY --from=builder /venv /venv

ENV PATH="/venv/bin:$PATH" \
    PYTHONPATH="${PYTHONPATH}:$WORKDIR" \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt