# -*- coding: utf-8 -*-
"""审计：写操作成功后追加记录到 ConfigMap fission-console-audit。

单 namespace（fission），data["audit.json"] 为 JSON 行数组；
超过 MAX_RECORDS(500) 条截断最旧。读取/写入失败不影响主流程（尽力而为）。
"""
from __future__ import annotations

import json
import time
from typing import Optional

from .. import k8s

AUDIT_CM = "fission-console-audit"
AUDIT_KEY = "audit.json"
MAX_RECORDS = 500


async def read_records() -> list:
    code, body = await k8s.request_k8s(
        "GET", f"/api/v1/namespaces/{k8s.CONFIG_NS}/configmaps/{AUDIT_CM}"
    )
    if code != 200:
        return []
    raw = (body.get("data") or {}).get(AUDIT_KEY, "[]")
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def append_record(rec: dict) -> None:
    records = await read_records()
    records.append(rec)
    records = records[-MAX_RECORDS:]
    payload = json.dumps(records, ensure_ascii=False)
    cm = {"apiVersion": "v1", "kind": "ConfigMap",
          "metadata": {"name": AUDIT_CM, "namespace": k8s.CONFIG_NS,
                       "labels": {"app": "fission-console"}},
          "data": {AUDIT_KEY: payload}}
    code, body = await k8s.request_k8s(
        "GET", f"/api/v1/namespaces/{k8s.CONFIG_NS}/configmaps/{AUDIT_CM}"
    )
    if code == 200:
        rv = (body.get("metadata") or {}).get("resourceVersion")
        if rv:
            cm["metadata"]["resourceVersion"] = rv
        code, _ = await k8s.request_k8s(
            "PUT", f"/api/v1/namespaces/{k8s.CONFIG_NS}/configmaps/{AUDIT_CM}", cm
        )
    else:
        code, _ = await k8s.request_k8s(
            "POST", f"/api/v1/namespaces/{k8s.CONFIG_NS}/configmaps", cm
        )
    if code not in (200, 201):
        raise RuntimeError(f"audit write failed: HTTP {code}")


def parse_target(path: str) -> dict:
    """从请求路径启发式解析 kind/ns/name/summary。"""
    parts = [p for p in path.strip("/").split("/") if p]
    # parts[0] == "api"
    info: dict = {"kind": "", "ns": "", "name": "", "action": parts[-1] if parts else ""}
    rest = parts[1:]
    if not rest:
        return info
    if rest[0] == "resources" and len(rest) >= 2:
        info["kind"] = rest[1]
        if len(rest) >= 4:
            info["ns"], info["name"] = rest[2], rest[3]
        info["action"] = "resource"
    elif rest[0] == "functions" and len(rest) >= 3:
        info["kind"] = "functions"
        info["ns"], info["name"] = rest[1], rest[2]
        info["action"] = rest[3] if len(rest) > 3 else "function"
    elif rest[0] in ("httptriggers",) and len(rest) >= 3:
        info["kind"] = "httptriggers"
        info["ns"], info["name"] = rest[1], rest[2]
        info["action"] = rest[3] if len(rest) > 3 else "httptrigger"
    elif rest[0] == "canaryconfigs":
        info["kind"] = "canaryconfigs"
        if len(rest) >= 3:
            info["ns"], info["name"] = rest[1], rest[2]
        info["action"] = "canary"
    elif rest[0] == "mcp":
        info["kind"] = "mcp"
        info["name"] = rest[2] if len(rest) > 2 and rest[1] == "tokens" else ""
        info["action"] = "tokens" if rest[1:2] == ["tokens"] else rest[1] if len(rest) > 1 else "mcp"
    elif rest[0] in ("testdata", "config", "gitops"):
        info["kind"] = rest[0]
        info["action"] = rest[0]
    return info


async def record_request(request, status_code: int) -> Optional[dict]:
    """由 main.py 中间件调用：仅 2xx 写请求记录。失败抛出由调用方吞掉。"""
    info = parse_target(request.url.path)
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user": "anonymous",  # 回填由调用方传入
        "action": f"{request.method} {request.url.path}",
        "kind": info["kind"],
        "ns": info["ns"],
        "name": info["name"],
        "summary": f"{request.method} {info['kind'] or 'resource'} "
                   f"{info['ns'] + '/' if info['ns'] else ''}{info['name']} -> {status_code}".strip(),
    }
    from . import policy
    rec["user"] = policy.current_user(request)
    await append_record(rec)
    return rec
