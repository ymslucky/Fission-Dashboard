# -*- coding: utf-8 -*-
"""GitOps 双向同步（从 fn/ext3.py、ext7.py、ext8.py 移植为异步版本）。"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from fastapi import HTTPException

from .. import k8s
from .github import gh_fetch_tree, gh_commit
from .miniyaml import miniyaml_dump, miniyaml_load
from .store import load_state, save_state

KIND_PLURAL = {"Function": "functions", "Package": "packages", "Environment": "environments",
               "HTTPTrigger": "httptriggers", "TimeTrigger": "timetriggers"}
OWNER_LABEL = "fission-console.io/source"


def _res_key(kind: str, ns: str, name: str) -> str:
    return f"{kind}/{ns}/{name}"


def _hash_obj(obj: dict, source: str) -> str:
    m = obj.get("metadata", {})
    core = {"kind": obj.get("kind"), "apiVersion": obj.get("apiVersion"),
            "name": m.get("name"), "namespace": m.get("namespace"),
            "spec": obj.get("spec", {}), "labels": m.get("labels", {}),
            "annotations": m.get("annotations", {})}
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _project(repo_obj: dict, live_obj: dict, source: str) -> dict:
    """把 live 投影到仓库声明的字段上，忽略服务端默认值。"""
    md_r = repo_obj.get("metadata", {})
    md_l = live_obj.get("metadata", {})

    def proj(r, l):
        if isinstance(r, dict):
            if not isinstance(l, dict):
                return l
            return {k: proj(r[k], l.get(k)) for k in r}
        return l

    labels = proj(md_r.get("labels", {}), md_l.get("labels", {})) or {}
    labels[OWNER_LABEL] = source
    ann = proj(md_r.get("annotations", {}), md_l.get("annotations", {})) or {}
    return {"kind": repo_obj.get("kind"), "apiVersion": repo_obj.get("apiVersion"),
            "name": md_r.get("name"), "namespace": md_r.get("namespace"),
            "spec": proj(repo_obj.get("spec", {}), live_obj.get("spec", {})),
            "labels": labels, "annotations": ann}


def _norm_hash(obj: dict, source: str) -> str:
    return _hash_obj(_project(obj, obj, source), source)


def _owned(res: dict, source: str) -> bool:
    return res.get("metadata", {}).get("labels", {}).get(OWNER_LABEL) == source


async def _repo_resources(src: dict):
    """返回 (files, 解析出的资源列表)。"""
    files = await gh_fetch_tree(src)
    out = []
    for path, content in sorted(files.items()):
        if not path.endswith((".yaml", ".yml", ".json")):
            continue
        text = content.decode(errors="replace")
        try:
            docs = [json.loads(text)] if path.endswith(".json") else miniyaml_load(text)
        except Exception as e:
            out.append({"path": path, "error": f"parse: {e}"})
            continue
        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") not in KIND_PLURAL:
                continue
            md = doc.get("metadata", {})
            out.append({"path": path, "kind": doc["kind"], "plural": KIND_PLURAL[doc["kind"]],
                        "ns": md.get("namespace", "fission"), "name": md.get("name"), "obj": doc})
    return files, out


async def get_source(sources_cfg: dict, name: str) -> dict:
    src = next((s for s in sources_cfg.get("sources", []) if s["name"] == name), None)
    if not src:
        raise HTTPException(status_code=404, detail={"message": "source not found"})
    return src


async def gitops_tree(src: dict) -> dict:
    files, res = await _repo_resources(src)
    state = (await load_state()).get(src["name"], {})
    by_key = {_res_key(r["kind"], r["ns"], r["name"]): r for r in res if r.get("name")}
    status = []
    for key, r in by_key.items():
        rh = _norm_hash(r["obj"], src["name"])
        base = state.get(key, {}).get("hash")
        try:
            live = await k8s.crd_get(r["plural"], r["ns"], r["name"])
            lh = _hash_obj(_project(r["obj"], live, src["name"]), src["name"])
        except HTTPException:
            lh = None
        if lh is None:
            st = "repo-only"
        elif rh == lh:
            st = "synced"
        elif base and rh != base and lh != base:
            st = "conflict"
        elif base and lh != base:
            st = "cluster-changed"
        else:
            st = "repo-changed"
        status.append({**{k: r[k] for k in ("path", "kind", "ns", "name")}, "state": st})
    return {"repo": src["repo"], "branch": src.get("branch") or "main",
            "files": [{"path": p, "size": len(c)} for p, c in sorted(files.items())],
            "resources": status}


async def gitops_file(src: dict, path: str) -> dict:
    files, _ = await _repo_resources(src)
    if path not in files:
        raise HTTPException(status_code=404, detail={"message": "file not found"})
    return {"path": path, "content": files[path].decode(errors="replace")}


async def gitops_plan_v2(src: dict) -> dict:
    files, res = await _repo_resources(src)
    state = (await load_state()).get(src["name"], {})
    live_owned: dict[str, dict] = {}
    for kind, plural in KIND_PLURAL.items():
        try:
            for item in await k8s.crd_list(plural):
                if _owned(item, src["name"]):
                    md = item["metadata"]
                    live_owned[_res_key(kind, md.get("namespace", "fission"), md["name"])] = item
        except HTTPException:
            pass
    repo_map = {_res_key(r["kind"], r["ns"], r["name"]): r for r in res if r.get("name")}
    plan = {"repo": src["repo"], "branch": src.get("branch") or "main", "files": len(files),
            "create": [], "update": [], "delete": [], "conflict": [], "unchanged": []}
    for key, r in repo_map.items():
        rh = _norm_hash(r["obj"], src["name"])
        base = state.get(key, {}).get("hash")
        live = live_owned.get(key)
        item = {"path": r["path"], "kind": r["kind"], "ns": r["ns"], "name": r["name"], "key": key}
        if live is None:
            plan["create"].append(item)
            continue
        lh = _hash_obj(_project(r["obj"], live, src["name"]), src["name"])
        if rh == lh:
            plan["unchanged"].append(item)
        elif base and rh != base and lh != base:
            plan["conflict"].append({**item, "hint": "仓库与集群均有修改"})
        elif base and lh != base:
            plan["conflict"].append({**item, "hint": "集群已修改,仓库未变(将以集群为准或跳过)"})
        else:
            plan["update"].append(item)
    for key, live in live_owned.items():
        if key not in repo_map:
            md = live["metadata"]
            plan["delete"].append({"kind": live["kind"], "ns": md.get("namespace"),
                                   "name": md["name"], "key": key})
    return plan


async def gitops_statuses(src: dict) -> dict:
    plan = await gitops_plan_v2(src)
    return {k: len(plan[k]) for k in ("create", "update", "delete", "conflict", "unchanged")}


async def gitops_apply_v2(src: dict, deletes: Optional[list] = None,
                          conflicts: Optional[dict] = None) -> dict:
    plan = await gitops_plan_v2(src)
    conflicts = conflicts or {}
    delete_set = set(deletes or [])
    all_state = await load_state()
    state = all_state.setdefault(src["name"], {})
    result = {"created": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": []}
    files, res = await _repo_resources(src)
    repo_map = {_res_key(r["kind"], r["ns"], r["name"]): r for r in res if r.get("name")}
    todo = list(plan["create"]) + list(plan["update"])
    for c in plan["conflict"]:
        choice = conflicts.get(c["key"])
        if choice == "repo":
            todo.append(c)
        elif choice == "cluster":
            live = await k8s.crd_get(KIND_PLURAL[c["kind"]], c["ns"], c["name"])
            state[c["key"]] = {"hash": _hash_obj(_project(repo_map[c["key"]]["obj"], live, src["name"]), src["name"])}
            result["skipped"] += 1
    for item in todo:
        try:
            doc = json.loads(json.dumps(repo_map[item["key"]]["obj"]))
            md = doc.setdefault("metadata", {})
            md["namespace"] = item["ns"]
            md.setdefault("labels", {})[OWNER_LABEL] = src["name"]
            try:
                live = await k8s.crd_get(KIND_PLURAL[doc["kind"]], item["ns"], item["name"])
                md["resourceVersion"] = live.get("metadata", {}).get("resourceVersion", "")
                merged_labels = dict(live.get("metadata", {}).get("labels", {}))
                merged_labels.update(md.get("labels", {}))
                md["labels"] = merged_labels
                await k8s.crd_update(KIND_PLURAL[doc["kind"]], item["ns"], item["name"], doc)
                result["updated"] += 1
            except HTTPException as e:
                if e.status_code != 404:
                    raise
                await k8s.crd_create(KIND_PLURAL[doc["kind"]], doc)
                result["created"] += 1
            live_after = await k8s.crd_get(KIND_PLURAL[doc["kind"]], item["ns"], item["name"])
            state[item["key"]] = {"hash": _hash_obj(_project(doc, live_after, src["name"]), src["name"])}
        except Exception as e:
            result["errors"].append(f"{item.get('name')}: {type(e).__name__}: {e}")
    for item in plan["delete"]:
        if item["key"] not in delete_set:
            continue
        try:
            await k8s.crd_delete(KIND_PLURAL[item["kind"]], item["ns"], item["name"])
            state.pop(item["key"], None)
            result["deleted"] += 1
        except Exception as e:
            result["errors"].append(f"delete {item['name']}: {e}")
    await save_state(all_state)
    return result


async def gitops_export(src: dict, mode: Optional[str] = None,
                        resources: Optional[list[str]] = None) -> dict:
    if mode:
        src = dict(src, writeMode=mode)
    owned: dict[str, dict] = {}
    for kind, plural in KIND_PLURAL.items():
        for item in await k8s.crd_list(plural):
            if _owned(item, src["name"]):
                md = item["metadata"]
                owned[_res_key(kind, md.get("namespace", "fission"), md["name"])] = item
    if resources is not None:
        owned = {k: v for k, v in owned.items() if k in set(resources)}
    prefix = (src.get("path") or "").strip("/")
    changes: dict[str, str] = {}
    for key, item in owned.items():
        md = item["metadata"]
        doc = {"apiVersion": "fission.io/v1", "kind": item["kind"],
               "metadata": {"name": md["name"], "namespace": md.get("namespace", "fission"),
                            "labels": md.get("labels", {})},
               "spec": item.get("spec", {})}
        plural = KIND_PLURAL[item["kind"]]
        path = f"{prefix}/{plural}/{md['name']}.yaml" if prefix else f"{plural}/{md['name']}.yaml"
        changes[path] = miniyaml_dump(doc) + "\n"
    if not changes:
        return {"changed": 0, "message": "没有可导出的归属资源"}
    out = await gh_commit(src, changes, f"chore(fission-console): sync {len(changes)} resources from cluster")
    out["changed"] = len(changes)
    return out
