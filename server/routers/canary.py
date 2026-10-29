# -*- coding: utf-8 -*-
"""CanaryConfig CRUD + status；HTTPTrigger 权重调整。"""
from __future__ import annotations

from typing import Optional

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import k8s

router = APIRouter()

CANARY_REQUIRED = ("newfunction", "oldfunction", "trigger", "weightincrement",
                   "duration", "failurethreshold", "failureType")
CANARY_FAILURE_TYPES = ("failure", "timeout")


def validate_canary(obj: dict) -> dict:
    """写前策略预检：必填字段 + 类型/范围校验。返回 spec。"""
    spec = obj.get("spec") or {}
    missing = [k for k in CANARY_REQUIRED if spec.get(k) in ("", None)]
    if missing:
        raise HTTPException(status_code=422,
                            detail={"message": f"缺少必填字段: {', '.join(missing)}"})
    if not isinstance(spec["weightincrement"], int) or not 0 < spec["weightincrement"] <= 100:
        raise HTTPException(status_code=422,
                            detail={"message": "weightincrement 必须是 1-100 的整数"})
    # v1.27 CanaryConfig.spec.duration 是 Go duration 字符串（json:"duration"，默认 "2m"）
    import re as _re
    dur = spec["duration"]
    if not isinstance(dur, str) or not _re.fullmatch(r"[0-9]+(ms|s|m|h|d)", dur):
        raise HTTPException(status_code=422,
                            detail={"message": "duration 必须是 Go duration 字符串，如 30s / 2m / 1h"})
    if not isinstance(spec["failurethreshold"], int) or spec["failurethreshold"] <= 0:
        raise HTTPException(status_code=422, detail={"message": "failurethreshold 必须是正整数"})
    if spec["failureType"] not in CANARY_FAILURE_TYPES:
        raise HTTPException(status_code=422,
                            detail={"message": f"failureType 必须是 {'/'.join(CANARY_FAILURE_TYPES)}"})
    return spec


async def _check_functions(ns: str, spec: dict) -> None:
    for k in ("newfunction", "oldfunction"):
        code, _ = await k8s.request_k8s(
            "GET", f"/apis/{k8s.GROUP}/{k8s.VERSION}/namespaces/{ns}/functions/{spec[k]}")
        if code != 200:
            raise HTTPException(status_code=422,
                                detail={"message": f"{k} 引用的函数 {ns}/{spec[k]} 不存在"})


@router.get("/canaryconfigs")
async def list_canary():
    await k8s.ensure_feature("canaryconfigs")
    return {"items": await k8s.crd_list("canaryconfigs")}


@router.get("/canaryconfigs/{ns}/{name}")
async def get_canary(ns: str, name: str):
    await k8s.ensure_feature("canaryconfigs")
    return await k8s.crd_get("canaryconfigs", ns, name)


@router.post("/canaryconfigs", status_code=201)
async def create_canary(obj: dict):
    await k8s.ensure_feature("canaryconfigs")
    spec = validate_canary(obj)
    ns = (obj.get("metadata") or {}).get("namespace", k8s.CONFIG_NS)
    await _check_functions(ns, spec)
    return await k8s.crd_create("canaryconfigs", obj)


@router.put("/canaryconfigs/{ns}/{name}")
async def update_canary(ns: str, name: str, obj: dict):
    await k8s.ensure_feature("canaryconfigs")
    spec = validate_canary(obj)
    await _check_functions(ns, spec)
    return await k8s.crd_update("canaryconfigs", ns, name, obj)


@router.delete("/canaryconfigs/{ns}/{name}")
async def delete_canary(ns: str, name: str):
    await k8s.ensure_feature("canaryconfigs")
    return await k8s.crd_delete("canaryconfigs", ns, name)


@router.get("/canaryconfigs/{ns}/{name}/status")
async def canary_status(ns: str, name: str):
    """status 阶段 + 关联 httptrigger 的实时权重 + 失败计数。"""
    await k8s.ensure_feature("canaryconfigs")
    cc = await k8s.crd_get("canaryconfigs", ns, name)
    status = cc.get("status") or {}
    spec = cc.get("spec") or {}
    weights = None
    trigger_name = spec.get("trigger", "")
    if trigger_name:
        code, tr = await k8s.request_k8s(
            "GET", f"/apis/{k8s.GROUP}/{k8s.VERSION}/namespaces/{ns}/httptriggers/{trigger_name}")
        if code == 200:
            weights = (tr.get("spec") or {}).get("functionWeights")
    return {
        "name": name,
        "ns": ns,
        "stage": status.get("status") or status.get("stage") or "unknown",
        "status": status,
        "functionWeights": weights,
        "failures": status.get("failures", status.get("failureCount", 0)),
        "weightincrement": spec.get("weightincrement"),
        "duration": spec.get("duration"),
        "failurethreshold": spec.get("failurethreshold"),
        "failureType": spec.get("failureType"),
    }


class WeightsBody(BaseModel):
    weights: dict[str, int]


@router.put("/httptriggers/{ns}/{name}/weights")
async def put_weights(ns: str, name: str, body: WeightsBody):
    """写 HTTPTrigger.spec.functionWeights；校验总和=100 且函数存在。"""
    total = sum(body.weights.values())
    if total != 100:
        raise HTTPException(status_code=422,
                            detail={"message": f"权重总和必须为 100，当前为 {total}"})
    if not body.weights:
        raise HTTPException(status_code=422, detail={"message": "weights 不能为空"})
    for fn in body.weights:
        code, _ = await k8s.request_k8s(
            "GET", f"/apis/{k8s.GROUP}/{k8s.VERSION}/namespaces/{ns}/functions/{fn}")
        if code != 200:
            raise HTTPException(status_code=404,
                                detail={"message": f"函数 {ns}/{fn} 不存在"})
    tr = await k8s.crd_get("httptriggers", ns, name)
    tr.setdefault("spec", {})["functionWeights"] = body.weights
    await k8s.crd_update("httptriggers", ns, name, tr)
    return {"ok": True, "functionWeights": body.weights}
