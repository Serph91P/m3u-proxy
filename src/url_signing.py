"""
HMAC signatures for the upstream URLs the proxy writes into rewritten
HLS playlists and DASH manifests.

The segment, file and variant-playlist routes fetch whatever URL they are
handed, so they only accept URLs carrying a signature the proxy produced
itself. The key is derived from API_TOKEN, which every worker sharing a
deployment already has; without a token a random per-process key is used.
"""

import base64
import hashlib
import hmac
import secrets
from typing import Optional

from config import settings

_FALLBACK_KEY = secrets.token_bytes(32)


def _signing_key() -> bytes:
    if settings.API_TOKEN:
        return hashlib.sha256(
            b"m3u-proxy-url-signing\0" + settings.API_TOKEN.encode()
        ).digest()
    return _FALLBACK_KEY


def sign_url(url: str) -> str:
    """Return a short URL-safe signature for an upstream URL."""
    digest = hmac.new(_signing_key(), url.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:16]).decode().rstrip("=")


def verify_url(url: str, signature: Optional[str]) -> bool:
    """Check a signature produced by sign_url, in constant time."""
    if not signature:
        return False
    return hmac.compare_digest(sign_url(url), signature)
