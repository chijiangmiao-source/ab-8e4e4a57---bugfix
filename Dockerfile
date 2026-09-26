FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app
COPY tests ./tests
COPY verify.py ./verify.py

# Container port; the host mapping is configurable in compose.
EXPOSE 8000

# Lightweight container-level healthcheck mirroring the readiness probe.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import json,urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3); sys.exit(0 if r.status==200 and json.load(r)['status']=='available' else 1)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
