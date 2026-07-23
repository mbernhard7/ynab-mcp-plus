"""Symmetric sealing of tokens.

The OAuth broker never stores anything server-side. Instead it encrypts small
JSON payloads (a client registration, an authorization code, or a user's YNAB
tokens) into opaque strings that the MCP client holds and hands back. Only this
process, holding ``MCP_TOKEN_SECRET``, can decrypt them, so the values are
authenticated and tamper-evident and survive restarts and scale-to-zero without
any database.
"""

import json
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


def generate_secret() -> str:
    """Generate a fresh key suitable for ``MCP_TOKEN_SECRET``."""
    return Fernet.generate_key().decode()


class Sealer:
    def __init__(self, secret: str):
        # A Fernet key is 32 url-safe-base64 bytes. Fail loudly on a bad key
        # rather than at first request.
        self._fernet = Fernet(secret.encode() if isinstance(secret, str) else secret)

    def seal(self, payload: dict) -> str:
        return self._fernet.encrypt(json.dumps(payload).encode()).decode()

    def unseal(self, token: str, ttl: Optional[int] = None) -> Optional[dict]:
        """Decrypt a sealed payload. Returns None if invalid, tampered, or
        (when ``ttl`` is given) older than ``ttl`` seconds."""
        try:
            raw = self._fernet.decrypt(token.encode(), ttl=ttl)
        except (InvalidToken, ValueError):
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
