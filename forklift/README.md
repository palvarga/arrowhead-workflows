# Arrowhead Workflow: shared-tool forklift example on Eclipse Arrowhead 4.6.2

"Shared Tool Delivery in a Forklift Assembly Plant") on the Workflow
Choreographer of `eclipse-arrowhead/core-java-spring` release **4.6.2**. Endpoint paths and
DTO fields were checked against the 4.6.2 sources (`CommonConstants.java`, `CoreSystemService.java`,
`Choreographer*DTO.java`, `ChoreographerService.java`, `ChoreographerDriver.java`, `ChoreographerPlanController.java`).

| File | Role |
|---|---|
| `plan.json` | Recipe W as a `ChoreographerPlanRequestDTO`: 3 Actions, 9 Steps, `nextStepNames` = next_α, `staticParameters` = {step id, dep(s)} |
| `plant.py` | Application Systems of the forklift example, the `session-context` service, one Executor per service definition, line-controller notify endpoints, `trace.jsonl` |
| `setup_and_run.py` | Registers everything and acts as the line controllers (`start <k>`) |
| `dryrun.py` | Stand-in Choreographer + Orchestrator with the same executor contract; no Arrowhead needed |

## 0. Try it without Arrowhead

```
python3 dryrun.py          # L2, then L1 and L3 queue for NR7: 3 × 33 transitions, tool granted in FIFO order
python3 dryrun.py --fail   # AGV route blocked during L2: S-FAILINVOKE after 9 transitions,
                           # L2's lease expires after 1.5 s, L1 and L3 then complete with 33 transitions each
```

## 1. How the concept is used

**Every Step through an Executor.** Each service definition has a registered Executor (`POST /choreographer/executor/register`).
For a Step, the Choreographer asks the Executor `POST <executor>/service-info` for its dependencies, orchestrates the main
service and every dependency **with the Executor as requester**, and calls `POST <executor>/start` with
`mainOrchestrationResult` and `preconditionOrchestrationResults`. The Executor reports
`POST /choreographer/executor/notify-step-done` with `SUCCESS` or `FATAL_ERROR`.

**ξ and ρ through a registered service.** `sessionctx` provides `session-context` (registered in the Service Registry, guarded by
authorization rules). Every Executor declares it as a dependency, so its address arrives in `preconditionOrchestrationResults`:

| Call | Meaning |
|---|---|
| `POST /session-context/<sid>/rho` | line controller binds ρ to the Session |
| `GET  /session-context/<sid>/rho?wait=30` | Executor reads ρ; waits until it is bound |
| `GET  /session-context/<sid>/xi?keys=a,b` | Executor reads ξ restricted to dep(s); 409 if one is missing (Lemma 2) |
| `POST /session-context/<sid>/xi/<step>` | Executor stores d′ before it sends notify-step-done |

The 4.6.2 client start returns the `sessionId` and then starts the Session asynchronously over JMS, so ρ can only be bound
after the Session exists. The waiting read makes that race harmless.

**Client start.** A line controller knows only the Orchestrator. It orchestrates the core service `choreographer-start-session`
(`POST /choreographer/session/start`, body `ChoreographerRunPlanRequestByClientDTO` with the plan `name`) and `session-context`,
starts the plan and binds ρ. Its notify port receives the `ChoreographerNotificationDTO` at the end.

**Tool lease.** ReserveTool answers at once with `granted` (token, `leaseUntil`) or `queued` (ticket). The reserve Executor keeps
its Step running and polls the ticket. The tool-using Executors (`agvexecutor`, `exconfigure`, `extighten`) declare a second
dependency, `renew-tool-lease`, and renew with ξ(res) before every physical use; a Step without a valid lease fails. A lease that is
neither renewed nor released expires and the next queued line is granted. To read ξ(res), `res` was added to dep of cfg, tight and ret;
res ≺ s holds for all of them, so (W4) is still met. Choose the lease longer than the longest single tool Step.

**Authorization (A2).** Rules are created per consumer: each Executor → its own service, `session-context` and its declared
dependencies; each line controller → `session-context` and `choreographer-start-session`. Only `agvexecutor` may consume `transport-tool`.

## 2. Run on Arrowhead

Download `arrowhead-{serviceregistry,authorization,orchestrator,choreographer}-4.6.2.zip` from the release page (or use the
`aitiaiiot/arrowhead-system` image from the wiki *Docker* page). For a first run set `server.ssl.enabled=false` in all four
`application.properties`. Ports: Service Registry 8443, Orchestrator 8441, Authorization 8445, Choreographer 8457.

```
python3 plant.py --chor http://<core-host>:8457 --lease 120
python3 setup_and_run.py setup   --plant-host <plant-ip> --sr http://<core-host>:8443 --auth http://<core-host>:8445 --chor http://<core-host>:8457
python3 setup_and_run.py start 2 --plant-host <plant-ip> --orch http://<core-host>:8441
python3 setup_and_run.py start 1 --plant-host <plant-ip> --orch http://<core-host>:8441   # while L2 holds NR7
python3 setup_and_run.py block on --plant-host <plant-ip>                                # failure case
```

Monitoring: `GET /choreographer/mgmt/session`, `/mgmt/session/steps`, `/mgmt/worklog`; plant side `trace.jsonl`.
Untested against live core systems: the dry run uses a stand-in. Points to watch on the first live run: whether
`/serviceregistry/query` is open to the setup script in your mode, and the interface name the core services register
(`HTTP-INSECURE-JSON` in insecure mode).

