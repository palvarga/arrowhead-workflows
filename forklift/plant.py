#!/usr/bin/env python3
"""Forklift plant for the Arrowhead Workflow example (paper Sec. 8), Arrowhead 4.6.2 executor model.

One process hosts
  * the Application Systems of the forklift example (providers): toolcrib, mes, agvfleet, nutrunner, qms
  * sessionctx: provider of the registered service `session-context`, which holds rho (session
    parameters) and xi (Step results) per Choreographer sessionId
  * one Choreographer Executor per service definition. Every Executor declares `session-context`
    as a dependency in its /service-info answer; the tool-using Executors also declare
    `renew-tool-lease`. The Choreographer orchestrates these dependencies with the Executor as
    requester and hands them over in preconditionOrchestrationResults, so no address is hard-wired.
  * notify endpoints of the three line controllers (ChoreographerNotificationDTO)

The tool crib grants a reservation as a lease. ReserveTool answers at once (granted or queued);
the reserve Executor keeps its Step running and polls the ticket until the grant. A lease that is
neither renewed nor released expires and the next line in the queue is granted, so a failed Session
cannot block the other lines forever.

Stdlib only:  python3 plant.py --chor http://<choreographer-host>:8457 [--lease 120]
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

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # plant traffic never goes via a proxy

class CallError(Exception):
    def __init__(self, code, body): super().__init__(f"HTTP {code}: {body}"); self.code = code; self.body = body

def call(method, url, body=None, timeout=None):
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            raw = r.read(); return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise CallError(e.code, e.read().decode(errors="replace")[:300]) from None

def post(url, body, timeout=None): return call("POST", url, body, timeout)

# ------------------------------------------------------------------ systems, ports, services
PROVIDERS = {   # system -> (port, [service definitions])
    "toolcrib":   (9101, ["reserve-tool", "release-tool", "renew-tool-lease"]),
    "mes":        (9102, ["get-program"]),
    "agvfleet":   (9103, ["transport-tool"]),
    "nutrunner":  (9104, ["configure-tool", "tighten-fasteners"]),
    "qms":        (9105, ["evaluate-tightening", "record-tightening"]),
    "sessionctx": (9300, ["session-context"]),
}
LEASE_DEP = ["renew-tool-lease"]
EXECUTORS = {   # system -> (port, service definition it executes, extra dependencies)
    "exreserve":   (9201, "reserve-tool", []),
    "exprogram":   (9202, "get-program", []),
    "agvexecutor": (9203, "transport-tool", LEASE_DEP),   # w_agv: the only consumer authorized for transport-tool (A2)
    "exconfigure": (9204, "configure-tool", LEASE_DEP),
    "extighten":   (9205, "tighten-fasteners", LEASE_DEP),
    "exevaluate":  (9206, "evaluate-tightening", []),
    "exrecord":    (9207, "record-tightening", []),
    "exrelease":   (9208, "release-tool", []),
}
LINES = {1: 9311, 2: 9312, 3: 9313}            # line controllers: system "linecontroller<k>", notify port
U_STEPS = {"move", "cfg", "tight", "ret"}       # Steps that use the tool physically (Proposition 1)

# ------------------------------------------------------------------ tool crib with leases (A1')
class Crib:
    def __init__(self, lease_s):
        self.cv = threading.Condition(); self.lease_s = lease_s
        self.holder = None; self.until = 0.0; self.queue = []; self.tickets = {}; self.n = 0
    def _grant_next(self):
        if self.holder is None and self.queue:
            t = self.queue.pop(0); line = self.tickets[t]["line"]
            self.holder = line; self.until = time.time() + self.lease_s
            self.tickets[t].update(status="granted", token=f"r{line}.{t}")
            trace(ev="crib-grant", line=line, token=self.tickets[t]["token"], lease_s=self.lease_s)
    def reserve(self, tool, line):
        with self.cv:
            self.n += 1; t = self.n; self.tickets[t] = {"line": line, "status": "queued"}
            self.queue.append(t); self._grant_next()
            return self.status(t)
    def status(self, t):
        tk = self.tickets[int(t)]
        if tk["status"] == "granted":
            return {"status": "granted", "token": tk["token"], "leaseUntil": round(self.until, 3)}
        return {"status": "queued", "ticket": int(t), "position": self.queue.index(int(t)) + 1}
    def _check(self, token):
        if self.holder is None or not token or not token.startswith(f"r{self.holder}."):
            raise PermissionError(f"token {token} holds no valid lease")
    def renew(self, tool, token):
        with self.cv:
            self._check(token); self.until = time.time() + self.lease_s
            return {"leaseUntil": round(self.until, 3)}
    def release(self, tool, token):
        with self.cv:
            self._check(token); self.holder = None; self._grant_next()
            return f"released {token}"
    def expire_loop(self):
        while True:
            time.sleep(0.1)
            with self.cv:
                if self.holder is not None and time.time() > self.until:
                    trace(ev="crib-lease-expired", line=self.holder, tool_at=PLANT["tool_at"],
                          note="reservation revoked; tool must be recovered to the crib before the next delivery")
                    PLANT["tool_at"] = "crib"   # operator recovery, outside the workflow
                    self.holder = None; self._grant_next()

CRIB = Crib(120.0)
PLANT = {"agv_blocked": False, "tool_at": "crib", "drift": False, "rec": 1000, "agv_seconds": 2.0, "nr_program": None}
PROGRAMS = {"F25": [8, 210, 0.05], "F30": [8, 210, 0.05], "F35": [6, 180, 0.05]}

def provider_logic(svc, x):
    """Provider side. x is the composed input kappa_s(rho, xi|dep(s))."""
    if svc == "reserve-tool":     return CRIB.reserve(*x)
    if svc == "renew-tool-lease": return CRIB.renew(*x)
    if svc == "release-tool":     return CRIB.release(*x)
    if svc == "get-program":      return PROGRAMS[x]
    if svc == "transport-tool":
        tool, src, dst = x[0], x[1], x[2]
        time.sleep(PLANT["agv_seconds"])
        if PLANT["agv_blocked"]: raise RuntimeError("AGV route blocked")
        PLANT["tool_at"] = dst; return f"{tool} arrived at {dst}"
    if svc == "configure-tool":                 # the program now lives in the tool controller
        PLANT["nr_program"] = x[1]; n, T, tol = x[1]; return f"program loaded ({n} x {T} N*m +-{tol:.0%})"
    if svc == "tighten-fasteners":
        n, T, _ = PLANT["nr_program"]
        return [round(T * (1 + (0.072 if PLANT["drift"] and j == 4 else random.uniform(-0.03, 0.03))), 1) for j in range(n)]
    if svc == "evaluate-tightening":
        R, (n, T, tol) = x
        return "OK" if len(R) == n and all(abs(v - T) <= tol * T for v in R) else "NOK"
    if svc == "record-tightening":
        PLANT["rec"] += 1; return f"stored #{PLANT['rec']}"
    raise KeyError(svc)

# ------------------------------------------------------------------ session-context service
CTX = {}                                        # sessionId -> {"rho": {...} | None, "xi": {}}
CTX_CV = threading.Condition()

def ctx_dispatch(method, path, q, body):
    # /session-context/<sid>/rho      POST rho | GET (?wait=s long-poll until rho is bound)
    # /session-context/<sid>/xi/<s>   POST {"value": d'}
    # /session-context/<sid>/xi       GET ?keys=a,b   -> 409 if a requested result is missing (Lemma 2)
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
def kappa(step, rho, r):
    """Input composition kappa_s of the forklift Recipe."""
    return {
        "res":   lambda: [rho["tool"], rho["line"]],
        "prog":  lambda: rho["variant"],
        "move":  lambda: [rho["tool"], rho["crib"], rho["stn"], r["res"]],
        "cfg":   lambda: [rho["tool"], r["prog"]],
        "tight": lambda: [rho["tool"], r["cfg"]],
        "chk":   lambda: [r["tight"], r["prog"]],
        "rec":   lambda: [rho["line"], rho["variant"], rho["tool"], r["tight"]],
        "ret":   lambda: [rho["tool"], rho["stn"], rho["crib"]],
        "rel":   lambda: [rho["tool"], r["res"]],
    }[step]()

def url_of(orch):
    p = orch["provider"]; return f"http://{p['address']}:{p['port']}{orch['serviceUri']}"

def dependencies_of(name):
    return [{"serviceDefinitionRequirement": d, "interfaceRequirements": [IFACE]}
            for d in ["session-context"] + EXECUTORS[name][2]]

def run_step(name, svc, chor, req):
    sid, ssid = req["sessionId"], req["sessionStepId"]
    sp = req.get("staticParameters") or {}
    step, dep = sp["step"], [d for d in sp.get("dep", "").split(",") if d]
    pre = {o["service"]["serviceDefinition"]: url_of(o) for o in req.get("preconditionOrchestrationResults") or []}
    main = req["mainOrchestrationResult"]
    status, msg = "SUCCESS", None
    try:
        ctx = pre["session-context"].rstrip("/") + f"/{sid}"
        rho = call("GET", ctx + "/rho?wait=30")
        xi = call("GET", ctx + "/xi?keys=" + ",".join(dep)) if dep else {}
        if step in U_STEPS:                     # no physical use of the tool without a valid lease
            post(pre["renew-tool-lease"], {"input": [rho["tool"], xi["res"]]})
        x = kappa(step, rho, xi)
        trace(ev="invoke", sessionId=sid, step=step, executor=name, provider=main["provider"]["systemName"], uri=url_of(main))
        d = post(url_of(main), {"input": x})["result"]
        if step == "res":                       # lease semantics: answer at once, keep the Step running until granted
            while d["status"] == "queued":
                trace(ev="waiting", sessionId=sid, step=step, position=d["position"]); time.sleep(0.5)
                d = call("GET", url_of(main) + f"/{d['ticket']}")["result"]
            d = d["token"]
        post(ctx + f"/xi/{step}", {"value": d})  # xi(s) := d' before notifying, as Lemma 2 needs
        trace(ev="done", sessionId=sid, step=step, result=d)
    except Exception as e:
        status, msg = "FATAL_ERROR", str(e)      # S-FAILINVOKE; ERROR would make 4.6.2 try another executor
        trace(ev="fail", sessionId=sid, step=step, error=msg)
    body = {"sessionId": sid, "sessionStepId": ssid, "status": status}
    if msg: body.update(message=msg, exception=msg)
    post(chor.rstrip("/") + "/choreographer/executor/notify-step-done", body, timeout=10)

# ------------------------------------------------------------------ HTTP plumbing
def make_handler(dispatch):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _go(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n)) if n else None
            u = urlparse(self.path)
            try: code, obj = dispatch(method, u.path, parse_qs(u.query), body)
            except PermissionError as e: code, obj = 409, {"error": str(e)}
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
        if svc == "mgmt" and method == "POST":                  # plant switches (route blocked, torque drift), not a service
            PLANT.update({k: v for k, v in (body or {}).items() if k in ("agv_blocked", "drift", "agv_seconds")}); return 200, PLANT
        if svc not in services: return 404, {"error": path}
        if svc == "session-context": return ctx_dispatch(method, path, q, body)
        if method == "GET" and svc == "reserve-tool":          # ticket polling
            with CRIB.cv: return 200, {"result": CRIB.status(path.strip("/").split("/")[1])}
        return 200, {"result": provider_logic(svc, body["input"])}
    return d

def executor_dispatch(name, svc, chor):
    def d(method, path, q, body):
        if path == "/service-info":
            return 200, {"serviceDefinition": svc, "minVersion": 1, "maxVersion": 1, "dependencies": dependencies_of(name)}
        if path == "/start":
            threading.Thread(target=run_step, args=(name, svc, chor, body), daemon=True).start()
            return 200, None                    # the Choreographer expects an immediate answer
        if path == "/abort":
            trace(ev="abort", executor=name, sessionId=body["sessionId"], sessionStepId=body["sessionStepId"]); return 200, None
        return 404, {"error": path}
    return d

def line_dispatch(line):
    def d(method, path, q, body):
        trace(ev="session-status", line=line, **(body or {})); return 200, None
    return d

def start_all(chor, lease_s=None):
    if lease_s: CRIB.lease_s = lease_s
    threading.Thread(target=CRIB.expire_loop, daemon=True).start()
    for name, (port, svcs) in PROVIDERS.items(): serve(port, provider_dispatch(svcs))
    for name, (port, svc, _) in EXECUTORS.items(): serve(port, executor_dispatch(name, svc, chor))
    for line, port in LINES.items(): serve(port, line_dispatch(line))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chor", default="http://127.0.0.1:8457", help="Choreographer base URL (for notify-step-done)")
    ap.add_argument("--lease", type=float, default=120.0, help="tool lease in seconds")
    a = ap.parse_args()
    start_all(a.chor, a.lease)
    print(f"plant up: providers 9101-9105 + session-context 9300, executors 9201-9208, line notify 9311-9313, lease {a.lease}s")
    threading.Event().wait()

if __name__ == "__main__":
    main()
