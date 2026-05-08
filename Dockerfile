# ── Stage 1 : build UI ───────────────────────────────────────────────────────
FROM node:20-alpine AS ui-builder
WORKDIR /app/ui
COPY ui/package.json ui/package-lock.json* ./
RUN npm ci --ignore-scripts
COPY ui/ ./
RUN npm run build

# ── Stage 2 : Python gateway ──────────────────────────────────────────────────
FROM python:3.12-slim AS gateway
WORKDIR /app

# System deps (asyncssh needs cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libffi-dev openssh-client curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Gateway source
COPY gateway/ ./gateway/
COPY mcp_server/ ./mcp_server/

# UI static files
COPY --from=ui-builder /app/ui/dist ./static/

# Non-root user
RUN useradd -m -u 1001 gateway && chown -R gateway:gateway /app
USER gateway

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

CMD uvicorn gateway.main:app --host 0.0.0.0 --port 8080 --workers ${WEB_CONCURRENCY:-4} --log-level info
