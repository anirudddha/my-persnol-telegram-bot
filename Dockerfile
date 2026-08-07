FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # LiteLLM otherwise downloads its pricing table on every cold start, which
    # is a network call between the container starting and it serving.
    LITELLM_LOCAL_MODEL_COST_MAP=True

WORKDIR /app

# Copied first so dependency layers survive a code-only change.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY jarvis ./jarvis

RUN useradd --create-home --uid 1000 jarvis && chown -R jarvis:jarvis /app
USER jarvis

# Cloud Run injects PORT; the default keeps `docker run` working unchanged.
ENV PORT=8080
EXPOSE 8080

# Shell form so ${PORT} expands, exec so uvicorn gets SIGTERM directly and
# Cloud Run can shut the instance down cleanly.
CMD exec uvicorn jarvis.web:app --host 0.0.0.0 --port ${PORT}
