# -*- coding: utf-8 -*-
"""ConfigMap/Secret 存储辅助（从 fn/ext3.py、ext6.py 移植为异步版本）。

testdata、gitops sources、sync state、github token 都落在 fission 命名空间的
ConfigMap / Secret 中。
"""
from __future__ import annotations

import base64
import json
from typing import Any

from fastapi import HTTPException

from .. import k8s

CONFIG_NS = "fission"
CONFIG_CM = "fission-console-config"
TESTDATA_CM = "fission-console-testdata"
STATE_CM = "fission-console-sync-state"
TOKEN_SECRET = "fission-console-github"


async def cm_get(name: str, key: str, default: Any) -> Any:
    code, body = await k8s.request_k8s("GET", f"/api/v1/namespaces/{CONFIG_NS}/configmaps/{name}")
    if code != 200:
        return default
    try:
        return json.loads(body.get("data", {}).get(key, json.dumps(default)))
    except Exception:
        return default


async def cm_put(name: str, key: str, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    cm = {"apiVersion": "v1", "kind": "ConfigMap",
          "metadata": {"name": name, "namespace": CONFIG_NS, "labels": {"app": "fission-console"}},
          "data": {key: payload}}
    code, body = await k8s.request_k8s("GET", f"/api/v1/namespaces/{CONFIG_NS}/configmaps/{name}")
    if code == 200:
        cm["metadata"]["resourceVersion"] = body["metadata"]["resourceVersion"]
        merged = dict(body.get("data", {}))
        merged.update(cm["data"])
        cm["data"] = merged
        code, body = await k8s.request_k8s("PUT", f"/api/v1/namespaces/{CONFIG_NS}/configmaps/{name}", cm)
    else:
        code, body = await k8s.request_k8s("POST", f"/api/v1/namespaces/{CONFIG_NS}/configmaps", cm)
    if code not in (200, 201):
        raise HTTPException(status_code=code if code >= 400 else 502, detail={"message": "failed to save configmap"})


async def load_sources() -> dict:
    return await cm_get(CONFIG_CM, "sources.json", {"sources": []})


async def save_sources(cfg: dict) -> None:
    await cm_put(CONFIG_CM, "sources.json", cfg)


async def load_testdata() -> dict:
    return {"collections": await cm_get(TESTDATA_CM, "collections.json", []),
            "environments": await cm_get(TESTDATA_CM, "environments.json", [])}


async def save_testdata(data: dict) -> None:
    await cm_put(TESTDATA_CM, "collections.json", data.get("collections", []))
    await cm_put(TESTDATA_CM, "environments.json", data.get("environments", []))


async def load_state() -> dict:
    return await cm_get(STATE_CM, "state.json", {})


async def save_state(state: dict) -> None:
    await cm_put(STATE_CM, "state.json", state)


async def token_for(src: dict) -> str:
    name = src.get("tokenSecretName") or TOKEN_SECRET
    key = src.get("tokenSecretKey") or src.get("name")
    code, body = await k8s.request_k8s("GET", f"/api/v1/namespaces/{CONFIG_NS}/secrets/{name}")
    if code != 200:
        return ""
    try:
        return base64.b64decode(body["data"].get(key, "")).decode()
    except Exception:
        return ""


async def token_save(source_name: str, token: str) -> None:
    code, body = await k8s.request_k8s("GET", f"/api/v1/namespaces/{CONFIG_NS}/secrets/{TOKEN_SECRET}")
    sec = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
           "metadata": {"name": TOKEN_SECRET, "namespace": CONFIG_NS, "labels": {"app": "fission-console"}},
           "data": {}}
    if code == 200:
        sec["data"] = body.get("data", {})
        sec["metadata"]["resourceVersion"] = body["metadata"]["resourceVersion"]
    sec["data"][source_name] = base64.b64encode(token.encode()).decode()
    if code == 200:
        c, b = await k8s.request_k8s("PUT", f"/api/v1/namespaces/{CONFIG_NS}/secrets/{TOKEN_SECRET}", sec)
    else:
        c, b = await k8s.request_k8s("POST", f"/api/v1/namespaces/{CONFIG_NS}/secrets", sec)
    if c not in (200, 201):
        raise HTTPException(status_code=c if c >= 400 else 502, detail={"message": "failed to save token"})
