"""Google sign-in for the tenant API.

The browser gets an ID token from Google Identity Services and posts it to /api/auth/google.
This module checks the token (signature, audience, issuer, expiry, verified email) and hands
back the three fields the server keeps in memory. Nothing here touches SpacetimeDB.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISSUERS = ("accounts.google.com", "https://accounts.google.com")


def load_env():
    """Same shape as the agents' loaders: repo-root .env, never overriding the real environment."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def google_client_id():
    load_env()
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def verify_google_token(credential, client_id, request=None):
    """Return {"sub", "email", "name"} for a valid Google ID token, else raise ValueError."""
    if not credential or not client_id:
        raise ValueError("Missing token or client id.")
    from google.auth import exceptions  # noqa: PLC0415
    from google.auth.transport import requests  # noqa: PLC0415
    from google.oauth2 import id_token  # noqa: PLC0415

    try:
        info = id_token.verify_oauth2_token(credential, request or requests.Request(), client_id)
    except (ValueError, exceptions.GoogleAuthError) as e:
        raise ValueError(f"Invalid Google token: {e}") from None
    if info.get("iss") not in ISSUERS:
        raise ValueError("Invalid Google token: wrong issuer.")
    if not info.get("sub"):
        raise ValueError("Invalid Google token: no subject.")
    if info.get("email_verified") is not True:
        raise ValueError("Google account email is not verified.")
    return {"sub": info["sub"], "email": info.get("email") or "", "name": info.get("name") or info.get("email") or "Tenant"}
