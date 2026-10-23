#!/usr/bin/env python3
"""Register the forklift example in an Arrowhead 4.6.2 Local Cloud (insecure/HTTP mode) and act as the
three line controllers.

  python3 setup_and_run.py setup        --plant-host 192.168.1.20
  python3 setup_and_run.py start 2      --plant-host 192.168.1.20   # line L2 needs the tool
  python3 setup_and_run.py start 1      --plant-host 192.168.1.20   # line L1 asks while L2 holds it
  python3 setup_and_run.py block on|off --plant-host 192.168.1.20   # AGV route blocked -> S-FAILINVOKE

A line controller knows only the Orchestrator. It orchestrates the core service
`choreographer-start-session` (POST /choreographer/session/start, the 4.6.2 client endpoint) and the
application service `session-context`, starts the plan by name, and binds rho to the new sessionId.

Endpoints (checked against the 4.6.2 sources):
  Service Registry  POST /serviceregistry/register, /serviceregistry/register-system, /serviceregistry/query   (8443)
  Authorization     POST /authorization/mgmt/intracloud                                                          (8445)
  Orchestrator      POST /orchestrator/orchestration                                                             (8441)
  Choreographer     POST /choreographer/executor/register, /choreographer/mgmt/plan                              (8457)
"""
import argparse, json, sys, pathlib
HERE = pathlib.Path(__file__).parent; sys.path.insert(0, str(HERE))
from plant import PROVIDERS, EXECUTORS, LINES, IFACE, call, CallError     # noqa: E402

STATE = HERE / "ids.json"
PLAN_NAME = "shared-tool-tightening"

def ok(method, url, body=None):
    try: return call(method, url, body, timeout=30)
    except (CallError, OSError) as e: print(f"  ! {method} {url} -> {e}"); return None

def system(name, host, port): return {"systemName": name, "address": host, "port": port}

def setup(a):
    sr, au, ch = a.sr.rstrip("/"), a.auth.rstrip("/"), a.chor.rstrip("/")
    ids = {"svc": {}, "prov": {}, "iface": None, "cons": {}}
    print("1) provider systems and services -> Service Registry")
    for name, (port, svcs) in PROVIDERS.items():
        for s in svcs:
            r = ok("POST", f"{sr}/serviceregistry/register", {"serviceDefinition": s, "serviceUri": "/" + s,
                   "secure": "NOT_SECURE", "interfaces": [IFACE], "version": 1, "providerSystem": system(name, a.plant_host, port)})
            if r:
                ids["svc"][s] = r["serviceDefinition"]["id"]; ids["prov"][s] = [r["provider"]["id"]]
                ids["iface"] = r["interfaces"][0]["id"]; print(f"   {name}:{port}  {s}")
    r = ok("POST", f"{sr}/serviceregistry/query", {"serviceDefinitionRequirement": "choreographer-start-session"})
    if r and r.get("serviceQueryData"):
        q = r["serviceQueryData"][0]
        ids["svc"]["choreographer-start-session"] = q["serviceDefinition"]["id"]
        ids["prov"]["choreographer-start-session"] = [q["provider"]["id"]]
        ids["chor_iface"] = q["interfaces"][0]["id"]; print("   found core service choreographer-start-session")
    print("2) consumer systems: executors and line controllers -> Service Registry; executors -> Choreographer")
    consumers = {n: p for n, (p, _, _) in EXECUTORS.items()} | {f"linecontroller{k}": p for k, p in LINES.items()}
    for name, port in consumers.items():
        r = ok("POST", f"{sr}/serviceregistry/register-system", system(name, a.plant_host, port))
        if r: ids["cons"][name] = r["id"]
    for name, (port, svc, deps) in EXECUTORS.items():
        ok("POST", f"{ch}/choreographer/executor/register", {"system": system(name, a.plant_host, port), "baseUri": "",
           "serviceDefinitionName": svc, "minVersion": 1, "maxVersion": 1})
        print(f"   {name}:{port}  executes {svc}; dependencies session-context{''.join(', ' + d for d in deps)}")
    print("3) intra-cloud authorization rules")
    rules = []
    for name, (_, svc, deps) in EXECUTORS.items():
        rules += [(name, s) for s in [svc, "session-context"] + deps]          # agvexecutor alone gets transport-tool (A2)
    for k in LINES:
        rules += [(f"linecontroller{k}", "session-context"), (f"linecontroller{k}", "choreographer-start-session")]
    for cons, svc in rules:
        if cons in ids["cons"] and svc in ids["svc"]:
            ok("POST", f"{au}/authorization/mgmt/intracloud", {"consumerId": ids["cons"][cons], "providerIds": ids["prov"][svc],
               "serviceDefinitionIds": [ids["svc"][svc]], "interfaceIds": [ids.get("chor_iface") if svc.startswith("choreographer") else ids["iface"]]})
            print(f"   {cons} -> {svc}")
    print("4) plan -> Choreographer")
    r = ok("POST", f"{ch}/choreographer/mgmt/plan", json.loads((HERE / "plan.json").read_text()))
    if r: ids["planId"] = r["id"]; print(f"   planId = {r['id']}  ({PLAN_NAME})")
    STATE.write_text(json.dumps(ids, indent=1))

def orchestrate(a, requester, svc):
    r = ok("POST", f"{a.orch.rstrip('/')}/orchestrator/orchestration", {
        "requesterSystem": requester,
        "requestedService": {"serviceDefinitionRequirement": svc, "interfaceRequirements": [IFACE]},
        "orchestrationFlags": {"overrideStore": True, "matchmaking": True}})
    if not r or not r.get("response"):
        sys.exit(f"Orchestrator found no authorized provider of {svc} for {requester['systemName']}")
    o = r["response"][0]; p = o["provider"]
    return f"http://{p['address']}:{p['port']}{o['serviceUri']}"

def start(a):
    line = int(a.arg); me = system(f"linecontroller{line}", a.plant_host, LINES[line])
    start_url = orchestrate(a, me, "choreographer-start-session")
    ctx_url = orchestrate(a, me, "session-context")
    r = ok("POST", start_url, [{"name": PLAN_NAME, "quantity": 1, "allowInterCloud": False,
                               "notifyAddress": a.plant_host, "notifyPort": LINES[line], "notifyPath": "/notify"}])
    if not r: return
    res = r[0]; print(json.dumps(res, indent=1))
    if res.get("sessionId"):                     # Executors long-poll rho, so the race with the JMS-started Session is harmless
        rho = {"tool": "NR7", "line": line, "stn": f"st{line}", "variant": {1: "F25", 2: "F30", 3: "F35"}[line], "crib": "crib"}
        ok("POST", f"{ctx_url}/{res['sessionId']}/rho", rho)

def block(a):
    port = PROVIDERS["agvfleet"][0]
    print(ok("POST", f"http://{a.plant_host}:{port}/mgmt", {"agv_blocked": a.arg == "on"}))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["setup", "start", "block"]); ap.add_argument("arg", nargs="?")
    ap.add_argument("--plant-host", required=True, help="address of the machine running plant.py, reachable from the core systems")
    ap.add_argument("--sr", default="http://127.0.0.1:8443"); ap.add_argument("--auth", default="http://127.0.0.1:8445")
    ap.add_argument("--orch", default="http://127.0.0.1:8441"); ap.add_argument("--chor", default="http://127.0.0.1:8457")
    a = ap.parse_args()
    {"setup": setup, "start": start, "block": block}[a.cmd](a)
