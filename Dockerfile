FROM python:3.13-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY app app
COPY data data
COPY migrations migrations
COPY scripts scripts
COPY ui ui

RUN useradd --create-home coach
USER coach

EXPOSE 8000
# One worker: the /ask rate limiter keeps its counters in process memory.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]


# Dependencies are installed before the sources are copied so that editing the
# dashboard does not invalidate the npm layer.
FROM node:22-alpine AS webbuild
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


FROM caddy:2-alpine AS caddy
COPY --from=webbuild /web/dist /srv
