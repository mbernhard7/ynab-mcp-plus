from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables (or a .env file).

    The server runs in one of two auth modes:

    * **PAT mode** — set ``YNAB_PAT`` to a Personal Access Token. Good for local
      stdio use and single-user self-hosting without a login flow.
    * **OAuth mode** — set ``YNAB_OAUTH_CLIENT_ID`` / ``YNAB_OAUTH_CLIENT_SECRET``
      (from a registered YNAB OAuth application), ``PUBLIC_URL`` (this server's
      externally reachable base URL), and ``MCP_TOKEN_SECRET``. Clients then
      "Sign in with YNAB"; no token is stored server-side.
    """

    # --- PAT mode ---------------------------------------------------------- #
    ynab_api_token: Optional[str] = Field(None, alias="YNAB_PAT")
    """A YNAB Personal Access Token. Enables PAT mode when set."""

    # --- OAuth mode -------------------------------------------------------- #
    ynab_oauth_client_id: Optional[str] = Field(None, alias="YNAB_OAUTH_CLIENT_ID")
    ynab_oauth_client_secret: Optional[str] = Field(None, alias="YNAB_OAUTH_CLIENT_SECRET")
    public_url: Optional[str] = Field(None, alias="PUBLIC_URL")
    """This server's externally reachable base URL, e.g. https://x.run.app.
    Used as the OAuth issuer and to build the YNAB redirect URI."""

    token_secret: Optional[str] = Field(None, alias="MCP_TOKEN_SECRET")
    """Fernet key used to seal issued tokens. Generate with
    ``python -m ynab_mcp_server.gen_secret``."""

    ynab_oauth_scope: Optional[str] = Field(None, alias="YNAB_OAUTH_SCOPE")
    """Set to "read-only" for a read-only OAuth grant. Omit for full access."""

    # --- Static bearer (simple self-host without OAuth) -------------------- #
    mcp_auth_token: Optional[str] = Field(None, alias="MCP_AUTH_TOKEN")
    """A static bearer token gating the HTTP endpoint when OAuth is not
    configured. Used only in PAT mode over HTTP."""

    # --- Behaviour --------------------------------------------------------- #
    ynab_default_plan_id: Optional[str] = Field(None, alias="YNAB_DEFAULT_PLAN_ID")
    """If set, the server operates in single-plan mode with this plan ID."""

    ynab_read_only: bool = Field(False, alias="YNAB_READ_ONLY")
    """If true, write tools are disabled."""

    slack_webhook_url: Optional[str] = Field(None, alias="SLACK_WEBHOOK_URL")
    """Slack incoming-webhook URL for onboarding access requests. When set, the
    onboarding page's request form is enabled and submissions are posted here."""

    slack_signing_secret: Optional[str] = Field(None, alias="SLACK_SIGNING_SECRET")
    """Signing secret of the Slack app behind the webhook. Enables the Approve
    button: interaction callbacks to /slack/interact are verified with it."""

    smtp_host: Optional[str] = Field(None, alias="SMTP_HOST")
    """SMTP server for approval emails, e.g. smtp.gmail.com."""

    smtp_port: int = Field(587, alias="SMTP_PORT")

    smtp_user: Optional[str] = Field(None, alias="SMTP_USER")

    smtp_pass: Optional[str] = Field(None, alias="SMTP_PASS")
    """SMTP password — for Gmail, an App Password (myaccount.google.com/apppasswords)."""

    approval_email_from: Optional[str] = Field(None, alias="APPROVAL_EMAIL_FROM")
    """From address for approval emails. Defaults to SMTP_USER."""

    ynab_allowed_user_ids: Optional[str] = Field(None, alias="YNAB_ALLOWED_USER_IDS")
    """Comma-separated list of YNAB user IDs allowed to use this server.
    When set, any caller whose YNAB account is not in the list is rejected
    (both OAuth sign-ins and the configured PAT). Find your ID at
    https://api.ynab.com/v1/user. Unset means no restriction."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def allowed_user_ids(self) -> Optional[set[str]]:
        """Parsed YNAB_ALLOWED_USER_IDS, or None when no restriction is set."""
        if not self.ynab_allowed_user_ids:
            return None
        ids = {part.strip() for part in self.ynab_allowed_user_ids.split(",") if part.strip()}
        return ids or None

    @property
    def oauth_enabled(self) -> bool:
        return bool(
            self.ynab_oauth_client_id
            and self.ynab_oauth_client_secret
            and self.public_url
            and self.token_secret
        )


settings = Settings()
