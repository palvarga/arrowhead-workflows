#!/usr/bin/env python3
"""Recipe Validator for the shared-tool forklift example (intent paper, Section 9.4).

Deterministic checks over an abstract Workflow Recipe W = P = (name, A, alpha_0, next_A) and the
context Ctx_i (context.json). No LLM is involved here.

  I1  intent grounding     requested capabilities exist in sigma_i, scope exists     -> Reject / Clarify
  I2  policy conflict      the intent asks to omit a service mandated by policy (E5)  -> Clarify
  V1  structure            alpha_0 in A; next_A, next_alpha acyclic; names unique;
                           next within an Action; dep(s) only on Steps preceding s    -> Revise
  V2  service grounding    theta(s) in sigma_i                                         -> Revise
  V3  binding feasibility  dry-run O_i(c(s), theta(s)) != bottom, c(s) = executor or CH_i  -> Revise
  V4  physical precond.    transport to station precedes configure / tighten (E3)     -> Revise
  V5  data provenance      no program literals; program consumers depend on get-program (E4) -> Revise
  V6  goal coverage        mandatory services present; tighten precedes evaluate/record (E5) -> Revise
  V7  resource bracketing  reserve < every tool use < release; return to crib < release (E1) -> Revise
  V8  failure admission    lease declared (else Clarify); tool-using Steps depend on reserve (E6) -> Revise

Verdict: Reject if any Reject finding, else Clarify if any Clarify finding, else Revise if any Revise
finding, else Accept.

Recipe format (see README.md): {"name", "first_action", "actions": [{"name", "next", "steps":
[{"name", "service", "next": [...], "dep": [...], "executor": null | "<executor>", "params": {...}}]}]}
"""
import json, pathlib, sys

VERDICT_ORDER = ["accept", "revise", "clarify", "reject"]


class Recipe:
    """Indexed view of an abstract Recipe; precedence s < s' as defined in Section 9.4."""

    def __init__(self, r):
        self.r = r
        self.actions = {a["name"]: a for a in r.get("actions", [])}
        self.step = {}
        self.action_of = {}
        self.dup = []
        for a in r.get("actions", []):
            for s in a.get("steps", []):
                if s["name"] in self.step:
                    self.dup.append(s["name"])
                self.step[s["name"]] = s
                self.action_of[s["name"]] = a["name"]
        self.action_seq, self.action_cycle = self._action_sequence()

    def _action_sequence(self):
        seq, seen, a = [], set(), self.r.get("first_action")
        while a is not None and a in self.actions:
            if a in seen:
                return seq, True
            seen.add(a); seq.append(a)
            a = self.actions[a].get("next")
        return seq, False

    def _reach(self, s):
        """Steps reachable from s through next_alpha (transitive closure), same Action only."""
        out, todo = set(), list(self.step[s].get("next", []))
        while todo:
            n = todo.pop()
            if n in out or n not in self.step:
                continue
            out.add(n); todo.extend(self.step[n].get("next", []))
        return out

    def precedes(self, a, b):
        if a not in self.step or b not in self.step or a == b:
            return False
        aa, ab = self.action_of[a], self.action_of[b]
        if aa == ab:
            return b in self._reach(a)
        if aa in self.action_seq and ab in self.action_seq:
            return self.action_seq.index(aa) < self.action_seq.index(ab)
        return False

    def by_service(self, svc, to=None):
        return [n for n, s in self.step.items()
                if s.get("service") == svc and (to is None or (s.get("params") or {}).get("to") == to)]


def finding(check, ok, msg, severity="revise", fact=None):
    f = {"check": check, "ok": ok, "message": msg}
    if not ok:
        f["severity"] = severity
    if fact:
        f["fact"] = fact
    return f


