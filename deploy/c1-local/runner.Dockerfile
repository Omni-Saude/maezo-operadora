# C1: runner Python 3.12 (o mesmo do portal) so com as DEPENDENCIAS travadas no uv.lock.
# O codigo nao entra na imagem: o checkout e montado read-only em /repo (PYTHONPATH).
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client openssl \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 1000 c1 && useradd --uid 1000 --gid 1000 --create-home c1
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/repo/src:/repo:/repo/deploy/c1-local
WORKDIR /repo
