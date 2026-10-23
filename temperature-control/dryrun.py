#!/usr/bin/env python3
"""Offline dry run of the temperature-control example: a minimal stand-in for the 4.6.2 Choreographer and
Orchestrator executes plan.json against plant.py with the transition rules of Sec. 5.9, over real HTTP and with
the real executor contract. No Arrowhead core systems needed.

  python3 dryrun.py                 # 8 control periods, threshold 23 °C, room starts at 24.6 °C
  python3 dryrun.py --fail          # the heating switch stops answering from period 4 on
"""
import argparse, json, queue, threading, time, pathlib, sys
HERE = pathlib.Path(__file__).parent; sys.path.insert(0, str(HERE))
import plant                                                            # noqa: E402

PORT = 18457
PLAN = json.loads((HERE / "plan.json").read_text())
SVC_PROVIDER = {s: (n, p) for n, (p, ss) in plant.PROVIDERS.items() for s in ss}
SVC_EXECUTOR = {s: (n, p) for n, (p, s) in plant.EXECUTORS.items()}
AUTH = {(n, s) for n, (_, svc) in plant.EXECUTORS.items() for s in [svc, "session-context"]}
INBOX, NEXT_ID, LOCK = {}, [1000], threading.Lock()

def orchestrate(requester, svc):                                        # O_i: plan pi, or None for bottom
    if svc not in SVC_PROVIDER or (requester, svc) not in AUTH: return None
    n, p = SVC_PROVIDER[svc]
    return {"provider": {"systemName": n, "address": "127.0.0.1", "port": p}, "serviceUri": "/" + svc,
            "service": {"serviceDefinition": svc}, "interfaces": [{"interfaceName": plant.IFACE}]}

def session(line, sid, out):
    acts = PLAN["actions"]; A = [x["name"] for x in acts]
    step = {s["name"]: dict(s, action=x["name"]) for x in acts for s in x["steps"]}
    pre = {n: [m for m, s in step.items() if n in s["nextStepNames"]] for n in step}
    M = {n: "inactive" for n in step}; MA = {a: "inactive" for a in A}; MA[A[0]] = "active"
    n_tr, status, by_ssid = 0, "running", {}
    def tr(label):
        nonlocal n_tr; n_tr += 1; out.append((time.time(), line, n_tr, label))
    INBOX[sid] = queue.Queue()
    while status == "running":
        fired = True
        while fired and status == "running":
            fired = False
            if all(v == "completed" for v in MA.values()): status = "completed"; tr("close"); break
            for i, a in enumerate(A):
                if MA[a] == "inactive" and i and MA[A[i-1]] == "completed": MA[a] = "active"; tr(f"start({a})"); fired = True
                if MA[a] == "active" and all(M[n] == "completed" for n in step if step[n]["action"] == a):
                    MA[a] = "completed"; tr(f"finish({a})"); fired = True
            for n, s in step.items():
                if M[n] == "inactive" and MA[s["action"]] == "active" and all(M[p] == "completed" for p in pre[n]):
                    M[n] = "enabled"; tr(f"enable({n})"); fired = True
                elif M[n] == "enabled":
                    svc = s["serviceRequirement"]["serviceDefinitionRequirement"]
                    ex, port = SVC_EXECUTOR[svc]
                    info = plant.post(f"http://127.0.0.1:{port}/service-info", {"serviceDefinition": svc, "minVersion": 1, "maxVersion": 1}, 5)
                    deps = [orchestrate(ex, d["serviceDefinitionRequirement"]) for d in info["dependencies"]]
                    pi = orchestrate(ex, svc)
                    if pi is None or None in deps: status = "failed"; tr(f"fail({n}) S-FAILBIND"); break
                    with LOCK: NEXT_ID[0] += 1; ssid = NEXT_ID[0]
                    by_ssid[ssid] = n; M[n] = "running"
                    tr(f"bind({n}) -> {pi['provider']['systemName']} via {ex} [+{', '.join(d['service']['serviceDefinition'] for d in deps)}]")
                    plant.post(f"http://127.0.0.1:{port}/start", {"sessionId": sid, "sessionStepId": ssid, "quantity": 1,
                               "staticParameters": s["staticParameters"], "mainOrchestrationResult": pi,
                               "preconditionOrchestrationResults": deps}, timeout=5)
                    fired = True
        if status != "running": break
        msg = INBOX[sid].get()                                          # notify-step-done
        n = by_ssid[msg["sessionStepId"]]
        if msg["status"] == "SUCCESS": M[n] = "completed"; tr(f"done({n})")
        else: status = "failed"; tr(f"fail({n}) S-FAILINVOKE: {msg.get('message')}")
    out.append((time.time(), line, n_tr, f"== session {sid} {status} after {n_tr} transitions"))

def chor_dispatch(method, path, q, body):
    if path == "/choreographer/executor/notify-step-done": INBOX[body["sessionId"]].put(body); return 200, None
    return 404, {"error": path}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--fail", action="store_true"); ap.add_argument("--periods", type=int, default=8)
    a = ap.parse_args()
    plant.TRACE_PATH = str(HERE / "trace_dryrun.jsonl"); open(plant.TRACE_PATH, "w").close()
    plant.ROOM["rate"] = 0.0                               # the dry run advances the room once per period instead
    plant.start_all(f"http://127.0.0.1:{PORT}")
    plant.serve(PORT, chor_dispatch)
    for p in range(1, a.periods + 1):
        if a.fail and p == 4: plant.ROOM["switch_broken"] = True
        out, sid = [], 100 + p
        t = threading.Thread(target=session, args=(p, sid, out), daemon=True); t.start()
        time.sleep(0.05)                                   # client start: rho is bound right after the Session begins
        plant.post(f"http://127.0.0.1:9400/session-context/{sid}/rho", {"thr": 23.0})
        t.join(timeout=10)
        xi = plant.CTX.get(sid, {}).get("xi", {})
        print(f"period {p}:  read={xi.get('read')} °C  decision={xi.get('eval')}  switch={xi.get('act')}  ->  {out[-1][3].split('== ')[-1]}")
        for _, _, n, lab in out[:-1]:
            if p == 1 or "fail" in lab: print(f"      #{n:<2} {lab}")
        plant.ROOM["temp"] = round(plant.ROOM["temp"] + (0.45 if plant.ROOM["heater"] == "ON" else -0.35), 2)

if __name__ == "__main__":
    main()
