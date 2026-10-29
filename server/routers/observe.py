# -*- coding: utf-8 -*-
"""观测类端点：overview、metrics、pods、events、logs（含 SSE 跟随）。"""
from __future__ import annotations

import asyncio
import json
import urllib.parse

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import k8s

router = APIRouter()

@router.get("/overview")
async def overview():
    """五类资源计数 + fission 命名空间核心组件 Pod 就绪汇总。"""
    counts: dict[str, int] = {}
    for kind in k8s.KINDS:
        try:
            items = await k8s.crd_list(kind)
            counts[kind] = len(items)
        except HTTPException:
            counts[kind] = -1
    code, body = await k8s.request_k8s("GET", "/api/v1/namespaces/fission/pods")
    pods = body.get("items", []) if code == 200 else []
    by_app: dict[str, dict] = {}
    for p in pods:
        app = p.get("metadata", {}).get("labels", {}).get("app") or "other"
        e = by_app.setdefault(app, {"app": app, "pods": 0, "ready": 0})
        e["pods"] += 1
        if all(c.get("ready") for c in p.get("status", {}).get("containerStatuses", [{"ready": True}])):
            e["ready"] += 1
    return {"counts": counts, "core": sorted(by_app.values(), key=lambda x: x["app"])}

@router.get("/metrics")
async def metrics():
    """metrics.k8s.io 按 functionName 聚合 CPU(毫核)/内存(Mi)。"""
    pods = await k8s.fn_pods_all()
    code, body = await k8s.request_k8s("GET", "/apis/metrics.k8s.io/v1beta1/pods", timeout=15)
    fns: dict[str, dict] = {
        fn: {"ns": e["ns"], "pods": e["pods"], "ready": e["ready"], "cpu_m": 0, "mem_mi": 0}
        for fn, e in pods.items()
    }
    if code == 200:
        for p in body.get("items", []):
            fn = p.get("metadata", {}).get("labels", {}).get("functionName")
            if not fn:
                continue
            e = fns.setdefault(fn, {"ns": p.get("metadata", {}).get("namespace", ""),
                                    "pods": 0, "ready": 0, "cpu_m": 0, "mem_mi": 0})
            for c in p.get("containers", []):
                u = c.get("usage", {})
                e["cpu_m"] += k8s.quantity_to_cpu_milli(u.get("cpu", "0"))
                e["mem_mi"] += k8s.quantity_to_mem_mi(u.get("memory", "0Ki"))
    return {"functions": fns, "ts": int(asyncio.get_event_loop().time())}

@router.get("/functions/{ns}/{name}/pods")
async def function_pods(ns: str, name: str):
    return {"items": await k8s.fn_pods_of(ns, name)}

@router.get("/functions/{ns}/{name}/events")
async def function_events(ns: str, name: str):
    return {"items": await k8s.fn_events_of(ns, name)}

async def _pick_log_container(ns: str, pod: str) -> str:
    """多容器 Pod 必须显式指定 container，否则 API 返回 400。

    Fission 函数日志在环境容器里（newdeploy 为 <env> 容器），排除 fetcher 侧车。
    """
    code, pod_obj = await k8s.request_k8s("GET", f"/api/v1/namespaces/{ns}/pods/{pod}")
    if code != 200:
        return ""
    names = [c.get("name", "") for c in (pod_obj.get("spec") or {}).get("containers", [])]
    if len(names) <= 1:
        return names[0] if names else ""
    fn_containers = [n for n in names if "fetcher" not in n.lower()]
    return fn_containers[0] if fn_containers else names[0]


async def _stream_pod_logs(ns: str, pod: str, container: str = ""):
    """SSE：转发 K8s pod log follow=true 流。"""
    qs = {"follow": "true", "tailLines": "300", "timestamps": "false"}
    container = container or await _pick_log_container(ns, pod)
    if container:
        qs["container"] = container
    path = f"/api/v1/namespaces/{ns}/pods/{pod}/log?" + urllib.parse.urlencode(qs)
    token = k8s.sa_token()
    # 不要设置 Accept: text/plain —— apiserver 对 /log 子资源做内容协商，会回 406
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token

    async def gen():
        client = k8s.get_client()
        async with client.stream("GET", k8s.K8S_BASE + path, headers=headers, timeout=None) as resp:
            if resp.status_code != 200:
                msg = f"[failed: HTTP {resp.status_code}]"
                yield f"data: {json.dumps({'error': msg})}\n\n"
                return
            async for chunk in resp.aiter_text():
                for line in chunk.splitlines():
                    if line:
                        yield f"data: {json.dumps({'pod': pod, 'line': line})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")

@router.get("/functions/{ns}/{name}/logs")
async def function_logs(ns: str, name: str, pod: str = "", follow: bool = False, tailLines: int = 300):
    """follow=false：一次性聚合 tailLines 行；follow=true：SSE 流式跟随。"""
    if follow:
        if not pod:
            pods = await k8s.fn_pods_of(ns, name)
            if not pods:
                raise HTTPException(status_code=404, detail={"message": "no pods found"})
            pod = pods[0]["name"]
        return await _stream_pod_logs(ns, pod)
    pods = await k8s.fn_pods_of(ns, name)
    targets = [p["name"] for p in pods] if not pod else [pod]
    logs: dict[str, str] = {}
    for pn in targets:
        container = await _pick_log_container(ns, pn)
        cparam = f"&container={urllib.parse.quote(container)}" if container else ""
        code, log = await k8s.request_k8s(
            "GET",
            f"/api/v1/namespaces/{ns}/pods/{pn}/log?tailLines={tailLines}&timestamps=false{cparam}",
            raw=True,
        )
        logs[pn] = log.decode(errors="replace") if code == 200 else f"[failed: HTTP {code}]"
    return {"pods": pods, "logs": logs}
