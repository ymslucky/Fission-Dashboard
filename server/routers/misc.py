# -*- coding: utf-8 -*-
"""杂项：testdata、config（gitops sources）、source-token、gitops 系列。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import gitops
from ..services.store import load_sources, load_testdata, save_sources, save_testdata, token_save

router = APIRouter()


# ---------------------------------------------------------------- testdata
class TestdataPayload(BaseModel):
    collections: list = Field(default_factory=list)
    environments: list = Field(default_factory=list)

@router.get("/testdata")
async def get_testdata():
    return await load_testdata()

@router.put("/testdata")
async def put_testdata(data: dict):
    await save_testdata(data or {})
    return {"ok": True}


# ---------------------------------------------------------------- config（gitops sources CRUD）
@router.get("/config")
async def get_config():
    return await load_sources()

@router.put("/config")
async def put_config(cfg: dict):
    await save_sources(cfg)
    return cfg


# ---------------------------------------------------------------- source token
class TokenPayload(BaseModel):
    token: str = ""

@router.put("/sources/{name}/token")
async def put_source_token(name: str, payload: TokenPayload):
    await token_save(name, payload.token)
    return {"ok": True}


# ---------------------------------------------------------------- gitops v2
def _src_dep(name: str):
    """依赖辅助：按名字取 source（异步包装见各端点内）。"""
    return name

async def _load_src(name: str) -> dict:
    return await gitops.get_source(await load_sources(), name)

@router.get("/gitops/{source}/tree")
async def gitops_tree(source: str):
    return await gitops.gitops_tree(await _load_src(source))

@router.get("/gitops/{source}/file")
async def gitops_file(source: str, path: str):
    return await gitops.gitops_file(await _load_src(source), path)

@router.get("/gitops/{source}/status")
async def gitops_status(source: str):
    return await gitops.gitops_statuses(await _load_src(source))

@router.get("/gitops/{source}/plan")
async def gitops_plan(source: str):
    return await gitops.gitops_plan_v2(await _load_src(source))

class ApplyPayload(BaseModel):
    deletes: Optional[list] = None
    conflicts: Optional[dict] = None

@router.post("/gitops/{source}/apply")
async def gitops_apply(source: str, payload: ApplyPayload):
    return await gitops.gitops_apply_v2(await _load_src(source), payload.deletes, payload.conflicts)

class ExportPayload(BaseModel):
    mode: Optional[str] = None
    resources: Optional[list] = None

@router.post("/gitops/{source}/export")
async def gitops_export(source: str, payload: ExportPayload):
    return await gitops.gitops_export(await _load_src(source), payload.mode, payload.resources)
