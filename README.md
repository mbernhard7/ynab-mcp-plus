# ynab-mcp-plus

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A [Model Context Protocol](https://modelcontextprotocol.io) server for [YNAB](https://www.ynab.com) (You Need A Budget). It lets an AI assistant read and manage your YNAB plan — accounts, categories, transactions, payees, scheduled transactions — through natural language.

It supports three ways to authenticate, including a full **"Sign in with YNAB" OAuth flow**, so you can self-host it as a real remote MCP server without pasting a long-lived token into a client.

> This is a fork of [Jtewen/ynab-mcp](https://github.com/Jtewen/ynab-mcp), updated for the current YNAB API (the `budgets` → `plans` rename in `ynab` 4.x), trimmed to a focused tool set, and extended with OAuth SSO and an HTTP transport for self-hosting. See [Attribution](#attribution).

## Features

- **13 focused tools** covering the parts of YNAB you actually use conversationally — read accounts/categories/payees/months, query transactions by account, **category**, **payee**, or date, and make changes (budget amounts, transactions, scheduled transactions, payee cleanup).
- **Three auth modes** — a local Personal Access Token, a shared static bearer token, or full **OAuth "Sign in with YNAB"**.
- **Stateless OAuth broker** — issued tokens are self-contained encrypted blobs, so nothing is stored server-side and the flow survives restarts / scale-to-zero. No database required.
- **Read-only mode** — disable every write tool with one env var.
- **Self-host friendly** — ships a `Dockerfile`; deploys to Cloud Run (or anything that runs a container) in a few minutes.

## Tools

| Tool | What it does |
|---|---|
| `list-plans` | List your YNAB plans (hidden in single-plan mode). |
| `list-accounts` | List accounts for a plan. |
| `list-transactions` | List transactions, filtered by **account**, **category**, **payee**, or **month** — or all recent transactions with `since_date`. |
| `list-categories` | List category groups and their budgeted / activity / balance. |
| `list-payees` | List payees. |
| `list-scheduled-transactions` | List upcoming scheduled (recurring) transactions. |
| `get-month-info` | Detailed info for a month (or list all months). |
| `lookup-entity-by-id` | Resolve an account, category, or payee by ID. |
| `lookup-payee-locations` | Geographic locations associated with payees. |
| `manage-budgeted-amount` | Assign a budgeted amount, or move money between categories. *(write)* |
| `bulk-manage-transactions` | Create, update, or delete transactions in bulk. *(write)* |
| `manage-scheduled-transaction` | Create, update, or delete a scheduled transaction. *(write)* |
| `manage-payees` | Merge/rename payees to clean up messy data. *(write)* |

Tools marked *(write)* are disabled when `YNAB_READ_ONLY=true`.

## Choosing an auth mode

| Mode | Best for | How the client authenticates |
|---|---|---|
| **PAT** | Local use (Claude Desktop) over stdio | A YNAB Personal Access Token in the client config |
| **Static bearer** | A private self-hosted instance, minimal setup | One shared secret in the client config |
| **OAuth** | A real remote MCP server; "Sign in with YNAB" | Browser login to YNAB; per-user tokens, nothing pasted |

All three run the same tools. Pick based on how much you care about not putting a long-lived token in your client config.

---

## Setup

### Option A — Local, with a Personal Access Token (stdio)

The simplest way to use it from Claude Desktop.

1. Create a Personal Access Token at [YNAB → Developer Settings](https://app.ynab.com/settings/developer).
2. Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "ynab": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/mbernhard7/ynab-mcp-plus", "ynab-mcp-server"],
      "env": { "YNAB_PAT": "your_token_here" }
    }
  }
}
```

Optional: `YNAB_DEFAULT_PLAN_ID` to lock to one plan, `YNAB_READ_ONLY=true` for safety.

### Option B — Self-host with "Sign in with YNAB" (OAuth) on Cloud Run

This runs it as a remote MCP server. You register **your own** YNAB OAuth app and deploy **your own** instance, so the running service only ever holds your tokens.

**1. Deploy once to get your URL** (the OAuth redirect URI needs it):

```bash
git clone https://github.com/mbernhard7/ynab-mcp-plus && cd ynab-mcp-plus
gcloud run deploy ynab-mcp --source . --region us-central1 --allow-unauthenticated
```

Note the service URL it prints, e.g. `https://ynab-mcp-xxxx-uc.a.run.app`.

