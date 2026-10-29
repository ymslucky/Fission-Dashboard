# -*- coding: utf-8 -*-
"""批 2 集成模拟：FunctionVersion 列表、Alias 切换（回滚）、501 降级、RBAC、竞态 409。

现实约束：线上集群（Fission 1.27.0）未注册 functionversions/functionalias CRD →
线上全部走 501 降级。本批 L3 主体在 mock K8s **存在**这两个 CRD 的前提下，
测 BFF（server/routers/versions.py）解析与回滚逻辑；501 降级另设 2 条用例。

沿用 test_api.FakeK8s 打桩；versions.py 读取路径（源码确认）：
- 列表：GET /apis/fission.io/v1/functionversions（集群级）+ 逐版本 GET functions/{target_fn}
- FunctionVersion→Function 名解析顺序：spec.functionName → labels.functionName →
  spec.function → metadata.name（_fv_function_name / explicit 同逻辑）
- 回滚：GET functionversions/{v} → GET functions/{fn} → GET functionalias/{alias}
  → PUT functionalias/{alias}（整对象回写）
"""
from __future__ import annotations

import time as _time

import pytest
from fastapi.testclient import TestClient

from server import k8s as k8s_mod
from server.main import create_app
from server.tests.test_api import FakeK8s

COLD = "目标版本若无 warm Pod，回滚后首调将经历一次普通冷启动"


@pytest.fixture
def f(monkeypatch):
    fk = FakeK8s()
    monkeypatch.setattr(k8s_mod, "request_k8s", fk.request_k8s)
    k8s_mod._feat_cache.update({"ts": 0.0, "available": set()})
    return fk


def _client():
    return TestClient(create_app())


def _enable_features(*kinds):
    k8s_mod._feat_cache.update({"ts": _time.time() + 1e6, "available": set(kinds)})


def _setup_versions(f, *, items=None, alias_items=None, fn_exists=("hello",)):
    """默认：v1（spec.functionName 形态，Function 存在）+ v2（同名兜底形态，Function 缺失）。"""
    if items is None:
        items = [
            {"metadata": {"name": "v2", "namespace": "ns1",
                          "creationTimestamp": "2026-01-02T00:00:00Z"},
             "spec": {}},  # 无显式 functionName → FunctionVersion 名即函数名（不存在）
            {"metadata": {"name": "v1", "namespace": "ns1",
                          "creationTimestamp": "2026-01-01T00:00:00Z"},
             "spec": {"functionName": "hello"}},  # 显式 functionName 形态
        ]
    if alias_items is None:
        alias_items = [
            {"metadata": {"name": "prod", "namespace": "ns1",
                          "creationTimestamp": "2026-01-01T00:00:00Z"},
             "spec": {"target": "hello"}},
        ]
    f.on("GET", "/apis/fission.io/v1/functionversions",
         lambda p, b, r: (200, {"items": items}))
    f.on("GET", "/apis/fission.io/v1/functionalias",
         lambda p, b, r: (200, {"items": alias_items}))
    for fn in fn_exists:
        f.on("GET", f"/apis/fission.io/v1/namespaces/ns1/functions/{fn}",
             lambda p, b, r: (200, {"metadata": {"name": "x"}}))


# ---------------------------------------------------------------- 用例 1 versions-happy
def test_versions_happy_two_fv_one_alias(f):
    """v1（spec.functionName=hello，Function 存在）+ v2（同名兜底，无对应 Function）
    + alias prod→hello。v2 functionExists=false；按 created 降序。"""
    _enable_features("functionversions", "functionalias")
    _setup_versions(f)
    data = _client().get("/api/functions/ns1/hello/versions").json()
    assert [v["name"] for v in data["versions"]] == ["v2", "v1"]
    assert data["versions"][0] == {
        "name": "v2", "created": "2026-01-02T00:00:00Z",
        "function": "v2", "functionExists": False}
    assert data["versions"][1]["functionExists"] is True
    assert data["versions"][1]["function"] == "hello"
    assert data["aliases"] == [{"name": "prod", "target": "hello",
                                "created": "2026-01-01T00:00:00Z"}]


# ---------------------------------------------------------------- 用例 2 versions-empty
def test_versions_empty_items(f):
    """CRD 存在但 items:[] → {versions:[], aliases:[]}。"""
    _enable_features("functionversions", "functionalias")
    _setup_versions(f, items=[], alias_items=[])
    data = _client().get("/api/functions/ns1/hello/versions").json()
    assert data == {"versions": [], "aliases": []}


# ---------------------------------------------------------------- 用例 3 versions-501
def test_versions_501_feature_missing(f):
    """_feat_cache 复位且 /apis/fission.io/v1 不含 functionversions → 501 + feature 键。"""
    # 不 enable 特性；无 /apis 路由 → 探测结果为空集合
    r = _client().get("/api/functions/ns1/hello/versions")
    assert r.status_code == 501
    detail = r.json()["detail"]
    assert detail["message"] == "集群未启用 functionversions CRD"
    assert detail["feature"] == "functionversions"


