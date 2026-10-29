# -*- coding: utf-8 -*-
"""Kubernetes 访问层：共享 httpx.AsyncClient + ServiceAccount token 自动刷新。

- 复用连接池（同一 AsyncClient）。
- SA token 文件可能被 kubelet 轮换：按 mtime 缓存，变化时重新读取。
- 所有 CRUD 辅助函数在非 2xx 时抛出 HTTPException，错误体统一 {"message": ...}。
"""
from __future__ import annotations

import json
import os
import urllib.parse
import ssl
import time
from typing import Any, Optional

import httpx
from fastapi import HTTPException

K8S_HOST = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
K8S_PORT = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
K8S_BASE = f"https://{K8S_HOST}:{K8S_PORT}"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
# 本地开发/冒烟覆盖：显式指定 API 地址、token 与 CA（优先于集群内路径）
K8S_BASE = os.environ.get("K8S_API_URL", K8S_BASE)
if os.environ.get("K8S_TOKEN_FILE"):
    TOKEN_PATH = os.environ["K8S_TOKEN_FILE"]
if os.environ.get("K8S_CA_FILE"):
    CA_PATH = os.environ["K8S_CA_FILE"]
ROUTER = os.environ.get("FISSION_ROUTER", "http://router.fission.svc:80")
CONFIG_NS = os.environ.get("FISSION_CONSOLE_NS", "fission")

GROUP = "fission.io"
VERSION = "v1"
# 基础五类（始终可用）+ 扩展 CRD（需特性探测，集群未安装时端点返回 501）
BASE_KINDS = ("functions", "packages", "environments", "httptriggers", "timetriggers")
EXT_KINDS = ("messagequeuetriggers", "kuberneteswatchtriggers", "canaryconfigs",
             "functionversions", "functionalias")
KINDS = BASE_KINDS + EXT_KINDS

# ---------------------------------------------------------------- SSL 上下文
def _ssl_context() -> ssl.SSLContext:
    ca = CA_PATH if os.path.exists(CA_PATH) else None
    return ssl.create_default_context(cafile=ca)

_SSL_CTX = _ssl_context()

# ---------------------------------------------------------------- token 缓存（mtime）
_token_cache: dict[str, Any] = {"mtime": None, "value": ""}

def sa_token() -> str:
    """读取 SA token，带 mtime 缓存；本地开发（无 token 文件）返回空串。"""
    try:
        mtime = os.stat(TOKEN_PATH).st_mtime
    except OSError:
        return ""
    if _token_cache["mtime"] != mtime or not _token_cache["value"]:
        with open(TOKEN_PATH, encoding="utf-8") as fh:
            _token_cache["value"] = fh.read().strip()
        _token_cache["mtime"] = mtime
    return _token_cache["value"]

# ---------------------------------------------------------------- 共享 client
_client: Optional[httpx.AsyncClient] = None

def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            verify=_SSL_CTX,
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _client

async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None

# ---------------------------------------------------------------- 核心请求
async def request_k8s(
    method: str,
    path: str,
    body: Any = None,
    headers: Optional[dict] = None,
    timeout: float = 30.0,
    raw: bool = False,
):
    """发起 K8s API 请求。返回 (status, data)；raw=True 时 data 为 bytes。非 2xx 不抛异常。"""
    token = sa_token()
    hdrs = {"Accept": "application/json"}
    if token:
        hdrs["Authorization"] = "Bearer " + token
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    resp = await get_client().request(
        method, K8S_BASE + path, content=data, headers=hdrs, timeout=timeout
    )
    payload = resp.content
    if raw:
        return resp.status_code, payload
    if not payload:
        return resp.status_code, {}
    try:
        return resp.status_code, json.loads(payload)
    except Exception:
        return resp.status_code, {"message": payload.decode(errors="replace")}

def _raise(code: int, body: Any) -> None:
    if isinstance(body, dict) and "message" in body:
        msg = body["message"]
    else:
        msg = json.dumps(body, ensure_ascii=False)
    if code == 403:
        # RBAC 拒绝：向上透传并附加清晰说明
        msg = f"Kubernetes RBAC 拒绝: {msg}"
    raise HTTPException(status_code=code, detail={"message": msg})

async def request_or_raise(method: str, path: str, body=None, *, ok=(200,), **kw):
    code, data = await request_k8s(method, path, body=body, **kw)
    if code not in ok:
        _raise(code if code >= 400 else 502, data if isinstance(data, dict) else {"message": str(data)})
    return data

# ---------------------------------------------------------------- CRD CRUD
def check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(status_code=404, detail={"message": f"unknown kind {kind}"})
    return kind

async def crd_list(kind: str, label_selector: str = "", field_selector: str = "") -> list:
    qs = []
    if label_selector:
        qs.append("labelSelector=" + urllib.parse.quote(label_selector, safe=""))
    if field_selector:
        qs.append("fieldSelector=" + urllib.parse.quote(field_selector, safe=""))
    path = f"/apis/{GROUP}/{VERSION}/{kind}"
    if qs:
        path += "?" + "&".join(qs)
    body = await request_or_raise("GET", path)
    return body.get("items", [])

async def crd_get(kind: str, ns: str, name: str) -> dict:
    return await request_or_raise("GET", f"/apis/{GROUP}/{VERSION}/namespaces/{ns}/{kind}/{name}")

