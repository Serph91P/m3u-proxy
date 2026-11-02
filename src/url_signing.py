"""
HMAC signatures for the upstream URLs the proxy writes into rewritten
HLS playlists and DASH manifests.

The segment, file and variant-playlist routes fetch whatever URL they are
handed, so they only accept URLs carrying a signature the proxy produced
itself. Capabilities bind the issuing StreamInfo generation, parent/credential
context, resource role and header origin. The key is derived from API_TOKEN;
a shared token alone does not share the in-memory stream context between
workers. Without a token a random per-process key is used.
"""

import base64
import functools
import hashlib
import hmac
import secrets
from typing import Optional

from config import settings

_FALLBACK_KEY = secrets.token_bytes(32)


@functools.lru_cache(maxsize=4)
def _derive_key(api_token: str) -> bytes:
    # Rewritten playlists expose (url, signature) pairs, so a fast hash would let
    # anyone holding one test API_TOKEN guesses offline. PBKDF2 makes each guess
    # expensive; the result is cached, so this runs once per process.
    return hashlib.pbkdf2_hmac(
        "sha256", api_token.encode(), b"m3u-proxy-url-signing", 600_000
    )


def _signing_key() -> bytes:
    if settings.API_TOKEN:
        return _derive_key(settings.API_TOKEN)
    return _FALLBACK_KEY


def url_origin(url: str) -> str:
    """Canonical HTTP origin, without userinfo, path, query or credentials."""
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.scheme}://{parsed.hostname.lower()}:{port}"


def sign_url(
    url: str, *, context: str = "", role: str = "", source_origin: str = ""
) -> str:
    """Sign a target, resource role, live context and header origin (v2 only).

    Context is authenticated, not serialized: it can contain private source
    configuration. Only the credential-free origin and MAC leave the server.
    """
    import json

    origin = base64.urlsafe_b64encode(source_origin.encode()).decode().rstrip("=")
    payload = json.dumps(["v2", url, context, role, origin]).encode()
    digest = hmac.new(_signing_key(), payload, hashlib.sha256).digest()
    mac = base64.urlsafe_b64encode(digest[:16]).decode().rstrip("=")
    return f"v2.{origin}.{mac}"


def signature_origin(signature: str) -> str:
    """Read the origin only after verify_url has authenticated the token."""
    version, origin, _ = signature.split(".")
    if version != "v2":
        raise ValueError("Invalid capability version")
    return base64.urlsafe_b64decode(origin + "=" * (-len(origin) % 4)).decode()


def verify_url(
    url: str, signature: Optional[str], *, context: str = "", role: str = ""
) -> bool:
    """Reject legacy/unbound tokens; compare the scoped MAC in constant time."""
    if not signature or not signature.isascii():
        return False
    try:
        origin = signature_origin(signature)
    except (ValueError, UnicodeError):
        return False
    return hmac.compare_digest(
        sign_url(url, context=context, role=role, source_origin=origin), signature
    )
