# syntax=docker/dockerfile:1.7
#
# Polygraph Cloud API + worker image (one image, three commands).
#
# The build context is cloud/api. The frozen results schema comes from the
# monorepo package, passed in as a named build context:
#
#   docker build -t polygraph-cloud \
#     --build-context polygraph_pkg=packages/pytest-polygraph \
#     cloud/api
#
# docker compose: build.context: cloud/api, build.additional_contexts:
#   polygraph_pkg: packages/pytest-polygraph
#
# Commands:
#   API     (default)  uvicorn polygraph_cloud.app:create_app --factory ...
#   worker             python -m polygraph_cloud.worker
#   migrate (release)  python -m polygraph_cloud.cli migrate
#   prune   (daily)    python -m polygraph_cloud.jobs.prune

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /src
# Only polygraph.results / polygraph.metrics are used (pydantic only), so no deps.
COPY --from=polygraph_pkg pyproject.toml README.md /src/pytest-polygraph/
COPY --from=polygraph_pkg src /src/pytest-polygraph/src
RUN pip install --no-deps /src/pytest-polygraph
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
RUN pip install . \
 && find /opt/venv -name "__pycache__" -prune -exec rm -rf {} + \
 && python -c "import polygraph.results, polygraph.metrics, polygraph_cloud.app"

FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="polygraph-cloud" \
      org.opencontainers.image.description="Polygraph Cloud API and upload worker" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
# ECS runs with a read-only root file system; /tmp is the one writable mount.
RUN mkdir -p /tmp && chmod 1777 /tmp
VOLUME ["/tmp"]
WORKDIR /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "polygraph_cloud.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--no-access-log", "--no-server-header", "--timeout-graceful-shutdown", "20"]
