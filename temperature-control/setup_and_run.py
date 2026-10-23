#!/usr/bin/env python3
"""Register the temperature-control example in an Arrowhead 4.6.2 Local Cloud (insecure/HTTP mode)
and act as controlclock, which starts one Workflow Session per control period.

  python3 setup_and_run.py setup          --plant-host 192.168.1.20
  python3 setup_and_run.py start          --plant-host 192.168.1.20               # one control period
  python3 setup_and_run.py run 20         --plant-host 192.168.1.20 --every 10    # 20 periods, one every 10 s
  python3 setup_and_run.py window on|off  --plant-host 192.168.1.20
  python3 setup_and_run.py brokenswitch on|off --plant-host 192.168.1.20         # S-FAILINVOKE in the act Step
"""
import argparse, json, sys, time, pathlib
HERE = pathlib.Path(__file__).parent; sys.path.insert(0, str(HERE))
from plant import PROVIDERS, EXECUTORS, CLOCK, IFACE, call, CallError     # noqa: E402

STATE = HERE / "ids.json"
PLAN_NAME = "temperature-control"

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
            r = ok("POST", f"{sr}/serviceregistry/register", {"serviceDefinition": s, "serviceUri": "/" + s, "secure": "NOT_SECURE",
                   "interfaces": [IFACE], "version": 1, "providerSystem": system(name, a.plant_host, port)})
            if r:
                ids["svc"][s] = r["serviceDefinition"]["id"]; ids["prov"][s] = [r["provider"]["id"]]
                ids["iface"] = r["interfaces"][0]["id"]; print(f"   {name}:{port}  {s}")
    r = ok("POST", f"{sr}/serviceregistry/query", {"serviceDefinitionRequirement": "choreographer-start-session"})
    if r and r.get("serviceQueryData"):
        q = r["serviceQueryData"][0]
        ids["svc"]["choreographer-start-session"] = q["serviceDefinition"]["id"]
        ids["prov"]["choreographer-start-session"] = [q["provider"]["id"]]; ids["chor_iface"] = q["interfaces"][0]["id"]
    print("2) consumer systems -> Service Registry; executors -> Choreographer")
    for name, port in {**{n: p for n, (p, _) in EXECUTORS.items()}, CLOCK[0]: CLOCK[1]}.items():
        r = ok("POST", f"{sr}/serviceregistry/register-system", system(name, a.plant_host, port))
        if r: ids["cons"][name] = r["id"]
    for name, (port, svc) in EXECUTORS.items():
        ok("POST", f"{ch}/choreographer/executor/register", {"system": system(name, a.plant_host, port), "baseUri": "",
           "serviceDefinitionName": svc, "minVersion": 1, "maxVersion": 1})
        print(f"   {name}:{port}  executes {svc}; dependency session-context")
    print("3) intra-cloud authorization rules")
    rules = [(n, s) for n, (_, svc) in EXECUTORS.items() for s in (svc, "session-context")]
    rules += [(CLOCK[0], "session-context"), (CLOCK[0], "choreographer-start-session")]
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
    r = ok("POST", f"{a.orch.rstrip('/')}/orchestrator/orchestration", {"requesterSystem": requester,
           "requestedService": {"serviceDefinitionRequirement": svc, "interfaceRequirements": [IFACE]},
           "orchestrationFlags": {"overrideStore": True, "matchmaking": True}})
    if not r or not r.get("response"): sys.exit(f"Orchestrator found no authorized provider of {svc}")
    o = r["response"][0]; p = o["provider"]; return f"http://{p['address']}:{p['port']}{o['serviceUri']}"

def start(a):
    me = system(CLOCK[0], a.plant_host, CLOCK[1])
    start_url, ctx_url = orchestrate(a, me, "choreographer-start-session"), orchestrate(a, me, "session-context")
    r = ok("POST", start_url, [{"name": PLAN_NAME, "quantity": 1, "notifyAddress": a.plant_host, "notifyPort": CLOCK[1], "notifyPath": "/notify"}])
    if r and r[0].get("sessionId"):
        ok("POST", f"{ctx_url}/{r[0]['sessionId']}/rho", {"thr": a.thr}); print(f"session {r[0]['sessionId']} started, rho_thr = {a.thr}")

def switch(a, key):
    port = PROVIDERS["temperaturesensor"][0]
    print(ok("POST", f"http://{a.plant_host}:{port}/mgmt", {key: a.arg == "on"}))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["setup", "start", "run", "window", "brokenswitch"]); ap.add_argument("arg", nargs="?")
    ap.add_argument("--plant-host", required=True); ap.add_argument("--thr", type=float, default=23.0)
    ap.add_argument("--every", type=float, default=10.0)
    ap.add_argument("--sr", default="http://127.0.0.1:8443"); ap.add_argument("--auth", default="http://127.0.0.1:8445")
    ap.add_argument("--orch", default="http://127.0.0.1:8441"); ap.add_argument("--chor", default="http://127.0.0.1:8457")
    a = ap.parse_args()
    if a.cmd == "setup": setup(a)
    elif a.cmd == "start": start(a)
    elif a.cmd == "run":
        for _ in range(int(a.arg or 10)): start(a); time.sleep(a.every)
    elif a.cmd == "window": switch(a, "window")
    else: switch(a, "switch_broken")
