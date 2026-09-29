# BotGraph pipeline services and API: one image, the command picks the role.
#   docker build -f deploy/Dockerfile -t botgraph .
#   docker run --rm botgraph --help                                  # ingest | detect | replay ...
#   docker run --rm --entrypoint botgraph-api botgraph serve --host 0.0.0.0
# Trained model bundles are not baked in: mount them at /app/ml/models.
#
# Hardened for Kubernetes: uv only exists in the build stage, the runtime runs as a fixed
# non-root UID and works with a read-only root filesystem (writable paths: /app/data, /tmp).

FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.4.20 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md params.yaml ./
COPY packages packages
COPY ml/pyproject.toml ml/pyproject.toml
COPY ml/src ml/src
COPY ml/labels ml/labels
COPY services services
COPY api api
RUN uv sync --frozen --no-dev

FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/app/.venv/bin:$PATH \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    BOTGRAPH_LOG_FORMAT=json
RUN groupadd --gid 10001 botgraph \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin botgraph
WORKDIR /app
# Workspace packages are installed editable: the source tree stays at /app (repo_path()).
COPY --from=build /app /app
RUN mkdir -p /app/data && chown 10001:10001 /app/data
USER 10001:10001
ENTRYPOINT ["botgraph"]
CMD ["--help"]
