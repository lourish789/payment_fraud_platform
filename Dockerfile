# ---- web console: static build; Node never reaches the runtime image -----------------------------------
FROM node:22-alpine AS console
WORKDIR /console
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- API (also serves the console from /app/frontend/dist, same origin) --------------------------------
FROM python:3.11-slim AS base

# libgomp: LightGBM; libgl/glib: OpenCV (pulled in by RapidOCR); dejavu: receipt rendering in tests
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[vision,postgres]"
COPY configs ./configs
COPY --from=console /console/dist ./frontend/dist
# Champion card model + vision thresholds (small, committed). A mounted ./artifacts volume replaces it.
COPY deploy/bundle ./artifacts

ENV PAYGUARD_ARTIFACTS_DIR=/app/artifacts \
    PAYGUARD_DATA_DIR=/app/data \
    PAYGUARD_FRONTEND_DIST=/app/frontend/dist \
    PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --create-home payguard && mkdir -p /app/artifacts /app/data && chown -R payguard /app
USER payguard

EXPOSE 8000
# PORT and WEB_CONCURRENCY let hosts such as Render pick the port and size the worker count to the instance.
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import os,urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8000')).status != 200)"
CMD ["sh", "-c", "exec uvicorn payguard.api.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
