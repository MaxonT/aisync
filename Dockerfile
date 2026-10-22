# What: container image for aisync, published to ghcr.io by the release workflow.
# Why: GitHub Packages does not host Python packages, so the image is the package.
# How: docker run --rm ghcr.io/maxont/aisync --help
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/MaxonT/aisync" \
      org.opencontainers.image.description="One standup for all your AI coding tools: shared rules, a hash-chained journal, file claims and mid-session news hooks for Claude Code, Codex and Cursor." \
      org.opencontainers.image.licenses="MIT"
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY ai_sync ./ai_sync
RUN pip install --no-cache-dir . && rm -rf /src
WORKDIR /data
ENV NO_STAR_PROMPT=1
ENTRYPOINT ["aisync"]
CMD ["--help"]
