# ---------- v2: bidirectional gitops ----------
def _res_key(kind, ns, name):
    return f"{kind}/{ns}/{name}"

def _hash_obj(obj):
    m = obj.get("metadata", {})
    core = {"kind": obj.get("kind"), "apiVersion": obj.get("apiVersion"),
            "name": m.get("name"), "namespace": m.get("namespace"),
            "spec": obj.get("spec", {}), "labels": m.get("labels", {}),
            "annotations": m.get("annotations", {})}
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]

def _project(repo_obj, live_obj, source):
    """Project live onto the fields declared by the repo, ignoring server defaults."""
    md_r = repo_obj.get("metadata", {})
    md_l = live_obj.get("metadata", {})
    def proj(r, l):
        if isinstance(r, dict):
            if not isinstance(l, dict): return l
            return {k: proj(r[k], l.get(k)) for k in r}
        return l
    labels = proj(md_r.get("labels", {}), md_l.get("labels", {})) or {}
    labels[OWNER_LABEL] = source
    ann = proj(md_r.get("annotations", {}), md_l.get("annotations", {})) or {}
    return {"kind": repo_obj.get("kind"), "apiVersion": repo_obj.get("apiVersion"),
            "name": md_r.get("name"), "namespace": md_r.get("namespace"),
            "spec": proj(repo_obj.get("spec", {}), live_obj.get("spec", {})),
            "labels": labels, "annotations": ann}

def _norm_hash(obj, source):
    return _hash_obj(_project(obj, obj, source))

def _owned(res, source):
    return res.get("metadata", {}).get("labels", {}).get(OWNER_LABEL) == source

def _repo_resources(src):
    files = gh_fetch_tree(src)
    out = []
    for path, content in sorted(files.items()):
        if not path.endswith((".yaml", ".yml", ".json")): continue
        text = content.decode(errors="replace")
        try:
            docs = json.loads(text) if path.endswith(".json") else miniyaml_load(text)
        except Exception as e:
            out.append({"path": path, "error": f"parse: {e}"}); continue
        if isinstance(docs, dict): docs = [docs]
        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") not in KIND_PLURAL: continue
            md = doc.get("metadata", {})
            out.append({"path": path, "kind": doc["kind"], "plural": KIND_PLURAL[doc["kind"]],
                        "ns": md.get("namespace", "fission"), "name": md.get("name"), "obj": doc})
    return files, out

def gitops_tree(src):
    files, res = _repo_resources(src)
    state = load_state().get(src["name"], {})
    by_key = {_res_key(r["kind"], r["ns"], r["name"]): r for r in res if r.get("name")}
    status = []
    for key, r in by_key.items():
        rh = _norm_hash(r["obj"], src["name"]); base = state.get(key, {}).get("hash")
        try:
            live = crd_get(r["plural"], r["ns"], r["name"])
            lh = _hash_obj(_project(r["obj"], live, src["name"]))
        except ApiError as e:
            lh = None
        if lh is None: st = "repo-only"
        elif rh == lh: st = "synced"
        elif base and rh != base and lh != base: st = "conflict"
        elif base and lh != base: st = "cluster-changed"
        elif base and rh != base: st = "repo-changed"
        else: st = "synced" if rh == lh else "repo-changed"
        status.append({**{k: r[k] for k in ("path", "kind", "ns", "name")}, "state": st})
    return {"repo": src["repo"], "branch": src.get("branch") or "main",
            "files": [{"path": p, "size": len(c)} for p, c in sorted(files.items())],
            "resources": status}

def gitops_file(src, path):
    files, _ = _repo_resources(src)
    if path not in files: raise ApiError(404, {"message": "file not found"})
    return {"path": path, "content": files[path].decode(errors="replace")}

