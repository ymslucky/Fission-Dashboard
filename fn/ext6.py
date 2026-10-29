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
