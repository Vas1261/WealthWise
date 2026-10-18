# WealthWise: one image for local Docker Compose and Render (NFR-13).
# Stage 1 builds the React frontend; stage 2 runs FastAPI, which also serves that build, so the browser
# sees a single origin and the SameSite auth cookies work unchanged.

FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


FROM python:3.11-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FRONTEND_DIST=/app/frontend \
    APP_ENV=production
WORKDIR /app

# Runtime dependencies only (the dev/test tools at the end of requirements.txt are skipped).
COPY backend/requirements.txt ./requirements.txt
RUN sed '/^# dev \/ test/,$d' requirements.txt > runtime-requirements.txt \
    && pip install -r runtime-requirements.txt

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY --from=frontend /frontend/dist ./frontend

# Never run as root. The code stays owned by root but readable by everyone (source files may arrive with
# owner-only permissions, e.g. from a macOS checkout), so the app can read but never modify itself.
RUN chmod -R a+rX /app && useradd --create-home --uid 10001 wealthwise
USER wealthwise

EXPOSE 8000
# Bring the database to the newest migration, then serve. Render sets PORT; locally it is 8000.
# --proxy-headers: behind Render's HTTPS proxy, trust its X-Forwarded-* headers.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
