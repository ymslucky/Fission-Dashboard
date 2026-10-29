# -*- coding: utf-8 -*-
"""函数扩展端点：describe 聚合、经 Router 直调的 test 归因。"""
from __future__ import annotations

import json
import time
import urllib.parse
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import k8s

router = APIRouter()

MCP_ANN_ENABLED = "fission.io/mcp-enabled"
MCP_ANN_DESC = "fission.io/mcp-tool-description"
MCP_ANN_SCHEMA = "fission.io/mcp-input-schema"


# ---------------------------------------------------------------- describe
@router.get("/functions/{ns}/{name}/describe")
async def describe_function(ns: str, name: str):
    """聚合复刻 fission function describe：spec / package 构建 / warm pods /
    invocable / triggers / canary / MCP 暴露。"""
    fn = await k8s.crd_get("functions", ns, name)
    spec = fn.get("spec") or {}
    annotations = (fn.get("metadata") or {}).get("annotations") or {}

    # package 与构建状态
    pkg_ref = (spec.get("package") or {}).get("packageref") or ""
    package = None
    build_status, buildlog = "unknown", ""
    if pkg_ref:
        code, pkg = await k8s.request_k8s(
            "GET", f"/apis/{k8s.GROUP}/{k8s.VERSION}/namespaces/{ns}/packages/{pkg_ref}")
        if code == 200:
            package = pkg
            bs = (pkg.get("status") or {}).get("buildstatus") or {}
            build_status = bs.get("status", "unknown")
            buildlog = bs.get("buildlog", "")

    # invocable 按 executorType 推导
    executor = ((spec.get("InvokeStrategy") or {}).get("ExecutionStrategy") or {}).get("ExecutorType", "poolmgr")

    # warm pods：poolmgr 函数跑在环境池 Deployment（poolmgr-{env}-{hash}）里，
    # Pod 无 functionName 标签——按环境池的就绪副本统计；newdeploy 维持按函数 Pod 统计。
    if executor.lower() == "newdeploy":
        pods = await k8s.fn_pods_of(ns, name)
        warm_pods = len(pods)
        ready_pods = sum(1 for p in pods if p["ready"])
    else:
        warm_pods = ready_pods = await _poolmgr_ready(ns, spec)
    if executor.lower() == "newdeploy":
        invocable = ready_pods > 0
        invocable_note = "Yes" if invocable else "No (等特化)"
    else:  # poolmgr
        invocable = True
        invocable_note = "Yes (cold start on first call)"

    # 关联 triggers（http / time / mq）
    triggers = []
    for kind in ("httptriggers", "timetriggers", "messagequeuetriggers"):
        try:
            items = await k8s.crd_list(kind)
        except HTTPException:
            continue
        for t in items:
            md = t.get("metadata") or {}
            tspec = t.get("spec") or {}
            # v1.27: HTTPTrigger.spec.functionref 是对象；两种形态：
            #   单函数: {"type":"name","name":<fn>}
            #   加权/金丝雀: {"type":"function-weights","name":"","functionweights":{<fn>:weight}}
            # （functionweights 嵌在 functionref 内部，不在 spec 顶层）
            ref = tspec.get("functionref")
            ref_name = ref.get("name") if isinstance(ref, dict) else ref
            weights = (ref or {}).get("functionweights") if isinstance(ref, dict) else None
            in_weights = isinstance(weights, dict) and name in weights
            if (ref_name == name and ref_name) or in_weights or md.get("labels", {}).get("functionName") == name:
                triggers.append({"kind": kind, "ns": md.get("namespace", ""),
                                 "name": md.get("name", ""),
                                 "url": tspec.get("relativeUrl") or tspec.get("prefix") or ""})

    # canary（被 newfunction/oldfunction 引用；CRD 缺失时忽略）
    canary = []
    code, body = await k8s.request_k8s(
        "GET", f"/apis/{k8s.GROUP}/{k8s.VERSION}/namespaces/{ns}/canaryconfigs")
    if code == 200:
        for c in body.get("items", []):
            cspec = c.get("spec") or {}
            if name in (cspec.get("newfunction"), cspec.get("oldfunction")):
                canary.append({"name": (c.get("metadata") or {}).get("name", ""),
                               "stage": (c.get("status") or {}).get("status", "")})

    # MCP 暴露（注解字段；若集群 CRD 有专用 spec 字段则优先级更高，见 routers/mcp.py 注释）
    mcp = {"enabled": str(annotations.get(MCP_ANN_ENABLED, "")).lower() == "true",
           "toolDescription": annotations.get(MCP_ANN_DESC, ""),
           "inputSchema": _try_json(annotations.get(MCP_ANN_SCHEMA, ""))}

    return {
        "function": fn,
        "package": {"name": pkg_ref, "buildStatus": build_status,
                    "buildlog": buildlog, "object": package},
        "pods": {"warm": warm_pods, "ready": ready_pods},
        "executorType": executor,
        "invocable": invocable,
        "invocableNote": invocable_note,
        "triggers": triggers,
        "canary": canary,
        "mcpToolExposed": mcp,
    }


