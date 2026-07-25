"""HTTP entry point.

Runs the MCP server over the Streamable HTTP transport in one of two modes:

* **OAuth mode** (when OAuth env vars are set): the server is an OAuth
  Authorization Server that brokers "Sign in with YNAB". Clients discover it via
  the standard ``.well-known`` metadata, register dynamically, and obtain
  per-user tokens. See ``oauth_provider``.
* **Static bearer mode** (otherwise): the ``/mcp`` endpoint is gated by a single
  shared ``MCP_AUTH_TOKEN`` and all calls use the configured ``YNAB_PAT``.
"""

import contextlib
import hmac
import os

import uvicorn
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.routes import (
    build_metadata,
    build_resource_metadata_url,
    cors_middleware,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp

from .oauth_provider import YnabOAuthProvider
from .onboarding import handle_onboard_request, onboarding_page
from .server import server
from .settings import settings


async def healthz(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def _session_manager() -> StreamableHTTPSessionManager:
    return StreamableHTTPSessionManager(app=server, json_response=False, stateless=True)


def _lifespan(session_manager: StreamableHTTPSessionManager):
    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with session_manager.run():
            yield

    return lifespan


# ---------------------------------------------------------------------------- #
# OAuth mode
# ---------------------------------------------------------------------------- #
def build_oauth_app() -> Starlette:
    provider = YnabOAuthProvider(
        client_id=settings.ynab_oauth_client_id,
        client_secret=settings.ynab_oauth_client_secret,
        public_url=settings.public_url,
        token_secret=settings.token_secret,
        scope=settings.ynab_oauth_scope,
        allowed_user_ids=settings.allowed_user_ids,
    )

    issuer = AnyHttpUrl(settings.public_url)
    resource_url = AnyHttpUrl(settings.public_url.rstrip("/") + "/mcp")
    resource_metadata_url = build_resource_metadata_url(resource_url)

    metadata = build_metadata(
        issuer_url=issuer,
        service_documentation_url=None,
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(),
    )
    client_authenticator = ClientAuthenticator(provider)

    session_manager = _session_manager()
    mcp_app = RequireAuthMiddleware(
        _AsgiMount(session_manager), required_scopes=[], resource_metadata_url=resource_metadata_url
    )

    routes = [
        Route("/", onboarding_page, methods=["GET"]),
        Route("/onboard/request", handle_onboard_request, methods=["POST"]),
        Route("/healthz", healthz),
        Route(
            "/.well-known/oauth-authorization-server",
            endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        ),
        Route(
            "/authorize", endpoint=AuthorizationHandler(provider).handle, methods=["GET", "POST"]
        ),
        Route(
            "/token",
            endpoint=cors_middleware(
                TokenHandler(provider, client_authenticator).handle, ["POST", "OPTIONS"]
            ),
            methods=["POST", "OPTIONS"],
        ),
        Route(
            "/register",
            endpoint=cors_middleware(provider.handle_register, ["POST", "OPTIONS"]),
            methods=["POST", "OPTIONS"],
        ),
        Route("/oauth/ynab/callback", endpoint=provider.handle_ynab_callback, methods=["GET"]),
        *create_protected_resource_routes(resource_url, [issuer]),
        Route("/mcp", endpoint=mcp_app),
    ]

    middleware = [
        Middleware(
            AuthenticationMiddleware, backend=BearerAuthBackend(ProviderTokenVerifier(provider))
        ),
        Middleware(AuthContextMiddleware),
    ]

    return Starlette(routes=routes, middleware=middleware, lifespan=_lifespan(session_manager))


class _AsgiMount:
    """Adapts a StreamableHTTPSessionManager to a plain ASGI callable."""

    def __init__(self, session_manager: StreamableHTTPSessionManager):
        self._sm = session_manager

    async def __call__(self, scope, receive, send):
        await self._sm.handle_request(scope, receive, send)


# ---------------------------------------------------------------------------- #
# Static bearer mode
# ---------------------------------------------------------------------------- #
class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, token: str):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        provided = request.headers.get("authorization", "")
        if provided.startswith("Bearer "):
            provided = provided[len("Bearer ") :]
        if not hmac.compare_digest(provided, self._token):
            return PlainTextResponse("Unauthorized", status_code=401)
        return await call_next(request)


def build_bearer_app() -> Starlette:
    token = settings.mcp_auth_token
    if not token:
        raise RuntimeError("MCP_AUTH_TOKEN is required in static bearer mode (or configure OAuth).")
    session_manager = _session_manager()
    app = Starlette(
        routes=[Route("/healthz", healthz), Route("/mcp", endpoint=_AsgiMount(session_manager))],
        lifespan=_lifespan(session_manager),
    )
    app.add_middleware(BearerAuthMiddleware, token=token)
    return app


def build_app() -> Starlette:
    return build_oauth_app() if settings.oauth_enabled else build_bearer_app()


def main() -> None:
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(build_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
