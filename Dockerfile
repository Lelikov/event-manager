# Self-contained build (context = this service repo root):
#   docker build -t event-receiver .
ARG BASE_IMAGE="python:3.14.0"

FROM ${BASE_IMAGE} AS base

ENV APP_PATH="/app/event-receiver"
ENV PATH="${APP_PATH}/.venv/bin:${PATH}"

WORKDIR ${APP_PATH}

FROM base AS deps

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --upgrade uv==0.10.7

# event-schemas is a git dependency pinned in uv.lock; uv fetches it during sync.
COPY pyproject.toml uv.lock ${APP_PATH}/
RUN uv sync --frozen --no-install-project --no-dev

FROM deps AS development

COPY event_receiver ${APP_PATH}/event_receiver
COPY uvicorn_config.json ${APP_PATH}/

EXPOSE 8888

ENTRYPOINT ["uvicorn", "event_receiver.main:app", "--host", "0.0.0.0", "--port", "8888", "--log-config", "uvicorn_config.json"]