# ---------------------------------------------------------------- 用例 4 rollback-happy
def test_rollback_happy_alias_put(f):
    """POST {alias:prod, toVersion:v2} → FunctionAlias PUT 发生（FakeK8s 捕获），
    响应含 coldStartWarning。v2 为同名兜底形态（spec.functionName 缺失）。"""
    _enable_features("functionversions", "functionalias")
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functionversions/v2",
         lambda p, b, r: (200, {"metadata": {"name": "v2", "namespace": "ns1"}, "spec": {}}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functions/v2",
         lambda p, b, r: (200, {}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functionalias/prod",
         lambda p, b, r: (200, {"metadata": {"name": "prod", "namespace": "ns1"},
                                "spec": {"target": "hello"}}))
    seen = {}
    f.on("PUT", "/apis/fission.io/v1/namespaces/ns1/functionalias/prod",
         lambda p, b, r: seen.update(b["spec"]) or (200, b))
    r = _client().post("/api/functions/ns1/hello/rollback",
                       json={"alias": "prod", "toVersion": "v2"})
    assert r.status_code == 200
    data = r.json()
    assert seen["target"] == "v2"           # 指针已从 hello → v2
    assert data["coldStartWarning"] == COLD
    assert data["ok"] is True and data["version"] == "v2"


# ---------------------------------------------------------------- 用例 5 rollback-version-not-exist
def test_rollback_version_not_exist_404(f):
    """toVersion 不在 versions（GET 404）→ 404，message 含版本名。"""
    _enable_features("functionversions", "functionalias")
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functionversions/ghost",
         lambda p, b, r: (404, {"message": "not found"}))
    r = _client().post("/api/functions/ns1/hello/rollback",
                       json={"alias": "prod", "toVersion": "ghost"})
    assert r.status_code == 404
    assert "ghost" in r.json()["detail"]["message"]


# ---------------------------------------------------------------- 用例 6 rollback-alias-not-exist
def test_rollback_alias_not_exist_404(f):
    """alias 名不存在（GET functionalias 404）→ 404，message 含别名名。"""
    _enable_features("functionversions", "functionalias")
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functionversions/v2",
         lambda p, b, r: (200, {"metadata": {"name": "v2"}, "spec": {"functionName": "hello"}}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functions/hello", lambda p, b, r: (200, {}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functionalias/prod",
         lambda p, b, r: (404, {"message": "not found"}))
    r = _client().post("/api/functions/ns1/hello/rollback",
                       json={"alias": "prod", "toVersion": "v2"})
    assert r.status_code == 404
    assert "prod" in r.json()["detail"]["message"]


# ---------------------------------------------------------------- 用例 7 rollback-501
def test_rollback_501_feature_missing(f):
    """特性缺失 → 501 + feature 键。"""
    r = _client().post("/api/functions/ns1/hello/rollback",
                       json={"alias": "prod", "toVersion": "v2"})
    assert r.status_code == 501
    assert r.json()["detail"]["feature"] == "functionversions"


# ---------------------------------------------------------------- 用例 8 versions-403
def test_versions_403_rbac_prefix(f):
    """GET functionversions → 403 → message 以 'Kubernetes RBAC 拒绝' 开头。"""
    _enable_features("functionversions", "functionalias")
    f.on("GET", "/apis/fission.io/v1/functionversions",
         lambda p, b, r: (403, {"message": "forbidden: User cannot list resource"}))
    r = _client().get("/api/functions/ns1/hello/versions")
    assert r.status_code == 403
    assert r.json()["detail"]["message"].startswith("Kubernetes RBAC 拒绝")


# ---------------------------------------------------------------- 用例 9 竞态 409
def test_rollback_conflict_409_passthrough(f):
    """回滚时 FunctionAlias 已被并发改动 → PUT 409 → message 透传（resourceVersion 冲突）。"""
    _enable_features("functionversions", "functionalias")
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functionversions/v2",
         lambda p, b, r: (200, {"metadata": {"name": "v2"}, "spec": {"functionName": "hello"}}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functions/hello", lambda p, b, r: (200, {}))
    f.on("GET", "/apis/fission.io/v1/namespaces/ns1/functionalias/prod",
         lambda p, b, r: (200, {"metadata": {"name": "prod", "resourceVersion": "1"},
                                "spec": {"target": "hello"}}))
    f.on("PUT", "/apis/fission.io/v1/namespaces/ns1/functionalias/prod",
         lambda p, b, r: (409, {"message": "Operation cannot be fulfilled on functionalias"
                                          " prod: the object has been modified"}))
    r = _client().post("/api/functions/ns1/hello/rollback",
                       json={"alias": "prod", "toVersion": "v2"})
    assert r.status_code == 409
    assert "has been modified" in r.json()["detail"]["message"]


# ---------------------------------------------------------------- 用例 10 字段边界
def test_versions_missing_creation_timestamp(f):
    """FunctionVersion 缺 creationTimestamp → created 输出空串不崩。"""
    _enable_features("functionversions", "functionalias")
    _setup_versions(
        f,
        items=[{"metadata": {"name": "v1", "namespace": "ns1"},
                "spec": {"functionName": "hello"}}],
        fn_exists=("hello",))
    data = _client().get("/api/functions/ns1/hello/versions").json()
    assert data["versions"][0]["created"] == ""
    assert data["versions"][0]["functionExists"] is True
