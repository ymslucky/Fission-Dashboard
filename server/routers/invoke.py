# -*- coding: utf-8 -*-
"""函数调用：经由 Fission Router，任意 method/headers/body。"""
from __future__ import annotations

import json
import time
import urllib.parse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Optional

from .. import k8s

router = APIRouter()

class InvokeRequest(BaseModel):
    """触发器定位 + 请求描述。body 为字符串原样发送，其他类型 JSON 序列化。"""
    ns: str = "fission"
    trigger: str
    method: Optional[str] = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None
    query: dict[str, str] = Field(default_factory=dict)

@router.post("/invoke")
async def invoke(req: InvokeRequest):
    # 查 httptrigger 拿相对 URL 与默认 method
    code, tr = await k8s.request_k8s(
        "GET", f"/apis/{k8s.GROUP}/{k8s.VERSION}/namespaces/{req.ns}/httptriggers/{req.trigger}"
    )
    if code != 200:
        raise HTTPException(status_code=code if code >= 400 else 502, detail={"message": "trigger not readable"})
    spec = tr.get("spec", {})
    url = spec.get("relativeUrl") or spec.get("relativeurl") or spec.get("prefix") or "/"
    target = k8s.ROUTER.rstrip("/") + (url if url.startswith("/") else "/" + url)
    if req.query:
        target += ("&" if "?" in target else "?") + urllib.parse.urlencode(req.query)
    method = (req.method or (spec.get("methods") or ["GET"])[0]).upper()
    data = None
    if isinstance(req.body, str):
        data = req.body.encode()
    elif req.body is not None:
        data = json.dumps(req.body).encode()
    headers = dict(req.headers)
    if data and "content-type" not in {k.lower() for k in headers}:
        headers["Content-Type"] = "text/plain"

    client = httpx.AsyncClient(timeout=60.0)
    start = time.time()
    try:
        resp = await client.request(method, target, content=data, headers=headers)
        latency = int((time.time() - start) * 1000)
        text = resp.text
        return {
            "status": resp.status_code,
            "latency_ms": latency,
            "headers": dict(resp.headers),
            "body": text[:20000],
        }
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail={"message": f"router unreachable: {e}"})
    finally:
        await client.aclose()
