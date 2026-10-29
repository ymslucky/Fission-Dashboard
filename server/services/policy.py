# -*- coding: utf-8 -*-
"""策略预检：写操作进入 K8s 之前的准入检查。

- ENV 写操作：仅 ADMIN_GROUPS（默认 fission-admins，X-authentik-groups 头）
  或 LOCAL_DEV=1 时放行。
- MessageQueueTrigger / KubernetesWatchTrigger 危险字段校验：
  spec.namespace 必须与资源 namespace 一致；引用的 Secret 必须存在于同 namespace。
- RBAC 拒绝（K8s 403）由 k8s._raise 统一附加清晰 message 向上透传。
"""
from __future__ import annotations

import os

from fastapi import HTTPException, Request

from .. import k8s

DANGEROUS_KINDS = ("messagequeuetriggers", "kuberneteswatchtriggers")


def admin_groups() -> list[str]:
    raw = os.environ.get("ADMIN_GROUPS", "fission-admins")
    return [g.strip() for g in raw.split(",") if g.strip()]


def current_user(request: Request) -> str:
    """审计用户：authentik 转发头，缺失时 anonymous。"""
    return (
        request.headers.get("X-authentik-identity")
        or request.headers.get("X-authentik-username")
        or "anonymous"
    )


def is_admin(request: Request) -> bool:
    if os.environ.get("LOCAL_DEV") == "1":
        return True
    groups = request.headers.get("X-authentik-groups", "")
    return any(g in groups for g in admin_groups())


def require_env_admin(request: Request) -> None:
    """ENV 写操作预检；不通过时 403。"""
    if not is_admin(request):
        raise HTTPException(
            status_code=403,
            detail={"message": "Environment 修改仅限平台管理员"},
        )


async def validate_dangerous_trigger(kind: str, obj: dict) -> None:
    """MQT / KWT 写前危险字段校验。"""
    if kind not in DANGEROUS_KINDS:
        return
    md = obj.get("metadata") or {}
    spec = obj.get("spec") or {}
    ns = md.get("namespace") or "fission"
    spec_ns = spec.get("namespace")
    if spec_ns and spec_ns != ns:
        raise HTTPException(
            status_code=422,
            detail={"message": f"spec.namespace ({spec_ns}) 不得与资源 namespace ({ns}) 不同"},
        )
    # 引用的 Secret 收集：spec.secret / spec.secrets（字符串或 {name:...}）
    names: list[str] = []
    s = spec.get("secret")
    if isinstance(s, str) and s:
        names.append(s)
    for item in spec.get("secrets") or []:
        if isinstance(item, str) and item:
            names.append(item)
        elif isinstance(item, dict) and item.get("name"):
            names.append(item["name"])
    for name in names:
        code, _ = await k8s.request_k8s("GET", f"/api/v1/namespaces/{ns}/secrets/{name}")
        if code != 200:
            raise HTTPException(
                status_code=422,
                detail={"message": f"引用的 Secret {ns}/{name} 不存在（须与资源同 namespace）"},
            )
