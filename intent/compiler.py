#!/usr/bin/env python3
"""Recipe Compiler: abstract Recipe -> ChoreographerPlanRequestDTO (Eclipse Arrowhead 4.6.2), in the exact
format of ../forklift/plan.json, so that the accepted Recipe runs in the existing kit.

Mapping (intent paper, Table 6 of Worked Example I, extended):
  P.name -> name;  alpha_0 -> firstActionName;  next_A -> nextActionName (absent at the end);
  F_alpha (Steps without predecessor, in Recipe order) -> firstStepNames;  next_alpha -> nextStepNames;
  theta(s) -> serviceDefinitionRequirement;  dep(s) -> staticParameters.dep (kit step ids);
  kit step id (the plant's input composition kappa_s is keyed by it) -> staticParameters.step.
eta(s) is not emitted: in the 4.6.2 kit every service definition has its own Executor, and the
Choreographer selects the Executor by service definition (see README.md).
Only call this on a Recipe the Validator has accepted.
"""
import json, sys


def kit_step_id(step, ctx):
    ids = ctx["kit"]["step_id"]
    key = step["service"]
    if key == "transport-tool":
        key = f"transport-tool@{(step.get('params') or {}).get('to')}"
    if key not in ids:
        raise KeyError(f"no kit step id for {key}")
    return ids[key]


def compile_recipe(recipe, ctx):
    steps = {s["name"]: s for a in recipe["actions"] for s in a["steps"]}
    kid = {n: kit_step_id(s, ctx) for n, s in steps.items()}
    actions = []
    for a in recipe["actions"]:
        targets = {m for s in a["steps"] for m in s.get("next", [])}
        out = {"name": a["name"]}
        if a.get("next"):
            out["nextActionName"] = a["next"]
        out["firstStepNames"] = [s["name"] for s in a["steps"] if s["name"] not in targets]
        out["steps"] = [{
            "name": s["name"],
            "serviceRequirement": {"serviceDefinitionRequirement": s["service"],
                                   "interfaceRequirements": [ctx["kit"]["interface"]]},
            "staticParameters": {"step": kid[s["name"]], "dep": ",".join(kid[d] for d in s.get("dep", []))},
            "quantity": 1,
            "nextStepNames": list(s.get("next", [])),
        } for s in a["steps"]]
        actions.append(out)
    return {"name": recipe["name"], "firstActionName": recipe["first_action"], "actions": actions}


def diff(a, b, path=""):
    """Structural differences between two plan DTOs (empty list = identical)."""
    if type(a) is not type(b):
        return [f"{path or '/'}: {a!r} != {b!r}"]
    if isinstance(a, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'generated' if k in a else 'reference'}")
            else:
                out += diff(a[k], b[k], f"{path}/{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in diff(x, y, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: compiler.py <candidate.json> [context.json]")
    cand = json.load(open(sys.argv[1]))
    ctx = json.load(open(sys.argv[2] if len(sys.argv) > 2 else __file__.rsplit("/", 1)[0] + "/context.json"))
    print(json.dumps(compile_recipe(cand.get("recipe", cand), ctx), indent=1))
