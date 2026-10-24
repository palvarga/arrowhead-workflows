#!/usr/bin/env python3
"""Intent-to-Recipe pipeline for the shared-tool forklift example (intent paper, Section 9).

  intent text --Translator--> candidate (interpretation + draft Recipe) --Validator--> verdict
     Revise: findings go back to the Translator (at most --k-max rounds)
     Accept: Compiler -> ChoreographerPlanRequestDTO -> compared with ../forklift/plan.json
             --run executes it with ../forklift/dryrun.py (stand-in Choreographer, three line Sessions)

  python3 pipeline.py --intent I-tool                       # stored drafts, D1-D5 caught, accepted in round 2
  python3 pipeline.py --all                                 # all intents of intents.json, summary table
  python3 pipeline.py --intent I-tool --run                 # ... and execute the accepted plan in the kit
  python3 pipeline.py --intent I-tool --run --fail          # failure case: L2 fails, lease expires, L1/L3 complete
  python3 pipeline.py --intent I-tool --no-lease            # V8: no lease semantics in Ctx -> Clarify
  python3 pipeline.py --all --translator claude             # real LLM (ANTHROPIC_API_KEY; --model to choose)
  python3 pipeline.py --all --export-web                    # write ../web/intent-data.js for web/intent.html

Python 3 standard library only. Every run is written to out/<translator>/<intent>/.
"""
import argparse, copy, datetime, json, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import compiler, translator, validator          # noqa: E402


def run_intent(intent, ctx, tr, k_max, out_dir, run_kit=False, quiet=False, fail=False):
    say = (lambda *a: None) if quiet else print
    d = out_dir / intent["id"]; d.mkdir(parents=True, exist_ok=True)
    say(f"\n=== {intent['id']}  (Ladder level {intent.get('ladder_level')}, expected: {intent.get('expected')})")
    say(f"    intent: \"{intent['text']}\"")
    rounds, feedback, verdict, plan, identical = [], None, None, None, None
    for k in range(k_max):
        cand = tr.propose(intent, ctx, feedback, k)
        res = validator.validate(cand, ctx)
        verdict = res["verdict"]
        rounds.append({"round": k, "source": cand.get("source"), "note": cand.get("note"),
                       "interpretation": cand.get("interpretation"), "recipe": cand.get("recipe"),
                       "verdict": verdict, "findings": res["findings"]})
        (d / f"round{k}.json").write_text(json.dumps(rounds[-1], indent=1))
        say(f"--- round {k}: {cand.get('source')}")
        for f in res["findings"]:
            if not f["ok"]:
                say(f"    {f['check']:3} {f['severity'].upper():8} {f['message']}")
        say(f"    verdict: {verdict.upper()}")
        if verdict != "revise":
            break
        feedback = res["findings"]
    else:
        verdict = "reject"
        say(f"    revision bound k_max={k_max} reached -> REJECT")
    result = {"intent": intent, "verdict": verdict, "rounds": len(rounds), "trace": rounds}
    if verdict == "accept":
        plan = compiler.compile_recipe(rounds[-1]["recipe"], ctx)
        (d / "plan.json").write_text(json.dumps(plan, indent=1) + "\n")
        ref = intent.get("reference_plan")
        if ref:
            diffs = compiler.diff(plan, json.loads((HERE / ref).read_text()))
            identical = not diffs
            result["reference_diffs"] = diffs
            say(f"    compiled plan: {d / 'plan.json'}; identical to hand-authored {ref}: "
                f"{'yes' if identical else 'no ' + str(diffs[:5])}")
        if run_kit:
            say("    executing in the kit: ../forklift/dryrun.py --plan ...")
            cmd = [sys.executable, str(HERE.parent / "forklift" / "dryrun.py"), "--plan", str(d / "plan.json")]
            p = subprocess.run(cmd + (["--fail"] if fail else []),
                               capture_output=True, text=True, timeout=120)
            (d / "dryrun.txt").write_text(p.stdout + p.stderr)
            summary = [ln for ln in p.stdout.splitlines() if "==" in ln]
            result["dryrun"] = summary
            for ln in summary:
                say("    " + ln.strip())
    result["identical_to_reference"] = identical
    (d / "result.json").write_text(json.dumps(result, indent=1))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--intent", help="intent id from intents.json")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--translator", choices=["stored", "claude"], default="stored")
    ap.add_argument("--model", help=f"Claude model (default {translator.DEFAULT_MODEL})")
    ap.add_argument("--k-max", type=int, default=3, help="maximum number of Translator rounds")
    ap.add_argument("--run", action="store_true", help="execute accepted plans with ../forklift/dryrun.py")
    ap.add_argument("--fail", action="store_true", help="with --run: block the AGV route during L2 (failure case)")
    ap.add_argument("--no-lease", action="store_true", help="drop the lease semantics E6 from Ctx (V8 demo)")
    ap.add_argument("--export-web", action="store_true", help="write ../web/intent-data.js")
    a = ap.parse_args()

    ctx = json.loads((HERE / "context.json").read_text())
    if a.no_lease:
        ctx = copy.deepcopy(ctx); ctx["K_eng"]["lease"]["declared"] = False
    intents = json.loads((HERE / "intents.json").read_text())
    todo = intents if a.all else [i for i in intents if i["id"] == a.intent]
    if not todo:
        sys.exit(f"unknown intent {a.intent}; known: {[i['id'] for i in intents]}")
    out_dir = HERE / "out" / (a.translator if a.translator == "stored" else f"claude-{a.model or translator.DEFAULT_MODEL}")
    if a.no_lease:
        out_dir = out_dir.with_name(out_dir.name + "-no-lease")
    tr = translator.StoredTranslator() if a.translator == "stored" else \
        translator.ClaudeTranslator(model=a.model, log_dir=out_dir / "api")

    results = [run_intent(i, ctx, tr, a.k_max, out_dir, a.run, fail=a.fail) for i in todo]

    print("\nintent        expected  verdict   rounds  identical-to-reference")
    for r in results:
        idt = {True: "yes", False: "no", None: "-"}[r["identical_to_reference"]]
        print(f"{r['intent']['id']:13} {r['intent'].get('expected', '-'):9} {r['verdict']:9} {r['rounds']:<7} {idt}")
    summary = {"translator": tr.name, "model": getattr(tr, "model", None),
               "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "no_lease": a.no_lease, "results": results}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    if a.export_web:
        web = HERE.parent / "web" / "intent-data.js"
        runs = {}
        if web.exists():                                   # keep runs of other translators
            txt = web.read_text()
            runs = json.loads(txt[txt.index("=") + 1:].rstrip().rstrip(";"))
        key = out_dir.name
        runs[key] = summary
        web.write_text("// generated by intent/pipeline.py --export-web; do not edit\nwindow.INTENT_RUNS = "
                       + json.dumps(runs, indent=1) + ";\n")
        print(f"\nwrote run '{key}' to {web}")


if __name__ == "__main__":
    main()
