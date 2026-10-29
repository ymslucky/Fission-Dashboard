# -*- coding: utf-8 -*-
"""Fission 资源 REST CRUD 代理。

- 扩展 CRD（EXT_KINDS）先做特性探测，未安装时 501。
- environments 写操作走策略预检（仅平台管理员 / LOCAL_DEV）。
- messagequeuetriggers / kuberneteswatchtriggers 写前做危险字段校验。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request

from .. import k8s
from ..services import policy

router = APIRouter()


async def _precheck_write(kind: str, request: Request, obj: Any) -> None:
    """写操作统一策略预检。"""
    if kind == "environments":
        policy.require_env_admin(request)
    if kind in policy.DANGEROUS_KINDS and isinstance(obj, dict):
        await policy.validate_dangerous_trigger(kind, obj)


@router.get("/resources/{kind}")
async def list_kind(
    kind: str,
    labelSelector: str = Query("", alias="labelSelector"),
    fieldSelector: str = Query("", alias="fieldSelector"),
):
    k8s.check_kind(kind)
    if kind in k8s.EXT_KINDS:
        await k8s.ensure_feature(kind)
    items = await k8s.crd_list(kind, labelSelector, fieldSelector)
    return {"items": items}


@router.get("/resources/{kind}/{ns}/{name}")
async def get_kind(kind: str, ns: str, name: str):
    k8s.check_kind(kind)
    if kind in k8s.EXT_KINDS:
        await k8s.ensure_feature(kind)
    return await k8s.crd_get(kind, ns, name)


@router.post("/resources/{kind}", status_code=201)
async def create_kind(kind: str, obj: Any = Body(...), request: Request = None):
    k8s.check_kind(kind)
    if kind in k8s.EXT_KINDS:
        await k8s.ensure_feature(kind)
    await _precheck_write(kind, request, obj)
    return await k8s.crd_create(kind, obj)


@router.put("/resources/{kind}/{ns}/{name}")
async def update_kind(kind: str, ns: str, name: str, obj: Any = Body(...), request: Request = None):
    k8s.check_kind(kind)
    if kind in k8s.EXT_KINDS:
        await k8s.ensure_feature(kind)
    await _precheck_write(kind, request, obj)
    return await k8s.crd_update(kind, ns, name, obj)


@router.delete("/resources/{kind}/{ns}/{name}")
async def delete_kind(kind: str, ns: str, name: str,
                      request: Request = None,
                      propagationPolicy: str = Query("", alias="propagationPolicy")):
    k8s.check_kind(kind)
    if kind in k8s.EXT_KINDS:
        await k8s.ensure_feature(kind)
    if kind == "environments":
        policy.require_env_admin(request)
    return await k8s.crd_delete(kind, ns, name, propagationPolicy)
