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
# packaging layout the container build happens to use.
ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<defs><linearGradient id="logoGrad" x1="0" y1="1" x2="1" y2="0">'
    '<stop offset="0" stop-color="#a3e635"/>'
    '<stop offset="0.55" stop-color="#34d399"/>'
    '<stop offset="1" stop-color="#2dd4a8"/>'
    "</linearGradient></defs>"
    '<rect width="64" height="64" rx="16" fill="url(#logoGrad)"/>'
    '<path d="M10.5 52 L 15.28 18 L 24.78 18 L 32.61 29.82 L 43.54 18.48 L 39.62 14.83 '
    'L 56.09 11.85 L 53.95 28.17 L 50.03 24.52 L 30.4 44.88 L 22.65 33.17 L 20 52 Z" '
    'fill="#0a0c12"/>'
    '<path d="M41.5 52 L 43.75 36 L 53.25 36 L 51 52 Z" fill="#0a0c12"/>'
    "</svg>"
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
