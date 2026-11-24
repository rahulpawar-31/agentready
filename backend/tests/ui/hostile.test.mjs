// Every field of every API body gets every hostile or odd value, and the whole flow is driven through the real
// start(). Whatever the server sends, the page must not throw, must not get stuck on an in-progress message, must
// keep the address form usable and must only contain elements and attributes the views are allowed to make.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createFakeDocument, byTag, byText, findAll } from "./fake-dom.mjs";
import { start } from "../../app/ui/app.js";

const RID = "0123456789abcdef0123456789abcdef";
const SECTIONS = ["step-url", "step-verify", "step-report", "step-fixes"];
const CSS = fs.readFileSync(new URL("../../app/ui/app.css", import.meta.url), "utf8");
const UNSTYLED_UNTIL_GROUP_B = ["chip-not-detected", "chip-confidence", "chip-proposed"];
const CSS_CLASSES = new Set([...CSS.matchAll(/\.([a-zA-Z][\w-]*)/g)].map((m) => m[1]).concat(UNSTYLED_UNTIL_GROUP_B));
const STRINGS = ["<img src=x onerror=alert(1)>", "\"><script>alert(1)</script>", "javascript:alert(1)", "</pre><b>x</b>", "<!--", "]]>",
  "‮⁦abc", "a​b", "x\u0000y", "a\nb\rc d e", "diff-add chip chip-detected", "' onmouseover='x", "__proto__",
  "constructor", "toString", "hasOwnProperty", "valueOf", "x".repeat(20000), "", " ", "{{7*7}}", "${7*7}", "%s%n"];
const OTHER = [null, undefined, 0, -1, NaN, Infinity, 1e308, true, false, {}, [], [1, 2], { toString() { return "TS"; } }, { a: { b: 1 } },
  "5", [[[]]], { toString() { throw new Error("no text"); } }];
const VALUES = [...STRINGS, ...OTHER];

const reply = (status, body) => ({ status, ok: status < 300, async json() { return body; } });
const INSTR = { domain: "x.test", token: "tok", meta_tag: '<meta name="agentready-verification" content="tok">', meta_tag_placement: "inside <head>",
  dns_txt_name: "_agentready.x.test", dns_txt_value: "agentready-verify=tok" };
const FINDING = { check_id: "llms_txt", category: "discoverability", passed: false, weight: 2, check: "GET /llms.txt", evidence: "returned 404",
  url: "https://x.test/llms.txt", confidence: "high", status: "not detected" };
const REPORT = { site: "https://x.test", score: 72, score_status: "scored", unscored: 1, category_scores: { discoverability: 40 }, findings: [FINDING] };
const FIX = { id: "llms_txt", kind: "llms_txt", path: "/llms.txt", filename: "llms.txt", description: "d", finding_id: "llms_txt", status: "proposed",
  diff: "--- /dev/null\n+++ b/llms.txt\n+# x\n", diff_kind: "full", warning: "w", note: "n", sha256: "a".repeat(64) };
const base = () => ({ report: structuredClone(REPORT), fixes: { fixes: [structuredClone(FIX)], skipped: [{ kind: "json_ld", reason: "r" }] }, instr: structuredClone(INSTR) });

function setAt(root, path, value) {
  const keys = path.split(".");
  let o = root;
  for (const k of keys.slice(0, -1)) o = o[k];
  o[keys.at(-1)] = value;
}

const PATHS = ["report.site", "report.score", "report.score_status", "report.unscored", "report.category_scores", "report.findings",
  ...Object.keys(FINDING).map((k) => `report.findings.0.${k}`), ...Object.keys(FIX).map((k) => `fixes.fixes.0.${k}`),
  "fixes.skipped.0.kind", "fixes.skipped.0.reason", "fixes.skipped", "fixes.fixes", "fixes", ...Object.keys(INSTR).map((k) => `instr.${k}`), "instr"];

