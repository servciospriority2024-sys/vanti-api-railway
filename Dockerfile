FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb wget gnupg ca-certificates fonts-liberation \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && (apt-get install -y /tmp/chrome.deb || apt-get install -f -y) \
    && rm -rf /tmp/chrome.deb /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py consultar.py ./

EXPOSE 8088

CMD ["sh", "-c", "rm -f /tmp/.X99-lock && Xvfb :99 -screen 0 1280x800x24 -nolisten tcp & sleep 2 && uvicorn app:app --host 0.0.0.0 --port ${PORT:-8088} --workers 1"]
