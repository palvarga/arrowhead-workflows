#!/usr/bin/env python3
"""Room for the Arrowhead Workflow temperature-control example (paper Sec. 7), Arrowhead 4.6.2 executor model.

One process hosts
  * the Application Systems: temperaturesensor (read-temperature), temperaturecontroller
    (evaluate-temperature), heatingswitch (switch-heating)
  * sessionctx: provider of the registered service `session-context` (rho and xi per sessionId)
  * one Choreographer Executor per service definition; each declares `session-context` as a
    dependency, so the Choreographer orchestrates it and hands it over in preconditionOrchestrationResults
  * the notify endpoint of controlclock, the system that starts one Session per control period
  * a simple room model: heating ON warms the room, OFF lets it cool

Stdlib only:  python3 plant.py --chor http://<choreographer-host>:8457
Every event is appended to trace.jsonl.
"""
import argparse, json, random, threading, time, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

LOCK = threading.Lock()
TRACE_PATH = "trace.jsonl"
IFACE = "HTTP-INSECURE-JSON"

def trace(**ev):
    ev["t"] = round(time.time(), 3)
    with LOCK, open(TRACE_PATH, "a") as f:
        f.write(json.dumps(ev) + "\n")
    print(json.dumps(ev), flush=True)

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

class CallError(Exception):
    def __init__(self, code, body): super().__init__(f"HTTP {code}: {body}"); self.code = code

def call(method, url, body=None, timeout=None):
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            raw = r.read(); return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise CallError(e.code, e.read().decode(errors="replace")[:300]) from None

def post(url, body, timeout=None): return call("POST", url, body, timeout)

PROVIDERS = {   # system -> (port, [service definitions])
    "temperaturesensor":     (9401, ["read-temperature"]),
    "temperaturecontroller": (9402, ["evaluate-temperature"]),
    "heatingswitch":         (9403, ["switch-heating"]),
    "sessionctx":            (9400, ["session-context"]),
}
EXECUTORS = {   # system -> (port, service definition it executes)
    "exread":     (9411, "read-temperature"),
    "exevaluate": (9412, "evaluate-temperature"),
    "exswitch":   (9413, "switch-heating"),
}
CLOCK = ("controlclock", 9420)

ROOM = {"temp": 24.6, "heater": "ON", "window": False, "switch_broken": False, "rate": 1.0}

def room_loop():
    while True:
        time.sleep(1.0)
        d = (0.05 if ROOM["heater"] == "ON" else -0.04) - (0.06 if ROOM["window"] else 0.0)
        ROOM["temp"] = round(ROOM["temp"] + ROOM["rate"] * d + random.uniform(-0.005, 0.005), 3)

def provider_logic(svc, x):
    if svc == "read-temperature":     return round(ROOM["temp"], 1)        # ReadTemperature() -> temp
    if svc == "evaluate-temperature": return x[0] > x[1]                   # EvaluateTemperature(temp, thr) -> decision
    if svc == "switch-heating":                                            # SwitchHeating(decision) -> switchState
        if ROOM["switch_broken"]: raise RuntimeError("heating switch does not answer")
        ROOM["heater"] = "OFF" if x else "ON"; return ROOM["heater"]
    raise KeyError(svc)

def kappa(step, rho, r):
    """kappa_read(rho)=(), kappa_eval(rho,r)=(r(read), rho_thr), kappa_act(rho,r)=r(eval)."""
    return {"read": lambda: [], "eval": lambda: [r["read"], rho["thr"]], "act": lambda: r["eval"]}[step]()

# ------------------------------------------------------------------ session-context service
CTX, CTX_CV = {}, threading.Condition()

def ctx_dispatch(method, path, q, body):
    parts = path.strip("/").split("/")
    if parts[0] != "session-context" or len(parts) < 3: return 404, {"error": path}
    sid, what = int(parts[1]), parts[2]
    with CTX_CV:
        c = CTX.setdefault(sid, {"rho": None, "xi": {}})
        if what == "rho" and method == "POST":
            c["rho"] = body; CTX_CV.notify_all(); trace(ev="ctx-rho", sessionId=sid, rho=body); return 200, {"ok": True}
        if what == "rho" and method == "GET":
            if not CTX_CV.wait_for(lambda: c["rho"] is not None, timeout=float(q.get("wait", ["0"])[0])):
                return 504, {"error": f"rho of session {sid} not bound"}
            return 200, c["rho"]
        if what == "xi" and method == "POST":
            c["xi"][parts[3]] = body["value"]; return 200, {"ok": True}
        if what == "xi" and method == "GET":
            keys = [k for k in q.get("keys", [""])[0].split(",") if k]
            missing = [k for k in keys if k not in c["xi"]]
            if missing: return 409, {"error": f"results {missing} not stored"}
            return 200, {k: c["xi"][k] for k in keys}
    return 404, {"error": path}

