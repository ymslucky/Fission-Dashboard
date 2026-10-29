# -*- coding: utf-8 -*-
"""MCP JWT（HS256，自实现，无第三方依赖）。

签名密钥保存在 fission namespace 的 Secret fission-console-mcp-keys：
- key "hs256-key"：hex 随机密钥，首次签发自动生成
- key "token-<name>"：签发的 JWT 存档（删除 token = 移除该 key）

claim：sub=name, allowed_namespaces=[...], iat, exp, iss=fission-console
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

from fastapi import HTTPException

from .. import k8s

MCP_SECRET = "fission-console-mcp-keys"
KEY_FIELD = "hs256-key"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


async def _secret_body() -> dict:
    code, body = await k8s.request_k8s(
        "GET", f"/api/v1/namespaces/{k8s.CONFIG_NS}/secrets/{MCP_SECRET}"
    )
    if code != 200:
        return {}
    return body


def _sign(payload: dict, key: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64url(hmac.new(key.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


def verify(token: str, key: str) -> Optional[dict]:
    """校验签名与有效期，返回 claim；失败返回 None。"""
    try:
        header, body, sig = token.split(".")
        expect = _b64url(hmac.new(key.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            return None
        pad = "=" * (-len(body) % 4)
        claim = json.loads(base64.urlsafe_b64decode(body + pad))
        if claim.get("exp", 0) < time.time():
            return None
        return claim
    except Exception:
        return None


async def _get_or_create_key() -> tuple[str, dict, bool]:
    body = await _secret_body()
    data: dict = {}
    rv: Optional[str] = None
    if body:
        data = {
            k: base64.b64encode(v.encode()).decode()
            for k, v in (body.get("data") or {}).items()
        }
        rv = body.get("metadata", {}).get("resourceVersion")
    key_b64 = data.get(KEY_FIELD)
    created = False
    if key_b64:
        key = base64.b64decode(key_b64).decode()
    else:
        key = secrets.token_hex(32)
        data[KEY_FIELD] = base64.b64encode(key.encode()).decode()
        created = True
    sec = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
           "metadata": {"name": MCP_SECRET, "namespace": k8s.CONFIG_NS,
                        "labels": {"app": "fission-console"}},
           "data": data}
    if rv:
        sec["metadata"]["resourceVersion"] = rv
        code, _ = await k8s.request_k8s(
            "PUT", f"/api/v1/namespaces/{k8s.CONFIG_NS}/secrets/{MCP_SECRET}", sec)
    else:
        code, _ = await k8s.request_k8s(
            "POST", f"/api/v1/namespaces/{k8s.CONFIG_NS}/secrets", sec)
    if code not in (200, 201):
        raise HTTPException(status_code=502, detail={"message": "failed to persist mcp key secret"})
    return key, data, created


async def issue_token(name: str, allowed_namespaces: list[str], ttl_days: int) -> dict:
    key, data, _ = await _get_or_create_key()
    now = int(time.time())
    exp = now + int(ttl_days) * 86400
    claim = {"sub": name, "allowed_namespaces": allowed_namespaces,
             "iat": now, "exp": exp, "iss": "fission-console"}
    token = _sign(claim, key)
    data[f"token-{name}"] = base64.b64encode(token.encode()).decode()
    await _write_secret(data)
    return {"token": token, "name": name, "expiresAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp))}


async def _write_secret(data: dict) -> None:
    body = await _secret_body()
    sec = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
           "metadata": {"name": MCP_SECRET, "namespace": k8s.CONFIG_NS,
                        "labels": {"app": "fission-console"}},
           "data": data}
    if body:
        sec["metadata"]["resourceVersion"] = body["metadata"]["resourceVersion"]
        code, _ = await k8s.request_k8s(
            "PUT", f"/api/v1/namespaces/{k8s.CONFIG_NS}/secrets/{MCP_SECRET}", sec)
    else:
        code, _ = await k8s.request_k8s(
            "POST", f"/api/v1/namespaces/{k8s.CONFIG_NS}/secrets", sec)
    if code not in (200, 201):
        raise HTTPException(status_code=502, detail={"message": "failed to write mcp secret"})


async def revoke_token(name: str) -> None:
    body = await _secret_body()
    field = f"token-{name}"
    if not body or field not in (body.get("data") or {}):
        raise HTTPException(status_code=404, detail={"message": f"MCP token {name} 不存在"})
    data = {
        k: base64.b64encode(v.encode()).decode()
        for k, v in body["data"].items() if k != field
    }
    await _write_secret(data)


async def stored_tokens() -> list[str]:
    body = await _secret_body()
    if not body:
        return []
    return [k[len("token-"):] for k in body.get("data", {}) if k.startswith("token-")]


async def list_tokens() -> list[dict]:
    """列出全部 token 的元数据（不解密签名，仅解析 claim）。"""
    body = await _secret_body()
    key = (body.get("data") or {}).get(KEY_FIELD)
    if not key:
        return []
    import base64 as _b64
    key_bytes = _b64.b64decode(key).decode() if key else ""
    out = []
    for field, value in sorted((body.get("data") or {}).items()):
        if not field.startswith("token-"):
            continue
        name = field[len("token-"):]
        token = _b64.b64decode(value).decode(errors="replace")
        # 无需验签：读自己的存档 claim
        try:
            _, b64body, _ = token.split(".")
            claim = json.loads(base64.urlsafe_b64decode(b64body + "=" * (-len(b64body) % 4)))
        except Exception:
            claim = {}
        import time as _time
        out.append({
            "name": name,
            "allowedNamespaces": claim.get("allowed_namespaces", []),
            "issuedAt": claim.get("iat"),
            "expiresAt": claim.get("exp"),
            "expired": bool(claim.get("exp", 0) and claim["exp"] < _time.time()),
        })
    return out
