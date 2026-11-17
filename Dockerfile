# What: container image for aisync, published to ghcr.io by the release workflow.
# Why: GitHub Packages does not host Python packages, so the image is the package.
#      Glama (and any MCP host) launches the image with no arguments and talks MCP over stdio,
#      so the default command is the MCP server, and the mcp extra must be installed for it to start.
# How: docker run --rm -i ghcr.io/maxont/aisync            (MCP server over stdio)
#      docker run --rm ghcr.io/maxont/aisync --help        (CLI)
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/MaxonT/aisync" \
      org.opencontainers.image.description="Advanced context management for all your AI coding tools: shared rules, a hash-chained journal, file claims and mid-session news hooks for Claude Code, Codex and Cursor." \
      org.opencontainers.image.licenses="MIT"
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY ai_sync ./ai_sync
RUN pip install --no-cache-dir ".[mcp]" && rm -rf /src
WORKDIR /data
ENV NO_STAR_PROMPT=1
ENTRYPOINT ["aisync"]
CMD ["mcp"]
