# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Pinned to an exact patch so the image is reproducible, matching the uv that
# produced uv.lock. A floating minor tag would silently change the resolver.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

# Dependencies first, so a source change does not invalidate the install layer.
# --frozen installs exactly what uv.lock pins; the build fails rather than
# silently resolving a different version than CI tested.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

# Render injects PORT. Default to 8000 so the image runs identically in local
# docker without extra flags.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uv run --no-dev uvicorn offerdelta.api.main:app --host 0.0.0.0 --port ${PORT}"]
