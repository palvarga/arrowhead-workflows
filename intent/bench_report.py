#!/usr/bin/env python3
"""Summarize bench_out/ (written by bench.py) into the benchmark table of the intent paper.

Outcome definitions (per intent and repetition):
  llm-only  admitted = the round-0 draft contains a Recipe (it would be executed). Correct for an accept intent if
            the plan completes in the kit (3/3 Sessions) and the draft violates none of V1-V10 (policies such as the
            quality check or program provenance are not observable in the kit; the Validator's checks serve as the
            oracle here). Correct for a clarify/reject intent if no Recipe is proposed.
  validated admitted = verdict Accept. Correct if the verdict is the expected one (or listed in also_ok) and, for
            Accept, the plan completes in the kit.
  false accept  admitted, but the intent should not be accepted, or the plan fails in the kit, or (llm-only) it
            violates a check.   false reject  an accept intent that is not admitted.

  python3 bench_report.py            # Markdown to stdout, LaTeX rows to bench_out/table.tex
"""
import collections, json, pathlib, sys

HERE = pathlib.Path(__file__).parent
CATS = ["P", "C", "R", "J"]


def load(cfg_dir):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(cfg_dir.glob("*/rep*/result.json"))]


def outcome_llm_only(r):
    lo = r["llm_only"]
    adm = lo["admitted"]
    kit_ok = adm and lo.get("kit", {}).get("status") == "completed"
    clean = not [c for c in lo["validator_would_flag"] if c.startswith("V")]
    if r["expected"] == "accept":
        correct = adm and kit_ok and clean
        fa, fr = adm and not correct, not adm
    else:
        correct, fa, fr = not adm, adm, False
    return correct, fa, fr, 1


def outcome_validated(r):
    v = r["llm_val"]
    adm = v["verdict"] == "accept"
    kit_ok = adm and v.get("kit", {}).get("status") == "completed"
    ok_verdicts = {r["expected"], *r.get("also_ok", [])}
    correct = v["verdict"] in ok_verdicts and (not adm or kit_ok)
    fa = adm and (r["expected"] != "accept" or not kit_ok)
    fr = r["expected"] == "accept" and not adm
    return correct, fa, fr, v["rounds"]


def row(name, recs, fn):
    by = collections.defaultdict(list)
    tot = []
    for r in recs:
        o = fn(r)
        by[r["cat"]].append(o); tot.append(o)
    pct = lambda xs, i: 100.0 * sum(x[i] for x in xs) / len(xs) if xs else float("nan")
    # stability: share of intents whose outcome (correct or not) is identical over all repetitions
    per = collections.defaultdict(set)
    for r in recs:
        per[r["intent"]].add(fn(r)[0])
    stable = 100.0 * sum(len(s) == 1 for s in per.values()) / len(per) if per else float("nan")
    reps = len(recs) // max(1, len(per))
    return {"config": name, "n": len(recs), "reps": reps, **{c: pct(by[c], 0) for c in CATS},
            "all": pct(tot, 0), "false_accept": pct(tot, 1), "false_reject": pct(tot, 2),
            "calls": sum(x[3] for x in tot) / len(tot) if tot else float("nan"), "stable": stable}


def main():
    root = HERE / "bench_out"
    rows = []
    t = root / "template"
    if t.exists():
        rows.append(row("Template (+ Validator)", load(t), outcome_validated))
    for d in sorted(p for p in root.glob("claude-*") if p.is_dir()):
        recs = load(d)
        m = d.name[len("claude-"):]
        rows.append(row(f"{m}, LLM only", recs, outcome_llm_only))
        rows.append(row(f"{m}, LLM + Validator", recs, outcome_validated))
    if not rows:
        sys.exit("no results in bench_out/; run bench.py first")
    hdr = "| Configuration | runs | P | C | R | J | correct | false accept | false reject | calls | stable |"
    print(hdr); print("|---" * (hdr.count("|") - 1) + "|")
    f = lambda x: "-" if x != x else f"{x:.0f}"
    tex = []
    for r in rows:
        print(f"| {r['config']} | {r['n']} | {f(r['P'])} | {f(r['C'])} | {f(r['R'])} | {f(r['J'])} | {f(r['all'])} | "
              f"{f(r['false_accept'])} | {f(r['false_reject'])} | {r['calls']:.2f} | {f(r['stable'])} |")
        tex.append(f"{r['config']} & {r['n']} & {f(r['P'])} & {f(r['C'])} & {f(r['R'])} & {f(r['J'])} & "
                   f"{f(r['all'])} & {f(r['false_accept'])} & {f(r['false_reject'])} & {r['calls']:.2f} \\\\")
    (root / "table.tex").write_text("\n".join(tex) + "\n", encoding="utf-8")
    (root / "summary.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"\nP/C/R/J/correct/false accept/false reject in % of runs; calls = mean Translator calls per run; "
          f"stable = % of intents with the same outcome in every repetition.\nLaTeX rows: {root / 'table.tex'}")


if __name__ == "__main__":
    main()
