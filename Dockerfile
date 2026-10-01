FROM python:3.11-slim AS base

# libgomp: LightGBM; libgl/glib: OpenCV (pulled in by RapidOCR); dejavu: receipt rendering in tests
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[vision,postgres]"
COPY configs ./configs

ENV PAYGUARD_ARTIFACTS_DIR=/app/artifacts \
    PAYGUARD_DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --create-home payguard && mkdir -p /app/artifacts /app/data && chown -R payguard /app
USER payguard

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz').status != 200)"
CMD ["uvicorn", "payguard.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
