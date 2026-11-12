# One container: FastAPI (imports gaik from PyPI) serving the statically exported Next.js front end.
# bun installs, Node builds: bun's own build crashes on Rahti build nodes.
FROM docker.io/oven/bun:1 AS webdeps
WORKDIR /web
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile

FROM docker.io/library/node:22-slim AS web
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=webdeps /web/node_modules ./node_modules
COPY web/ .
RUN node node_modules/next/dist/bin/next build

FROM docker.io/library/python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/server/.venv PATH=/app/server/.venv/bin:$PATH HOME=/app \
    DATA_DIR=/data
WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock server/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache \
 && chgrp -R 0 /app && chmod -R g+rwX /app
COPY server/app ./app
COPY data/ai-act /app/data/ai-act
COPY --from=web /web/out /app/web/out
RUN mkdir -p /data && chgrp -R 0 /app/server/app /app/data /app/web /data \
 && chmod -R g+rwX /app/server/app /app/data /app/web /data
USER 1001
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "10"]