# ------------------------------------------------------------------ Executors (4.6.2 contract)
def url_of(o): p = o["provider"]; return f"http://{p['address']}:{p['port']}{o['serviceUri']}"

def run_step(name, chor, req):
    sid, ssid = req["sessionId"], req["sessionStepId"]
    sp = req.get("staticParameters") or {}
    step, dep = sp["step"], [d for d in sp.get("dep", "").split(",") if d]
    pre = {o["service"]["serviceDefinition"]: url_of(o) for o in req.get("preconditionOrchestrationResults") or []}
    main = req["mainOrchestrationResult"]; status, msg = "SUCCESS", None
    try:
        ctx = pre["session-context"].rstrip("/") + f"/{sid}"
        rho = call("GET", ctx + "/rho?wait=30")
        xi = call("GET", ctx + "/xi?keys=" + ",".join(dep)) if dep else {}
        x = kappa(step, rho, xi)
        trace(ev="invoke", sessionId=sid, step=step, executor=name, provider=main["provider"]["systemName"], input=x)
        d = post(url_of(main), {"input": x})["result"]
        post(ctx + f"/xi/{step}", {"value": d})      # store xi(s) before notifying (Lemma 2)
        trace(ev="done", sessionId=sid, step=step, result=d)
    except Exception as e:
        status, msg = "FATAL_ERROR", str(e); trace(ev="fail", sessionId=sid, step=step, error=msg)
    body = {"sessionId": sid, "sessionStepId": ssid, "status": status}
    if msg: body.update(message=msg, exception=msg)
    post(chor.rstrip("/") + "/choreographer/executor/notify-step-done", body, timeout=10)

def make_handler(dispatch):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _go(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n)) if n else None
            u = urlparse(self.path)
            try: code, obj = dispatch(method, u.path, parse_qs(u.query), body)
            except Exception as e: code, obj = 503, {"error": str(e)}
            raw = b"" if obj is None else json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def do_GET(self): self._go("GET")
        def do_POST(self): self._go("POST")
    return H

def serve(port, dispatch):
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(dispatch))
    threading.Thread(target=srv.serve_forever, daemon=True).start(); return srv

def provider_dispatch(services):
    def d(method, path, q, body):
        svc = path.strip("/").split("/")[0]
        if svc == "mgmt" and method == "POST":           # room switches, not a service
            ROOM.update({k: v for k, v in (body or {}).items() if k in ("window", "switch_broken", "rate", "temp")}); return 200, ROOM
        if svc == "state": return 200, ROOM
        if svc not in services: return 404, {"error": path}
        if svc == "session-context": return ctx_dispatch(method, path, q, body)
        return 200, {"result": provider_logic(svc, body["input"])}
    return d

def executor_dispatch(name, svc, chor):
    def d(method, path, q, body):
        if path == "/service-info":
            return 200, {"serviceDefinition": svc, "minVersion": 1, "maxVersion": 1,
                         "dependencies": [{"serviceDefinitionRequirement": "session-context", "interfaceRequirements": [IFACE]}]}
        if path == "/start":
            threading.Thread(target=run_step, args=(name, chor, body), daemon=True).start(); return 200, None
        if path == "/abort":
            trace(ev="abort", executor=name, sessionId=body["sessionId"]); return 200, None
        return 404, {"error": path}
    return d

def start_all(chor):
    threading.Thread(target=room_loop, daemon=True).start()
    for name, (port, svcs) in PROVIDERS.items(): serve(port, provider_dispatch(svcs))
    for name, (port, svc) in EXECUTORS.items(): serve(port, executor_dispatch(name, svc, chor))
    serve(CLOCK[1], lambda m, p, q, b: (trace(ev="session-status", **(b or {})), (200, None))[1])

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--chor", default="http://127.0.0.1:8457")
    a = ap.parse_args(); start_all(a.chor)
    print("room up: providers 9401-9403 + session-context 9400, executors 9411-9413, controlclock notify 9420")
    threading.Event().wait()

if __name__ == "__main__":
    main()
