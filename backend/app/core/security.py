"""API-credential token generation and verification.

Design note: the token is hashed with a *keyed* HMAC-SHA256 rather than a slow
password hash (bcrypt/argon2). That is deliberate. Authenticating a request has
to answer "which credential is this?" from the token alone, which means looking
the hash up in an index -- impossible with a per-row random salt without
scanning every credential in the table. HMAC keyed by API_SECRET keeps the
lookup a single indexed equality check while ensuring a leaked database dump
alone cannot be brute-forced into working tokens: the attacker also needs
API_SECRET, which never touches the database.

Tokens are high-entropy (256 bits) and randomly generated, so the offline
brute-force resistance a slow hash buys for human passwords is not needed here.
"""

import hashlib
import hmac
import secrets

from app.core.config import get_settings

# Characters of the raw token kept in cleartext so the console can show the
# user *which* credential a row is without ever revealing the usable secret.
PREVIEW_LENGTH = 12


def generate_token() -> str:
    """Create a new raw API token. Shown to the user exactly once."""
    settings = get_settings()
    return f"{settings.token_prefix}{secrets.token_urlsafe(32)}"


def hash_token(raw_token: str) -> str:
    """Derive the stored, non-reversible representation of a raw token."""
    settings = get_settings()
    return hmac.new(
        settings.api_secret.encode("utf-8"),
        raw_token.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def token_preview(raw_token: str) -> str:
    """A safe, display-only fragment of the token, e.g. `memora_a1b2c3...`."""
    return f"{raw_token[:PREVIEW_LENGTH]}..."


def verify_admin_key(supplied: str | None) -> bool:
    """Constant-time check of the console admin key.

    An empty ADMIN_API_KEY disables the check entirely, which is the intended
    default for local development.
    """
    expected = get_settings().admin_api_key
    if not expected:
        return True
    if not supplied:
        return False
    return hmac.compare_digest(supplied, expected)
