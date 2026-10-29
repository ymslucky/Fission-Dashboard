# -*- coding: utf-8 -*-
"""批 1 集成模拟：函数 describe 触发器关联、test 归因、logs（406 回归 / container 选择）。

沿用 test_api.py 的 FakeK8s 打桩（conftest 仅提供 app fixture；FakeK8s 定义于 test_api，
此处直接复用）与 mock httpx.AsyncClient.request 的 Router 桩。
"""
from __future__ import annotations

import time as _time

import httpx
import pytest
from fastapi.testclient import TestClient

from server import k8s as k8s_mod
from server.main import create_app
from server.tests.test_api import FakeK8s, _FakeResp, _patch_http


@pytest.fixture
def f(monkeypatch):
    fk = FakeK8s()
    monkeypatch.setattr(k8s_mod, "request_k8s", fk.request_k8s)
    k8s_mod._feat_cache.update({"ts": 0.0, "available": set()})
    # 内部认证：未配置 master secret → 跳过签名头（pass-through）
    monkeypatch.setenv("FISSION_INTERNAL_AUTH_SECRET", "")
    return fk


def _client():
    return TestClient(create_app())


def _setup_fn(f, name="hello", ns="ns1", triggers=None, executor="newdeploy"):
    """describe 所需最小桩：函数 + 包 + 空 pod 列表 + 触发器列表。"""
    f.on("GET", f"/apis/fission.io/v1/namespaces/{ns}/functions/{name}",
         lambda p, b, r: (200, {"metadata": {"name": name, "namespace": ns},
                                "spec": {"package": {"packageref": "pkg1"},
                                         "environment": {"name": "python"},
                                         "InvokeStrategy": {"ExecutionStrategy": {"ExecutorType": executor}}}}))
    f.on("GET", f"/apis/fission.io/v1/namespaces/{ns}/packages/pkg1",
         lambda p, b, r: (200, {"metadata": {"name": "pkg1"},
                                "status": {"buildstatus": {"status": "succeeded"}}}))
    f.on("GET", f"/api/v1/namespaces/{ns}/pods?", lambda p, b, r: (200, {"items": []}))
    f.on("GET", "/apis/fission.io/v1/httptriggers",
         lambda p, b, r: (200, {"items": triggers if triggers is not None else []}))
    f.on("GET", "/apis/fission.io/v1/timetriggers", lambda p, b, r: (200, {"items": []}))
    f.on("GET", "/apis/fission.io/v1/messagequeuetriggers", lambda p, b, r: (200, {"items": []}))
    f.on("GET", f"/apis/fission.io/v1/namespaces/{ns}/canaryconfigs",
         lambda p, b, r: (200, {"items": []}))


# ---------------------------------------------------------------- test 归因：Router 四分类
def test_function_test_happy_200(f, monkeypatch):
    """happy：Router 200 → component=function / reason=ok / requestId 透传；
    并断言 BFF 使用内部监听器 base（FISSION_ROUTER_INTERNAL，默认 8889）。"""
    monkeypatch.setenv("FISSION_ROUTER_INTERNAL", "http://mock-router-internal:8889")
    captured = _patch_http(monkeypatch, _FakeResp(200, "hi", {"X-Fission-Request-ID": "rid-1"}))
    client = _client()
    data = client.post("/api/functions/ns1/hello/test", json={"body": "x"}).json()
    assert data["status"] == 200
    assert data["component"] == "function"
    assert data["reason"] == "ok"
    assert data["requestId"] == "rid-1"
    assert data["body"] == "hi"
    # 8888 公共 vs 8889 内部属部署配置：BFF 读 FISSION_ROUTER_INTERNAL（8889 内部 Service）
    assert captured["url"].startswith("http://mock-router-internal:8889")
    assert captured["url"].endswith("/fission-function/ns1/hello/")


def test_function_test_router_401_hmac_denied(f, monkeypatch):
    """HMAC 拒绝（内部监听器 401）→ component=router，reason 归因含 401。"""
    captured = _patch_http(monkeypatch, _FakeResp(401, "unauthorized"))
    data = _client().post("/api/functions/ns1/hello/test", json={}).json()
    assert data["status"] == 401
    assert data["component"] == "router"
    assert "401" in data["reason"]


def test_function_test_router_404_no_route(f, monkeypatch):
    """路径不匹配（Router 404）→ reason='function or route not found'。"""
    _patch_http(monkeypatch, _FakeResp(404, "no route"))
    data = _client().post("/api/functions/ns1/hello/test", json={}).json()
    assert data["status"] == 404
    assert data["component"] == "router"
    assert data["reason"] == "function or route not found"


def test_function_test_router_unreachable_502(f, monkeypatch):
    """依赖缺失：Router 不可达 → 502，detail.message 含 'router unreachable'。"""
    _patch_http(monkeypatch, exc=httpx.ConnectError("connection refused"))
    r = _client().post("/api/functions/ns1/hello/test", json={})
    assert r.status_code == 502
    assert "router unreachable" in r.json()["detail"]["message"]


