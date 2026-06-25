from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from html import escape
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse


router = APIRouter(prefix="/oauth", tags=["oauth"])


@dataclass(frozen=True)
class OAuthProvider:
    name: str
    auth_url: str
    token_url: str
    client_id_env: str
    client_secret_env: str
    scopes: tuple[str, ...]
    scope_separator: str = " "
    extra_auth_params: dict[str, str] | None = None


PROVIDERS: dict[str, OAuthProvider] = {
    "gmail": OAuthProvider(
        name="gmail",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        client_id_env="GOOGLE_OAUTH_CLIENT_ID",
        client_secret_env="GOOGLE_OAUTH_CLIENT_SECRET",
        scopes=(
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.readonly",
        ),
        extra_auth_params={"access_type": "offline", "prompt": "consent"},
    ),
    "linkedin": OAuthProvider(
        name="linkedin",
        auth_url="https://www.linkedin.com/oauth/v2/authorization",
        token_url="https://www.linkedin.com/oauth/v2/accessToken",
        client_id_env="LINKEDIN_CLIENT_ID",
        client_secret_env="LINKEDIN_CLIENT_SECRET",
        scopes=("openid", "profile", "email", "w_member_social"),
    ),
    "meta": OAuthProvider(
        name="meta",
        auth_url="https://www.facebook.com/v19.0/dialog/oauth",
        token_url="https://graph.facebook.com/v19.0/oauth/access_token",
        client_id_env="META_APP_ID",
        client_secret_env="META_APP_SECRET",
        scopes=("public_profile", "email", "user_posts", "instagram_basic"),
        scope_separator=",",
    ),
}


def _public_base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def _redirect_uri(request: Request, provider: str) -> str:
    return f"{_public_base_url(request)}/oauth/{provider}/callback"


def _state_secret() -> bytes:
    secret = os.getenv("OAUTH_STATE_SECRET") or os.getenv("GROQ_API_KEY") or "shadowself-dev"
    return secret.encode("utf-8")


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload: bytes) -> str:
    return _b64encode(hmac.new(_state_secret(), payload, hashlib.sha256).digest())


def _make_state(provider: str) -> str:
    payload = json.dumps(
        {"provider": provider, "nonce": secrets.token_urlsafe(12), "iat": int(time.time())},
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{_b64encode(payload)}.{_sign(payload)}"


def _read_state(value: str, expected_provider: str) -> dict[str, Any]:
    try:
        payload_part, signature = value.split(".", 1)
        payload = _b64decode(payload_part)
        if not hmac.compare_digest(signature, _sign(payload)):
            raise ValueError("bad signature")
        data = json.loads(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid OAuth state.") from exc

    if data.get("provider") != expected_provider:
        raise HTTPException(status_code=400, detail="OAuth state provider mismatch.")
    if int(time.time()) - int(data.get("iat", 0)) > 900:
        raise HTTPException(status_code=400, detail="OAuth state expired. Try connecting again.")
    return data


def _provider(provider: str) -> OAuthProvider:
    config = PROVIDERS.get(provider)
    if not config:
        raise HTTPException(status_code=404, detail="Unsupported OAuth provider.")
    return config


def _client_config(config: OAuthProvider) -> tuple[str, str]:
    client_id = os.getenv(config.client_id_env, "").strip()
    client_secret = os.getenv(config.client_secret_env, "").strip()
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail=f"{config.client_id_env} and {config.client_secret_env} must be configured.",
        )
    return client_id, client_secret


@router.get("/providers")
async def oauth_providers() -> dict[str, Any]:
    return {
        name: {
            "configured": bool(os.getenv(provider.client_id_env) and os.getenv(provider.client_secret_env)),
            "client_id_env": provider.client_id_env,
            "client_secret_env": provider.client_secret_env,
            "scopes": list(provider.scopes),
        }
        for name, provider in PROVIDERS.items()
    }


@router.get("/{provider}/start")
async def oauth_start(provider: str, request: Request) -> RedirectResponse:
    config = _provider(provider)
    client_id, _ = _client_config(config)
    params = {
        "client_id": client_id,
        "redirect_uri": _redirect_uri(request, provider),
        "response_type": "code",
        "scope": config.scope_separator.join(config.scopes),
        "state": _make_state(provider),
    }
    if config.extra_auth_params:
        params.update(config.extra_auth_params)
    return RedirectResponse(f"{config.auth_url}?{urlencode(params)}")


@router.get("/{provider}/callback", response_class=HTMLResponse)
async def oauth_callback(provider: str, request: Request) -> HTMLResponse:
    config = _provider(provider)
    _read_state(request.query_params.get("state", ""), provider)
    error = request.query_params.get("error")
    if error:
        description = request.query_params.get("error_description") or error
        return _callback_page(provider, error=description)

    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing OAuth code.")

    client_id, client_secret = _client_config(config)
    token_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(request, provider),
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(config.token_url, data=token_payload)
            response.raise_for_status()
            token = response.json()
    except httpx.HTTPStatusError as exc:
        return _callback_page(provider, error=exc.response.text)
    except httpx.HTTPError as exc:
        return _callback_page(provider, error=str(exc))

    access_token = token.get("access_token")
    if not access_token:
        return _callback_page(provider, error=f"No access_token returned: {token}")

    expires_in = token.get("expires_in")
    return _callback_page(provider, access_token=access_token, expires_in=expires_in)


def _callback_page(
    provider: str,
    *,
    access_token: str | None = None,
    expires_in: int | None = None,
    error: str | None = None,
) -> HTMLResponse:
    payload = {
        "provider": provider,
        "access_token": access_token,
        "expires_in": expires_in,
        "error": error,
    }
    payload_json = json.dumps(payload)
    safe_title = escape(provider.title())
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{safe_title} connected</title>
  </head>
  <body>
    <script>
      const payload = {payload_json};
      if (payload.access_token) {{
        localStorage.setItem(`shadowself_oauth_${{payload.provider}}`, payload.access_token);
        localStorage.setItem(`shadowself_oauth_${{payload.provider}}_connected_at`, String(Date.now()));
      }}
      if (payload.error) {{
        localStorage.setItem(`shadowself_oauth_${{payload.provider}}_error`, payload.error);
      }}
      window.location.replace("/");
    </script>
  </body>
</html>"""
    )

