import test from "node:test";
import assert from "node:assert/strict";
import { createStore } from "../../app/ui/state.js";

const ID = "0123456789abcdef0123456789abcdef";
const memory = () => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), m }; };
const broken = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); }, removeItem() { throw new Error("denied"); } };

test("the store starts at the first step and merges patches", () => {
  const s = createStore(null);
  assert.equal(s.get().step, "enter");
  s.set({ step: "verify", domain: "x.test" });
  assert.deepEqual([s.get().step, s.get().domain], ["verify", "x.test"]);
});

test("subscribers hear about changes and can unsubscribe", () => {
  const s = createStore(null);
  const seen = [];
  const off = s.subscribe((st) => seen.push(st.step));
  s.set({ step: "auditing" });
  off();
  s.set({ step: "report" });
  assert.deepEqual(seen, ["auditing"]);
});

test("the report id is kept in storage and in memory, but never in the rendered state", () => {
  const st = memory();
  const s = createStore(st);
  s.saveReportId(ID);
  assert.equal(s.getReportId(), ID);
  assert.equal(st.m.size, 1);
  assert.ok(!JSON.stringify(s.get()).includes(ID));
  s.set({ reportId: "must-be-ignored", report: { site: "x" } });
  assert.ok(!JSON.stringify(s.get()).includes("must-be-ignored"));
  assert.equal(s.getReportId(), ID);
  assert.equal(createStore(st).loadReportId(), ID);
  s.clearReportId();
  assert.equal(s.getReportId(), null);
  assert.equal(st.m.size, 0);
});

test("a malformed id is neither saved nor loaded", () => {
  const st = memory();
  const s = createStore(st);
  s.saveReportId("../../x");
  assert.equal(s.getReportId(), null);
  assert.equal(st.m.size, 0);
  st.setItem("agentready.report", "<script>");
  assert.equal(createStore(st).loadReportId(), null);
});

test("storage that is missing or throws never breaks the store", () => {
  for (const storage of [null, undefined, broken]) {
    const s = createStore(storage);
    s.saveReportId(ID);
    assert.equal(s.getReportId(), ID);          // still held in memory for this page view
    assert.equal(createStore(storage).loadReportId(), null);
    s.clearReportId();
    assert.equal(s.getReportId(), null);
  }
});

test("the state object handed out cannot be used to change the store", () => {
  const s = createStore(null);
  const copy = s.get();
  copy.step = "hacked";
  assert.equal(s.get().step, "enter");
});
