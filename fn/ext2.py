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
