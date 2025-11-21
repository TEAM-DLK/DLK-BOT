# Simple Dockerfile to run the bot on Docker-compatible hosts (Koyeb, Railway, VPS via container, etc.)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# system deps for some libraries (pillow, yt-dlp may require)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# default command: run main.py (the launcher)
CMD ["python", "DLK.py"]
