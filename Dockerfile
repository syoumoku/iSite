FROM node:22-alpine AS ui-build

ENV NODE_OPTIONS=--max-old-space-size=1024

WORKDIR /app/ui/world_map
COPY ui/world_map/package.json ui/world_map/package-lock.json ./
RUN npm ci
COPY ui/world_map/ ./
RUN npm run build

FROM python:3.12-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    ISITE2_APP_MODE=public_view \
    ISITE2_ENABLE_OVERLAY_SYNC=0

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY schemas ./schemas
COPY db ./db
COPY --from=ui-build /app/ui/world_map/dist ./ui/world_map/dist

RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "isite2.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