def gitops_plan_v2(src):
    files, res = _repo_resources(src)
    state = load_state().get(src["name"], {})
    live_owned = {}
    for kind, plural in KIND_PLURAL.items():
        try:
            for item in crd_list(plural):
                if _owned(item, src["name"]):
                    md = item["metadata"]
                    live_owned[_res_key(kind, md.get("namespace", "fission"), md["name"])] = item
        except ApiError:
            pass
    repo_map = {_res_key(r["kind"], r["ns"], r["name"]): r for r in res if r.get("name")}
    plan = {"repo": src["repo"], "branch": src.get("branch") or "main", "files": len(files),
            "create": [], "update": [], "delete": [], "conflict": [], "unchanged": []}
    for key, r in repo_map.items():
        rh = _norm_hash(r["obj"], src["name"]); base = state.get(key, {}).get("hash"); live = live_owned.get(key)
        item = {"path": r["path"], "kind": r["kind"], "ns": r["ns"], "name": r["name"], "key": key}
        if live is None:
            plan["create"].append(item); continue
        lh = _hash_obj(_project(r["obj"], live, src["name"]))
        if rh == lh: plan["unchanged"].append(item)
        elif base and rh != base and lh != base: plan["conflict"].append({**item, "hint": "仓库与集群均有修改"})
        elif lh != base and base: plan["conflict"].append({**item, "hint": "集群已修改,仓库未变(将以集群为准或跳过)"})
        else: plan["update"].append(item)
    for key, live in live_owned.items():
        if key not in repo_map:
            md = live["metadata"]
            plan["delete"].append({"kind": live["kind"], "ns": md.get("namespace"), "name": md["name"], "key": key})
    return plan

