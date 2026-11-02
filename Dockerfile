# Voice Coach image: the FastAPI app and the single HTML page. Fake providers by default, so it runs with no keys.
#   docker build -t voice-coach .
#   docker run --rm -p 127.0.0.1:8000:8000 voice-coach
# Optional local Whisper (large; the model downloads on first use and is lost with the container unless you mount a volume):
#   docker build --build-arg WITH_ASR=1 -t voice-coach-asr .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first: this layer is rebuilt only when a requirements file changes, not on every code edit.
COPY requirements.txt requirements-asr.txt ./
ARG WITH_ASR=0
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$WITH_ASR" = "1" ]; then pip install --no-cache-dir -r requirements-asr.txt; fi

COPY backend ./backend
COPY frontend ./frontend

# Do not run as root inside the container.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

# 0.0.0.0 is needed inside a container; publish the port on 127.0.0.1 only (see above), because /metrics has no authentication.
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
