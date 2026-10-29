#!/usr/bin/env python3
"""Benchmark of the Intent-to-Recipe pipeline on the intents of benchmark.json (intent paper, Section 8).

Three configurations are compared on the same intents:

  template   deterministic baseline without an LLM: keyword match on the intent text; if it names the tightening
             task, the hand-authored reference Recipe is instantiated for the line found in the text, otherwise no
             Recipe is proposed. Then validated like any candidate.
  llm-only   the Translator's first draft (round 0) is executed directly: no Validator, no revision.
             It is the round-0 call of the llm+validator run, so it costs no extra API call.
  llm+val    Algorithm 2: Translator and Validator in the generate-validate-revise loop, k_max rounds.

Every admitted plan is executed in the runnable kit (../forklift/dryrun.py, three line Sessions); it is counted as
executed correctly only if all three Sessions complete. Per (intent, repetition) the result is written to
bench_out/<config>/<intent>/rep<r>/result.json; existing results are skipped, so an interrupted run resumes.

  python3 bench.py --config template                                    # no network, deterministic
  python3 bench.py --config claude --model claude-opus-5-5 --repeats 5  # needs ANTHROPIC_API_KEY
  python3 bench.py --config claude --model <second model> --repeats 5
  python3 bench_report.py                                               # tables (Markdown + LaTeX)

Python 3 standard library only.
"""
import argparse, concurrent.futures as cf, copy, json, pathlib, re, subprocess, sys, tempfile, threading, time

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import compiler, translator, validator          # noqa: E402

KIT_LOCK = threading.Lock()                     # the kit uses fixed ports: one execution at a time
REFERENCE = json.loads((HERE / "drafts" / "I-tool" / "round2.json").read_text(encoding="utf-8"))["recipe"]


def patched_context(ctx, patch):
    """Prompt injection through context data: append text to the Translator-visible service descriptions only."""
    if not patch:
        return ctx
    c = copy.deepcopy(ctx)
    for svc, extra in patch.items():
        c["K_AH"]["services"][svc]["text"] += " " + extra
    return c


