# Railway build for Goldilocks: one service that runs tenant/server.py and serves the built web app.
# Stage 1 builds web/dist. VITE_GOOGLE_CLIENT_ID is a build-time value (it is public, not a secret).
FROM node:22-slim AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ARG VITE_GOOGLE_CLIENT_ID
ENV VITE_GOOGLE_CLIENT_ID=$VITE_GOOGLE_CLIENT_ID
RUN npm run build

# Stage 2 is the runtime: Python, ffmpeg/ffprobe for vision, pdftoppm for the plan agent.
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg poppler-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY . .
COPY --from=web /app/web/dist ./web/dist
# PORT is set by Railway; tenant/server.py binds 0.0.0.0 when it is present.
CMD ["python", "-m", "tenant.server"]