async def crd_create(kind: str, obj: dict) -> dict:
    ns = obj.get("metadata", {}).get("namespace", "fission")
    return await request_or_raise("POST", f"/apis/{GROUP}/{VERSION}/namespaces/{ns}/{kind}", obj, ok=(200, 201, 202))

async def crd_update(kind: str, ns: str, name: str, obj: dict) -> dict:
    return await request_or_raise("PUT", f"/apis/{GROUP}/{VERSION}/namespaces/{ns}/{kind}/{name}", obj)

async def crd_delete(kind: str, ns: str, name: str, propagation_policy: str = "") -> dict:
    path = f"/apis/{GROUP}/{VERSION}/namespaces/{ns}/{kind}/{name}"
    if propagation_policy:
        path += "?propagationPolicy=" + urllib.parse.quote(propagation_policy, safe="")
    return await request_or_raise("DELETE", path, ok=(200, 202))

# ---------------------------------------------------------------- CRD 特性探测
_feat_cache: dict[str, Any] = {"ts": 0.0, "available": set()}
FEAT_TTL = 60.0

async def feature_available(kind: str) -> bool:
    """探测 kind 是否出现在 /apis/fission.io/v1 的资源列表（带 60s 缓存）。"""
    import time as _time
    now = _time.time()
    if now - _feat_cache["ts"] > FEAT_TTL:
        code, body = await request_k8s("GET", f"/apis/{GROUP}/{VERSION}")
        names: set[str] = set()
        if code == 200:
            for r in body.get("resources", []):
                n = r.get("name", "")
                if n and "/" not in n:
                    names.add(n)
        _feat_cache["ts"] = now
        _feat_cache["available"] = names
    return kind in _feat_cache["available"]

async def ensure_feature(kind: str) -> None:
    """kind 不存在时抛 501，前端据此降级。"""
    if not await feature_available(kind):
        raise HTTPException(
            status_code=501,
            detail={"message": f"集群未启用 {kind} CRD", "feature": kind},
        )

# ---------------------------------------------------------------- core 资源（ConfigMap/Secret/Pod 等）
async def core_get(path: str) -> Any:
    code, data = await request_k8s("GET", path)
    return code, data

# ---------------------------------------------------------------- Pod 辅助
def _pod_ready(pod: dict) -> bool:
    statuses = pod.get("status", {}).get("containerStatuses") or [{"ready": True}]
    return all(c.get("ready") for c in statuses)

async def fn_pods_all() -> dict:
    """全集群按 functionName 聚合 pod 计数：{fn: {'ns':..,'pods':n,'ready':n}}"""
    code, body = await request_k8s("GET", "/api/v1/pods?labelSelector=functionName")
    out: dict[str, dict] = {}
    if code != 200:
        return out
    for p in body.get("items", []):
        md = p.get("metadata", {})
        fn = md.get("labels", {}).get("functionName")
        if not fn:
            continue
        e = out.setdefault(fn, {"ns": md.get("namespace", ""), "pods": 0, "ready": 0})
        e["pods"] += 1
        if _pod_ready(p):
            e["ready"] += 1
    return out

async def fn_pods_of(ns: str, name: str) -> list:
    code, body = await request_k8s("GET", f"/api/v1/namespaces/{ns}/pods?labelSelector=functionName%3D{name}")
    if code != 200:
        _raise(code, body)
    pods = []
    for p in body.get("items", []):
        md = p.get("metadata", {})
        pods.append({
            "name": md.get("name", ""),
            "phase": p.get("status", {}).get("phase", ""),
            "ready": _pod_ready(p),
            "created": md.get("creationTimestamp", ""),
        })
    return pods

async def fn_events_of(ns: str, name: str) -> list:
    """与 functionName=<name> 的 Pod 关联的 K8s Events。"""
    pods = await fn_pods_of(ns, name)
    names = {p["name"] for p in pods}
    code, body = await request_k8s("GET", f"/api/v1/namespaces/{ns}/events")
    if code != 200:
        _raise(code, body)
    events = []
    for ev in body.get("items", []):
        inv = ev.get("involvedObject", {})
        if inv.get("kind") == "Pod" and inv.get("name") in names:
            events.append({
                "pod": inv.get("name"),
                "type": ev.get("type", ""),
                "reason": ev.get("reason", ""),
                "message": ev.get("message", ""),
                "count": ev.get("count", 1),
                "lastTimestamp": ev.get("lastTimestamp") or ev.get("firstTimestamp") or "",
            })
    events.sort(key=lambda e: e["lastTimestamp"] or "", reverse=True)
    return events

def quantity_to_cpu_milli(v: str) -> int:
    v = v or "0"
    if v.endswith("n"):
        return int(float(v[:-1]) / 1e6)
    if v.endswith("u"):
        return int(float(v[:-1]) / 1e3)
    if v.endswith("m"):
        return int(float(v[:-1]))
    return int(float(v) * 1000)

def quantity_to_mem_mi(v: str) -> int:
    v = v or "0Ki"
    if v.endswith("Ki"):
        return int(float(v[:-2]) / 1024)
    if v.endswith("Mi"):
        return int(float(v[:-2]))
    if v.endswith("Gi"):
        return int(float(v[:-2]) * 1024)
    return 0
