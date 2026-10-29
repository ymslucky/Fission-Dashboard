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


class Handler(BaseHTTPRequestHandler):
    server_version = "FissionConsole/1.0"

    def log_message(self, fmt, *args):
        print("[http]", self.address_string(), fmt % args, flush=True)

    # -- helpers -------------------------------------------------------
    def _json(self, code, obj):
        payload = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _static(self, rel):
        if rel == "/":
            rel = "/index.html"
        path = os.path.normpath(os.path.join(STATIC_DIR, rel.lstrip("/")))
        if not path.startswith(STATIC_DIR) or not os.path.isfile(path):
            self._json(404, {"message": "not found"})
            return
        ctype = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8",
                 "css": "text/css; charset=utf-8", "svg": "image/svg+xml"}.get(
                     path.rsplit(".", 1)[-1], "application/octet-stream")
        with open(path, "rb") as fh:
            payload = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(payload)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            raise ApiError(400, {"message": "invalid JSON body"})

    def _kind(self, kind):
        if kind not in KINDS:
            raise ApiError(404, {"message": f"unknown kind {kind}"})
        return kind

    # -- routes --------------------------------------------------------
    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            qs = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/healthz":
                return self._json(200, {"ok": True})
            if not parts or parts[0] != "api":
                return self._static(parsed.path)
            if parts[1] == "overview" and len(parts) == 2:
                return self._json(200, overview())
            if parts[1] == "invoke":
                raise ApiError(405, {"message": "use POST"})
            kind = self._kind(parts[1])
            if len(parts) == 2:
                return self._json(200, {"items": crd_list(kind)})
            if parts[1] == "functions" and len(parts) == 5 and parts[4] == "logs":
                return self._json(200, function_logs(parts[2], parts[3], (qs.get("pod") or [None])[0]))
            if len(parts) == 4:
                return self._json(200, crd_get(kind, parts[2], parts[3]))
            raise ApiError(404, {"message": "not found"})
        except ApiError as e:
            self._json(e.code, e.body)
        except Exception as e:
            self._json(500, {"message": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            if parts[:2] == ["api", "invoke"]:
                b = self._body()
                return self._json(200, invoke(b["ns"], b["trigger"], b.get("method"),
                                              b.get("headers"), b.get("body")))
            kind = self._kind(parts[1])
            obj = self._body()
            return self._json(201, crd_create(kind, obj))
        except ApiError as e:
            self._json(e.code, e.body)
        except Exception as e:
            self._json(500, {"message": f"{type(e).__name__}: {e}"})

    def do_PUT(self):
        try:
            parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p]
            kind = self._kind(parts[1])
            obj = self._body()
            ns = obj.get("metadata", {}).get("namespace", parts[2])
            return self._json(200, crd_update(kind, parts[2], parts[3], obj))
        except ApiError as e:
            self._json(e.code, e.body)
        except Exception as e:
            self._json(500, {"message": f"{type(e).__name__}: {e}"})

    def do_DELETE(self):
        try:
            parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p]
            kind = self._kind(parts[1])
            return self._json(200, crd_delete(kind, parts[2], parts[3]))
        except ApiError as e:
            self._json(e.code, e.body)
        except Exception as e:
            self._json(500, {"message": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"fission-console listening on :{port}", flush=True)
    server.serve_forever()
