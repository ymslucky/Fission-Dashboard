# -*- coding: utf-8 -*-
"""K8s watch → SSE：/api/watch/{kind} 推送 ADDED/MODIFIED/DELETED 精简事件。"""
from __future__ import annotations

import asyncio
import json
import urllib.parse

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from .. import k8s

router = APIRouter()

@router.get("/watch/{kind}")
async def watch_kind(
    kind: str,
    resourceVersion: str = Query("", alias="resourceVersion"),
    labelSelector: str = Query("", alias="labelSelector"),
    fieldSelector: str = Query("", alias="fieldSelector"),
):
    k8s.check_kind(kind)
    qs = {"watch": "true", "allowWatchBookmarks": "false", "timeoutSeconds": "300"}
    if resourceVersion:
        qs["resourceVersion"] = resourceVersion
    if labelSelector:
        qs["labelSelector"] = labelSelector
    if fieldSelector:
        qs["fieldSelector"] = fieldSelector
    path = f"/apis/{k8s.GROUP}/{k8s.VERSION}/{kind}?" + urllib.parse.urlencode(qs)
    token = k8s.sa_token()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token

    async def gen():
        client = k8s.get_client()
        while True:
            try:
                async with client.stream("GET", k8s.K8S_BASE + path, headers=headers, timeout=None) as resp:
                    if resp.status_code != 200:
                        yield f"event: upstream-error\ndata: {json.dumps({'message': f'watch failed: HTTP {resp.status_code}'})}\n\n"
                        return
                    buf = ""
                    async for chunk in resp.aiter_text():
                        buf += chunk
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                ev = json.loads(line)
                            except Exception:
                                continue
                            obj = ev.get("object", {})
                            md = obj.get("metadata", {})
                            slim = {
                                "type": ev.get("type", ""),
                                "kind": kind,
                                "ns": md.get("namespace", ""),
                                "name": md.get("name", ""),
                                "resourceVersion": md.get("resourceVersion", ""),
                            }
                            yield f"data: {json.dumps(slim, ensure_ascii=False)}\n\n"
                # 流结束（timeoutSeconds 到期），自动重连
            except asyncio.CancelledError:
                return
            except Exception as e:
                yield f"event: upstream-error\ndata: {json.dumps({'message': str(e)})}\n\n"
                return
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream")
