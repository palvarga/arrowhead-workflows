/* Duck-cell model: ProductionIntent -> Choreographer plan -> static validation -> digital-twin dry run -> oracle.
   A JavaScript port for illustration. Station limits and formulas follow the generator's system prompt of the
   TDK 2026 cell; execution semantics follow intents/digital_twin.py (Kahn order inside an Action, Actions along
   nextActionName, plan repeated session-quantity times, station state carried over, stop at the first failing step).
   Where the prompt gives no formula (AMR and drone energy, floor coordinates, initial stock) the values are
   schematic and chosen to match the benchmark notes. It is not the cell's own digital twin; results may differ. */
(function (root) {
  "use strict";
  const num = v => (v === undefined || v === null || v === "" ? NaN : Number(v));
  const truthy = v => String(v).toLowerCase() === "true";
  const clone = o => JSON.parse(JSON.stringify(o));
  const r1 = x => Math.round(x * 10) / 10;

  // ---------------------------------------------------------------- limits and formulas
  const CNC = { programs: { DUCK_MOLD_ROUGH: 6, DUCK_MOLD_FINISH: 4, BRACKET_DRILL: 1.5, FIXTURE_PLATE: 5 },
    vc: { abs: 300, aluminium: 250, brass: 180, steel: 90, stainless: 60 }, wearLimit: 90, maxKg: 300 };
  const PAL = { perLayer: 8, base: 144, maxHeight: 1800, maxMass: 1000, maxCase: 25, defaultHeight: 200 };
  const OVEN = { windows: { waterborne: { lo: 60, hi: 90, dwell: 20 }, solvent_paint: { lo: 120, hi: 160, dwell: 20 },
      powder: { lo: 160, hi: 200, dwell: 10 }, clear_coat: { lo: 110, hi: 140, dwell: 25 }, silicone: { lo: 150, hi: 200, dwell: 30 } },
    maxKg: 100, maxParts: 200, ambient: 22, doorSafe: 60 };
  const DELTA = { maxPick: 3, maxCells: 48, defaultCapacity: 5 };
  const PAINT = { colors: ["yellow", "blue", "red", "white", "clear"], filterJobs: 12, maxKg: 13, maxArea: 20000 };
  const GANTRY = { x: 2000, y: 1000, z: 500, maxKg: 50 };
  // Floor map (m) and energy use are schematic: the prompt gives the location names, not coordinates.
  const FLOOR = { DOCK: [4, 4], WAREHOUSE: [6, 30], CONVEYOR_ENTRY: [22, 32], CONVEYOR_EXIT: [42, 32], PAINT_BOOTH: [56, 30],
    CURING_OVEN: [70, 30], CNC: [30, 6], GANTRY: [44, 6], CHECKWEIGHER: [58, 6], PALLETIZER: [72, 8], SHIPPING: [84, 18] };
  const AMR = { maxKg: 250, reserve: 15, pctPerM: 0.07, pctPerMPerKg: 0.0003 };
  // Drone: hover-inspection cost per zone chosen so that three rounds leave 23.6 % (benchmark note of X05).
  const DRONE = { maxKg: 2.5, reserve: 20, maxWind: 8, pads: ["HOME", "WAREHOUSE", "CONVEYOR_ENTRY", "PAINT_BOOTH", "CNC", "CHECKWEIGHER", "PALLETIZER", "HIGH_BAY_RACK", "ROOF_TRUSS"],
    inspect: { HIGH_BAY_RACK: 6.6, ROOF_TRUSS: 6.5, PALLETIZER: 6.2, CNC: 6.17 }, defaultInspect: 6.4, rth: 1.5, deliveryPerKg: 2, delivery: 5 };
  const wearPerPart = (program, material) => (CNC.programs[program] / 60) * (300 / CNC.vc[material]) * 100;
  const xyz = s => { const m = /X:\s*(-?[\d.]+)\s*,\s*Y:\s*(-?[\d.]+)\s*,\s*Z:\s*(-?[\d.]+)/i.exec(String(s || "")); return m ? { x: +m[1], y: +m[2], z: +m[3] } : null; };
  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
  const fdist = (a, b) => Math.hypot(FLOOR[a][0] - FLOOR[b][0], FLOOR[a][1] - FLOOR[b][1]);

  const STATION_SERVICES = {
    camera: ["vision-duck-color-detector", "vision-defect-inspection", "vision-qr-barcode-reader", "camera-stream-snapshot"],
    laser: ["optical-defect-scanner", "laser-profile-measure", "laser-engrave-marking", "laser-surface-profiler"],
    warehouse: ["warehouse-dispense-item", "warehouse-store-finished-goods", "warehouse-quarantine-store", "warehouse-inventory-query"],
    painting_robot: ["paint-spray-coat", "paint-color-change", "paint-booth-purge", "paint-reservoir-status"],
    amr: ["amr-transport-load", "amr-navigate-to", "amr-dock-charge", "amr-fleet-status"],
    drone: ["drone-aerial-delivery", "drone-aerial-inspection", "drone-return-to-home", "drone-battery-swap"],
    delta_robot: ["delta-pick-and-place", "delta-color-sort", "delta-tray-pack"],
    cartesian_robot: ["gantry-homing", "gantry-move-xyz", "gantry-pick-place"],
    cnc: ["cnc-load-program", "cnc-machine-part", "cnc-tool-change", "cnc-spindle-status"],
    curing_oven: ["oven-preheat", "oven-cure-batch", "oven-cooldown", "oven-status"],
    palletizer: ["palletizer-stack-case", "palletizer-wrap-pallet", "palletizer-pallet-change", "palletizer-status"],
    checkweigher: ["checkweigher-weigh-item", "checkweigher-calibrate", "checkweigher-tare", "checkweigher-statistics"],
  };
  const SERVICE_STATION = {};
  Object.entries(STATION_SERVICES).forEach(([k, l]) => l.forEach(s => (SERVICE_STATION[s] = k)));

  function freshState(station) {
    switch (station) {
      case "cnc": return { program: null, activeTool: 1, wear: [0, 0, 0, 0, 0, 0, 0, 0, 0], parts_machined: 0, tool_changes: 0, spindle_reads: 0, lastWearPerPart: null };
      case "palletizer": return { cases: [], wrapped: false, film_layers: 0, completed_pallets: [], cases_total: 0 };
      case "curing_oven": return { chamber_c: OVEN.ambient, door_locked: false, batches: [], batches_cured: 0, profile: [{ t: 0, c: OVEN.ambient }], clock: 0 };
      case "delta_robot": return { trays: [], total_picks: 0 };
      case "amr": return { location: "DOCK", battery_pct: 100, missions: 0, delivered_kg: 0, trips: [], path: [] };
      case "drone": return { location: "HOME", battery_pct: 100, inspections: [], swaps: [], log: [{ k: "start", pct: 100, loc: "HOME" }] };
      case "painting_robot": return { filter_jobs: 0, painted_parts: 0, purges: 0, color: null, jobs: [] };
      case "cartesian_robot": return { homed: false, position: { x: 0, y: 0, z: 0 }, travel_mm: 0, holding: null, path: [] };
      case "warehouse": {
        const stock = c => Array.from({ length: 14 }, (_, i) => `${c.toUpperCase()}-${i + 1}`);
        return { raw_inventory: { yellow: stock("y"), blue: stock("b"), red: stock("r") }, finished_goods_rack: [], quarantine: [], dispensed: 0 };
      }
      case "laser": return { engraved: [] };
      case "checkweigher": return { drift_g: 0, weighed: 0, readings: [] };
      default: return {};
    }
  }

  // ---------------------------------------------------------------- L3: step-local limits (static validator)
  function stepLimits(svc, p) {
    const kg = num(p.payload_kg);
    switch (svc) {
      case "cnc-load-program": return CNC.programs[p.program] ? null : `unknown NC program '${p.program}'`;
      case "cnc-machine-part":
        if (!CNC.vc[p.material]) return `unknown material '${p.material}'`;
        if (kg > CNC.maxKg) return `payload ${p.payload_kg} kg > ${CNC.maxKg} kg`;
        if ((p.material === "steel" || p.material === "stainless") && !truthy(p.coolant)) return `${p.material} requires coolant`;
        return null;
      case "cnc-tool-change": return num(p.tool) >= 1 && num(p.tool) <= 8 ? null : `tool pocket ${p.tool} outside 1..8`;
      case "palletizer-stack-case": {
        const c = num(p.count);
        if (!(c >= 1 && c <= 80)) return `count ${p.count} outside 1..80`;
        if (kg > PAL.maxCase) return `case mass ${p.payload_kg} kg > ${PAL.maxCase} kg`;
        return null;
      }
      case "palletizer-wrap-pallet": return num(p.film_layers) >= 1 && num(p.film_layers) <= 10 ? null : `film_layers ${p.film_layers} outside 1..10`;
      case "oven-cure-batch": {
        const w = OVEN.windows[p.coating];
        if (!w) return `unknown coating '${p.coating}'`;
        const t = num(p.temperature_c), d = num(p.dwell_min);
        if (t < w.lo || t > w.hi) return `${t} °C outside the ${p.coating} cure window ${w.lo}–${w.hi} °C`;
        if (d < w.dwell) return `dwell ${d} min < ${w.dwell} min required for ${p.coating} (under-curing)`;
        if (kg > OVEN.maxKg) return `batch ${p.payload_kg} kg > ${OVEN.maxKg} kg`;
        if (num(p.parts) > OVEN.maxParts) return `${p.parts} parts > ${OVEN.maxParts}`;
        return null;
      }
      case "oven-cooldown": return num(p.target_c) >= OVEN.ambient ? null : `target ${p.target_c} °C below ambient`;
      case "delta-tray-pack": {
        const r = num(p.rows), c = num(p.cols), cap = isNaN(num(p.tray_capacity_kg)) ? DELTA.defaultCapacity : num(p.tray_capacity_kg);
        if (!(r >= 1 && c >= 1) || r * c > DELTA.maxCells) return `tray grid ${p.rows}×${p.cols} outside 1..${DELTA.maxCells} cells`;
        if (kg > DELTA.maxPick) return `pick mass ${p.payload_kg} kg > ${DELTA.maxPick} kg (payload limit)`;
        if (r * c * kg > cap + 1e-9) return `tray mass ${r1(r * c * kg)} kg > capacity ${cap} kg`;
        return null;
      }
      case "amr-transport-load":
        if (!FLOOR[p.from] || !FLOOR[p.to]) return `unknown floor location '${!FLOOR[p.from] ? p.from : p.to}'`;
        if (kg > AMR.maxKg) return `payload ${p.payload_kg} kg > ${AMR.maxKg} kg per trip`;
        return null;
      case "amr-navigate-to": return FLOOR[p.destination] ? null : `unknown floor location '${p.destination}'`;
      case "drone-aerial-inspection":
        if (!DRONE.pads.includes(p.zone)) return `unknown flight zone '${p.zone}'`;
        return num(p.altitude_m) >= 1 && num(p.altitude_m) <= 8 ? null : `altitude ${p.altitude_m} m outside 1..8 m`;
      case "drone-aerial-delivery":
        if (!DRONE.pads.includes(p.from) || !DRONE.pads.includes(p.to)) return "unknown drone pad";
        if (kg > DRONE.maxKg) return `payload ${p.payload_kg} kg exceeds the ${DRONE.maxKg} kg drone limit`;
        if (num(p.wind_ms) > DRONE.maxWind) return `wind ${p.wind_ms} m/s > ${DRONE.maxWind} m/s`;
        return null;
      case "paint-spray-coat":
        if (!PAINT.colors.includes(p.color)) return `unknown paint colour '${p.color}'`;
        if (!(num(p.coats) >= 1 && num(p.coats) <= 5)) return `coats ${p.coats} outside 1..5`;
        if (!(num(p.film_thickness_um) >= 10 && num(p.film_thickness_um) <= 200)) return `film ${p.film_thickness_um} µm outside 10..200 µm per coat`;
        if (num(p.area_cm2) > PAINT.maxArea) return `area ${p.area_cm2} cm² > ${PAINT.maxArea} cm²`;
        if (kg > PAINT.maxKg) return `part ${p.payload_kg} kg > ${PAINT.maxKg} kg`;
        return null;
      case "gantry-pick-place": {
        const a = xyz(p.pick), b = xyz(p.place);
        if (!a || !b) return "pick/place coordinates unparseable";
        if (kg > GANTRY.maxKg) return `payload ${p.payload_kg} kg > ${GANTRY.maxKg} kg (payload limit)`;
        for (const q of [a, b]) if (q.x < 0 || q.x > GANTRY.x || q.y < 0 || q.y > GANTRY.y || q.z < 0 || q.z > GANTRY.z) return `target ${q.x},${q.y},${q.z} outside the travel range`;
        return null;
      }
      case "gantry-move-xyz": {
        const q = xyz(p.target_coordinates);
        if (!q) return "target coordinates unparseable";
        return q.x < 0 || q.x > GANTRY.x || q.y < 0 || q.y > GANTRY.y || q.z < 0 || q.z > GANTRY.z ? "target outside the travel range" : null;
      }
      case "warehouse-dispense-item": return ["yellow", "blue", "red"].includes(p.color) ? null : `colour '${p.color}' is not stocked`;
      case "laser-engrave-marking":
        if (String(p.text || "").length > 64) return "marking text longer than 64 characters";
        return num(p.power) >= 1 && num(p.power) <= 100 ? null : `laser power ${p.power} outside 1..100 %`;
      case "checkweigher-weigh-item": return num(p.nominal_kg) > 0 && num(p.nominal_kg) <= 6 ? null : `nominal ${p.nominal_kg} kg outside the 6 kg range`;
      default: return null;
    }
  }

  // ---------------------------------------------------------------- L4: stateful execution of one step
  // returns {msg, result}; throws on refusal
  function execStep(svc, st, p) {
    const R = (msg, result) => ({ msg, result: result || {} });
    switch (svc) {
      case "cnc-load-program": st.program = p.program; return R(`program ${p.program} loaded`);
      case "cnc-machine-part": {
        if (!st.program) throw new Error("no NC program loaded");
        const w = wearPerPart(st.program, p.material), cur = st.wear[st.activeTool];
        st.lastWearPerPart = w;
        if (cur + w > CNC.wearLimit + 1e-9) throw new Error(`tool T${st.activeTool} worn: wear would reach ${(cur + w).toFixed(1)} % > ${CNC.wearLimit} % (tool wear limit)`);
        st.wear[st.activeTool] = cur + w; st.parts_machined++;
        return R(`part ${st.parts_machined} machined; T${st.activeTool} wear ${st.wear[st.activeTool].toFixed(1)} %`);
      }
      case "cnc-tool-change": st.activeTool = num(p.tool); st.wear[st.activeTool] = 0; st.tool_changes++; return R(`fresh insert in T${st.activeTool}`);
      case "cnc-spindle-status": st.spindle_reads++; return R(`spindle idle, T${st.activeTool} wear ${st.wear[st.activeTool].toFixed(1)} %`);
      case "palletizer-stack-case": {
        if (st.wrapped) throw new Error("pallet is already wrapped: no more cases accepted");
        const h = isNaN(num(p.case_height_mm)) ? PAL.defaultHeight : num(p.case_height_mm), m = num(p.payload_kg), c = num(p.count);
        if (st.cases.length && st.cases[0].h !== h) throw new Error("mixed case heights on one pallet");
        const n = st.cases.length + c, layers = Math.ceil(n / PAL.perLayer), height = PAL.base + layers * h;
        const mass = st.cases.reduce((a, x) => a + x.m, 0) + c * m;
        if (height > PAL.maxHeight) throw new Error(`stack height ${height} mm > ${PAL.maxHeight} mm (${layers} layers)`);
        if (mass > PAL.maxMass) throw new Error(`pallet load ${mass} kg > ${PAL.maxMass} kg`);
        for (let i = 0; i < c; i++) st.cases.push({ h, m });
        st.cases_total += c;
        return R(`${c} case(s) stacked: ${layers} layers, ${height} mm, ${r1(mass)} kg`);
      }
      case "palletizer-wrap-pallet": st.wrapped = true; st.film_layers = num(p.film_layers); return R(`pallet wrapped with ${p.film_layers} film layers`);
      case "palletizer-pallet-change": {
        if (st.cases.length && !st.wrapped) throw new Error("a loaded pallet must be wrapped before release");
        if (st.cases.length) {
          const h = st.cases[0].h, layers = Math.ceil(st.cases.length / PAL.perLayer);
          st.completed_pallets.push({ cases: st.cases.length, height: PAL.base + layers * h, mass: st.cases.reduce((a, x) => a + x.m, 0), film: st.film_layers });
        }
        st.cases = []; st.wrapped = false; st.film_layers = 0;
        return R(`pallet released (${st.completed_pallets.length} completed)`);
      }
      case "palletizer-status": return R(`${st.cases.length} cases on the current pallet`);
      case "oven-preheat": ovenRamp(st, num(p.setpoint_c)); return R(`preheated to ${p.setpoint_c} °C`);
      case "oven-cure-batch": {
        const t = num(p.temperature_c), d = num(p.dwell_min);
        ovenRamp(st, t); st.door_locked = t > OVEN.doorSafe;
        st.clock += d; st.profile.push({ t: st.clock, c: t, dwell: true });
        st.batches.push({ coating: p.coating, temperature_c: t, dwell_min: d, parts: num(p.parts), payload_kg: num(p.payload_kg) });
        st.batches_cured++;
        return R(`${p.parts} part(s) (${p.payload_kg} kg) cured as ${p.coating} at ${t} °C for ${d} min`);
      }
      case "oven-cooldown": ovenRamp(st, num(p.target_c)); st.door_locked = st.chamber_c > OVEN.doorSafe;
        return R(`cooled to ${p.target_c} °C; door ${st.door_locked ? "locked" : "can be opened"}`);
      case "oven-status": return R(`chamber ${st.chamber_c} °C`);
      case "delta-tray-pack": {
        const r = num(p.rows), c = num(p.cols), m = num(p.payload_kg);
        const tm = Math.round(r * c * m * 100) / 100;
        st.trays.push({ rows: r, cols: c, mass: tm }); st.total_picks += r * c;
        return R(`tray ${st.trays.length}: ${r}×${c} = ${r * c} caps, ${tm} kg; ${st.total_picks} picks`);
      }
      case "amr-transport-load": {
        const kg = num(p.payload_kg), d1 = fdist(st.location, p.from), d2 = fdist(p.from, p.to);
        const need = d1 * AMR.pctPerM + d2 * (AMR.pctPerM + kg * AMR.pctPerMPerKg), back = fdist(p.to, "DOCK") * AMR.pctPerM;
        if (st.battery_pct - need - back < AMR.reserve) throw new Error(`battery ${r1(st.battery_pct)} %: the mission would strand the AMR below the ${AMR.reserve} % reserve`);
        st.path.push([st.location, p.from, 0], [p.from, p.to, kg]);
        st.battery_pct = r1(st.battery_pct - need); st.location = p.to; st.missions++; st.delivered_kg += kg;
        st.trips.push({ from: p.from, to: p.to, kg, load: p.load_id || "" });
        return R(`${kg} kg ${p.from} → ${p.to}; battery ${st.battery_pct} %`);
      }
      case "amr-navigate-to": {
        const d = fdist(st.location, p.destination); st.path.push([st.location, p.destination, 0]);
        st.battery_pct = r1(st.battery_pct - d * AMR.pctPerM); st.location = p.destination; return R(`at ${p.destination}`);
      }
      case "amr-dock-charge": {
        const d = fdist(st.location, "DOCK"); st.path.push([st.location, "DOCK", 0]);
        st.location = "DOCK"; st.battery_pct = num(p.target_pct) || 100; return R(`docked and charged to ${st.battery_pct} %`);
      }
      case "amr-fleet-status": return R(`AMR at ${st.location}, ${st.battery_pct} %`);
      case "drone-aerial-inspection": {
        const cost = DRONE.inspect[p.zone] || DRONE.defaultInspect;
        if (st.battery_pct - cost < DRONE.reserve) throw new Error(`battery ${st.battery_pct.toFixed(1)} %: inspection of ${p.zone} would go below the ${DRONE.reserve} % reserve; flight refused`);
        st.battery_pct = Math.round((st.battery_pct - cost) * 100) / 100; st.location = p.zone; st.inspections.push(p.zone);
        st.log.push({ k: "insp", pct: st.battery_pct, loc: p.zone });
        return R(`${p.zone} inspected at ${p.altitude_m} m; battery ${st.battery_pct.toFixed(1)} %`);
      }
      case "drone-return-to-home":
        st.battery_pct = Math.round((st.battery_pct - (st.location === "HOME" ? 0 : DRONE.rth)) * 100) / 100; st.location = "HOME";
        st.log.push({ k: "rth", pct: st.battery_pct, loc: "HOME" }); return R(`landed on HOME; battery ${st.battery_pct.toFixed(1)} %`);
      case "drone-battery-swap": {
        if (st.location !== "HOME") throw new Error(`battery swap only on the HOME pad (drone is at ${st.location})`);
        const prev = st.battery_pct; st.swaps.push(prev); st.battery_pct = 100; st.log.push({ k: "swap", pct: 100, loc: "HOME", prev });
        return R(`battery swapped (${prev.toFixed(1)} % → 100 %)`, { previousPct: prev });
      }
      case "drone-aerial-delivery": {
        const kg = num(p.payload_kg), cost = DRONE.delivery + kg * DRONE.deliveryPerKg;
        if (st.battery_pct - cost < DRONE.reserve) throw new Error("battery reserve would be violated; flight refused");
        st.battery_pct = r1(st.battery_pct - cost); st.location = p.to; st.log.push({ k: "deliv", pct: st.battery_pct, loc: p.to, kg });
        return R(`${kg} kg flown ${p.from} → ${p.to}`);
      }
      case "paint-spray-coat": {
        if (st.filter_jobs >= PAINT.filterJobs) throw new Error(`booth exhaust filter saturated after ${PAINT.filterJobs} spray jobs: purge required`);
        st.filter_jobs++; st.painted_parts++; st.color = p.color;
        st.jobs.push({ coats: num(p.coats), film: num(p.film_thickness_um) });
        const ml = num(p.area_cm2) * num(p.film_thickness_um) * num(p.coats) * 1e-4 / 0.65;
        return R(`part ${st.painted_parts}: ${p.coats} × ${p.film_thickness_um} µm ${p.color}, ${r1(ml)} ml; filter ${st.filter_jobs}/${PAINT.filterJobs}`);
      }
      case "paint-booth-purge": { const n = st.filter_jobs; st.filter_jobs = 0; st.purges++; return R(`booth purged (${n} jobs cleared)`, { jobsCleared: n }); }
      case "paint-color-change": st.color = p.color; return R(`flushed to ${p.color}`);
      case "gantry-homing":
        if (st.holding) throw new Error("homing refused while holding a load");
        st.travel_mm += dist(st.position, { x: 0, y: 0, z: 0 }); st.position = { x: 0, y: 0, z: 0 }; st.homed = true; st.path.push({ x: 0, y: 0, z: 0, k: "home" });
        return R("reference run done");
      case "gantry-pick-place": {
        if (!st.homed) throw new Error("axes not homed: homing required before any motion");
        const a = xyz(p.pick), b = xyz(p.place);
        st.travel_mm += dist(st.position, a) + dist(a, b); st.position = b;
        st.path.push(Object.assign({ k: "pick" }, a), Object.assign({ k: "place", kg: num(p.payload_kg) }, b));
        return R(`${p.payload_kg} kg moved ${p.pick} → ${p.place}; travel ${Math.round(st.travel_mm)} mm`);
      }
      case "gantry-move-xyz": {
        if (!st.homed) throw new Error("axes not homed: homing required before any motion");
        const q = xyz(p.target_coordinates); st.travel_mm += dist(st.position, q); st.position = q; st.path.push(Object.assign({ k: "move" }, q));
        return R(`at ${q.x},${q.y},${q.z}; travel ${Math.round(st.travel_mm)} mm`);
      }
      case "warehouse-dispense-item": {
        const bin = st.raw_inventory[p.color];
        if (!bin || !bin.length) throw new Error(`colour '${p.color}' out of stock`);
        bin.pop(); st.dispensed++; return R(`one ${p.color} duck dispensed (${bin.length} left)`);
      }
      case "warehouse-store-finished-goods": st.finished_goods_rack.push({ itemId: p.item_id || "", slot: p.slot || "" }); return R(`stored as '${p.item_id || ""}'`);
      case "warehouse-quarantine-store": st.quarantine.push({ itemId: p.item_id || "", reason: p.reason || "" }); return R("quarantined");
      case "warehouse-inventory-query": return R("inventory read");
      case "laser-engrave-marking": st.engraved.push(p.text); return R(`'${p.text}' engraved at ${p.power} %`);
      case "checkweigher-weigh-item": {
        if (st.drift_g > 2) throw new Error("load-cell drift > 2 g: recalibration required");
        const n = num(p.count) || 1, gross = num(p.nominal_kg) + st.drift_g / 1000, dev = (gross / num(p.nominal_kg) - 1) * 100;
        const cls = Math.abs(dev) <= (num(p.tolerance_pct) || 0) ? "OK" : dev < 0 ? "UNDER" : "OVER";
        st.drift_g = Math.round((st.drift_g + 0.02 * n) * 1000) / 1000; st.weighed += n; st.readings.push(cls);
        if (cls !== "OK" && truthy(p.fail_on_reject)) throw new Error(`item ${cls} tolerance: quality gate`);
        return R(`${n} item(s) weighed: ${cls} (simulated reading = nominal + drift)`, { classification: cls });
      }
      default: return R("ok");
    }
  }
  // Ramps are schematic (heating 5 °C/min, cooling 4 °C/min); only the plateau values are checked.
  function ovenRamp(st, target) {
    const rate = target >= st.chamber_c ? 5 : 4;
    st.clock += Math.abs(target - st.chamber_c) / rate; st.chamber_c = target; st.profile.push({ t: st.clock, c: target });
  }

  // ---------------------------------------------------------------- compiler: ProductionIntent -> plan
  function compile(intent) {
    const ops = intent.operations || [];
    const preds = ops.map((o, i) => (Array.isArray(o.after) ? o.after.slice() : i > 0 ? [ops[i - 1].id] : []));
    const steps = ops.map(o => ({
      name: o.id, quantity: 1, serviceRequirement: { serviceDefinitionRequirement: o.service },
      staticParameters: Object.fromEntries(Object.entries(o.parameters || {}).map(([k, v]) => [k, String(v)])),
      nextStepNames: ops.filter((_, j) => preds[j].includes(o.id)).map(x => x.id),
    }));
    return { name: intent.intentId || "intent", firstActionName: "main", sessionQuantity: Math.max(1, num(intent.target && intent.target.batchSize) || 1),
      actions: [{ name: "main", firstStepNames: ops.filter((_, i) => !preds[i].length).map(o => o.id), steps }], preds: Object.fromEntries(ops.map((o, i) => [o.id, preds[i]])) };
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
  const svcOf = s => s.serviceRequirement.serviceDefinitionRequirement;

  // ---------------------------------------------------------------- L1 + L2 + L3: static validation
  function validateStatic(plan) {
    const errors = [];
    plan.actions.forEach(a => {
      const names = a.steps.map(s => s.name);
      names.forEach((n, i) => { if (!n) errors.push({ layer: "L1", msg: "empty step name" }); else if (names.indexOf(n) !== i) errors.push({ layer: "L1", msg: `duplicate step '${n}'` }); });
      a.steps.forEach(s => (s.nextStepNames || []).forEach(n => { if (!names.includes(n)) errors.push({ layer: "L1", msg: `dangling successor '${n}'` }); }));
      const t = topoSteps(a);
      if (t.unreachable.length) errors.push({ layer: "L1", msg: `cycle or unreachable steps: ${t.unreachable.join(", ")}` });
      a.steps.forEach(s => {
        const svc = svcOf(s);
        if (!SERVICE_STATION[svc]) errors.push({ layer: "L2", step: s.name, service: svc, msg: `no workstation provides '${svc}'` });
        const e = stepLimits(svc, s.staticParameters);
        if (e) errors.push({ layer: "L3", step: s.name, service: svc, msg: e });
      });
      // station precondition the static validator can see in the plan: gantry homing before the first motion
      const ord = t.order, firstMove = ord.findIndex(s => /^gantry-(pick-place|move-xyz)$/.test(svcOf(s)));
      if (firstMove >= 0 && !ord.slice(0, firstMove).some(s => svcOf(s) === "gantry-homing"))
        errors.push({ layer: "L3", step: ord[firstMove].name, service: svcOf(ord[firstMove]), msg: "gantry motion before homing: homing is required once before any motion" });
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
          const svc = svcOf(s), station = SERVICE_STATION[svc];
          const rec = { iteration: it, step: s.name, service: svc, station, params: s.staticParameters };
          if (!station) { rec.status = "ERROR"; rec.message = `No provider for service '${svc}'`; }
          else {
            states[station] = states[station] || freshState(station);
            const before = clone(states[station]);
            try { const o = execStep(svc, states[station], s.staticParameters); rec.message = o.msg; rec.result = o.result; rec.status = "SUCCESS"; }
            catch (e) { rec.message = e.message; rec.status = "ERROR"; states[station] = before; }
            rec.snapshot = clone(states);
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

  // ---------------------------------------------------------------- L6: benchmark oracle
  function getPath(obj, path) {
    let cur = obj;
    for (const part of String(path).split(".")) { if (cur === null || cur === undefined) return undefined; cur = Array.isArray(cur) && /^-?\d+$/.test(part) ? cur[+part < 0 ? cur.length + +part : +part] : cur[part]; }
    return cur;
  }
  function cmp(actual, c) {
    if ("len" in c) return actual !== undefined && actual !== null && actual.length === c.len;
    if ("equals" in c) return typeof c.equals === "number" ? Math.abs(Number(actual) - c.equals) <= (c.tol || 1e-6) : String(actual).toLowerCase() === String(c.equals).toLowerCase();
    let ok = actual !== undefined && actual !== null;
    if ("min" in c) ok = ok && Number(actual) >= c.min;
    if ("max" in c) ok = ok && Number(actual) <= c.max;
    return ok;
  }
  const want = c => ("len" in c ? `has ${c.len} entries` : "equals" in c ? `= ${c.equals}` : ["min" in c ? `≥ ${c.min}` : "", "max" in c ? `≤ ${c.max}` : ""].filter(Boolean).join(" "));
  const show = v => (Array.isArray(v) ? `${v.length} entries` : typeof v === "number" ? +v.toFixed(3) : v === undefined ? "–" : v);

  function oracle(spec, plan, run) {
    const checks = [], add = (label, ok, detail) => checks.push({ label, ok: !!ok, detail: detail || "" });
    const steps = plan.actions.flatMap(a => a.steps), q = plan.sessionQuantity;
    const by = Object.fromEntries(steps.map(s => [s.name, s]));
    const execs = svc => steps.filter(s => svcOf(s) === svc).flatMap(s => Array(s.quantity * q).fill(s.staticParameters));
    const using = svc => steps.filter(s => svcOf(s) === svc);
    const reach = (a, b) => { const seen = new Set(), st = [...by[a].nextStepNames];
      while (st.length) { const n = st.pop(); if (n === b) return true; if (!seen.has(n)) { seen.add(n); st.push(...(by[n] ? by[n].nextStepNames : [])); } } return false; };
    const count = {}; (spec.required_services || []).forEach(s => (count[s] = (count[s] || 0) + 1));
    Object.entries(count).forEach(([s, n]) => { const k = using(s).length; add(`uses ${s}${n > 1 ? " ×" + n : ""}`, k >= n, `${k} step(s)`); });
    (spec.forbidden_services || []).forEach(s => add(`does not use ${s}`, !using(s).length));
    (spec.forbidden_together || []).forEach(g => add(`not all of ${g.join(" + ")}`, !g.every(s => using(s).length), g.every(s => using(s).length) ? "both branches run unconditionally" : ""));
    if (spec.session_quantity !== undefined) add(`session quantity ${spec.session_quantity}`, q === spec.session_quantity, `is ${q}`);
    Object.entries(spec.exact_counts || {}).forEach(([s, n]) => { const k = execs(s).length; add(`${s} executed exactly ${n}×`, k === n, `executed ${k}×`); });
    (spec.order || []).forEach(([a, b]) => add(`some ${a} before some ${b}`, using(a).some(x => using(b).some(y => reach(x.name, y.name)))));
    (spec.strict_order || []).forEach(([a, b]) => { const A = using(a), B = using(b); add(`every ${a} before every ${b}`, A.length && B.length && A.every(x => B.every(y => reach(x.name, y.name)))); });
    (spec.parallel || []).forEach(([a, b]) => { const A = using(a), B = using(b);
      add(`${a} ∥ ${b} (no ordering path)`, A.length && B.length && A.every(x => B.every(y => !reach(x.name, y.name) && !reach(y.name, x.name))), A.length && B.length ? "" : "service missing"); });
    Object.entries(spec.params || {}).forEach(([s, kv]) => add(`${s} has ${fmt(kv)}`, execs(s).some(p => match(p, kv))));
    Object.entries(spec.params_all || {}).forEach(([s, kv]) => { const e = execs(s), bad = e.filter(p => !match(p, kv));
      add(`every ${s} has ${fmt(kv)}`, e.length && !bad.length, bad.length ? `differs: ${fmt(bad[0], Object.keys(kv))}` : ""); });
    Object.entries(spec.param_counts || {}).forEach(([s, list]) => list.forEach(pc => { const k = execs(s).filter(p => match(p, pc.params)).length; add(`${pc.count}× ${s} with ${fmt(pc.params)}`, k === pc.count, `found ${k}×`); }));
    Object.entries(spec.param_values || {}).forEach(([s, kv]) => Object.entries(kv).forEach(([k, vals]) => {
      const got = execs(s).map(p => String(p[k] ?? "")).sort(), exp = vals.map(String).sort();
      add(`${s}.${k} values = {${vals.join(", ")}}`, JSON.stringify(got) === JSON.stringify(exp), `got {${got.join(", ")}}`); }));
    (spec.aggregates || []).forEach(g => {
      const v = execs(g.service).map(p => num(p[g.param])), r = !v.length ? NaN : g.op === "sum" ? v.reduce((a, b) => a + b, 0) : g.op === "max" ? Math.max(...v) : Math.min(...v);
      add(`${g.op} of ${g.service}.${g.param} ${want(g)}`, !isNaN(r) && cmp(r, g), `${g.op} = ${isNaN(r) ? "–" : +r.toFixed(3)}`); });
    const tw = spec.twin;
    if (tw) {
      add(`twin status ${tw.status}`, run.status === tw.status, run.failed ? run.failed.message : "");
      if (tw.first_service) add(`first executed step uses ${tw.first_service}`, run.trace.length && run.trace[0].service === tw.first_service, run.trace.length ? run.trace[0].service : "");
      (tw.param_sequence || []).forEach(c => { const seq = run.trace.filter(r => r.service === c.service).map(r => String(r.params[c.param] ?? ""));
        add(`execution order of ${c.service}.${c.param}`, JSON.stringify(seq) === JSON.stringify(c.equals.map(String)), seq.length > 6 ? `${seq.length} values` : seq.join(", ")); });
      (tw.step_results || []).forEach(c => { const vals = run.trace.filter(r => r.service === c.service && r.status === "SUCCESS").map(r => getPath(r.result, c.path));
        const sel = c.which === "last" ? vals.slice(-1) : vals, good = sel.map(v => cmp(v, c));
        const ok = vals.length && (c.which === "any" ? good.some(Boolean) : good.every(Boolean));
        add(`${c.which || "all"} ${c.service} result ${c.path} ${want(c)}`, ok, vals.length ? `${vals.map(v => show(v)).join(", ")}` : "no such step"); });
      (tw.final_state || []).forEach(c => { const st = run.states[c.station] || freshState(c.station), v = getPath(st, c.path);
        add(`final ${c.station}.${c.path} ${want(c)}`, cmp(v, c), `is ${show(v)}`); });
    }
    const passed = checks.filter(c => c.ok).length;
    return { checks, score: checks.length ? passed / checks.length : 1, passed, total: checks.length };
  }
  const match = (p, kv) => Object.entries(kv).every(([k, v]) => String(p[k]).toLowerCase() === String(v).toLowerCase());
  const fmt = (kv, keys) => (keys || Object.keys(kv)).map(k => `${k}=${kv[k]}`).join(", ");

  // ---------------------------------------------------------------- whole chain for one translation
  // Outcome classes as in Table 5.4 of the TDK paper (the reviewer L5 and the human gate L7 are not modelled).
  function evaluate(spec, intent) {
    const expected = spec.expected_outcome || "ACCEPT";
    const plan = compile(intent), stat = validateStatic(plan);
    let refusal = null, run = null;
    if (stat.length) refusal = { layer: stat[0].layer, reason: stat[0].msg, step: stat[0].step };
    else { run = runTwin(plan); if (run.status !== "SUCCESS") refusal = { layer: "L4", reason: run.failed.message, step: run.failed.step }; }
    const base = { plan, static: stat, run, expected };
    if (refusal) {
      if (expected === "REJECT") {
        const kws = spec.reject_keywords || [], hit = !kws.length || kws.some(k => refusal.reason.toLowerCase().includes(k.toLowerCase()));
        return Object.assign(base, { outcome: "CORRECT_REFUSAL", layer: refusal.layer, step: refusal.step, reason: refusal.reason, score: hit ? 1 : 0.5, good: true, dispatched: false,
          note: hit ? `the error names ${kws.join(" / ")}` : `but the error does not name ${kws.join(" / ")}` });
      }
      if (expected === "LIMITATION") return Object.assign(base, { outcome: "CORRECT_FLAG", layer: refusal.layer, step: refusal.step, reason: refusal.reason, good: true, dispatched: false, score: 1 });
      return Object.assign(base, { outcome: "NOT_ACCEPTED", layer: refusal.layer, step: refusal.step, reason: refusal.reason, good: false, dispatched: false, failure: "safe" });
    }
    if (expected === "REJECT") return Object.assign(base, { outcome: "FALSE_ACCEPT", layer: null, reason: "an infeasible request was dispatched; it silently drops or changes what was asked", good: false, dispatched: true, score: 0, failure: "silent" });
    const or = oracle(spec, plan, run), ok = or.score >= 0.999;
    if (expected === "LIMITATION") return Object.assign(base, { oracle: or, outcome: ok ? "ACCEPTABLE_PLAN" : "WRONG_PLAN", good: ok, dispatched: true, score: or.score, layer: ok ? null : "L6", failure: ok ? null : "silent",
      reason: ok ? "dispatched plan the oracle accepts (gate instead of a branch)" : or.checks.filter(c => !c.ok).map(c => c.label).join("; ") });
    return Object.assign(base, { oracle: or, outcome: ok ? "SUCCESS" : "WRONG_PLAN", good: ok, dispatched: true, score: or.score, layer: ok ? null : "L6", failure: ok ? null : "silent",
      reason: ok ? "plan meets the intent" : or.checks.filter(c => !c.ok).map(c => c.label).join("; ") });
  }

  root.DuckModel = { compile, validateStatic, runTwin, oracle, evaluate, freshState, topoSteps, wearPerPart, xyz, svcOf,
    CNC, PAL, OVEN, DELTA, PAINT, GANTRY, FLOOR, AMR, DRONE, SERVICE_STATION, STATION_SERVICES };
})(typeof window !== "undefined" ? window : globalThis);
