# -*- coding: utf-8 -*-
"""MCP：工具清单、函数暴露开关、token 签发/吊销、集群配置探测。

字段假设：Fission CRD 目前没有专用的 MCP spec 字段（以集群实际 CRD 为准——
本实现曾按"先 GET 真实 function 看结构"的原则核查，字段不存在），因此统一
落在 metadata.annotations：
- fission.io/mcp-enabled: "true"/"false"
- fission.io/mcp-tool-description: string
- fission.io/mcp-input-schema: JSON 字符串
若后续集群 CRD 增加 spec 字段，仅需调整 _get/_set 两个辅助函数。
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import k8s
from ..services import mcptoken

router = APIRouter()

ANN_ENABLED = "fission.io/mcp-enabled"
ANN_DESC = "fission.io/mcp-tool-description"
ANN_SCHEMA = "fission.io/mcp-input-schema"


def _mcp_of(fn: dict) -> Optional[dict]:
    ann = (fn.get("metadata") or {}).get("annotations") or {}
    if str(ann.get(ANN_ENABLED, "")).lower() != "true":
        return None
    schema = {}
    try:
        schema = json.loads(ann.get(ANN_SCHEMA, "") or "{}")
    except Exception:
        pass
    return {"toolDescription": ann.get(ANN_DESC, ""), "inputSchema": schema}


@router.get("/mcp/tools")
async def mcp_tools():
    """扫全命名空间 function 的 tool exposed 标记 + tool-description。"""
    code, body = await k8s.request_k8s("GET", f"/apis/{k8s.GROUP}/{k8s.VERSION}/functions")
    if code != 200:
        raise HTTPException(status_code=code if code >= 400 else 502,
                            detail={"message": "failed to list functions"})
    tools = []
    for fn in body.get("items", []):
        md = fn.get("metadata") or {}
        info = _mcp_of(fn)
        if info:
            tools.append({"ns": md.get("namespace", ""), "name": md.get("name", ""), **info})
    return {"tools": tools}


class McpPayload(BaseModel):
    enabled: bool
    toolDescription: str = ""
    inputSchema: Optional[dict] = None


@router.put("/functions/{ns}/{name}/mcp")
async def set_mcp(ns: str, name: str, payload: McpPayload):
    """写回 function 注解（见模块 docstring 的字段假设）。"""
    fn = await k8s.crd_get("functions", ns, name)
    md = fn.setdefault("metadata", {})
    ann = md.setdefault("annotations", {})
    ann[ANN_ENABLED] = "true" if payload.enabled else "false"
    if payload.enabled:
        ann[ANN_DESC] = payload.toolDescription
        if payload.inputSchema is not None:
            ann[ANN_SCHEMA] = json.dumps(payload.inputSchema, ensure_ascii=False)
    return await k8s.crd_update("functions", ns, name, fn)


class TokenPayload(BaseModel):
    name: str
    allowedNamespaces: list[str] = Field(default_factory=list)
    ttlDays: int = 30


@router.post("/mcp/tokens", status_code=201)
async def create_token(payload: TokenPayload):
    if not payload.name:
        raise HTTPException(status_code=422, detail={"message": "name 不能为空"})
    if payload.ttlDays <= 0 or payload.ttlDays > 365:
        raise HTTPException(status_code=422, detail={"message": "ttlDays 必须在 1-365 之间"})
    result = await mcptoken.issue_token(payload.name, payload.allowedNamespaces, payload.ttlDays)
    return result


@router.delete("/mcp/tokens/{name}")
async def delete_token(name: str):
    await mcptoken.revoke_token(name)
    return {"ok": True, "name": name}


@router.get("/mcp/tokens")
async def list_tokens():
    return {"tokens": await mcptoken.list_tokens()}


@router.get("/mcp/config")
async def mcp_config():
    """探测集群 mcp.enabled（fission configmap 尽力而为，失败即 enabled:false）。"""
    for cm_name in ("fission-controller", "fission-config"):
        code, cm = await k8s.request_k8s(
            "GET", f"/api/v1/namespaces/{k8s.CONFIG_NS}/configmaps/{cm_name}")
        if code != 200:
            continue
        data = cm.get("data") or {}
        raw = data.get("mcp.enabled")
        if raw is None and "mcp" in data:
            try:
                raw = json.loads(data["mcp"]).get("enabled")
            except Exception:
                raw = None
        if raw is not None:
            return {"enabled": str(raw).lower() in ("1", "true", "yes"),
                    "source": f"configmap/{cm_name}"}
    return {"enabled": False, "source": "probe-failed",
            "message": "未能从集群探测 MCP 配置（fission configmap 无 mcp.enabled），按未启用处理；"
                       "如已启用请检查 fission-controller/fission-config ConfigMap"}
