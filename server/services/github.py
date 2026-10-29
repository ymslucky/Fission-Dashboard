# -*- coding: utf-8 -*-
"""GitHub API 访问（从 fn/ext3.py、ext7.py 的 _gh_request / gh_fetch_tree / _gh_commit 移植）。"""
from __future__ import annotations

import base64
import time
from typing import Any, Optional

import httpx
from fastapi import HTTPException

from ..k8s import request_k8s
from .store import token_for

GH_API = "https://api.github.com"


async def gh_request(method: str, path: str, token: str, payload: Any = None):
    """返回 (status, json_body)，不抛异常（与旧行为一致）。"""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "fission-console"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if payload is not None:
        import json as _json
        data = _json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.request(method, GH_API + path, content=data, headers=headers)
            body = resp.content
            return resp.status_code, (await _jsonload(body))
        except httpx.HTTPError:
            return 0, {"message": "github unreachable"}


async def _jsonload(body: bytes):
    import json as _json
    if not body:
        return {}
    try:
        return _json.loads(body)
    except Exception:
        return {"message": body.decode(errors="replace")}


async def gh_fetch_tree(src: dict) -> dict[str, bytes]:
    """经 Git Trees/Blobs API 读取整个仓库子目录（无 CDN 缓存）。返回 {相对路径: 内容}。"""
    repo = src["repo"].strip()
    if repo.endswith(".git"):
        repo = repo[:-4]
    branch = src.get("branch") or "main"
    subdir = (src.get("path") or "").strip("/")
    token = await token_for(src)
    code, ref = await gh_request("GET", f"/repos/{repo}/git/ref/heads/{branch}", token)
    if code != 200:
        raise HTTPException(status_code=code if code >= 400 else 502,
                            detail={"message": f"cannot read branch {branch}: {ref.get('message', '')}"})
    head = ref["object"]["sha"]
    code, tree = await gh_request("GET", f"/repos/{repo}/git/trees/{head}?recursive=1", token)
    if code != 200:
        raise HTTPException(status_code=code if code >= 400 else 502,
                            detail={"message": tree.get("message", "cannot list tree")})
    files: dict[str, bytes] = {}
    for item in tree.get("tree", []):
        if item.get("type") != "blob":
            continue
        rel = item["path"]
        if subdir:
            if not rel.startswith(subdir + "/"):
                continue
            rel = rel[len(subdir) + 1:]
        if item.get("size", 0) > 512 * 1024:
            continue
        code, blob = await gh_request("GET", f"/repos/{repo}/git/blobs/{item['sha']}", token)
        if code != 200:
            continue
        try:
            files[rel] = base64.b64decode(blob.get("content", ""))
        except Exception:
            continue
    return files


async def gh_commit(src: dict, changes: dict[str, str], message: str) -> dict:
    """把 {路径: 文本} 提交到分支；writeMode=pr 时建分支并发 PR。"""
    token = await token_for(src)
    repo = src["repo"].strip()
    if repo.endswith(".git"):
        repo = repo[:-4]
    base = src.get("branch") or "main"
    code, ref = await gh_request("GET", f"/repos/{repo}/git/ref/heads/{base}", token)
    if code != 200:
        raise HTTPException(status_code=code if code >= 400 else 502,
                            detail={"message": f"cannot read base branch {base}"})
    head = ref["object"]["sha"]
    mode = src.get("writeMode") or "pr"
    branch = base
    if mode == "pr":
        branch = f"fission-console/sync-{int(time.time())}"
        c, _ = await gh_request("POST", f"/repos/{repo}/git/refs", token,
                                {"ref": f"refs/heads/{branch}", "sha": head})
        if c not in (200, 201):
            raise HTTPException(status_code=c if c >= 400 else 502, detail={"message": "cannot create branch"})
    code, tree = await gh_request("GET", f"/repos/{repo}/git/trees/{head}?recursive=1", token)
    entries = []
    for path, text in changes.items():
        c, blob = await gh_request("POST", f"/repos/{repo}/git/blobs", token,
                                   {"content": text, "encoding": "utf-8"})
        if c not in (200, 201):
            raise HTTPException(status_code=c if c >= 400 else 502, detail={"message": f"blob failed {path}"})
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    c, newtree = await gh_request("POST", f"/repos/{repo}/git/trees", token,
                                  {"base_tree": tree.get("sha"), "tree": entries})
    if c not in (200, 201):
        raise HTTPException(status_code=c if c >= 400 else 502, detail={"message": "tree failed"})
    c, commit = await gh_request("POST", f"/repos/{repo}/git/commits", token,
                                 {"message": message, "tree": newtree["sha"], "parents": [head]})
    if c not in (200, 201):
        raise HTTPException(status_code=c if c >= 400 else 502, detail={"message": "commit failed"})
    c, _ = await gh_request("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", token, {"sha": commit["sha"]})
    if c not in (200, 201):
        raise HTTPException(status_code=c if c >= 400 else 502, detail={"message": "ref update failed"})
    out = {"commit": commit["sha"], "branch": branch, "mode": mode}
    if mode == "pr":
        c, pr = await gh_request("POST", f"/repos/{repo}/pulls", token,
                                 {"title": "Fission Console sync", "head": branch, "base": base, "body": message})
        if c in (200, 201):
            out["pr_url"] = pr.get("html_url")
        else:
            out["pr_error"] = pr.get("message")
    return out
