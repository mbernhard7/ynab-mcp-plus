"""YNAB OAuth broker.

Implements the MCP SDK's ``OAuthAuthorizationServerProvider`` so this server can
act as the OAuth Authorization Server that MCP clients talk to, while relaying
the actual login to YNAB. YNAB doesn't publish OAuth discovery metadata or
support dynamic client registration, so a broker in front of it is required for
MCP's "just point the client at the URL and sign in" flow.

Nothing is persisted. Client registrations, authorization codes, and issued
access/refresh tokens are all self-contained sealed blobs (see ``crypto``), so
the flow survives restarts and scale-to-zero with no database.
"""

import time
from typing import Optional

import httpx
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from .crypto import Sealer

YNAB_AUTHORIZE_URL = "https://app.ynab.com/oauth/authorize"
YNAB_TOKEN_URL = "https://app.ynab.com/oauth/token"
YNAB_USER_URL = "https://api.ynab.com/v1/user"

# Sealed-blob lifetimes.
_STATE_TTL = 600  # authorize -> YNAB -> callback round trip
_CODE_TTL = 300  # our authorization code -> client /token exchange


class YnabAuthorizationCode(AuthorizationCode):
    ynab_access_token: str
    ynab_refresh_token: str
    ynab_expires_at: int
    ynab_user_id: Optional[str] = None


class YnabRefreshToken(RefreshToken):
    ynab_refresh_token: str
    ynab_user_id: Optional[str] = None


