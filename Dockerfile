# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- stage 1
# Build the SPA. Node is only needed here; it never reaches the final image.
FROM node:22-alpine AS web

WORKDIR /web
COPY package.json package-lock.json ./
COPY frontend/package.json ./frontend/package.json
RUN npm ci

# Include public assets (fonts and flags) as well as screens and build configuration.
COPY frontend ./frontend
RUN npm run build


# ---------------------------------------------------------------- stage 2
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# psycopg[binary] ships its own libpq, so no build toolchain is needed here.
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./

# The compiled front end, served by the same process as the API.
COPY --from=web /web/frontend/dist ./static

# Changes on every build, so a deploy that reused an old image is obvious.
RUN date -u +%Y-%m-%dT%H:%M:%SZ > /app/BUILD

# Run as a non-root user.
RUN mkdir -p /app/data/uploads \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

VOLUME ["/app/data/uploads"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import json,urllib.request,sys; r=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)); sys.exit(0 if r.get('status') == 'ok' else 1)"

# Migrations run on boot so a Dokploy redeploy always lands on the right schema.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
