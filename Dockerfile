FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app

COPY pyproject.toml README.md ./
COPY core ./core
COPY userbot ./userbot
COPY admin_bot ./admin_bot
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir .

USER app