async def _poolmgr_ready(ns: str, spec: dict) -> int:
    """统计 poolmgr-{env}-* 池 Deployment 的就绪副本数（按 environmentName 标签过滤）。"""
    env_name = (spec.get("environment") or {}).get("name") or ""
    if not env_name:
        return 0
    sel = urllib.parse.quote(f"environmentName={env_name}", safe="")
    code, body = await k8s.request_k8s(
        "GET", f"/apis/apps/v1/namespaces/{ns}/deployments?labelSelector={sel}")
    if code != 200:
        return 0
    prefix = f"poolmgr-{env_name}-"
    total = 0
    for dep in body.get("items", []):
        dname = (dep.get("metadata") or {}).get("name", "")
        if not dname.startswith(prefix):
            continue
        total += (dep.get("status") or {}).get("readyReplicas") or 0
    return total


def _try_json(s: str) -> Any:
    try:
        return json.loads(s)
    except Exception:
        return {}


# ---------------------------------------------------------------- test（经 Router 直调函数）
class TestRequest(BaseModel):
    method: Optional[str] = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None
    query: dict[str, str] = Field(default_factory=dict)
    timeoutSeconds: float = 60.0


def _attribute(status, timeout, timeout_s):
    """从 HTTP 状态/超时异常推导 component 与 reason。"""
    if timeout:
        return "router", f"fission function timeout ({int(timeout_s)}s，Fission 默认 60s)"
    if status is None:
        return "router", "no response"
    if 200 <= status < 300:
        return "function", "ok"
    if status == 404:
        return "router", "function or route not found"
    if status == 503:
        return "router", "newdeploy 特化失败或无就绪 Pod（router 503）"
    if status >= 500:
        return "function", f"function error HTTP {status}"
    return "router", f"HTTP {status}"


