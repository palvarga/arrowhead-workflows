#!/usr/bin/env python3
"""Track 2 scenarios O1-O3 in the format of intent_benchmark.json (v2.0), with three extensions:
  - "actions": a ProductionIntent may group its operations into Actions (Choreographer plan hierarchy);
  - "sessions": an intent may hold several concurrent plans (one Workflow Session each);
  - spec "flow" (material flow between stations), "initial_state" (runtime state K_runtime),
    "sessions" / "deadline" / "makespan" oracle checks.
Writes orders.json next to this file."""
import copy, json, pathlib

def op(i, svc, **p):
    return {"id": i, "service": svc, "parameters": {k: str(v) for k, v in p.items()}}

def intent(title, actions=None, operations=None, sessions=None, batch=1):
    d = {"intentId": "INTENT-" + title.upper().replace(" ", "-")[:24], "title": title, "description": "", "category": "GENERIC",
         "target": {"productType": "housing", "color": "red", "batchSize": batch}}
    if actions is not None: d["actions"] = actions
    if operations is not None: d["operations"] = operations
    if sessions is not None: d["sessions"] = sessions
    return d

# ------------------------------------------------------------------ O1: end-to-end order, several Actions, material flow
STEEL = dict(material="steel", payload_kg=4.5, coolant="true")
PAINT = dict(color="red", coats=3, film_thickness_um=30, area_cm2=800, payload_kg=4.5)

def machining(changes_after, tools_from=2):
    ops, t = [op("load", "cnc-load-program", program="FIXTURE_PLATE")], tools_from
    for i in range(1, 15):
        ops.append(op(f"m{i}", "cnc-machine-part", **STEEL))
        if i in changes_after:
            ops.append(op(f"t{t}", "cnc-tool-change", tool=t)); t += 1
    return {"name": "machining", "operations": ops}

def painting(purge_before=(13,)):
    ops = []
    for i in range(1, 15):
        if i in purge_before: ops.append(op(f"purge{i}", "paint-booth-purge"))
        ops.append(op(f"s{i}", "paint-spray-coat", **PAINT))
    return {"name": "painting", "operations": ops}

def move(name, a, b, kg=63, trips=1):
    return {"name": name, "operations": [op(f"{name}{'_' + str(k + 1) if trips > 1 else ''}", "amr-transport-load", **{"from": a, "to": b, "payload_kg": kg, "load_id": "HOUSINGS"})
                                         for k in range(trips)]}

def o1_actions(**kw):
    acts = [
        machining(kw.get("changes", (3, 6, 9, 12))),
        move("to_paint", "CNC", "PAINT_BOOTH", **kw.get("trip", {})),
        painting(kw.get("purge", (13,))),
        move("to_oven", "PAINT_BOOTH", "CURING_OVEN", **kw.get("trip", {})),
        {"name": "curing", "operations": [op("cure", "oven-cure-batch", coating="solvent_paint", temperature_c=150, dwell_min=25, parts=14, payload_kg=63)]
                                          + ([op("cool", "oven-cooldown", target_c=60)] if kw.get("cool", True) else [])},
        move("to_qa", "CURING_OVEN", "CHECKWEIGHER", **kw.get("trip", {})),
        {"name": "quality", "operations": [op("weigh", "checkweigher-weigh-item", nominal_kg=4.5, tolerance_pct=2, count=14, **({"fail_on_reject": "true"} if kw.get("gate", True) else {}))]},
        move("to_pallet", "CHECKWEIGHER", "PALLETIZER", **kw.get("trip", {})),
        {"name": "palletizing", "operations": [op("stack", "palletizer-stack-case", count=14, payload_kg=4.5, case_height_mm=300),
                                               op("wrap", "palletizer-wrap-pallet", film_layers=4), op("release", "palletizer-pallet-change")]},
    ]
    drop = kw.get("drop")
    return [a for a in acts if a["name"] != drop]