# ---------------------------------------------------------------- describe：触发器关联（加权回归）
def test_describe_functionweights_trigger_matched(f):
    """回归根因：httptrigger 为 functionweights 形态（functionref 内嵌 functionweights
    含该函数）→ triggers 数组含该触发器（此前漏判）。"""
    _setup_fn(f, triggers=[
        {"metadata": {"name": "canary-route", "namespace": "ns1"},
         "spec": {"relativeUrl": "/canary",
                  "functionref": {"type": "function-weights", "name": "",
                                  "functionweights": {"hello": 80, "other": 20}}}},
    ])
    data = _client().get("/api/functions/ns1/hello/describe").json()
    assert {"kind": "httptriggers", "ns": "ns1", "name": "canary-route", "url": "/canary"} in data["triggers"]


def test_describe_functionref_name_hit(f):
    _setup_fn(f, triggers=[
        {"metadata": {"name": "t1", "namespace": "ns1"},
         "spec": {"relativeUrl": "/hello", "functionref": {"type": "name", "name": "hello"}}},
        {"metadata": {"name": "other", "namespace": "ns1"},
         "spec": {"relativeUrl": "/other", "functionref": {"type": "name", "name": "other"}}},
    ])
    triggers = _client().get("/api/functions/ns1/hello/describe").json()["triggers"]
    assert [t["name"] for t in triggers] == ["t1"]


def test_describe_no_triggers_empty(f):
    _setup_fn(f, triggers=[])
    assert _client().get("/api/functions/ns1/hello/describe").json()["triggers"] == []


# ---------------------------------------------------------------- describe：失败矩阵
def test_describe_403_rbac_message(f):
    """403：K8s RBAC 拒绝 → detail.message 带 'Kubernetes RBAC 拒绝' 前缀。"""
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functions/hello",
         lambda p, b, r: (403, {"message": "forbidden: User cannot get resource"}))
    r = _client().get("/api/functions/ns1/hello/describe")
    assert r.status_code == 403
    assert r.json()["detail"]["message"].startswith("Kubernetes RBAC 拒绝")


def test_describe_404_missing_function(f):
    """业务失败：函数不存在 → 404。"""
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functions/ghost",
         lambda p, b, r: (404, {"message": 'functions.fission.io "ghost" not found'}))
    assert _client().get("/api/functions/ns1/ghost/describe").status_code == 404


# 501（依赖未上线）：describe/test 均不经 ensure_feature 门控（区别于 versions 等
# 扩展端点），不存在 501 路径 —— SKIP：501 不适用于 describe。


# ---------------------------------------------------------------- logs：container 选择 / 406 / follow
def test_logs_multi_container_picks_non_fetcher(f):
    """多容器 Pod（fission-fetcher + python）→ log 请求带 container=<非 fetcher 容器>。"""
    f.on("GET", "/api/v1/namespaces/ns1/pods?", lambda p, b, r: (200, {"items": [
        {"metadata": {"name": "pod-a", "creationTimestamp": "t"},
         "status": {"phase": "Running", "containerStatuses": [{"ready": True}, {"ready": True}]}},
    ]}))
    # FakeK8s 按注册顺序做前缀匹配：log 子资源路径须先于 Pod 详情前缀注册
    captured = {}

    def log_h(path, b, r):
        captured["path"] = path
        return 200, b"line1"

    f.on("GET", "/api/v1/namespaces/ns1/pods/pod-a/log", log_h)
    f.on("GET", "/api/v1/namespaces/ns1/pods/pod-a",
         lambda p, b, r: (200, {"metadata": {"name": "pod-a"},
                                "spec": {"containers": [{"name": "fission-fetcher"}, {"name": "python"}]}}))
    data = _client().get("/api/functions/ns1/hello/logs").json()
    assert data["logs"]["pod-a"] == "line1"
    assert "container=python" in captured["path"]
    assert "fetcher" not in captured["path"]


def test_logs_406_marks_failed(f):
    """406 回归：log API 返回 406（如误设 Accept 协商头）→ 非 follow 响应
    logs[pod]='[failed: HTTP 406]'（固定现状）。"""
    f.on("GET", "/api/v1/namespaces/ns1/pods?", lambda p, b, r: (200, {"items": [
        {"metadata": {"name": "pod-a"}, "status": {"phase": "Running"}},
    ]}))
    f.on("GET", "/api/v1/namespaces/ns1/pods/pod-a/log", lambda p, b, r: (406, b"not acceptable"))
    f.on("GET", "/api/v1/namespaces/ns1/pods/pod-a",
         lambda p, b, r: (200, {"spec": {"containers": [{"name": "python"}]}}))
    data = _client().get("/api/functions/ns1/hello/logs").json()
    assert data["logs"]["pod-a"] == "[failed: HTTP 406]"


def test_logs_follow_without_pods_404(f):
    """follow=true 且无 Pod → 404 message='no pods found'。"""
    f.on("GET", "/api/v1/namespaces/ns1/pods?", lambda p, b, r: (200, {"items": []}))
    r = _client().get("/api/functions/ns1/x/logs", params={"follow": "true"})
    assert r.status_code == 404
    assert r.json()["detail"]["message"] == "no pods found"


def test_logs_pods_list_unreadable_403(f):
    """失败矩阵 403（logs 端点）：Pod 列表不可读 → 403 RBAC 前缀。"""
    f.on("GET", "/api/v1/namespaces/ns1/pods?",
         lambda p, b, r: (403, {"message": "forbidden: cannot list pods"}))
    r = _client().get("/api/functions/ns1/x/logs")
    assert r.status_code == 403
    assert r.json()["detail"]["message"].startswith("Kubernetes RBAC 拒绝")