# ------------------------------------------------------------------ intent-level checks
def check_intent(interp, ctx):
    out = []
    sigma = ctx["K_AH"]["services"]
    req = (interp or {}).get("requested_capabilities") or []
    missing = [c for c in req if c not in sigma]
    out.append(finding("I1", not missing,
                       f"requested capabilities not exposed by the Local Cloud: {missing}" if missing
                       else "all requested capabilities are grounded in sigma_i", "reject"))
    line = str(((interp or {}).get("scope") or {}).get("line", ""))
    if line and line not in ctx["K_runtime"]["lines"]:
        out.append(finding("I1", False, f"scope line {line} unknown in K_runtime", "clarify", "E7"))
    pol = ctx["K_eng"]["quality_policy"]
    omit = [s for s in (interp or {}).get("omit_services") or [] if s in pol["mandatory_services"]]
    out.append(finding("I2", not omit,
                       f"intent asks to omit {omit}, which the quality policy mandates; operator clarification needed"
                       if omit else "no conflict with mandatory policies", "clarify", pol["fact"]))
    return out


# ------------------------------------------------------------------ Recipe checks V1..V8
def v1_structure(R):
    errs = []
    if R.r.get("first_action") not in R.actions:
        errs.append("alpha_0 not in A")
    if R.action_cycle:
        errs.append("next_A is cyclic")
    if set(R.action_seq) != set(R.actions):
        errs.append(f"Actions not reachable from alpha_0: {sorted(set(R.actions) - set(R.action_seq))}")
    if R.dup:
        errs.append(f"duplicate Step names {R.dup}")
    for n, s in R.step.items():
        for m in s.get("next", []):
            if m not in R.step or R.action_of[m] != R.action_of[n]:
                errs.append(f"next of {n} -> {m} leaves its Action or is undefined")
        if n in R._reach(n):
            errs.append(f"next_alpha has a cycle through {n}")
        for d in s.get("dep", []):
            if not R.precedes(d, n):
                errs.append(f"dep({n}) contains {d}, which does not precede {n}")
    return finding("V1", not errs, "; ".join(errs) or "well-formed Plan, Actions and Steps; dep only on preceding Steps")


def v2_grounding(R, ctx):
    sigma = ctx["K_AH"]["services"]
    bad = sorted({s["service"] for s in R.step.values() if s.get("service") not in sigma})
    return finding("V2", not bad, f"services not in sigma_i: {bad}" if bad
                   else f"all {len(R.step)} Steps use services of sigma_i")


def orchestrate_dry(ctx, consumer, svc):
    """Dry-run O_i(consumer, svc): a binding exists iff the service is registered and the consumer authorized."""
    if svc not in ctx["K_AH"]["services"]:
        return None
    if svc not in ctx["K_AH"]["authorization"].get(consumer, []):
        return None
    return ctx["K_AH"]["services"][svc]["provider"]


def v3_binding(R, ctx):
    errs = []
    for n, s in R.step.items():
        if s.get("service") not in ctx["K_AH"]["services"]:
            continue                                         # reported by V2
        ex = s.get("executor")
        if ex is not None and ex not in ctx["K_AH"]["executors"]:
            errs.append(f"{n}: Executor {ex} not registered"); continue
        c = ex or "choreographer"
        if orchestrate_dry(ctx, c, s["service"]) is None:
            errs.append(f"{n}: O_i({c}, {s['service']}) = bottom")
    return finding("V3", not errs, "; ".join(errs) or "dry-run orchestration succeeds for every Step", fact="E2")


def v4_precondition(R, ctx):
    pc = ctx["K_eng"]["physical_precondition"]
    moves = R.by_service("transport-tool", pc["transport_to"])
    errs = []
    for svc in pc["before_services"]:
        for n in R.by_service(svc):
            if not any(R.precedes(m, n) for m in moves):
                errs.append(f"no transport to {pc['transport_to']} precedes {n} ({svc})")
    return finding("V4", not errs, "; ".join(errs) or "tool reaches the station before it is configured or used",
                   fact=pc["fact"])


def v5_provenance(R, ctx):
    pv = ctx["K_eng"]["program_provenance"]
    errs = []
    src = R.by_service(pv["source_service"])
    for n, s in R.step.items():
        lit = [k for k in (s.get("params") or {}) if k in pv["forbidden_literal_params"]]
        if lit:
            errs.append(f"{n} carries program literals {lit}")
    for svc in pv["consumer_services"]:
        for n in R.by_service(svc):
            if not any(p in R.step[n].get("dep", []) for p in src):
                errs.append(f"{n} ({svc}) does not depend on {pv['source_service']}")
    return finding("V5", not errs, "; ".join(errs) or "program parameters come only from get-program", fact=pv["fact"])


