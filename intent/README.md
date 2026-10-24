# Intent to Recipe: shared-tool tightening

Runnable companion of **Worked Example II** of the paper *Intent-based Workflow Choreography* (P. Varga, revision
under review at *Sensors*). It takes an operator's natural-language intent for the forklift shared-tool example, has
an Intent Translator propose an interpreted intent and a draft Workflow Recipe, checks the draft with a
deterministic Recipe Validator, and compiles an accepted Recipe into the Choreographer plan that the existing kit in
[`../forklift`](../forklift) executes.

```
intent text --Translator--> interpretation + draft Recipe --Validator--> accept | revise | clarify | reject
                 ^                                              |
                 +------------- findings (revise, <= k_max) ----+
accept --Compiler--> ChoreographerPlanRequestDTO --compare--> ../forklift/plan.json --run--> ../forklift/dryrun.py
```

Python 3 standard library only. The Validator and Compiler never call an LLM.

## Try it

```
python3 pipeline.py --all                           # stored drafts: all four intents, summary table
python3 pipeline.py --intent I-tool --run           # D1-D5 caught, accepted in round 2, identical plan, 3 x 33 transitions
python3 pipeline.py --intent I-tool --run --fail    # failure case: L2 fails, its lease expires, L1 and L3 complete
python3 pipeline.py --intent I-tool --no-lease      # without lease semantics E6: V8 -> clarify
python3 -m unittest -v test_intent.py               # 15 tests
python3 pipeline.py --all --run --export-web        # writes ../web/intent-data.js for ../web/intent.html
```

With a real LLM (Claude through the Anthropic Messages API):

```
export ANTHROPIC_API_KEY=...
python3 pipeline.py --all --run --translator claude                   # default model claude-opus-5-5
python3 pipeline.py --all --translator claude --model <model-id> --export-web
```

On Windows (PowerShell), use `py` (or `python`) instead of `python3` and set the key with
`$env:ANTHROPIC_API_KEY = "..."`. All files are read and written as UTF-8.

The pipeline prints `OK` and exits with status 0 only if every verdict matches the expected one and, with
`--run`, every accepted plan completes in the kit; otherwise it prints `NOT OK` and exits with 1.

**Why V9 exists.** In the first run with `claude-opus-5-5` (2026-10-24) both accepted Recipes ordered configure
before tighten through `next`, but did not list configure in dep(tighten). Ordering does not make a result
available; the kit composes the input of `tighten-fasteners` from ξ restricted to dep(s), so every Session failed
at that Step. The context did not state which results each service needs, so neither the Translator nor the
Validator could know. `context.json` now states it (`requires_results`, also in the service texts the Translator
sees), and V9 checks it. The defect is kept as `drafts/defects/D8_missing_input_dependency.json`.

Every run is written to `out/<run>/<intent>/` (`round<k>.json` with interpretation, draft and findings,
`plan.json`, `dryrun.txt`, `result.json`); Claude runs also keep the raw API responses in `out/<run>/api/`.

## Files

| File | Role |
|---|---|
| `context.json` | Ctx_i: services and authorization (K_AH), engineering facts E1–E6 with machine-readable fields (K_eng), current request (K_runtime, E7), kit step ids |
| `intents.json` | The intents: I-tool (main example), I-tool-L3, I-noqc (Table 9, D6), I-wrench (Table 9, D7), with expected verdicts |
| `translator.py` | `StoredTranslator` replays `drafts/<intent>/round<k>.json`; `ClaudeTranslator` calls the Anthropic API |
| `prompts/` | System prompt and output format of the Translator |
| `validator.py` | Checks I1–I2 and V1–V8 and the decision rule |
| `compiler.py` | Recipe → `ChoreographerPlanRequestDTO` in the format of `../forklift/plan.json`; structural diff |
| `pipeline.py` | The generate–validate–revise loop, comparison with the reference plan, kit execution, web export |
| `drafts/I-tool/` | Stored drafts: round 0 with defects D1, D3, D5; round 1 with D2, D4; round 2 correct |
| `drafts/defects/` | Reference Recipe with one defect each (D1–D5 constructed; D8 observed in the first Claude run), used by the tests |
| `test_intent.py` | Unit tests |

