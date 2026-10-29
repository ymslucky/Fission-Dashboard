# -*- coding: utf-8 -*-
"""FunctionVersion / FunctionAlias：版本列表与回滚（移动 alias 指针）。

结构假设（以集群 CRD 实际 schema 为准，未在集群验证）：
- FunctionVersion：namespaced；spec.functionName（或 label functionName）指向对应
  Function，兜底与 FunctionVersion 同名（同名即对应 Function 的一个版本）。
- FunctionAlias：namespaced；spec.target 为字符串 Function 名。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import k8s

router = APIRouter()

COLD_START_WARNING = "目标版本若无 warm Pod，回滚后首调将经历一次普通冷启动"


def _fv_function_name(fv: dict) -> str:
    spec = fv.get("spec") or {}
    md = fv.get("metadata") or {}
    return (spec.get("functionName")
            or md.get("labels", {}).get("functionName")
            or spec.get("function")
            or md.get("name", ""))


@router.get("/functions/{ns}/{name}/versions")
async def list_versions(ns: str, name: str):
    await k8s.ensure_feature("functionversions")
    await k8s.ensure_feature("functionalias")
    versions = []
    for fv in await k8s.crd_list("functionversions"):
        md = fv.get("metadata") or {}
        if md.get("namespace", ns) not in ("", ns):
            continue
        spec = fv.get("spec") or {}
        explicit = spec.get("functionName") or md.get("labels", {}).get("functionName") \
            or spec.get("function") or ""
        # 显式 functionName 与请求的函数不一致则跳过；否则假定 FunctionVersion
        # 名称即对应 Function 名称（见模块 docstring 的结构假设）
        if explicit and explicit != name:
            continue
        target_fn = explicit or md.get("name", "")
        code, _ = await k8s.request_k8s(
            "GET", f"/apis/{k8s.GROUP}/{k8s.VERSION}/namespaces/{ns}/functions/{target_fn}")
        versions.append({
            "name": md.get("name", ""),
            "created": md.get("creationTimestamp", ""),
            "function": target_fn,
            "functionExists": code == 200,
        })
    versions.sort(key=lambda v: v["created"], reverse=True)

    aliases = []
    for al in await k8s.crd_list("functionalias"):
        md = al.get("metadata") or {}
        spec = al.get("spec") or {}
        target = spec.get("target")
        if isinstance(target, dict):
            target = target.get("name", "")
        if target and target != name:
            continue
        aliases.append({"name": md.get("name", ""), "target": target or "",
                        "created": md.get("creationTimestamp", "")})
    return {"versions": versions, "aliases": aliases}


class RollbackBody(BaseModel):
    alias: str
    toVersion: str


@router.post("/functions/{ns}/{name}/rollback")
async def rollback(ns: str, name: str, body: RollbackBody):
    """回滚 = 把 FunctionAlias.spec.target 指到目标 FunctionVersion 对应的 Function。"""
    await k8s.ensure_feature("functionversions")
    await k8s.ensure_feature("functionalias")
    try:
        fv = await k8s.crd_get("functionversions", ns, body.toVersion)
    except HTTPException:
        raise HTTPException(status_code=404,
                            detail={"message": f"FunctionVersion {ns}/{body.toVersion} 不存在"})
    fn_name = _fv_function_name(fv) or name
    code, _ = await k8s.request_k8s(
        "GET", f"/apis/{k8s.GROUP}/{k8s.VERSION}/namespaces/{ns}/functions/{fn_name}")
    if code != 200:
        raise HTTPException(status_code=404,
                            detail={"message": f"目标 Function {ns}/{fn_name} 不存在"})
    try:
        alias_cr = await k8s.crd_get("functionalias", ns, body.alias)
    except HTTPException:
        raise HTTPException(status_code=404,
                            detail={"message": f"FunctionAlias {ns}/{body.alias} 不存在"})
    alias_cr.setdefault("spec", {})["target"] = fn_name
    await k8s.crd_update("functionalias", ns, body.alias, alias_cr)
    return {"ok": True, "alias": body.alias, "version": body.toVersion,
            "target": fn_name, "coldStartWarning": COLD_START_WARNING}
