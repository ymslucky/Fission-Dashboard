import collections
import base64
import io
import tarfile
import threading
#!/usr/bin/env python3
"""Fission Console backend - Python stdlib only.

Serves the static SPA and a JSON API that proxies the official
Fission surfaces: the Kubernetes CRD API (fission.io/v1) and the
Router invocation endpoint. No third-party dependencies.
"""
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATIC_DIR = os.environ.get("STATIC_DIR", "/app/static")
K8S_HOST = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
K8S_PORT = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
ROUTER = os.environ.get("FISSION_ROUTER", "http://router.fission.svc:80")
GROUP = "fission.io"
VERSION = "v1"
KINDS = ("functions", "packages", "environments", "httptriggers", "timetriggers")

_ssl_ctx = ssl.create_default_context(cafile=CA_PATH if os.path.exists(CA_PATH) else None)


def _token():
    with open(TOKEN_PATH) as fh:
        return fh.read().strip()


def k8s(method, path, body=None, headers=None, raw=False, timeout=30):
    url = f"https://{K8S_HOST}:{K8S_PORT}{path}"
    data = None
    hdrs = {"Authorization": "Bearer " + _token()}
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    last_exc = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, context=_ssl_ctx, timeout=timeout) as resp:
                payload = resp.read()
                if raw:
                    return resp.status, payload, dict(resp.headers)
                return resp.status, json.loads(payload) if payload else {}, None
        except urllib.error.HTTPError as e:
            payload = e.read()
            if e.code in (429, 503) and attempt < 2:
                time.sleep(0.6 * (attempt + 1)); continue
            if raw:
                return e.code, payload, dict(e.headers)
            try:
                return e.code, json.loads(payload), None
            except Exception:
                return e.code, {"message": payload.decode(errors="replace")}, None
        except Exception as e:
            last_exc = e
            if attempt < 2:
                time.sleep(0.3 * (attempt + 1)); continue
            raise
    if last_exc: raise last_exc
    return 503, {"message": "unavailable"}, None


def crd_list(kind):
    code, body, _ = k8s("GET", f"/apis/{GROUP}/{VERSION}/{kind}")
    if code != 200:
        raise ApiError(code, body)
    return body.get("items", [])


def crd_get(kind, ns, name):
    code, body, _ = k8s("GET", f"/apis/{GROUP}/{VERSION}/namespaces/{ns}/{kind}/{name}")
    if code != 200:
        raise ApiError(code, body)
    return body


def crd_create(kind, obj):
    ns = obj.get("metadata", {}).get("namespace", "fission")
    code, body, _ = k8s("POST", f"/apis/{GROUP}/{VERSION}/namespaces/{ns}/{kind}", obj)
    if code not in (200, 201):
        raise ApiError(code, body)
    return body


def crd_update(kind, ns, name, obj):
    code, body, _ = k8s("PUT", f"/apis/{GROUP}/{VERSION}/namespaces/{ns}/{kind}/{name}", obj)
    if code != 200:
        raise ApiError(code, body)
    return body


def crd_delete(kind, ns, name):
    code, body, _ = k8s("DELETE", f"/apis/{GROUP}/{VERSION}/namespaces/{ns}/{kind}/{name}")
    if code not in (200, 202):
        raise ApiError(code, body)
    return body


class ApiError(Exception):
    def __init__(self, code, body):
        self.code = code
        self.body = body
        super().__init__(str(body))


def overview():
    data = {"counts": {}, "core": []}
    for kind in KINDS:
        data["counts"][kind] = len(crd_list(kind))
    code, body, _ = k8s("GET", "/api/v1/namespaces/fission/pods")
    pods = body.get("items", []) if code == 200 else []
    by_app = {}
    for p in pods:
        labels = p.get("metadata", {}).get("labels", {})
        app = labels.get("app") or "other"
        ready = all(c.get("ready") for c in p.get("status", {}).get("containerStatuses", [{"ready": True}]))
        e = by_app.setdefault(app, {"app": app, "pods": 0, "ready": 0})
        e["pods"] += 1
        if ready:
            e["ready"] += 1
    data["core"] = sorted(by_app.values(), key=lambda x: x["app"])
    return data


def function_logs(ns, name, pod=None):
    sel = urllib.parse.quote(f"functionName={name}")
    code, body, _ = k8s("GET", f"/api/v1/namespaces/{ns}/pods?labelSelector={sel}")
    if code != 200:
        raise ApiError(code, body)
    pods = []
    for p in body.get("items", []):
        ready = all(c.get("ready") for c in p.get("status", {}).get("containerStatuses", [])) or None
        pods.append({"name": p["metadata"]["name"], "phase": p.get("status", {}).get("phase", ""),
                     "ready": ready, "created": p["metadata"].get("creationTimestamp", "")})
    result = {"pods": pods, "logs": {}}
    targets = [p["name"] for p in pods] if not pod else [pod]
    for pn in targets:
        c, log, _ = k8s(
            "GET",
            f"/api/v1/namespaces/{ns}/pods/{pn}/log?tailLines=300&timestamps=false",
            raw=True,
        )
        result["logs"][pn] = log.decode(errors="replace") if c == 200 else f"[failed: HTTP {c}]"
    return result


def invoke(ns, trigger, method=None, headers=None, body=None, body_extra=None):
    code, tr, _ = k8s("GET", f"/apis/{GROUP}/{VERSION}/namespaces/{ns}/httptriggers/{trigger}")
    if code != 200:
        raise ApiError(code, tr)
    spec = tr.get("spec", {})
    url = spec.get("relativeUrl") or spec.get("relativeurl") or spec.get("prefix") or "/"
    target = ROUTER.rstrip("/") + (url if url.startswith("/") else "/" + url)
    m = (method or (spec.get("methods") or ["GET"])[0]).upper()
    data = body.encode() if isinstance(body, str) else (json.dumps(body).encode() if body else None)
    hdrs = dict(headers or {})
    if data and "Content-Type" not in {k.title() for k in hdrs}:
        hdrs["Content-Type"] = "text/plain"
    extra_q = body_extra if isinstance(body_extra, dict) else None
    if extra_q:
        target += ("&" if "?" in target else "?") + urllib.parse.urlencode(extra_q)
    req = urllib.request.Request(target, data=data, headers=hdrs, method=m)
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read().decode(errors="replace")
            return {"status": resp.status, "latency_ms": int((time.time() - start) * 1000),
                    "headers": dict(resp.headers), "body": payload[:20000]}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "latency_ms": int((time.time() - start) * 1000),
                "headers": dict(e.headers), "body": e.read().decode(errors="replace")[:20000]}


# ---------- extensions: request routing, metrics sampler, mini-yaml, git sync ----------
import collections

MINIYAML_ERR = ""

def _parse_scalar(s):
    s = s.strip()
    if s == "" or s == "~" or s == "null": return None
    if s.startswith(("'", '"')):
        q = s[0]
        if len(s) >= 2 and s.endswith(q): return s[1:-1]
        return s
    if s in ("true", "True"): return True
    if s in ("false", "False"): return False
    if re.fullmatch(r"-?\d+", s): return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s): return float(s)
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_parse_scalar(x) for x in re.split(r",(?![^\[]*\])", inner) if x.strip()] if inner else []
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        out = {}
        if inner:
            for part in re.split(r",(?![^{}]*\})", inner):
                k, _, v = part.partition(":")
                out[_parse_scalar(k)] = _parse_scalar(v)
        return out
    return s

def _strip_comment(line):
    out, in_q = "", None
    for ch in line:
        if in_q:
            out += ch
            if ch == in_q: in_q = None
        elif ch in ("'", '"'):
            in_q = ch; out += ch
        elif ch == "#":
            break
        else:
            out += ch
    return out.rstrip()

def miniyaml_load(text):
    """Parse a practical subset of YAML (block maps/sequences, scalars,
    quoted strings, inline []/{}, | and |- block scalars, multi-doc ---)."""
    global MINIYAML_ERR
    docs = []
    lines = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if stripped.strip() == "---":
            docs.append(None); lines.append(("DOC", 0, "")); continue
        lines.append(("L", len(stripped) - len(stripped.lstrip(" ")), stripped.strip()))
    docs_cur = {}
    has_doc = any(t == "DOC" for t, _, _ in lines)
    def parse_block(idx, indent):
        """returns (obj, next_idx)"""
        if idx >= len(lines) or lines[idx][0] == "DOC" or lines[idx][1] < indent:
            return {}, idx
        is_seq = lines[idx][0] == "L" and lines[idx][2].startswith("- ") or lines[idx][2] == "-"
        if is_seq:
            arr = []
            while idx < len(lines) and lines[idx][0] == "L" and lines[idx][1] == indent and (lines[idx][2].startswith("- ") or lines[idx][2] == "-"):
                item_line = lines[idx][2]
                if item_line == "-":
                    obj, idx = parse_block(idx + 1, indent + 2); arr.append(obj); continue
                rest = item_line[2:]
                inner_ind = indent + 2
                if ":" in _strip_comment(rest) and not rest.startswith(("[", "{", '"', "'")):
                    k, _, v = rest.partition(":")
                    k = _parse_scalar(k)
                    v = _strip_comment(v).strip()
                    if v == "":
                        obj, idx = parse_block(idx + 1, lines[idx + 1][1] if idx + 1 < len(lines) and lines[idx+1][1] > indent else inner_ind)
                        arr.append({k: obj}); continue
                    arr.append({k: _parse_scalar(v)}); idx += 1; continue
                arr.append(_parse_scalar(rest)); idx += 1
            return arr, idx
        m = {}
        while idx < len(lines) and lines[idx][0] == "L" and lines[idx][1] == indent and not lines[idx][2].startswith("- "):
            line = lines[idx][2]
            if ":" not in line: idx += 1; continue
            k, _, v = line.partition(":")
            key = _parse_scalar(k)
            v = v.strip()
            if v in ("|", "|-", "|+", ">", ">-", ">+"):
                block_lines = []
                idx += 1
                bind = None
                while idx < len(lines) and lines[idx][0] == "L" and (lines[idx][2] == "" or lines[idx][1] > indent):
                    if lines[idx][2] == "":
                        block_lines.append(""); idx += 1; continue
                    if bind is None: bind = lines[idx][1]
                    block_lines.append(" " * (lines[idx][1] - bind) + lines[idx][2]); idx += 1
                m[key] = ("\n" if not v.startswith("|-") else "") + "\n".join(block_lines).rstrip("\n") + ("\n" if v in ("|", "|+") else "")
                continue
            if v == "":
                nxt = idx + 1
                if nxt < len(lines) and lines[nxt][0] == "L" and (lines[nxt][1] > indent or lines[nxt][0] == "DOC"):
                    if lines[nxt][0] == "DOC": m[key] = None; idx = nxt; continue
                    obj, idx = parse_block(nxt, lines[nxt][1]); m[key] = obj; continue
                m[key] = None; idx += 1; continue
            m[key] = _parse_scalar(v); idx += 1
        return m, idx
    i = 0
    cur = {}
    while i < len(lines):
        if lines[i][0] == "DOC":
            docs.append(cur); cur = {}; i += 1; continue
        obj, i = parse_block(i, lines[i][1])
        cur.update(obj if isinstance(obj, dict) else {})
    docs.append(cur)
    if not has_doc: return [docs[-1]] if docs else []
    return [d for d in docs if d]

def miniyaml_dump_loads(text):
    return miniyaml_load(text)


_lock = threading.Lock()
# ---------- metrics sampler ----------
METRICS_LOCK = threading.Lock()
METRICS = {"series": collections.deque(maxlen=480), "cold_starts": {}, "last_error": ""}
SAMPLER_STARTED = False

def _fn_pods():
    """{fn_name: {'ns':..,'pods':n,'ready':n}} across all namespaces"""
    out = {}
    code, body, _ = k8s("GET", "/api/v1/pods?labelSelector=functionName")
    if code != 200: return out
    for p in body.get("items", []):
        fn = p["metadata"].get("labels", {}).get("functionName")
        if not fn: continue
        e = out.setdefault(fn, {"ns": p["metadata"]["namespace"], "pods": 0, "ready": 0})
        e["pods"] += 1
        if all(c.get("ready") for c in p.get("status", {}).get("containerStatuses", [])):
            e["ready"] += 1
    return out

def _fn_cpu_mem():
    """{fn_name: {'cpu_m':int,'mem_mi':int}} from metrics.k8s.io"""
    out = {}
    code, body, _ = k8s("GET", "/apis/metrics.k8s.io/v1beta1/pods", timeout=15)
    if code != 200: return out
    for p in body.get("items", []):
        fn = p.get("metadata", {}).get("labels", {}).get("functionName")
        if not fn: continue
        cpu_m, mem_mi = 0, 0
        for c in p.get("containers", []):
            u = c.get("usage", {})
            cpu = u.get("cpu", "0"); mem = u.get("memory", "0Ki")
            if cpu.endswith("n"): cpu_m += int(cpu[:-1]) / 1e6
            elif cpu.endswith("u"): cpu_m += int(cpu[:-1]) / 1e3
            elif cpu.endswith("m"): cpu_m += int(cpu[:-1])
            if mem.endswith("Ki"): mem_mi += int(mem[:-2]) / 1024
            elif mem.endswith("Mi"): mem_mi += int(mem[:-2])
            elif mem.endswith("Gi"): mem_mi += int(mem[:-2]) * 1024
        e = out.setdefault(fn, {"cpu_m": 0, "mem_mi": 0})
        e["cpu_m"] += round(cpu_m); e["mem_mi"] += round(mem_mi)
    return out

def _sample_once():
    global METRICS
    pods = _fn_pods()
    res = _fn_cpu_mem()
    with METRICS_LOCK:
        snap = {"ts": int(time.time())}
        for fn, e in pods.items():
            prev = None
            for s in reversed(METRICS["series"]):
                if fn in s["fns"]: prev = s["fns"][fn]; break
            if prev is not None and prev["pods"] == 0 and e["pods"] > 0:
                METRICS["cold_starts"][fn] = METRICS["cold_starts"].get(fn, 0) + 1
        snap["fns"] = {fn: {"pods": e["pods"], "ready": e["ready"],
                            "cpu_m": res.get(fn, {}).get("cpu_m", 0),
                            "mem_mi": res.get(fn, {}).get("mem_mi", 0)} for fn, e in pods.items()}
        # keep last-seen functions with zero pods for a while
        for fn in list(snap["fns"]):
            pass
        METRICS["series"].append(snap)
        METRICS["last_error"] = ""

def _sampler_loop():
    while True:
        try:
            _sample_once()
        except Exception as e:
            with METRICS_LOCK:
                METRICS["last_error"] = f"{type(e).__name__}: {e}"
        time.sleep(15)

def start_sampler():
    global SAMPLER_STARTED
    with _lock:
        if SAMPLER_STARTED: return
        SAMPLER_STARTED = True
        threading.Thread(target=_sampler_loop, daemon=True).start()

def metrics_snapshot():
    with METRICS_LOCK:
        return {"series": list(METRICS["series"]), "cold_starts": dict(METRICS["cold_starts"]),
                "last_error": METRICS["last_error"]}


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


# ---------- v2: storage, yaml dump, github write-back ----------
import hashlib

TESTDATA_CM = "fission-console-testdata"
STATE_CM = "fission-console-sync-state"
TOKEN_SECRET = "fission-console-github"
OWNER_LABEL = "fission-console.io/source"

def _cm_get(name, key, default):
    code, body, _ = k8s("GET", f"/api/v1/namespaces/{CONFIG_NS}/configmaps/{name}")
    if code != 200: return default
    try: return json.loads(body.get("data", {}).get(key, json.dumps(default)))
    except Exception: return default

def _cm_put(name, key, value):
    payload = json.dumps(value, ensure_ascii=False)
    cm = {"apiVersion": "v1", "kind": "ConfigMap",
          "metadata": {"name": name, "namespace": CONFIG_NS, "labels": {"app": "fission-console"}},
          "data": {key: payload}}
    code, body, _ = k8s("GET", f"/api/v1/namespaces/{CONFIG_NS}/configmaps/{name}")
    if code == 200:
        cm["metadata"]["resourceVersion"] = body["metadata"]["resourceVersion"]
        merged = dict(body.get("data", {}))
        merged.update(cm["data"])
        cm["data"] = merged
        code, body, _ = k8s("PUT", f"/api/v1/namespaces/{CONFIG_NS}/configmaps/{name}", cm)
    else:
        code, body, _ = k8s("POST", f"/api/v1/namespaces/{CONFIG_NS}/configmaps", cm)
    if code not in (200, 201):
        raise ApiError(code, body)

def load_testdata():
    return {"collections": _cm_get(TESTDATA_CM, "collections.json", []),
            "environments": _cm_get(TESTDATA_CM, "environments.json", [])}

def save_testdata(data):
    _cm_put(TESTDATA_CM, "collections.json", data.get("collections", []))
    _cm_put(TESTDATA_CM, "environments.json", data.get("environments", []))

def load_state():
    return _cm_get(STATE_CM, "state.json", {})

def save_state(state):
    _cm_put(STATE_CM, "state.json", state)

def token_for(src):
    name = src.get("tokenSecretName") or TOKEN_SECRET
    key = src.get("tokenSecretKey") or src.get("name")
    code, body, _ = k8s("GET", f"/api/v1/namespaces/{CONFIG_NS}/secrets/{name}")
    if code != 200: return ""
    import base64 as _b64
    try: return _b64.b64decode(body["data"].get(key, "")).decode()
    except Exception: return ""

def token_save(source_name, token):
    import base64 as _b64
    code, body, _ = k8s("GET", f"/api/v1/namespaces/{CONFIG_NS}/secrets/{TOKEN_SECRET}")
    sec = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
           "metadata": {"name": TOKEN_SECRET, "namespace": CONFIG_NS, "labels": {"app": "fission-console"}},
           "data": {}}
    if code == 200:
        sec["data"] = body.get("data", {})
        sec["metadata"]["resourceVersion"] = body["metadata"]["resourceVersion"]
    sec["data"][source_name] = _b64.b64encode(token.encode()).decode()
    if code == 200:
        c, b, _ = k8s("PUT", f"/api/v1/namespaces/{CONFIG_NS}/secrets/{TOKEN_SECRET}", sec)
    else:
        c, b, _ = k8s("POST", f"/api/v1/namespaces/{CONFIG_NS}/secrets", sec)
    if c not in (200, 201): raise ApiError(c, b)

def _yaml_scalar(v):
    if v is None: return "null"
    if v is True: return "true"
    if v is False: return "false"
    if isinstance(v, (int, float)): return str(v)
    s = str(v)
    if s == "" or any(c in s for c in ":#{}[],&*!|>'\"%@`") or s.strip() != s:
        return json.dumps(s, ensure_ascii=False)
    return s

