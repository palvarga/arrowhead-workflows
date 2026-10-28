require("./model.js");
const M = globalThis.DuckModel, S = require("./scenarios.json");
let bad = 0; const tally = {};
for (const [id, spec] of Object.entries(S)) {
  const all = [["ref", spec.reference_intent, null], ...(spec.traps || []).map(t => [t.id, t.intent, t.error])];
  for (const [name, intent] of all) {
    const r = M.evaluate(spec, intent);
    const s = r.oracle ? ` S=${r.oracle.passed}/${r.oracle.total}` : "";
    console.log(`${id} ${spec.expected_outcome.padEnd(10)} ${name.padEnd(22)} ${r.outcome.padEnd(15)} ${(r.layer || "").padEnd(3)}${s}  ${String(r.reason).slice(0, 100)}`);
    if (name === "ref" && !r.good) bad++;
    if (name !== "ref") { if (r.good) bad++; const k = r.dispatched ? "executed (silent)" : "caught " + r.layer; tally[k] = (tally[k] || 0) + 1; }
  }
}
console.log("traps:", JSON.stringify(tally), "total", Object.values(tally).reduce((a, b) => a + b, 0));
console.log(bad ? `FAIL ${bad}` : "OK: every reference translation passes, every trap fails");
