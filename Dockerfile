FROM node:22-bookworm-slim AS web-build

WORKDIR /web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements.txt

COPY . /app
COPY --from=web-build /web/dist /app/web/dist
RUN mkdir -p /app/data \
    && chmod 0755 /app/deploy/entrypoint.sh \
    && chown -R app:app /app

USER app

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["python", "/app/deploy/healthcheck.py"]

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
