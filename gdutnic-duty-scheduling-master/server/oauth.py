from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from server.mcp_client import origin_from_mcp_url, normalize_mcp_url
from server import db, admin_store

SCOPES = "read"


async def _wiki_request(method: str, url: str, **kwargs) -> httpx.Response:
    # Metadata and restored OAuth flows must not choose a different origin.
    configured = admin_store.mcp_url()
    target = urlparse(url)
    allowed = urlparse(origin_from_mcp_url(normalize_mcp_url(configured))) if configured else None
    if (allowed is None or target.scheme not in {"http", "https"}
            or target.username is not None or target.password is not None
            or (target.scheme, target.hostname, target.port or (443 if target.scheme == "https" else 80))
            != (allowed.scheme, allowed.hostname, allowed.port or (443 if allowed.scheme == "https" else 80))):
        raise ValueError("OAuth 目标地址不在管理员配置的范围内")
    last: Exception | None = None
    for trust_env in (True, False):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=15.0),
                follow_redirects=False,
                trust_env=trust_env,
            ) as client:
                return await client.request(method, url, **kwargs)
        except Exception as exc:
            print(f"[wiki] {method} {url} trust_env={trust_env}: {type(exc).__name__}: {exc}")
            last = exc
    raise RuntimeError("无法连接网协 Wiki，请检查网络、VPN 或系统代理") from last


async def start_oauth(mcp_url: str, redirect_uri: str) -> dict[str, str]:
    origin = origin_from_mcp_url(normalize_mcp_url(mcp_url))
    meta = await _auth_server_metadata(origin)
    client = await _get_or_register_client(origin, meta, redirect_uri)
    verifier = secrets.token_urlsafe(64)
    challenge = _s256(verifier)
    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": normalize_mcp_url(mcp_url),
    }
    authorize_url = meta["authorization_endpoint"] + "?" + urlencode(params)
    return {
        "authorize_url": authorize_url,
        "state": state,
        "code_verifier": verifier,
        "client_id": client["client_id"],
        "client_secret": client.get("client_secret") or "",
        "token_endpoint": meta["token_endpoint"],
        "mcp_url": normalize_mcp_url(mcp_url),
        "origin": origin,
    }


async def exchange_code(
    *,
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    mcp_url: str,
) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
        "resource": mcp_url,
    }
    headers = {"Accept": "application/json"}
    auth = None
    if client_secret:
        data["client_secret"] = client_secret
    response = await _wiki_request("POST", token_endpoint, data=data, headers=headers, auth=auth)
    if response.status_code >= 300:
        raise RuntimeError(f"OAuth token 交换失败: {response.text[:500]}")
    return response.json()


async def refresh_access_token(
    *,
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    mcp_url: str,
) -> dict[str, Any]:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "resource": mcp_url,
    }
    if client_secret:
        data["client_secret"] = client_secret
    response = await _wiki_request("POST", token_endpoint, data=data)
    if response.status_code >= 300:
        raise RuntimeError(f"刷新 token 失败: {response.text[:500]}")
    return response.json()


async def _auth_server_metadata(origin: str) -> dict[str, Any]:
    url = f"{origin}/.well-known/oauth-authorization-server"
    response = await _wiki_request("GET", url)
    response.raise_for_status()
    return response.json()


async def _get_or_register_client(origin: str, meta: dict[str, Any], redirect_uri: str) -> dict[str, Any]:
    with db.read() as conn:
        row = conn.execute(
            "SELECT client_id, client_secret FROM oauth_clients WHERE origin = ? AND redirect_uri = ?",
            (origin, redirect_uri),
        ).fetchone()
        if row:
            return {"client_id": row["client_id"], "client_secret": row["client_secret"] or ""}
    register_url = meta.get("registration_endpoint") or f"{origin}/oauth/register"
    payload = {
        "client_name": "GDUTNIC Outline 查询助手",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": SCOPES,
    }
    response = await _wiki_request("POST", register_url, json=payload)
    if response.status_code >= 300:
        raise RuntimeError(f"OAuth 客户端注册失败: {response.text[:500]}")
    created = response.json()
    rec = {
        "client_id": created["client_id"],
        "client_secret": created.get("client_secret") or "",
    }
    with db.write() as conn:
        db.upsert(conn, "oauth_clients", dict(origin=origin, redirect_uri=redirect_uri,
                                             client_id=rec["client_id"], client_secret=rec["client_secret"]),
                  ["origin", "redirect_uri"])
    return rec


def _s256(verifier: str) -> str:
    import base64
    import hashlib

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def host_allowed(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
