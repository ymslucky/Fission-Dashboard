# ---------- request routing handle() ----------
def handle(method, path, query, body_bytes):
    """Framework-agnostic entry: returns (status, content_type, payload_bytes)."""
    parts = [p for p in path.split("/") if p]
    qs = urllib.parse.parse_qs(query)

    def J(code, obj):
        return code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode()

    try:
        if path == "/healthz":
            return 200, "application/json", b'{"ok": true}'
        if not parts or parts[0] != "api":
            raise ApiError(404, {"message": "not found (static files are served via /static/*)"})
        if method == "GET":
            if len(parts) == 2 and parts[1] == "overview":
                return J(200, overview())
            if len(parts) == 3 and parts[1] == "metrics":
                return J(200, metrics_snapshot())
            kind = parts[1]
            if kind not in KINDS:
                raise ApiError(404, {"message": f"unknown kind {kind}"})
            if len(parts) == 2:
                return J(200, {"items": crd_list(kind)})
            if kind == "functions" and len(parts) == 5 and parts[4] == "logs":
                return J(200, function_logs(parts[2], parts[3], (qs.get("pod") or [None])[0]))
            if len(parts) == 4:
                return J(200, crd_get(kind, parts[2], parts[3]))
            raise ApiError(404, {"message": "not found"})
        if method == "POST":
            try:
                body = json.loads(body_bytes) if body_bytes else {}
            except Exception:
                raise ApiError(400, {"message": "invalid JSON body"})
            if parts[:2] == ["api", "invoke"]:
                return J(200, invoke(body["ns"], body["trigger"], body.get("method"),
                                     body.get("headers"), body.get("body")))
            if parts[:2] == ["api", "gitops"]:
                if len(parts) == 4 and parts[2] == "plan":
                    cfg = _load_sources()
                    src = next((s for s in cfg["sources"] if s["name"] == parts[3]), None)
                    if not src: raise ApiError(404, {"message": "source not found"})
                    return J(200, gh_plan(src))
                if len(parts) == 4 and parts[2] == "sync":
                    cfg = _load_sources()
                    src = next((s for s in cfg["sources"] if s["name"] == parts[3]), None)
                    if not src: raise ApiError(404, {"message": "source not found"})
                    return J(200, gh_apply(src))
                raise ApiError(404, {"message": "not found"})
            kind = parts[1]
            if kind not in KINDS:
                raise ApiError(404, {"message": f"unknown kind {kind}"})
            return J(201, crd_create(kind, body))
        if method in ("PUT", "DELETE"):
            kind = parts[1] if len(parts) > 1 else ""
            if kind not in KINDS:
                raise ApiError(404, {"message": f"unknown kind {kind}"})
            try:
                body = json.loads(body_bytes) if body_bytes else {}
            except Exception:
                raise ApiError(400, {"message": "invalid JSON body"})
            if method == "PUT":
                ns = body.get("metadata", {}).get("namespace", parts[2])
                return J(200, crd_update(kind, parts[2], parts[3], body))
            return J(200, crd_delete(kind, parts[2], parts[3]))
        raise ApiError(405, {"message": "method not allowed"})
    except ApiError as e:
        return J(e.code, e.body)
    except Exception as e:
        return J(500, {"message": f"{type(e).__name__}: {e}"})

# ---------- config endpoints (gitops sources CRUD via configmap) ----------
def handle_config(method, body_bytes):
    try:
        if method == "GET":
            return 200, "application/json; charset=utf-8", json.dumps(_load_sources(), ensure_ascii=False).encode()
        cfg = json.loads(body_bytes) if body_bytes else None
        if cfg is None: raise ApiError(400, {"message": "invalid body"})
        _save_sources(cfg)
        return 200, "application/json; charset=utf-8", json.dumps(cfg, ensure_ascii=False).encode()
    except ApiError as e:
        return e.code, "application/json; charset=utf-8", json.dumps(e.body).encode()
    except Exception as e:
        return 500, "application/json; charset=utf-8", json.dumps({"message": str(e)}).encode()