def v6_goal(R, ctx):
    pol = ctx["K_eng"]["quality_policy"]
    errs = [f"no Step requires {svc}" for svc in pol["mandatory_services"] if not R.by_service(svc)]
    for t in R.by_service(pol["after_service"]):
        for svc in pol["mandatory_services"]:
            if svc == pol["after_service"]:
                continue
            for n in R.by_service(svc):
                if not R.precedes(t, n):
                    errs.append(f"{t} does not precede {n}")
    return finding("V6", not errs, "; ".join(errs) or "tightening, evaluation and recording covered in order",
                   fact=pol["fact"])


def tool_uses(R, ctx):
    ex = ctx["K_eng"]["exclusive_resource"]
    return [n for n, s in R.step.items() if s.get("service") in ex["tool_using_services"]]


def v7_bracketing(R, ctx):
    ex = ctx["K_eng"]["exclusive_resource"]
    acq, rel = R.by_service(ex["acquire_service"]), R.by_service(ex["release_service"])
    errs = []
    if not acq:
        errs.append(f"no Step requires {ex['acquire_service']}")
    if not rel:
        errs.append(f"no Step requires {ex['release_service']}")
    if acq and rel:
        for u in tool_uses(R, ctx):
            if not any(R.precedes(a, u) for a in acq):
                errs.append(f"{u} is not preceded by {ex['acquire_service']}")
            if not any(R.precedes(u, l) for l in rel):
                errs.append(f"{u} does not precede {ex['release_service']}")
        backs = R.by_service("transport-tool", ex["return_transport_to"])
        if not any(R.precedes(b, l) for b in backs for l in rel):
            errs.append(f"no transport to {ex['return_transport_to']} precedes {ex['release_service']}")
    return finding("V7", not errs, "; ".join(errs) or "every tool use lies between reserve and release; tool returned",
                   fact=ex["fact"])


def v8_failure(R, ctx):
    ex, lease = ctx["K_eng"]["exclusive_resource"], ctx["K_eng"]["lease"]
    acq = R.by_service(ex["acquire_service"])
    if not acq:
        return finding("V8", True, "no exclusive resource acquired")
    if not lease.get("declared"):
        return finding("V8", False, f"hold on {ex['resource']} is not bounded independently of Session completion "
                                    "(no lease semantics in K_eng); an engineer must supply it", "clarify", lease["fact"])
    errs = [f"{n} renews the lease but does not depend on {acq[0]}"
            for n, s in R.step.items()
            if s.get("service") in lease["renewing_services"] and not any(a in s.get("dep", []) for a in acq)]
    return finding("V8", not errs, "; ".join(errs) or "lease declared; every tool-using Step depends on the reservation",
                   fact=lease["fact"])


def validate(candidate, ctx):
    """candidate = {"interpretation": {...}, "recipe": {...} | None}. Returns {"verdict", "findings"}."""
    findings = check_intent(candidate.get("interpretation"), ctx)
    recipe = candidate.get("recipe")
    if recipe is None:
        if all(f["ok"] for f in findings):
            findings.append(finding("V1", False, "no candidate Recipe proposed"))
    else:
        R = Recipe(recipe)
        findings += [v1_structure(R), v2_grounding(R, ctx), v3_binding(R, ctx), v4_precondition(R, ctx),
                     v5_provenance(R, ctx), v6_goal(R, ctx), v7_bracketing(R, ctx), v8_failure(R, ctx)]
    sev = [f["severity"] for f in findings if not f["ok"]]
    verdict = max(sev, key=VERDICT_ORDER.index) if sev else "accept"
    return {"verdict": verdict, "findings": findings}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: validator.py <candidate.json> [context.json]")
    cand = json.load(open(sys.argv[1], encoding="utf-8"))
    if "recipe" not in cand:
        cand = {"recipe": cand}
    ctx = json.load(open(sys.argv[2] if len(sys.argv) > 2 else pathlib.Path(__file__).with_name("context.json"), encoding="utf-8"))
    res = validate(cand, ctx)
    for f in res["findings"]:
        print(f"{f['check']:3} {'ok  ' if f['ok'] else f['severity'].upper():8} {f['message']}")
    print("verdict:", res["verdict"])
