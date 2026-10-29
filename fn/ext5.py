def handle_op(op, a):
    """Single-endpoint RPC used by the SPA. Returns (status, ctype, bytes)."""
    def J(code, obj):
        return code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode()
    try:
        r2 = handle_op2(op, a)
        if r2: return r2
        if op == "overview": return J(200, overview())
        if op == "metrics": return J(200, metrics_snapshot())
        if op == "list": return J(200, {"items": crd_list(a["kind"])})
        if op == "get": return J(200, crd_get(a["kind"], a["ns"], a["name"]))
        if op == "create": return J(201, crd_create(a["kind"], a["obj"]))
        if op == "update": return J(200, crd_update(a["kind"], a["ns"], a["name"], a["obj"]))
        if op == "delete": return J(200, crd_delete(a["kind"], a["ns"], a["name"]))
        if op == "logs": return J(200, function_logs(a["ns"], a["name"], a.get("pod")))
        if op == "invoke":
            return J(200, invoke(a["ns"], a["trigger"], a.get("method"), a.get("headers"),
                                 a.get("body"), a.get("query")))
        if op == "config_get": return J(200, _load_sources())
        if op == "config_put":
            _save_sources(a["cfg"]); return J(200, a["cfg"])
        if op in ("gitops_plan", "gitops_sync"):
            cfg = _load_sources()
            src = next((s for s in cfg["sources"] if s["name"] == a["name"]), None)
            if not src: raise ApiError(404, {"message": "source not found"})
            return J(200, gh_plan(src) if op == "gitops_plan" else gh_apply(src))
        raise ApiError(404, {"message": f"unknown op {op}"})
    except ApiError as e:
        return J(e.code, e.body)
    except Exception as e:
        return J(500, {"message": f"{type(e).__name__}: {e}"})