const ALLOWED_TAGS = new Set(["div", "span", "p", "h1", "h2", "h3", "ul", "li", "button", "input", "label", "details", "summary", "pre", "code", "section", "form", "strong", "progress"]);
const ALLOWED_ATTRS = new Set(["class", "id", "type", "hidden", "tabindex", "role", "for", "open", "value", "placeholder", "autocomplete", "spellcheck", "max", "disabled", "data-fix-status"]);
function violations(root) {
  const bad = [];
  for (const n of findAll(root, () => true)) {
    if (!ALLOWED_TAGS.has(n.tagName)) bad.push(`tag ${n.tagName}`);
    for (const [k, v] of n.attributes) {
      if (!ALLOWED_ATTRS.has(k) && !/^aria-[a-z-]+$/.test(k)) bad.push(`attribute ${k}`);
      if (k === "class") for (const c of v.split(/\s+/)) if (!CSS_CLASSES.has(c)) bad.push(`class ${c}`);
      if (k === "data-fix-status" && !["proposed", "approved", "rejected", "unknown"].includes(v)) bad.push(`status ${v}`);
    }
  }
  return bad;
}

async function trial(label, mutate) {
  const doc = createFakeDocument();
  const els = {};
  for (const id of ["status", ...SECTIONS]) {
    const e = doc.createElement(id === "status" ? "div" : "section");
    e.setAttribute("id", id);
    if (id !== "status" && id !== "step-url") e.setAttribute("hidden", "");
    doc.body.appendChild(e);
    els[id] = e;
  }
  const bodies = base();
  mutate(bodies);
  const fetch = async (path, init = {}) => {
    const key = `${init.method} ${path}`;
    if (key.startsWith("GET /ownership-token")) return reply(200, bodies.instr);
    if (key === "POST /audit") return reply(200, { id: RID, ...bodies.report });
    if (key === `GET /report/${RID}`) return reply(200, bodies.report);
    if (key === `POST /report/${RID}/fixes` || key === `GET /report/${RID}/fixes`) return reply(200, bodies.fixes);
    if (/\/(approve|reject)$/.test(key)) return reply(200, { ...FIX, status: key.endsWith("approve") ? "approved" : "rejected" });
    if (key.endsWith("/export")) return reply(200, { filename: "llms.txt", content: "# x\n", diff: "", sha256: "", note: "" });
    return reply(404, { detail: "Not Found" });
  };
  const app = await start({ document: doc, fetch, storage: null, clipboard: { writeText: async () => {} }, downloadBlob: () => {}, now: () => new Date(0) });
  const click = async (id, text) => { const [b] = byText(els[id], "button", text); if (b) { b.click(); await app.idle(); } };
  const [input] = byTag(els["step-url"], "input");
  input.value = "x.test";
  byTag(els["step-url"], "form")[0].dispatch("submit");
  await app.idle();
  await click("step-verify", "Verify and audit");
  await click("step-report", "Propose fixes");
  for (const text of ["Approve", "Download", "Copy", "Revoke approval"]) await click("step-fixes", text);
  const problems = [];
  for (const id of SECTIONS) for (const v of violations(els[id])) problems.push(`${id}: ${v}`);
  const msg = els["status"].textContent;
  if (/Checking your site|Preparing fixes/.test(msg)) problems.push(`status stuck: ${msg}`);
  if (byTag(els["step-url"], "input").length !== 1) problems.push("the address form is gone");
  for (const b of findAll(doc.body, (n) => n.tagName === "button")) if (b.disabled) problems.push("a button stayed disabled");
  return problems.map((p) => `${label}: ${p}`);
}

test("every field of every API body can be hostile or odd and the page stays usable (about 1,500 trials)", async () => {
  const unhandled = [];
  const onRejection = (e) => unhandled.push(String(e && e.message));
  process.on("unhandledRejection", onRejection);
  const failures = [];
  let trials = 0;
  try {
    for (const path of PATHS) {
      for (const value of VALUES) {
        trials += 1;
        const label = `${path} = ${typeof value === "string" ? JSON.stringify(value.slice(0, 24)) : typeof value === "object" && value !== null ? "[object]" : String(value)}`;
        try { failures.push(...(await trial(label, (b) => setAt(b, path, value)))); }
        catch (e) { failures.push(`${label}: THREW ${String(e && e.message)}`); }
      }
    }
  } finally {
    process.off("unhandledRejection", onRejection);
  }
  assert.ok(trials > 1400, `ran ${trials} trials`);
  assert.deepEqual(unhandled, []);
  assert.deepEqual(failures.slice(0, 10), [], `${failures.length} failures`);
});
