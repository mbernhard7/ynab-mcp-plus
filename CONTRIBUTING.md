# Contributing

Thanks for your interest! Here's how to get set up.

## Development setup

```bash
git clone https://github.com/mbernhard7/ynab-mcp-plus.git
cd ynab-mcp-plus
uv sync
```

Create a `.env` (see [`.env.example`](.env.example)) for the mode you want to run.

## Running

```bash
# stdio (Personal Access Token)
YNAB_PAT=... uv run ynab-mcp-server

# HTTP transport (OAuth or static bearer), on :8080
uv run ynab-mcp-server-http
```

## Before opening a PR

```bash
ruff check .
ruff format --check .
```

Please keep changes focused, follow the existing style, and update the README
when you change tool behavior or configuration.
