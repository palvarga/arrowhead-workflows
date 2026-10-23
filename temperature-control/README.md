# Arrowhead Workflow: temperature-control example on Eclipse Arrowhead 4.6.2

Runs the simple demonstration of Sec. 7: one Action, the Steps read → evaluate → switch, threshold ρ_thr = 23 °C.
One control period is one Workflow Session, started by the application system `controlclock`.

| File | Role |
|---|---|
| `plan.json` | Recipe as a `ChoreographerPlanRequestDTO`: Action `control`, Steps `read`, `evaluate`, `switch` |
| `plant.py` | `temperaturesensor`, `temperaturecontroller`, `heatingswitch`, the `session-context` service, Executors `exread`, `exevaluate`, `exswitch`, a simple room model, `trace.jsonl` |
| `setup_and_run.py` | Registers everything and acts as `controlclock` (`start`, `run N --every s`) |
| `dryrun.py` | Stand-in Choreographer + Orchestrator with the same executor contract; no Arrowhead needed |

## Try it without Arrowhead

```
python3 dryrun.py          # 8 periods: 24.6 °C → OFF, the room cools, the heating cycles around 23 °C; 11 transitions each
python3 dryrun.py --fail   # from period 4 the switch does not answer: S-FAILINVOKE after 9 transitions per period
```

## Run on Arrowhead

Core systems and insecure mode as in `../forklift/README.md`. Then:

```
python3 plant.py --chor http://<core-host>:8457
python3 setup_and_run.py setup --plant-host <plant-ip> --sr http://<core-host>:8443 --auth http://<core-host>:8445 --chor http://<core-host>:8457
python3 setup_and_run.py run 20 --every 10 --plant-host <plant-ip> --orch http://<core-host>:8441
python3 setup_and_run.py window on --plant-host <plant-ip>
python3 setup_and_run.py brokenswitch on --plant-host <plant-ip>
```

Per Step, the Choreographer asks the Executor for its dependencies (`session-context`), orchestrates the main service and the
dependency with the Executor as requester, and calls `POST <executor>/start`. The Executor reads ρ and ξ restricted to dep(s),
composes the input (κ_read = (), κ_eval = (ξ(read), ρ_thr), κ_act = ξ(eval)), calls the provider, stores ξ(s) and sends
`notify-step-done`. `controlclock` starts each period through the client endpoint `choreographer-start-session` and binds ρ
to the new Session.
