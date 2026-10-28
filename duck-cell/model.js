/* Duck-cell model: ProductionIntent -> Choreographer plan -> static validation -> digital-twin dry run -> oracle.
   A JavaScript port for illustration. Station limits and formulas follow the generator's system prompt of the
   TDK 2026 cell; execution semantics follow intents/digital_twin.py (Kahn order inside an Action, Actions along
   nextActionName, plan repeated session-quantity times, station state carried over, stop at the first failing step).
   It is not the cell's own digital twin; results may differ from it. */
(function (root) {
  "use strict";
  const num = v => (v === undefined || v === null || v === "" ? NaN : Number(v));
  const truthy = v => String(v).toLowerCase() === "true";
  const clone = o => JSON.parse(JSON.stringify(o));

  // ---------------------------------------------------------------- stations: limits from the system prompt
  const CNC = {
    programs: { DUCK_MOLD_ROUGH: 6, DUCK_MOLD_FINISH: 4, BRACKET_DRILL: 1.5, FIXTURE_PLATE: 5 },   // cycle min
    vc: { abs: 300, aluminium: 250, brass: 180, steel: 90, stainless: 60 },                       // m/min
    wearLimit: 90, maxKg: 300,
  };
  const PAL = { perLayer: 8, base: 144, maxHeight: 1800, maxMass: 1000, maxCase: 25 };
  const OVEN = {
    windows: {
      waterborne: { lo: 60, hi: 90, dwell: 20 }, solvent_paint: { lo: 120, hi: 160, dwell: 20 },
      powder: { lo: 160, hi: 200, dwell: 10 }, clear_coat: { lo: 110, hi: 140, dwell: 25 },
      silicone: { lo: 150, hi: 200, dwell: 30 },
    },
    maxKg: 100, maxParts: 200, ambient: 22, doorSafe: 60,
  };
  const wearPerPart = (program, material) => (CNC.programs[program] / 60) * (300 / CNC.vc[material]) * 100;

  const SERVICE_STATION = {
    "cnc-load-program": "cnc", "cnc-machine-part": "cnc", "cnc-tool-change": "cnc", "cnc-spindle-status": "cnc",
    "palletizer-stack-case": "palletizer", "palletizer-wrap-pallet": "palletizer",
    "palletizer-pallet-change": "palletizer", "palletizer-status": "palletizer",
    "oven-preheat": "curing_oven", "oven-cure-batch": "curing_oven", "oven-cooldown": "curing_oven", "oven-status": "curing_oven",
  };

  function freshState(station) {
    if (station === "cnc") return { program: null, activeTool: 1, wear: [0, 0, 0, 0, 0, 0, 0, 0, 0], parts_machined: 0, tool_changes: 0, spindle_reads: 0 };
    if (station === "palletizer") return { cases: [], wrapped: false, film_layers: 0, completed_pallets: [], cases_total: 0 };
    if (station === "curing_oven") return { chamber_c: OVEN.ambient, door_locked: false, batches: [], profile: [{ t: 0, c: OVEN.ambient }], clock: 0 };
    return {};
  }

  // ---------------------------------------------------------------- L3: step-local limits (static validator)
  function stepLimits(svc, p) {
    switch (svc) {
      case "cnc-load-program": return CNC.programs[p.program] ? null : `unknown NC program '${p.program}'`;
      case "cnc-machine-part": {
        if (!CNC.vc[p.material]) return `unknown material '${p.material}'`;
        if (num(p.payload_kg) > CNC.maxKg) return `payload ${p.payload_kg} kg > ${CNC.maxKg} kg`;
        if ((p.material === "steel" || p.material === "stainless") && !truthy(p.coolant)) return `${p.material} requires coolant`;
        return null;
      }
      case "cnc-tool-change": return num(p.tool) >= 1 && num(p.tool) <= 8 ? null : `tool pocket ${p.tool} outside 1..8`;
      case "palletizer-stack-case": {
        const c = num(p.count);
        if (!(c >= 1 && c <= 80)) return `count ${p.count} outside 1..80`;
        if (num(p.payload_kg) > PAL.maxCase) return `case mass ${p.payload_kg} kg > ${PAL.maxCase} kg`;
        if (!(num(p.case_height_mm) > 0)) return "case_height_mm missing";
        return null;
      }
      case "palletizer-wrap-pallet": return num(p.film_layers) >= 1 && num(p.film_layers) <= 10 ? null : `film_layers ${p.film_layers} outside 1..10`;
      case "oven-cure-batch": {
        const w = OVEN.windows[p.coating];
        if (!w) return `unknown coating '${p.coating}'`;
        const t = num(p.temperature_c), d = num(p.dwell_min);
        if (t < w.lo || t > w.hi) return `${t} °C outside the ${p.coating} cure window ${w.lo}–${w.hi} °C`;
        if (d < w.dwell) return `dwell ${d} min < ${w.dwell} min required for ${p.coating} (under-curing)`;
        if (num(p.payload_kg) > OVEN.maxKg) return `batch ${p.payload_kg} kg > ${OVEN.maxKg} kg`;
        if (num(p.parts) > OVEN.maxParts) return `${p.parts} parts > ${OVEN.maxParts}`;
        return null;
      }
      case "oven-cooldown": return num(p.target_c) >= OVEN.ambient ? null : `target ${p.target_c} °C below ambient`;
      default: return null;
    }
  }

  // ---------------------------------------------------------------- L4: stateful execution of one step
  function execStep(svc, st, p) {
    switch (svc) {
      case "cnc-load-program": st.program = p.program; return `program ${p.program} loaded`;
      case "cnc-machine-part": {
        if (!st.program) throw new Error("no NC program loaded");
        const w = wearPerPart(st.program, p.material), cur = st.wear[st.activeTool];
        if (cur + w > CNC.wearLimit + 1e-9)
          throw new Error(`tool T${st.activeTool} wear would reach ${(cur + w).toFixed(1)} % > ${CNC.wearLimit} % (tool wear limit)`);
        st.wear[st.activeTool] = cur + w; st.parts_machined++;
        return `part ${st.parts_machined} machined; T${st.activeTool} wear ${st.wear[st.activeTool].toFixed(1)} %`;
      }
      case "cnc-tool-change": st.activeTool = num(p.tool); st.wear[st.activeTool] = 0; st.tool_changes++;
        return `fresh insert in T${st.activeTool}`;
      case "cnc-spindle-status": st.spindle_reads++; return `spindle idle, T${st.activeTool} wear ${st.wear[st.activeTool].toFixed(1)} %`;
      case "palletizer-stack-case": {
        if (st.wrapped) throw new Error("pallet is already wrapped: no more cases accepted");
        const h = num(p.case_height_mm), m = num(p.payload_kg), c = num(p.count);
        if (st.cases.length && st.cases[0].h !== h) throw new Error("mixed case heights on one pallet");
        const n = st.cases.length + c, layers = Math.ceil(n / PAL.perLayer), height = PAL.base + layers * h;
        const mass = st.cases.reduce((a, x) => a + x.m, 0) + c * m;
        if (height > PAL.maxHeight) throw new Error(`stack height ${height} mm > ${PAL.maxHeight} mm (${layers} layers)`);
        if (mass > PAL.maxMass) throw new Error(`pallet load ${mass} kg > ${PAL.maxMass} kg`);
        for (let i = 0; i < c; i++) st.cases.push({ h, m });
        st.cases_total += c;
        return `${c} cases stacked: ${layers} layers, ${height} mm, ${mass} kg`;
      }
      case "palletizer-wrap-pallet": st.wrapped = true; st.film_layers = num(p.film_layers); return `pallet wrapped with ${p.film_layers} film layers`;
      case "palletizer-pallet-change": {
        if (st.cases.length && !st.wrapped) throw new Error("a loaded pallet must be wrapped before release");
        if (st.cases.length) {
          const h = st.cases[0].h, layers = Math.ceil(st.cases.length / PAL.perLayer);
          st.completed_pallets.push({ cases: st.cases.length, height: PAL.base + layers * h, mass: st.cases.reduce((a, x) => a + x.m, 0), film: st.film_layers });
        }
        st.cases = []; st.wrapped = false; st.film_layers = 0;
        return `pallet released (${st.completed_pallets.length} completed)`;
      }
      case "palletizer-status": return `${st.cases.length} cases on the current pallet`;
      case "oven-preheat": ovenRamp(st, num(p.setpoint_c)); return `preheated to ${p.setpoint_c} °C`;
      case "oven-cure-batch": {
        const t = num(p.temperature_c), d = num(p.dwell_min);
        ovenRamp(st, t); st.door_locked = t > OVEN.doorSafe;
        st.clock += d; st.profile.push({ t: st.clock, c: t, dwell: true });
        st.batches.push({ coating: p.coating, temperature_c: t, dwell_min: d, parts: num(p.parts), payload_kg: num(p.payload_kg) });
        return `${p.parts} parts (${p.payload_kg} kg) cured as ${p.coating} at ${t} °C for ${d} min`;
      }
      case "oven-cooldown": ovenRamp(st, num(p.target_c)); st.door_locked = st.chamber_c > OVEN.doorSafe;
        return `cooled to ${p.target_c} °C; door ${st.door_locked ? "locked" : "can be opened"}`;
      case "oven-status": return `chamber ${st.chamber_c} °C`;
      default: return "ok";
    }
  }
  // Ramps are schematic (heating 5 °C/min, cooling 4 °C/min); only the plateau values are checked.
  function ovenRamp(st, target) {
    const rate = target >= st.chamber_c ? 5 : 4;
    st.clock += Math.abs(target - st.chamber_c) / rate;
    st.chamber_c = target; st.profile.push({ t: st.clock, c: target });
  }

  // ---------------------------------------------------------------- compiler: ProductionIntent -> plan
  function compile(intent) {
    const ops = intent.operations || [];
    const preds = ops.map((o, i) => (Array.isArray(o.after) ? o.after.slice() : i > 0 ? [ops[i - 1].id] : []));
    const steps = ops.map((o, i) => ({
      name: o.id, quantity: 1,
      serviceRequirement: { serviceDefinitionRequirement: o.service },
      staticParameters: Object.fromEntries(Object.entries(o.parameters || {}).map(([k, v]) => [k, String(v)])),
      nextStepNames: ops.filter((_, j) => preds[j].includes(o.id)).map(x => x.id),
    }));
    return {
      name: intent.intentId || "intent", firstActionName: "main", sessionQuantity: Math.max(1, num(intent.target && intent.target.batchSize) || 1),
      actions: [{ name: "main", firstStepNames: ops.filter((_, i) => !preds[i].length).map(o => o.id), steps }],
    };
  }

  function topoSteps(action) {                     // same rule as _topological_steps in digital_twin.py
    const steps = action.steps || [], by = Object.fromEntries(steps.map(s => [s.name, s]));
    const indeg = Object.fromEntries(steps.map(s => [s.name, 0]));
    steps.forEach(s => (s.nextStepNames || []).forEach(n => { if (n in indeg) indeg[n]++; }));
    const ready = (action.firstStepNames || []).filter(n => by[n] && indeg[n] === 0), order = [], seen = new Set();
    while (ready.length) {
      const n = ready.shift(); if (seen.has(n)) continue;
      seen.add(n); order.push(by[n]);
      (by[n].nextStepNames || []).forEach(m => { if (m in indeg && --indeg[m] === 0) ready.push(m); });
    }
    return { order: order.concat(steps.filter(s => !seen.has(s.name))), unreachable: steps.filter(s => !seen.has(s.name)).map(s => s.name) };
  }

  // ---------------------------------------------------------------- L1 + L3: static validation
  function validateStatic(plan) {
    const errors = [];
    plan.actions.forEach(a => {
      const names = a.steps.map(s => s.name);
      names.forEach((n, i) => { if (!n) errors.push({ layer: "L1", msg: "empty step name" }); else if (names.indexOf(n) !== i) errors.push({ layer: "L1", msg: `duplicate step '${n}'` }); });
      a.steps.forEach(s => (s.nextStepNames || []).forEach(n => { if (!names.includes(n)) errors.push({ layer: "L1", msg: `dangling successor '${n}'` }); }));
      const t = topoSteps(a);
      if (t.unreachable.length) errors.push({ layer: "L1", msg: `cycle or unreachable steps: ${t.unreachable.join(", ")}` });
      a.steps.forEach(s => {
        const svc = s.serviceRequirement.serviceDefinitionRequirement;
        if (!SERVICE_STATION[svc]) errors.push({ layer: "L2", step: s.name, msg: `no workstation provides '${svc}'` });
        const e = stepLimits(svc, s.staticParameters);
        if (e) errors.push({ layer: "L3", step: s.name, service: svc, msg: e });
      });
    });
    return errors;
  }

  // ---------------------------------------------------------------- L4: digital-twin dry run with snapshots
  function runTwin(plan) {
    const states = {}, trace = [], actions = Object.fromEntries(plan.actions.map(a => [a.name, a]));
    let status = "SUCCESS", failed = null, iterations = 0;
    outer: for (let it = 1; it <= plan.sessionQuantity; it++) {
      let cur = plan.firstActionName; const visited = new Set();
      while (actions[cur]) {
        if (visited.has(cur)) { status = "FAILED"; failed = { step: cur, message: "Action cycle" }; break outer; }
        visited.add(cur);
        for (const s of topoSteps(actions[cur]).order) {
          const svc = s.serviceRequirement.serviceDefinitionRequirement, station = SERVICE_STATION[svc];
          const rec = { iteration: it, step: s.name, service: svc, station, params: s.staticParameters };
          if (!station) { rec.status = "ERROR"; rec.message = `No provider for service '${svc}'`; }
          else {
            states[station] = states[station] || freshState(station);
            const before = clone(states[station]);
            try { rec.message = execStep(svc, states[station], s.staticParameters); rec.status = "SUCCESS"; }
            catch (e) { rec.message = e.message; rec.status = "ERROR"; states[station] = before; }
            rec.state = clone(states[station]);
          }
          trace.push(rec);
          if (rec.status !== "SUCCESS") { status = "FAILED"; failed = rec; break outer; }
        }
        cur = actions[cur].nextActionName;
      }
      iterations = it;
    }
    return { status, failed, trace, states, iterations };
  }

  // ---------------------------------------------------------------- L6: benchmark oracle (subset used by X01/X03/X08)
  function oracle(spec, plan, run) {
    const checks = [], add = (label, ok, detail) => checks.push({ label, ok: !!ok, detail: detail || "" });
    const steps = plan.actions.flatMap(a => a.steps), q = plan.sessionQuantity;
    const svcOf = s => s.serviceRequirement.serviceDefinitionRequirement;
    const execs = svc => steps.filter(s => svcOf(s) === svc).flatMap(s => Array(s.quantity * q).fill(s.staticParameters));
    const reach = (a, b) => { // path a -> b inside the Action DAG
      const by = Object.fromEntries(steps.map(s => [s.name, s])), seen = new Set(), st = [...by[a].nextStepNames];
      while (st.length) { const n = st.pop(); if (n === b) return true; if (!seen.has(n)) { seen.add(n); st.push(...(by[n] ? by[n].nextStepNames : [])); } }
      return false;
    };
    const count = {}; (spec.required_services || []).forEach(s => (count[s] = (count[s] || 0) + 1));
    Object.entries(count).forEach(([s, n]) => { const k = steps.filter(x => svcOf(x) === s).length; add(`uses ${s}${n > 1 ? " ×" + n : ""}`, k >= n, `${k} step(s)`); });
    (spec.forbidden_services || []).forEach(s => add(`does not use ${s}`, !steps.some(x => svcOf(x) === s)));
    Object.entries(spec.exact_counts || {}).forEach(([s, n]) => { const k = execs(s).length; add(`${s} executed exactly ${n}×`, k === n, `executed ${k}×`); });
    (spec.strict_order || []).forEach(([a, b]) => {
      const A = steps.filter(s => svcOf(s) === a), B = steps.filter(s => svcOf(s) === b);
      add(`every ${a} before every ${b}`, A.length && B.length && A.every(x => B.every(y => reach(x.name, y.name))));
    });
    Object.entries(spec.params || {}).forEach(([s, kv]) => add(`${s} has ${fmt(kv)}`, execs(s).some(p => match(p, kv))));
    Object.entries(spec.params_all || {}).forEach(([s, kv]) => {
      const e = execs(s), bad = e.filter(p => !match(p, kv));
      add(`every ${s} has ${fmt(kv)}`, e.length && !bad.length, bad.length ? `differs: ${fmt(bad[0], Object.keys(kv))}` : "");
    });
    Object.entries(spec.param_counts || {}).forEach(([s, list]) => list.forEach(pc => {
      const k = execs(s).filter(p => match(p, pc.params)).length; add(`${pc.count}× ${s} with ${fmt(pc.params)}`, k === pc.count, `found ${k}×`);
    }));
    (spec.aggregates || []).forEach(g => {
      const v = execs(g.service).map(p => num(p[g.param])), r = !v.length ? NaN : g.op === "sum" ? v.reduce((a, b) => a + b, 0) : g.op === "max" ? Math.max(...v) : Math.min(...v);
      const ok = ("equals" in g ? Math.abs(r - g.equals) <= (g.tol || 1e-6) : true) && ("min" in g ? r >= g.min : true) && ("max" in g ? r <= g.max : true);
      const want = "equals" in g ? `= ${g.equals}` : "min" in g ? `≥ ${g.min}` : `≤ ${g.max}`;
      add(`${g.op} of ${g.service}.${g.param} ${want}`, ok, `${g.op} = ${isNaN(r) ? "–" : +r.toFixed(3)}`);
    });
    if (spec.twin) {
      add(`twin status ${spec.twin.status}`, run.status === spec.twin.status, run.failed ? run.failed.message : "");
      (spec.twin.final_state || []).forEach(c => {
        const st = run.states[c.station] || freshState(c.station), v = st[c.path];
        const ok = "len" in c ? v && v.length === c.len : String(v) === String(c.equals);
        add(`final ${c.station}.${c.path} ${"len" in c ? "has " + c.len + " entries" : "= " + c.equals}`, ok, `is ${"len" in c ? (v ? v.length : 0) + " entries" : v}`);
      });
    }
    const passed = checks.filter(c => c.ok).length;
    return { checks, score: checks.length ? passed / checks.length : 1, passed, total: checks.length };
  }
  const match = (p, kv) => Object.entries(kv).every(([k, v]) => String(p[k]).toLowerCase() === String(v).toLowerCase());
  const fmt = (kv, keys) => (keys || Object.keys(kv)).map(k => `${k}=${kv[k]}`).join(", ");

  // ---------------------------------------------------------------- whole chain for one translation
  function evaluate(spec, intent) {
    const plan = compile(intent), stat = validateStatic(plan);
    if (stat.length) {
      return { plan, static: stat, run: null, oracle: null, outcome: "NOT_ACCEPTED", layer: stat[0].layer, failure: "safe",
        reason: stat[0].msg, step: stat[0].step };
    }
    const run = runTwin(plan);
    if (run.status !== "SUCCESS") {
      return { plan, static: [], run, oracle: null, outcome: "NOT_ACCEPTED", layer: "L4", failure: "safe", reason: run.failed.message, step: run.failed.step };
    }
    const or = oracle(spec, plan, run), ok = or.score >= 0.999;
    return { plan, static: [], run, oracle: or, outcome: ok ? "SUCCESS" : "WRONG_PLAN", layer: ok ? null : "L6", failure: ok ? null : "silent",
      reason: ok ? "plan meets the intent" : or.checks.filter(c => !c.ok).map(c => c.label).join("; ") };
  }

  root.DuckModel = { compile, validateStatic, runTwin, oracle, evaluate, freshState, topoSteps, wearPerPart, CNC, PAL, OVEN, SERVICE_STATION };
})(typeof window !== "undefined" ? window : globalThis);
