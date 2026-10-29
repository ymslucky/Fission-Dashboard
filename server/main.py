# -*- coding: utf-8 -*-
"""Fission Console — FastAPI 后端入口。

- /api/*：JSON API（资源 CRUD、观测、watch、invoke、misc、describe/test、
  版本回滚、canary、MCP、prom/mesh/audit）
- /healthz：健康检查
- 写请求成功后由审计中间件尽力记录到 ConfigMap fission-console-audit。
- 静态目录 ../web/dist 存在时挂载 SPA（同源部署；跨源时开启 CORS）。
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import k8s
from .k8s import close_client
from .routers import canary, functions_ext, invoke, mcp, misc, observe, ops, resources, versions, watch

STATIC_DIR = os.environ.get("STATIC_DIR")
if not STATIC_DIR:
    candidate = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "dist")
    STATIC_DIR = candidate if os.path.isdir(candidate) else ""

# CORS：默认关闭（同源部署）；设置 FISSION_CONSOLE_CORS=* 或逗号分隔来源开启
_cors = os.environ.get("FISSION_CONSOLE_CORS", "").strip()


def create_app() -> FastAPI:
    app = FastAPI(title="Fission Console", version="2.0")
    if _cors:
        origins = ["*"] if _cors == "*" else [o.strip() for o in _cors.split(",") if o.strip()]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(resources.router, prefix="/api")
    app.include_router(observe.router, prefix="/api")
    app.include_router(watch.router, prefix="/api")
    app.include_router(invoke.router, prefix="/api")
    app.include_router(functions_ext.router, prefix="/api")
    app.include_router(versions.router, prefix="/api")
    app.include_router(canary.router, prefix="/api")
    app.include_router(mcp.router, prefix="/api")
    app.include_router(ops.router, prefix="/api")
    app.include_router(misc.router, prefix="/api")

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    # 审计中间件：所有 /api/* 写请求成功后追加记录（尽力而为，失败不影响响应）
    @app.middleware("http")
    async def audit_middleware(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if (request.method in ("POST", "PUT", "DELETE")
                and path.startswith("/api/")
                and not path.startswith("/api/audit")
                and 200 <= response.status_code < 300):
            try:
                from .services import audit
                await audit.record_request(request, response.status_code)
            except Exception:
                pass
        return response

    # 错误统一 {"message": ...}
    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"message": f"{type(exc).__name__}: {exc}"})

    # SPA history-mode fallback：非 /api 路径回落 index.html
    if STATIC_DIR:
        from fastapi.responses import FileResponse

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail={"message": "not found"})
            candidate = os.path.normpath(os.path.join(STATIC_DIR, full_path))
            if candidate.startswith(STATIC_DIR) and os.path.isfile(candidate):
                return FileResponse(candidate)
            return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    if STATIC_DIR and os.path.isdir(STATIC_DIR):
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    @app.on_event("shutdown")
    async def _shutdown():
        await close_client()

    return app


app = create_app()
