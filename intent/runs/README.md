# Recorded runs with a real LLM

Both runs used `claude-opus-5-5` through the Anthropic Messages API, one call per intent and no revision round
needed, executed on Windows with

```
py pipeline.py --all --run --translator claude --export-web
```

Each folder holds `summary.json`, per intent `round<k>.json` (interpretation, draft Recipe, findings),
`result.json`, for accepted intents `plan.json` and `dryrun.txt` (kit execution), and the raw API responses in `api/`.

| Run | Time (UTC) | Code | I-tool | I-tool-L3 | I-noqc | I-wrench |
|---|---|---|---|---|---|---|
| `2026-10-24_claude-opus-5-5_run1` | 11:48 | before V9 (commit `df9e5e1`) | accept, **kit: 0/3 Sessions** | accept, **kit: 0/3 Sessions** | clarify | reject |
| `2026-10-24_claude-opus-5-5_run2` | 11:57 | with V9 (commit `70e28ac`) | accept, kit: 3/3 × 33 transitions | accept, kit: 3/3 × 33 transitions | clarify | reject |

**Run 1: a false accept.** Both accepted Recipes order `configure-tool` before `tighten-fasteners` through
`next`, but dep(tighten) does not contain the configure Step. The kit composes the input of `tighten-fasteners`
from ξ restricted to dep(s), so every Session fails at that Step (`S-FAILINVOKE: 'cfg'`, after 17 and 19
transitions). The context did not say which results each service needs, so neither the Translator nor the
Validator could know. Re-validated with the current code, both drafts get `revise` from V9 (defect D8 in
`../drafts/defects/`). Adding that one dependency makes both plans complete in the kit (3 × 33 and 3 × 35
transitions; the I-tool-L3 Recipe of run 1 has four Actions, 3|S| + 2|A| = 35).

**Run 2: correct end to end.** With `requires_results` in the service descriptions, both Recipes contain
dep(tighten) = {configure, reserve}; all checks pass in round 0, and all three line Sessions complete in the kit.
Exclusive tool use holds (tool-use intervals of the three Sessions disjoint; tool granted L2, L1, L3), and with the
AGV route blocked (`--fail`) the failed Session's lease expires and the other two lines complete. Neither Recipe is
structurally identical to `../../forklift/plan.json`: Claude names Steps differently and partitions the Actions
differently (configure in the tightening Action; for I-tool-L3 evaluate and record in the last Action, parallel to
the return transport). Both are valid realizations.

For I-noqc Claude followed the operator, listed `evaluate-tightening` in `omit_services` and flagged the conflict
with E5 in its constraints; the Validator returns `clarify`. For I-wrench it reported the ungroundable capability
instead of inventing a service; the Validator returns `reject`.

These are two single runs of one model, not a statistical evaluation.
