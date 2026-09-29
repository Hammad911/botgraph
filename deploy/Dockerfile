# BotGraph pipeline services (ingest, detect, replay): one image, the command picks the role.
#   docker build -f deploy/Dockerfile -t botgraph .
#   docker run --rm botgraph --help
# Trained model bundles are not baked in: mount them at /app/ml/models.
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.4.20 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md params.yaml ./
COPY packages packages
COPY ml/pyproject.toml ml/pyproject.toml
COPY ml/src ml/src
COPY ml/labels ml/labels
COPY services services
RUN uv sync --frozen --no-dev

RUN useradd --create-home botgraph && mkdir -p /app/data && chown botgraph /app/data
USER botgraph
ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "botgraph"]
CMD ["--help"]