O1 = {
    "id": "O1", "level": "track2", "expected_outcome": "ACCEPT", "tags": ["multi-action", "material-flow", "end-to-end"],
    "prompt": "Machine 14 steel housings (4.5 kg each, coolant on) with program FIXTURE_PLATE; the CNC starts with fresh inserts in all pockets, "
              "so change the tool only when the next housing could not be finished. Bring all housings with the AMR to the paint booth and paint "
              "them red with 3 coats of 30 µm on 800 cm², purging the booth only when the filter requires it. Cure them together in one oven batch "
              "as solvent paint at 150 °C for 25 minutes and let the oven cool down until the door can be opened by hand. Weigh every housing at the "
              "checkweigher (nominal 4.5 kg, ±2 %) and stop the line on any reject. Finally stack them on one EUR pallet as 300 mm high cases, "
              "wrap it with 4 film layers and release it. Use the AMR for every move between stations, in as few trips as possible.",
    "flow": {"part_kg": 4.5, "start": {"CNC": {"raw": 14}}},
    "required_services": ["cnc-load-program", "cnc-machine-part", "cnc-tool-change", "amr-transport-load", "paint-spray-coat", "paint-booth-purge",
                          "oven-cure-batch", "oven-cooldown", "checkweigher-weigh-item", "palletizer-stack-case", "palletizer-wrap-pallet", "palletizer-pallet-change"],
    "exact_counts": {"cnc-machine-part": 14, "cnc-tool-change": 4, "paint-spray-coat": 14, "paint-booth-purge": 1, "oven-cure-batch": 1,
                     "amr-transport-load": 4, "palletizer-pallet-change": 1},
    "strict_order": [["cnc-machine-part", "paint-spray-coat"], ["paint-spray-coat", "oven-cure-batch"], ["oven-cure-batch", "checkweigher-weigh-item"],
                     ["checkweigher-weigh-item", "palletizer-stack-case"]],
    "params_all": {"cnc-machine-part": {"material": "steel", "payload_kg": "4.5", "coolant": "true"},
                   "paint-spray-coat": {"color": "red", "coats": "3", "film_thickness_um": "30", "area_cm2": "800"},
                   "oven-cure-batch": {"coating": "solvent_paint", "temperature_c": "150", "dwell_min": "25", "parts": "14", "payload_kg": "63"},
                   "checkweigher-weigh-item": {"nominal_kg": "4.5", "tolerance_pct": "2", "fail_on_reject": "true"},
                   "palletizer-wrap-pallet": {"film_layers": "4"}},
    "aggregates": [{"service": "oven-cooldown", "param": "target_c", "op": "max", "max": 60},
                   {"service": "checkweigher-weigh-item", "param": "count", "op": "sum", "equals": 14},
                   {"service": "palletizer-stack-case", "param": "count", "op": "sum", "equals": 14}],
    "twin": {"status": "SUCCESS", "final_state": [{"station": "plant", "path": "at.PALLETIZER.palletized", "equals": 14},
                                                  {"station": "palletizer", "path": "completed_pallets", "len": 1},
                                                  {"station": "painting_robot", "path": "purges", "equals": 1}]},
    "notes": "FIXTURE_PLATE on steel: 5/60 · 300/90 = 27.8 % per housing, three per insert (83.3 %), 14 housings -> changes after 3, 6, 9 and 12 (4). "
             "Booth filter: one purge before housing 13. Oven: 14 x 4.5 = 63 kg, one batch; door opens by hand at <= 60 °C, so the AMR can unload only "
             "after a cool-down to <= 60 °C. 63 kg <= 250 kg: one AMR trip per move, 4 trips. Pallet: 14 cases = 2 layers, 144 + 2 x 300 = 744 mm.",
    "reference_intent": intent("end-to-end order", actions=o1_actions()),
    "traps": [
        {"id": "rough_rule", "error": "tool change every 2 housings (the DUCK_MOLD_ROUGH rule)", "intent": intent("t", actions=o1_actions(changes=(2, 4, 6, 8, 10, 12)))},
        {"id": "late_change", "error": "tool change every 4 housings", "intent": intent("t", actions=o1_actions(changes=(4, 8, 12)))},
        {"id": "no_purge", "error": "no booth purge", "intent": intent("t", actions=o1_actions(purge=()))},
        {"id": "no_oven_transport", "error": "forgets the AMR move from the paint booth to the oven", "intent": intent("t", actions=o1_actions(drop="to_oven"))},
        {"id": "no_cooldown", "error": "no cool-down: the oven door stays locked at 150 °C", "intent": intent("t", actions=o1_actions(cool=False))},
        {"id": "split_trips", "error": "two half-load AMR trips per move", "intent": intent("t", actions=o1_actions(trip={"kg": 31.5, "trips": 2}))},
        {"id": "no_quality_gate", "error": "weighs but does not stop the line on a reject", "intent": intent("t", actions=o1_actions(gate=False))},
    ],
}

