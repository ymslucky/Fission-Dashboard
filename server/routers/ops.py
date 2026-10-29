# -*- coding: utf-8 -*-
"""杂项运维端点：Prometheus 只读代理、网格探测、审计查询。"""
from __future__ import annotations

import os
import re

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from .. import k8s
from ..services import audit as audit_svc

router = APIRouter()


@router.get("/whoami")
async def whoami(request: Request):
    """身份回显：来自 Authentik forward-auth 注入头。"""
    groups_raw = request.headers.get("x-authentik-groups", "")
    groups = [g.strip() for g in groups_raw.split(",") if g.strip()]
    return {
        "user": request.headers.get("x-authentik-identity")
            or request.headers.get("x-authentik-username") or "anonymous",
        "groups": groups,
        "admin": "fission-admins" in groups
            or os.environ.get("ADMIN_GROUPS", "fission-admins") in groups
            or os.environ.get("LOCAL_DEV") == "1",
    }

# Prometheus 白名单：fission 冷启动/时延/调用量指标 + up / process_* 系统指标
# 注意：fission_function_duration_seconds_bucket / fission_function_calls_total
# 为网格面板预期指标名，真实指标名待线上验证。
ALLOWED_METRICS = {
    "fission_function_cold_starts_total",
    "fission_function_cold_start_errors_total",
    "fission_function_duration_seconds_bucket",
    "fission_function_calls_total",
}
_PROM_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?(\[[^\]]*\])?$")


def _prom_allowed(query: str) -> bool:
    m = _PROM_RE.match(query.strip())
    if not m:
        return False
    metric = m.group(1)
    if metric in ALLOWED_METRICS:
        return True
    return metric == "up" or metric.startswith("process_")


@router.get("/prom/{query:path}")
async def prom_query(query: str):
    url = os.environ.get("PROMETHEUS_URL", "").rstrip("/")
    if not url:
        raise HTTPException(status_code=501,
                            detail={"message": "未配置 PROMETHEUS_URL 环境变量",
                                    "feature": "prometheus"})
    if not _prom_allowed(query):
        raise HTTPException(status_code=400,
                            detail={"message": f"查询不在白名单内，仅允许 fission_function_cold_start* / up / process_* 指标: {query}"})
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{url}/api/v1/query", params={"query": query})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail={"message": f"prometheus unreachable: {e}"})
    if resp.status_code != 200:
        raise HTTPException(status_code=502,
                            detail={"message": f"prometheus query failed: HTTP {resp.status_code}"})
    try:
        return resp.json().get("data", resp.json())
    except Exception:
        raise HTTPException(status_code=502, detail={"message": "prometheus returned invalid JSON"})


@router.get("/mesh/probe")
async def mesh_probe():
    """检查 istio-system / linkerd 命名空间是否存在；403/404 等按 unknown。"""
    async def probe(ns: str):
        code, _ = await k8s.request_k8s("GET", f"/api/v1/namespaces/{ns}")
        if code == 200:
            return True
        if code == 404:
            return False
        return None  # 无权限等 -> unknown
    istio = await probe("istio-system")
    linkerd = await probe("linkerd")
    return {"istio": istio, "linkerd": linkerd}


@router.get("/audit")
async def get_audit(limit: int = Query(100, ge=1, le=500)):
    records = await audit_svc.read_records()
    return {"items": records[-limit:]}
