ARG BASE_IMAGE="python:3.14.0"

FROM ${BASE_IMAGE} AS base

ENV HOME_PATH="/opt"
ENV PATH="${HOME_PATH}/.venv/bin:${PATH}"

WORKDIR ${HOME_PATH}

FROM base AS deps

RUN pip install --no-cache-dir --upgrade pip \
&& pip install --no-cache-dir --upgrade uv==0.10.7

COPY pyproject.toml uv.lock ${HOME_PATH}/
RUN uv sync --frozen --no-install-project --no-dev

FROM deps AS development

COPY --from=deps ${HOME_PATH}/.venv ${HOME_PATH}/.venv
COPY event_receiver ${HOME_PATH}/event_receiver
COPY uvicorn_config.json ${HOME_PATH}
WORKDIR ${HOME_PATH}

EXPOSE 8888

ENTRYPOINT ["uvicorn", "event_receiver.main:app", "--host", "0.0.0.0", "--port", "8888", "--log-config", "uvicorn_config.json"]