# ------------------------------------------------------------------ O2: two concurrent orders sharing the AMR (leases, deadline)
def lease(i): return op(i, "resource-lease-acquire", resource="amr")
def free(i): return op(i, "resource-lease-release", resource="amr")
BLANK = dict(material="steel", payload_kg=12, coolant="true")

def order_a(leases=True, whole=False):
    ops = []
    if leases: ops.append(lease("lease_in"))
    ops.append(op("blanks_in", "amr-transport-load", **{"from": "WAREHOUSE", "to": "CNC", "payload_kg": 24, "load_id": "BLANKS-A"}))
    if leases and not whole: ops.append(free("free_in"))
    ops += [op("load", "cnc-load-program", program="DUCK_MOLD_ROUGH"), op("m1", "cnc-machine-part", **BLANK), op("m2", "cnc-machine-part", **BLANK)]
    if leases and not whole: ops.append(lease("lease_out"))
    ops.append(op("parts_out", "amr-transport-load", **{"from": "CNC", "to": "PAINT_BOOTH", "payload_kg": 24, "load_id": "PARTS-A"}))
    if leases: ops.append(free("free_out"))
    return ops

def order_b(leases=True, release="after_move"):
    ops = []
    if leases: ops.append(lease("lease_b"))
    ops.append(op("housings", "amr-transport-load", **{"from": "PAINT_BOOTH", "to": "CURING_OVEN", "payload_kg": 63, "load_id": "HOUSINGS-B"}))
    if leases and release == "after_move": ops.append(free("free_b"))
    ops.append(op("cure", "oven-cure-batch", coating="solvent_paint", temperature_c=150, dwell_min=25, parts=14, payload_kg=63))
    if leases and release == "after_cure": ops.append(free("free_b"))
    return ops

O2 = {
    "id": "O2", "level": "track2", "expected_outcome": "ACCEPT", "tags": ["concurrency", "shared-resource", "lease", "deadline"],
    "prompt": "Run two orders at the same time; both need the AMR. Order A: bring 2 steel blanks (12 kg each) from the WAREHOUSE to the CNC, "
              "rough-machine them with DUCK_MOLD_ROUGH (coolant on) and bring them to the PAINT_BOOTH; they must arrive there within 30 minutes. "
              "Order B: bring the 14 painted housings (63 kg) from the PAINT_BOOTH to the CURING_OVEN and cure them in one batch as solvent paint "
              "at 150 °C for 25 minutes. The two orders must never use the AMR at the same time, and everything should be done as early as possible.",
    "required_services": ["resource-lease-acquire", "resource-lease-release", "amr-transport-load", "cnc-machine-part", "oven-cure-batch"],
    "sessions": 2,
    "exact_counts": {"amr-transport-load": 3, "cnc-machine-part": 2, "oven-cure-batch": 1},
    "deadline": [{"session": "A", "service": "amr-transport-load", "to": "PAINT_BOOTH", "max_min": 30}],
    "makespan": {"max_min": 56},
    "twin": {"status": "SUCCESS", "final_state": [{"station": "cnc", "path": "parts_machined", "equals": 2},
                                                  {"station": "curing_oven", "path": "batches_cured", "equals": 1}]},
    "notes": "AMR at 1 m/s plus 1 min handling per trip (schematic). Order B is bound by the oven: ramp 22 -> 150 °C at 5 °C/min (25.6 min) + 25 min dwell. "
             "With the lease released right after each trip, order A reaches the paint booth after ~17 min and the last step ends after ~54 min. "
             "Holding the lease through the cure delays A beyond its 30-minute deadline; holding it for the whole order A delays B by ~15 min.",
    "reference_intent": intent("two orders", sessions=[{"name": "A", "operations": order_a()}, {"name": "B", "operations": order_b()}]),
    "traps": [
        {"id": "no_lease", "error": "no lease: both orders dispatch the AMR at once", "intent": intent("t", sessions=[{"name": "A", "operations": order_a(False)}, {"name": "B", "operations": order_b(False)}])},
        {"id": "lease_through_cure", "error": "order B holds the AMR lease until its cure has finished", "intent": intent("t", sessions=[{"name": "B", "operations": order_b(release="after_cure")}, {"name": "A", "operations": order_a()}])},
        {"id": "release_forgotten", "error": "order B never releases the lease", "intent": intent("t", sessions=[{"name": "A", "operations": order_a()}, {"name": "B", "operations": order_b(release="never")}])},
        {"id": "lease_whole_order", "error": "order A holds the lease for its whole order", "intent": intent("t", sessions=[{"name": "A", "operations": order_a(whole=True)}, {"name": "B", "operations": order_b()}])},
        {"id": "one_session", "error": "both orders in one session, one after the other", "intent": intent("t", sessions=[{"name": "A+B", "operations": order_a(False) + order_b(False)}])},
    ],
}