def run_kit(recipe, ctx):
    """Compile and execute in the kit. Returns {"status": completed|failed|not-compilable|error, "completed": n}."""
    try:
        plan = compiler.compile_recipe(recipe, ctx)
    except Exception as e:                       # unknown service, transport without destination, malformed Recipe
        return {"status": "not-compilable", "completed": 0, "error": f"{type(e).__name__}: {e}"}
    with KIT_LOCK, tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "plan.json"
        p.write_text(json.dumps(plan), encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, str(HERE.parent / "forklift" / "dryrun.py"), "--plan", str(p)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        except subprocess.TimeoutExpired:
            return {"status": "error", "completed": 0, "error": "kit timeout"}
    lines = [ln for ln in r.stdout.splitlines() if "==" in ln]
    done = sum(" completed after " in ln for ln in lines)
    failed = sum(" failed after " in ln for ln in lines)
    status = "completed" if r.returncode == 0 and done == 3 and not failed else ("failed" if failed else "error")
    return {"status": status, "completed": done, "failed": failed, "summary": lines}


# ------------------------------------------------------------------ template baseline
TASK = re.compile(r"nutrunner|NR7|tighten|csavaroz|meghúz", re.I)
LINE = re.compile(r"(?:\bline\s*|\bL|\bstation\s*|\bst)(\d)\b|(\d)-es\s+sor", re.I)


class TemplateTranslator:
    name = "template"

    def propose(self, intent, ctx, feedback=None, round_no=0):
        t = intent["text"]
        m = LINE.search(t)
        line = int(next(g for g in m.groups() if g)) if m else None
        interp = {"name": "SharedToolTightening", "scope": {"line": line, "tool": "NR7"},
                  "requested_capabilities": [], "omit_services": []}
        recipe = copy.deepcopy(REFERENCE) if TASK.search(t) and line else None
        return {"interpretation": interp, "recipe": recipe, "source": "template"}


# ------------------------------------------------------------------ one benchmark item
def failed_checks(res):
    return sorted({f["check"] for f in res["findings"] if not f["ok"]})


def run_item(intent, ctx0, make_tr, k_max, out):
    ctx = patched_context(ctx0, intent.get("context_patch"))
    tr = make_tr(out)
    t0 = time.time()
    rounds, feedback, verdict = [], None, None
    for k in range(k_max):
        cand = tr.propose(intent, ctx, feedback, k)
        res = validator.validate(cand, ctx0)        # the Validator always reads the unpatched machine-readable Ctx
        rounds.append({"round": k, "interpretation": cand.get("interpretation"), "recipe": cand.get("recipe"),
                       "source": cand.get("source"), "usage": cand.get("usage"),
                       "verdict": res["verdict"], "failed": failed_checks(res), "findings": res["findings"]})
        verdict = res["verdict"]
        if verdict != "revise":
            break
        feedback = res["findings"]
    else:
        verdict = "reject"                          # revision bound reached
    r0 = rounds[0]
    llm_only = {"admitted": r0["recipe"] is not None, "validator_would_flag": r0["failed"],
                "omit_services": (r0["interpretation"] or {}).get("omit_services") or [],
                "requested_capabilities": (r0["interpretation"] or {}).get("requested_capabilities") or []}
    if llm_only["admitted"]:
        llm_only["kit"] = run_kit(r0["recipe"], ctx0)
    with_val = {"verdict": verdict, "rounds": len(rounds)}
    if verdict == "accept":
        if rounds[-1]["recipe"] == r0["recipe"] and "kit" in llm_only:
            with_val["kit"] = llm_only["kit"]
        else:
            with_val["kit"] = run_kit(rounds[-1]["recipe"], ctx0)
        with_val["preference"] = validator.validate(rounds[-1], ctx0).get("preference")
    result = {"intent": intent["id"], "cat": intent["cat"], "expected": intent["expected"],
              "also_ok": intent.get("also_ok", []), "translator": tr.name, "model": getattr(tr, "model", None),
              "seconds": round(time.time() - t0, 1), "llm_only": llm_only, "llm_val": with_val, "trace": rounds}
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", choices=["template", "claude"], required=True)
    ap.add_argument("--model", help=f"Claude model (default {translator.DEFAULT_MODEL})")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--k-max", type=int, default=3)
    ap.add_argument("--workers", type=int, default=4, help="parallel Translator calls (kit runs are serialized)")
    ap.add_argument("--ids", nargs="*", help="only these intent ids")
    a = ap.parse_args()

    ctx = json.loads((HERE / "context.json").read_text(encoding="utf-8"))
    bench = json.loads((HERE / "benchmark.json").read_text(encoding="utf-8"))["intents"]
    if a.ids:
        bench = [i for i in bench if i["id"] in a.ids]
    if a.config == "template":
        cfg, reps = "template", 1                    # deterministic: one repetition suffices
        make_tr = lambda out: TemplateTranslator()
    else:
        model = a.model or translator.DEFAULT_MODEL
        cfg, reps = f"claude-{model}", a.repeats
        make_tr = lambda out: translator.ClaudeTranslator(model=model, log_dir=out / "api")
    root = HERE / "bench_out" / cfg
    jobs = [(i, root / i["id"] / f"rep{r}") for i in bench for r in range(reps)]
    todo = [(i, o) for i, o in jobs if not (o / "result.json").exists()]
    print(f"{cfg}: {len(jobs)} items, {len(jobs) - len(todo)} already done, {len(todo)} to run")
    errors = 0
    with cf.ThreadPoolExecutor(max_workers=1 if a.config == "template" else a.workers) as ex:
        futs = {ex.submit(run_item, i, ctx, make_tr, a.k_max, o): (i, o) for i, o in todo}
        for n, f in enumerate(cf.as_completed(futs), 1):
            i, o = futs[f]
            try:
                r = f.result()
                kit = (r["llm_val"].get("kit") or {}).get("status", "-")
                print(f"[{n}/{len(todo)}] {i['id']} {o.name}: expected {i['expected']:7} -> {r['llm_val']['verdict']:7}"
                      f" rounds {r['llm_val']['rounds']} kit {kit}; round-0 recipe "
                      f"{'yes' if r['llm_only']['admitted'] else 'no'} flags {r['llm_only']['validator_would_flag']}")
            except Exception as e:
                errors += 1
                print(f"[{n}/{len(todo)}] {i['id']} {o.name}: ERROR {type(e).__name__}: {e}")
    print(f"done; {errors} error(s); results in {root}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
