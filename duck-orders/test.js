require("./model.js");
const M = globalThis.DuckModel, S = require("./orders.json");
let bad = 0;
for (const [id, spec] of Object.entries(S)) {
  for (const [name, intent] of [["ref", spec.reference_intent], ...spec.traps.map(t => [t.id, t.intent])]) {
    const r = M.evaluate(spec, intent);
    const s = r.oracle ? ` S=${r.oracle.passed}/${r.oracle.total}` : "", ms = r.run && r.run.makespan !== undefined ? ` makespan ${r.run.makespan.toFixed(1)}` : "";
    console.log(`${id} ${name.padEnd(18)} ${r.outcome.padEnd(13)} ${(r.layer || "").padEnd(3)}${s}${ms}  ${String(r.reason).slice(0, 150)}`);
    if (name === "ref" ? !r.good : r.good) bad++;
  }
}
const g = M.evaluate(S.O2, S.O2.reference_intent);
g.run.trace.forEach(r => console.log("  ", r.session, r.step.padEnd(10), r.start.toFixed(1), "→", r.end.toFixed(1), r.waited ? "waited " + r.waited.toFixed(1) : ""));
console.log(bad ? `FAIL ${bad}` : "OK: every reference passes, every trap fails");
