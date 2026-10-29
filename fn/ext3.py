CONFIG_CM = "fission-console-config"
CONFIG_NS = "fission"
# ---------- github sync ----------
GH_API = "https://api.github.com"

def _load_sources():
    code, body, _ = k8s("GET", f"/api/v1/namespaces/{CONFIG_NS}/configmaps/{CONFIG_CM}")
    if code != 200:
        return {"sources": []}
    try:
        return json.loads(body["data"].get("sources.json", '{"sources": []}'))
    except Exception:
        return {"sources": []}

def _save_sources(cfg):
    cm = {"apiVersion": "v1", "kind": "ConfigMap",
          "metadata": {"name": CONFIG_CM, "namespace": CONFIG_NS,
                       "labels": {"app": "fission-console"}},
          "data": {"sources.json": json.dumps(cfg, ensure_ascii=False)}}
    code, body, _ = k8s("GET", f"/api/v1/namespaces/{CONFIG_NS}/configmaps/{CONFIG_CM}")
    if code == 200:
        cm["metadata"].setdefault("resourceVersion", body["metadata"]["resourceVersion"])
        code, _, _ = k8s("PUT", f"/api/v1/namespaces/{CONFIG_NS}/configmaps/{CONFIG_CM}", cm)
    else:
        code, _, _ = k8s("POST", f"/api/v1/namespaces/{CONFIG_NS}/configmaps", cm)
    if code not in (200, 201):
        raise ApiError(code, {"message": "failed to save config"})

def gh_fetch_tree(src):
    """Read repo via Git Trees/Blobs API (fresh, no CDN cache)."""
    repo = src["repo"].strip()
    if repo.endswith(".git"): repo = repo[:-4]
    branch = src.get("branch") or "main"
    subdir = (src.get("path") or "").strip("/")
    token = token_for(src)
    code, ref = _gh_request("GET", f"/repos/{repo}/git/ref/heads/{branch}", token)
    if code != 200:
        raise ApiError(code, {"message": f"cannot read branch {branch}: {ref.get('message','')}"})
    head = ref["object"]["sha"]
    code, tree = _gh_request("GET", f"/repos/{repo}/git/trees/{head}?recursive=1", token)
    if code != 200:
        raise ApiError(code, {"message": tree.get("message", "cannot list tree")})
    files = {}
    for item in tree.get("tree", []):
        if item.get("type") != "blob": continue
        rel = item["path"]
        if subdir:
            if not rel.startswith(subdir + "/"): continue
            rel = rel[len(subdir) + 1:]
        if item.get("size", 0) > 512 * 1024: continue
        code, blob = _gh_request("GET", f"/repos/{repo}/git/blobs/{item['sha']}", token)
        if code != 200: continue
        try:
            import base64 as _b64
            files[rel] = _b64.b64decode(blob.get("content", ""))
        except Exception:
            continue
    return files

KIND_PLURAL = {"Function": "functions", "Package": "packages", "Environment": "environments",
               "HTTPTrigger": "httptriggers", "TimeTrigger": "timetriggers"}

def gh_plan(src):
    files = gh_fetch_tree(src)
    wanted = []
    for path, content in sorted(files.items()):
        if not path.endswith((".yaml", ".yml", ".json")): continue
        text = content.decode(errors="replace")
        try:
            docs = (json.loads(text) if path.endswith(".json") else miniyaml_load(text))
        except Exception as e:
            wanted.append({"path": path, "error": f"parse: {e}"}); continue
        if isinstance(docs, dict): docs = [docs]
        for doc in docs:
            if not isinstance(doc, dict): continue
            kind = doc.get("kind")
            if kind not in KIND_PLURAL: continue
            md = doc.get("metadata", {})
            wanted.append({"path": path, "kind": kind, "plural": KIND_PLURAL[kind],
                           "ns": md.get("namespace", "fission"), "name": md.get("name"),
                           "obj": doc})
    plan = {"repo": src["repo"], "branch": src.get("branch") or "main",
            "files": len(files), "create": [], "update": [], "unchanged": [], "errors": wanted}
    for w in wanted:
        if "error" in w or not w.get("name"): continue
        try:
            cur = crd_get(w["plural"], w["ns"], w["name"])
            cur.pop("metadata", None); desired = json.dumps(w["obj"], sort_keys=True)
            actual = json.dumps({**w["obj"], "metadata": {}}, sort_keys=True)
            live = dict(cur); live.pop("metadata", None)
            cur_obj = {k: live.get(k) for k in w["obj"] if k in live or k != "metadata"}
            same = json.dumps({k: cur.get(k) for k in w["obj"] if k != "metadata"}, sort_keys=True) == \
                   json.dumps({k: w["obj"].get(k) for k in w["obj"] if k != "metadata"}, sort_keys=True)
            (plan["unchanged"] if same else plan["update"]).append(
                {"path": w["path"], "kind": w["kind"], "ns": w["ns"], "name": w["name"]})
        except ApiError as e:
            if e.code == 404:
                plan["create"].append({"path": w["path"], "kind": w["kind"], "ns": w["ns"], "name": w["name"]})
            else:
                plan["errors"].append({"path": w["path"], "error": str(e.body)[:200]})
    return plan

def gh_apply(src, plan=None):
    if plan is None: plan = gh_plan(src)
    results = {"created": 0, "updated": 0, "errors": []}
    files = None
    for item in plan.get("create", []) + plan.get("update", []):
        try:
            if files is None: files = gh_fetch_tree(src)
            key = item["path"]
            text = files[key].decode(errors="replace")
            docs = json.loads(text) if key.endswith(".json") else miniyaml_load(text)
            if isinstance(docs, dict): docs = [docs]
            for doc in docs:
                if not isinstance(doc, dict) or doc.get("kind") != item["kind"]: continue
                md = doc.get("metadata", {})
                if md.get("name") != item["name"]: continue
                md["namespace"] = item["ns"]
                try:
                    crd_get(item["plural"], item["ns"], item["name"])
                    crd_update(item["plural"], item["ns"], item["name"], doc)
                    results["updated"] += 1
                except ApiError as e:
                    if e.code == 404:
                        crd_create(item["plural"], doc); results["created"] += 1
                    else: raise
        except Exception as e:
            results["errors"].append(f"{item.get('path')}: {type(e).__name__}: {e}")
    return results
