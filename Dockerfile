FROM ghcr.io/astral-sh/uv:python3.13-bookworm@sha256:47965cdc9d53a515f68f78241161c901e70051ce428f12e791bd7fe19f6a631a

# Create a non-root user with an explicit UID
RUN adduser --disabled-password --gecos '' --uid 1000 agent

USER agent
WORKDIR /home/agent

# Copy dependency files first
COPY --chown=agent:agent pyproject.toml uv.lock README.md ./

# Install dependencies
RUN --mount=type=cache,target=/home/agent/.cache/uv,uid=1000 \
    uv sync --frozen --no-install-project

# Copy source code
COPY --chown=agent:agent src src

# Install the project itself
RUN --mount=type=cache,target=/home/agent/.cache/uv,uid=1000 \
    uv sync --frozen

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PATH="/home/agent/.venv/bin:$PATH"

ARG VCS_REF=""
ARG SOURCE_URL=""
LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="${SOURCE_URL}"

ENTRYPOINT ["uv", "run", "src/server.py"]
CMD ["--host", "0.0.0.0"]
EXPOSE 9009

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:9009/.well-known/agent-card.json', timeout=2).raise_for_status()"
