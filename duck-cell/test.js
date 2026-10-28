require("./model.js");
const M = globalThis.DuckModel, S = require("./scenarios.json");
let bad = 0;
for (const [id, spec] of Object.entries(S)) {
  const all = [["gold", spec.reference_intent, null], ...spec.traps.map(t => [t.id, t.intent, t.error])];
  for (const [name, intent, err] of all) {
    const r = M.evaluate(spec, intent);
    const s = r.oracle ? ` S=${r.oracle.passed}/${r.oracle.total}` : "";
    console.log(`${id} ${name.padEnd(22)} ${r.outcome.padEnd(13)} ${r.layer || "  "}${s}  ${r.reason.slice(0, 110)}`);
    if (name === "gold" && r.outcome !== "SUCCESS") bad++;
    if (name !== "gold" && r.outcome === "SUCCESS") bad++;
  }
}
console.log(bad ? `FAIL ${bad}` : "OK: gold SUCCESS, no trap passes");