async def _internal_auth_headers(method: str, request_uri: str, body: bytes) -> dict:
    """按 Fission v1.27 内部认证方案签名（pkg/auth/hmac）。

    - master secret = Router 的 FISSION_INTERNAL_AUTH_SECRET：优先本 Pod 环境变量，
      否则从 router Pod（application=fission-router）的 env 读取（BFF 有 pods get 权限）。
      为空 = 集群未启用内部认证（Router 侧为 pass-through），返回空 headers。
    - key = HKDF-SHA256(master, salt=nil, info="fission-internal-v1:router-internal", L=32)
    - canonical = METHOD\nREQUEST-URI\nsha256hex(body)\nunix_minute
    """
    import hashlib, hmac as _hmac, os, time as _time

    master = os.environ.get("FISSION_INTERNAL_AUTH_SECRET", "").encode()
    if not master:
        sel = urllib.parse.quote("application=fission-router", safe="")
        code, pod_body = await k8s.request_k8s(
            "GET", f"/api/v1/namespaces/fission/pods?labelSelector={sel}")
        if code == 200:
            for pod in pod_body.get("items", []):
                for ctn in (pod.get("spec") or {}).get("containers", []):
                    for ev in ctn.get("env", []):
                        if ev.get("name") != "FISSION_INTERNAL_AUTH_SECRET":
                            continue
                        v = ev.get("value")
                        if v:
                            master = v.encode()
                            break
                        ref = (ev.get("valueFrom") or {}).get("secretKeyRef") or {}
                        if ref:
                            s_code, sec = await k8s.request_k8s(
                                "GET", f"/api/v1/namespaces/fission/secrets/{ref.get('name')}")
                            if s_code == 200:
                                import base64
                                raw = (sec.get("data") or {}).get(ref.get("key", "")) or ""
                                master = base64.b64decode(raw) if raw else b""
                    if master:
                        break
                if master:
                    break
    if not master:
        return {}

    ts = int(_time.time())
    body_hash = hashlib.sha256(body).hexdigest()
    minute = ts - (ts % 60)
    canonical = f"{method}\n{request_uri}\n{body_hash}\n{minute}"
    info = b"fission-internal-v1:router-internal"
    # RFC 5869：salt 缺省 = HashLen 个全零字节（Go hkdf.Key 传 nil salt 同义）
    prk = _hmac.new(b"\x00" * 32, master, hashlib.sha256).digest()
    key = _hmac.new(prk, info + bytes([1]), hashlib.sha256).digest()[:32]
    sig = _hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()
    return {"X-Fission-Auth-Timestamp": str(ts), "X-Fission-Auth-Signature": sig}


def _router_internal_base() -> str:
    """Router 内部监听器地址；v1.27 为独立 Service router-internal:8889（8889 未在 svc/router 上暴露）。"""
    import os
    return os.environ.get("FISSION_ROUTER_INTERNAL", "http://router-internal.fission.svc:8889").rstrip("/")


@router.post("/functions/{ns}/{name}/test")
async def test_function(ns: str, name: str, req: TestRequest):
    """经 Router 内部监听器（8889）/fission-function/{ns}/{name}/ 调用函数，返回归因卡片。"""
    internal_base = _router_internal_base()
    path = f"/fission-function/{ns}/{name}/"
    target = internal_base + path
    if req.query:
        qs = urllib.parse.urlencode(req.query)
        target += "?" + qs
        path += "?" + qs
    method = (req.method or "GET").upper()
    data = None
    if isinstance(req.body, str):
        data = req.body.encode()
    elif req.body is not None:
        data = json.dumps(req.body).encode()
    headers = dict(req.headers)
    if data and "content-type" not in {k.lower() for k in headers}:
        headers["Content-Type"] = "text/plain"
    try:
        headers.update(await _internal_auth_headers(method, path, data or b""))
    except Exception:
        headers = dict(req.headers)

    client = httpx.AsyncClient(timeout=req.timeoutSeconds)
    start = time.time()
    try:
        resp = await client.request(method, target, content=data, headers=headers)
        latency = int((time.time() - start) * 1000)
        component, reason = _attribute(resp.status_code, False, req.timeoutSeconds)
        return {
            "status": resp.status_code,
            "latencyMs": latency,
            "requestId": resp.headers.get("X-Fission-Request-ID", ""),
            "component": component,
            "reason": reason,
            "body": resp.text[:20000],
            "headers": dict(resp.headers),
        }
    except httpx.TimeoutException:
        latency = int((time.time() - start) * 1000)
        component, reason = _attribute(None, True, req.timeoutSeconds)
        return {"status": None, "latencyMs": latency, "requestId": "",
                "component": component, "reason": reason, "body": "", "headers": {}}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail={"message": f"router unreachable: {e}"})
    finally:
        await client.aclose()