def miniyaml_dump(obj, indent=0):
    pad = "  " * indent
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:")
                out.append(miniyaml_dump(v, indent + 1))
            elif isinstance(v, (dict, list)):
                out.append(f"{pad}{k}: {'{}' if isinstance(v, dict) else '[]'}")
            else:
                out.append(f"{pad}{k}: {_yaml_scalar(v)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and item:
                first = True
                for k, v in item.items():
                    if isinstance(v, (dict, list)) and v:
                        prefix = f"{pad}- {k}:" if first else f"{pad}  {k}:"
                        out.append(prefix)
                        out.append(miniyaml_dump(v, indent + 2))
                    else:
                        out.append((f"{pad}- " if first else f"{pad}  ") + f"{k}: {_yaml_scalar(v)}")
                    first = False
            else:
                out.append(f"{pad}- {_yaml_scalar(item)}")
    return "\n".join(out)


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


# ===================== single-endpoint fission entrypoint =====================
from flask import request, Response

INDEX_HTML = base64.b64decode("PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9InpoLUNOIj4KPGhlYWQ+CjxtZXRhIGNoYXJzZXQ9InV0Zi04Ij4KPG1ldGEgbmFtZT0idmlld3BvcnQiIGNvbnRlbnQ9IndpZHRoPWRldmljZS13aWR0aCwgaW5pdGlhbC1zY2FsZT0xIj4KPHRpdGxlPkZpc3Npb24gQ29uc29sZTwvdGl0bGU+CjxzdHlsZT4KOnJvb3R7CiAgLS15OiNmMmMzMDA7IC0teS1kZWVwOiNkOWFmMDA7IC0taW5rOiMwYjBiMGI7IC0tcGFwZXI6I2YyZjNlZjsgLS13aGl0ZTojZmZmZmZmOwogIC0tbGluZTojMGIwYjBiOyAtLW11dGVkOiM1ZjVmNWY7IC0tcmVkOiNkMjJiMmI7IC0tZ3JlZW46IzBlN2EzYzsgLS1ibHVlOiMxNDVhODY7CiAgLS1zYW5zOiJTZWdvZSBVSSBWYXJpYWJsZSBUZXh0IiwiU2Vnb2UgVUkiLCJQaW5nRmFuZyBTQyIsIk1pY3Jvc29mdCBZYUhlaSIsc2Fucy1zZXJpZjsKICAtLW1vbm86IkNhc2NhZGlhIE1vbm8iLCJDYXNjYWRpYSBDb2RlIixDb25zb2xhcyxtb25vc3BhY2U7Cn0KKntib3gtc2l6aW5nOmJvcmRlci1ib3h9Cmh0bWwsYm9keXttYXJnaW46MDtoZWlnaHQ6MTAwJX0KYm9keXtiYWNrZ3JvdW5kOnZhcigtLXBhcGVyKTtjb2xvcjp2YXIoLS1pbmspO2ZvbnQ6MTMuNXB4LzEuNSB2YXIoLS1zYW5zKTstd2Via2l0LWZvbnQtc21vb3RoaW5nOmFudGlhbGlhc2VkfQo6OnNlbGVjdGlvbntiYWNrZ3JvdW5kOnZhcigtLXkpO2NvbG9yOnZhcigtLWluayl9Cjpmb2N1cy12aXNpYmxle291dGxpbmU6M3B4IHNvbGlkIHZhcigtLXJlZCk7b3V0bGluZS1vZmZzZXQ6MnB4fQouc2hlbGx7bWluLWhlaWdodDoxMDB2aDtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1ufQovKiBtYXN0aGVhZCAqLwouc2lkZWJhcntwb3NpdGlvbjpzdGF0aWM7aGVpZ2h0Ojc4cHg7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOnN0cmV0Y2g7Z2FwOjA7YmFja2dyb3VuZDp2YXIoLS1pbmspO2NvbG9yOiNmZmY7cGFkZGluZzowO2ZsZXg6bm9uZX0KLmJyYW5ke2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHg7cGFkZGluZzowIDIwcHg7aGVpZ2h0Ojc4cHg7ZmxleDpub25lfQouYnJhbmQgLm1hcmt7d2lkdGg6MzRweDtoZWlnaHQ6MzRweDtib3JkZXI6MnB4IHNvbGlkIHZhcigtLXkpO2JvcmRlci1yYWRpdXM6NTAlO2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Y29sb3I6dmFyKC0teSl9Ci5icmFuZCBie2Rpc3BsYXk6YmxvY2s7Zm9udDo3MDAgMTRweCB2YXIoLS1zYW5zKTtsZXR0ZXItc3BhY2luZzouMDhlbTt3aGl0ZS1zcGFjZTpub3dyYXB9Ci5icmFuZCBzbWFsbHtkaXNwbGF5Om5vbmV9Cm5hdiNuYXZ7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOnN0cmV0Y2g7Z2FwOjA7aGVpZ2h0Ojc4cHg7cGFkZGluZzowIDhweDtvdmVyZmxvdy14OmF1dG87ZmxleDoxO2p1c3RpZnktY29udGVudDpjZW50ZXJ9Cm5hdiAuZ3JvdXB7ZGlzcGxheTpub25lfQpuYXYgYXtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO3BhZGRpbmc6MCAxMnB4O2NvbG9yOiNjZmNmY2Y7dGV4dC1kZWNvcmF0aW9uOm5vbmU7Zm9udDo2MDAgMTFweCB2YXIoLS1zYW5zKTtsZXR0ZXItc3BhY2luZzouMDhlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7d2hpdGUtc3BhY2U6bm93cmFwO2JvcmRlci1ib3R0b206NHB4IHNvbGlkIHRyYW5zcGFyZW50fQpuYXYgYSBzdmd7ZGlzcGxheTpub25lfQpuYXYgYTpob3Zlcntjb2xvcjojZmZmfQpuYXYgYS5hY3RpdmV7Y29sb3I6dmFyKC0teSk7Ym9yZGVyLWJvdHRvbS1jb2xvcjp2YXIoLS15KX0KLnNpZGViYXItZm9vdHtkaXNwbGF5Om5vbmV9Ci5oZWFkLXJpZ2h0e2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjZweDtwYWRkaW5nOjAgMThweCAwIDZweDtmbGV4Om5vbmV9Ci8qIHRvcCBzdGF0dXMgc3RyaXAgKi8KLndvcmtzcGFjZXttaW4td2lkdGg6MDtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1ufQoudG9wYmFye2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEycHg7cGFkZGluZzo4cHggMjRweDtiYWNrZ3JvdW5kOnZhcigtLWluayk7Y29sb3I6I2M5YzljOTtib3JkZXItdG9wOjFweCBzb2xpZCAjMmEyYTJhfQouY3J1bWJ7Zm9udDoxMXB4IHZhcigtLW1vbm8pO2NvbG9yOiM5YTlhOWF9Ci5jcnVtYiBie2NvbG9yOiNmZmY7Zm9udC13ZWlnaHQ6NTAwfQoudG9wYmFyIC5zcGFjZXJ7ZmxleDoxfQouY21kLWJ0bntkaXNwbGF5Om5vbmU7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo4cHg7Ym9yZGVyOjFweCBzb2xpZCAjM2EzYTNhO2JhY2tncm91bmQ6IzE1MTUxNTtjb2xvcjojYmRiZGJkO3BhZGRpbmc6NHB4IDlweDtmb250OjExcHggdmFyKC0tbW9ubyk7Y3Vyc29yOnBvaW50ZXI7bWluLXdpZHRoOjE2MHB4fQouY21kLWJ0bjpob3Zlcntib3JkZXItY29sb3I6dmFyKC0teSk7Y29sb3I6dmFyKC0teSl9Ci5jbWQtYnRuIGtiZHttYXJnaW4tbGVmdDphdXRvO2JvcmRlcjoxcHggc29saWQgIzQ0NDtwYWRkaW5nOjAgNHB4O2ZvbnQ6MTFweCB2YXIoLS1tb25vKX0KLmNoaXBiYXJ7ZGlzcGxheTpmbGV4O2dhcDo2cHg7ZmxleC13cmFwOndyYXB9Ci5waWxse2Rpc3BsYXk6aW5saW5lLWZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo1cHg7Ym9yZGVyOjFweCBzb2xpZCAjNmE2YTZhO3BhZGRpbmc6M3B4IDhweDtmb250OjExcHggdmFyKC0tbW9ubyk7Y29sb3I6I2YwZjBmMDt3aGl0ZS1zcGFjZTpub3dyYXB9Ci5waWxsIC5kb3R7d2lkdGg6NXB4O2hlaWdodDo1cHg7Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dyb3VuZDp2YXIoLS1ncmVlbil9Ci5waWxsLndhcm4gLmRvdHtiYWNrZ3JvdW5kOnZhcigtLXkpfS5waWxsLmVyciAuZG90e2JhY2tncm91bmQ6dmFyKC0tcmVkKX0ucGlsbC5pbmZvIC5kb3R7YmFja2dyb3VuZDp2YXIoLS1ibHVlKX0KLyogeWVsbG93IHBhZ2UgaGVybyAqLwoubWFpbnt3aWR0aDoxMDAlO21heC13aWR0aDoxNDQwcHg7bWFyZ2luOjAgYXV0bztwYWRkaW5nOjAgMCA0NHB4O21pbi1oZWlnaHQ6ODIycHg7ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbn0KLmhlcm97cG9zaXRpb246cmVsYXRpdmU7YmFja2dyb3VuZDp2YXIoLS15KTtib3JkZXItYm90dG9tOjVweCBzb2xpZCB2YXIoLS1pbmspO3BhZGRpbmc6MTJweCAyOHB4IDEwcHg7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmZsZXgtc3RhcnQ7Z2FwOjIwcHg7aGVpZ2h0OjEyMHB4fQouaGVybyBoMXttYXJnaW46MDtmb250LXNpemU6NTZweDtsaW5lLWhlaWdodDouOTY7bGV0dGVyLXNwYWNpbmc6LS4wMmVtO2ZvbnQtd2VpZ2h0OjcwMH0KLmhlcm8gLnN1YnttYXJnaW46OHB4IDAgMDtmb250LXNpemU6MTNweDttYXgtd2lkdGg6NTJjaH0KLmhlcm8gLm1ldGF7bWFyZ2luLWxlZnQ6YXV0bzttYXJnaW4tdG9wOjIwcHg7dGV4dC1hbGlnbjpyaWdodDtmb250OjExcHgvMS42IHZhcigtLW1vbm8pO2NvbG9yOiMwYTBjMDh9Ci5oZXJvIC5tZXRhIGJ7Y29sb3I6dmFyKC0taW5rKX1cbi5oZXJvLXRvb2xze2Rpc3BsYXk6ZmxleDtnYXA6NnB4O2p1c3RpZnktY29udGVudDpmbGV4LWVuZDttYXJnaW4tYm90dG9tOjZweH1cbi5oZXJvLXRvb2xzIGlucHV0e21pbi1oZWlnaHQ6MjhweDt3aWR0aDoxOTBweDtib3JkZXItY29sb3I6dmFyKC0taW5rKTtiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsLjg1KX1cbi5oZXJvLXRvb2xzIC5idG57bWluLWhlaWdodDoyOHB4fQpoMntmb250OjcwMCAxMC41cHggdmFyKC0tc2Fucyk7bGV0dGVyLXNwYWNpbmc6LjE3ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO21hcmdpbjoxOHB4IDAgOHB4O2Rpc3BsYXk6ZmxleDtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjthbGlnbi1pdGVtczpjZW50ZXJ9CmgyIC5jb3VudHtiYWNrZ3JvdW5kOnZhcigtLWluayk7Y29sb3I6I2ZmZjtwYWRkaW5nOjFweCA3cHg7Zm9udDo3MDAgMTFweCB2YXIoLS1tb25vKX0KaDN7Zm9udC1zaXplOjE0cHg7bWFyZ2luOjAgMCA4cHh9Ci5wYWdlLWJvZHl7cGFkZGluZzowO2ZsZXg6MTtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1ufQoucGFnZS1mb290e3Bvc2l0aW9uOmZpeGVkO2xlZnQ6MDtyaWdodDowO2JvdHRvbTowO2hlaWdodDo0NHB4O2JhY2tncm91bmQ6dmFyKC0taW5rKTtjb2xvcjojZjhmOGVjO2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjIycHg7cGFkZGluZzowIDI4cHg7Zm9udDo1MDAgMTJweCB2YXIoLS1tb25vKTt6LWluZGV4OjgwfQoubGlzdC10b29sc3ttYXJnaW4tbGVmdDphdXRvO2Rpc3BsYXk6ZmxleDtnYXA6NXB4O2FsaWduLWl0ZW1zOmNlbnRlcn0KLmxpc3QtdG9vbHMgaW5wdXR7d2lkdGg6MTIwcHg7bWluLWhlaWdodDoyNHB4O2hlaWdodDoyNHB4O3BhZGRpbmc6MnB4IDZweDtmb250LXNpemU6MTAuNXB4fQoubGlzdC10b29scyAuYnRuLnNtYWxse21pbi1oZWlnaHQ6MjRweDtwYWRkaW5nOjFweCA2cHh9Ci50b29sYmFye2Rpc3BsYXk6ZmxleDtnYXA6OHB4O2FsaWduLWl0ZW1zOmNlbnRlcjttYXJnaW4tYm90dG9tOjEycHg7ZmxleC13cmFwOndyYXB9Ci50b29sYmFyIC5zcGFjZXJ7ZmxleDoxfQppbnB1dFt0eXBlPXRleHRdLGlucHV0W3R5cGU9c2VhcmNoXSxpbnB1dFt0eXBlPXBhc3N3b3JkXSxpbnB1dFt0eXBlPW51bWJlcl0sc2VsZWN0LHRleHRhcmVhe2ZvbnQ6MTJweCB2YXIoLS1zYW5zKTtjb2xvcjp2YXIoLS1pbmspO2JhY2tncm91bmQ6dmFyKC0td2hpdGUpO2JvcmRlcjoycHggc29saWQgdmFyKC0taW5rKTtwYWRkaW5nOjZweCA4cHg7bWluLWhlaWdodDozMnB4O2JvcmRlci1yYWRpdXM6MH0KaW5wdXRbdHlwZT1zZWFyY2hde3dpZHRoOjIzMHB4fQp0ZXh0YXJlYXtmb250OjExLjVweC8xLjY1IHZhcigtLW1vbm8pfQppbnB1dDpmb2N1cyxzZWxlY3Q6Zm9jdXMsdGV4dGFyZWE6Zm9jdXN7b3V0bGluZTpub25lO2JvcmRlci1jb2xvcjp2YXIoLS1yZWQpfQouYnRue2Rpc3BsYXk6aW5saW5lLWZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7Z2FwOjZweDtmb250OjcwMCAxMS41cHggdmFyKC0tc2Fucyk7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1pbmspO2JhY2tncm91bmQ6dmFyKC0td2hpdGUpO2NvbG9yOnZhcigtLWluayk7cGFkZGluZzo1cHggMTJweDttaW4taGVpZ2h0OjMycHg7Y3Vyc29yOnBvaW50ZXI7dGV4dC1kZWNvcmF0aW9uOm5vbmU7Ym9yZGVyLXJhZGl1czowfQouYnRuOmhvdmVye2JhY2tncm91bmQ6dmFyKC0teSl9Ci5idG46YWN0aXZle3RyYW5zZm9ybTp0cmFuc2xhdGVZKDFweCl9Ci5idG46ZGlzYWJsZWR7b3BhY2l0eTouNDtjdXJzb3I6bm90LWFsbG93ZWR9Ci5idG4ucHJpbWFyeXtiYWNrZ3JvdW5kOnZhcigtLXkpfQouYnRuLmRhbmdlcntjb2xvcjp2YXIoLS1yZWQpO2JvcmRlci1jb2xvcjp2YXIoLS1yZWQpfQouYnRuLmRhbmdlcjpob3ZlcntiYWNrZ3JvdW5kOnZhcigtLXJlZCk7Y29sb3I6I2ZmZn0KLmJ0bi5zbWFsbHttaW4taGVpZ2h0OjI1cHg7cGFkZGluZzoycHggN3B4O2ZvbnQtc2l6ZToxMC41cHh9Ci5idG4ubGlua3tib3JkZXI6bm9uZTtiYWNrZ3JvdW5kOm5vbmU7Y29sb3I6dmFyKC0tYmx1ZSk7cGFkZGluZzoycHh9CnRhYmxle3dpZHRoOjEwMCU7Ym9yZGVyLWNvbGxhcHNlOmNvbGxhcHNlO2ZvbnQtc2l6ZToxMi41cHh9CnRoe2ZvbnQ6NzAwIDkuNXB4IHZhcigtLXNhbnMpO2xldHRlci1zcGFjaW5nOi4xNGVtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTt0ZXh0LWFsaWduOmxlZnQ7cGFkZGluZzo3cHggOXB4O2JvcmRlci1ib3R0b206M3B4IHNvbGlkIHZhcigtLWluayk7d2hpdGUtc3BhY2U6bm93cmFwO2JhY2tncm91bmQ6dmFyKC0taW5rKTtjb2xvcjojZmZmfQp0ZHtwYWRkaW5nOjlweDtib3JkZXItYm90dG9tOjFweCBzb2xpZCAjY2ZjZmNmO3ZlcnRpY2FsLWFsaWduOm1pZGRsZX0KdGJvZHkgdHI6aG92ZXJ7YmFja2dyb3VuZDojZmZmOWRkfQoudGFibGUtd3JhcHtiYWNrZ3JvdW5kOnZhcigtLXdoaXRlKTtib3JkZXI6MnB4IHNvbGlkIHZhcigtLWluayl9Ci5uYW1le2ZvbnQtd2VpZ2h0OjcwMDtmb250LXNpemU6MTNweDtjdXJzb3I6cG9pbnRlcn0ubmFtZTpob3Zlcntjb2xvcjp2YXIoLS1yZWQpfQoubW9ub3tmb250OjExcHggdmFyKC0tbW9ubyk7Y29sb3I6IzRkNGQ0ZDt3b3JkLWJyZWFrOmJyZWFrLWFsbH0KLmJhZGdle2Rpc3BsYXk6aW5saW5lLWZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo1cHg7Zm9udDo3MDAgMTFweCB2YXIoLS1tb25vKTtwYWRkaW5nOjFweCA2cHg7d2hpdGUtc3BhY2U6bm93cmFwO2JvcmRlcjoxcHggc29saWQgY3VycmVudENvbG9yfQouYmFkZ2U6OmJlZm9yZXtjb250ZW50OiIiO3dpZHRoOjVweDtoZWlnaHQ6NXB4O2JvcmRlci1yYWRpdXM6NTAlO2JhY2tncm91bmQ6Y3VycmVudENvbG9yfQouYmFkZ2Uub2t7Y29sb3I6dmFyKC0tZ3JlZW4pfS5iYWRnZS5lcnJ7Y29sb3I6dmFyKC0tcmVkKX0uYmFkZ2Uud2Fybntjb2xvcjojOGE2ZDAwfS5iYWRnZS5idWlsZHtjb2xvcjp2YXIoLS1ibHVlKX0uYmFkZ2UuaWRsZXtjb2xvcjp2YXIoLS1tdXRlZCl9Ci5jaGlwe2Rpc3BsYXk6aW5saW5lLWJsb2NrO3BhZGRpbmc6MCA1cHg7Ym9yZGVyOjFweCBzb2xpZCAjYmJiO2ZvbnQ6MTFweCB2YXIoLS1tb25vKTtjb2xvcjojNDQ0O21hcmdpbi1yaWdodDozcHh9Ci5lbXB0eXtwYWRkaW5nOjM0cHggMTZweDt0ZXh0LWFsaWduOmNlbnRlcjtjb2xvcjp2YXIoLS1tdXRlZCl9Ci5lbXB0eSBzdHJvbmd7ZGlzcGxheTpibG9jaztmb250LXNpemU6MTNweDtjb2xvcjp2YXIoLS1pbmspO21hcmdpbi1ib3R0b206NHB4fQouc2tlbGV0b257cGFkZGluZzo4cHh9LnNrZWxldG9uIC5yb3d7aGVpZ2h0OjEwcHg7YmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQoOTBkZWcsI2U4ZTVkYyAyNSUsI2Y0ZjFlOCA1MCUsI2U4ZTVkYyA3NSUpO2JhY2tncm91bmQtc2l6ZToyMDAlIDEwMCU7YW5pbWF0aW9uOnNoaW1tZXIgMS4ycyBsaW5lYXIgaW5maW5pdGU7bWFyZ2luOjlweCAwfQpAa2V5ZnJhbWVzIHNoaW1tZXJ7dG97YmFja2dyb3VuZC1wb3NpdGlvbjotMjAwJSAwfX0KLyogb3ZlcnZpZXcgKi8KLnN0cmlwe2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0KDUsMWZyKTtib3JkZXI6MnB4IHNvbGlkIHZhcigtLWluayk7YmFja2dyb3VuZDp2YXIoLS13aGl0ZSk7bWFyZ2luLWJvdHRvbToxNnB4fQouc3RyaXAgLmNlbGx7cGFkZGluZzoxMHB4IDE0cHg7Ym9yZGVyLXJpZ2h0OjJweCBzb2xpZCB2YXIoLS1pbmspfS5zdHJpcCAuY2VsbDpsYXN0LWNoaWxke2JvcmRlci1yaWdodDpub25lfQouc3RyaXAgLmt7Zm9udDo3MDAgMTFweCB2YXIoLS1zYW5zKTtsZXR0ZXItc3BhY2luZzouMTRlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7Y29sb3I6dmFyKC0tbXV0ZWQpfQouc3RyaXAgLnZ7Zm9udDo3MDAgMjZweC8xLjEgdmFyKC0tbW9ubyl9Ci5ib2FyZHtkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOjEuNTVmciAxZnI7Z2FwOjIwcHg7YWxpZ24taXRlbXM6c3RhcnR9Ci5jaGFydC1ib2FyZHtib3JkZXI6MnB4IHNvbGlkIHZhcigtLWluayk7YmFja2dyb3VuZDp2YXIoLS13aGl0ZSk7cGFkZGluZzoxMHB4IDEycHh9Ci5jaGFydC1oZWFke2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpiYXNlbGluZTtnYXA6OHB4O21hcmdpbjo0cHggMCAycHh9Ci5jaGFydC1oZWFkIGJ7Zm9udC1zaXplOjEycHh9LmNoYXJ0LWhlYWQgc3Bhbntmb250OjExcHggdmFyKC0tbW9ubyk7Y29sb3I6dmFyKC0tbXV0ZWQpfQouc2lnbmFse2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6MTBweCAxZnIgYXV0bztnYXA6OHB4O3BhZGRpbmc6N3B4IDJweDtib3JkZXItYm90dG9tOjFweCBzb2xpZCAjZDRkMGM0O2FsaWduLWl0ZW1zOmJhc2VsaW5lfQouc2lnbmFsIC5tYXJre3dpZHRoOjZweDtoZWlnaHQ6NnB4O2JhY2tncm91bmQ6dmFyKC0tZ3JlZW4pO21hcmdpbi10b3A6NXB4fQouc2lnbmFsLndhcm4gLm1hcmt7YmFja2dyb3VuZDp2YXIoLS15KX0uc2lnbmFsLmVyciAubWFya3tiYWNrZ3JvdW5kOnZhcigtLXJlZCl9Ci5zaWduYWwgLnR7Zm9udC1zaXplOjEycHh9LnNpZ25hbCAudXtmb250OjExcHggdmFyKC0tbW9ubyk7Y29sb3I6dmFyKC0tbXV0ZWQpfQovKiBtYXN0ZXItZGV0YWlsICovCi5tYXN0ZXJ7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczozODBweCBtaW5tYXgoMCwxZnIpO2dhcDo1cHg7YmFja2dyb3VuZDp2YXIoLS1pbmspO2JvcmRlci10b3A6NXB4IHNvbGlkIHZhcigtLWluayk7bWluLWhlaWdodDo2NDBweDtmbGV4OjF9Ci5tYXN0ZXItbGlzdHtiYWNrZ3JvdW5kOnZhcigtLXdoaXRlKTtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1ufQoubWFzdGVyLWxpc3QgaDJ7bWFyZ2luOjA7cGFkZGluZzoxMHB4IDE0cHg7YmFja2dyb3VuZDp2YXIoLS1pbmspO2NvbG9yOnZhcigtLXkpO2Rpc3BsYXk6ZmxleH0KLm1hc3Rlci1saXN0IGgyIC5jb3VudHtiYWNrZ3JvdW5kOnZhcigtLXkpO2NvbG9yOnZhcigtLWluayl9Ci5tcm93e2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6NDRweCBtaW5tYXgoMCwxZnIpIGF1dG87Z2FwOjEwcHg7YWxpZ24taXRlbXM6Y2VudGVyO3BhZGRpbmc6MTFweCAxNHB4O2JvcmRlci1ib3R0b206MnB4IHNvbGlkIHZhcigtLWluayk7Y3Vyc29yOnBvaW50ZXI7YmFja2dyb3VuZDp2YXIoLS13aGl0ZSl9Ci5tcm93OmhvdmVye2JhY2tncm91bmQ6I2ZmZjlkZH0KLm1yb3cuc2Vse2JhY2tncm91bmQ6dmFyKC0teSl9Ci5tcm93IC5udW17Zm9udDo3MDAgMjBweCB2YXIoLS1tb25vKTtjb2xvcjojOGE4YThhfS5tcm93LnNlbCAubnVte2NvbG9yOnZhcigtLWluayl9Ci5tcm93IC5ubXtmb250LXdlaWdodDo3MDA7Zm9udC1zaXplOjE0cHh9Lm1yb3cgLm10e2ZvbnQ6MTFweCB2YXIoLS1tb25vKTtjb2xvcjojNTU1fQoubXJvdyAuc3R7Zm9udDo3MDAgMTFweCB2YXIoLS1zYW5zKTtsZXR0ZXItc3BhY2luZzouMWVtO2NvbG9yOnZhcigtLWdyZWVuKX0KLmluc3BlY3RvcntiYWNrZ3JvdW5kOnZhcigtLXBhcGVyKTtwYWRkaW5nOjE4cHggMjRweDttaW4td2lkdGg6MH0KLmluc3AtaGVhZHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6ZmxleC1zdGFydDtnYXA6MTZweDtib3JkZXItYm90dG9tOjVweCBzb2xpZCB2YXIoLS1pbmspO3BhZGRpbmctYm90dG9tOjEycHh9Ci5pbnNwLWhlYWQgLmdhdGV7Zm9udDo3MDAgNjRweC8wLjkgdmFyKC0tbW9ubyk7bGV0dGVyLXNwYWNpbmc6LS4wNWVtfQouaW5zcC1oZWFkIGgye21hcmdpbjowO2ZvbnQtc2l6ZToyNnB4O2xldHRlci1zcGFjaW5nOi0uMDFlbX0KLmluc3AtaGVhZCAuc3Vie21hcmdpbjo0cHggMCAwO2ZvbnQ6MTEuNXB4IHZhcigtLW1vbm8pO2NvbG9yOiM1NTV9Ci5pbnNwLWhlYWQgLnN0YXR1c3ttYXJnaW4tbGVmdDphdXRvO2JhY2tncm91bmQ6dmFyKC0tZ3JlZW4pO2NvbG9yOiNmZmY7cGFkZGluZzo1cHggOXB4O2ZvbnQ6NzAwIDExcHggdmFyKC0tc2Fucyk7bGV0dGVyLXNwYWNpbmc6LjEyZW19Ci50YWJze2Rpc3BsYXk6ZmxleDtnYXA6MjBweDttYXJnaW46MTJweCAwIDEycHh9Ci50YWJzIGJ1dHRvbntib3JkZXI6bm9uZTtiYWNrZ3JvdW5kOm5vbmU7Zm9udDo3MDAgMTJweCB2YXIoLS1zYW5zKTtjb2xvcjojNjY2O3BhZGRpbmc6MCAwIDVweDtjdXJzb3I6cG9pbnRlcjtib3JkZXItYm90dG9tOjNweCBzb2xpZCB0cmFuc3BhcmVudH0KLnRhYnMgYnV0dG9uLmFjdGl2ZXtjb2xvcjp2YXIoLS1pbmspO2JvcmRlci1ib3R0b20tY29sb3I6dmFyKC0tcmVkKX0KLmtwaXN7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQoNCwxZnIpO2JvcmRlcjozcHggc29saWQgdmFyKC0taW5rKTtiYWNrZ3JvdW5kOnZhcigtLXdoaXRlKTttYXJnaW4tYm90dG9tOjEycHh9Ci5rcGl7cGFkZGluZzo4cHggMTJweDtib3JkZXItcmlnaHQ6MnB4IHNvbGlkIHZhcigtLWluayl9LmtwaTpsYXN0LWNoaWxke2JvcmRlci1yaWdodDpub25lfQoua3BpIC5re2ZvbnQ6NzAwIDkuNXB4IHZhcigtLXNhbnMpO2xldHRlci1zcGFjaW5nOi4xNGVtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTtjb2xvcjojNjY2fQoua3BpIC52e2ZvbnQ6NzAwIDI0cHgvMS4yIHZhcigtLW1vbm8pfQoubG9nLWJveHtiYWNrZ3JvdW5kOiMxMDEwMTA7Y29sb3I6I2QyZjg5ODtmb250OjExLjVweC8xLjc1IHZhcigtLW1vbm8pO3BhZGRpbmc6MTJweCAxNHB4O21pbi1oZWlnaHQ6MjM0cHg7bWF4LWhlaWdodDozMzBweDtvdmVyZmxvdzphdXRvO3doaXRlLXNwYWNlOnByZS13cmFwO3dvcmQtYnJlYWs6YnJlYWstYWxsO2JvcmRlcjozcHggc29saWQgdmFyKC0taW5rKX0KLmxvZy1ib3ggLm11dGVke2NvbG9yOiM3ZDdkN2R9Ci5qc29uLXZpZXd7YmFja2dyb3VuZDp2YXIoLS13aGl0ZSk7Ym9yZGVyOjNweCBzb2xpZCB2YXIoLS1pbmspO3BhZGRpbmc6MTFweCAxM3B4O2ZvbnQ6MTFweC8xLjcgdmFyKC0tbW9ubyk7d2hpdGUtc3BhY2U6cHJlLXdyYXA7bWF4LWhlaWdodDo0MjBweDtvdmVyZmxvdzphdXRvfQouYWN0aW9uc3tkaXNwbGF5OmZsZXg7Z2FwOjhweDttYXJnaW4tdG9wOjA7cGFkZGluZy10b3A6MTFweDtmbGV4LXdyYXA6d3JhcH0uYWN0aW9ucyAuYnRue21pbi1oZWlnaHQ6MzZweDtib3JkZXItd2lkdGg6M3B4O3BhZGRpbmc6NHB4IDhweDtmb250LXNpemU6MTFweH0uYWN0aW9ucyAuYnRuOm50aC1jaGlsZCgxKXt3aWR0aDo4MnB4fS5hY3Rpb25zIC5idG46bnRoLWNoaWxkKDIpe3dpZHRoOjgycHh9LmFjdGlvbnMgLmJ0bjpudGgtY2hpbGQoMyl7d2lkdGg6OTZweH0KLm1ldGEtZ3JpZHtkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOnJlcGVhdCgyLG1pbm1heCgwLDFmcikpO2dhcDo2cHggMjBweDtmb250LXNpemU6MTJweDttYXJnaW46MTBweCAwfQoubWV0YS1ncmlkIGR0e2ZvbnQ6NzAwIDkuNXB4IHZhcigtLXNhbnMpO2xldHRlci1zcGFjaW5nOi4xMmVtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTtjb2xvcjojNjY2fQoubWV0YS1ncmlkIGRke21hcmdpbjowIDAgNHB4O2ZvbnQ6MTEuNXB4IHZhcigtLW1vbm8pO3dvcmQtYnJlYWs6YnJlYWstYWxsfQovKiB0ZXN0ZXIgKi8KLnRlc3QtZ3JpZHtkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOjIzMHB4IG1pbm1heCgwLDFmcikgMzkwcHg7Z2FwOjNweDtiYWNrZ3JvdW5kOnZhcigtLWluayk7Ym9yZGVyLXRvcDo1cHggc29saWQgdmFyKC0taW5rKX0KLnRlc3QtY29se2JhY2tncm91bmQ6dmFyKC0td2hpdGUpO3BhZGRpbmc6MTJweCAxNHB4O21pbi1oZWlnaHQ6NjIwcHh9Ci50ZXN0LW1haW57cGFkZGluZzoxNnB4IDIwcHg7bWluLXdpZHRoOjB9Ci50ZXN0LXNpZGV7YmFja2dyb3VuZDp2YXIoLS13aGl0ZSk7cGFkZGluZzoxNHB4O21pbi13aWR0aDowfQoucmFpbC10aXRsZXtmb250OjcwMCAxMXB4IHZhcigtLXNhbnMpO2xldHRlci1zcGFjaW5nOi4xNmVtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTttYXJnaW46MTRweCAwIDdweH0KLmNvbGwtbmFtZXtmb250LXNpemU6MTEuNXB4O2ZvbnQtd2VpZ2h0OjcwMDttYXJnaW46OHB4IDAgM3B4fQoucmVxLWl0ZW17ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczozNHB4IG1pbm1heCgwLDFmcikgMTZweDtnYXA6NnB4O2FsaWduLWl0ZW1zOmNlbnRlcjtib3JkZXItYm90dG9tOjFweCBzb2xpZCAjZDRkMGM0O3BhZGRpbmc6NXB4IDB9Ci5yZXEtaXRlbSAubXtmb250OjcwMCA5cHggdmFyKC0tbW9ubyk7Y29sb3I6dmFyKC0tcmVkKX0KLnJlcS1pdGVtIGJ1dHRvbntib3JkZXI6bm9uZTtiYWNrZ3JvdW5kOm5vbmU7dGV4dC1hbGlnbjpsZWZ0O2ZvbnQ6MTEuNXB4IHZhcigtLXNhbnMpO2NvbG9yOiM0NDQ7Y3Vyc29yOnBvaW50ZXI7cGFkZGluZzowfQoudXJsYmFye2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6OTJweCAxZnIgYXV0bztib3JkZXI6M3B4IHNvbGlkIHZhcigtLWluayk7YmFja2dyb3VuZDp2YXIoLS13aGl0ZSl9Ci51cmxiYXIgc2VsZWN0LC51cmxiYXIgaW5wdXR7Ym9yZGVyOm5vbmU7bWluLWhlaWdodDozOHB4fQoudXJsYmFyIHNlbGVjdHtib3JkZXItcmlnaHQ6MnB4IHNvbGlkIHZhcigtLWluayk7Zm9udDo3MDAgMTFweCB2YXIoLS1tb25vKTtjb2xvcjp2YXIoLS1yZWQpfQoudXJsYmFyIC5idG57Ym9yZGVyOm5vbmU7Ym9yZGVyLWxlZnQ6M3B4IHNvbGlkIHZhcigtLWluayk7YmFja2dyb3VuZDp2YXIoLS15KTtjb2xvcjp2YXIoLS1pbmspO3BhZGRpbmc6MCAxOHB4fQoua3YtdGFibGV7d2lkdGg6MTAwJTtib3JkZXItY29sbGFwc2U6Y29sbGFwc2U7YmFja2dyb3VuZDp2YXIoLS13aGl0ZSk7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1pbmspfQoua3YtdGFibGUgdGR7Ym9yZGVyLWJvdHRvbToxcHggc29saWQgI2Q0ZDBjNDtwYWRkaW5nOjB9Lmt2LXRhYmxlIHRkIGlucHV0e2JvcmRlcjpub25lO2JhY2tncm91bmQ6dHJhbnNwYXJlbnQ7d2lkdGg6MTAwJTttaW4taGVpZ2h0OjMwcHh9Ci5rdi10YWJsZSAueHt3aWR0aDoyOHB4O3RleHQtYWxpZ246Y2VudGVyfS5rdi10YWJsZSAueCBidXR0b257Ym9yZGVyOm5vbmU7YmFja2dyb3VuZDpub25lO2N1cnNvcjpwb2ludGVyO2NvbG9yOiM3Nzd9Ci5yZXNwLWhlYWR7ZGlzcGxheTpmbGV4O2dhcDo4cHg7YWxpZ24taXRlbXM6Y2VudGVyO2JvcmRlcjozcHggc29saWQgdmFyKC0taW5rKTtiYWNrZ3JvdW5kOnZhcigtLXdoaXRlKTtwYWRkaW5nOjZweCA5cHg7Ym9yZGVyLWJvdHRvbTpub25lfQoucmVzcC1jb2Rle2ZvbnQ6NzAwIDEycHggdmFyKC0tbW9ubyk7Y29sb3I6dmFyKC0tZ3JlZW4pfS5yZXNwLWNvZGUuYmFke2NvbG9yOnZhcigtLXJlZCl9Ci5yZXNwLW1ldGF7Zm9udDoxMXB4IHZhcigtLW1vbm8pO2NvbG9yOiM1NTV9Ci5yZXNwLWJvZHl7YmFja2dyb3VuZDojMTAxMDEwO2NvbG9yOiNkMmY4OTg7Zm9udDoxMXB4LzEuNyB2YXIoLS1tb25vKTtib3JkZXI6M3B4IHNvbGlkIHZhcigtLWluayk7cGFkZGluZzoxMXB4O21heC1oZWlnaHQ6MzkwcHg7b3ZlcmZsb3c6YXV0bzt3aGl0ZS1zcGFjZTpwcmUtd3JhcDt3b3JkLWJyZWFrOmJyZWFrLWFsbH0KLnJlc3AtdGFic3tkaXNwbGF5OmZsZXg7Z2FwOjEwcHg7bWFyZ2luOjZweCAwfQoucmVzcC10YWJzIGJ1dHRvbntib3JkZXI6bm9uZTtiYWNrZ3JvdW5kOm5vbmU7Zm9udDo3MDAgMTAuNXB4IHZhcigtLXNhbnMpO2NvbG9yOiM2NjY7cGFkZGluZzowIDAgM3B4O2N1cnNvcjpwb2ludGVyfQoucmVzcC10YWJzIGJ1dHRvbi5hY3RpdmV7Y29sb3I6dmFyKC0taW5rKTtib3JkZXItYm90dG9tOjJweCBzb2xpZCB2YXIoLS1yZWQpfQovKiBnaXRvcHMgKi8KLnBpcGVsaW5le2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0KDQsMWZyKTtnYXA6MnB4O2JvcmRlcjozcHggc29saWQgdmFyKC0taW5rKTtiYWNrZ3JvdW5kOnZhcigtLWluayk7bWFyZ2luLWJvdHRvbToxNHB4fQouc3RhZ2V7cGFkZGluZzo5cHggMTJweDtiYWNrZ3JvdW5kOnZhcigtLXdoaXRlKTtwb3NpdGlvbjpyZWxhdGl2ZX0KLnN0YWdlIC5ue2ZvbnQ6NzAwIDkuNXB4IHZhcigtLXNhbnMpO2xldHRlci1zcGFjaW5nOi4xNWVtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTtjb2xvcjojNjY2fQouc3RhZ2UgLmR7Zm9udC13ZWlnaHQ6NzAwO2ZvbnQtc2l6ZToxMi41cHg7bWFyZ2luLXRvcDoycHh9LnN0YWdlIC5ze2ZvbnQ6MTFweCB2YXIoLS1tb25vKTtjb2xvcjojNjY2fQouc3RhZ2U6OmFmdGVye2NvbnRlbnQ6IiI7cG9zaXRpb246YWJzb2x1dGU7cmlnaHQ6LTdweDt0b3A6NTAlO3dpZHRoOjlweDtoZWlnaHQ6MnB4O2JhY2tncm91bmQ6dmFyKC0tcmVkKX0KLnN0YWdlOmxhc3QtY2hpbGQ6OmFmdGVye2Rpc3BsYXk6bm9uZX0KLnNvdXJjZS1yb3d7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczoxLjJmciAxLjZmciAuN2ZyIC43ZnIgYXV0bztnYXA6MTJweDthbGlnbi1pdGVtczpjZW50ZXI7Ym9yZGVyLWJvdHRvbToycHggc29saWQgdmFyKC0taW5rKTtwYWRkaW5nOjEwcHggNHB4O2JhY2tncm91bmQ6dmFyKC0td2hpdGUpfQouc291cmNlLXJvdzpob3ZlcntiYWNrZ3JvdW5kOiNmZmY5ZGR9LnNvdXJjZS1yb3cgLm5te2ZvbnQtd2VpZ2h0OjcwMDtmb250LXNpemU6MTIuNXB4fQouYnJvd3Nlci1ncmlke2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6MjUwcHggbWlubWF4KDAsMWZyKSAzMDBweDtnYXA6MTRweH0KLmJyb3dzZXItZmlsZXtib3JkZXItYm90dG9tOjFweCBzb2xpZCAjZDRkMGM0O3BhZGRpbmc6NXB4IDNweDtmb250OjExcHggdmFyKC0tbW9ubyk7Y3Vyc29yOnBvaW50ZXJ9LmJyb3dzZXItZmlsZTpob3Zlcntjb2xvcjp2YXIoLS1yZWQpfQovKiBkaWFsb2dzICYgdG9hc3QgKi8KLm1vZGFse2JvcmRlcjozcHggc29saWQgdmFyKC0taW5rKTtiYWNrZ3JvdW5kOnZhcigtLXBhcGVyKTtwYWRkaW5nOjA7d2lkdGg6bWluKDc2MHB4LDk0dncpO2NvbG9yOnZhcigtLWluayl9Ci5tb2RhbDo6YmFja2Ryb3B7YmFja2dyb3VuZDpyZ2JhKDExLDExLDExLC42KX0KLm1vZGFsLXBhZHtwYWRkaW5nOjE2cHggMThweH0ubW9kYWwgaDN7bWFyZ2luOjAgMCAxMHB4fQouZm9ybS1ncmlke2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6MWZyIDFmcjtnYXA6MTBweCAxMnB4fS5mb3JtLWdyaWQgLmZ1bGx7Z3JpZC1jb2x1bW46MS8tMX0KLmZvcm0tZ3JpZCBsYWJlbHtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1uO2dhcDozcHg7Zm9udDo3MDAgMTFweCB2YXIoLS1zYW5zKTtjb2xvcjojNTU1fQoubW9kZS1zd2l0Y2h7ZGlzcGxheTpmbGV4O2JvcmRlcjoycHggc29saWQgdmFyKC0taW5rKTt3aWR0aDptYXgtY29udGVudDttYXJnaW4tYm90dG9tOjEwcHh9Ci5tb2RlLXN3aXRjaCBidXR0b257Ym9yZGVyOm5vbmU7YmFja2dyb3VuZDp2YXIoLS13aGl0ZSk7Zm9udDo3MDAgMTAuNXB4IHZhcigtLXNhbnMpO3BhZGRpbmc6NHB4IDEwcHg7Y3Vyc29yOnBvaW50ZXJ9Ci5tb2RlLXN3aXRjaCBidXR0b24uYWN0aXZle2JhY2tncm91bmQ6dmFyKC0teSl9Ci50b2FzdHN7cG9zaXRpb246Zml4ZWQ7cmlnaHQ6MTZweDtib3R0b206MTZweDtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1uO2dhcDo2cHg7ei1pbmRleDoyMDB9Ci50b2FzdHtiYWNrZ3JvdW5kOnZhcigtLWluayk7Y29sb3I6I2ZmZjtwYWRkaW5nOjhweCAxMnB4O2ZvbnQtc2l6ZToxMS41cHg7Ym9yZGVyLXRvcDo0cHggc29saWQgdmFyKC0teSl9Ci50b2FzdC5lcnJ7Ym9yZGVyLXRvcC1jb2xvcjp2YXIoLS1yZWQpfQouY21ka3tib3JkZXI6M3B4IHNvbGlkIHZhcigtLWluayk7YmFja2dyb3VuZDp2YXIoLS1wYXBlcik7cGFkZGluZzowO3dpZHRoOm1pbig1NjBweCw5NHZ3KX0KLmNtZGs6OmJhY2tkcm9we2JhY2tncm91bmQ6cmdiYSgxMSwxMSwxMSwuNil9Ci5jbWRrIGlucHV0e3dpZHRoOjEwMCU7Ym9yZGVyOm5vbmU7Ym9yZGVyLWJvdHRvbToycHggc29saWQgdmFyKC0taW5rKTtwYWRkaW5nOjEycHg7Zm9udDoxMnB4IHZhcigtLW1vbm8pO2JhY2tncm91bmQ6dmFyKC0td2hpdGUpfQouY21kayAubGlzdHttYXgtaGVpZ2h0OjM2MHB4O292ZXJmbG93OmF1dG87cGFkZGluZzo0cHh9Ci5jbWRrIC5pdGVte2Rpc3BsYXk6ZmxleDtnYXA6MTBweDtwYWRkaW5nOjdweCAxMHB4O2ZvbnQtc2l6ZToxMnB4O2N1cnNvcjpwb2ludGVyfQouY21kayAuaXRlbTpob3ZlciwuY21kayAuaXRlbS5hY3RpdmV7YmFja2dyb3VuZDp2YXIoLS15KX0KLmNtZGsgLml0ZW0gLmt7Zm9udDoxMXB4IHZhcigtLW1vbm8pO2NvbG9yOiM2NjY7bWFyZ2luLWxlZnQ6YXV0b30KQGtleWZyYW1lcyByaXNle2Zyb217b3BhY2l0eTowO3RyYW5zZm9ybTp0cmFuc2xhdGVZKDRweCl9dG97b3BhY2l0eToxO3RyYW5zZm9ybTpub25lfX0KLm1haW4+KnthbmltYXRpb246cmlzZSAuM3MgY3ViaWMtYmV6aWVyKC4xNiwuODQsLjMsMSkgYm90aH0KQG1lZGlhKG1heC13aWR0aDoxMTAwcHgpey5ib2FyZHtncmlkLXRlbXBsYXRlLWNvbHVtbnM6MWZyfS5tYXN0ZXJ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOjMyMHB4IG1pbm1heCgwLDFmcil9LnRlc3QtZ3JpZHtncmlkLXRlbXBsYXRlLWNvbHVtbnM6MjAwcHggbWlubWF4KDAsMWZyKX0udGVzdC1zaWRle2dyaWQtY29sdW1uOjEvLTE7Ym9yZGVyLWxlZnQ6bm9uZTtib3JkZXItdG9wOjNweCBzb2xpZCB2YXIoLS1pbmspfX0KQG1lZGlhKG1heC13aWR0aDo5MDBweCl7LnNpZGViYXJ7aGVpZ2h0OmF1dG87ZmxleC13cmFwOndyYXB9LmJyYW5ke2hlaWdodDo1MnB4fS5icmFuZCBie2ZvbnQtc2l6ZToxMnB4fW5hdiNuYXZ7aGVpZ2h0OmF1dG87ZmxleC13cmFwOndyYXA7b3ZlcmZsb3c6dmlzaWJsZTtvcmRlcjozO3dpZHRoOjEwMCU7cGFkZGluZzowIDhweCA0cHh9bmF2IGF7aGVpZ2h0OjMycHg7cGFkZGluZzowIDhweDtmb250LXNpemU6MTFweH0uaGVhZC1yaWdodHtwYWRkaW5nOjZweCAxMnB4O21hcmdpbi1sZWZ0OmF1dG99LmNtZC1idG57bWluLXdpZHRoOjB9LmNydW1ie2Rpc3BsYXk6bm9uZX0ubWFpbntwYWRkaW5nOjAgMCAzMHB4fS5oZXJve3BhZGRpbmc6MTRweCAxNHB4O21pbi1oZWlnaHQ6OTJweH0uaGVybyBoMXtmb250LXNpemU6MzZweH0uaGVybyAubWV0YXttYXJnaW4tbGVmdDowO3RleHQtYWxpZ246bGVmdDt3aWR0aDoxMDAlfS5wYWdlLWJvZHl7cGFkZGluZzoxMnB4IDE0cHggMH0uc3RyaXB7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOnJlcGVhdCgyLDFmcil9Lm1hc3RlcntncmlkLXRlbXBsYXRlLWNvbHVtbnM6MWZyfS5tYXN0ZXItbGlzdHtib3JkZXItcmlnaHQ6bm9uZTtib3JkZXItYm90dG9tOjVweCBzb2xpZCB2YXIoLS1pbmspO21heC1oZWlnaHQ6MzAwcHg7b3ZlcmZsb3c6YXV0b30uaW5zcGVjdG9ye3BhZGRpbmc6MTRweH0udGVzdC1ncmlke2dyaWQtdGVtcGxhdGUtY29sdW1uczoxZnJ9LnRlc3QtY29se2JvcmRlci1yaWdodDpub25lO2JvcmRlci1ib3R0b206M3B4IHNvbGlkIHZhcigtLWluayk7bWluLWhlaWdodDowfS50ZXN0LW1haW57cGFkZGluZzoxMnB4fS50ZXN0LXNpZGV7cGFkZGluZzoxMnB4fS5waXBlbGluZXtncmlkLXRlbXBsYXRlLWNvbHVtbnM6MWZyIDFmcn0uc291cmNlLXJvd3tncmlkLXRlbXBsYXRlLWNvbHVtbnM6MWZyIDFmcn0uZm9ybS1ncmlke2dyaWQtdGVtcGxhdGUtY29sdW1uczoxZnJ9LmtwaXN7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOjFmciAxZnJ9fQoKQG1lZGlhKG1heC13aWR0aDo5MDBweCl7LmFjdGlvbnMgLmJ0bnttaW4taGVpZ2h0OjQ0cHg7cGFkZGluZzo4cHggMTBweH19CgoKPC9zdHlsZT4KPC9oZWFkPgo8Ym9keT4KPGRpdiBjbGFzcz0ic2hlbGwiPgogIDxoZWFkZXIgY2xhc3M9InNpZGViYXIiPgogICAgPGRpdiBjbGFzcz0iYnJhbmQiPjxzcGFuIGNsYXNzPSJtYXJrIj48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgd2lkdGg9IjE2IiBoZWlnaHQ9IjE2IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMTMgMiA0LjUgMTMuNUgxMUw5LjUgMjIgMTkgOS41aC02LjVMMTMgMloiLz48L3N2Zz48L3NwYW4+PHNwYW4+PGI+RklTU0lPTiBDT05TT0xFPC9iPjxzbWFsbD5ob21lbGFiPC9zbWFsbD48L3NwYW4+PC9kaXY+CiAgICA8bmF2IGlkPSJuYXYiIGFyaWEtbGFiZWw9IuS4u+WvvOiIqiI+CiAgICAgIDxhIGhyZWY9IiMvb3ZlcnZpZXciIGRhdGEtdmlldz0ib3ZlcnZpZXciPuaAu+iniDwvYT4KICAgICAgPGEgaHJlZj0iIy9mdW5jdGlvbnMiIGRhdGEtdmlldz0iZnVuY3Rpb25zIj5GdW5jdGlvbnM8L2E+CiAgICAgIDxhIGhyZWY9IiMvcGFja2FnZXMiIGRhdGEtdmlldz0icGFja2FnZXMiPlBhY2thZ2VzPC9hPgogICAgICA8YSBocmVmPSIjL2Vudmlyb25tZW50cyIgZGF0YS12aWV3PSJlbnZpcm9ubWVudHMiPkVudmlyb25tZW50czwvYT4KICAgICAgPGEgaHJlZj0iIy9odHRwdHJpZ2dlcnMiIGRhdGEtdmlldz0iaHR0cHRyaWdnZXJzIj5IVFRQIFRyaWdnZXJzPC9hPgogICAgICA8YSBocmVmPSIjL3RpbWV0cmlnZ2VycyIgZGF0YS12aWV3PSJ0aW1ldHJpZ2dlcnMiPlRpbWVyIFRyaWdnZXJzPC9hPgogICAgICA8YSBocmVmPSIjL3Rlc3RlciIgZGF0YS12aWV3PSJ0ZXN0ZXIiPkFQSSDmtYvor5Xlj7A8L2E+CiAgICAgIDxhIGhyZWY9IiMvZ2l0b3BzIiBkYXRhLXZpZXc9ImdpdG9wcyI+R2l0T3BzPC9hPgogICAgPC9uYXY+CiAgICA8ZGl2IGNsYXNzPSJoZWFkLXJpZ2h0Ij4KICAgICAgPHNwYW4gY2xhc3M9ImNydW1iIiBoaWRkZW4+Zmlzc2lvbiAvIDxiIGlkPSJjcnVtYiI+5oC76KeIPC9iPjwvc3Bhbj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iY21kLWJ0biIgaWQ9ImNtZGstb3BlbiI+5ZG95LukIDxrYmQ+Q3RybCBLPC9rYmQ+PC9idXR0b24+CiAgICAgIDxzcGFuIGNsYXNzPSJwaWxsIiBpZD0idG9wLWNsdXN0ZXIiPjxzcGFuIGNsYXNzPSJkb3QiPjwvc3Bhbj7ov57mjqXkuK08L3NwYW4+CiAgICAgIDxzcGFuIGNsYXNzPSJwaWxsIGluZm8iIGlkPSJ0b3Atc291cmNlIj48c3BhbiBjbGFzcz0iZG90Ij48L3NwYW4+5LuT5bqT5pyq6YWN572uPC9zcGFuPgogICAgPC9kaXY+CiAgPC9oZWFkZXI+CiAgPGRpdiBjbGFzcz0id29ya3NwYWNlIj4KICAgIDxtYWluIGlkPSJtYWluIiBjbGFzcz0ibWFpbiIgdGFiaW5kZXg9Ii0xIj48L21haW4+CiAgPC9kaXY+CjwvZGl2Pgo8ZGlhbG9nIGlkPSJjbWRrIiBjbGFzcz0iY21kayI+CiAgPGlucHV0IGlkPSJjbWRrLWlucHV0IiBwbGFjZWhvbGRlcj0i5pCc57Si6aG16Z2i44CB6LWE5rqQ5oiW5pON5L2c4oCmIiBhdXRvY29tcGxldGU9Im9mZiI+CiAgPGRpdiBjbGFzcz0ibGlzdCIgaWQ9ImNtZGstbGlzdCI+PC9kaXY+CjwvZGlhbG9nPgo8ZGl2IGlkPSJ0b2FzdHMiIGNsYXNzPSJ0b2FzdHMiIGFyaWEtbGl2ZT0icG9saXRlIj48L2Rpdj4KPGRpYWxvZyBpZD0ibW9kYWwiIGNsYXNzPSJtb2RhbCI+PGRpdiBpZD0ibW9kYWwtYm9keSI+PC9kaXY+PC9kaWFsb2c+CjxzY3JpcHQ+Ci8qIEZpc3Npb24gQ29uc29sZSAtIHZhbmlsbGEgSlMsIG5vIGJ1aWxkIHN0ZXAgKi8KInVzZSBzdHJpY3QiOwpjb25zdCAkID0gKHNlbCwgZWw9ZG9jdW1lbnQpID0+IGVsLnF1ZXJ5U2VsZWN0b3Ioc2VsKTsKY29uc3QgJCQgPSAoc2VsLCBlbD1kb2N1bWVudCkgPT4gWy4uLmVsLnF1ZXJ5U2VsZWN0b3JBbGwoc2VsKV07CmNvbnN0IEtJTkRTID0gWyJmdW5jdGlvbnMiLCJwYWNrYWdlcyIsImVudmlyb25tZW50cyIsImh0dHB0cmlnZ2VycyIsInRpbWV0cmlnZ2VycyJdOwpjb25zdCBMQUJFTFMgPSB7ZnVuY3Rpb25zOiJGdW5jdGlvbnMiLHBhY2thZ2VzOiJQYWNrYWdlcyIsZW52aXJvbm1lbnRzOiJFbnZpcm9ubWVudHMiLAogIGh0dHB0cmlnZ2VyczoiSFRUUCBUcmlnZ2VycyIsdGltZXRyaWdnZXJzOiJUaW1lciBUcmlnZ2VycyJ9OwoKY29uc3QgQkFTRSA9IChsb2NhdGlvbi5wYXRobmFtZS5yZXBsYWNlKC9cLyskLywgIiIpIHx8ICIvY29uc29sZSIpOwpmdW5jdGlvbiBidWlsZE9wKG1ldGhvZCwgdXJsLCBib2R5KXsKICBjb25zdCBbcGF0aCwgcXNdID0gdXJsLnNwbGl0KCI/Iik7CiAgY29uc3QgcSA9IG5ldyBVUkxTZWFyY2hQYXJhbXMocXMgfHwgIiIpOwogIGNvbnN0IHBhcnRzID0gcGF0aC5zcGxpdCgiLyIpLmZpbHRlcihCb29sZWFuKTsgLy8gYXBpLCAuLi4KICBpZiAocGFydHNbMV0gPT09ICJvdmVydmlldyIpIHJldHVybiB7b3A6Im92ZXJ2aWV3In07CiAgaWYgKHBhcnRzWzFdID09PSAibWV0cmljcyIpIHJldHVybiB7b3A6Im1ldHJpY3MifTsKICBpZiAocGFydHNbMV0gPT09ICJjb25maWciKSByZXR1cm4gbWV0aG9kID09PSAiR0VUIiA/IHtvcDoiY29uZmlnX2dldCJ9IDoge29wOiJjb25maWdfcHV0IiwgY2ZnOmJvZHl9OwogIGlmIChwYXJ0c1sxXSA9PT0gImludm9rZSIpIHJldHVybiBPYmplY3QuYXNzaWduKHtvcDoiaW52b2tlIn0sIGJvZHkpOwogIGlmIChwYXJ0c1sxXSA9PT0gInRlc3RkYXRhIikgcmV0dXJuIG1ldGhvZCA9PT0gIkdFVCIgPyB7b3A6InRlc3RkYXRhX2dldCJ9IDoge29wOiJ0ZXN0ZGF0YV9wdXQiLCBkYXRhOiBib2R5LmRhdGF9OwogIGlmIChwYXJ0c1sxXSA9PT0gInNvdXJjZS10b2tlbiIpIHJldHVybiB7b3A6InNvdXJjZV90b2tlbl9wdXQiLCBuYW1lOiBib2R5Lm5hbWUsIHRva2VuOiBib2R5LnRva2VufTsKICBpZiAocGFydHNbMV0gPT09ICJnaXRvcHMiKSB7CiAgICBjb25zdCBvcG09e3RyZWU6ImdpdG9wc190cmVlIiwgZmlsZToiZ2l0b3BzX2ZpbGUiLCBzdGF0dXM6ImdpdG9wc19zdGF0dXMiLCBwbGFuOiJnaXRvcHNfcGxhbiIsIGFwcGx5OiJnaXRvcHNfYXBwbHkiLCBleHBvcnQ6ImdpdG9wc19leHBvcnQifTsKICAgIHJldHVybiBPYmplY3QuYXNzaWduKHtvcDpvcG1bcGFydHNbMl1dfHwiZ2l0b3BzX3BsYW4iLCBuYW1lOiBib2R5Lm5hbWV9LCBib2R5KTsKICB9CiAgaWYgKHBhcnRzWzFdID09PSAiZnVuY3Rpb25zIiAmJiBwYXJ0c1s0XSA9PT0gImxvZ3MiKQogICAgcmV0dXJuIHtvcDoibG9ncyIsIG5zOnBhcnRzWzJdLCBuYW1lOnBhcnRzWzNdLCBwb2Q6cS5nZXQoInBvZCIpIHx8ICIifTsKICBjb25zdCBraW5kID0gcGFydHNbMV07CiAgaWYgKHBhcnRzLmxlbmd0aCA9PT0gMikgcmV0dXJuIG1ldGhvZCA9PT0gIlBPU1QiID8ge29wOiJjcmVhdGUiLCBraW5kLCBvYmo6Ym9keX0gOiB7b3A6Imxpc3QiLCBraW5kfTsKICBpZiAocGFydHMubGVuZ3RoID09PSA0KSB7CiAgICBpZiAobWV0aG9kID09PSAiUFVUIikgcmV0dXJuIHtvcDoidXBkYXRlIiwga2luZCwgbnM6cGFydHNbMl0sIG5hbWU6cGFydHNbM10sIG9iajpib2R5fTsKICAgIGlmIChtZXRob2QgPT09ICJERUxFVEUiKSByZXR1cm4ge29wOiJkZWxldGUiLCBraW5kLCBuczpwYXJ0c1syXSwgbmFtZTpwYXJ0c1szXX07CiAgICByZXR1cm4ge29wOiJnZXQiLCBraW5kLCBuczpwYXJ0c1syXSwgbmFtZTpwYXJ0c1szXX07CiAgfQogIHRocm93IG5ldyBFcnJvcigidW5zdXBwb3J0ZWQgZW5kcG9pbnQgIiArIHVybCk7Cn0KYXN5bmMgZnVuY3Rpb24gYXBpKG1ldGhvZCwgdXJsLCBib2R5KSB7CiAgY29uc3QgZW52ZWxvcGUgPSBidWlsZE9wKG1ldGhvZCwgdXJsLCBib2R5KTsKICBjb25zdCByZXMgPSBhd2FpdCBmZXRjaChCQVNFLCB7bWV0aG9kOiJQT1NUIiwgaGVhZGVyczp7IkNvbnRlbnQtVHlwZSI6ImFwcGxpY2F0aW9uL2pzb24ifSwgYm9keTpKU09OLnN0cmluZ2lmeShlbnZlbG9wZSl9KTsKICBjb25zdCBkYXRhID0gYXdhaXQgcmVzLmpzb24oKS5jYXRjaCgoKSA9PiAoe30pKTsKICBpZiAoIXJlcy5vaykgdGhyb3cgbmV3IEVycm9yKGRhdGEubWVzc2FnZSB8fCByZXMuc3RhdHVzVGV4dCB8fCAoIkhUVFAgIiArIHJlcy5zdGF0dXMpKTsKICByZXR1cm4gZGF0YTsKfQpmdW5jdGlvbiB0b2FzdChtc2csIGVycj1mYWxzZSkgewogIGNvbnN0IGVsID0gZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgiZGl2Iik7CiAgZWwuY2xhc3NOYW1lID0gInRvYXN0IiArIChlcnIgPyAiIGVyciIgOiAiIik7CiAgZWwudGV4dENvbnRlbnQgPSBtc2c7CiAgJCgiI3RvYXN0cyIpLmFwcGVuZENoaWxkKGVsKTsKICBzZXRUaW1lb3V0KCgpID0+IGVsLnJlbW92ZSgpLCAzNjAwKTsKfQpmdW5jdGlvbiBlc2Mocyl7cmV0dXJuIFN0cmluZyhzPz8iIikucmVwbGFjZSgvWyY8PiInXS9nLCBjPT4oeyImIjoiJmFtcDsiLCI8IjoiJmx0OyIsIj4iOiImZ3Q7IiwnIic6IiZxdW90OyIsIiciOiImIzM5OyJ9W2NdKSk7fQpmdW5jdGlvbiBtb2RhbChodG1sKXsgJCgiI21vZGFsLWJvZHkiKS5pbm5lckhUTUwgPSBodG1sOyAkKCIjbW9kYWwiKS5zaG93TW9kYWwoKTsgfQpmdW5jdGlvbiBjbG9zZU1vZGFsKCl7IGNvbnN0IG09JCgiI21vZGFsIik7IGlmKG0ub3BlbikgbS5jbG9zZSgpOyB9CiQoIiNtb2RhbCIpLmFkZEV2ZW50TGlzdGVuZXIoImNsaWNrIiwgZSA9PiB7IGlmIChlLnRhcmdldCA9PT0gJCgiI21vZGFsIikpIGNsb3NlTW9kYWwoKTsgfSk7CgpmdW5jdGlvbiBiYWRnZShraW5kLCB0ZXh0KXsKICBjb25zdCBtYXAgPSB7b2s6Im9rIiwgZXJyOiJlcnIiLCB3YXJuOiJ3YXJuIiwgYnVpbGQ6ImJ1aWxkIiwgaWRsZToiaWRsZSJ9OwogIHJldHVybiBgPHNwYW4gY2xhc3M9ImJhZGdlICR7bWFwW2tpbmRdfHwiaWRsZSJ9Ij4ke2VzYyh0ZXh0KX08L3NwYW4+YDsKfQpmdW5jdGlvbiBwa2dCdWlsZFN0YXR1cyhwa2cpewogIGNvbnN0IGMgPSBwa2c/LnN0YXR1cz8uYnVpbGRzdGF0dXM/LnN0YXR1cyB8fCAibm9uZSI7CiAgaWYgKGMgPT09ICJzdWNjZWVkZWQiKSByZXR1cm4gYmFkZ2UoIm9rIiwi5p6E5bu65oiQ5YqfIik7CiAgaWYgKGMgPT09ICJmYWlsZWQiKSByZXR1cm4gYmFkZ2UoImVyciIsIuaehOW7uuWksei0pSIpOwogIGlmIChjID09PSAicnVubmluZyIgfHwgYyA9PT0gInBlbmRpbmciKSByZXR1cm4gYmFkZ2UoImJ1aWxkIiwi5p6E5bu65LitIik7CiAgcmV0dXJuIGJhZGdlKCJpZGxlIiwi5peg5p6E5bu6Iik7Cn0KZnVuY3Rpb24gaGVybyh0aXRsZSwgc3ViLCBtZXRhKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9Imhlcm8iPjxkaXY+PGgxPiR7ZXNjKHRpdGxlKX08L2gxPjxwIGNsYXNzPSJzdWIiPiR7ZXNjKHN1Ynx8IiIpfTwvcD48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Im1ldGEiPiR7bWV0YXx8IiJ9PC9kaXY+PC9kaXY+YDsKfQpmdW5jdGlvbiBza2VsZXRvbnMocm93cz01KXsgcmV0dXJuIGA8ZGl2IGNsYXNzPSJza2VsZXRvbiI+JHtBcnJheS5mcm9tKHtsZW5ndGg6cm93c30sKF8saSk9PmA8ZGl2IGNsYXNzPSJyb3ciIHN0eWxlPSJ3aWR0aDokezkyLWkqMTF9JSI+PC9kaXY+YCkuam9pbigiIil9PC9kaXY+YDsgfQpmdW5jdGlvbiBlbXB0eUJveCh0aXRsZSwgaGludCl7IHJldHVybiBgPGRpdiBjbGFzcz0iZW1wdHkiPjxzdHJvbmc+JHtlc2ModGl0bGUpfTwvc3Ryb25nPiR7ZXNjKGhpbnR8fCIiKX08L2Rpdj5gOyB9CgovKiAtLS0tLS0tLS0tIHJvdXRpbmcgLS0tLS0tLS0tLSAqLwpjb25zdCByb3V0ZXMgPSB7CiAgIiI6IHZpZXdPdmVydmlldywgIm92ZXJ2aWV3Ijogdmlld092ZXJ2aWV3LAogICJmdW5jdGlvbnMiOiB2aWV3S2luZCwgInBhY2thZ2VzIjogdmlld0tpbmQsICJlbnZpcm9ubWVudHMiOiB2aWV3S2luZCwKICAiaHR0cHRyaWdnZXJzIjogdmlld0tpbmQsICJ0aW1ldHJpZ2dlcnMiOiB2aWV3S2luZCwKICAiZ2l0b3BzIjogdmlld0dpdG9wc1YyLCAidGVzdGVyIjogdmlld1Rlc3RlciwKfTsKY29uc3QgQ1JVTUJTPXtvdmVydmlldzoi5oC76KeIIix0ZXN0ZXI6IkFQSSDmtYvor5Xlj7AiLGZ1bmN0aW9uczoiRnVuY3Rpb25zIixwYWNrYWdlczoiUGFja2FnZXMiLGVudmlyb25tZW50czoiRW52aXJvbm1lbnRzIixodHRwdHJpZ2dlcnM6IkhUVFAgVHJpZ2dlcnMiLHRpbWV0cmlnZ2VyczoiVGltZXIgVHJpZ2dlcnMiLGdpdG9wczoiR2l0T3BzIOWQjOatpSJ9Owphc3luYyBmdW5jdGlvbiByZWZyZXNoVG9wYmFyKCl7CiAgdHJ5ewogICAgY29uc3Qgbz1hd2FpdCBhcGkoIkdFVCIsIi9hcGkvb3ZlcnZpZXciKTsKICAgIGNvbnN0IGJhZD1vLmNvcmUuZmlsdGVyKHg9PngucmVhZHkhPT14LnBvZHMpLmxlbmd0aDsKICAgIGNvbnN0IGVsPSQoIiN0b3AtY2x1c3RlciIpOwogICAgZWwuY2xhc3NOYW1lPSJwaWxsIisoYmFkPyIgd2FybiI6IiIpOwogICAgZWwuaW5uZXJIVE1MPWA8c3BhbiBjbGFzcz0iZG90Ij48L3NwYW4+JHtiYWQ/IkZJU1NJT04gREVHUkFERUQiOiI2LzYgTk9ERVMgUkVBRFkifWA7CiAgfWNhdGNoKGUpeyAkKCIjdG9wLWNsdXN0ZXIiKS5pbm5lckhUTUw9JzxzcGFuIGNsYXNzPSJkb3QiPjwvc3Bhbj5BUEkg5LiN5Y+v6L6+JzsgJCgiI3RvcC1jbHVzdGVyIikuY2xhc3NOYW1lPSJwaWxsIGVyciI7IH0KICB0cnl7CiAgICBjb25zdCBjZmc9YXdhaXQgYXBpKCJHRVQiLCIvYXBpL2NvbmZpZyIpOyBjb25zdCBzPShjZmcuc291cmNlc3x8W10pWzBdOwogICAgY29uc3QgZWw9JCgiI3RvcC1zb3VyY2UiKTsKICAgIGlmKHMpeyBlbC5pbm5lckhUTUw9YDxzcGFuIGNsYXNzPSJkb3QiPjwvc3Bhbj5HSVRIVUIgwrcgJHtlc2MoKHMuYnJhbmNofHwibWFpbiIpLnRvVXBwZXJDYXNlKCkpfWA7IGVsLmNsYXNzTmFtZT0icGlsbCBpbmZvIjsgfQogICAgZWxzZSB7IGVsLmlubmVySFRNTD0nPHNwYW4gY2xhc3M9ImRvdCI+PC9zcGFuPuS7k+W6k+acqumFjee9ric7IGVsLmNsYXNzTmFtZT0icGlsbCI7IH0KICB9Y2F0Y2goZSl7fQp9CmZ1bmN0aW9uIG5hdmlnYXRlKCl7IGNsb3NlTW9kYWwoKTsKICBjb25zdCBjcnVtYj0kKCIjY3J1bWIiKTsgaWYoY3J1bWIpIGNydW1iLnRleHRDb250ZW50PUNSVU1CU1sobG9jYXRpb24uaGFzaC5yZXBsYWNlKC9eI1wvPy8sIiIpLnNwbGl0KCIvIilbMF0pfHwib3ZlcnZpZXciXXx8IuaAu+iniCI7CiAgY29uc3QgaCA9IGxvY2F0aW9uLmhhc2gucmVwbGFjZSgvXiNcLz8vLCAiIik7CiAgY29uc3QgcGFydHMgPSBoLnNwbGl0KCIvIik7CiAgJCQoIiNuYXYgYSIpLmZvckVhY2goYSA9PiBhLmNsYXNzTGlzdC50b2dnbGUoImFjdGl2ZSIsIGEuZGF0YXNldC52aWV3ID09PSAocGFydHNbMF18fCJvdmVydmlldyIpKSk7CiAgcmVmcmVzaFRvcGJhcigpOwogIGNvbnN0IG1haW4gPSAkKCIjbWFpbiIpOwogIHRyeSB7CiAgICBpZiAocGFydHNbMF0gPT09ICJmdW5jdGlvbiIgJiYgcGFydHMubGVuZ3RoID49IDMpIHJldHVybiB2aWV3RnVuY3Rpb25EZXRhaWwocGFydHNbMV0sIHBhcnRzLnNsaWNlKDIpLmpvaW4oIi8iKSk7CiAgICBjb25zdCB2aWV3ID0gcm91dGVzW3BhcnRzWzBdXSB8fCB2aWV3T3ZlcnZpZXc7CiAgICB2aWV3KHBhcnRzWzBdID09PSAiIiB8fCBwYXJ0c1swXSA9PT0gIm92ZXJ2aWV3IiA/ICJvdmVydmlldyIgOiBwYXJ0c1swXSk7CiAgfSBjYXRjaCAoZSkgeyBtYWluLmlubmVySFRNTCA9IGVtcHR5Qm94KCLpobXpnaLliqDovb3lpLHotKUiLCBlLm1lc3NhZ2UpOyB9Cn0Kd2luZG93LmFkZEV2ZW50TGlzdGVuZXIoImhhc2hjaGFuZ2UiLCBuYXZpZ2F0ZSk7CgovKiAtLS0tLS0tLS0tIG92ZXJ2aWV3IC0tLS0tLS0tLS0gKi8KYXN5bmMgZnVuY3Rpb24gdmlld092ZXJ2aWV3KCl7CiAgJCgiI21haW4iKS5pbm5lckhUTUwgPSBgPGgxPuaAu+iniDwvaDE+PHAgY2xhc3M9InN1YnRpdGxlIj7pm4bnvqTotYTmupDkuI4gRmlzc2lvbiDmoLjlv4Pnu4Tku7blgaXlurc8L3A+YCArIHNrZWxldG9ucygpOwogIGNvbnN0IGQgPSBhd2FpdCBhcGkoIkdFVCIsICIvYXBpL292ZXJ2aWV3Iik7CiAgY29uc3QgY250ID0gZC5jb3VudHM7CiAgY29uc3QgY29yZVJvd3MgPSBkLmNvcmUubWFwKGMgPT4gYDx0cj48dGQ+PHNwYW4gY2xhc3M9Im5hbWUiPiR7ZXNjKGMuYXBwKX08L3NwYW4+PC90ZD4KICAgIDx0ZD4ke2MucG9kc308L3RkPjx0ZD4ke2MucmVhZHkgPT09IGMucG9kcyA/IGJhZGdlKCJvayIsIuWBpeW6tyIpIDogYmFkZ2UoImVyciIsYCR7Yy5yZWFkeX0vJHtjLnBvZHN9IOWwsee7qmApfTwvdGQ+PC90cj5gKS5qb2luKCIiKQogICAgfHwgYDx0cj48dGQgY29sc3Bhbj0iMyI+JHtlbXB0eUJveCgi5peg5qC45b+D57uE5Lu2IiwiZmlzc2lvbiDlkb3lkI3nqbrpl7TmsqHmnIkgUG9kIil9PC90ZD48L3RyPmA7CiAgJCgiI21haW4iKS5pbm5lckhUTUwgPSBoZXJvKCLov5DooYzmgLvop4giLCAi5Ye95pWw44CB6Kem5Y+R5Zmo5LiO5qC45b+D57uE5Lu255qE5b2T5YmN5oCB5Yq/IiwKICAgIGA8Yj4ke25ldyBEYXRlKCkudG9Mb2NhbGVEYXRlU3RyaW5nKCJ6aC1DTiIpfTwvYj48YnI+MTVzIOmHh+agtyDCtyBGSVNTSU9OLklPL1YxYCkgKyBgCiAgICA8ZGl2IGNsYXNzPSJwYWdlLWJvZHkiPgogICAgICA8ZGl2IGNsYXNzPSJzdHJpcCI+JHtbWyJGdW5jdGlvbnMiLGNudC5mdW5jdGlvbnNdLFsiUGFja2FnZXMiLGNudC5wYWNrYWdlc10sWyJFbnZpcm9ubWVudHMiLGNudC5lbnZpcm9ubWVudHNdLFsiSFRUUCBUcmlnZ2VycyIsY250Lmh0dHB0cmlnZ2Vyc10sWyJUaW1lciBUcmlnZ2VycyIsY250LnRpbWV0cmlnZ2Vyc11dCiAgICAgICAgLm1hcCgoW2ssdl0pPT5gPGRpdiBjbGFzcz0iY2VsbCI+PGRpdiBjbGFzcz0iayI+JHtrfTwvZGl2PjxkaXYgY2xhc3M9InYiPiR7dj8/MH08L2Rpdj48L2Rpdj5gKS5qb2luKCIiKX08L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0iYm9hcmQiPgogICAgICAgIDxzZWN0aW9uPjxoMj7otYTmupDop4LmtYsgPHNwYW4gY2xhc3M9ImNvdW50Ij4xNXM8L3NwYW4+PC9oMj48ZGl2IGlkPSJvYnMiIGNsYXNzPSJjaGFydC1ib2FyZCI+PC9kaXY+PC9zZWN0aW9uPgogICAgICAgIDxhc2lkZT48aDI+5qC45b+D57uE5Lu2IDxzcGFuIGNsYXNzPSJjb3VudCI+JHtkLmNvcmUubGVuZ3RofTwvc3Bhbj48L2gyPgogICAgICAgICAgPGRpdiBjbGFzcz0idGFibGUtd3JhcCI+PHRhYmxlPjx0aGVhZD48dHI+PHRoPue7hOS7tjwvdGg+PHRoPlBvZHM8L3RoPjx0aD7nirbmgIE8L3RoPjwvdHI+PC90aGVhZD48dGJvZHk+JHtjb3JlUm93c308L3Rib2R5PjwvdGFibGU+PC9kaXY+CiAgICAgICAgICA8aDI+6L+Q6KGM5o+Q56S6PC9oMj4KICAgICAgICAgIDxkaXYgY2xhc3M9InNpZ25hbCI+PHNwYW4gY2xhc3M9Im1hcmsiPjwvc3Bhbj48c3BhbiBjbGFzcz0idCI+6YeH5qC35Zmo5L+d5oyB5Zyo57q/77yM5Zu+6KGo5q+PIDE1IOenkui/veWKoOaVsOaNrueCuTwvc3Bhbj48c3BhbiBjbGFzcz0idSI+QVVUTzwvc3Bhbj48L2Rpdj4KICAgICAgICAgIDxkaXYgY2xhc3M9InNpZ25hbCI+PHNwYW4gY2xhc3M9Im1hcmsiPjwvc3Bhbj48c3BhbiBjbGFzcz0idCI+R2l0T3BzIOWGmeWbnumcgOS6uuW3peinpuWPke+8jOiHquWKqOWQjOatpeS7heaLieWPljwvc3Bhbj48c3BhbiBjbGFzcz0idSI+U0FGRTwvc3Bhbj48L2Rpdj4KICAgICAgICAgIDxoMj7lh73mlbDmuIXljZUgPHNwYW4gY2xhc3M9ImNvdW50IiBpZD0iZm4tY291bnQiPuKAlDwvc3Bhbj48L2gyPjxkaXYgaWQ9ImZuLWxpc3QiPjxkaXYgY2xhc3M9InNrZWxldG9uIj48ZGl2IGNsYXNzPSJyb3ciPjwvZGl2PjxkaXYgY2xhc3M9InJvdyIgc3R5bGU9IndpZHRoOjcwJSI+PC9kaXY+PC9kaXY+PC9kaXY+CiAgICAgICAgPC9hc2lkZT4KICAgICAgPC9kaXY+CiAgICA8L2Rpdj5gOwogIHJlbmRlck9ic2VydmFiaWxpdHkoKTsKICAoYXN5bmMoKT0+ewogICAgdHJ5ewogICAgICBjb25zdCBbZm5zLHRyc109YXdhaXQgUHJvbWlzZS5hbGwoW2FwaSgiR0VUIiwiL2FwaS9mdW5jdGlvbnMiKSxhcGkoIkdFVCIsIi9hcGkvaHR0cHRyaWdnZXJzIildKTsKICAgICAgY29uc3Qgcm93cz1mbnMuaXRlbXMubWFwKGY9PnsKICAgICAgICBjb25zdCB0PXRycy5pdGVtcy5maWx0ZXIoeD0+Zm5SZWYoeC5zcGVjKS5zcGxpdCgiOiIpWzBdPT09Zi5tZXRhZGF0YS5uYW1lKS5tYXAoeD0+eC5tZXRhZGF0YS5uYW1lKTsKICAgICAgICBjb25zdCBleD1mLnNwZWM/Lkludm9rZVN0cmF0ZWd5Py5FeGVjdXRpb25TdHJhdGVneT8uRXhlY3V0b3JUeXBlfHxmLnNwZWM/Lmludm9rZVN0cmF0ZWd5Py5leGVjdXRpb25TdHJhdGVneT8uZXhlY3V0b3JUeXBlfHwicG9vbG1nciI7CiAgICAgICAgcmV0dXJuIGA8dHI+PHRkPjxzcGFuIGNsYXNzPSJuYW1lIj4ke2VzYyhmLm1ldGFkYXRhLm5hbWUpfTwvc3Bhbj48ZGl2IGNsYXNzPSJtb25vIj4ke2VzYyhmLm1ldGFkYXRhLm5hbWVzcGFjZSl9IMK3ICR7ZXNjKGV4KX08L2Rpdj48L3RkPgogICAgICAgICAgPHRkPiR7dC5sZW5ndGg/dC5tYXAoeD0+YDxzcGFuIGNsYXNzPSJjaGlwIj4ke2VzYyh4KX08L3NwYW4+YCkuam9pbigiIik6JzxzcGFuIGNsYXNzPSJtb25vIj7ml6Dop6blj5Hlmag8L3NwYW4+J308L3RkPjwvdHI+YDsKICAgICAgfSkuam9pbigiIik7CiAgICAgIGNvbnN0IGZjPSQoIiNmbi1jb3VudCIpOyBpZihmYykgZmMudGV4dENvbnRlbnQ9Zm5zLml0ZW1zLmxlbmd0aDsKICAgICAgJCgiI2ZuLWxpc3QiKS5pbm5lckhUTUw9YDxkaXYgY2xhc3M9InRhYmxlLXdyYXAiPjx0YWJsZT48dGhlYWQ+PHRyPjx0aD7lh73mlbA8L3RoPjx0aD7op6blj5Hlmag8L3RoPjwvdHI+PC90aGVhZD48dGJvZHk+JHtyb3dzfTwvdGJvZHk+PC90YWJsZT48L2Rpdj5gOwogICAgfWNhdGNoKGUpeyAkKCIjZm4tbGlzdCIpLmlubmVySFRNTD1lbXB0eUJveCgi5Ye95pWw5YiX6KGo5LiN5Y+v55SoIixlLm1lc3NhZ2UpOyB9CiAgfSkoKTsKfQoKLyogLS0tLS0tLS0tLSBraW5kIGxpc3Qgdmlld3MgLS0tLS0tLS0tLSAqLwpjb25zdCBDT0xVTU5TID0gewogIGZ1bmN0aW9uczogZm4gPT4gewogICAgY29uc3QgZW52ID0gZm4uc3BlYz8uZW52aXJvbm1lbnQ/Lm5hbWUgfHwgIi0iOwogICAgY29uc3QgcGtnID0gZm4uc3BlYz8ucGFja2FnZT8ucGFja2FnZXJlZj8ubmFtZSB8fCAiLSI7CiAgICBjb25zdCB0cmlnZ2VycyA9IChmbi5fdHJpZ2dlcnN8fFtdKS5tYXAodD0+dC5tZXRhZGF0YS5uYW1lKTsKICAgIHJldHVybiBgPHRkPjxzcGFuIGNsYXNzPSJuYW1lIiBkYXRhLW5zPSIke2VzYyhmbi5tZXRhZGF0YS5uYW1lc3BhY2UpfSIgZGF0YS1uYW1lPSIke2VzYyhmbi5tZXRhZGF0YS5uYW1lKX0iPiR7ZXNjKGZuLm1ldGFkYXRhLm5hbWUpfTwvc3Bhbj4KICAgICAgPGRpdiBjbGFzcz0ibW9ubyI+JHtlc2MoZm4ubWV0YWRhdGEubmFtZXNwYWNlKX08L2Rpdj48L3RkPgogICAgICA8dGQ+JHtlc2MoZW52KX08L3RkPjx0ZCBjbGFzcz0ibW9ubyI+JHtlc2MocGtnKS5zbGljZSgwLDI4KX0ke3BrZy5sZW5ndGg+Mjg/IuKApiI6IiJ9PC90ZD4KICAgICAgPHRkPiR7dHJpZ2dlcnMubGVuZ3RoID8gdHJpZ2dlcnMubWFwKHQ9PmA8c3BhbiBjbGFzcz0iY2hpcCI+JHtlc2ModCl9PC9zcGFuPmApLmpvaW4oIiIpIDogJzxzcGFuIGNsYXNzPSJiYWRnZSBpZGxlIj7ml6Dop6blj5Hlmag8L3NwYW4+J308L3RkPmA7CiAgfSwKICBwYWNrYWdlczogcCA9PiBgPHRkPjxzcGFuIGNsYXNzPSJtb25vIj4ke2VzYyhwLm1ldGFkYXRhLm5hbWUpfTwvc3Bhbj48ZGl2IGNsYXNzPSJtb25vIj4ke2VzYyhwLm1ldGFkYXRhLm5hbWVzcGFjZSl9PC9kaXY+PC90ZD4KICAgIDx0ZD4ke2VzYyhwLnNwZWM/LmVudmlyb25tZW50Py5uYW1lfHwiLSIpfTwvdGQ+PHRkPiR7cGtnQnVpbGRTdGF0dXMocCl9PC90ZD5gLAogIGVudmlyb25tZW50czogZSA9PiBgPHRkPjxzcGFuIGNsYXNzPSJuYW1lIj4ke2VzYyhlLm1ldGFkYXRhLm5hbWUpfTwvc3Bhbj48ZGl2IGNsYXNzPSJtb25vIj4ke2VzYyhlLm1ldGFkYXRhLm5hbWVzcGFjZSl9PC9kaXY+PC90ZD4KICAgIDx0ZCBjbGFzcz0ibW9ubyI+JHtlc2MoZS5zcGVjPy5ydW50aW1lPy5pbWFnZXx8IiIpfTwvdGQ+CiAgICA8dGQ+JHtiYWRnZSgib2siLCLlsLHnu6oiKX08L3RkPmAsCiAgaHR0cHRyaWdnZXJzOiB0ID0+IHsgY29uc3Qgcz10LnNwZWN8fHt9OyBjb25zdCBtZXRob2RzPShzLm1ldGhvZHMmJnMubWV0aG9kcy5sZW5ndGg/cy5tZXRob2RzOltzLm1ldGhvZF0pLmZpbHRlcihCb29sZWFuKS5qb2luKCIsIik7CiAgICByZXR1cm4gYDx0ZD48c3BhbiBjbGFzcz0ibmFtZSI+JHtlc2ModC5tZXRhZGF0YS5uYW1lKX08L3NwYW4+PGRpdiBjbGFzcz0ibW9ubyI+JHtlc2ModC5tZXRhZGF0YS5uYW1lc3BhY2UpfTwvZGl2PjwvdGQ+CiAgICA8dGQ+PHNwYW4gY2xhc3M9ImNoaXAiPiR7ZXNjKG1ldGhvZHN8fCJHRVQiKX08L3NwYW4+PC90ZD48dGQgY2xhc3M9Im1vbm8iPiR7ZXNjKChzLnJlbGF0aXZlVXJsfHxzLnJlbGF0aXZldXJsfHxzLnByZWZpeCl8fCIiKX08L3RkPgogICAgPHRkIGNsYXNzPSJtb25vIj4ke2VzYyhmblJlZihzKSl9PC90ZD4KICAgIDx0ZD4ke3MuY3JlYXRlaW5ncmVzcyA/IGJhZGdlKCJ3YXJuIiwi5bey5bu6IEluZ3Jlc3MiKSA6IGJhZGdlKCJpZGxlIiwi5YaF6YOoIil9PC90ZD5gOyB9LAogIHRpbWV0cmlnZ2VyczogdCA9PiBgPHRkPjxzcGFuIGNsYXNzPSJuYW1lIj4ke2VzYyh0Lm1ldGFkYXRhLm5hbWUpfTwvc3Bhbj48ZGl2IGNsYXNzPSJtb25vIj4ke2VzYyh0Lm1ldGFkYXRhLm5hbWVzcGFjZSl9PC9kaXY+PC90ZD4KICAgIDx0ZCBjbGFzcz0ibW9ubyI+JHtlc2ModC5zcGVjPy5jcm9ufHwiIil9PC90ZD48dGQgY2xhc3M9Im1vbm8iPiR7ZXNjKGZuUmVmKHQuc3BlYykpfTwvdGQ+YCwKfTsKZnVuY3Rpb24gZm5SZWYoc3BlYyl7CiAgY29uc3QgciA9IHNwZWM/LmZ1bmN0aW9ucmVmIHx8IHt9OwogIGlmIChyLnR5cGUgPT09ICJmdW5jdGlvbi13ZWlnaHRzIikKICAgIHJldHVybiBPYmplY3QuZW50cmllcyhyLmZ1bmN0aW9ud2VpZ2h0c3x8e30pLm1hcCgoW24sd10pPT5gJHtufToke3d9JWApLmpvaW4oIiAiKTsKICByZXR1cm4gci5uYW1lIHx8ICIiOwp9CmNvbnN0IFRFTVBMQVRFUyA9IHsKICBmdW5jdGlvbnM6IG5zID0+ICh7YXBpVmVyc2lvbjoiZmlzc2lvbi5pby92MSIsa2luZDoiRnVuY3Rpb24iLG1ldGFkYXRhOntuYW1lOiJuZXctZnVuYyIsbmFtZXNwYWNlOm5zfSwKICAgIHNwZWM6e2Vudmlyb25tZW50OntuYW1lOiJweXRob24iLG5hbWVzcGFjZTpuc30scGFja2FnZTp7cGFja2FnZXJlZjp7bmFtZToiUkVQTEFDRS1QQUNLQUdFIixuYW1lc3BhY2U6bnN9fSwKICAgIGludm9rZVN0cmF0ZWd5OntzdHJhdGVneVR5cGU6ImV4ZWN1dGlvbiIsZXhlY3V0aW9uU3RyYXRlZ3k6e2V4ZWN1dG9yVHlwZToicG9vbG1nciIsc3BlY2lhbGl6YXRpb25UaW1lb3V0OjEyMH19LAogICAgZnVuY3Rpb25UaW1lb3V0OjYwLGlkbGV0aW1lb3V0OjEyMCxjb25jdXJyZW5jeTo1MDAscmVxdWVzdHNQZXJQb2Q6MX19KSwKICBwYWNrYWdlczogbnMgPT4gKHthcGlWZXJzaW9uOiJmaXNzaW9uLmlvL3YxIixraW5kOiJQYWNrYWdlIixtZXRhZGF0YTp7bmFtZToibmV3LXBrZyIsbmFtZXNwYWNlOm5zfSwKICAgIHNwZWM6e2Vudmlyb25tZW50OntuYW1lOiJweXRob24iLG5hbWVzcGFjZTpuc30sZGVwbG95bWVudDp7bGl0ZXJhbDoiIn19fSksCiAgZW52aXJvbm1lbnRzOiBucyA9PiAoe2FwaVZlcnNpb246ImZpc3Npb24uaW8vdjEiLGtpbmQ6IkVudmlyb25tZW50IixtZXRhZGF0YTp7bmFtZToibmV3LWVudiIsbmFtZXNwYWNlOm5zfSwKICAgIHNwZWM6e3ZlcnNpb246MixydW50aW1lOntpbWFnZToiZmlzc2lvbi9weXRob24tZW52OmxhdGVzdCJ9LGJ1aWxkZXI6e2ltYWdlOiJmaXNzaW9uL3B5dGhvbi1idWlsZGVyOmxhdGVzdCJ9fX0pLAogIGh0dHB0cmlnZ2VyczogbnMgPT4gKHthcGlWZXJzaW9uOiJmaXNzaW9uLmlvL3YxIixraW5kOiJIVFRQVHJpZ2dlciIsbWV0YWRhdGE6e25hbWU6Im5ldy10cmlnZ2VyIixuYW1lc3BhY2U6bnN9LAogICAgc3BlYzp7cmVsYXRpdmVVcmw6Ii9uZXctZnVuYyIsbWV0aG9kczpbIkdFVCJdLGZ1bmN0aW9ucmVmOnt0eXBlOiJmdW5jdGlvbiIsbmFtZToiUkVQTEFDRSJ9fX0pLAogIHRpbWV0cmlnZ2VyczogbnMgPT4gKHthcGlWZXJzaW9uOiJmaXNzaW9uLmlvL3YxIixraW5kOiJUaW1lVHJpZ2dlciIsbWV0YWRhdGE6e25hbWU6Im5ldy10aW1lciIsbmFtZXNwYWNlOm5zfSwKICAgIHNwZWM6e2Nyb246IkBldmVyeSAxaCIsZnVuY3Rpb25yZWY6e3R5cGU6Im5hbWUiLG5hbWU6IlJFUExBQ0UifX19KSwKfTsKY29uc3QgRk9STVMgPSB7CiAgaHR0cHRyaWdnZXJzOiBbWyJuYW1lIiwi5ZCN56ewIiwidGV4dCIsIm5ldy10cmlnZ2VyIl0sWyJuYW1lc3BhY2UiLCLlkb3lkI3nqbrpl7QiLCJ0ZXh0IiwiZmlzc2lvbiJdLAogICAgWyJyZWxhdGl2ZVVybCIsIlVSTCDot6/lvoQiLCJ0ZXh0IiwiL25ldy1mdW5jIl0sWyJtZXRob2RzIiwiSFRUUCDmlrnms5Uo6YCX5Y+35YiG6ZqUKSIsInRleHQiLCJHRVQiXSwKICAgIFsiZnVuY3Rpb25yZWYubmFtZSIsIuebruagh+WHveaVsCIsInRleHQiLCIiXV0sCiAgdGltZXRyaWdnZXJzOiBbWyJuYW1lIiwi5ZCN56ewIiwidGV4dCIsIm5ldy10aW1lciJdLFsibmFtZXNwYWNlIiwi5ZG95ZCN56m66Ze0IiwidGV4dCIsImZpc3Npb24iXSwKICAgIFsiY3JvbiIsIkNyb24g6KGo6L6+5byPIiwidGV4dCIsIkBldmVyeSAxaCJdLFsiZnVuY3Rpb25yZWYubmFtZSIsIuebruagh+WHveaVsCIsInRleHQiLCIiXV0sCiAgZW52aXJvbm1lbnRzOiBbWyJuYW1lIiwi5ZCN56ewIiwidGV4dCIsIm5ldy1lbnYiXSxbIm5hbWVzcGFjZSIsIuWRveWQjeepuumXtCIsInRleHQiLCJmaXNzaW9uIl0sCiAgICBbInNwZWMucnVudGltZS5pbWFnZSIsIui/kOihjOmVnOWDjyIsInRleHQiLCJmaXNzaW9uL3B5dGhvbi1lbnY6bGF0ZXN0Il1dLAp9OwpmdW5jdGlvbiBmb3JtVG9PYmoobWV0YSwgdmFsdWVzKXsKICBjb25zdCBvYmogPSBzdHJ1Y3R1cmVkQ2xvbmUobWV0YSk7CiAgZm9yIChjb25zdCBbcGF0aCwgdmFsXSBvZiBPYmplY3QuZW50cmllcyh2YWx1ZXMpKXsKICAgIGlmICghdmFsKSBjb250aW51ZTsKICAgIGNvbnN0IGtleXMgPSBwYXRoLnNwbGl0KCIuIik7CiAgICBsZXQgY3VyID0gb2JqOwogICAgd2hpbGUgKGtleXMubGVuZ3RoID4gMSl7IGN1ciA9IGN1cltrZXlzWzBdXSA9IGN1cltrZXlzWzBdXSB8fCB7fTsga2V5cy5zaGlmdCgpOyB9CiAgICBjdXJba2V5c1swXV0gPSBrZXlzWzBdPT09Im1ldGhvZHMiID8gdmFsLnNwbGl0KCIsIikubWFwKHM9PnMudHJpbSgpKS5maWx0ZXIoQm9vbGVhbikgOiB2YWw7CiAgfQogIHJldHVybiBvYmo7Cn0KbGV0IEZVTkNTID0gW10sIFNFTEVDVEVEX0ZOID0gbnVsbDsKYXN5bmMgZnVuY3Rpb24gdmlld0tpbmQoa2luZCl7CiAgY29uc3QgbWFpbiA9ICQoIiNtYWluIik7CiAgbWFpbi5pbm5lckhUTUwgPSBoZXJvKExBQkVMU1traW5kXSwgIui1hOa6kOeahOWjsOaYjuOAgeeKtuaAgeS4juaTjeS9nCIsICI8Yj5maXNzaW9uLmlvL3YxPC9iPjxicj5DUkQg55u06L+eIikgKyBgPGRpdiBjbGFzcz0icGFnZS1ib2R5Ij4ke3NrZWxldG9ucygpfTwvZGl2PmA7CiAgbGV0IGl0ZW1zID0gKGF3YWl0IGFwaSgiR0VUIiwgYC9hcGkvJHtraW5kfWApKS5pdGVtczsKICBpZiAoa2luZCA9PT0gImZ1bmN0aW9ucyIpewogICAgY29uc3QgdHJzID0gKGF3YWl0IGFwaSgiR0VUIiwgIi9hcGkvaHR0cHRyaWdnZXJzIikpLml0ZW1zOwogICAgaXRlbXMuZm9yRWFjaChmID0+IGYuX3RyaWdnZXJzID0gdHJzLmZpbHRlcih0ID0+IGZuUmVmKHQuc3BlYykuc3BsaXQoIjoiKVswXSA9PT0gZi5tZXRhZGF0YS5uYW1lKSk7CiAgICBGVU5DUyA9IGl0ZW1zOwogICAgbWFpbi5pbm5lckhUTUwgPSBoZXJvKCJGdW5jdGlvbnMiLCAiIiwKICAgICAgYDxiPiR7aXRlbXMubGVuZ3RofSBGVU5DVElPTlMgwrcgMDMgUEFDS0FHRVM8L2I+PGJyPmZpc3Npb24uaW8vdjEgwrcgbGl2ZWApICsgYAogICAgICA8ZGl2IGNsYXNzPSJwYWdlLWJvZHkiPgogICAgICAgIDxkaXYgY2xhc3M9Im1hc3RlciI+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJtYXN0ZXItbGlzdCI+PGgyPui1hOa6kOa4heWNlSA8c3BhbiBjbGFzcz0iY291bnQiPiR7aXRlbXMubGVuZ3RofTwvc3Bhbj48L2gyPjxkaXYgaWQ9ImZuLXJvd3MiPjwvZGl2PjwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0iaW5zcGVjdG9yIiBpZD0iZm4taW5zcGVjdG9yIj4ke2VtcHR5Qm94KCLpgInmi6nkuIDkuKrlh73mlbAiLCLku47lt6bkvqfmuIXljZXpgInmi6nlh73mlbDvvIzmn6XnnIvml6Xlv5fjgIHmjIfmoIfkuI7mk43kvZzjgIIiKX08L2Rpdj4KICAgICAgICA8L2Rpdj4KICAgICAgPC9kaXY+YDsKICAgIGNvbnN0IHBhaW50ID0gKGt3PSIiKSA9PiB7CiAgICAgIGNvbnN0IGxpc3QgPSBpdGVtcy5maWx0ZXIoZiA9PiAoZi5tZXRhZGF0YS5uYW1lICsgZi5tZXRhZGF0YS5uYW1lc3BhY2UpLnRvTG93ZXJDYXNlKCkuaW5jbHVkZXMoa3cudG9Mb3dlckNhc2UoKSkpOwogICAgICAkKCIjZm4tcm93cyIpLmlubmVySFRNTCA9IGxpc3QubWFwKChmLCBpKSA9PiBgCiAgICAgICAgPGRpdiBjbGFzcz0ibXJvdyAke1NFTEVDVEVEX0ZOPT09Zi5tZXRhZGF0YS5uYW1lPyJzZWwiOiIifSIgZGF0YS1mbj0iJHtlc2MoZi5tZXRhZGF0YS5uYW1lKX0iPgogICAgICAgICAgPHNwYW4gY2xhc3M9Im51bSI+JHtTdHJpbmcoaSsxKS5wYWRTdGFydCgyLCIwIil9PC9zcGFuPgogICAgICAgICAgPGRpdj48ZGl2IGNsYXNzPSJubSI+JHtlc2MoZi5tZXRhZGF0YS5uYW1lKX08L2Rpdj48ZGl2IGNsYXNzPSJtdCI+JHtlc2MoZi5tZXRhZGF0YS5uYW1lc3BhY2UpfSDCtyAke2VzYyhmLnNwZWM/LmVudmlyb25tZW50Py5uYW1lfHwicHl0aG9uIil9IMK3ICR7ZXNjKGYuc3BlYz8uSW52b2tlU3RyYXRlZ3k/LkV4ZWN1dGlvblN0cmF0ZWd5Py5FeGVjdXRvclR5cGV8fGYuc3BlYz8uaW52b2tlU3RyYXRlZ3k/LmV4ZWN1dGlvblN0cmF0ZWd5Py5leGVjdXRvclR5cGV8fCJwb29sbWdyIil9PC9kaXY+PC9kaXY+CiAgICAgICAgICA8c3BhbiBjbGFzcz0ic3QiPlJFQURZPC9zcGFuPgogICAgICAgIDwvZGl2PmApLmpvaW4oIiIpIHx8IGVtcHR5Qm94KCLml6DljLnphY3lh73mlbAiLCLosIPmlbTmkJzntKLlhbPplK7or43jgIIiKTsKICAgICAgJCQoIiNmbi1yb3dzIFtkYXRhLWZuXSIpLmZvckVhY2gocm93ID0+IHJvdy5vbmNsaWNrID0gKCkgPT4geyBTRUxFQ1RFRF9GTiA9IHJvdy5kYXRhc2V0LmZuOyBwYWludChrdyk7IHNlbGVjdEZ1bmN0aW9uKGl0ZW1zLmZpbmQoZj0+Zi5tZXRhZGF0YS5uYW1lPT09U0VMRUNURURfRk4pKTsgfSk7CiAgICB9OwogICAgcGFpbnQoKTsKICAgIGNvbnN0IGZzPSQoIiNmbi1zZWFyY2giKTsgaWYoZnMpIGZzLm9uaW5wdXQgPSBlID0+IHBhaW50KGUudGFyZ2V0LnZhbHVlKTsKICAgIGNvbnN0IGJuPSQoIiNidG4tbmV3Iik7IGlmKGJuKSBibi5vbmNsaWNrID0gKCkgPT4gY3JlYXRlUmVzb3VyY2UoImZ1bmN0aW9ucyIpOwogICAgaWYgKGl0ZW1zLmxlbmd0aCkgeyBTRUxFQ1RFRF9GTiA9IGl0ZW1zWzBdLm1ldGFkYXRhLm5hbWU7IHBhaW50KCk7IHNlbGVjdEZ1bmN0aW9uKGl0ZW1zWzBdKTsgfQogICAgcmV0dXJuOwogIH0KICBpdGVtcy5zb3J0KChhLGIpPT4oYS5tZXRhZGF0YS5uYW1lc3BhY2UrYS5tZXRhZGF0YS5uYW1lKS5sb2NhbGVDb21wYXJlKGIubWV0YWRhdGEubmFtZXNwYWNlK2IubWV0YWRhdGEubmFtZSkpOwogIGNvbnN0IHJlbmRlciA9IChrdz0iIikgPT4gewogICAgY29uc3QgZiA9IGl0ZW1zLmZpbHRlcihpID0+IChpLm1ldGFkYXRhLm5hbWUgKyBpLm1ldGFkYXRhLm5hbWVzcGFjZSkudG9Mb3dlckNhc2UoKS5pbmNsdWRlcyhrdy50b0xvd2VyQ2FzZSgpKSk7CiAgICAkKCIjbGlzdCIpLmlubmVySFRNTCA9ICFmLmxlbmd0aCAmJiAha3cgPyBlbXB0eUJveChg6L+Y5rKh5pyJICR7TEFCRUxTW2tpbmRdfWAsIueCueWHu+OAjOaWsOW7uuOAjeWIm+W7uuesrOS4gOS4qui1hOa6kOOAgiIpCiAgICAgIDogIWYubGVuZ3RoID8gZW1wdHlCb3goIuaXoOWMuemFjee7k+aenCIsIuiwg+aVtOaQnOe0ouWFs+mUruivjeOAgiIpCiAgICAgIDogYDxkaXYgY2xhc3M9InRhYmxlLXdyYXAiPjx0YWJsZT48dGhlYWQ+PHRyPjx0aD7lkI3np7A8L3RoPgogICAgICAgICAgJHtraW5kPT09InBhY2thZ2VzIj8iPHRoPueOr+WigzwvdGg+PHRoPuaehOW7ujwvdGg+IjoiIn0ke2tpbmQ9PT0iZW52aXJvbm1lbnRzIj8iPHRoPui/kOihjOmVnOWDjzwvdGg+PHRoPueKtuaAgTwvdGg+IjoiIn0KICAgICAgICAgICR7a2luZD09PSJodHRwdHJpZ2dlcnMiPyI8dGg+5pa55rOVPC90aD48dGg+VVJMPC90aD48dGg+55uu5qCH5Ye95pWwPC90aD48dGg+5a+55aSWPC90aD4iOiIifSR7a2luZD09PSJ0aW1ldHJpZ2dlcnMiPyI8dGg+Q3JvbjwvdGg+PHRoPuebruagh+WHveaVsDwvdGg+IjoiIn0KICAgICAgICAgIDx0aCBzdHlsZT0id2lkdGg6MTQwcHgiPuaTjeS9nDwvdGg+PC90cj48L3RoZWFkPjx0Ym9keT4KICAgICAgICAgICR7Zi5tYXAoaT0+YDx0cj4ke0NPTFVNTlNba2luZF0oaSl9PHRkPjxidXR0b24gY2xhc3M9ImJ0biBzbWFsbCIgZGF0YS1lZGl0PSIke2VzYyhpLm1ldGFkYXRhLm5hbWVzcGFjZSl9fCR7ZXNjKGkubWV0YWRhdGEubmFtZSl9Ij7nvJbovpE8L2J1dHRvbj4KICAgICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtYWxsIGRhbmdlciIgZGF0YS1kZWw9IiR7ZXNjKGkubWV0YWRhdGEubmFtZXNwYWNlKX18JHtlc2MoaS5tZXRhZGF0YS5uYW1lKX0iPuWIoOmZpDwvYnV0dG9uPjwvdGQ+PC90cj5gKS5qb2luKCIiKX0KICAgICAgICAgIDwvdGJvZHk+PC90YWJsZT48L2Rpdj5gOwogICAgJCQoIiNsaXN0IFtkYXRhLWVkaXRdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PmVkaXRSZXNvdXJjZShraW5kLC4uLmIuZGF0YXNldC5lZGl0LnNwbGl0KCJ8IikpKTsKICAgICQkKCIjbGlzdCBbZGF0YS1kZWxdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PmRlbGV0ZVJlc291cmNlKGtpbmQsLi4uYi5kYXRhc2V0LmRlbC5zcGxpdCgifCIpKSk7CiAgfTsKICBtYWluLmlubmVySFRNTCA9IGhlcm8oTEFCRUxTW2tpbmRdLCAi6LWE5rqQ55qE5aOw5piO44CB54q25oCB5LiO5pON5L2cIiwgYDxiPiR7aXRlbXMubGVuZ3RofSAke0xBQkVMU1traW5kXS50b1VwcGVyQ2FzZSgpfTwvYj48YnI+Zmlzc2lvbi5pby92MSDCtyBDUkRgKSArIGAKICAgIDxkaXYgY2xhc3M9InBhZ2UtYm9keSI+PGRpdiBjbGFzcz0idG9vbGJhciI+PGlucHV0IHR5cGU9InNlYXJjaCIgaWQ9InNlYXJjaCIgcGxhY2Vob2xkZXI9IuaQnOe0ouWQjeensOaIluWRveWQjeepuumXtCI+CiAgICAgIDxkaXYgY2xhc3M9InNwYWNlciI+PC9kaXY+PGJ1dHRvbiBjbGFzcz0iYnRuIHByaW1hcnkiIGlkPSJidG4tbmV3Ij7mlrDlu7ogJHtMQUJFTFNba2luZF19PC9idXR0b24+PC9kaXY+PGRpdiBpZD0ibGlzdCI+PC9kaXY+PC9kaXY+YDsKICByZW5kZXIoKTsKICAkKCIjc2VhcmNoIikub25pbnB1dCA9IGUgPT4gcmVuZGVyKGUudGFyZ2V0LnZhbHVlKTsKICAkKCIjYnRuLW5ldyIpLm9uY2xpY2sgPSAoKSA9PiBjcmVhdGVSZXNvdXJjZShraW5kKTsKfQoKYXN5bmMgZnVuY3Rpb24gc2VsZWN0RnVuY3Rpb24oZm4pewogIGNvbnN0IGVsPSQoIiNmbi1pbnNwZWN0b3IiKTsgaWYoIWVsKSByZXR1cm47CiAgY29uc3Qgc3BlYz1mbi5zcGVjfHx7fTsgY29uc3QgbnM9Zm4ubWV0YWRhdGEubmFtZXNwYWNlLCBuYW1lPWZuLm1ldGFkYXRhLm5hbWU7CiAgbGV0IG1ldD17fTsKICB0cnl7IGNvbnN0IG09YXdhaXQgYXBpKCJHRVQiLCIvYXBpL21ldHJpY3MiKTsgY29uc3QgbGFzdD0obS5zZXJpZXN8fFtdKS5zbGljZSgtMSlbMF07IG1ldD0obGFzdCYmbGFzdC5mbnMmJmxhc3QuZm5zW25hbWVdKXx8e307IH1jYXRjaChlKXt9CiAgY29uc3QgaWR4PU1hdGgubWF4KDAsRlVOQ1MuZmluZEluZGV4KGY9PmYubWV0YWRhdGEubmFtZT09PW5hbWUpKTsKICBlbC5pbm5lckhUTUw9YAogICAgPGRpdiBjbGFzcz0iaW5zcC1oZWFkIj48ZGl2IGNsYXNzPSJnYXRlIj4ke1N0cmluZyhpZHgrMSkucGFkU3RhcnQoMiwiMCIpfTwvZGl2PgogICAgICA8ZGl2PjxoMj4ke2VzYyhuYW1lKX08L2gyPjxkaXYgY2xhc3M9InN1YiI+JHtlc2MobnMpfSAvICR7ZXNjKHNwZWMuZW52aXJvbm1lbnQ/Lm5hbWV8fCJweXRob24iKX0gwrcgJHtlc2Moc3BlYy5wYWNrYWdlPy5wYWNrYWdlcmVmPy5uYW1lfHwi4oCUIil9PC9kaXY+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0YXR1cyI+UkVBRFk8L2Rpdj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9InRhYnMiPjxidXR0b24gY2xhc3M9ImFjdGl2ZSIgZGF0YS1mdGFiPSJsb2dzIj7ml6Xlv5c8L2J1dHRvbj48YnV0dG9uIGRhdGEtZnRhYj0iaW52b2tlIj7osIPnlKjmtYvor5U8L2J1dHRvbj48YnV0dG9uIGRhdGEtZnRhYj0ieWFtbCI+WUFNTDwvYnV0dG9uPjxidXR0b24gZGF0YS1mdGFiPSJtZXRhIj7op6blj5Hlmag8L2J1dHRvbj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImtwaXMiPgogICAgICA8ZGl2IGNsYXNzPSJrcGkiPjxkaXYgY2xhc3M9ImsiPkNQVTwvZGl2PjxkaXYgY2xhc3M9InYiPiR7bWV0LmNwdV9tPz8i4oCUIn0ke21ldC5jcHVfbSE9bnVsbD8ibSI6IiJ9PC9kaXY+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9ImtwaSI+PGRpdiBjbGFzcz0iayI+5YaF5a2YPC9kaXY+PGRpdiBjbGFzcz0idiI+JHttZXQubWVtX21pPz8i4oCUIn0ke21ldC5tZW1fbWkhPW51bGw/Ik1pIjoiIn08L2Rpdj48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ia3BpIj48ZGl2IGNsYXNzPSJrIj7lia/mnKw8L2Rpdj48ZGl2IGNsYXNzPSJ2Ij4ke21ldC5wb2RzPz8i4oCUIn08L2Rpdj48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ia3BpIj48ZGl2IGNsYXNzPSJrIj5QOTk8L2Rpdj48ZGl2IGNsYXNzPSJ2Ij7igJQ8L2Rpdj48L2Rpdj4KICAgIDwvZGl2PgogICAgPGRpdiBpZD0idGFiLWJvZHkiPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iYWN0aW9ucyI+PGJ1dHRvbiBjbGFzcz0iYnRuIHByaW1hcnkiIGlkPSJpbnNwLWludm9rZSI+5Y+R6LW36LCD55SoPC9idXR0b24+PGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0iaW5zcC15YW1sIj7mn6XnnIsgWUFNTDwvYnV0dG9uPjxidXR0b24gY2xhc3M9ImJ0biIgaWQ9Imluc3AtZ2l0Ij7lkIzmraXliLDku5PlupM8L2J1dHRvbj48L2Rpdj5gOwogIGNvbnN0IHNob3c9dGFiPT57CiAgICAkJCgiW2RhdGEtZnRhYl0iKS5mb3JFYWNoKGI9PmIuY2xhc3NMaXN0LnRvZ2dsZSgiYWN0aXZlIixiLmRhdGFzZXQuZnRhYj09PXRhYikpOwogICAgaWYodGFiPT09ImxvZ3MiKSB0YWJMb2dzKG5zLG5hbWUpOwogICAgZWxzZSBpZih0YWI9PT0iaW52b2tlIikgdGFiSW52b2tlKG5zLG5hbWUsZm4uX3RyaWdnZXJzfHxbXSk7CiAgICBlbHNlIGlmKHRhYj09PSJ5YW1sIikgdGFiWWFtbChmbik7CiAgICBlbHNlICQoIiN0YWItYm9keSIpLmlubmVySFRNTD1gPGRpdiBjbGFzcz0idGFibGUtd3JhcCI+PHRhYmxlPjx0aGVhZD48dHI+PHRoPuinpuWPkeWZqDwvdGg+PHRoPlVSTDwvdGg+PHRoPuaWueazlTwvdGg+PC90cj48L3RoZWFkPjx0Ym9keT4KICAgICAgJHsoZm4uX3RyaWdnZXJzfHxbXSkubWFwKHQ9PmA8dHI+PHRkIGNsYXNzPSJtb25vIj4ke2VzYyh0Lm1ldGFkYXRhLm5hbWUpfTwvdGQ+PHRkIGNsYXNzPSJtb25vIj4ke2VzYyh0LnNwZWMucmVsYXRpdmV1cmx8fHQuc3BlYy5yZWxhdGl2ZVVybHx8dC5zcGVjLnByZWZpeHx8IiIpfTwvdGQ+PHRkPiR7ZXNjKCh0LnNwZWMubWV0aG9kc3x8W3Quc3BlYy5tZXRob2RdKS5maWx0ZXIoQm9vbGVhbikuam9pbigiLCAiKSl9PC90ZD48L3RyPmApLmpvaW4oIiIpfHwiPHRyPjx0ZCBjb2xzcGFuPTM+5peg6Kem5Y+R5ZmoPC90ZD48L3RyPiJ9PC90Ym9keT48L3RhYmxlPjwvZGl2PmA7CiAgfTsKICAkJCgiW2RhdGEtZnRhYl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+c2hvdyhiLmRhdGFzZXQuZnRhYikpOwogICQoIiNpbnNwLWludm9rZSIpLm9uY2xpY2s9KCk9PnsgbG9jYXRpb24uaGFzaD0iIy90ZXN0ZXIiOyB9OwogICQoIiNpbnNwLXlhbWwiKS5vbmNsaWNrPSgpPT5zaG93KCJ5YW1sIik7CiAgJCgiI2luc3AtZ2l0Iikub25jbGljaz0oKT0+eyBsb2NhdGlvbi5oYXNoPSIjL2dpdG9wcyI7IH07CiAgc2hvdygibG9ncyIpOwp9CmZ1bmN0aW9uIGNyZWF0ZVJlc291cmNlKGtpbmQpewogIGNvbnN0IG5zID0gS0lORFMuaW5jbHVkZXMoa2luZCkgPyAiZmlzc2lvbiIgOiAiZmlzc2lvbiI7CiAgY29uc3QgZm9ybSA9IEZPUk1TW2tpbmRdOwogIGNvbnN0IGJvZHkgPSBmb3JtCiAgICA/IGA8ZGl2IGNsYXNzPSJtb2RlLXN3aXRjaCI+PGJ1dHRvbiBjbGFzcz0iYWN0aXZlIiBkYXRhLW09ImZvcm0iPuihqOWNlTwvYnV0dG9uPjxidXR0b24gZGF0YS1tPSJqc29uIj5KU09OPC9idXR0b24+PC9kaXY+CiAgICAgICA8ZGl2IGNsYXNzPSJmb3JtLWdyaWQiIGlkPSJmb3JtLWFyZWEiPiR7Zm9ybS5tYXAoKFtwYXRoLGxhYmVsLHR5cGUscGhdKT0+CiAgICAgICAgIGA8bGFiZWw+JHtsYWJlbH08aW5wdXQgZGF0YS1wYXRoPSIke3BhdGh9IiB0eXBlPSIke3R5cGV9IiBwbGFjZWhvbGRlcj0iJHtlc2MocGgpfSI+PC9sYWJlbD5gKS5qb2luKCIiKX08L2Rpdj4KICAgICAgIDxkaXYgaWQ9Impzb24tYXJlYSIgc3R5bGU9ImRpc3BsYXk6bm9uZSI+PHRleHRhcmVhIGlkPSJqc29uLWlucHV0IiByb3dzPSIxNiIgc3R5bGU9IndpZHRoOjEwMCUiPiR7ZXNjKEpTT04uc3RyaW5naWZ5KFRFTVBMQVRFU1traW5kXShucyksbnVsbCwyKSl9PC90ZXh0YXJlYT48L2Rpdj5gCiAgICA6IGA8dGV4dGFyZWEgaWQ9Impzb24taW5wdXQiIHJvd3M9IjE2IiBzdHlsZT0id2lkdGg6MTAwJSI+JHtlc2MoSlNPTi5zdHJpbmdpZnkoVEVNUExBVEVTW2tpbmRdKG5zKSxudWxsLDIpKX08L3RleHRhcmVhPmA7CiAgbW9kYWwoYDxkaXYgY2xhc3M9Im1vZGFsLXBhZCI+PGgzPuaWsOW7uiAke0xBQkVMU1traW5kXX08L2gzPiR7Ym9keX0KICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6MTBweDtqdXN0aWZ5LWNvbnRlbnQ6ZmxleC1lbmQ7bWFyZ2luLXRvcDoxNnB4Ij4KICAgIDxidXR0b24gY2xhc3M9ImJ0biIgaWQ9Im0tY2FuY2VsIj7lj5bmtog8L2J1dHRvbj48YnV0dG9uIGNsYXNzPSJidG4gcHJpbWFyeSIgaWQ9Im0tc2F2ZSI+5Yib5bu6PC9idXR0b24+PC9kaXY+PC9kaXY+YCk7CiAgbGV0IHVzZUpzb24gPSAhZm9ybTsKICBpZiAoZm9ybSkgJCQoIi5tb2RlLXN3aXRjaCBidXR0b24iKS5mb3JFYWNoKGIgPT4gYi5vbmNsaWNrID0gKCkgPT4gewogICAgdXNlSnNvbiA9IGIuZGF0YXNldC5tID09PSAianNvbiI7CiAgICAkJCgiLm1vZGUtc3dpdGNoIGJ1dHRvbiIpLmZvckVhY2goeD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJhY3RpdmUiLCB4PT09YikpOwogICAgJCgiI2Zvcm0tYXJlYSIpLnN0eWxlLmRpc3BsYXkgPSB1c2VKc29uID8gIm5vbmUiIDogIiI7CiAgICAkKCIjanNvbi1hcmVhIikuc3R5bGUuZGlzcGxheSA9IHVzZUpzb24gPyAiIiA6ICJub25lIjsKICAgIGlmICh1c2VKc29uKXsKICAgICAgY29uc3QgdmFsdWVzID0ge307ICQkKCIjZm9ybS1hcmVhIGlucHV0IikuZm9yRWFjaChpID0+IHZhbHVlc1tpLmRhdGFzZXQucGF0aF0gPSBpLnZhbHVlKTsKICAgICAgY29uc3QgbWVyZ2VkID0gZm9ybVRvT2JqKFRFTVBMQVRFU1traW5kXShucyksIHZhbHVlcyk7CiAgICAgICQoIiNqc29uLWlucHV0IikudmFsdWUgPSBKU09OLnN0cmluZ2lmeShtZXJnZWQsIG51bGwsIDIpOwogICAgfQogIH0pOwogICQoIiNtLWNhbmNlbCIpLm9uY2xpY2sgPSBjbG9zZU1vZGFsOwogICQoIiNtLXNhdmUiKS5vbmNsaWNrID0gYXN5bmMgKCkgPT4gewogICAgbGV0IG9iajsKICAgIGlmICh1c2VKc29uIHx8ICFmb3JtKXsgb2JqID0gSlNPTi5wYXJzZSgkKCIjanNvbi1pbnB1dCIpLnZhbHVlKTsgfQogICAgZWxzZSB7CiAgICAgIGNvbnN0IHZhbHVlcyA9IHt9OyAkJCgiI2Zvcm0tYXJlYSBpbnB1dCIpLmZvckVhY2goaSA9PiB2YWx1ZXNbaS5kYXRhc2V0LnBhdGhdID0gaS52YWx1ZSk7CiAgICAgIG9iaiA9IGZvcm1Ub09iaihURU1QTEFURVNba2luZF0obnMpLCB2YWx1ZXMpOwogICAgfQogICAgdHJ5IHsgYXdhaXQgYXBpKCJQT1NUIiwgYC9hcGkvJHtraW5kfWAsIG9iaik7IHRvYXN0KGDlt7LliJvlu7ogJHtvYmoubWV0YWRhdGEubmFtZX1gKTsgY2xvc2VNb2RhbCgpOyBuYXZpZ2F0ZSgpOyB9CiAgICBjYXRjaChlKXsgdG9hc3QoIuWIm+W7uuWksei0pTogIiArIGUubWVzc2FnZSwgdHJ1ZSk7IH0KICB9Owp9CmZ1bmN0aW9uIGVkaXRSZXNvdXJjZShraW5kLCBucywgbmFtZSl7CiAgYXBpKCJHRVQiLCBgL2FwaS8ke2tpbmR9LyR7bnN9LyR7bmFtZX1gKS50aGVuKG9iaiA9PiB7CiAgICBtb2RhbChgPGRpdiBjbGFzcz0ibW9kYWwtcGFkIj48aDM+57yW6L6RICR7ZXNjKGtpbmQpfS8ke2VzYyhuYW1lKX08L2gzPgogICAgICA8dGV4dGFyZWEgaWQ9Impzb24taW5wdXQiIHJvd3M9IjE4IiBzdHlsZT0id2lkdGg6MTAwJSI+JHtlc2MoSlNPTi5zdHJpbmdpZnkob2JqLG51bGwsMikpfTwvdGV4dGFyZWE+CiAgICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6MTBweDtqdXN0aWZ5LWNvbnRlbnQ6ZmxleC1lbmQ7bWFyZ2luLXRvcDoxNHB4Ij4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0ibS1jYW5jZWwiPuWPlua2iDwvYnV0dG9uPjxidXR0b24gY2xhc3M9ImJ0biBwcmltYXJ5IiBpZD0ibS1zYXZlIj7kv53lrZgo5LmQ6KeC6ZSBKTwvYnV0dG9uPjwvZGl2PjwvZGl2PmApOwogICAgJCgiI20tY2FuY2VsIikub25jbGljayA9IGNsb3NlTW9kYWw7CiAgICAkKCIjbS1zYXZlIikub25jbGljayA9IGFzeW5jICgpID0+IHsKICAgICAgdHJ5IHsgYXdhaXQgYXBpKCJQVVQiLCBgL2FwaS8ke2tpbmR9LyR7bnN9LyR7bmFtZX1gLCBKU09OLnBhcnNlKCQoIiNqc29uLWlucHV0IikudmFsdWUpKTsKICAgICAgICB0b2FzdCgi5bey5L+d5a2YIik7IGNsb3NlTW9kYWwoKTsgbmF2aWdhdGUoKTsgfQogICAgICBjYXRjaChlKXsgdG9hc3QoIuS/neWtmOWksei0pTogIiArIGUubWVzc2FnZSwgdHJ1ZSk7IH0KICAgIH07CiAgfSk7Cn0KZnVuY3Rpb24gZGVsZXRlUmVzb3VyY2Uoa2luZCwgbnMsIG5hbWUpewogIG1vZGFsKGA8ZGl2IGNsYXNzPSJtb2RhbC1wYWQiPjxoMz7liKDpmaQgJHtlc2MobmFtZSl9PC9oMz4KICAgIDxwIHN0eWxlPSJjb2xvcjp2YXIoLS10ZXh0LTIpIj7mraTmk43kvZzkuI3lj6/mgaLlpI3jgILovpPlhaXotYTmupDlkI3np7Dku6Xnoa7orqQ6PC9wPgogICAgPGlucHV0IHR5cGU9InRleHQiIGlkPSJjb25maXJtLW5hbWUiIHBsYWNlaG9sZGVyPSIke2VzYyhuYW1lKX0iIHN0eWxlPSJ3aWR0aDoxMDAlIj4KICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6MTBweDtqdXN0aWZ5LWNvbnRlbnQ6ZmxleC1lbmQ7bWFyZ2luLXRvcDoxNnB4Ij4KICAgIDxidXR0b24gY2xhc3M9ImJ0biIgaWQ9Im0tY2FuY2VsIj7lj5bmtog8L2J1dHRvbj48YnV0dG9uIGNsYXNzPSJidG4gZGFuZ2VyIiBpZD0ibS1kZWwiIGRpc2FibGVkPuWIoOmZpDwvYnV0dG9uPjwvZGl2PjwvZGl2PmApOwogICQoIiNjb25maXJtLW5hbWUiKS5vbmlucHV0ID0gZSA9PiAkKCIjbS1kZWwiKS5kaXNhYmxlZCA9IGUudGFyZ2V0LnZhbHVlICE9PSBuYW1lOwogICQoIiNtLWNhbmNlbCIpLm9uY2xpY2sgPSBjbG9zZU1vZGFsOwogICQoIiNtLWRlbCIpLm9uY2xpY2sgPSBhc3luYyAoKSA9PiB7CiAgICB0cnkgeyBhd2FpdCBhcGkoIkRFTEVURSIsIGAvYXBpLyR7a2luZH0vJHtuc30vJHtuYW1lfWApOyB0b2FzdChg5bey5Yig6ZmkICR7bmFtZX1gKTsgY2xvc2VNb2RhbCgpOyBuYXZpZ2F0ZSgpOyB9CiAgICBjYXRjaChlKXsgdG9hc3QoIuWIoOmZpOWksei0pTogIiArIGUubWVzc2FnZSwgdHJ1ZSk7IH0KICB9Owp9CgovKiAtLS0tLS0tLS0tIGZ1bmN0aW9uIGRldGFpbCAtLS0tLS0tLS0tICovCmFzeW5jIGZ1bmN0aW9uIHZpZXdGdW5jdGlvbkRldGFpbChucywgbmFtZSl7CiAgY29uc3QgbWFpbiA9ICQoIiNtYWluIik7CiAgbWFpbi5pbm5lckhUTUwgPSBgPGEgY2xhc3M9ImJhY2siIGhyZWY9IiMvZnVuY3Rpb25zIj7ihpAg6L+U5ZueIEZ1bmN0aW9uczwvYT5gICsgc2tlbGV0b25zKCk7CiAgY29uc3QgZm4gPSBhd2FpdCBhcGkoIkdFVCIsIGAvYXBpL2Z1bmN0aW9ucy8ke25zfS8ke25hbWV9YCk7CiAgY29uc3QgdHJzID0gKGF3YWl0IGFwaSgiR0VUIiwgIi9hcGkvaHR0cHRyaWdnZXJzIikpLml0ZW1zLmZpbHRlcih0ID0+IGZuUmVmKHQuc3BlYykuc3BsaXQoIjoiKVswXSA9PT0gbmFtZSk7CiAgY29uc3Qgc3BlYyA9IGZuLnNwZWMgfHwge307CiAgY29uc3QgdHJpZ2dlclJvdyA9IHRycy5sZW5ndGgKICAgID8gYDx0cj48dGQ+6Kem5Y+R5ZmoPC90ZD48dGQ+JHt0cnMubWFwKHQ9PmA8c3BhbiBjbGFzcz0iY2hpcCI+JHtlc2ModC5tZXRhZGF0YS5uYW1lKX0gKCR7ZXNjKCh0LnNwZWMucmVsYXRpdmVVcmx8fHQuc3BlYy5wcmVmaXh8fCIiKSl9KTwvc3Bhbj5gKS5qb2luKCIgIil9PC90ZD48L3RyPmAgOiAiIjsKICBtYWluLmlubmVySFRNTCA9IGAKICAgIDxhIGNsYXNzPSJiYWNrIiBocmVmPSIjL2Z1bmN0aW9ucyIgc3R5bGU9ImZvbnQtc2l6ZToxMnB4O2NvbG9yOnZhcigtLWluay0zKTt0ZXh0LWRlY29yYXRpb246bm9uZSI+4oaQIEZ1bmN0aW9uczwvYT4KICAgIDxkaXYgY2xhc3M9ImRldGFpbC1oZWFkIj48aDEgc3R5bGU9Im1hcmdpbjowIj4ke2VzYyhuYW1lKX08L2gxPgogICAgICA8cCBjbGFzcz0ic3VidGl0bGUiIHN0eWxlPSJtYXJnaW46NnB4IDAgMCI+JHtlc2MobnMpfSDlkb3lkI3nqbrpl7Qgwrcg546v5aKDICR7ZXNjKHNwZWMuZW52aXJvbm1lbnQ/Lm5hbWV8fCItIil9PC9wPjwvZGl2PgogICAgPGRsIGNsYXNzPSJrdiI+CiAgICAgIDxkdD5QYWNrYWdlPC9kdD48ZGQgY2xhc3M9Im1vbm8iPiR7ZXNjKHNwZWMucGFja2FnZT8ucGFja2FnZXJlZj8ubmFtZXx8Ii0iKX08L2RkPgogICAgICA8ZHQ+5omn6KGM562W55WlPC9kdD48ZGQ+JHtlc2Moc3BlYy5pbnZva2VTdHJhdGVneT8uZXhlY3V0aW9uU3RyYXRlZ3k/LmV4ZWN1dG9yVHlwZXx8InBvb2xtZ3IiKX0KICAgICAgICDCtyDotoXml7YgJHtlc2Moc3BlYy5mdW5jdGlvblRpbWVvdXQ/PyItIil9cyDCtyDnqbrpl7Llm57mlLYgJHtlc2Moc3BlYy5pZGxldGltZW91dD8/Ii0iKX1zPC9kZD4KICAgICAgPGR0PuW5tuWPkTwvZHQ+PGRkPiR7ZXNjKHNwZWMuY29uY3VycmVuY3k/PyItIil9IMK3IOavj1BvZOivt+axguaVsCAke2VzYyhzcGVjLnJlcXVlc3RzUGVyUG9kPz8iLSIpfTwvZGQ+CiAgICAgICR7dHJpZ2dlclJvd30KICAgIDwvZGw+CiAgICA8ZGl2IGNsYXNzPSJ0YWJzIj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYWN0aXZlIiBkYXRhLXRhYj0ibG9ncyI+5pel5b+XPC9idXR0b24+CiAgICAgIDxidXR0b24gZGF0YS10YWI9Imludm9rZSI+6LCD55So5rWL6K+VPC9idXR0b24+CiAgICAgIDxidXR0b24gZGF0YS10YWI9InlhbWwiPllBTUw8L2J1dHRvbj4KICAgIDwvZGl2PgogICAgPGRpdiBpZD0idGFiLWJvZHkiPjwvZGl2PmA7CiAgbGV0IGFjdGl2ZSA9ICJsb2dzIjsKICBjb25zdCBib2R5ID0gKCkgPT4gewogICAgaWYgKGFjdGl2ZSA9PT0gImxvZ3MiKSByZXR1cm4gdGFiTG9ncyhucywgbmFtZSk7CiAgICBpZiAoYWN0aXZlID09PSAiaW52b2tlIikgcmV0dXJuIHRhYkludm9rZShucywgbmFtZSwgdHJzKTsKICAgIHJldHVybiB0YWJZYW1sKGZuKTsKICB9OwogICQkKCIudGFicyBidXR0b24iKS5mb3JFYWNoKGIgPT4gYi5vbmNsaWNrID0gKCkgPT4gewogICAgYWN0aXZlID0gYi5kYXRhc2V0LnRhYjsKICAgICQkKCIudGFicyBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnRvZ2dsZSgiYWN0aXZlIiwgeD09PWIpKTsKICAgIGJvZHkoKTsKICB9KTsKICBib2R5KCk7Cn0KYXN5bmMgZnVuY3Rpb24gdGFiTG9ncyhucywgbmFtZSl7CiAgY29uc3QgZWwgPSAkKCIjdGFiLWJvZHkiKTsKICBlbC5pbm5lckhUTUwgPSBgPHNlbGVjdCBpZD0ibG9nLXBvZCIgaGlkZGVuPjxvcHRpb24gdmFsdWU9IiI+5Yqg6L295Lit4oCmPC9vcHRpb24+PC9zZWxlY3Q+CiAgICA8ZGl2IGNsYXNzPSJsb2ctYm94Ij48c3BhbiBjbGFzcz0ibXV0ZWQiPuato+WcqOWKoOi9veaXpeW/l+KApjwvc3Bhbj48L2Rpdj5gOwogIGNvbnN0IGxvYWQgPSBhc3luYyAoKSA9PiB7CiAgICBjb25zdCBkID0gYXdhaXQgYXBpKCJHRVQiLCBgL2FwaS9mdW5jdGlvbnMvJHtuc30vJHtuYW1lfS9sb2dzP3BvZD0keyQoIiNsb2ctcG9kIikudmFsdWV8fCIifWApOwogICAgY29uc3Qgc2VsID0gJCgiI2xvZy1wb2QiKTsKICAgIGlmICghZC5wb2RzLmxlbmd0aCl7CiAgICAgIGVsLmlubmVySFRNTCA9IGA8ZGl2IGNsYXNzPSJsb2ctYm94Ij48c3BhbiBjbGFzcz0ibXV0ZWQiPuivpeWHveaVsOW9k+WJjeayoeaciei/kOihjOS4reeahCBQb2QocG9vbG1nciDlt7LnvKnlrrkp44CC6LCD55So5LiA5qyh5Ye95pWw5ZCO5Y2z5Y+v5p+l55yL5pel5b+X44CCPC9zcGFuPjwvZGl2PmA7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGlmIChzZWwub3B0aW9ucy5sZW5ndGggIT09IGQucG9kcy5sZW5ndGgpewogICAgICBzZWwuaW5uZXJIVE1MID0gZC5wb2RzLm1hcChwPT5gPG9wdGlvbiB2YWx1ZT0iJHtlc2MocC5uYW1lKX0iPiR7ZXNjKHAubmFtZSl9IMK3ICR7ZXNjKHAucGhhc2UpfSR7cC5yZWFkeT8iIjoiICjmnKrlsLHnu6opIn08L29wdGlvbj5gKS5qb2luKCIiKTsKICAgIH0gZWxzZSB7CiAgICAgIHNlbC52YWx1ZSA9IHNlbC52YWx1ZSB8fCBkLnBvZHNbMF0ubmFtZTsKICAgIH0KICAgIGNvbnN0IHBvZCA9IHNlbC52YWx1ZSB8fCBkLnBvZHNbMF0ubmFtZTsKICAgIGNvbnN0IHRleHQgPSBkLmxvZ3NbcG9kXSB8fCAiIjsKICAgIGVsLnF1ZXJ5U2VsZWN0b3IoIi5sb2ctYm94IikudGV4dENvbnRlbnQgPSB0ZXh0IHx8ICLvvIjmmoLml6Dml6Xlv5fovpPlh7rvvIkiOwogIH07CiAgJCgiI2xvZy1wb2QiKS5vbmNoYW5nZSA9IGxvYWQ7CiAgYXdhaXQgbG9hZCgpOwp9CmZ1bmN0aW9uIHRhYkludm9rZShucywgbmFtZSwgdHJzKXsKICBpZiAoIXRycy5sZW5ndGgpewogICAgJCgiI3RhYi1ib2R5IikuaW5uZXJIVE1MID0gZW1wdHlCb3goIuayoeacieWPr+eUqOeahCBIVFRQIFRyaWdnZXIiLAogICAgICAi5YWI5ZyoIEhUVFAgVHJpZ2dlcnMg6aG15Li66K+l5Ye95pWw5Yib5bu65LiA5Liq6Kem5Y+R5ZmoLOaJjeiDveS7juaOp+WItuWPsOiwg+eUqOOAgiIpOwogICAgcmV0dXJuOwogIH0KICAkKCIjdGFiLWJvZHkiKS5pbm5lckhUTUwgPSBgCiAgICA8ZGl2IGNsYXNzPSJmb3JtLWdyaWQiPgogICAgICA8bGFiZWw+6Kem5Y+R5ZmoPHNlbGVjdCBpZD0iaW52LXRyaWdnZXIiPiR7dHJzLm1hcCh0PT5gPG9wdGlvbiB2YWx1ZT0iJHtlc2ModC5tZXRhZGF0YS5uYW1lKX0iPiR7ZXNjKHQubWV0YWRhdGEubmFtZSl9ICgke2VzYyh0LnNwZWMucmVsYXRpdmVVcmx8fHQuc3BlYy5wcmVmaXh8fCIiKX0pPC9vcHRpb24+YCkuam9pbigiIil9PC9zZWxlY3Q+PC9sYWJlbD4KICAgICAgPGxhYmVsPkhUVFAg5pa55rOVPHNlbGVjdCBpZD0iaW52LW1ldGhvZCI+PG9wdGlvbj5HRVQ8L29wdGlvbj48b3B0aW9uPlBPU1Q8L29wdGlvbj48b3B0aW9uPlBVVDwvb3B0aW9uPjxvcHRpb24+REVMRVRFPC9vcHRpb24+PC9zZWxlY3Q+PC9sYWJlbD4KICAgICAgPGxhYmVsIGNsYXNzPSJmdWxsIj5IZWFkZXJzKEpTT04s5Y+v56m6KTxpbnB1dCBpZD0iaW52LWhlYWRlcnMiIHBsYWNlaG9sZGVyPSd7IlgtVGVzdCI6IjEifSc+PC9sYWJlbD4KICAgICAgPGxhYmVsIGNsYXNzPSJmdWxsIj5Cb2R5KOaWh+acrCxQT1NUL1BVVCDml7blj5HpgIEpPHRleHRhcmVhIGlkPSJpbnYtYm9keSIgcm93cz0iNSIgc3R5bGU9IndpZHRoOjEwMCUiPjwvdGV4dGFyZWE+PC9sYWJlbD4KICAgIDwvZGl2PgogICAgPGRpdiBzdHlsZT0ibWFyZ2luLXRvcDoxNHB4Ij48YnV0dG9uIGNsYXNzPSJidG4gcHJpbWFyeSIgaWQ9Imludi1nbyI+5Y+R6LW36LCD55SoPC9idXR0b24+CiAgICA8c3BhbiBpZD0iaW52LW1ldGEiIHN0eWxlPSJtYXJnaW4tbGVmdDoxMnB4O2NvbG9yOnZhcigtLXRleHQtMikiPjwvc3Bhbj48L2Rpdj4KICAgIDxkaXYgaWQ9Imludi1yZXN1bHQiIHN0eWxlPSJtYXJnaW4tdG9wOjE0cHgiPjwvZGl2PmA7CiAgJCgiI2ludi1nbyIpLm9uY2xpY2sgPSBhc3luYyAoKSA9PiB7CiAgICBjb25zdCBidG4gPSAkKCIjaW52LWdvIik7IGJ0bi5kaXNhYmxlZCA9IHRydWU7IGJ0bi50ZXh0Q29udGVudCA9ICLosIPnlKjkuK3igKYiOwogICAgJCgiI2ludi1yZXN1bHQiKS5pbm5lckhUTUwgPSAiIjsKICAgIGxldCBoZWFkZXJzID0ge307CiAgICBpZiAoJCgiI2ludi1oZWFkZXJzIikudmFsdWUudHJpbSgpKXsgdHJ5IHsgaGVhZGVycyA9IEpTT04ucGFyc2UoJCgiI2ludi1oZWFkZXJzIikudmFsdWUpOyB9CiAgICAgIGNhdGNoKGUpeyB0b2FzdCgiSGVhZGVycyDkuI3mmK/lkIjms5UgSlNPTiIsIHRydWUpOyBidG4uZGlzYWJsZWQ9ZmFsc2U7IGJ0bi50ZXh0Q29udGVudD0i5Y+R6LW36LCD55SoIjsgcmV0dXJuOyB9IH0KICAgIHRyeSB7CiAgICAgIGNvbnN0IHIgPSBhd2FpdCBhcGkoIlBPU1QiLCAiL2FwaS9pbnZva2UiLCB7CiAgICAgICAgbnMsIHRyaWdnZXI6ICQoIiNpbnYtdHJpZ2dlciIpLnZhbHVlLCBtZXRob2Q6ICQoIiNpbnYtbWV0aG9kIikudmFsdWUsCiAgICAgICAgaGVhZGVycywgYm9keTogJCgiI2ludi1ib2R5IikudmFsdWUgfHwgbnVsbCB9KTsKICAgICAgJCgiI2ludi1tZXRhIikudGV4dENvbnRlbnQgPSBgSFRUUCAke3Iuc3RhdHVzfSDCtyAke3IubGF0ZW5jeV9tc30gbXNgOwogICAgICAkKCIjaW52LXJlc3VsdCIpLmlubmVySFRNTCA9IGA8ZGl2IGNsYXNzPSJqc29uLXZpZXciPiR7ZXNjKHIuYm9keSB8fCAiKOepuuWTjeW6lCkiKX08L2Rpdj5gOwogICAgICBpZiAoci5zdGF0dXMgPj0gNDAwKSB0b2FzdChg6LCD55So6L+U5ZueIEhUVFAgJHtyLnN0YXR1c31gLCB0cnVlKTsKICAgIH0gY2F0Y2goZSl7IHRvYXN0KCLosIPnlKjlpLHotKU6ICIgKyBlLm1lc3NhZ2UsIHRydWUpOyB9CiAgICBidG4uZGlzYWJsZWQgPSBmYWxzZTsgYnRuLnRleHRDb250ZW50ID0gIuWPkei1t+iwg+eUqCI7CiAgfTsKfQpmdW5jdGlvbiB0YWJZYW1sKGZuKXsKICAkKCIjdGFiLWJvZHkiKS5pbm5lckhUTUwgPSBgPGRpdiBjbGFzcz0ianNvbi12aWV3Ij4ke2VzYyhKU09OLnN0cmluZ2lmeShmbixudWxsLDIpKX08L2Rpdj5gOwp9CgovKiAtLS0tLS0tLS0tIEFQSSDmtYvor5Xlj7DvvIhQb3N0bWFuIOi9u+mHj+eJiO+8iSAtLS0tLS0tLS0tICovCmxldCBUREFUQSA9IHtjb2xsZWN0aW9uczogW10sIGVudmlyb25tZW50czogW119OwpsZXQgVEVTVEVSID0ge21ldGhvZDoiR0VUIiwgdHJpZ2dlcjoiIiwgaGVhZGVyczpbXSwgcXVlcnk6W10sIGJvZHk6IiIsIGVudjoiIiwgbGFzdFJlc3A6bnVsbH07CmFzeW5jIGZ1bmN0aW9uIGxvYWRUZXN0ZGF0YSgpeyBUREFUQSA9IGF3YWl0IGFwaSgiR0VUIiwgIi9hcGkvdGVzdGRhdGEiKTsgfQphc3luYyBmdW5jdGlvbiBzYXZlVGVzdGRhdGEoKXsgYXdhaXQgYXBpKCJQVVQiLCAiL2FwaS90ZXN0ZGF0YSIsIHtkYXRhOiBUREFUQX0pOyB9CgpmdW5jdGlvbiBlbnZWYXJzKCl7CiAgY29uc3QgZW52ID0gVERBVEEuZW52aXJvbm1lbnRzLmZpbmQoZT0+ZS5uYW1lPT09VEVTVEVSLmVudik7CiAgY29uc3QgdmFycyA9IHt9OyAoZW52P2Vudi52YXJzOltdKS5mb3JFYWNoKHY9PnsgaWYodi5rZXkpIHZhcnNbdi5rZXldPXYudmFsdWU7IH0pOwogIHJldHVybiB2YXJzOwp9CmZ1bmN0aW9uIGFwcGx5VmFycyhzKXsKICBpZiAoIXMpIHJldHVybiBzOwogIHJldHVybiBTdHJpbmcocykucmVwbGFjZSgvXHtce1xzKihbXHcuLV0rKVxzKlx9XH0vZywgKG0sayk9PmVudlZhcnMoKVtrXSA/PyBtKTsKfQphc3luYyBmdW5jdGlvbiB2aWV3VGVzdGVyKHByZXNldCl7CiAgY29uc3QgbWFpbiA9ICQoIiNtYWluIik7CiAgbWFpbi5pbm5lckhUTUwgPSBoZXJvKCJBUEkg5rWL6K+V5Y+wIiwi5p6E6YCg6K+35rGC44CB5L+d5a2Y5pS26JeP44CB566h55CG546v5aKD5Y+Y6YeP44CB5p+l55yL5a6M5pW05ZON5bqUIiwiPGI+UkVRVUVTVCAvIFJFU1BPTlNFPC9iPjxicj7lj5jph4/mm7/mjaIgwrcg5Y6G5Y+y6YeN5pS+IikgKyBgPGRpdiBjbGFzcz0icGFnZS1ib2R5Ij48ZGl2IGlkPSJ0ZXN0ZXItcm9vdCI+JHtza2VsZXRvbnMoNCl9PC9kaXY+PC9kaXY+YDsKICBhd2FpdCBsb2FkVGVzdGRhdGEoKTsKICBjb25zdCB0cnMgPSAoYXdhaXQgYXBpKCJHRVQiLCAiL2FwaS9odHRwdHJpZ2dlcnMiKSkuaXRlbXM7CiAgaWYgKCF0cnMubGVuZ3RoKXsKICAgICQoIiN0ZXN0ZXItcm9vdCIpLmlubmVySFRNTCA9IGVtcHR5Qm94KCLmsqHmnInlj6/osIPnlKjnmoTop6blj5HlmagiLCLor7flhYjlnKggSFRUUCBUcmlnZ2VycyDpobXpnaLkuLrlh73mlbDliJvlu7rop6blj5Hlmags5YaN5Zue5p2l5rWL6K+V44CCIik7CiAgICByZXR1cm47CiAgfQogIGlmIChwcmVzZXQ/LnRyaWdnZXIpIFRFU1RFUi50cmlnZ2VyID0gcHJlc2V0LnRyaWdnZXI7CiAgaWYgKCFURVNURVIudHJpZ2dlciAmJiB0cnMubGVuZ3RoKSBURVNURVIudHJpZ2dlciA9IHRyc1swXS5tZXRhZGF0YS5uYW1lOwogIFRFU1RFUi5oZWFkZXJzID0gVEVTVEVSLmhlYWRlcnMubGVuZ3RoID8gVEVTVEVSLmhlYWRlcnMgOiBbe2s6IiIsdjoiIn1dOwogIFRFU1RFUi5xdWVyeSA9IFRFU1RFUi5xdWVyeS5sZW5ndGggPyBURVNURVIucXVlcnkgOiBbe2s6IiIsdjoiIn1dOwogIHJlbmRlclRlc3Rlcih0cnMpOwp9CmZ1bmN0aW9uIGt2Um93cyhraW5kKXsKICByZXR1cm4gVEVTVEVSW2tpbmRdLm1hcCgocm93LGkpPT5gPHRyPgogICAgPHRkPjxpbnB1dCBkYXRhLWt2PSIke2tpbmR9IiBkYXRhLWk9IiR7aX0iIGRhdGEtZj0iayIgdmFsdWU9IiR7ZXNjKHJvdy5rKX0iIHBsYWNlaG9sZGVyPSJrZXkiIHN0eWxlPSJ3aWR0aDoxMDAlIj48L3RkPgogICAgPHRkPjxpbnB1dCBkYXRhLWt2PSIke2tpbmR9IiBkYXRhLWk9IiR7aX0iIGRhdGEtZj0idiIgdmFsdWU9IiR7ZXNjKHJvdy52KX0iIHBsYWNlaG9sZGVyPSJ2YWx1ZSIgc3R5bGU9IndpZHRoOjEwMCUiPjwvdGQ+CiAgICA8dGQ+PGJ1dHRvbiBjbGFzcz0iYnRuIHNtYWxsIiBkYXRhLWt2LWRlbD0iJHtraW5kfSIgZGF0YS1pPSIke2l9Ij7DlzwvYnV0dG9uPjwvdGQ+PC90cj5gKS5qb2luKCIiKTsKfQpmdW5jdGlvbiByZW5kZXJUZXN0ZXIodHJzKXsKICAkKCIjdGVzdGVyLXJvb3QiKS5pbm5lckhUTUwgPSBgCiAgICA8ZGl2IGNsYXNzPSJ0ZXN0LWdyaWQiPgogICAgICA8YXNpZGUgY2xhc3M9InRlc3QtY29sIj4KICAgICAgICA8ZGl2IGNsYXNzPSJyYWlsLXRpdGxlIj7mlLbol4/pm4Y8L2Rpdj4KICAgICAgICA8ZGl2IGlkPSJjb2xsLWxpc3QiPiR7VERBVEEuY29sbGVjdGlvbnMubWFwKChjLGNpKT0+YAogICAgICAgICAgPGRpdiBjbGFzcz0iY29sbC1uYW1lIj4ke2VzYyhjLm5hbWUpfTwvZGl2PgogICAgICAgICAgJHtjLnJlcXVlc3RzLm1hcCgocixyaSk9PmA8ZGl2IGNsYXNzPSJyZXEtaXRlbSI+CiAgICAgICAgICAgIDxzcGFuIGNsYXNzPSJtIj4ke2VzYyhyLm1ldGhvZCl9PC9zcGFuPgogICAgICAgICAgICA8YnV0dG9uIGRhdGEtbG9hZC1yZXE9IiR7Y2l9fCR7cml9Ij4ke2VzYyhyLm5hbWUpfTwvYnV0dG9uPgogICAgICAgICAgICA8YnV0dG9uIGRhdGEtZGVsLXJlcT0iJHtjaX18JHtyaX0iIHN0eWxlPSJqdXN0aWZ5LXNlbGY6ZW5kO2NvbG9yOnZhcigtLWluay0zKSI+w5c8L2J1dHRvbj48L2Rpdj5gKS5qb2luKCIiKX1gKS5qb2luKCIiKQogICAgICAgICAgfHwgJzxkaXYgc3R5bGU9ImNvbG9yOnZhcigtLWluay0zKTtmb250LXNpemU6MTJweCI+6L+Y5rKh5pyJ5pS26JeP55qE6K+35rGCPC9kaXY+J30KICAgICAgICA8L2Rpdj4KICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc21hbGwiIGlkPSJhZGQtY29sbCIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCI+5paw5bu65pS26JeP6ZuGPC9idXR0b24+CiAgICAgICAgPGRpdiBjbGFzcz0icmFpbC10aXRsZSI+546v5aKDPC9kaXY+CiAgICAgICAgPHNlbGVjdCBpZD0iZW52LXNlbCIgc3R5bGU9IndpZHRoOjEwMCUiPjxvcHRpb24gdmFsdWU9IiI+5peg546v5aKDPC9vcHRpb24+CiAgICAgICAgICAke1REQVRBLmVudmlyb25tZW50cy5tYXAoZT0+YDxvcHRpb24gJHtlLm5hbWU9PT1URVNURVIuZW52PyJzZWxlY3RlZCI6IiJ9PiR7ZXNjKGUubmFtZSl9PC9vcHRpb24+YCkuam9pbigiIil9PC9zZWxlY3Q+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtYWxsIiBpZD0iZWRpdC1lbnYiIHN0eWxlPSJtYXJnaW4tdG9wOjZweCI+57yW6L6R5Y+Y6YePPC9idXR0b24+CiAgICAgIDwvYXNpZGU+CiAgICAgIDxzZWN0aW9uIGNsYXNzPSJ0ZXN0LW1haW4iPgogICAgICAgIDxkaXYgY2xhc3M9InVybGJhciI+CiAgICAgICAgICA8c2VsZWN0IGlkPSJyZXEtbWV0aG9kIj4ke1siR0VUIiwiUE9TVCIsIlBVVCIsIlBBVENIIiwiREVMRVRFIl0ubWFwKG09PmA8b3B0aW9uICR7bT09PVRFU1RFUi5tZXRob2Q/InNlbGVjdGVkIjoiIn0+JHttfTwvb3B0aW9uPmApLmpvaW4oIiIpfTwvc2VsZWN0PgogICAgICAgICAgPGlucHV0IGlkPSJyZXEtdHJpZ2dlci1pbnB1dCIgbGlzdD0idHJpZ2dlci1saXN0IiB2YWx1ZT0iJHtlc2MoVEVTVEVSLnRyaWdnZXIpfSIgcGxhY2Vob2xkZXI9IumAieaLqeinpuWPkeWZqCI+CiAgICAgICAgICA8ZGF0YWxpc3QgaWQ9InRyaWdnZXItbGlzdCI+JHt0cnMubWFwKHQ9PmA8b3B0aW9uIHZhbHVlPSIke2VzYyh0Lm1ldGFkYXRhLm5hbWUpfSI+JHtlc2ModC5zcGVjLnJlbGF0aXZldXJsfHx0LnNwZWMucmVsYXRpdmVVcmx8fHQuc3BlYy5wcmVmaXh8fCIiKX08L29wdGlvbj5gKS5qb2luKCIiKX08L2RhdGFsaXN0PgogICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0icmVxLXNlbmQiPuWPkemAgTwvYnV0dG9uPgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6MThweDttYXJnaW4tdG9wOjhweDtmbGV4LXdyYXA6d3JhcCI+CiAgICAgICAgICA8c3BhbiBjbGFzcz0ibW9ubyI+JHt0cnMuZmluZCh0PT50Lm1ldGFkYXRhLm5hbWU9PT1URVNURVIudHJpZ2dlcik/LnNwZWM/LnJlbGF0aXZldXJsfHwiIn08L3NwYW4+CiAgICAgICAgICA8c3BhbiBjbGFzcz0ibW9ubyIgc3R5bGU9ImNvbG9yOnZhcigtLWluay0zKSI+5Y+Y6YeP55SoIHt7bmFtZX19IOW8leeUqOeOr+Wig+WAvDwvc3Bhbj4KICAgICAgICA8L2Rpdj4KICAgICAgICA8aDI+UXVlcnkg5Y+C5pWwPC9oMj4KICAgICAgICA8dGFibGUgY2xhc3M9Imt2LXRhYmxlIj48dGJvZHkgaWQ9InF1ZXJ5LXJvd3MiPiR7a3ZSb3dzKCJxdWVyeSIpfTwvdGJvZHk+PC90YWJsZT4KICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc21hbGwiIGRhdGEta3YtYWRkPSJxdWVyeSIgc3R5bGU9Im1hcmdpbi10b3A6NnB4Ij4rIOWPguaVsDwvYnV0dG9uPgogICAgICAgIDxoMj5IZWFkZXJzPC9oMj4KICAgICAgICA8dGFibGUgY2xhc3M9Imt2LXRhYmxlIj48dGJvZHkgaWQ9ImhlYWRlci1yb3dzIj4ke2t2Um93cygiaGVhZGVycyIpfTwvdGJvZHk+PC90YWJsZT4KICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc21hbGwiIGRhdGEta3YtYWRkPSJoZWFkZXJzIiBzdHlsZT0ibWFyZ2luLXRvcDo2cHgiPisgSGVhZGVyPC9idXR0b24+CiAgICAgICAgPGgyPkJvZHk8L2gyPgogICAgICAgIDx0ZXh0YXJlYSBpZD0icmVxLWJvZHkiIHJvd3M9IjgiIHN0eWxlPSJ3aWR0aDoxMDAlIiBwbGFjZWhvbGRlcj0neyJwbGFuIjoicHJvIiwic2VhdHMiOjI1fSc+JHtlc2MoVEVTVEVSLmJvZHkpfTwvdGV4dGFyZWE+CiAgICAgICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo4cHg7bWFyZ2luLXRvcDo4cHgiPgogICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0iYm9keS1wcmV0dHkiPuagvOW8j+WMliBKU09OPC9idXR0b24+CiAgICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4iIGlkPSJzYXZlLXJlcSI+5L+d5a2Y5Yiw5pS26JePPC9idXR0b24+CiAgICAgICAgPC9kaXY+CiAgICAgIDwvc2VjdGlvbj4KICAgICAgPGFzaWRlIGNsYXNzPSJ0ZXN0LXNpZGUiPgogICAgICAgIDxkaXYgY2xhc3M9InJhaWwtdGl0bGUiIHN0eWxlPSJtYXJnaW4tdG9wOjAiPuWTjeW6lDwvZGl2PgogICAgICAgIDxkaXYgaWQ9InJlc3Atcm9vdCI+JHtURVNURVIubGFzdFJlc3AgPyAiIiA6ICc8ZGl2IGNsYXNzPSJlbXB0eSIgc3R5bGU9InBhZGRpbmc6MjhweCA4cHgiPjxzdHJvbmc+562J5b6F6K+35rGCPC9zdHJvbmc+5Y+R6YCB5ZCO5Zyo5q2k5pi+56S654q25oCB44CB6ICX5pe25LiO5ZON5bqU5L2TPC9kaXY+J308L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJyYWlsLXRpdGxlIj7ljoblj7I8L2Rpdj4KICAgICAgICA8ZGl2IGlkPSJoaXN0LXJvb3QiPjwvZGl2PgogICAgICA8L2FzaWRlPgogICAgPC9kaXY+YDsKICBiaW5kVGVzdGVyKHRycyk7Cn0KZnVuY3Rpb24gYmluZFRlc3Rlcih0cnMpewogIGNvbnN0IHJvb3QgPSAkKCIjdGVzdGVyLXJvb3QiKTsKICByb290Lm9uaW5wdXQgPSBlID0+IHsKICAgIGlmIChlLnRhcmdldC5kYXRhc2V0Lmt2KXsgVEVTVEVSW2UudGFyZ2V0LmRhdGFzZXQua3ZdWytlLnRhcmdldC5kYXRhc2V0LmldW2UudGFyZ2V0LmRhdGFzZXQuZl0gPSBlLnRhcmdldC52YWx1ZTsgfQogICAgaWYgKGUudGFyZ2V0LmlkID09PSAicmVxLWJvZHkiKSBURVNURVIuYm9keSA9IGUudGFyZ2V0LnZhbHVlOwogICAgaWYgKGUudGFyZ2V0LmlkID09PSAicmVxLW1ldGhvZCIpIFRFU1RFUi5tZXRob2QgPSBlLnRhcmdldC52YWx1ZTsKICAgIGlmIChlLnRhcmdldC5pZCA9PT0gInJlcS10cmlnZ2VyLWlucHV0IikgVEVTVEVSLnRyaWdnZXIgPSBlLnRhcmdldC52YWx1ZTsKICAgIGlmIChlLnRhcmdldC5pZCA9PT0gImVudi1zZWwiKSBURVNURVIuZW52ID0gZS50YXJnZXQudmFsdWU7CiAgfTsKICByb290Lm9uY2xpY2sgPSBhc3luYyBlID0+IHsKICAgIGNvbnN0IHQgPSBlLnRhcmdldDsKICAgIGlmICh0LmRhdGFzZXQua3ZBZGQpeyBURVNURVJbdC5kYXRhc2V0Lmt2QWRkXS5wdXNoKHtrOiIiLHY6IiJ9KTsgcmVuZGVyVGVzdGVyKHRycyk7IH0KICAgIGlmICh0LmRhdGFzZXQua3ZEZWwpeyBURVNURVJbdC5kYXRhc2V0Lmt2RGVsXS5zcGxpY2UoK3QuZGF0YXNldC5pLDEpOyByZW5kZXJUZXN0ZXIodHJzKTsgfQogICAgaWYgKHQuaWQgPT09ICJib2R5LXByZXR0eSIpeyB0cnl7IFRFU1RFUi5ib2R5ID0gSlNPTi5zdHJpbmdpZnkoSlNPTi5wYXJzZShURVNURVIuYm9keSksbnVsbCwyKTsgJCgiI3JlcS1ib2R5IikudmFsdWUgPSBURVNURVIuYm9keTsgfWNhdGNoKGVycil7IHRvYXN0KCJCb2R5IOS4jeaYr+WQiOazlSBKU09OIiwgdHJ1ZSk7IH0gfQogICAgaWYgKHQuaWQgPT09ICJyZXEtc2VuZCIpIGF3YWl0IHNlbmRUZXN0KCk7CiAgICBpZiAodC5pZCA9PT0gInNhdmUtcmVxIikgc2F2ZVJlcXVlc3QoKTsKICAgIGlmICh0LmRhdGFzZXQubG9hZFJlcSl7IGNvbnN0IFtjaSxyaV09dC5kYXRhc2V0LmxvYWRSZXEuc3BsaXQoInwiKS5tYXAoTnVtYmVyKTsgT2JqZWN0LmFzc2lnbihURVNURVIsIFREQVRBLmNvbGxlY3Rpb25zW2NpXS5yZXF1ZXN0c1tyaV0pOyByZW5kZXJUZXN0ZXIodHJzKTsgfQogICAgaWYgKHQuZGF0YXNldC5kZWxSZXEpeyBjb25zdCBbY2kscmldPXQuZGF0YXNldC5kZWxSZXEuc3BsaXQoInwiKS5tYXAoTnVtYmVyKTsgVERBVEEuY29sbGVjdGlvbnNbY2ldLnJlcXVlc3RzLnNwbGljZShyaSwxKTsgYXdhaXQgc2F2ZVRlc3RkYXRhKCk7IHJlbmRlclRlc3Rlcih0cnMpOyB9CiAgICBpZiAodC5pZCA9PT0gImFkZC1jb2xsIil7IGNvbnN0IG5hbWU9cHJvbXB0KCLmlLbol4/pm4blkI3np7AiKTsgaWYobmFtZSl7IFREQVRBLmNvbGxlY3Rpb25zLnB1c2goe25hbWUscmVxdWVzdHM6W119KTsgYXdhaXQgc2F2ZVRlc3RkYXRhKCk7IHJlbmRlclRlc3Rlcih0cnMpOyB9IH0KICAgIGlmICh0LmlkID09PSAiZWRpdC1lbnYiKSBlZGl0RW52aXJvbm1lbnQoKTsKICB9OwogIHJlbmRlckhpc3RvcnkoKTsKfQpmdW5jdGlvbiBoZWFkZXJzT2JqKCl7CiAgY29uc3Qgb3V0PXt9OyBURVNURVIuaGVhZGVycy5mb3JFYWNoKGg9PnsgaWYoaC5rKSBvdXRbYXBwbHlWYXJzKGguayldID0gYXBwbHlWYXJzKGgudik7IH0pOyByZXR1cm4gb3V0Owp9CmZ1bmN0aW9uIHF1ZXJ5T2JqKCl7CiAgY29uc3Qgb3V0PXt9OyBURVNURVIucXVlcnkuZm9yRWFjaChxPT57IGlmKHEuaykgb3V0W2FwcGx5VmFycyhxLmspXSA9IGFwcGx5VmFycyhxLnYpOyB9KTsgcmV0dXJuIG91dDsKfQphc3luYyBmdW5jdGlvbiBzZW5kVGVzdCgpewogIGNvbnN0IGJ0bj0kKCIjcmVxLXNlbmQiKTsgYnRuLmRpc2FibGVkPXRydWU7IGJ0bi50ZXh0Q29udGVudD0i5Y+R6YCB5Lit4oCmIjsKICBsZXQgYm9keSA9IGFwcGx5VmFycyhURVNURVIuYm9keSkgfHwgbnVsbDsKICBpZiAoYm9keSAmJiBbIlBPU1QiLCJQVVQiLCJQQVRDSCJdLmluY2x1ZGVzKFRFU1RFUi5tZXRob2QpKXsKICAgIHRyeXsgSlNPTi5wYXJzZShib2R5KTsgfWNhdGNoKGUpeyAvKiB0cmVhdCBhcyByYXcgdGV4dCAqLyB9CiAgfQogIGNvbnN0IHN0YXJ0ZWQ9cGVyZm9ybWFuY2Uubm93KCk7CiAgdHJ5ewogICAgY29uc3QgciA9IGF3YWl0IGFwaSgiUE9TVCIsICIvYXBpL2ludm9rZSIsIHtuczoiZmlzc2lvbiIsIHRyaWdnZXI6VEVTVEVSLnRyaWdnZXIsIG1ldGhvZDpURVNURVIubWV0aG9kLAogICAgICBoZWFkZXJzOmhlYWRlcnNPYmooKSwgcXVlcnk6cXVlcnlPYmooKSwgYm9keX0pOwogICAgVEVTVEVSLmxhc3RSZXNwID0gcjsKICAgIHRyeSB7IHJlbmRlclJlc3BvbnNlKHIsIE1hdGgucm91bmQocGVyZm9ybWFuY2Uubm93KCktc3RhcnRlZCkpOyB9CiAgICBjYXRjaChlcnIpeyBjb25zb2xlLmVycm9yKGVycik7IHRvYXN0KCLlk43lupTmuLLmn5PlpLHotKU6ICIgKyBlcnIubWVzc2FnZSwgdHJ1ZSk7IH0KICAgIHB1c2hIaXN0b3J5KHt0czpEYXRlLm5vdygpLCBtZXRob2Q6VEVTVEVSLm1ldGhvZCwgdHJpZ2dlcjpURVNURVIudHJpZ2dlciwgc3RhdHVzOnIuc3RhdHVzLCBsYXRlbmN5OnIubGF0ZW5jeV9tcywKICAgICAgcmVxOntoZWFkZXJzOmhlYWRlcnNPYmooKSwgcXVlcnk6cXVlcnlPYmooKSwgYm9keX0sIHJlc3A6cn0pOwogIH1jYXRjaChlKXsgdG9hc3QoIuWPkemAgeWksei0pTogIitlLm1lc3NhZ2UsIHRydWUpOyBURVNURVIubGFzdFJlc3A9bnVsbDsgfQogIGJ0bi5kaXNhYmxlZD1mYWxzZTsgYnRuLnRleHRDb250ZW50PSLlj5HpgIEiOwp9CmZ1bmN0aW9uIHJlbmRlclJlc3BvbnNlKHIsIHdhbGwpewogIGNvbnN0IG9rID0gci5zdGF0dXMgPCA0MDA7CiAgY29uc3Qgc2l6ZSA9IG5ldyBCbG9iKFtyLmJvZHl8fCIiXSkuc2l6ZTsKICAkKCIjcmVzcC1yb290IikuaW5uZXJIVE1MID0gYAogICAgPGRpdiBjbGFzcz0icmVzcC1oZWFkIj4KICAgICAgPHNwYW4gY2xhc3M9InJlc3AtY29kZSAke29rPyIiOiJiYWQifSI+SFRUUCAke3Iuc3RhdHVzfTwvc3Bhbj4KICAgICAgPHNwYW4gY2xhc3M9InJlc3AtbWV0YSI+JHtyLmxhdGVuY3lfbXN9IG1zIMK3ICR7c2l6ZX0gQiDCtyDlvoDov5QgJHt3YWxsfSBtczwvc3Bhbj4KICAgIDwvZGl2PgogICAgPGRpdiBjbGFzcz0icmVzcC10YWJzIj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYWN0aXZlIiBkYXRhLXJlc3AtdGFiPSJib2R5Ij5Cb2R5PC9idXR0b24+PGJ1dHRvbiBkYXRhLXJlc3AtdGFiPSJoZWFkZXJzIj5IZWFkZXJzPC9idXR0b24+CiAgICA8L2Rpdj4KICAgIDxkaXYgaWQ9InJlc3AtcGFuZSI+PC9kaXY+YDsKICBjb25zdCBib2R5UGFuZSA9ICgpID0+IHsKICAgIGxldCBwcmV0dHkgPSByLmJvZHkgfHwgIiI7CiAgICB0cnkgeyBwcmV0dHkgPSBKU09OLnN0cmluZ2lmeShKU09OLnBhcnNlKHByZXR0eSksIG51bGwsIDIpOyB9IGNhdGNoKGUpe30KICAgICQoIiNyZXNwLXBhbmUiKS5pbm5lckhUTUwgPSBgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo2cHg7bWFyZ2luLWJvdHRvbTo2cHgiPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc21hbGwiIGRhdGEtcmVzcC1tb2RlPSJwcmV0dHkiPlByZXR0eTwvYnV0dG9uPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc21hbGwiIGRhdGEtcmVzcC1tb2RlPSJyYXciPlJhdzwvYnV0dG9uPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc21hbGwiIGlkPSJjb3B5LXJlc3AiPuWkjeWItjwvYnV0dG9uPjwvZGl2PgogICAgICA8cHJlIGNsYXNzPSJyZXNwLWJvZHkiIGlkPSJyZXNwLWJvZHkiPiR7ZXNjKHByZXR0eSl9PC9wcmU+YDsKICAgICQkKCIjcmVzcC1wYW5lIFtkYXRhLXJlc3AtbW9kZV0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogICAgICAkJCgiI3Jlc3AtcGFuZSBbZGF0YS1yZXNwLW1vZGVdIikuZm9yRWFjaCh4PT54LmNsYXNzTGlzdC50b2dnbGUoImFjdGl2ZSIsIHg9PT1iKSk7CiAgICAgICQoIiNyZXNwLWJvZHkiKS50ZXh0Q29udGVudCA9IGIuZGF0YXNldC5yZXNwTW9kZT09PSJwcmV0dHkiID8gcHJldHR5IDogKHIuYm9keXx8IiIpOwogICAgfSk7CiAgICAkKCIjY29weS1yZXNwIikub25jbGljayA9ICgpID0+IHsgbmF2aWdhdG9yLmNsaXBib2FyZD8ud3JpdGVUZXh0KHIuYm9keXx8IiIpOyB0b2FzdCgi5bey5aSN5Yi25ZON5bqUIik7IH07CiAgfTsKICBib2R5UGFuZSgpOwogICQkKCJbZGF0YS1yZXNwLXRhYl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogICAgJCQoIltkYXRhLXJlc3AtdGFiXSIpLmZvckVhY2goeD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJhY3RpdmUiLHg9PT1iKSk7CiAgICBpZihiLmRhdGFzZXQucmVzcFRhYj09PSJib2R5IikgYm9keVBhbmUoKTsKICAgIGVsc2UgJCgiI3Jlc3AtcGFuZSIpLmlubmVySFRNTCA9IGA8dGFibGU+PHRoZWFkPjx0cj48dGg+SGVhZGVyPC90aD48dGg+VmFsdWU8L3RoPjwvdHI+PC90aGVhZD48dGJvZHk+JHtPYmplY3QuZW50cmllcyhyLmhlYWRlcnN8fHt9KS5tYXAoKFtrLHZdKT0+YDx0cj48dGQgY2xhc3M9Im1vbm8iPiR7ZXNjKGspfTwvdGQ+PHRkIGNsYXNzPSJtb25vIj4ke2VzYyh2KX08L3RkPjwvdHI+YCkuam9pbigiIil9PC90Ym9keT48L3RhYmxlPmA7CiAgfSk7Cn0KZnVuY3Rpb24gc2F2ZVJlcXVlc3QoKXsKICBjb25zdCBuYW1lID0gcHJvbXB0KCLor7fmsYLlkI3np7AiKTsKICBpZighbmFtZSkgcmV0dXJuOwogIGxldCBjb2xsID0gVERBVEEuY29sbGVjdGlvbnNbMF07CiAgaWYoIWNvbGwpeyBjb2xsPXtuYW1lOiLpu5jorqTmlLbol4/pm4YiLHJlcXVlc3RzOltdfTsgVERBVEEuY29sbGVjdGlvbnMucHVzaChjb2xsKTsgfQogIGNvbGwucmVxdWVzdHMucHVzaCh7bmFtZSwgbWV0aG9kOlRFU1RFUi5tZXRob2QsIHRyaWdnZXI6VEVTVEVSLnRyaWdnZXIsCiAgICBoZWFkZXJzOlRFU1RFUi5oZWFkZXJzLmZpbHRlcihoPT5oLmspLCBxdWVyeTpURVNURVIucXVlcnkuZmlsdGVyKHE9PnEuayksIGJvZHk6VEVTVEVSLmJvZHksIGVudjpURVNURVIuZW52fSk7CiAgc2F2ZVRlc3RkYXRhKCkudGhlbigoKT0+e3RvYXN0KCLlt7Lkv53lrZgiKTsgdmlld1Rlc3RlcigpO30pOwp9CmZ1bmN0aW9uIGVkaXRFbnZpcm9ubWVudCgpewogIGNvbnN0IG5hbWUgPSBwcm9tcHQoIueOr+Wig+WQjeensCjnlZnnqbrliJvlu7rmlrDnjq/looMpIiwgVEVTVEVSLmVudik7CiAgaWYobmFtZT09PW51bGwpIHJldHVybjsKICBjb25zdCBlbnYgPSBUREFUQS5lbnZpcm9ubWVudHMuZmluZChlPT5lLm5hbWU9PT1uYW1lKSB8fCAoVERBVEEuZW52aXJvbm1lbnRzLnB1c2goe25hbWUsdmFyczpbXX0pLCBUREFUQS5lbnZpcm9ubWVudHNbVERBVEEuZW52aXJvbm1lbnRzLmxlbmd0aC0xXSk7CiAgY29uc3QgdGV4dCA9IGVudi52YXJzLm1hcCh2PT5gJHt2LmtleX09JHt2LnZhbHVlfWApLmpvaW4oIlxuIik7CiAgY29uc3QgbmV4dCA9IHByb21wdCgi5q+P6KGMIEtFWT1WQUxVRSIsIHRleHQpOwogIGlmKG5leHQ9PT1udWxsKSByZXR1cm47CiAgZW52LnZhcnMgPSBuZXh0LnNwbGl0KCJcbiIpLm1hcChsPT57Y29uc3QgaT1sLmluZGV4T2YoIj0iKTsgcmV0dXJuIGk8MD9udWxsOntrZXk6bC5zbGljZSgwLGkpLnRyaW0oKSwgdmFsdWU6bC5zbGljZShpKzEpfTt9KS5maWx0ZXIoQm9vbGVhbik7CiAgVEVTVEVSLmVudiA9IG5hbWU7CiAgc2F2ZVRlc3RkYXRhKCkudGhlbigoKT0+e3RvYXN0KCLnjq/looPlt7Lkv53lrZgiKTsgdmlld1Rlc3RlcigpO30pOwp9CmZ1bmN0aW9uIHB1c2hIaXN0b3J5KGl0ZW0pewogIGNvbnN0IGggPSBKU09OLnBhcnNlKGxvY2FsU3RvcmFnZS5nZXRJdGVtKCJmYy1oaXN0b3J5Iil8fCJbXSIpOwogIGgudW5zaGlmdChpdGVtKTsgbG9jYWxTdG9yYWdlLnNldEl0ZW0oImZjLWhpc3RvcnkiLCBKU09OLnN0cmluZ2lmeShoLnNsaWNlKDAsNTApKSk7IHJlbmRlckhpc3RvcnkoKTsKfQpmdW5jdGlvbiByZW5kZXJIaXN0b3J5KCl7CiAgY29uc3QgZWw9JCgiI2hpc3Qtcm9vdCIpOyBpZighZWwpIHJldHVybjsKICBjb25zdCBoPUpTT04ucGFyc2UobG9jYWxTdG9yYWdlLmdldEl0ZW0oImZjLWhpc3RvcnkiKXx8IltdIik7CiAgZWwuaW5uZXJIVE1MID0gaC5sZW5ndGggPyBgPGRpdiBjbGFzcz0idGFibGUtd3JhcCI+PHRhYmxlPjx0aGVhZD48dHI+PHRoPuaXtumXtDwvdGg+PHRoPuaWueazlTwvdGg+PHRoPuinpuWPkeWZqDwvdGg+PHRoPueKtuaAgTwvdGg+PHRoPuiAl+aXtjwvdGg+PHRoPjwvdGg+PC90cj48L3RoZWFkPjx0Ym9keT4KICAgICR7aC5tYXAoKHgsaSk9PmA8dHI+PHRkPiR7bmV3IERhdGUoeC50cykudG9Mb2NhbGVUaW1lU3RyaW5nKCl9PC90ZD48dGQ+JHtlc2MoeC5tZXRob2QpfTwvdGQ+PHRkIGNsYXNzPSJtb25vIj4ke2VzYyh4LnRyaWdnZXIpfTwvdGQ+CiAgICAgIDx0ZD4ke2JhZGdlKHguc3RhdHVzPDQwMD8ib2siOiJlcnIiLHguc3RhdHVzKX08L3RkPjx0ZD4ke3gubGF0ZW5jeX0gbXM8L3RkPgogICAgICA8dGQ+PGJ1dHRvbiBjbGFzcz0iYnRuIHNtYWxsIiBkYXRhLWhpc3Rvcnk9IiR7aX0iPumHjeaUvjwvYnV0dG9uPjwvdGQ+PC90cj5gKS5qb2luKCIiKX08L3Rib2R5PjwvdGFibGU+PC9kaXY+YAogICAgOiBlbXB0eUJveCgi5pqC5peg5Y6G5Y+y6K6w5b2VIiwi5Y+R6YCB5LiA5qyh6K+35rGC5ZCO5Lya5Ye6546w5Zyo6L+Z6YeM44CCIik7CiAgJCQoIltkYXRhLWhpc3RvcnldIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IHg9aFsrYi5kYXRhc2V0Lmhpc3RvcnldOwogICAgVEVTVEVSLm1ldGhvZD14Lm1ldGhvZDsgVEVTVEVSLnRyaWdnZXI9eC50cmlnZ2VyOwogICAgVEVTVEVSLmhlYWRlcnM9T2JqZWN0LmVudHJpZXMoeC5yZXEuaGVhZGVyc3x8e30pLm1hcCgoW2ssdl0pPT4oe2ssdn0pKTsKICAgIFRFU1RFUi5xdWVyeT1PYmplY3QuZW50cmllcyh4LnJlcS5xdWVyeXx8e30pLm1hcCgoW2ssdl0pPT4oe2ssdn0pKTsKICAgIFRFU1RFUi5ib2R5PXgucmVxLmJvZHl8fCIiOwogICAgdmlld1Rlc3RlcigpOwogIH0pOwp9CgovKiAtLS0tLS0tLS0tIEdpdE9wcyB2MiAtLS0tLS0tLS0tICovCmFzeW5jIGZ1bmN0aW9uIHZpZXdHaXRvcHNWMigpewogICQoIiNtYWluIikuaW5uZXJIVE1MID0gaGVybygiR2l0T3BzIOWPjOWQkeWQjOatpSIsIuS7k+W6k+aYr+S6i+Wunua6kO+8jOmbhue+pOaYr+i/kOihjOaAge+8m+S4pOi+uemDveWPr+S7peWPkei1t+WPmOabtCIsIjxiPlBVTEwgLyBQVVNIPC9iPjxicj7kurrlt6Xnoa7orqTlkI7nlJ/mlYgiKSArIGA8ZGl2IGNsYXNzPSJwYWdlLWJvZHkiPgogICAgPGRpdiBjbGFzcz0icGlwZWxpbmUiPgogICAgICA8ZGl2IGNsYXNzPSJzdGFnZSI+PGRpdiBjbGFzcz0ibiI+U291cmNlPC9kaXY+PGRpdiBjbGFzcz0iZCI+R2l0SHViIOS7k+W6kzwvZGl2PjxkaXYgY2xhc3M9InMiPllBTUwg5riF5Y2VPC9kaXY+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0YWdlIj48ZGl2IGNsYXNzPSJuIj5QbGFuPC9kaXY+PGRpdiBjbGFzcz0iZCI+5beu5byC5oqV5b2xPC9kaXY+PGRpdiBjbGFzcz0icyI+Y3JlYXRlIC8gdXBkYXRlIC8gZGVsZXRlPC9kaXY+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0YWdlIj48ZGl2IGNsYXNzPSJuIj5BcHBseTwvZGl2PjxkaXYgY2xhc3M9ImQiPuWQjOatpembhue+pDwvZGl2PjxkaXYgY2xhc3M9InMiPuW9kuWxnuagh+etvuS/neaKpDwvZGl2PjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGFnZSI+PGRpdiBjbGFzcz0ibiI+V3JpdGViYWNrPC9kaXY+PGRpdiBjbGFzcz0iZCI+UFIg5oiW55u05o6oPC9kaXY+PGRpdiBjbGFzcz0icyI+5Lq65bel6Kem5Y+RPC9kaXY+PC9kaXY+CiAgICA8L2Rpdj4KICAgIDxkaXYgY2xhc3M9InRvb2xiYXIiPjxidXR0b24gY2xhc3M9ImJ0biBwcmltYXJ5IiBpZD0iYnRuLWFkZC1zcmMiPua3u+WKoOS7k+W6k+a6kDwvYnV0dG9uPjwvZGl2PjxkaXYgaWQ9InNyYy1saXN0Ij4ke3NrZWxldG9ucygpfTwvZGl2PjxkaXYgaWQ9ImdpdG9wcy1zdW1tYXJ5Ij48L2Rpdj48L2Rpdj5gOwogIGNvbnN0IGNmZyA9IGF3YWl0IGFwaSgiR0VUIiwiL2FwaS9jb25maWciKTsKICBpZiAoIWNmZy5zb3VyY2VzLmxlbmd0aCl7CiAgICAkKCIjc3JjLWxpc3QiKS5pbm5lckhUTUwgPSBlbXB0eUJveCgi6L+Y5rKh5pyJ5LuT5bqT5rqQIiwi54K55Ye744CM5re75Yqg5LuT5bqT5rqQ44CN6L+e5o6l5LiA5Liq5YyF5ZCrIEZpc3Npb24g5riF5Y2V55qEIEdpdEh1YiDku5PlupPjgIIiKTsKICB9IGVsc2UgewogICAgJCgiI3NyYy1saXN0IikuaW5uZXJIVE1MID0gYDxkaXYgc3R5bGU9ImJvcmRlci10b3A6MnB4IHNvbGlkIHZhcigtLWluaykiPiR7Y2ZnLnNvdXJjZXMubWFwKHM9PmAKICAgICAgPGRpdiBjbGFzcz0ic291cmNlLXJvdyI+CiAgICAgICAgPGRpdj48ZGl2IGNsYXNzPSJubSI+JHtlc2Mocy5uYW1lKX08L2Rpdj48ZGl2IGNsYXNzPSJtb25vIj4ke3Mud3JpdGVNb2RlPT09ImRpcmVjdCI/IkRJUkVDVCBQVVNIIjoiUFVMTCBSRVFVRVNUIn08L2Rpdj48L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJtb25vIj4ke2VzYyhzLnJlcG8pfTxicj4ke2VzYyhzLmJyYW5jaHx8Im1haW4iKX0ke3MucGF0aD8iIC8gIitlc2Mocy5wYXRoKToiIn08L2Rpdj4KICAgICAgICA8ZGl2PiR7cy53cml0ZU1vZGU9PT0iZGlyZWN0Ij9iYWRnZSgid2FybiIsIuebtOaOqCIpOmJhZGdlKCJvayIsIlBSIil9PC9kaXY+CiAgICAgICAgPGRpdj4ke3MuYXV0b01pbnV0ZXM/YmFkZ2UoImJ1aWxkIiwi5q+PICIrcy5hdXRvTWludXRlcysiIOWIhiIpOmJhZGdlKCJpZGxlIiwi5omL5YqoIil9PC9kaXY+CiAgICAgICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo0cHg7anVzdGlmeS1jb250ZW50OmZsZXgtZW5kO2ZsZXgtd3JhcDp3cmFwIj4KICAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbWFsbCIgZGF0YS1icm93c2U9IiR7ZXNjKHMubmFtZSl9Ij7mtY/op4g8L2J1dHRvbj4KICAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbWFsbCIgZGF0YS1wbGFuMj0iJHtlc2Mocy5uYW1lKX0iPuW3ruW8gjwvYnV0dG9uPgogICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtYWxsIHByaW1hcnkiIGRhdGEtZXhwb3J0Mj0iJHtlc2Mocy5uYW1lKX0iPuWGmeWbnjwvYnV0dG9uPgogICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtYWxsIiBkYXRhLWVkaXQtc3JjMj0iJHtlc2Mocy5uYW1lKX0iPue8lui+kTwvYnV0dG9uPgogICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtYWxsIGRhbmdlciIgZGF0YS1kZWwtc3JjMj0iJHtlc2Mocy5uYW1lKX0iPuWIoOmZpDwvYnV0dG9uPgogICAgICAgIDwvZGl2PgogICAgICA8L2Rpdj5gKS5qb2luKCIiKX08L2Rpdj5gOwogIH0KICAkKCIjYnRuLWFkZC1zcmMiKS5vbmNsaWNrID0gKCkgPT4gZWRpdFNvdXJjZTIobnVsbCwgbnVsbCk7CiAgJCQoIltkYXRhLWVkaXQtc3JjMl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ZWRpdFNvdXJjZTIoYi5kYXRhc2V0LmVkaXRTcmMyLCBjZmcuc291cmNlcy5maW5kKHM9PnMubmFtZT09PWIuZGF0YXNldC5lZGl0U3JjMikpKTsKICAkJCgiW2RhdGEtZGVsLXNyYzJdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgaWYoIWNvbmZpcm0oYOWIoOmZpOS7k+W6k+a6kCAke2IuZGF0YXNldC5kZWxTcmMyfT9gKSkgcmV0dXJuOwogICAgY2ZnLnNvdXJjZXMgPSBjZmcuc291cmNlcy5maWx0ZXIocz0+cy5uYW1lIT09Yi5kYXRhc2V0LmRlbFNyYzIpOwogICAgYXdhaXQgYXBpKCJQVVQiLCIvYXBpL2NvbmZpZyIsY2ZnKTsgdG9hc3QoIuW3suWIoOmZpCIpOyB2aWV3R2l0b3BzVjIoKTsKICB9KTsKICAkJCgiW2RhdGEtYnJvd3NlXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT5icm93c2VSZXBvKGIuZGF0YXNldC5icm93c2UpKTsKICAkJCgiW2RhdGEtcGxhbjJdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnBsYW5WMihiLmRhdGFzZXQucGxhbjIpKTsKICAkJCgiW2RhdGEtZXhwb3J0Ml0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ZXhwb3J0VjIoYi5kYXRhc2V0LmV4cG9ydDIpKTsKICBjb25zdCBmaXJzdD0oY2ZnLnNvdXJjZXN8fFtdKVswXTsKICBpZihmaXJzdCkgbG9hZEdpdG9wc1N1bW1hcnkoZmlyc3QubmFtZSk7Cn0KYXN5bmMgZnVuY3Rpb24gbG9hZEdpdG9wc1N1bW1hcnkobmFtZSl7CiAgY29uc3QgZWw9JCgiI2dpdG9wcy1zdW1tYXJ5Iik7IGlmKCFlbCkgcmV0dXJuOwogIGVsLmlubmVySFRNTD1gPGgyPuS7k+W6k+aAgeWKvzwvaDI+JHtza2VsZXRvbnMoMyl9YDsKICB0cnl7CiAgICBjb25zdCBbdHJlZSxwbGFuXT1hd2FpdCBQcm9taXNlLmFsbChbCiAgICAgIGFwaSgiUE9TVCIsIi9hcGkvZ2l0b3BzL3RyZWUiLHtuYW1lfSksCiAgICAgIGFwaSgiUE9TVCIsIi9hcGkvZ2l0b3BzL3BsYW4iLHtuYW1lfSkKICAgIF0pOwogICAgY29uc3QgY2hhbmdlcz0ocGxhbi5jcmVhdGV8fFtdKS5sZW5ndGgrKHBsYW4udXBkYXRlfHxbXSkubGVuZ3RoOwogICAgY29uc3Qgc3RhdGVzPXt9OwogICAgKHRyZWUucmVzb3VyY2VzfHxbXSkuZm9yRWFjaChyPT5zdGF0ZXNbci5zdGF0ZV09KHN0YXRlc1tyLnN0YXRlXXx8MCkrMSk7CiAgICBlbC5pbm5lckhUTUw9YDxoMj7ku5PlupPmgIHlir8gwrcgJHtlc2ModHJlZS5yZXBvKX1AJHtlc2ModHJlZS5icmFuY2gpfTwvaDI+CiAgICAgIDxkaXYgY2xhc3M9InN0cmlwIj4KICAgICAgICA8ZGl2IGNsYXNzPSJjZWxsIj48ZGl2IGNsYXNzPSJrIj7ku5PlupPmlofku7Y8L2Rpdj48ZGl2IGNsYXNzPSJ2Ij4ke3RyZWUuZmlsZXMubGVuZ3RofTwvZGl2PjwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9ImNlbGwiPjxkaXYgY2xhc3M9ImsiPuino+aekOi1hOa6kDwvZGl2PjxkaXYgY2xhc3M9InYiPiR7dHJlZS5yZXNvdXJjZXMubGVuZ3RofTwvZGl2PjwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9ImNlbGwiPjxkaXYgY2xhc3M9ImsiPuW+heW6lOeUqDwvZGl2PjxkaXYgY2xhc3M9InYiPiR7Y2hhbmdlc308L2Rpdj48L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJjZWxsIj48ZGl2IGNsYXNzPSJrIj7liKDpmaTlgJnpgIk8L2Rpdj48ZGl2IGNsYXNzPSJ2Ij4keyhwbGFuLmRlbGV0ZXx8W10pLmxlbmd0aH08L2Rpdj48L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJjZWxsIj48ZGl2IGNsYXNzPSJrIj7lhrLnqoE8L2Rpdj48ZGl2IGNsYXNzPSJ2Ij4keyhwbGFuLmNvbmZsaWN0fHxbXSkubGVuZ3RofTwvZGl2PjwvZGl2PgogICAgICA8L2Rpdj4KICAgICAgPGgyPui1hOa6kOeKtuaAgTwvaDI+CiAgICAgICR7dHJlZS5yZXNvdXJjZXMubGVuZ3RoP2A8ZGl2IGNsYXNzPSJ0YWJsZS13cmFwIj48dGFibGU+PHRoZWFkPjx0cj48dGg+6LWE5rqQPC90aD48dGg+S2luZDwvdGg+PHRoPuaWh+S7tjwvdGg+PHRoPueKtuaAgTwvdGg+PC90cj48L3RoZWFkPjx0Ym9keT4KICAgICAgICAke3RyZWUucmVzb3VyY2VzLnNsaWNlKDAsMTIpLm1hcChyPT5gPHRyPjx0ZD48c3BhbiBjbGFzcz0ibmFtZSI+JHtlc2Moci5uYW1lKX08L3NwYW4+PGRpdiBjbGFzcz0ibW9ubyI+JHtlc2Moci5ucyl9PC9kaXY+PC90ZD4KICAgICAgICAgIDx0ZD4ke2VzYyhyLmtpbmQpfTwvdGQ+PHRkIGNsYXNzPSJtb25vIj4ke2VzYyhyLnBhdGgpfTwvdGQ+PHRkPiR7c3RhdGVCYWRnZShyLnN0YXRlKX08L3RkPjwvdHI+YCkuam9pbigiIil9CiAgICAgIDwvdGJvZHk+PC90YWJsZT48L2Rpdj5gOmVtcHR5Qm94KCLku5PlupPkuK3ov5jmsqHmnIkgRmlzc2lvbiDotYTmupAiLCLmiorpm4bnvqTotYTmupDlhpnlm57ku5PlupMs5oiW5Zyo5LuT5bqT5Lit5re75YqgIFlBTUwg5riF5Y2V44CCIil9YDsKICB9Y2F0Y2goZSl7IGVsLmlubmVySFRNTD1lbXB0eUJveCgi5LuT5bqT5oCB5Yq/5LiN5Y+v55SoIixlLm1lc3NhZ2UpOyB9Cn0KZnVuY3Rpb24gZWRpdFNvdXJjZTIobmFtZSwgZXhpc3RpbmcpewogIGNvbnN0IHMgPSBleGlzdGluZyB8fCB7bmFtZToiIixyZXBvOiIiLGJyYW5jaDoibWFpbiIscGF0aDoiIixhdXRvTWludXRlczowLHdyaXRlTW9kZToicHIifTsKICBtb2RhbChgPGRpdiBjbGFzcz0ibW9kYWwtcGFkIj48aDM+JHtuYW1lPyLnvJbovpEiOiLmt7vliqAifeS7k+W6k+a6kDwvaDM+CiAgICA8ZGl2IGNsYXNzPSJmb3JtLWdyaWQiPgogICAgICA8bGFiZWw+5ZCN56ewPGlucHV0IGlkPSJzMi1uYW1lIiB2YWx1ZT0iJHtlc2Mocy5uYW1lKX0iICR7bmFtZT8iZGlzYWJsZWQiOiIifT48L2xhYmVsPgogICAgICA8bGFiZWw+5LuT5bqTKG93bmVyL25hbWUpPGlucHV0IGlkPSJzMi1yZXBvIiB2YWx1ZT0iJHtlc2Mocy5yZXBvKX0iPjwvbGFiZWw+CiAgICAgIDxsYWJlbD7liIbmlK88aW5wdXQgaWQ9InMyLWJyYW5jaCIgdmFsdWU9IiR7ZXNjKHMuYnJhbmNofHwibWFpbiIpfSI+PC9sYWJlbD4KICAgICAgPGxhYmVsPuWtkOebruW9lTxpbnB1dCBpZD0iczItcGF0aCIgdmFsdWU9IiR7ZXNjKHMucGF0aHx8IiIpfSI+PC9sYWJlbD4KICAgICAgPGxhYmVsPuWGmeWbnuaooeW8jzxzZWxlY3QgaWQ9InMyLW1vZGUiPjxvcHRpb24gdmFsdWU9InByIiAke3Mud3JpdGVNb2RlIT09ImRpcmVjdCI/InNlbGVjdGVkIjoiIn0+UFIo5o6o6I2QKTwvb3B0aW9uPjxvcHRpb24gdmFsdWU9ImRpcmVjdCIgJHtzLndyaXRlTW9kZT09PSJkaXJlY3QiPyJzZWxlY3RlZCI6IiJ9PuebtOaOpeaPkOS6pDwvb3B0aW9uPjwvc2VsZWN0PjwvbGFiZWw+CiAgICAgIDxsYWJlbD7oh6rliqjmi4nlj5Yo5YiG6ZKfLDA95YWz6ZetKTxpbnB1dCBpZD0iczItYXV0byIgdHlwZT0ibnVtYmVyIiB2YWx1ZT0iJHtzLmF1dG9NaW51dGVzfHwwfSI+PC9sYWJlbD4KICAgICAgPGxhYmVsIGNsYXNzPSJmdWxsIj5HaXRIdWIgVG9rZW4o5a2YIFNlY3JldCznlZnnqbrkuI3kv67mlLkpPGlucHV0IGlkPSJzMi10b2tlbiIgdHlwZT0icGFzc3dvcmQiIHBsYWNlaG9sZGVyPSJnaHBfLi4uIj48L2xhYmVsPgogICAgPC9kaXY+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjEwcHg7anVzdGlmeS1jb250ZW50OmZsZXgtZW5kO21hcmdpbi10b3A6MTZweCI+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biIgaWQ9Im0tY2FuY2VsIj7lj5bmtog8L2J1dHRvbj48YnV0dG9uIGNsYXNzPSJidG4gcHJpbWFyeSIgaWQ9Im0tc2F2ZSI+5L+d5a2YPC9idXR0b24+PC9kaXY+PC9kaXY+YCk7CiAgJCgiI20tY2FuY2VsIikub25jbGljayA9IGNsb3NlTW9kYWw7CiAgJCgiI20tc2F2ZSIpLm9uY2xpY2sgPSBhc3luYyAoKSA9PiB7CiAgICBjb25zdCBzcmMgPSB7bmFtZTokKCIjczItbmFtZSIpLnZhbHVlLnRyaW0oKSwgcmVwbzokKCIjczItcmVwbyIpLnZhbHVlLnRyaW0oKSwKICAgICAgYnJhbmNoOiQoIiNzMi1icmFuY2giKS52YWx1ZS50cmltKCl8fCJtYWluIiwgcGF0aDokKCIjczItcGF0aCIpLnZhbHVlLnRyaW0oKSwKICAgICAgYXV0b01pbnV0ZXM6cGFyc2VJbnQoJCgiI3MyLWF1dG8iKS52YWx1ZXx8IjAiKSwgd3JpdGVNb2RlOiQoIiNzMi1tb2RlIikudmFsdWV9OwogICAgaWYoIXNyYy5uYW1lfHwhc3JjLnJlcG8peyB0b2FzdCgi5ZCN56ew5ZKM5LuT5bqT5b+F5aGrIix0cnVlKTsgcmV0dXJuOyB9CiAgICBjb25zdCB0b2tlbiA9ICQoIiNzMi10b2tlbiIpLnZhbHVlLnRyaW0oKTsKICAgIGNvbnN0IGNmZyA9IGF3YWl0IGFwaSgiR0VUIiwiL2FwaS9jb25maWciKTsKICAgIGNmZy5zb3VyY2VzID0gKGNmZy5zb3VyY2VzfHxbXSkuZmlsdGVyKHg9PngubmFtZSE9PShuYW1lfHxzcmMubmFtZSkpOwogICAgY2ZnLnNvdXJjZXMucHVzaChzcmMpOwogICAgdHJ5ewogICAgICBhd2FpdCBhcGkoIlBVVCIsIi9hcGkvY29uZmlnIixjZmcpOwogICAgICBpZih0b2tlbikgYXdhaXQgYXBpKCJQT1NUIiwiL2FwaS9zb3VyY2UtdG9rZW4iLHtuYW1lOnNyYy5uYW1lLHRva2VufSk7CiAgICAgIHRvYXN0KCLlt7Lkv53lrZgiKTsgY2xvc2VNb2RhbCgpOyB2aWV3R2l0b3BzVjIoKTsKICAgIH1jYXRjaChlKXsgdG9hc3QoIuS/neWtmOWksei0pTogIitlLm1lc3NhZ2UsdHJ1ZSk7IH0KICB9Owp9CmFzeW5jIGZ1bmN0aW9uIGJyb3dzZVJlcG8obmFtZSl7CiAgbW9kYWwoYDxkaXYgY2xhc3M9Im1vZGFsLXBhZCIgc3R5bGU9IndpZHRoOm1pbigxMTAwcHgsOTR2dykiPjxoMz7ku5PlupPmtY/op4ggwrcgJHtlc2MobmFtZSl9PC9oMz48ZGl2IGlkPSJicm93c2UtYm9keSI+JHtza2VsZXRvbnMoNil9PC9kaXY+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7anVzdGlmeS1jb250ZW50OmZsZXgtZW5kO21hcmdpbi10b3A6MTJweCI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0ibS1jYW5jZWwiPuWFs+mXrTwvYnV0dG9uPjwvZGl2PjwvZGl2PmApOwogICQoIiNtLWNhbmNlbCIpLm9uY2xpY2sgPSBjbG9zZU1vZGFsOwogIHRyeXsKICAgIGNvbnN0IHQgPSBhd2FpdCBhcGkoIlBPU1QiLCIvYXBpL2dpdG9wcy90cmVlIix7bmFtZX0pOwogICAgJCgiI2Jyb3dzZS1ib2R5IikuaW5uZXJIVE1MID0gYDxkaXYgY2xhc3M9ImJyb3dzZXItZ3JpZCI+CiAgICAgIDxkaXY+PGRpdiBjbGFzcz0icmFpbC10aXRsZSIgc3R5bGU9Im1hcmdpbi10b3A6MCI+5LuT5bqT5paH5Lu2IMK3ICR7dC5maWxlcy5sZW5ndGh9PC9kaXY+PGRpdiBzdHlsZT0ibWF4LWhlaWdodDo0NDBweDtvdmVyZmxvdzphdXRvIj4KICAgICAgICAke3QuZmlsZXMubWFwKGY9PmA8ZGl2IGNsYXNzPSJicm93c2VyLWZpbGUiIGRhdGEtZmlsZT0iJHtlc2MoZi5wYXRoKX0iPiR7ZXNjKGYucGF0aCl9PHNwYW4gc3R5bGU9ImZsb2F0OnJpZ2h0O2NvbG9yOnZhcigtLWluay0zKSI+JHtmLnNpemV9Qjwvc3Bhbj48L2Rpdj5gKS5qb2luKCIiKXx8IjxkaXYgY2xhc3M9J2VtcHR5Jz7ml6Dmlofku7Y8L2Rpdj4ifQogICAgICA8L2Rpdj48L2Rpdj4KICAgICAgPGRpdj48ZGl2IGNsYXNzPSJyYWlsLXRpdGxlIiBzdHlsZT0ibWFyZ2luLXRvcDowIj7mlofku7blhoXlrrk8L2Rpdj48cHJlIGNsYXNzPSJyZXNwLWJvZHkiIGlkPSJmaWxlLXZpZXciIHN0eWxlPSJtYXgtaGVpZ2h0OjMwMHB4Ij7pgInmi6nlt6bkvqfmlofku7bmn6XnnIs8L3ByZT48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0icmVzLWNlbGwiPjxkaXYgY2xhc3M9InJhaWwtdGl0bGUiIHN0eWxlPSJtYXJnaW4tdG9wOjAiPuino+aekOi1hOa6kCDCtyAke3QucmVzb3VyY2VzLmxlbmd0aH08L2Rpdj4KICAgICAgICA8ZGl2IHN0eWxlPSJtYXgtaGVpZ2h0OjM0MHB4O292ZXJmbG93OmF1dG8iPjx0YWJsZT48dGhlYWQ+PHRyPjx0aD7otYTmupA8L3RoPjx0aD5LaW5kPC90aD48dGg+54q25oCBPC90aD48L3RyPjwvdGhlYWQ+PHRib2R5PgogICAgICAgICR7dC5yZXNvdXJjZXMubWFwKHI9PmA8dHI+PHRkIGNsYXNzPSJtb25vIj4ke2VzYyhyLm5zKX0vJHtlc2Moci5uYW1lKX08L3RkPjx0ZD4ke2VzYyhyLmtpbmQpfTwvdGQ+CiAgICAgICAgICA8dGQ+JHtzdGF0ZUJhZGdlKHIuc3RhdGUpfTwvdGQ+PC90cj5gKS5qb2luKCIiKX08L3Rib2R5PjwvdGFibGU+PC9kaXY+CiAgICAgIDwvZGl2PjwvZGl2PmA7CiAgICAkJCgiW2RhdGEtZmlsZV0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICAgIGNvbnN0IGYgPSBhd2FpdCBhcGkoIlBPU1QiLCIvYXBpL2dpdG9wcy9maWxlIix7bmFtZSxwYXRoOmIuZGF0YXNldC5maWxlfSk7CiAgICAgICQoIiNmaWxlLXZpZXciKS50ZXh0Q29udGVudCA9IGYuY29udGVudDsKICAgIH0pOwogIH1jYXRjaChlKXsgJCgiI2Jyb3dzZS1ib2R5IikuaW5uZXJIVE1MID0gZW1wdHlCb3goIuS7k+W6k+ivu+WPluWksei0pSIsIGUubWVzc2FnZSk7IH0KfQpmdW5jdGlvbiBzdGF0ZUJhZGdlKHMpewogIGNvbnN0IG1hcCA9IHtzeW5jZWQ6WyJvayIsIuW3suWQjOatpSJdLCJyZXBvLWNoYW5nZWQiOlsid2FybiIsIuS7k+W6k+acieWPmOabtCJdLCJjbHVzdGVyLWNoYW5nZWQiOlsiYnVpbGQiLCLpm4bnvqTmnInlj5jmm7QiXSwKICAgIGNvbmZsaWN0OlsiZXJyIiwi5Y+M5pa55Yay56qBIl0sInJlcG8tb25seSI6WyJpZGxlIiwi5LuF5LuT5bqTIl19OwogIGNvbnN0IG0gPSBtYXBbc118fFsiaWRsZSIsc107IHJldHVybiBiYWRnZShtWzBdLG1bMV0pOwp9CmFzeW5jIGZ1bmN0aW9uIHBsYW5WMihuYW1lKXsKICBtb2RhbChgPGRpdiBjbGFzcz0ibW9kYWwtcGFkIiBzdHlsZT0id2lkdGg6bWluKDkwMHB4LDk0dncpIj48aDM+5beu5byC5LiO5ZCM5q2lIMK3ICR7ZXNjKG5hbWUpfTwvaDM+PGRpdiBpZD0icGxhbjItYm9keSI+JHtza2VsZXRvbnMoNCl9PC9kaXY+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjEwcHg7anVzdGlmeS1jb250ZW50OmZsZXgtZW5kO21hcmdpbi10b3A6MTRweCI+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biIgaWQ9Im0tY2FuY2VsIj7lhbPpl608L2J1dHRvbj48YnV0dG9uIGNsYXNzPSJidG4gcHJpbWFyeSIgaWQ9InBsYW4yLWFwcGx5Ij7lupTnlKg8L2J1dHRvbj48L2Rpdj48L2Rpdj5gKTsKICAkKCIjbS1jYW5jZWwiKS5vbmNsaWNrID0gY2xvc2VNb2RhbDsKICBsZXQgcGxhbjsKICB0cnl7IHBsYW4gPSBhd2FpdCBhcGkoIlBPU1QiLCIvYXBpL2dpdG9wcy9wbGFuIix7bmFtZX0pOyB9CiAgY2F0Y2goZSl7ICQoIiNwbGFuMi1ib2R5IikuaW5uZXJIVE1MID0gZW1wdHlCb3goIumihOiniOWksei0pSIsZS5tZXNzYWdlKTsgcmV0dXJuOyB9CiAgY29uc3Qgc2VjPSh0aXRsZSxhcnIscmVuZGVyKT0+YXJyLmxlbmd0aD9gPGgyPiR7dGl0bGV9KCR7YXJyLmxlbmd0aH0pPC9oMj48ZGl2IGNsYXNzPSJ0YWJsZS13cmFwIj48dGFibGU+PHRoZWFkPjx0cj48dGg+6LWE5rqQPC90aD48dGg+S2luZDwvdGg+PHRoPiR7cmVuZGVyPyLlpITnkIYiOiLkvY3nva4ifTwvdGg+PC90cj48L3RoZWFkPjx0Ym9keT4KICAgICR7YXJyLm1hcCgoaSxpZHgpPT5gPHRyPjx0ZCBjbGFzcz0ibW9ubyI+JHtlc2MoaS5ucyl9LyR7ZXNjKGkubmFtZSl9PC90ZD48dGQ+JHtlc2MoaS5raW5kKX08L3RkPgogICAgICA8dGQ+JHtyZW5kZXI/cmVuZGVyKGksaWR4KTplc2MoaS5wYXRofHwiIil9PC90ZD48L3RyPmApLmpvaW4oIiIpfTwvdGJvZHk+PC90YWJsZT48L2Rpdj5gOiIiOwogICQoIiNwbGFuMi1ib2R5IikuaW5uZXJIVE1MID0KICAgIGA8cCBjbGFzcz0ic3VidGl0bGUiPuS7k+W6kyAke2VzYyhwbGFuLnJlcG8pfUAke2VzYyhwbGFuLmJyYW5jaCl9IMK3ICR7cGxhbi5maWxlc30g5Liq5paH5Lu2PC9wPmAgKwogICAgc2VjKCLlsIbliJvlu7oiLHBsYW4uY3JlYXRlKSArIHNlYygi5bCG5pu05pawIixwbGFuLnVwZGF0ZSkgKwogICAgc2VjKCLlsIbliKDpmaQo5Yu+6YCJ5ZCO5omn6KGMKSIscGxhbi5kZWxldGUsKGksaWR4KT0+YDxsYWJlbD48aW5wdXQgdHlwZT0iY2hlY2tib3giIGNsYXNzPSJkZWwtY2hlY2siIGRhdGEta2V5PSIke2VzYyhpLmtleSl9Ij4g5Yig6ZmkPC9sYWJlbD5gKSArCiAgICBzZWMoIuWGsueqgSjpgJDmnaHpgInmi6kpIixwbGFuLmNvbmZsaWN0LChpLGlkeCk9PmA8c2VsZWN0IGNsYXNzPSJjb25mbGljdC1zZWwiIGRhdGEta2V5PSIke2VzYyhpLmtleSl9Ij48b3B0aW9uIHZhbHVlPSIiPui3s+i/hzwvb3B0aW9uPjxvcHRpb24gdmFsdWU9InJlcG8iPuS7peS7k+W6k+S4uuWHhjwvb3B0aW9uPjxvcHRpb24gdmFsdWU9ImNsdXN0ZXIiPuS7pembhue+pOS4uuWHhjwvb3B0aW9uPjwvc2VsZWN0PmApICsKICAgIHNlYygi5peg5Y+Y5YyWIixwbGFuLnVuY2hhbmdlZCkgKwogICAgKCghcGxhbi5jcmVhdGUubGVuZ3RoJiYhcGxhbi51cGRhdGUubGVuZ3RoJiYhcGxhbi5kZWxldGUubGVuZ3RoJiYhcGxhbi5jb25mbGljdC5sZW5ndGgpP2VtcHR5Qm94KCLkuIDliIfmraPluLgiLCLpm4bnvqTkuI7ku5PlupPlt7LkuIDoh7TjgIIiKToiIik7CiAgJCgiI3BsYW4yLWFwcGx5Iikub25jbGljayA9IGFzeW5jICgpID0+IHsKICAgIGNvbnN0IGRlbGV0ZXM9JCQoIi5kZWwtY2hlY2s6Y2hlY2tlZCIpLm1hcChjPT5jLmRhdGFzZXQua2V5KTsKICAgIGNvbnN0IGNvbmZsaWN0cz17fTsgJCQoIi5jb25mbGljdC1zZWwiKS5mb3JFYWNoKHM9PnsgaWYocy52YWx1ZSkgY29uZmxpY3RzW3MuZGF0YXNldC5rZXldPXMudmFsdWU7IH0pOwogICAgY29uc3QgdW5yZXNvbHZlZCA9IHBsYW4uY29uZmxpY3QuZmlsdGVyKGM9PiFjb25mbGljdHNbYy5rZXldKTsKICAgIGlmKHVucmVzb2x2ZWQubGVuZ3RoICYmICFjb25maXJtKGAke3VucmVzb2x2ZWQubGVuZ3RofSDkuKrlhrLnqoHmnKrlpITnkIYs57un57ut5bCG6Lez6L+H5a6D5Lus44CC57un57utP2ApKSByZXR1cm47CiAgICB0cnl7CiAgICAgIGNvbnN0IHIgPSBhd2FpdCBhcGkoIlBPU1QiLCIvYXBpL2dpdG9wcy9hcHBseSIse25hbWUsZGVsZXRlcyxjb25mbGljdHN9KTsKICAgICAgdG9hc3QoYOWujOaIkDrliJvlu7ogJHtyLmNyZWF0ZWR9IMK3IOabtOaWsCAke3IudXBkYXRlZH0gwrcg5Yig6ZmkICR7ci5kZWxldGVkfWAgKyAoci5lcnJvcnMubGVuZ3RoP2Agwrcg6ZSZ6K+vICR7ci5lcnJvcnMubGVuZ3RofWA6IiIpLCByLmVycm9ycy5sZW5ndGg+MCk7CiAgICAgIGlmKHIuZXJyb3JzLmxlbmd0aCkgbW9kYWwoYDxkaXYgY2xhc3M9Im1vZGFsLXBhZCI+PGgzPuWQjOatpemUmeivrzwvaDM+PGRpdiBjbGFzcz0ianNvbi12aWV3Ij4ke2VzYyhyLmVycm9ycy5qb2luKCJcbiIpKX08L2Rpdj48L2Rpdj5gKTsKICAgICAgZWxzZSBjbG9zZU1vZGFsKCk7CiAgICB9Y2F0Y2goZSl7IHRvYXN0KCLlkIzmraXlpLHotKU6ICIrZS5tZXNzYWdlLHRydWUpOyB9CiAgfTsKfQphc3luYyBmdW5jdGlvbiBleHBvcnRWMihuYW1lKXsKICBtb2RhbChgPGRpdiBjbGFzcz0ibW9kYWwtcGFkIiBzdHlsZT0id2lkdGg6bWluKDc2MHB4LDk0dncpIj48aDM+5YaZ5Zue5LuT5bqTIMK3ICR7ZXNjKG5hbWUpfTwvaDM+CiAgICA8cCBjbGFzcz0ic3VidGl0bGUiPuS7heWvvOWHuuW4puacieW9kuWxnuagh+etvueahOi1hOa6kCzmlofku7bluIPlsYDkuLrjgIznsbvlnosv5ZCN56ewLnlhbWzjgI08L3A+CiAgICA8ZGl2IGNsYXNzPSJmb3JtLWdyaWQiPjxsYWJlbCBjbGFzcz0iZnVsbCI+5YaZ5Zue5qih5byPPHNlbGVjdCBpZD0iZXhwLW1vZGUiPjxvcHRpb24gdmFsdWU9InByIj5QUijmjqjojZApPC9vcHRpb24+PG9wdGlvbiB2YWx1ZT0iZGlyZWN0Ij7nm7TmjqXmj5DkuqQ8L29wdGlvbj48L3NlbGVjdD48L2xhYmVsPjwvZGl2PgogICAgPGRpdiBpZD0iZXhwLXJlc3VsdCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PC9kaXY+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjEwcHg7anVzdGlmeS1jb250ZW50OmZsZXgtZW5kO21hcmdpbi10b3A6MTRweCI+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biIgaWQ9Im0tY2FuY2VsIj7lhbPpl608L2J1dHRvbj48YnV0dG9uIGNsYXNzPSJidG4gcHJpbWFyeSIgaWQ9ImV4cC1nbyI+5byA5aeL5YaZ5ZuePC9idXR0b24+PC9kaXY+PC9kaXY+YCk7CiAgJCgiI20tY2FuY2VsIikub25jbGljayA9IGNsb3NlTW9kYWw7CiAgJCgiI2V4cC1nbyIpLm9uY2xpY2sgPSBhc3luYyAoKSA9PiB7CiAgICBjb25zdCBidG49JCgiI2V4cC1nbyIpOyBidG4uZGlzYWJsZWQ9dHJ1ZTsgYnRuLnRleHRDb250ZW50PSLlhpnlm57kuK3igKYiOwogICAgdHJ5ewogICAgICBjb25zdCByID0gYXdhaXQgYXBpKCJQT1NUIiwiL2FwaS9naXRvcHMvZXhwb3J0Iix7bmFtZSxtb2RlOiQoIiNleHAtbW9kZSIpLnZhbHVlfSk7CiAgICAgIGlmKHIucHJfdXJsKSAkKCIjZXhwLXJlc3VsdCIpLmlubmVySFRNTCA9IGA8cD7lt7LliJvlu7ogUFI6PGEgaHJlZj0iJHtlc2Moci5wcl91cmwpfSIgdGFyZ2V0PSJfYmxhbmsiIHJlbD0ibm9vcGVuZXIiPiR7ZXNjKHIucHJfdXJsKX08L2E+PC9wPmA7CiAgICAgIGVsc2UgJCgiI2V4cC1yZXN1bHQiKS5pbm5lckhUTUwgPSBgPHA+5bey5o+Q5LqkICR7ZXNjKHIuY29tbWl0fHwiIil9IOWIsOWIhuaUryAke2VzYyhyLmJyYW5jaHx8IiIpfTwvcD5gICsgKHIucHJfZXJyb3I/YDxwIGNsYXNzPSJiYWRnZSBlcnIiPiR7ZXNjKHIucHJfZXJyb3IpfTwvcD5gOiIiKTsKICAgICAgdG9hc3QoYOWGmeWbnuWujOaIkDoke3IuY2hhbmdlZHx8MH0g5Liq5paH5Lu2YCk7CiAgICB9Y2F0Y2goZSl7ICQoIiNleHAtcmVzdWx0IikuaW5uZXJIVE1MID0gYDxkaXYgY2xhc3M9Impzb24tdmlldyI+JHtlc2MoZS5tZXNzYWdlKX08L2Rpdj5gOyB9CiAgICBidG4uZGlzYWJsZWQ9ZmFsc2U7IGJ0bi50ZXh0Q29udGVudD0i5byA5aeL5YaZ5ZueIjsKICB9Owp9CgoKCi8qIC0tLS0tLS0tLS0gY29tbWFuZCBwYWxldHRlIC0tLS0tLS0tLS0gKi8KY29uc3QgQ01EX0lURU1TID0gWwogIHtsYWJlbDoi5oC76KeIIiwgaGludDoi6aG16Z2iIiwgcnVuOigpPT5sb2NhdGlvbi5oYXNoPSIjL292ZXJ2aWV3In0sCiAge2xhYmVsOiJBUEkg5rWL6K+V5Y+wIiwgaGludDoi6aG16Z2iIiwgcnVuOigpPT5sb2NhdGlvbi5oYXNoPSIjL3Rlc3RlciJ9LAogIHtsYWJlbDoiRnVuY3Rpb25zIiwgaGludDoi6aG16Z2iIiwgcnVuOigpPT5sb2NhdGlvbi5oYXNoPSIjL2Z1bmN0aW9ucyJ9LAogIHtsYWJlbDoiUGFja2FnZXMiLCBoaW50OiLpobXpnaIiLCBydW46KCk9PmxvY2F0aW9uLmhhc2g9IiMvcGFja2FnZXMifSwKICB7bGFiZWw6IkVudmlyb25tZW50cyIsIGhpbnQ6IumhtemdoiIsIHJ1bjooKT0+bG9jYXRpb24uaGFzaD0iIy9lbnZpcm9ubWVudHMifSwKICB7bGFiZWw6IkhUVFAgVHJpZ2dlcnMiLCBoaW50OiLpobXpnaIiLCBydW46KCk9PmxvY2F0aW9uLmhhc2g9IiMvaHR0cHRyaWdnZXJzIn0sCiAge2xhYmVsOiJUaW1lciBUcmlnZ2VycyIsIGhpbnQ6IumhtemdoiIsIHJ1bjooKT0+bG9jYXRpb24uaGFzaD0iIy90aW1ldHJpZ2dlcnMifSwKICB7bGFiZWw6IkdpdE9wcyDlkIzmraUiLCBoaW50OiLpobXpnaIiLCBydW46KCk9PmxvY2F0aW9uLmhhc2g9IiMvZ2l0b3BzIn0sCiAge2xhYmVsOiLmlrDlu7ogRnVuY3Rpb24iLCBoaW50OiLmk43kvZwiLCBydW46KCk9PnsgbG9jYXRpb24uaGFzaD0iIy9mdW5jdGlvbnMiOyBzZXRUaW1lb3V0KCgpPT57Y29uc3QgYj0kKCIjYnRuLW5ldyIpOyBpZihiKSBiLmNsaWNrKCk7fSwyNTApOyB9fSwKICB7bGFiZWw6IumihOiniOS7k+W6k+W3ruW8giIsIGhpbnQ6IuaTjeS9nCIsIHJ1bjooKT0+eyBsb2NhdGlvbi5oYXNoPSIjL2dpdG9wcyI7IHNldFRpbWVvdXQoKCk9Pntjb25zdCBiPSQoIltkYXRhLXBsYW4yXSIpOyBpZihiKSBiLmNsaWNrKCk7fSwzMDApOyB9fSwKICB7bGFiZWw6IuWIt+aWsOW9k+WJjemhtSIsIGhpbnQ6IuaTjeS9nCIsIHJ1bjooKT0+bmF2aWdhdGUoKX0sCl07CmxldCBjbWRrSW5kZXggPSAwOwpmdW5jdGlvbiBvcGVuQ21kaygpewogIGNvbnN0IGQ9JCgiI2NtZGsiKTsgaWYoIWQpIHJldHVybjsgZC5zaG93TW9kYWwoKTsKICAkKCIjY21kay1pbnB1dCIpLnZhbHVlPSIiOyBjbWRrSW5kZXg9MDsgcmVuZGVyQ21kaygiIik7CiAgc2V0VGltZW91dCgoKT0+JCgiI2NtZGstaW5wdXQiKS5mb2N1cygpLDEwKTsKfQpmdW5jdGlvbiByZW5kZXJDbWRrKHEpewogIGNvbnN0IGl0ZW1zPUNNRF9JVEVNUy5maWx0ZXIoaT0+aS5sYWJlbC50b0xvd2VyQ2FzZSgpLmluY2x1ZGVzKHEudG9Mb3dlckNhc2UoKSkpOwogIGNtZGtJbmRleD1NYXRoLm1heCgwLE1hdGgubWluKGNtZGtJbmRleCxpdGVtcy5sZW5ndGgtMSkpOwogICQoIiNjbWRrLWxpc3QiKS5pbm5lckhUTUw9aXRlbXMubWFwKChpLG4pPT5gPGRpdiBjbGFzcz0iaXRlbSAke249PT1jbWRrSW5kZXg/ImFjdGl2ZSI6IiJ9IiBkYXRhLWNtZD0iJHtufSI+JHtlc2MoaS5sYWJlbCl9PHNwYW4gY2xhc3M9ImsiPiR7ZXNjKGkuaGludCl9PC9zcGFuPjwvZGl2PmApLmpvaW4oIiIpCiAgICB8fCBgPGRpdiBjbGFzcz0iaXRlbSI+5peg5Yy56YWN57uT5p6cPC9kaXY+YDsKICAkJCgiI2NtZGstbGlzdCAuaXRlbVtkYXRhLWNtZF0iKS5mb3JFYWNoKGVsPT5lbC5vbmNsaWNrPSgpPT57IGNvbnN0IGl0PWl0ZW1zWytlbC5kYXRhc2V0LmNtZF07ICQoIiNjbWRrIikuY2xvc2UoKTsgaXQucnVuKCk7IH0pOwp9CmZ1bmN0aW9uIGluaXRDbWRrKCl7CiAgY29uc3Qgb3Blbj0kKCIjY21kay1vcGVuIik7IGlmKG9wZW4pIG9wZW4ub25jbGljaz1vcGVuQ21kazsKICBjb25zdCBpbnB1dD0kKCIjY21kay1pbnB1dCIpOwogIGlmKGlucHV0KXsKICAgIGlucHV0Lm9uaW5wdXQ9KCk9PnJlbmRlckNtZGsoaW5wdXQudmFsdWUpOwogICAgaW5wdXQub25rZXlkb3duPWU9PnsKICAgICAgY29uc3QgaXRlbXM9Q01EX0lURU1TLmZpbHRlcihpPT5pLmxhYmVsLnRvTG93ZXJDYXNlKCkuaW5jbHVkZXMoaW5wdXQudmFsdWUudG9Mb3dlckNhc2UoKSkpOwogICAgICBpZihlLmtleT09PSJBcnJvd0Rvd24iKXsgZS5wcmV2ZW50RGVmYXVsdCgpOyBjbWRrSW5kZXg9TWF0aC5taW4oY21ka0luZGV4KzEsaXRlbXMubGVuZ3RoLTEpOyByZW5kZXJDbWRrKGlucHV0LnZhbHVlKTsgfQogICAgICBpZihlLmtleT09PSJBcnJvd1VwIil7IGUucHJldmVudERlZmF1bHQoKTsgY21ka0luZGV4PU1hdGgubWF4KGNtZGtJbmRleC0xLDApOyByZW5kZXJDbWRrKGlucHV0LnZhbHVlKTsgfQogICAgICBpZihlLmtleT09PSJFbnRlciImJml0ZW1zW2NtZGtJbmRleF0peyAkKCIjY21kayIpLmNsb3NlKCk7IGl0ZW1zW2NtZGtJbmRleF0ucnVuKCk7IH0KICAgIH07CiAgfQogIGRvY3VtZW50LmFkZEV2ZW50TGlzdGVuZXIoImtleWRvd24iLGU9PnsKICAgIGlmKChlLmN0cmxLZXl8fGUubWV0YUtleSkmJmUua2V5LnRvTG93ZXJDYXNlKCk9PT0iayIpeyBlLnByZXZlbnREZWZhdWx0KCk7IG9wZW5DbWRrKCk7IH0KICB9KTsKfQppbml0Q21kaygpOwoKCm5hdmlnYXRlKCk7CgovKiAtLS0tLS0tLS0tIG9ic2VydmFiaWxpdHkgY2hhcnRzICh2YW5pbGxhIFNWRykgLS0tLS0tLS0tLSAqLwpsZXQgb2JzVGltZXIgPSBudWxsOwphc3luYyBmdW5jdGlvbiByZW5kZXJPYnNlcnZhYmlsaXR5KCl7CiAgY29uc3QgZWwgPSAkKCIjb2JzIik7IGlmICghZWwpIHJldHVybjsKICBjb25zdCBkcmF3ID0gYXN5bmMgKCkgPT4gewogICAgbGV0IG07IHRyeSB7IG0gPSBhd2FpdCBhcGkoIkdFVCIsICIvYXBpL21ldHJpY3MiKTsgfSBjYXRjaChlKXsgZWwuaW5uZXJIVE1MID0gZW1wdHlCb3goIuingua1i+aVsOaNruWKoOi9veWksei0pSIsIGUubWVzc2FnZSk7IHJldHVybjsgfQogICAgaWYgKG0ubGFzdF9lcnJvcikgeyBlbC5pbm5lckhUTUwgPSBlbXB0eUJveCgi6YeH5qC35Zmo5pqC5LiN5Y+v55SoIiwgbS5sYXN0X2Vycm9yKTsgcmV0dXJuOyB9CiAgICBjb25zdCBzZXJpZXMgPSBtLnNlcmllcyB8fCBbXTsKICAgIGNvbnN0IGZucyA9IHt9OwogICAgc2VyaWVzLmZvckVhY2gocyA9PiBPYmplY3QuZW50cmllcyhzLmZuc3x8e30pLmZvckVhY2goKFtmLHZdKSA9PiB7IChmbnNbZl0gPSBmbnNbZl0gfHwge2NwdTpbXSxtZW06W10scG9kczpbXX0pLmNwdS5wdXNoKFtzLnRzLHYuY3B1X21dKTsgZm5zW2ZdLm1lbS5wdXNoKFtzLnRzLHYubWVtX21pXSk7IGZuc1tmXS5wb2RzLnB1c2goW3MudHMsdi5wb2RzXSk7IH0pKTsKICAgIHNlcmllcy5mb3JFYWNoKHMgPT4gT2JqZWN0LmtleXMoZm5zKS5mb3JFYWNoKGYgPT4geyBpZiAoIShzLmZuc3x8e30pW2ZdKSB7IGZuc1tmXS5jcHUucHVzaChbcy50cywwXSk7IGZuc1tmXS5tZW0ucHVzaChbcy50cywwXSk7IGZuc1tmXS5wb2RzLnB1c2goW3MudHMsMF0pOyB9IH0pKTsKICAgIGNvbnN0IG5hbWVzID0gT2JqZWN0LmtleXMoZm5zKTsKICAgIGlmICghbmFtZXMubGVuZ3RoKXsgZWwuaW5uZXJIVE1MID0gZW1wdHlCb3goIuaaguaXoOWHveaVsOi/kOihjOaVsOaNriIsIuWHveaVsOiiq+iwg+eUqOW5tuS6p+eUnyBQb2Qg5ZCOLOi/memHjOS8muWHuueOsCBDUFUgLyDlhoXlrZggLyDlia/mnKzmlbDml7bpl7Tluo/liJfjgIIiKTsgcmV0dXJuOyB9CiAgICBjb25zdCBjcyA9IG0uY29sZF9zdGFydHN8fHt9OwogICAgZWwuaW5uZXJIVE1MID0gYDxkaXYgY2xhc3M9ImNhcmRzIiBzdHlsZT0ibWFyZ2luLWJvdHRvbToxMnB4Ij4ke25hbWVzLm1hcChmPT5gCiAgICAgIDxkaXYgY2xhc3M9InN0YXQiPjxkaXYgY2xhc3M9Im51bSI+JHtjc1tmXXx8MH08L2Rpdj48ZGl2IGNsYXNzPSJsYWJlbCI+JHtlc2MoZil9IOWGt+WQr+WKqOasoeaVsDwvZGl2PjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PgogICAgICAke2NoYXJ0QmxvY2soIkNQVSAo5q+r5qC4KSIsIG5hbWVzLCBmbnMsICJjcHUiKX0KICAgICAgJHtjaGFydEJsb2NrKCLlhoXlrZggKE1pQikiLCBuYW1lcywgZm5zLCAibWVtIil9CiAgICAgICR7Y2hhcnRCbG9jaygi5Ymv5pys5pWwIiwgbmFtZXMsIGZucywgInBvZHMiKX1gOwogIH07CiAgYXdhaXQgZHJhdygpOwogIGlmIChvYnNUaW1lcikgY2xlYXJJbnRlcnZhbChvYnNUaW1lcik7CiAgb2JzVGltZXIgPSBzZXRJbnRlcnZhbCgoKSA9PiB7IGlmIChsb2NhdGlvbi5oYXNoLmluY2x1ZGVzKCJvdmVydmlldyIpKSBkcmF3KCk7IGVsc2UgY2xlYXJJbnRlcnZhbChvYnNUaW1lcik7IH0sIDE1MDAwKTsKfQpjb25zdCBDSEFSVF9DT0xPUlMgPSBbIiMwYjBiMGIiLCIjZDIyYjJiIiwiIzBlN2EzYyIsIiMxNDVhODYiLCIjOGE2ZDAwIiwiI2Q5YWYwMCJdOwpmdW5jdGlvbiBjaGFydEJsb2NrKHRpdGxlLCBuYW1lcywgZm5zLCBrZXkpewogIGNvbnN0IFc9OTgwLEg9MjAwLFA9NDQ7CiAgbGV0IHB0cz17fSwgbWluPTAsIG1heD0xOwogIG5hbWVzLmZvckVhY2goKG4pPT57IHB0c1tuXT1mbnNbbl1ba2V5XS5tYXAoKFt0LHZdKT0+W3Qsdl0pOyBtYXg9TWF0aC5tYXgobWF4LC4uLmZuc1tuXVtrZXldLm1hcCh4PT54WzFdKSk7IH0pOwogIGlmIChrZXk9PT0icG9kcyIpIG1heD1NYXRoLm1heCgxLG1heCk7CiAgbWF4ID0gTWF0aC5jZWlsKG1heCoxLjIqMTApLzEwOwogIGNvbnN0IHQwPU1hdGgubWluKC4uLm5hbWVzLm1hcChuPT5wdHNbbl1bMF0/LlswXT8/SW5maW5pdHkpKSwgdDE9TWF0aC5tYXgoLi4ubmFtZXMubWFwKG49PnB0c1tuXVtwdHNbbl0ubGVuZ3RoLTFdPy5bMF0/PzApKTsKICBpZiAoIWlzRmluaXRlKHQwKXx8dDE8PXQwKXsgcmV0dXJuIGA8aDI+JHtlc2ModGl0bGUpfTwvaDI+YCtlbXB0eUJveCgi5pWw5o2u56ev57Sv5LitIiwi5q+P5Liq6YeH5qC35ZGo5pyfKDE1cynlop7liqDkuIDkuKrngrks56iN5ZCO5YaN55yL44CCIik7IH0KICBjb25zdCBYPXQ9PlArKHQtdDApLyh0MS10MCkqKFctUC0xMCksIFk9dj0+SC0yNC0odi1taW4pLyhtYXgtbWlufHwxKSooSC00MCk7CiAgY29uc3QgcGF0aHMgPSBuYW1lcy5tYXAoKG4saSk9PnsKICAgIGNvbnN0IGQgPSBwdHNbbl0ubWFwKChbdCx2XSxqKT0+KGo/IkwiOiJNIikrWCh0KS50b0ZpeGVkKDEpKyIgIitZKHYpLnRvRml4ZWQoMSkpLmpvaW4oIiAiKTsKICAgIGNvbnN0IGNvbCA9IENIQVJUX0NPTE9SU1tpJUNIQVJUX0NPTE9SUy5sZW5ndGhdOwogICAgcmV0dXJuIGA8cGF0aCBkPSIke2R9IiBmaWxsPSJub25lIiBzdHJva2U9IiR7Y29sfSIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+CiAgICAgICR7cHRzW25dLm1hcCgoW3Qsdl0pPT5gPGNpcmNsZSBjeD0iJHtYKHQpLnRvRml4ZWQoMSl9IiBjeT0iJHtZKHYpLnRvRml4ZWQoMSl9IiByPSIyLjUiIGZpbGw9IiR7Y29sfSI+PHRpdGxlPiR7ZXNjKG4pfSAke25ldyBEYXRlKHQqMTAwMCkudG9Mb2NhbGVUaW1lU3RyaW5nKCl9IMK3ICR7dn08L3RpdGxlPjwvY2lyY2xlPmApLmpvaW4oIiIpfWA7CiAgfSkuam9pbigiIik7CiAgY29uc3QgZ3JpZD1bMCwuNSwxXS5tYXAoZj0+e2NvbnN0IGd5PShILTI0KS1mKihILTQwKTtjb25zdCBndj0obWF4LW1pbikqZittaW47CiAgICByZXR1cm4gYDxsaW5lIHgxPSIke1B9IiB5MT0iJHtneX0iIHgyPSIke1ctMTB9IiB5Mj0iJHtneX0iIHN0cm9rZT0iI2Q0ZDBjNCIvPjx0ZXh0IHg9IiR7UC02fSIgeT0iJHtneSs0fSIgZm9udC1zaXplPSIxMCIgZmlsbD0iIzY2NjY2NiIgdGV4dC1hbmNob3I9ImVuZCI+JHtndiUxP2d2LnRvRml4ZWQoMSk6Z3Z9PC90ZXh0PmA7fSkuam9pbigiIik7CiAgY29uc3QgbGVnZW5kID0gbmFtZXMubWFwKChuLGkpPT5gPHNwYW4gc3R5bGU9ImRpc3BsYXk6aW5saW5lLWZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo1cHg7Zm9udDoxMXB4IHZhcigtLW1vbm8pO2NvbG9yOnZhcigtLWluay0yKTttYXJnaW4tcmlnaHQ6MTJweCI+PGkgc3R5bGU9IndpZHRoOjE0cHg7aGVpZ2h0OjJweDtiYWNrZ3JvdW5kOiR7Q0hBUlRfQ09MT1JTW2klQ0hBUlRfQ09MT1JTLmxlbmd0aF19Ij48L2k+JHtlc2Mobil9PC9zcGFuPmApLmpvaW4oIiIpOwogIHJldHVybiBgPGRpdiBjbGFzcz0iY2hhcnQtaGVhZCI+PGI+JHtlc2ModGl0bGUpfTwvYj48c3Bhbj4ke2xlZ2VuZH08L3NwYW4+PC9kaXY+PGRpdiBzdHlsZT0icGFkZGluZzo4cHggMCAwO2JvcmRlci10b3A6MXB4IHNvbGlkIHZhcigtLXJ1bGUpIj4KICAgIDxzdmcgdmlld0JveD0iMCAwICR7V30gJHtIfSIgc3R5bGU9IndpZHRoOjEwMCU7aGVpZ2h0OmF1dG8iPiR7Z3JpZH0ke3BhdGhzfTwvc3ZnPgogICAgPGRpdiBzdHlsZT0iZm9udC1zaXplOjExcHg7Y29sb3I6dmFyKC0tdGV4dC0zKTtwYWRkaW5nOjJweCA2cHggNnB4Ij7ml7bpl7Tnqpflj6M6JHtuZXcgRGF0ZSh0MCoxMDAwKS50b0xvY2FsZVRpbWVTdHJpbmcoKX0g4oaSICR7bmV3IERhdGUodDEqMTAwMCkudG9Mb2NhbGVUaW1lU3RyaW5nKCl9KDE1cyDph4fmoLcpPC9kaXY+PC9kaXY+YDsKfQoKLyogLS0tLS0tLS0tLSBnaXRvcHMgdmlldyAtLS0tLS0tLS0tICovCmFzeW5jIGZ1bmN0aW9uIHZpZXdHaXRvcHMoKXsKICBjb25zdCBtYWluID0gJCgiI21haW4iKTsKICBtYWluLmlubmVySFRNTCA9IGA8aDE+R2l0T3BzIOWQjOatpTwvaDE+PHAgY2xhc3M9InN1YnRpdGxlIj7ku44gR2l0SHViIOS7k+W6k+aLieWPliBGaXNzaW9uIOa4heWNlSzoh6rliqjlkIzmraXlh73mlbAgLyDnjq/looMgLyDop6blj5Hlmag8L3A+CiAgICA8ZGl2IGNsYXNzPSJ0b29sYmFyIj48YnV0dG9uIGNsYXNzPSJidG4gcHJpbWFyeSIgaWQ9ImJ0bi1hZGQtc3JjIj7mt7vliqDku5PlupPmupA8L2J1dHRvbj48L2Rpdj48ZGl2IGlkPSJzcmMtbGlzdCI+JHtza2VsZXRvbnMoKX08L2Rpdj48ZGl2IGlkPSJnaXRvcHMtc3VtbWFyeSI+PC9kaXY+PC9kaXY+YDsKICBjb25zdCBsb2FkID0gYXN5bmMgKCkgPT4gewogICAgY29uc3QgY2ZnID0gYXdhaXQgYXBpKCJHRVQiLCAiL2FwaS9jb25maWciKTsKICAgIGlmICghY2ZnLnNvdXJjZXMubGVuZ3RoKXsgJCgiI3NyYy1saXN0IikuaW5uZXJIVE1MID0gZW1wdHlCb3goIui/mOayoeacieS7k+W6k+a6kCIsIueCueWHu+OAjOa3u+WKoOS7k+W6k+a6kOOAjei/nuaOpeS4gOS4quWMheWQqyBGaXNzaW9uIFlBTUwg55qEIEdpdEh1YiDku5PlupPjgIIiKTsgfQogICAgZWxzZSB7CiAgICAgICQoIiNzcmMtbGlzdCIpLmlubmVySFRNTCA9IGA8ZGl2IGNsYXNzPSJ0YWJsZS13cmFwIj48dGFibGU+PHRoZWFkPjx0cj48dGg+5ZCN56ewPC90aD48dGg+5LuT5bqTPC90aD48dGg+57G75Z6LPC90aD48dGg+6Ieq5Yqo5ZCM5q2lPC90aD48dGg+5pON5L2cPC90aD48L3RyPjwvdGhlYWQ+PHRib2R5PgogICAgICAgICR7Y2ZnLnNvdXJjZXMubWFwKHM9PmA8dHI+CiAgICAgICAgICA8dGQ+PHNwYW4gY2xhc3M9Im5hbWUiPiR7ZXNjKHMubmFtZSl9PC9zcGFuPjwvdGQ+CiAgICAgICAgICA8dGQgY2xhc3M9Im1vbm8iPiR7ZXNjKHMucmVwbyl9PGRpdj4ke2VzYyhzLmJyYW5jaHx8Im1haW4iKX0ke3MucGF0aD8iIMK3ICIrZXNjKHMucGF0aCk6IiJ9PC9kaXY+PC90ZD4KICAgICAgICAgIDx0ZD4ke3MudG9rZW4/YmFkZ2UoIndhcm4iLCLnp4HmnIkiKSA6IGJhZGdlKCJpZGxlIiwi5YWs5byAIil9PC90ZD4KICAgICAgICAgIDx0ZD4ke3MuYXV0b01pbnV0ZXM/YmFkZ2UoIm9rIiwi5q+PICIrcy5hdXRvTWludXRlcysiIOWIhumSnyIpIDogYmFkZ2UoImlkbGUiLCLmiYvliqgiKX08L3RkPgogICAgICAgICAgPHRkPjxidXR0b24gY2xhc3M9ImJ0biBzbWFsbCIgZGF0YS1wbGFuPSIke2VzYyhzLm5hbWUpfSI+6aKE6KeI5beu5byCPC9idXR0b24+CiAgICAgICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtYWxsIHByaW1hcnkiIGRhdGEtc3luYz0iJHtlc2Mocy5uYW1lKX0iPueri+WNs+WQjOatpTwvYnV0dG9uPgogICAgICAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbWFsbCIgZGF0YS1lZGl0LXNyYz0iJHtlc2Mocy5uYW1lKX0iPue8lui+kTwvYnV0dG9uPgogICAgICAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbWFsbCBkYW5nZXIiIGRhdGEtZGVsLXNyYz0iJHtlc2Mocy5uYW1lKX0iPuWIoOmZpDwvYnV0dG9uPjwvdGQ+PC90cj5gKS5qb2luKCIiKX0KICAgICAgICA8L3Rib2R5PjwvdGFibGU+PC9kaXY+YDsKICAgIH0KICAgICQoIiNidG4tYWRkLXNyYyIpLm9uY2xpY2sgPSAoKSA9PiBlZGl0U291cmNlKG51bGwpOwogICAgJCQoIiNzcmMtbGlzdCBbZGF0YS1lZGl0LXNyY10iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ZWRpdFNvdXJjZShiLmRhdGFzZXQuZWRpdFNyYywgY2ZnLnNvdXJjZXMuZmluZChzPT5zLm5hbWU9PT1iLmRhdGFzZXQuZWRpdFNyYykpKTsKICAgICQkKCIjc3JjLWxpc3QgW2RhdGEtZGVsLXNyY10iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICAgIGlmKCFjb25maXJtKGDliKDpmaTku5PlupPmupAgJHtiLmRhdGFzZXQuZGVsU3JjfT9gKSkgcmV0dXJuOwogICAgICBjZmcuc291cmNlcyA9IGNmZy5zb3VyY2VzLmZpbHRlcihzPT5zLm5hbWUhPT1iLmRhdGFzZXQuZGVsU3JjKTsKICAgICAgYXdhaXQgYXBpKCJQVVQiLCIvYXBpL2NvbmZpZyIsY2ZnKTsgdG9hc3QoIuW3suWIoOmZpCIpOyBsb2FkKCk7CiAgICB9KTsKICAgICQkKCIjc3JjLWxpc3QgW2RhdGEtcGxhbl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+cnVuUGxhbihiLmRhdGFzZXQucGxhbikpOwogICAgJCQoIiNzcmMtbGlzdCBbZGF0YS1zeW5jXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT5ydW5TeW5jKGIuZGF0YXNldC5zeW5jKSk7CiAgfTsKICBhd2FpdCBsb2FkKCk7Cn0KZnVuY3Rpb24gZWRpdFNvdXJjZShuYW1lLCBleGlzdGluZyl7CiAgY29uc3QgcyA9IGV4aXN0aW5nIHx8IHtuYW1lOiIiLCByZXBvOiIiLCBicmFuY2g6Im1haW4iLCBwYXRoOiIiLCB0b2tlbjoiIiwgYXV0b01pbnV0ZXM6MH07CiAgbW9kYWwoYDxkaXYgY2xhc3M9Im1vZGFsLXBhZCI+PGgzPiR7bmFtZT8i57yW6L6RIjoi5re75YqgIn3ku5PlupPmupA8L2gzPgogICAgPGRpdiBjbGFzcz0iZm9ybS1ncmlkIj4KICAgICAgPGxhYmVsPuWQjeensDxpbnB1dCBpZD0ic3JjLW5hbWUiIHZhbHVlPSIke2VzYyhzLm5hbWUpfSI+PC9sYWJlbD4KICAgICAgPGxhYmVsPuS7k+W6kyhvd25lci9uYW1lKTxpbnB1dCBpZD0ic3JjLXJlcG8iIHZhbHVlPSIke2VzYyhzLnJlcG8pfSIgcGxhY2Vob2xkZXI9Im9jdG9jYXQvaGVsbG8td29ybGQiPjwvbGFiZWw+CiAgICAgIDxsYWJlbD7liIbmlK88aW5wdXQgaWQ9InNyYy1icmFuY2giIHZhbHVlPSIke2VzYyhzLmJyYW5jaHx8Im1haW4iKX0iPjwvbGFiZWw+CiAgICAgIDxsYWJlbD7lrZDnm67lvZUo5Y+v56m6KTxpbnB1dCBpZD0ic3JjLXBhdGgiIHZhbHVlPSIke2VzYyhzLnBhdGh8fCIiKX0iPjwvbGFiZWw+CiAgICAgIDxsYWJlbCBjbGFzcz0iZnVsbCI+VG9rZW4o56eB5pyJ5LuT5bqT5b+F5aGrLOWFrOW8gOeVmeepuik8aW5wdXQgaWQ9InNyYy10b2tlbiIgdmFsdWU9IiR7ZXNjKHMudG9rZW58fCIiKX0iPjwvbGFiZWw+CiAgICAgIDxsYWJlbD7oh6rliqjlkIzmraUo5YiG6ZKfLDA95YWz6ZetKTxpbnB1dCBpZD0ic3JjLWF1dG8iIHR5cGU9Im51bWJlciIgdmFsdWU9IiR7cy5hdXRvTWludXRlc3x8MH0iPjwvbGFiZWw+CiAgICA8L2Rpdj4KICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6MTBweDtqdXN0aWZ5LWNvbnRlbnQ6ZmxleC1lbmQ7bWFyZ2luLXRvcDoxNnB4Ij4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0ibS1jYW5jZWwiPuWPlua2iDwvYnV0dG9uPjxidXR0b24gY2xhc3M9ImJ0biBwcmltYXJ5IiBpZD0ibS1zYXZlIj7kv53lrZg8L2J1dHRvbj48L2Rpdj48L2Rpdj5gKTsKICAkKCIjbS1jYW5jZWwiKS5vbmNsaWNrID0gY2xvc2VNb2RhbDsKICAkKCIjbS1zYXZlIikub25jbGljayA9IGFzeW5jICgpID0+IHsKICAgIGNvbnN0IHNyYyA9IHtuYW1lOiAkKCIjc3JjLW5hbWUiKS52YWx1ZS50cmltKCksIHJlcG86ICQoIiNzcmMtcmVwbyIpLnZhbHVlLnRyaW0oKSwKICAgICAgYnJhbmNoOiAkKCIjc3JjLWJyYW5jaCIpLnZhbHVlLnRyaW0oKSB8fCAibWFpbiIsIHBhdGg6ICQoIiNzcmMtcGF0aCIpLnZhbHVlLnRyaW0oKSwKICAgICAgdG9rZW46ICQoIiNzcmMtdG9rZW4iKS52YWx1ZS50cmltKCksIGF1dG9NaW51dGVzOiBwYXJzZUludCgkKCIjc3JjLWF1dG8iKS52YWx1ZXx8IjAiKX07CiAgICBpZiAoIXNyYy5uYW1lIHx8ICFzcmMucmVwbyl7IHRvYXN0KCLlkI3np7Dlkozku5PlupPlv4XloasiLCB0cnVlKTsgcmV0dXJuOyB9CiAgICBjb25zdCBjZmcgPSBhd2FpdCBhcGkoIkdFVCIsIi9hcGkvY29uZmlnIik7CiAgICBjZmcuc291cmNlcyA9IChjZmcuc291cmNlc3x8W10pLmZpbHRlcih4PT54Lm5hbWUhPT1uYW1lKTsKICAgIGNmZy5zb3VyY2VzLnB1c2goc3JjKTsKICAgIHRyeSB7IGF3YWl0IGFwaSgiUFVUIiwiL2FwaS9jb25maWciLGNmZyk7IHRvYXN0KCLlt7Lkv53lrZgiKTsgY2xvc2VNb2RhbCgpOyBuYXZpZ2F0ZSgpOyB9CiAgICBjYXRjaChlKXsgdG9hc3QoIuS/neWtmOWksei0pTogIitlLm1lc3NhZ2UsIHRydWUpOyB9CiAgfTsKfQphc3luYyBmdW5jdGlvbiBydW5QbGFuKG5hbWUpewogIG1vZGFsKGA8ZGl2IGNsYXNzPSJtb2RhbC1wYWQiPjxoMz7lt67lvILpooTop4ggwrcgJHtlc2MobmFtZSl9PC9oMz48ZGl2IGlkPSJwbGFuLWJvZHkiPiR7c2tlbGV0b25zKDQpfTwvZGl2PgogICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDoxMHB4O2p1c3RpZnktY29udGVudDpmbGV4LWVuZDttYXJnaW4tdG9wOjE0cHgiPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0ibS1jYW5jZWwiPuWFs+mXrTwvYnV0dG9uPjwvZGl2PjwvZGl2PmApOwogICQoIiNtLWNhbmNlbCIpLm9uY2xpY2sgPSBjbG9zZU1vZGFsOwogIHRyeSB7CiAgICBjb25zdCBwbGFuID0gYXdhaXQgYXBpKCJQT1NUIiwgYC9hcGkvZ2l0b3BzL3BsYW4vJHtuYW1lfWApOwogICAgY29uc3Qgc2VjID0gKHQsIGFycikgPT4gYXJyLmxlbmd0aCA/IGA8aDI+JHt0fSgke2Fyci5sZW5ndGh9KTwvaDI+PGRpdiBjbGFzcz0idGFibGUtd3JhcCI+PHRhYmxlPjx0aGVhZD48dHI+PHRoPuaWh+S7tjwvdGg+PHRoPktpbmQ8L3RoPjx0aD7otYTmupA8L3RoPjwvdHI+PC90aGVhZD48dGJvZHk+CiAgICAgICR7YXJyLm1hcChpPT5gPHRyPjx0ZD4ke2VzYyhpLnBhdGh8fCIiKX08L3RkPjx0ZD4ke2VzYyhpLmtpbmR8fCIiKX08L3RkPjx0ZCBjbGFzcz0ibW9ubyI+JHtlc2MoaS5uc3x8IiIpfS8ke2VzYyhpLm5hbWV8fCIiKX08L3RkPjwvdHI+YCkuam9pbigiIil9PC90Ym9keT48L3RhYmxlPjwvZGl2PmAgOiAiIjsKICAgICQoIiNwbGFuLWJvZHkiKS5pbm5lckhUTUwgPQogICAgICBgPHAgY2xhc3M9InN1YnRpdGxlIj7ku5PlupMgJHtlc2MocGxhbi5yZXBvKX1AJHtlc2MocGxhbi5icmFuY2gpfSDCtyAke3BsYW4uZmlsZXN9IOS4quaWh+S7tjwvcD5gICsKICAgICAgc2VjKCLlsIbliJvlu7oiLCBwbGFuLmNyZWF0ZSkgKyBzZWMoIuWwhuabtOaWsCIsIHBsYW4udXBkYXRlKSArIHNlYygi5peg5Y+Y5YyWIiwgcGxhbi51bmNoYW5nZWQpICsKICAgICAgKChwbGFuLmVycm9yc3x8W10pLmxlbmd0aCA/IGA8aDI+6Kej5p6Q6ZSZ6K+vPC9oMj48ZGl2IGNsYXNzPSJqc29uLXZpZXciPiR7ZXNjKHBsYW4uZXJyb3JzLm1hcChlPT5lLnBhdGgrIjogIitlLmVycm9yKS5qb2luKCJcbiIpKX08L2Rpdj5gIDogIiIpICsKICAgICAgKCghcGxhbi5jcmVhdGUubGVuZ3RoICYmICFwbGFuLnVwZGF0ZS5sZW5ndGgpID8gZW1wdHlCb3goIuS4gOWIh+ato+W4uCIsIumbhue+pOS4juS7k+W6k+W3suS4gOiHtOOAgiIpIDogIiIpOwogIH0gY2F0Y2goZSl7ICQoIiNwbGFuLWJvZHkiKS5pbm5lckhUTUwgPSBlbXB0eUJveCgi6aKE6KeI5aSx6LSlIiwgZS5tZXNzYWdlKTsgfQp9CmFzeW5jIGZ1bmN0aW9uIHJ1blN5bmMobmFtZSl7CiAgaWYgKCFjb25maXJtKGDnq4vljbPku44gJHtuYW1lfSDlkIzmraXliLDpm4bnvqQ/YCkpIHJldHVybjsKICB0b2FzdCgi5ZCM5q2l5Lit4oCmIik7CiAgdHJ5IHsKICAgIGNvbnN0IHIgPSBhd2FpdCBhcGkoIlBPU1QiLCBgL2FwaS9naXRvcHMvc3luYy8ke25hbWV9YCk7CiAgICB0b2FzdChg5ZCM5q2l5a6M5oiQOuaWsOW7uiAke3IuY3JlYXRlZH0s5pu05pawICR7ci51cGRhdGVkfWAgKyAoci5lcnJvcnMubGVuZ3RoP2AsJHtyLmVycm9ycy5sZW5ndGh9IOS4qumUmeivr2A6IiIpLCByLmVycm9ycy5sZW5ndGg+MCk7CiAgICBpZiAoci5lcnJvcnMubGVuZ3RoKSBtb2RhbChgPGRpdiBjbGFzcz0ibW9kYWwtcGFkIj48aDM+5ZCM5q2l6ZSZ6K+vPC9oMz48ZGl2IGNsYXNzPSJqc29uLXZpZXciPiR7ZXNjKHIuZXJyb3JzLmpvaW4oIlxuIikpfTwvZGl2PjwvZGl2PmApOwogIH0gY2F0Y2goZSl7IHRvYXN0KCLlkIzmraXlpLHotKU6ICIrZS5tZXNzYWdlLCB0cnVlKTsgfQp9Cgo8L3NjcmlwdD4KPC9ib2R5Pgo8L2h0bWw+Cg==")

def main():
    start_sampler()
    start_auto()
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return Response(INDEX_HTML, 200, content_type="text/html; charset=utf-8")
    try:
        env = json.loads(request.get_data() or b"{}")
    except Exception:
        return Response(b'{"message":"invalid envelope"}', 400, content_type="application/json")
    op = env.pop("op", "")
    st, ct, payload = handle_op(op, env)
    return Response(payload, st, content_type=ct)
