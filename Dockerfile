FROM node:22-bookworm-slim AS console
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=2.4.1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}" \
    && useradd --create-home --uid 10001 gateway

WORKDIR /app/backend
COPY backend/pyproject.toml backend/poetry.lock ./
RUN poetry install --only main --no-root --no-ansi

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/gateway ./gateway
RUN mkdir -p /app/data && chown -R gateway:gateway /app

COPY --from=console /src/frontend/dist /app/frontend/dist
RUN chown -R gateway:gateway /app/frontend

USER gateway
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=25s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
