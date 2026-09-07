# Build the web bundle, then serve it from the API image.
FROM node:20-slim AS web
WORKDIR /build
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm install
COPY apps/web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY packages/ ./packages/
COPY services/ ./services/
RUN uv sync --frozen --no-dev

COPY fixtures/ ./fixtures/
COPY --from=web /build/dist ./apps/web/dist

# Run as a non root user.
RUN useradd --create-home --uid 10001 forklab && chown -R forklab:forklab /app
USER forklab

EXPOSE 8000
CMD ["uv", "run", "--no-dev", "uvicorn", "forklab_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
