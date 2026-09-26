FROM python:3.12-slim-bookworm
WORKDIR /app

# ffmpeg（B站 DASH 需要合流；純下載可不需要）
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && apt-get clean \
 && find /var/lib/apt/lists -mindepth 1 -delete

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && apt-get clean \
 && find /var/lib/apt/lists -mindepth 1 -delete

COPY app ./app
COPY static ./static

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