class YnabOAuthProvider:
    """OAuth AS that brokers to YNAB. See module docstring."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        public_url: str,
        token_secret: str,
        scope: Optional[str] = None,
        allowed_user_ids: Optional[set[str]] = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.public_url = public_url.rstrip("/")
        self.scope = scope  # None => full access
        self.allowed_user_ids = allowed_user_ids  # None => any YNAB account
        self.sealer = Sealer(token_secret)
        self.redirect_uri = f"{self.public_url}/oauth/ynab/callback"

    def _user_allowed(self, ynab_user_id: Optional[str]) -> bool:
        if self.allowed_user_ids is None:
            return True
        return ynab_user_id is not None and ynab_user_id in self.allowed_user_ids

    # ---------------------------------------------------------------- #
    # Dynamic client registration (stateless: the client_id encodes the
    # registration, so nothing needs to be stored).
    # ---------------------------------------------------------------- #
    async def handle_register(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            metadata = OAuthClientMetadata.model_validate(body)
        except Exception:
            return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)

        redirect_uris = [str(u) for u in metadata.redirect_uris or []]
        if not redirect_uris:
            return JSONResponse(
                {
                    "error": "invalid_redirect_uri",
                    "error_description": "at least one redirect_uri is required",
                },
                status_code=400,
            )

        client_id = self.sealer.seal(
            {"t": "client", "redirect_uris": redirect_uris, "name": metadata.client_name}
        )
        info = self._client_info(client_id, redirect_uris)
        # Echo back the full registration (RFC 7591). Public client: no secret.
        return JSONResponse(info.model_dump(mode="json", exclude_none=True), status_code=201)

    def _client_info(self, client_id: str, redirect_uris: list[str]) -> OAuthClientInformationFull:
        return OAuthClientInformationFull(
            client_id=client_id,
            client_secret=None,
            redirect_uris=[AnyUrl(u) for u in redirect_uris],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=self.scope,
        )

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        data = self.sealer.unseal(client_id)
        if not data or data.get("t") != "client":
            return None
        return self._client_info(client_id, data.get("redirect_uris", []))

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Registration is handled by handle_register (stateless); the SDK
        # RegistrationHandler is not mounted.
        return None

    # ---------------------------------------------------------------- #
    # Authorization: redirect the browser to YNAB, carrying the client's
    # request sealed into the OAuth `state`.
    # ---------------------------------------------------------------- #
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        state = self.sealer.seal(
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "code_challenge": params.code_challenge,
                "client_state": params.state,
                "scopes": params.scopes,
            }
        )
        query = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": state,
        }
        if self.scope:
            query["scope"] = self.scope
        return construct_redirect_uri(YNAB_AUTHORIZE_URL, **query)

    async def handle_ynab_callback(self, request: Request) -> RedirectResponse:
        """YNAB redirects the browser here after login. Exchange YNAB's code
        for tokens, mint our own authorization code, and bounce the browser back
        to the MCP client."""
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        if not code or not state:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        ctx = self.sealer.unseal(state, ttl=_STATE_TTL)
        if not ctx:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "expired or invalid state"},
                status_code=400,
            )

        try:
            tokens = await self._ynab_token(
                grant_type="authorization_code",
                code=code,
                redirect_uri=self.redirect_uri,
            )
        except httpx.HTTPError:
            return JSONResponse(
                {"error": "server_error", "error_description": "YNAB token exchange failed"},
                status_code=502,
            )

        ynab_user_id: Optional[str] = None
        if self.allowed_user_ids is not None:
            try:
                ynab_user_id = await self._ynab_user_id(tokens["access_token"])
            except httpx.HTTPError:
                return JSONResponse(
                    {"error": "server_error", "error_description": "YNAB user lookup failed"},
                    status_code=502,
                )
            if not self._user_allowed(ynab_user_id):
                return JSONResponse(
                    {
                        "error": "access_denied",
                        "error_description": "This YNAB account is not allowed to use this server.",
                    },
                    status_code=403,
                )

        our_code = self.sealer.seal(
            {
                "t": "code",
                "client_id": ctx["client_id"],
                "redirect_uri": ctx["redirect_uri"],
                "redirect_uri_provided_explicitly": ctx["redirect_uri_provided_explicitly"],
                "code_challenge": ctx["code_challenge"],
                "scopes": ctx.get("scopes") or [],
                "yat": tokens["access_token"],
                "yrt": tokens["refresh_token"],
                "yexp": int(time.time()) + int(tokens.get("expires_in", 7200)),
                "uid": ynab_user_id,
            }
        )
        location = construct_redirect_uri(
            ctx["redirect_uri"], code=our_code, state=ctx.get("client_state")
        )
        return RedirectResponse(url=location, status_code=302)

    # ---------------------------------------------------------------- #
    # Token endpoint: authorization_code + refresh_token grants.
    # ---------------------------------------------------------------- #
    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[YnabAuthorizationCode]:
        data = self.sealer.unseal(authorization_code, ttl=_CODE_TTL)
        if not data or data.get("t") != "code":
            return None
        if data.get("client_id") != client.client_id:
            return None
        return YnabAuthorizationCode(
            code=authorization_code,
            scopes=data.get("scopes") or [],
            expires_at=data["yexp"],
            client_id=client.client_id,
            code_challenge=data["code_challenge"],
            redirect_uri=AnyUrl(data["redirect_uri"]),
            redirect_uri_provided_explicitly=data["redirect_uri_provided_explicitly"],
            ynab_access_token=data["yat"],
            ynab_refresh_token=data["yrt"],
            ynab_expires_at=data["yexp"],
            ynab_user_id=data.get("uid"),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: YnabAuthorizationCode
    ) -> OAuthToken:
        # PKCE was already verified by the SDK's token handler.
        return self._mint(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            ynab_access_token=authorization_code.ynab_access_token,
            ynab_refresh_token=authorization_code.ynab_refresh_token,
            ynab_expires_at=authorization_code.ynab_expires_at,
            ynab_user_id=authorization_code.ynab_user_id,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[YnabRefreshToken]:
        data = self.sealer.unseal(refresh_token)
        if not data or data.get("t") != "rt":
            return None
        if data.get("client_id") != client.client_id:
            return None
        return YnabRefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=data.get("scopes") or [],
            ynab_refresh_token=data["yrt"],
            ynab_user_id=data.get("uid"),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: YnabRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        tokens = await self._ynab_token(
            grant_type="refresh_token",
            refresh_token=refresh_token.ynab_refresh_token,
        )
        ynab_user_id = refresh_token.ynab_user_id
        if self.allowed_user_ids is not None and ynab_user_id is None:
            # Token minted before the allowlist existed: look the user up now.
            ynab_user_id = await self._ynab_user_id(tokens["access_token"])
        if not self._user_allowed(ynab_user_id):
            raise ValueError("This YNAB account is not allowed to use this server.")
        return self._mint(
            client_id=client.client_id,
            scopes=scopes or refresh_token.scopes,
            ynab_access_token=tokens["access_token"],
            ynab_refresh_token=tokens["refresh_token"],
            ynab_expires_at=int(time.time()) + int(tokens.get("expires_in", 7200)),
            ynab_user_id=ynab_user_id,
        )

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        data = self.sealer.unseal(token)
        if not data or data.get("t") != "at":
            return None
        if int(time.time()) >= data["yexp"]:
            return None  # expired; client will refresh
        if not self._user_allowed(data.get("uid")):
            # Not (or no longer) on the allowlist; treat the token as invalid.
            return None
        return AccessToken(
            token=token,
            client_id=data["client_id"],
            scopes=data.get("scopes") or [],
            expires_at=data["yexp"],
            claims={"ynab_access_token": data["yat"], "ynab_user_id": data.get("uid")},
        )

    async def revoke_token(self, token) -> None:
        # Tokens are self-contained and stateless, so there is no server-side
        # record to delete. They expire on their own; to invalidate all
        # outstanding tokens at once, rotate MCP_TOKEN_SECRET.
        return None

    # ---------------------------------------------------------------- #
    # Helpers
    # ---------------------------------------------------------------- #
    def _mint(
        self,
        *,
        client_id: str,
        scopes: list[str],
        ynab_access_token: str,
        ynab_refresh_token: str,
        ynab_expires_at: int,
        ynab_user_id: Optional[str] = None,
    ) -> OAuthToken:
        access = self.sealer.seal(
            {
                "t": "at",
                "client_id": client_id,
                "scopes": scopes,
                "yat": ynab_access_token,
                "yexp": ynab_expires_at,
                "uid": ynab_user_id,
            }
        )
        refresh = self.sealer.seal(
            {
                "t": "rt",
                "client_id": client_id,
                "scopes": scopes,
                "yrt": ynab_refresh_token,
                "uid": ynab_user_id,
            }
        )
        expires_in = max(0, ynab_expires_at - int(time.time()))
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=expires_in,
            refresh_token=refresh,
            scope=" ".join(scopes) if scopes else None,
        )

    async def _ynab_user_id(self, access_token: str) -> str:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                YNAB_USER_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
            resp.raise_for_status()
            return str(resp.json()["data"]["user"]["id"])

    async def _ynab_token(self, **data) -> dict:
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            **data,
        }
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(YNAB_TOKEN_URL, data=payload)
            resp.raise_for_status()
            return resp.json()
