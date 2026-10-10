FROM python:3.11-slim

# Installs exactly what uv.lock pins, so a push to master deploys the versions
# that were tested rather than whatever is newest that day.
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

ENV PORT=8080 PATH="/app/.venv/bin:$PATH"
EXPOSE 8080

CMD ["ynab-mcp-server-http"]
