FROM python:3-alpine

LABEL maintainer="xnetcat (Jakub)"

# Install dependencies
RUN apk add --no-cache \
    ca-certificates \
    ffmpeg \
    openssl \
    aria2 \
    g++ \
    git \
    py3-cffi \
    libffi-dev \
    zlib-dev

# Install uv and update pip/wheel
RUN pip install --upgrade pip uv wheel spotipy

# Set workdir
WORKDIR /app

# Copy only dependency files first (cache until pyproject.toml/uv.lock change)
COPY pyproject.toml uv.lock ./

# Install dependencies only (cached when only app code changes)
RUN uv sync --no-install-project

# Copy app code; second uv sync only installs the project (fast)
COPY . .
RUN uv sync

# So that "spotdl" and "python" work when using docker exec (use venv from /app)
ENV PATH="/app/.venv/bin:$PATH"

# Create a volume for the output directory
VOLUME /music

# Change Workdir to download location
WORKDIR /music

# Entrypoint command
ENTRYPOINT ["uv", "run", "--project", "/app", "spotdl"]