def gitops_apply_v2(src, deletes=None, conflicts=None):
    plan = gitops_plan_v2(src)
    conflicts = conflicts or {}
    deletes = set(deletes or [])
    state = load_state().setdefault(src["name"], {})
    result = {"created": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": []}
    files, res = _repo_resources(src)
    repo_map = {_res_key(r["kind"], r["ns"], r["name"]): r for r in res if r.get("name")}
    todo = [i for i in plan["create"] + plan["update"]]
    for c in plan["conflict"]:
        choice = conflicts.get(c["key"])
        if choice == "repo": todo.append(c)
        elif choice == "cluster":
            live = crd_get(c["plural"] if "plural" in c else KIND_PLURAL[c["kind"]], c["ns"], c["name"])
            state[c["key"]] = {"hash": _hash_obj(_project(repo_map[c["key"]]["obj"], live, src["name"]))}; result["skipped"] += 1
    for item in todo:
        try:
            doc = json.loads(json.dumps(repo_map[item["key"]]["obj"]))
            md = doc.setdefault("metadata", {})
            md["namespace"] = item["ns"]
            md.setdefault("labels", {})[OWNER_LABEL] = src["name"]
            try:
                live = crd_get(KIND_PLURAL[doc["kind"]], item["ns"], item["name"])
                md["resourceVersion"] = live.get("metadata", {}).get("resourceVersion", "")
                merged_labels = dict(live.get("metadata", {}).get("labels", {}))
                merged_labels.update(md.get("labels", {}))
                md["labels"] = merged_labels
                crd_update(KIND_PLURAL[doc["kind"]], item["ns"], item["name"], doc)
                result["updated"] += 1
            except ApiError as e:
                if e.code != 404: raise
                crd_create(KIND_PLURAL[doc["kind"]], doc); result["created"] += 1
            live_after = crd_get(KIND_PLURAL[doc["kind"]], item["ns"], item["name"])
            state[item["key"]] = {"hash": _hash_obj(_project(doc, live_after, src["name"]))}
        except Exception as e:
            result["errors"].append(f"{item.get('name')}: {type(e).__name__}: {e}")
    for item in plan["delete"]:
        if item["key"] not in deletes: continue
        try:
            crd_delete(KIND_PLURAL[item["kind"]], item["ns"], item["name"])
            state.pop(item["key"], None); result["deleted"] += 1
        except Exception as e:
            result["errors"].append(f"delete {item['name']}: {e}")
    all_state = load_state(); all_state[src["name"]] = state; save_state(all_state)
    return result

def _gh_request(method, path, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(GH_API + path, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "fission-console")
    if token: req.add_header("Authorization", "Bearer " + token)
    if data: req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            return r.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

def _gh_commit(src, changes, message):
    """changes: {path: text}. Creates commit on branch or PR branch."""
    token = token_for(src)
    repo = src["repo"].strip()
    if repo.endswith(".git"): repo = repo[:-4]
    base = src.get("branch") or "main"
    code, ref = _gh_request("GET", f"/repos/{repo}/git/ref/heads/{base}", token)
    if code != 200: raise ApiError(code, {"message": f"cannot read base branch {base}"})
    head = ref["object"]["sha"]
    mode = src.get("writeMode") or "pr"
    branch = base
    if mode == "pr":
        branch = f"fission-console/sync-{int(time.time())}"
        c, _ = _gh_request("POST", f"/repos/{repo}/git/refs", token, {"ref": f"refs/heads/{branch}", "sha": head})
        if c not in (200, 201): raise ApiError(c, {"message": "cannot create branch"})
    code, tree = _gh_request("GET", f"/repos/{repo}/git/trees/{head}?recursive=1", token)
    tree_map = {t["path"]: t["sha"] for t in tree.get("tree", []) if t.get("type") == "blob"}
    entries = []
    for path, text in changes.items():
        c, blob = _gh_request("POST", f"/repos/{repo}/git/blobs", token,
                              {"content": text, "encoding": "utf-8"})
        if c not in (200, 201): raise ApiError(c, {"message": f"blob failed {path}"})
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    c, newtree = _gh_request("POST", f"/repos/{repo}/git/trees", token,
                             {"base_tree": tree.get("sha"), "tree": entries})
    if c not in (200, 201): raise ApiError(c, {"message": "tree failed"})
    c, commit = _gh_request("POST", f"/repos/{repo}/git/commits", token,
                            {"message": message, "tree": newtree["sha"], "parents": [head]})
    if c not in (200, 201): raise ApiError(c, {"message": "commit failed"})
    c, _ = _gh_request("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", token, {"sha": commit["sha"]})
    if c not in (200, 201): raise ApiError(c, {"message": "ref update failed"})
    out = {"commit": commit["sha"], "branch": branch, "mode": mode}
    if mode == "pr":
        c, pr = _gh_request("POST", f"/repos/{repo}/pulls", token,
                            {"title": "Fission Console sync", "head": branch, "base": base,
                             "body": message})
        if c in (200, 201): out["pr_url"] = pr.get("html_url")
        else: out["pr_error"] = pr.get("message")
    return out

def gitops_export(src, mode=None, resources=None):
    if mode: src = dict(src, writeMode=mode)
    owned = {}
    for kind, plural in KIND_PLURAL.items():
        for item in crd_list(plural):
            if _owned(item, src["name"]):
                md = item["metadata"]
                owned[_res_key(kind, md.get("namespace", "fission"), md["name"])] = item
    if resources is not None:
        owned = {k: v for k, v in owned.items() if k in set(resources)}
    prefix = (src.get("path") or "").strip("/")
    changes = {}
    for key, item in owned.items():
        md = item["metadata"]
        doc = {"apiVersion": "fission.io/v1", "kind": item["kind"],
               "metadata": {"name": md["name"], "namespace": md.get("namespace", "fission"),
                            "labels": md.get("labels", {})},
               "spec": item.get("spec", {})}
        plural = KIND_PLURAL[item["kind"]]
        path = f"{prefix}/{plural}/{md['name']}.yaml" if prefix else f"{plural}/{md['name']}.yaml"
        changes[path] = miniyaml_dump(doc) + "\n"
    if not changes: return {"changed": 0, "message": "没有可导出的归属资源"}
    out = _gh_commit(src, changes, f"chore(fission-console): sync {len(changes)} resources from cluster")
    out["changed"] = len(changes)
    return out
