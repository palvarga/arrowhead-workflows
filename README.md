# Arrowhead Workflow

Interactive visualizations and runnable kits for the two worked examples of the paper
*Arrowhead Workflow Choreography: a Formal Definition of Recipes and their Execution Model* (P. Varga, submitted to *Sensors*).

Both examples run on the Workflow Choreographer of
[eclipse-arrowhead/core-java-spring](https://github.com/eclipse-arrowhead/core-java-spring) release **4.6.2**,
using its executor model: every Step goes through a registered Choreographer Executor, and the Orchestrator selects
providers with the Executor as requester.

| Folder | Content |
|---|---|
| `web/temperature-control.html` | Interactive simulation of the temperature-control example (Sec. 7) |
| `web/forklift.html` | Interactive simulation of the shared-tool delivery example in a forklift assembly plant (Sec. 8) |
| `temperature-control/` | Plan, plant, Executors, setup script and offline dry run for Sec. 7 |
| `forklift/` | Plan, plant, Executors, tool lease, setup script and offline dry run for Sec. 8 |

## The web pages

Open either HTML file in a browser; no server or build step is needed. In each page you act as the scheduler: every button
is a rule instance (A-START, S-ENABLE, S-BIND, S-DONE, A-FINISH, Z-CLOSE, S-FAILBIND, S-FAILINVOKE) that applies in the
current Workflow Session state. The pages show

- the plant (room or plant floor) and which device is being called,
- the Session's Step markings, transition count and stored results ξ,
- the Arrowhead Local Cloud: core systems, Executors and application systems, with the calls of the last transition drawn as arrows,
- live checks of the paper's properties (Lemma 2, the 3|S| + 2|A| bound, progress, and in the forklift case exclusive tool use),
- disturbances that trigger the failure rules (unregistered provider, failed invocation, offline Executor).

## The kits

Python 3 standard library only. Each kit has a `dryrun.py` that runs the plan against the plant with a stand-in
Choreographer, so it can be checked before core systems are set up. See the README in each folder for registration and
run commands against real Arrowhead core systems.

Results of the dry runs:
- temperature control: 11 transitions per period; with a broken switch, S-FAILINVOKE after 9 transitions and the next period starts normally;
- forklift: three concurrent line Sessions, 33 transitions each, the tool granted in request order; with the AGV route blocked,
  the failed Session's tool lease expires and the other lines complete.