**2. Register a YNAB OAuth application** at [YNAB → Developer Settings](https://app.ynab.com/settings/developer) → **New Application**. Set the redirect URI to:

```
https://YOUR-SERVICE-URL/oauth/ynab/callback
```

Copy the **Client ID** and **Client Secret**.

**3. Generate a token-sealing secret:**

```bash
python -m ynab_mcp_server.gen_secret     # or: ynab-mcp-gen-secret
```

**4. Redeploy with the OAuth config** (store secrets in Secret Manager, not on the command line):

```bash
printf '%s' "YOUR_CLIENT_ID"     | gcloud secrets create ynab-oauth-client-id --data-file=-
printf '%s' "YOUR_CLIENT_SECRET" | gcloud secrets create ynab-oauth-client-secret --data-file=-
printf '%s' "YOUR_TOKEN_SECRET"  | gcloud secrets create mcp-token-secret --data-file=-

gcloud run deploy ynab-mcp --source . --region us-central1 --allow-unauthenticated \
  --set-env-vars PUBLIC_URL=https://YOUR-SERVICE-URL \
  --set-secrets YNAB_OAUTH_CLIENT_ID=ynab-oauth-client-id:latest,YNAB_OAUTH_CLIENT_SECRET=ynab-oauth-client-secret:latest,MCP_TOKEN_SECRET=mcp-token-secret:latest
```

`--allow-unauthenticated` is correct here: Cloud Run's own gate is off so the server's *own* OAuth protects `/mcp`.

**5. Connect your MCP client** to `https://YOUR-SERVICE-URL/mcp`. It discovers the OAuth endpoints automatically and prompts you to sign in with YNAB — no token pasting.

### Option C — Self-host with a static bearer token (no OAuth)

If you don't want the OAuth flow, gate the endpoint with one shared secret:

```bash
gcloud run deploy ynab-mcp --source . --region us-central1 --allow-unauthenticated \
  --set-secrets YNAB_PAT=ynab-pat:latest,MCP_AUTH_TOKEN=mcp-auth-token:latest
```

Then point your client at `https://YOUR-SERVICE-URL/mcp` with header `Authorization: Bearer <MCP_AUTH_TOKEN>`. All calls use the configured `YNAB_PAT`.

> The server picks its mode automatically: **OAuth** if the OAuth vars are set, otherwise **static bearer** (HTTP) or **PAT** (stdio).

## Configuration

| Variable | Mode | Description |
|---|---|---|
| `YNAB_PAT` | PAT / static bearer | YNAB Personal Access Token. |
| `MCP_AUTH_TOKEN` | static bearer | Shared secret gating `/mcp` when OAuth is off. |
| `YNAB_OAUTH_CLIENT_ID` | OAuth | Client ID of your YNAB OAuth app. |
| `YNAB_OAUTH_CLIENT_SECRET` | OAuth | Client secret of your YNAB OAuth app. |
| `PUBLIC_URL` | OAuth | This server's externally reachable base URL. |
| `MCP_TOKEN_SECRET` | OAuth | Fernet key for sealing issued tokens (`ynab-mcp-gen-secret`). |
| `YNAB_OAUTH_SCOPE` | OAuth | Set to `read-only` for a read-only grant. Omit for full access. |
| `YNAB_DEFAULT_PLAN_ID` | any | Lock to one plan; hides `list-plans`. |
| `YNAB_READ_ONLY` | any | `true` disables all write tools. |

## Security notes

- **Self-hosting is the recommended model.** Each instance is single-user and only ever handles your tokens. Don't run one shared instance for other people unless you intend to take on custody of their financial OAuth sessions.
- **No token is stored at rest.** In OAuth mode the tokens handed to your client are encrypted blobs; the server keeps only the `MCP_TOKEN_SECRET`. There is no database of credentials to leak.
- **Revocation** is by rotating `MCP_TOKEN_SECRET` (invalidates all issued tokens) or revoking the app in YNAB. Individual stateless tokens can't be revoked one-by-one — fine for single-user.
- **Keep `MCP_TOKEN_SECRET` and the OAuth client secret in a secret store** (e.g. Secret Manager), never in the repo or image.
- YNAB tokens are all-or-nothing on scope (no per-endpoint scoping); `read-only` is the one lever, via `YNAB_OAUTH_SCOPE`.

## Development

```bash
git clone https://github.com/mbernhard7/ynab-mcp-plus && cd ynab-mcp-plus
uv sync
# stdio (PAT):
YNAB_PAT=... uv run ynab-mcp-server
# HTTP (OAuth or bearer), on :8080:
uv run ynab-mcp-server-http
```

Inspect with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv run ynab-mcp-server
```

## Attribution

Forked from [**Jtewen/ynab-mcp**](https://github.com/Jtewen/ynab-mcp) by Jake Ewen, MIT-licensed. This fork updates it to the current YNAB `plans` API, trims the tool set, and adds the HTTP transport and OAuth broker. The original license and copyright are retained in [LICENSE](LICENSE).

Built on the [YNAB API](https://api.ynab.com/) and the [Model Context Protocol](https://modelcontextprotocol.io).

## License

MIT — see [LICENSE](LICENSE).
