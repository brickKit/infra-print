# The print component alone: the locked environment built with uv on Debian slim (Typst ships glibc wheels only), run
# on the same Python as the host (3.15.0). wget is for the platform's health check (it runs through /bin/sh and wget).
FROM python:3.15.0-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.15.0-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends wget && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PORT=8400
EXPOSE 8400 9400
ENTRYPOINT ["python", "-m", "infra_print"]