The stored drafts are constructed for illustration (Table 9 of the paper); they are not outputs of a model.

## Recipe format

The Translator writes an abstract Recipe in the notation of the formal model, P = (name, A, α₀, next_A):

```json
{"name": "shared-tool-tightening", "first_action": "provision",
 "actions": [{"name": "provision", "next": "use", "steps": [
   {"name": "move", "service": "transport-tool", "next": ["configure"], "dep": ["reserve"],
    "executor": "agvexecutor", "params": {"to": "station"}}, "..."]}, "..."]}
```

`next` of a Step is next_α inside its Action, `dep` the data dependencies dep(s), `executor` the Executor
requirement η(s), and `params.to` distinguishes the two `transport-tool` Steps. First Steps F_α are the Steps
without a predecessor. See `prompts/output_format.md`.

## Validator checks

Precedence s ≺ s′: same Action and s′ reachable from s through next_α, or the Action of s precedes the Action of
s′ along next_A. The consumer c(s) is the Executor if η(s) is set, otherwise the Choreographer.

| Check | Condition | Fact | On failure |
|---|---|---|---|
| I1 | requested capabilities exist in σ_i; scope line exists | E7 | reject / clarify |
| I2 | the intent does not ask to omit a service mandated by the quality policy | E5 | clarify |
| V1 | α₀ ∈ A; next_A and next_α acyclic; unique names; next inside an Action; dep(s) only on Steps preceding s | | revise |
| V2 | θ(s) ∈ σ_i | | revise |
| V3 | dry-run O_i(c(s), θ(s)) ≠ ⊥ (registered and authorized) | E2 | revise |
| V4 | a transport to the station precedes configure-tool and tighten-fasteners | E3 | revise |
| V5 | no program literals; configure-tool and evaluate-tightening depend on get-program | E4 | revise |
| V6 | tighten, evaluate and record present; tighten precedes evaluate and record | E5 | revise |
| V7 | reserve ≺ every tool use ≺ release; a transport to the crib precedes release | E1 | revise |
| V8 | lease semantics declared (else clarify); every tool-using Step depends on reserve | E6 | revise / clarify |
| V9 | dep(s) contains a Step of every service whose result the input of θ(s) needs (`requires_results` in `context.json`) | interface | revise |
| V10 | the tool lease (120 s) exceeds the nominal duration of every tool-using Step | E9 | clarify |

**Preference J(W).** For an accepted Recipe the Validator also reports J(W), the estimated tool-hold time:
the time from completion of `reserve-tool` to completion of `release-tool` on the critical path over
next_A and next_α, with the nominal durations of E9 (`K_eng.timing`). J(W) does not affect admission; it ranks
admissible Recipes, as the cost function of Ladder Level 4. It is a static estimate, not a measurement: the kit
compresses durations (e.g. a 0.4 s AGV trip), and timings in single dry runs are dominated by noise.

Verdict: reject if any reject finding, else clarify if any clarify finding, else revise if any revise finding,
else accept. After `--k-max` rounds (default 3) without acceptance the verdict is reject.

## Relation to the 4.6.2 kit

The Validator follows the formal model, in which only the AGV Steps are delegated to an Executor (η(s) set,
assumption A2) and the Choreographer is the consumer of all other Steps. The 4.6.2 kit in `../forklift` runs
every Step through an Executor registered for its service definition, and the Choreographer picks that Executor by
service definition. The Compiler therefore does not emit η(s). It sets `staticParameters.step` to the kit's step
id, because the plant's input composition κ_s is keyed by it, and `staticParameters.dep` to dep(s) in kit step ids.
The kit's line controllers start the Sessions of all three lines; the intent's scope corresponds to the Session
parameters ρ that a line controller binds.
