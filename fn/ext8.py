# ---------- v2: op routing + auto pull scheduler ----------
AUTO_STARTED = False
AUTO_LAST = {}

def handle_op2(op, a):
    try:
        def J(code, obj):
            return code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode()
        if op == "testdata_get": return J(200, load_testdata())
        if op == "testdata_put": save_testdata(a.get("data") or {}); return J(200, {"ok": True})
        if op == "source_token_put":
            token_save(a["name"], a.get("token", "")); return J(200, {"ok": True})
        if op == "gitops_tree": return J(200, gitops_tree(_src(a["name"])))
        if op == "gitops_file": return J(200, gitops_file(_src(a["name"]), a["path"]))
        if op == "gitops_status": return J(200, _statuses(_src(a["name"])))
        if op == "gitops_plan": return J(200, gitops_plan_v2(_src(a["name"])))
        if op == "gitops_apply":
            return J(200, gitops_apply_v2(_src(a["name"]), a.get("deletes"), a.get("conflicts")))
        if op == "gitops_export":
            return J(200, gitops_export(_src(a["name"]), a.get("mode"), a.get("resources")))
        return None
    except ApiError as e:
        return e.code, "application/json; charset=utf-8", json.dumps(e.body).encode()
    except Exception as e:
        return 500, "application/json; charset=utf-8", json.dumps({"message": f"{type(e).__name__}: {e}"}).encode()

def _src(name):
    cfg = _load_sources()
    src = next((s for s in cfg.get("sources", []) if s["name"] == name), None)
    if not src: raise ApiError(404, {"message": "source not found"})
    return src

def _statuses(src):
    plan = gitops_plan_v2(src)
    return {k: len(plan[k]) for k in ("create", "update", "delete", "conflict", "unchanged")}

def _auto_loop():
    while True:
        try:
            cfg = _load_sources()
            now = time.time()
            for src in cfg.get("sources", []):
                mins = int(src.get("autoMinutes") or 0)
                if mins <= 0: continue
                last = AUTO_LAST.get(src["name"], 0)
                if now - last >= mins * 60:
                    AUTO_LAST[src["name"]] = now
                    try: gitops_apply_v2(src, [], {})
                    except Exception: pass
        except Exception:
            pass
        time.sleep(60)

def start_auto():
    global AUTO_STARTED
    with _lock:
        if AUTO_STARTED: return
        AUTO_STARTED = True
        threading.Thread(target=_auto_loop, daemon=True).start()
