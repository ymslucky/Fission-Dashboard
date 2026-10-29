# -*- coding: utf-8 -*-
"""API 测试：打桩 K8s / Router / GitHub 网络出口。

通过 monkeypatch 替换 server.k8s.request_k8s（返回 (status, data) 的异步函数），
以及 observe 中的 SSE/Router 请求，覆盖 CRUD 代理、overview 聚合、logs 参数传递等。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from server import k8s as k8s_mod
from server.main import create_app


class FakeK8s:
    """按 (method, path 前缀) 路由的假 K8s。"""

    def __init__(self):
        self.routes = {}   # (method, prefix) -> callable(path, body, raw) -> (status, data)
        self.calls = []

    def on(self, method, prefix, handler):
        self.routes[(method, prefix)] = handler

    async def request_k8s(self, method, path, body=None, headers=None, timeout=30.0, raw=False):
        self.calls.append((method, path, body, raw))
        for (m, prefix), h in self.routes.items():
            if m == method and path.startswith(prefix):
                return h(path, body, raw)
        return 404, {"message": "no route " + path}


@pytest.fixture
def fake(monkeypatch):
    f = FakeK8s()
    monkeypatch.setattr(k8s_mod, "request_k8s", f.request_k8s)
    # 复位 CRD 特性探测缓存，避免跨测试污染
    k8s_mod._feat_cache.update({"ts": 0.0, "available": set()})
    # CRUD 辅助内部引用的是同一模块的 request_k8s，因此上面替换即可。
    app = create_app()
    return f, TestClient(app)


# ---------------------------------------------------------------- CRUD 代理
def test_list_functions_proxies_k8s(fake):
    f, client = fake
    f.on("GET", "/apis/fission.io/v1/functions",
         lambda p, b, r: (200, {"items": [{"metadata": {"name": "fn1"}}]}))
    resp = client.get("/api/resources/functions")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["metadata"]["name"] == "fn1"


def test_list_with_label_selector_passes_through(fake):
    f, client = fake
    seen = {}

    def h(path, b, r):
        seen["path"] = path
        return 200, {"items": []}
    f.on("GET", "/apis/fission.io/v1/packagex", h)
    f.on("GET", "/apis/fission.io/v1/packages", h)
    client.get("/api/resources/packages", params={"labelSelector": "env=py", "fieldSelector": ""})
    assert "labelSelector=env%3Dpy" in seen["path"]


def test_get_unknown_kind_404(fake):
    f, client = fake
    resp = client.get("/api/resources/widgets/ns/name")
    assert resp.status_code == 404
    assert "message" in resp.json()["detail"]


def test_create_update_delete_flow(fake):
    f, client = fake
    f.on("POST", "/apis/fission.io/v1/namespaces/fission/functions",
         lambda p, b, r: (201, b))
    f.on("PUT", "/apis/fission.io/v1/namespaces/fission/functions/fn1",
         lambda p, b, r: (200, b))
    f.on("DELETE", "/apis/fission.io/v1/namespaces/fission/functions/fn1",
         lambda p, b, r: (200, {}))
    obj = {"metadata": {"name": "fn1", "namespace": "fission"}, "spec": {}}
    assert client.post("/api/resources/functions", json=obj).status_code == 201
    assert client.put("/api/resources/functions/fission/fn1", json=obj).status_code == 200
    r = client.delete("/api/resources/functions/fission/fn1",
                      params={"propagationPolicy": "Background"})
    assert r.status_code == 200
    # propagationPolicy 被传递（审计中间件会追加 configmaps 调用，按方法过滤）
    assert any(m == "DELETE" and "propagationPolicy=Background" in p
               for m, p, _, _ in f.calls)


def test_k8s_error_maps_to_http_exception(fake):
    f, client = fake
    f.on("GET", "/apis/fission.io/v1/namespaces/x/functions/nope",
         lambda p, b, r: (404, {"message": "not found"}))
    resp = client.get("/api/resources/functions/x/nope")
    assert resp.status_code == 404


# ---------------------------------------------------------------- overview / metrics
def test_overview_aggregates_counts_and_core(fake):
    f, client = fake
    for kind in k8s_mod.KINDS:
        f.on("GET", f"/apis/fission.io/v1/{kind}",
             lambda p, b, r: (200, {"items": [{"a": 1}, {"a": 2}]}))
    f.on("GET", "/api/v1/namespaces/fission/pods", lambda p, b, r: (200, {"items": [
        {"metadata": {"labels": {"app": "router"}},
         "status": {"containerStatuses": [{"ready": True}]}},
        {"metadata": {"labels": {"app": "router"}},
         "status": {"containerStatuses": [{"ready": False}]}},
    ]}))
    data = client.get("/api/overview").json()
    assert data["counts"]["functions"] == 2
    core = {c["app"]: c for c in data["core"]}
    assert core["router"] == {"app": "router", "pods": 2, "ready": 1}


def test_metrics_aggregates_by_function(fake):
    f, client = fake
    f.on("GET", "/api/v1/pods", lambda p, b, r: (200, {"items": [
        {"metadata": {"namespace": "fission-fn", "labels": {"functionName": "hello"}},
         "status": {"containerStatuses": [{"ready": True}]}},
    ]}))
    f.on("GET", "/apis/metrics.k8s.io/v1beta1/pods", lambda p, b, r: (200, {"items": [
        {"metadata": {"labels": {"functionName": "hello"}},
         "containers": [{"usage": {"cpu": "150000000n", "memory": "2048Ki"}}]},
        {"metadata": {"labels": {"functionName": "hello"}},
         "containers": [{"usage": {"cpu": "50m", "memory": "1Mi"}}]},
    ]}))
    data = client.get("/api/metrics").json()
    fn = data["functions"]["hello"]
    assert fn["pods"] == 1 and fn["ready"] == 1
    assert fn["cpu_m"] == 200  # 150m + 50m
    assert fn["mem_mi"] == 3   # 2048Ki/1024 -> 2 + 1


# ---------------------------------------------------------------- logs
def test_logs_nonfollow_aggregates_tail_lines(fake):
    f, client = fake
    f.on("GET", "/api/v1/namespaces/ns1/pods?", lambda p, b, r: (200, {"items": [
        {"metadata": {"name": "pod-a", "creationTimestamp": "t"},
         "status": {"phase": "Running", "containerStatuses": [{"ready": True}]}},
    ]}))
    captured = {}

    def log_h(path, b, r):
        captured["path"] = path
        return 200, b"line1\nline2"
    f.on("GET", "/api/v1/namespaces/ns1/pods/pod-a/log", log_h)
    data = client.get("/api/functions/ns1/hello/logs", params={"tailLines": 42}).json()
    assert data["pods"][0]["name"] == "pod-a"
    assert data["logs"]["pod-a"] == "line1\nline2"
    assert "tailLines=42" in captured["path"]


def test_logs_failed_pod_log_marks_error(fake):
    f, client = fake
    f.on("GET", "/api/v1/namespaces/ns1/pods?", lambda p, b, r: (200, {"items": [
        {"metadata": {"name": "pod-b"}, "status": {"phase": "Pending"}},
    ]}))
    f.on("GET", "/api/v1/namespaces/ns1/pods/pod-b/log", lambda p, b, r: (403, b"denied"))
    data = client.get("/api/functions/ns1/x/logs").json()
    assert data["logs"]["pod-b"] == "[failed: HTTP 403]"


def test_logs_follow_requires_pod(fake, monkeypatch):
    f, client = fake
    f.on("GET", "/api/v1/namespaces/ns1/pods?", lambda p, b, r: (200, {"items": []}))
    resp = client.get("/api/functions/ns1/x/logs", params={"follow": "true"})
    assert resp.status_code == 404


def test_events_filtered_by_function_pods(fake):
    f, client = fake
    f.on("GET", "/api/v1/namespaces/ns1/pods?", lambda p, b, r: (200, {"items": [
        {"metadata": {"name": "pod-a"}, "status": {"phase": "Running"}},
    ]}))
    f.on("GET", "/api/v1/namespaces/ns1/events", lambda p, b, r: (200, {"items": [
        {"involvedObject": {"kind": "Pod", "name": "pod-a"}, "type": "Warning",
         "reason": "BackOff", "message": "back-off", "count": 3, "lastTimestamp": "2026-01-01T00:00:00Z"},
        {"involvedObject": {"kind": "Pod", "name": "other"}, "type": "Normal", "reason": "X", "message": "y"},
    ]}))
    data = client.get("/api/functions/ns1/hello/events").json()
    assert len(data["items"]) == 1
    assert data["items"][0]["reason"] == "BackOff"


# ---------------------------------------------------------------- invoke（Router）
def test_invoke_via_router(monkeypatch):
    f = FakeK8s()
    f.on("GET", "/apis/fission.io/v1/namespaces/fission/httptriggers/t1",
         lambda p, b, r: (200, {"spec": {"relativeUrl": "/hello", "methods": ["POST"]}}))
    monkeypatch.setattr(k8s_mod, "request_k8s", f.request_k8s)

    import server.routers.invoke as inv

    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/plain"}
        text = "hi"

    async def fake_req(self_method, url, **kw):
        fake_req.url = url
        return FakeResp()

    from server.main import create_app
    app = create_app()
    with TestClient(app) as client:
        # 打桩 httpx.AsyncClient.request
        import httpx as _httpx
        orig = _httpx.AsyncClient.request

        async def patched(self, method, url, **kw):
            fake_req.url = url
            return FakeResp()
        monkeypatch.setattr(_httpx.AsyncClient, "request", patched)
        r = client.post("/api/invoke", json={"ns": "fission", "trigger": "t1", "body": "x"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == 200 and data["body"] == "hi"
        assert fake_req.url.endswith("/hello")
        monkeypatch.setattr(_httpx.AsyncClient, "request", orig)


# ---------------------------------------------------------------- misc
def test_testdata_roundtrip(fake):
    f, client = fake

    def cm_h(path, b, r):
        return 404, {"message": "missing"}
    f.on("GET", "/api/v1/namespaces/fission/configmaps/fission-console-testdata", cm_h)
    f.on("POST", "/api/v1/namespaces/fission/configmaps", lambda p, b, r: (201, b))
    assert client.get("/api/testdata").json() == {"collections": [], "environments": []}
    payload = {"collections": [{"name": "c1"}], "environments": []}
    assert client.put("/api/testdata", json=payload).status_code == 200
    # POST 的 body 中带 collections.json
    posted = [c for c in f.calls if c[0] == "POST"]
    assert posted and 'collections.json' in posted[0][2]['data']


def test_config_get_put(fake):
    f, client = fake
    f.on("GET", "/api/v1/namespaces/fission/configmaps/fission-console-config",
         lambda p, b, r: (200, {"metadata": {"resourceVersion": "1"},
                                "data": {"sources.json": json.dumps({"sources": [{"name": "s1", "repo": "r"}]})}}))
    f.on("PUT", "/api/v1/namespaces/fission/configmaps/fission-console-config",
         lambda p, b, r: (200, b))
    cfg = client.get("/api/config").json()
    assert cfg["sources"][0]["name"] == "s1"
    cfg["sources"].append({"name": "s2", "repo": "r2"})
    assert client.put("/api/config", json=cfg).status_code == 200


def test_gitops_source_not_found(fake):
    f, client = fake
    f.on("GET", "/api/v1/namespaces/fission/configmaps/fission-console-config",
         lambda p, b, r: (404, {"message": "missing"}))
    resp = client.get("/api/gitops/nope/plan")
    assert resp.status_code == 404


def test_healthz(fake):
    f, client = fake
    assert client.get("/healthz").json() == {"ok": True}


# ================================================================ 扩展功能测试
import time as _time


def _enable_features(f, *kinds):
    """直接填充特性探测缓存（避免 /apis/fission.io/v1 路由前缀劫持其他路由）。"""
    k8s_mod._feat_cache.update({"ts": _time.time() + 1e6, "available": set(kinds)})


# ---------------------------------------------------------------- describe 聚合
def _setup_describe(f, executor="poolmgr", pods=None):
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functions/hello",
         lambda p, b, r: (200, {"metadata": {"name": "hello",
                                             "annotations": {"fission.io/mcp-enabled": "true",
                                                             "fission.io/mcp-tool-description": "say hi"}},
                                "spec": {"package": {"packageref": "pkg1"},
                                         "environment": {"name": "python"},
                                         "InvokeStrategy": {"ExecutionStrategy": {"ExecutorType": executor}}}}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/packages/pkg1",
         lambda p, b, r: (200, {"metadata": {"name": "pkg1"},
                                "status": {"buildstatus": {"status": "succeeded", "buildlog": "log-ref"}}}))
    if pods is None:
        pods = [{"metadata": {"name": "p1"}, "status": {"phase": "Running",
                                                        "containerStatuses": [{"ready": True}]}}]
    f.on("GET", "/api/v1/namespaces/ns1/pods?", lambda p, b, r: (200, {"items": pods}))
    f.on("GET", "/apis/fission.io/v1/httptriggers", lambda p, b, r: (200, {"items": [
        {"metadata": {"name": "t1", "namespace": "ns1"},
         "spec": {"functionref": "hello", "relativeUrl": "/hello"}},
        {"metadata": {"name": "other", "namespace": "ns1"},
         "spec": {"functionref": "other", "relativeUrl": "/other"}},
    ]}))
    f.on("GET", "/apis/fission.io/v1/timetriggers", lambda p, b, r: (200, {"items": []}))
    f.on("GET", "/apis/fission.io/v1/messagequeuetriggers", lambda p, b, r: (200, {"items": []}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/canaryconfigs",
         lambda p, b, r: (200, {"items": [
             {"metadata": {"name": "c1"},
              "spec": {"newfunction": "hello", "oldfunction": "old"},
              "status": {"status": "progressing"}},
         ]}))


def test_describe_aggregates_poolmgr(fake):
    f, client = fake
    _setup_describe(f, executor="poolmgr")
    # poolmgr：warm pods 来自环境池 Deployment（poolmgr-{env}-*）的就绪副本
    def pool_h(path, b, r):
        assert "environmentName%3Dpython" in path or "environmentName=python" in path
        return (200, {"items": [
            {"metadata": {"name": "poolmgr-python-abc"},
             "status": {"readyReplicas": 2}},
            {"metadata": {"name": "poolmgr-python-def"},
             "status": {"readyReplicas": 1}},
            {"metadata": {"name": "newdeploy-hello"},  # 非池部署，不计入
             "status": {"readyReplicas": 9}},
        ]})
    f.on("GET", "/apis/apps/v1/namespaces/ns1/deployments", pool_h)
    data = client.get("/api/functions/ns1/hello/describe").json()
    assert data["package"]["buildStatus"] == "succeeded"
    assert data["package"]["buildlog"] == "log-ref"
    assert data["pods"] == {"warm": 3, "ready": 3}
    assert data["executorType"] == "poolmgr"
    assert data["invocable"] is True
    assert data["invocable"] is True
    assert data["invocableNote"] == "Yes (cold start on first call)"
    assert data["triggers"][0]["name"] == "t1"
    assert data["canary"][0]["stage"] == "progressing"
    assert data["mcpToolExposed"]["enabled"] is True
    assert data["mcpToolExposed"]["toolDescription"] == "say hi"


def test_describe_newdeploy_no_pods_not_invocable(fake):
    f, client = fake
    _setup_describe(f, executor="newdeploy", pods=[])
    data = client.get("/api/functions/ns1/hello/describe").json()
    assert data["invocable"] is False
    assert data["invocableNote"] == "No (等特化)"


# ---------------------------------------------------------------- test 归因
class _FakeResp:
    def __init__(self, status=200, text="ok", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


def _patch_http(monkeypatch, resp=None, exc=None):
    import httpx as _httpx
    captured = {}

    async def patched(self, method, url, **kw):
        captured["method"] = method
        captured["url"] = url
        if exc is not None:
            raise exc
        return resp

    monkeypatch.setattr(_httpx.AsyncClient, "request", patched)
    return captured


def test_function_test_attribution_503(monkeypatch):
    f = FakeK8s()
    monkeypatch.setattr(k8s_mod, "request_k8s", f.request_k8s)
    captured = _patch_http(
        monkeypatch,
        _FakeResp(503, "no endpoints", {"X-Fission-Request-ID": "req-123"}))
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/functions/ns1/hello/test", json={"body": "x"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == 503
    assert data["requestId"] == "req-123"
    assert data["component"] == "router"
    assert "特化" in data["reason"]
    assert captured["url"].endswith("/fission-function/ns1/hello/")


def test_function_test_attribution_timeout(monkeypatch):
    import httpx as _httpx
    f = FakeK8s()
    monkeypatch.setattr(k8s_mod, "request_k8s", f.request_k8s)
    _patch_http(monkeypatch, exc=_httpx.TimeoutException("timed out"))
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/functions/ns1/hello/test", json={"timeoutSeconds": 5})
    data = r.json()
    assert data["status"] is None
    assert data["component"] == "router"
    assert "timeout" in data["reason"] and "60s" in data["reason"]


def test_function_test_ok_captures_request_id(monkeypatch):
    f = FakeK8s()
    monkeypatch.setattr(k8s_mod, "request_k8s", f.request_k8s)
    _patch_http(monkeypatch, _FakeResp(200, "hi", {"X-Fission-Request-ID": "rid"}))
    app = create_app()
    with TestClient(app) as client:
        data = client.post("/api/functions/ns1/hello/test", json={}).json()
    assert data["component"] == "function" and data["reason"] == "ok"
    assert data["requestId"] == "rid"
    assert data["body"] == "hi"


# ---------------------------------------------------------------- 权重校验
def test_weights_reject_non_100(fake):
    f, client = fake
    r = client.put("/api/httptriggers/ns1/t1/weights", json={"weights": {"a": 50}})
    assert r.status_code == 422
    assert "100" in r.json()["detail"]["message"]


def test_weights_ok_writes_spec(fake):
    f, client = fake
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functions/a", lambda p, b, r: (200, {}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functions/b", lambda p, b, r: (200, {}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/httptriggers/t1",
         lambda p, b, r: (200, {"metadata": {"name": "t1"}, "spec": {"relativeUrl": "/t"}}))
    seen = {}
    f.on("PUT", "/apis/fission.io/v1/namespaces/ns1/httptriggers/t1",
         lambda p, b, r: seen.update(b["spec"]) or (200, b))
    r = client.put("/api/httptriggers/ns1/t1/weights", json={"weights": {"a": 60, "b": 40}})
    assert r.status_code == 200
    assert seen["functionWeights"] == {"a": 60, "b": 40}


def test_weights_missing_function_404(fake):
    f, client = fake
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functions/a",
         lambda p, b, r: (404, {"message": "not found"}))
    r = client.put("/api/httptriggers/ns1/t1/weights", json={"weights": {"a": 100}})
    assert r.status_code == 404


# ---------------------------------------------------------------- canary 预检
CANARY_SPEC = {"newfunction": "n1", "oldfunction": "o1", "trigger": "t1",
               "weightincrement": 10, "duration": "2m", "failurethreshold": 3,
               "failureType": "failure"}


def test_canary_create_missing_fields_422(fake):
    f, client = fake
    k8s_mod._feat_cache.update({"ts": _time.time() + 1e6, "available": {"canaryconfigs"}})
    r = client.post("/api/canaryconfigs", json={"metadata": {"namespace": "fission"}, "spec": {}})
    assert r.status_code == 422
    assert "缺少必填字段" in r.json()["detail"]["message"]


def test_canary_create_ok_and_bad_failure_type(fake):
    f, client = fake
    k8s_mod._feat_cache.update({"ts": _time.time() + 1e6, "available": {"canaryconfigs"}})
    f.on("GET", "/apis/fission.io/v1/namespaces/fission/functions/n1", lambda p, b, r: (200, {}))
    f.on("GET", "/apis/fission.io/v1/namespaces/fission/functions/o1", lambda p, b, r: (200, {}))
    f.on("POST", "/apis/fission.io/v1/namespaces/fission/canaryconfigs",
         lambda p, b, r: (201, b))
    obj = {"metadata": {"name": "c1", "namespace": "fission"}, "spec": dict(CANARY_SPEC)}
    assert client.post("/api/canaryconfigs", json=obj).status_code == 201
    bad = {"metadata": {"name": "c2", "namespace": "fission"},
           "spec": dict(CANARY_SPEC, failureType="explode")}
    r = client.post("/api/canaryconfigs", json=bad)
    assert r.status_code == 422


def test_canary_status_reads_weights(fake):
    f, client = fake
    k8s_mod._feat_cache.update({"ts": _time.time() + 1e6, "available": {"canaryconfigs"}})
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/canaryconfigs/c1",
         lambda p, b, r: (200, {"metadata": {"name": "c1"},
                                "spec": {"trigger": "t1", "weightincrement": 10},
                                "status": {"status": "progressing", "failures": 2}}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/httptriggers/t1",
         lambda p, b, r: (200, {"spec": {"functionWeights": {"n1": 30, "o1": 70}}}))
    data = client.get("/api/canaryconfigs/ns1/c1/status").json()
    assert data["stage"] == "progressing"
    assert data["functionWeights"] == {"n1": 30, "o1": 70}
    assert data["failures"] == 2


# ---------------------------------------------------------------- policy 403 / 危险字段
def test_env_write_requires_admin(fake):
    f, client = fake
    f.on("POST", "/apis/fission.io/v1/namespaces/fission/environments",
         lambda p, b, r: (201, b))
    obj = {"metadata": {"name": "e1", "namespace": "fission"}, "spec": {}}
    r = client.post("/api/resources/environments", json=obj)
    assert r.status_code == 403
    assert r.json()["detail"]["message"] == "Environment 修改仅限平台管理员"
    # 带管理员组头放行
    r = client.post("/api/resources/environments", json=obj,
                    headers={"X-authentik-groups": "fission-admins"})
    assert r.status_code == 201


def test_env_delete_requires_admin(fake):
    f, client = fake
    r = client.delete("/api/resources/environments/fission/e1")
    assert r.status_code == 403


def test_mqt_namespace_mismatch_rejected(fake):
    f, client = fake
    _enable_features(f, "messagequeuetriggers")
    obj = {"metadata": {"name": "m1", "namespace": "ns1"},
           "spec": {"namespace": "other", "functionref": "f1"}}
    r = client.post("/api/resources/messagequeuetriggers", json=obj)
    assert r.status_code == 422
    assert "spec.namespace" in r.json()["detail"]["message"]


def test_mqt_missing_secret_rejected(fake):
    f, client = fake
    _enable_features(f, "messagequeuetriggers")
    f.on("GET", "/api/v1/namespaces/ns1/secrets/nope",
         lambda p, b, r: (404, {"message": "missing"}))
    obj = {"metadata": {"name": "m1", "namespace": "ns1"},
           "spec": {"secret": "nope"}}
    r = client.post("/api/resources/messagequeuetriggers", json=obj)
    assert r.status_code == 422
    assert "Secret" in r.json()["detail"]["message"]


def test_mqt_valid_secret_passes(fake):
    f, client = fake
    _enable_features(f, "messagequeuetriggers")
    f.on("GET", "/api/v1/namespaces/ns1/secrets/ok", lambda p, b, r: (200, {}))
    f.on("POST", "/apis/fission.io/v1/namespaces/ns1/messagequeuetriggers",
         lambda p, b, r: (201, b))
    obj = {"metadata": {"name": "m1", "namespace": "ns1"}, "spec": {"secret": "ok"}}
    assert client.post("/api/resources/messagequeuetriggers", json=obj).status_code == 201


def test_k8s_403_rbac_message(fake):
    f, client = fake
    f.on("GET", "/apis/fission.io/v1/namespaces/x/functions/nope",
         lambda p, b, r: (403, {"message": "forbidden: User cannot list"}))
    r = client.get("/api/resources/functions/x/nope")
    assert r.status_code == 403
    assert "RBAC" in r.json()["detail"]["message"]


# ---------------------------------------------------------------- audit
def _stub_audit_cm(f):
    """有状态审计 CM：POST/PUT configmaps 更新内存，GET audit CM 返回最新内容。"""
    state = {"cm": None}

    def get_audit(path, b, r):
        if state["cm"] is None:
            return 404, {"message": "missing"}
        return 200, state["cm"]

    def write_cm(path, b, r):
        if isinstance(b, dict) and (b.get("metadata") or {}).get("name") == "fission-console-audit":
            state["cm"] = b
        return 201, b

    def put_cm(path, b, r):
        if isinstance(b, dict) and (b.get("metadata") or {}).get("name") == "fission-console-audit":
            state["cm"] = b
        return 200, b

    f.on("GET", "/api/v1/namespaces/fission/configmaps/fission-console-audit", get_audit)
    f.on("POST", "/api/v1/namespaces/fission/configmaps", write_cm)
    f.on("PUT", "/api/v1/namespaces/fission/configmaps/fission-console-audit", put_cm)


def test_audit_appends_on_write(fake):
    f, client = fake
    _stub_audit_cm(f)
    f.on("GET", "/api/v1/namespaces/fission/configmaps/fission-console-testdata",
         lambda p, b, r: (404, {"message": "missing"}))
    assert client.put("/api/testdata", json={"collections": [], "environments": []}).status_code == 200
    data = client.get("/api/audit").json()
    assert len(data["items"]) == 1
    rec = data["items"][0]
    assert rec["user"] == "anonymous"
    assert rec["kind"] == "testdata"
    assert rec["action"].startswith("PUT /api/testdata")
    assert "ts" in rec and "summary" in rec


def test_audit_records_authenticated_user(fake):
    f, client = fake
    _stub_audit_cm(f)
    f.on("GET", "/api/v1/namespaces/fission/configmaps/fission-console-testdata",
         lambda p, b, r: (404, {"message": "missing"}))
    client.put("/api/testdata", json={"collections": [], "environments": []},
               headers={"X-authentik-username": "alice"})
    assert client.get("/api/audit").json()["items"][0]["user"] == "alice"


# ---------------------------------------------------------------- prom 白名单
def test_prom_unconfigured_501(fake, monkeypatch):
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    f, client = fake
    r = client.get("/api/prom/fission_function_cold_starts_total")
    assert r.status_code == 501
    assert r.json()["detail"]["feature"] == "prometheus"


def test_prom_whitelist_rejects_other_expressions(fake, monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom:9090")
    f, client = fake
    for q in ["evil_metric", "up;drop", "custom_metric{a='b'}",
              "fission_function_cold_starts_total or another_metric"]:
        r = client.get("/api/prom/" + q)
        assert r.status_code == 400, q
    # 白名单内：fission 冷启动、up、process_*
    import httpx as _httpx

    class FakePromResp:
        status_code = 200
        content = b'{"status":"success","data":{"result":[]}}'

        def json(self):
            return {"status": "success", "data": {"result": []}}

    async def fake_get(self, url, **kw):
        fake_get.url = url
        fake_get.params = kw.get("params")
        return FakePromResp()

    monkeypatch.setattr(_httpx.AsyncClient, "get", fake_get)
    for q in ["fission_function_cold_starts_total",
              "fission_function_cold_start_errors_total{function='hello'}",
              "up", "process_cpu_seconds_total"]:
        r = client.get("/api/prom/" + q)
        assert r.status_code == 200, q
    assert fake_get.params["query"] == "process_cpu_seconds_total"


# ---------------------------------------------------------------- feature 未启用 501
def test_feature_disabled_returns_501(fake):
    f, client = fake
    _enable_features(f, "functions")  # 不含 canaryconfigs
    r = client.get("/api/resources/canaryconfigs")
    assert r.status_code == 501
    detail = r.json()["detail"]
    assert detail["feature"] == "canaryconfigs"
    assert "集群未启用" in detail["message"]


def test_feature_enabled_list_ok(fake):
    f, client = fake
    _enable_features(f, "canaryconfigs", "functionversions", "functionalias")
    f.on("GET", "/apis/fission.io/v1/canaryconfigs",
         lambda p, b, r: (200, {"items": [{"metadata": {"name": "c1"}}]}))
    r = client.get("/api/resources/canaryconfigs")
    assert r.status_code == 200
    assert r.json()["items"][0]["metadata"]["name"] == "c1"


def test_versions_requires_feature(fake):
    f, client = fake
    _enable_features(f, "canaryconfigs")  # 缺 functionversions
    r = client.get("/api/functions/ns1/hello/versions")
    assert r.status_code == 501


def test_versions_lists_with_existence(fake):
    f, client = fake
    _enable_features(f, "functionversions", "functionalias")
    f.on("GET", "/apis/fission.io/v1/functionversions", lambda p, b, r: (200, {"items": [
        {"metadata": {"name": "v2", "namespace": "ns1", "creationTimestamp": "2026-01-02T00:00:00Z"},
         "spec": {}},
        {"metadata": {"name": "v1", "namespace": "ns1", "creationTimestamp": "2026-01-01T00:00:00Z"},
         "spec": {}},
    ]}))
    f.on("GET", "/apis/fission.io/v1/functionalias", lambda p, b, r: (200, {"items": [
        {"metadata": {"name": "stable", "namespace": "ns1"}, "spec": {"target": "hello"}},
    ]}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functions/v2",
         lambda p, b, r: (200, {}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functions/v1",
         lambda p, b, r: (404, {"message": "gone"}))
    data = client.get("/api/functions/ns1/hello/versions").json()
    assert [v["name"] for v in data["versions"]] == ["v2", "v1"]  # creationTimestamp 降序
    assert data["versions"][0]["functionExists"] is True
    assert data["versions"][1]["functionExists"] is False
    assert data["aliases"][0]["target"] == "hello"


def test_rollback_moves_alias_target(fake):
    f, client = fake
    _enable_features(f, "functionversions", "functionalias")
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functionversions/v1",
         lambda p, b, r: (200, {"metadata": {"name": "v1"}, "spec": {"functionName": "hello-v1"}}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functions/hello-v1", lambda p, b, r: (200, {}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functionalias/stable",
         lambda p, b, r: (200, {"metadata": {"name": "stable"}, "spec": {"target": "hello"}}))
    seen = {}
    f.on("PUT", "/apis/fission.io/v1/namespaces/ns1/functionalias/stable",
         lambda p, b, r: seen.update(b["spec"]) or (200, b))
    r = client.post("/api/functions/ns1/hello/rollback",
                    json={"alias": "stable", "toVersion": "v1"})
    assert r.status_code == 200
    data = r.json()
    assert seen["target"] == "hello-v1"
    assert data["coldStartWarning"] == "目标版本若无 warm Pod，回滚后首调将经历一次普通冷启动"


def test_rollback_missing_version_404(fake):
    f, client = fake
    _enable_features(f, "functionversions", "functionalias")
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functionversions/nope",
         lambda p, b, r: (404, {"message": "missing"}))
    r = client.post("/api/functions/ns1/hello/rollback",
                    json={"alias": "stable", "toVersion": "nope"})
    assert r.status_code == 404


# ---------------------------------------------------------------- mesh / mcp
def test_mesh_probe(fake):
    f, client = fake
    f.on("GET", "/api/v1/namespaces/istio-system", lambda p, b, r: (200, {}))
    f.on("GET", "/api/v1/namespaces/linkerd", lambda p, b, r: (404, {"message": "not found"}))
    data = client.get("/api/mesh/probe").json()
    assert data == {"istio": True, "linkerd": False}


def test_mesh_probe_permission_denied_unknown(fake):
    f, client = fake
    f.on("GET", "/api/v1/namespaces/istio-system", lambda p, b, r: (403, {"message": "denied"}))
    f.on("GET", "/api/v1/namespaces/linkerd", lambda p, b, r: (403, {"message": "denied"}))
    data = client.get("/api/mesh/probe").json()
    assert data["istio"] is None and data["linkerd"] is None


def test_mcp_tools_scans_annotations(fake):
    f, client = fake
    f.on("GET", "/apis/fission.io/v1/functions", lambda p, b, r: (200, {"items": [
        {"metadata": {"name": "t1", "namespace": "ns1",
                      "annotations": {"fission.io/mcp-enabled": "true",
                                      "fission.io/mcp-tool-description": "d",
                                      "fission.io/mcp-input-schema": '{"type":"object"}'}}},
        {"metadata": {"name": "t2", "namespace": "ns1", "annotations": {}}},
    ]}))
    tools = client.get("/api/mcp/tools").json()["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "t1" and tools[0]["inputSchema"] == {"type": "object"}


def test_mcp_put_writes_annotations(fake):
    f, client = fake
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functions/t1",
         lambda p, b, r: (200, {"metadata": {"name": "t1"}, "spec": {}}))
    seen = {}
    f.on("PUT", "/apis/fission.io/v1/namespaces/ns1/functions/t1",
         lambda p, b, r: seen.update(b["metadata"]["annotations"]) or (200, b))
    r = client.put("/api/functions/ns1/t1/mcp",
                   json={"enabled": True, "toolDescription": "d", "inputSchema": {"type": "object"}})
    assert r.status_code == 200
    assert seen["fission.io/mcp-enabled"] == "true"
    assert seen["fission.io/mcp-tool-description"] == "d"
    assert '{"type": "object"}' == seen["fission.io/mcp-input-schema"]


def test_mcp_token_issue_and_revoke(fake):
    f, client = fake
    f.on("GET", "/api/v1/namespaces/fission/secrets/fission-console-mcp-keys",
         lambda p, b, r: (404, {"message": "missing"}))
    f.on("POST", "/api/v1/namespaces/fission/secrets", lambda p, b, r: (201, b))
    r = client.post("/api/mcp/tokens",
                    json={"name": "bot", "allowedNamespaces": ["ns1"], "ttlDays": 7})
    assert r.status_code == 201
    token = r.json()["token"]
    assert token.count(".") == 2
    # JWT 可用自实现 verify 校验（密钥即 Secret 中保存的那个）
    posted = [c for c in f.calls if c[0] == "POST" and "/secrets" in c[1]][0]
    import base64 as b64
    data = posted[2]["data"]
    key = b64.b64decode(data["hs256-key"]).decode()
    from server.services import mcptoken
    claim = mcptoken.verify(token, key)
    assert claim["sub"] == "bot"
    assert claim["allowed_namespaces"] == ["ns1"]
    # 吊销
    f.on("GET", "/api/v1/namespaces/fission/secrets/fission-console-mcp-keys",
         lambda p, b, r: (200, {"metadata": {"resourceVersion": "1"}, "data": data}))
    f.on("PUT", "/api/v1/namespaces/fission/secrets/fission-console-mcp-keys",
         lambda p, b, r: (200, b))
    assert client.delete("/api/mcp/tokens/bot").status_code == 200
    put_calls = [c for c in f.calls if c[0] == "PUT" and "mcp-keys" in c[1]]
    assert "token-bot" not in put_calls[-1][2]["data"]


def test_mcp_token_revoke_missing_404(fake):
    f, client = fake
    f.on("GET", "/api/v1/namespaces/fission/secrets/fission-console-mcp-keys",
         lambda p, b, r: (404, {"message": "missing"}))
    assert client.delete("/api/mcp/tokens/ghost").status_code == 404


def test_mcp_config_probe_failure_defaults_disabled(fake):
    f, client = fake
    f.on("GET", "/api/v1/namespaces/fission/configmaps/fission-controller",
         lambda p, b, r: (404, {"message": "missing"}))
    f.on("GET", "/api/v1/namespaces/fission/configmaps/fission-config",
         lambda p, b, r: (404, {"message": "missing"}))
    data = client.get("/api/mcp/config").json()
    assert data["enabled"] is False
    assert data["source"] == "probe-failed"
    assert "message" in data


def test_mcp_config_probe_success(fake):
    f, client = fake
    f.on("GET", "/api/v1/namespaces/fission/configmaps/fission-controller",
         lambda p, b, r: (200, {"data": {"mcp.enabled": "true"}}))
    data = client.get("/api/mcp/config").json()
    assert data == {"enabled": True, "source": "configmap/fission-controller"}

