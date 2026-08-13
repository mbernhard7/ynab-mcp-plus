"""Server identity advertised to MCP clients.

Without an explicit icon in ``serverInfo`` a client has nothing authoritative to
render, so it falls back to scraping the registrable domain's favicon and
caching whatever it finds. That cache is not invalidated when the site's favicon
changes, which is why a stale mark can survive removing and re-adding the
connector. Declaring the icon here makes it deterministic.
"""

from mcp.types import Icon

from .settings import settings

# The mark, inlined rather than shipped as package data so it survives any
# packaging layout the container build happens to use. A Y for YNAB, with the
# dotted legs closing it into an M; it replaces the personal monogram this
# server used to borrow from the portfolio site.
ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<defs><linearGradient id="g" x1="0" y1="1" x2="1" y2="0">'
    '<stop offset="0" stop-color="#bfdbfe"/>'
    '<stop offset="0.55" stop-color="#60a5fa"/>'
    '<stop offset="1" stop-color="#2563eb"/>'
    "</linearGradient></defs>"
    '<rect width="64" height="64" rx="16" fill="url(#g)"/>'
    '<g stroke="#0a0c12" stroke-linecap="round">'
    '<g stroke-width="6" stroke-dasharray="0.1 9.9">'
    '<path d="M19 28 V48"/><path d="M45 28 V48"/>'
    "</g>"
    '<g stroke-width="4.5">'
    '<path d="M32 34 L20 21"/><path d="M32 34 L44 21"/><path d="M32 34 L32 47"/>'
    "</g></g>"
    '<g fill="#0a0c12">'
    '<circle cx="19" cy="19" r="6"/><circle cx="45" cy="19" r="6"/>'
    '<circle cx="32" cy="48" r="6"/>'
    "</g></svg>"
)

ICON_PATH = "/icon.svg"


def _base_url() -> str | None:
    return (settings.public_url or "").rstrip("/") or None


def icon_url() -> str | None:
    """Absolute URL of the icon, or None when PUBLIC_URL is unset (stdio mode)."""
    base = _base_url()
    return f"{base}{ICON_PATH}" if base else None


def server_icons() -> list[Icon] | None:
    """``icons`` for ``Server``/``serverInfo``; None when there is no public URL."""
    src = icon_url()
    if not src:
        return None
    return [Icon(src=src, mimeType="image/svg+xml", sizes=["any"])]


def website_url() -> str | None:
    return _base_url()