# ------------------------------------------------------------------ O3: resume after a stop (runtime state)
ROUGH = dict(material="steel", payload_kg=12, coolant="true")
def o3_ops(seq):
    ops, n = [], 0
    for s in seq:
        if s == "m": n += 1; ops.append(op(f"blank{n}", "cnc-machine-part", **ROUGH))
        elif s == "load": ops.append(op("load", "cnc-load-program", program="DUCK_MOLD_ROUGH"))
        else: ops.append(op(f"t{s}", "cnc-tool-change", tool=s))
    ops.append(op("status", "cnc-spindle-status"))
    return ops

O3 = {
    "id": "O3", "level": "track2", "expected_outcome": "ACCEPT", "tags": ["runtime-state", "resume", "tool-wear"],
    "prompt": "The rough-machining order of 5 steel duck-mould blanks (12 kg each, coolant on, DUCK_MOLD_ROUGH) stopped after a power cut. "
              "Finish the order with no unnecessary tool changes and read the spindle status at the end.",
    "initial_state": {"cnc": {"program": "DUCK_MOLD_ROUGH", "activeTool": 3, "wear": [0, 66.67, 66.67, 33.33, 0, 0, 0, 0, 0], "parts_machined": 2}},
    "runtime_note": "Cell state reported by the CNC after the restart: program DUCK_MOLD_ROUGH still loaded; 2 of 5 blanks machined; active insert T3 at 33.3 % wear; T1 and T2 at 66.7 %; T4–T8 fresh.",
    "required_services": ["cnc-machine-part", "cnc-tool-change", "cnc-spindle-status"],
    "exact_counts": {"cnc-machine-part": 3, "cnc-tool-change": 1, "cnc-spindle-status": 1},
    "params_all": {"cnc-machine-part": {"material": "steel", "payload_kg": "12", "coolant": "true"}},
    "strict_order": [["cnc-machine-part", "cnc-spindle-status"]],
    "twin": {"status": "SUCCESS", "final_state": [{"station": "cnc", "path": "parts_machined", "equals": 5}]},
    "notes": "The plan must start from the reported state: 3 blanks are left; T3 can take one more (33.3 + 33.3 = 66.7 %), then one change to a fresh "
             "pocket (T4) for the last two blanks: 1 tool change. A plan written for a fresh machine fails at the second blank.",
    "reference_intent": intent("resume", operations=o3_ops(["m", 4, "m", "m"])),
    "traps": [
        {"id": "from_scratch", "error": "replans the whole order as if the machine were fresh", "intent": intent("t", operations=o3_ops(["load", "m", "m", 2, "m", "m", 3, "m"]))},
        {"id": "ignores_wear", "error": "machines the 3 remaining blanks on T3 without a change", "intent": intent("t", operations=o3_ops(["m", "m", "m"]))},
        {"id": "change_first", "error": "changes the tool before the first blank (2 changes)", "intent": intent("t", operations=o3_ops([4, "m", "m", 5, "m"]))},
        {"id": "five_more", "error": "machines 5 more blanks (ignores the 2 already done)", "intent": intent("t", operations=o3_ops(["m", 4, "m", "m", 5, "m", "m"]))},
    ],
}

out = {"O1": O1, "O2": O2, "O3": O3}
pathlib.Path(__file__).with_name("orders.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print({k: len(v["traps"]) for k, v in out.items()})
