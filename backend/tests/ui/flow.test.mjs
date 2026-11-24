import test from "node:test";
import assert from "node:assert/strict";
import { allText, byTag, byText, createFakeDocument, findAll, serialize } from "./fake-dom.mjs";
import { makeDownloader, start } from "../../app/ui/app.js";

const RID = "0123456789abcdef0123456789abcdef";
const SECTIONS = ["step-url", "step-verify", "step-report", "step-fixes"];
const ALLOWED = [/^GET \/ownership-token\?domain=[A-Za-z0-9.%-]+$/, /^POST \/audit$/, new RegExp(`^GET /report/${RID}$`),
  new RegExp(`^(GET|POST) /report/${RID}/fixes$`), new RegExp(`^POST /report/${RID}/fixes/[a-z_]+/(approve|reject)$`),
  new RegExp(`^GET /report/${RID}/fixes/[a-z_]+/export$`)];
const INSTRUCTIONS = { domain: "x.test", token: "tok", meta_tag: '<meta name="agentready-verification" content="tok">',
  meta_tag_placement: "inside <head> on the home page", dns_txt_name: "_agentready.x.test", dns_txt_value: "agentready-verify=tok" };
const REPORT = { site: "https://x.test", score: 72, score_status: "scored", unscored: 0, category_scores: { discoverability: 40 },
  findings: [{ check_id: "llms_txt", category: "discoverability", passed: false, weight: 2, check: "GET /llms.txt", evidence: "returned 404",
               url: "https://x.test/llms.txt", confidence: "high", status: "not detected" }] };
const CONTENT = "# Acme\n> Handmade shoes.\n";
const mkFix = (id, status = "proposed") => ({ id, kind: id, path: id === "llms_txt" ? "/llms.txt" : "/", filename: id === "llms_txt" ? "llms.txt" : "jsonld-snippet.html",
  description: "d", finding_id: id, status, diff: `--- /dev/null\n+++ b/${id}\n+${id}\n`, diff_kind: "full", warning: "", note: "", sha256: "a".repeat(64) });

const reply = (status, body) => ({ status, ok: status < 300, async json() { return body; } });
const within = (promise, ms, what) => Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error(`timed out waiting for ${what}`)), ms).unref())]);
const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };
function fakeTimer() {
  const timers = [];
  return { timers, set(fn, ms) { timers.push({ fn, ms, live: true }); return timers.length; },
    clear(id) { if (timers[id - 1]) timers[id - 1].live = false; },
    fire(ms) { for (const t of timers) if (t.live && t.ms === ms) { t.live = false; t.fn(); } } };
}

function server(opts = {}) {
  const calls = [];
  const fixes = { llms_txt: mkFix("llms_txt"), json_ld: mkFix("json_ld") };
  const s = { calls, fixes, audits: [], verified: false, ...opts };
  s.fetch = async (path, init = {}) => {
    const key = `${init.method} ${path}`;
    calls.push({ key, path, init });
    if (s.networkDown) throw new TypeError("down");
    if (key.startsWith("GET /ownership-token")) return reply(200, INSTRUCTIONS);
    if (key === "POST /audit") {
      s.audits.push(JSON.parse(init.body));
      if (!s.verified) return reply(403, { detail: INSTRUCTIONS });
      return reply(200, { id: RID, ...(s.report ?? REPORT) });
    }
    if (key === `GET /report/${RID}`) return s.reportStatus ? reply(s.reportStatus, { detail: "Unknown report" }) : reply(200, s.report ?? REPORT);
    if (key === `POST /report/${RID}/fixes`) return reply(200, { fixes: Object.values(fixes), skipped: [] });
    if (key === `GET /report/${RID}/fixes`) return s.noFixes ? reply(404, { detail: "No fixes proposed yet" }) : reply(200, { fixes: Object.values(fixes), skipped: [] });
    let m = /^POST \/report\/[0-9a-f]{32}\/fixes\/([a-z_]+)\/(approve|reject)$/.exec(key);
    if (m) {
      if (s.conflict) return reply(409, { detail: s.conflictDetail ?? "SECRET server text" });
      if (s.lie) return reply(200, { ...fixes[m[1]], status: s.lie });
      fixes[m[1]].status = m[2] === "approve" ? "approved" : "rejected";
      return reply(200, fixes[m[1]]);
    }
    m = /^GET \/report\/[0-9a-f]{32}\/fixes\/([a-z_]+)\/export$/.exec(key);
    if (m) return fixes[m[1]].status === "approved" ? reply(200, { filename: s.exportName ?? fixes[m[1]].filename, content: CONTENT, diff: "d", sha256: "a".repeat(64), note: "n" })
      : reply(409, { detail: "Approve this fix before exporting it" });
    return reply(404, { detail: "Not Found" });
  };
  return s;
}

function page() {
  const doc = createFakeDocument();
  const els = {};
  for (const id of ["status", ...SECTIONS]) {
    const e = doc.createElement(id === "status" ? "div" : "section");
    e.setAttribute("id", id);
    if (id !== "status" && id !== "step-url") e.setAttribute("hidden", "");
    doc.body.appendChild(e);
    els[id] = e;
  }
  return { doc, els };
}
const memory = () => { const m = new Map(); return { m, getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) }; };
const broken = { getItem() { throw new Error("no"); }, setItem() { throw new Error("no"); }, removeItem() { throw new Error("no"); } };

async function boot(opts = {}) {
  const { doc, els } = page();
  const srv = opts.server ?? server(opts.serverOpts);
  const downloads = [];
  const copied = [];
  const clipboard = opts.clipboard === undefined ? { writeText: async (t) => { copied.push(t); } } : opts.clipboard;
  const storage = opts.storage === undefined ? memory() : opts.storage;
  const app = await start({ document: doc, fetch: srv.fetch, storage, clipboard, timer: opts.timer,
    downloadBlob: (blob, name) => downloads.push({ blob, name }), win: opts.win ?? null, now: opts.now ?? (() => new Date(Date.UTC(2026, 10, 24, 9, 5, 7))) });
  if (!opts.noWait) await app.idle();
  return { doc, els, srv, app, downloads, copied, storage };
}
const status = (els) => els["status"].textContent;
const shown = (els, id) => !els[id].hasAttribute("hidden");
const button = (els, id, text) => { const [b] = byText(els[id], "button", text); assert.ok(b, `button "${text}" in ${id}`); return b; };
async function enter(t, value = "x.test") {
  const [input] = byTag(t.els["step-url"], "input");
  input.value = value;
  byTag(t.els["step-url"], "form")[0].dispatch("submit");
  await t.app.idle();
}
async function click(t, id, text) { button(t.els, id, text).click(); await t.app.idle(); }
async function toReport(t) { await enter(t); t.srv.verified = true; await click(t, "step-verify", "Verify and audit"); }
const allSerialized = (els) => SECTIONS.map((id) => serialize(els[id])).join("") + serialize(els["status"]);

test("(a) an address that is not a site is refused with no request", async () => {
  const t = await boot();
  await enter(t, "not a url");
  assert.ok(status(t.els).includes("does not look like a site address"));
  assert.equal(t.srv.calls.length, 0);
  assert.ok(!shown(t.els, "step-verify"));
});

test("(b) a valid address shows the verification steps and makes no audit request yet", async () => {
  const t = await boot();
  await enter(t, "https://X.test/path");
  assert.deepEqual(t.srv.calls.map((c) => c.key), ["GET /ownership-token?domain=x.test"]);
  assert.ok(shown(t.els, "step-verify"));
  assert.ok(allText(t.els["step-verify"]).includes("Prove you control the site"));
  assert.ok(allText(t.els["step-verify"]).includes(INSTRUCTIONS.meta_tag));
  assert.equal(t.doc.activeElement.getAttribute("id"), "verify-heading");
});

test("(c) an unverified site keeps the instructions and says what to do; the button works again", async () => {
  const t = await boot();
  await enter(t);
  await click(t, "step-verify", "Verify and audit");
  assert.ok(status(t.els).includes("could not find the verification tag or DNS record yet"));
  assert.ok(shown(t.els, "step-verify") && !shown(t.els, "step-report"));
  assert.equal(button(t.els, "step-verify", "Verify and audit").hasAttribute("disabled"), false);
  assert.deepEqual(t.srv.audits, [{ url: "https://x.test", snapshot: "default" }]);
});

test("(d) after verification the report is shown and the report id stays out of the page", async () => {
  const t = await boot();
  await enter(t);
  await click(t, "step-verify", "Verify and audit");
  t.srv.verified = true;
  await click(t, "step-verify", "Verify and audit");
  assert.ok(shown(t.els, "step-report") && !shown(t.els, "step-verify"));
  assert.ok(allText(t.els["step-report"]).includes("72"));
  assert.equal(t.doc.activeElement.getAttribute("id"), "report-heading");
  assert.deepEqual([...t.storage.m.values()], [RID]);
  assert.ok(!allSerialized(t.els).includes(RID), "the report id must not appear in any rendered node or message");
  assert.equal(t.doc.location, undefined);
});

test("(e) proposing fixes shows Approve and no Download", async () => {
  const t = await boot();
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  assert.ok(shown(t.els, "step-fixes"));
  assert.equal(byText(t.els["step-fixes"], "button", "Approve").length, 2);
  assert.equal(byText(t.els["step-fixes"], "button", "Download").length, 0);
});

test("(f) approve, then download gives the exact exported text under a safe file name", async () => {
  const t = await boot();
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  assert.equal(byText(t.els["step-fixes"], "button", "Download").length, 0);
  await click(t, "step-fixes", "Approve");                       // the first card: llms_txt
  assert.deepEqual(t.srv.calls.at(-1).key, `POST /report/${RID}/fixes/llms_txt/approve`);
  assert.equal(byText(t.els["step-fixes"], "button", "Download").length, 1);
  assert.equal(byText(t.els["step-fixes"], "button", "Approve").length, 1, "the other fix is still waiting");
  assert.equal(t.downloads.length, 0, "approval alone exports nothing");
  assert.equal(t.srv.calls.filter((c) => c.key.endsWith("/export")).length, 0);
  await click(t, "step-fixes", "Download");
  assert.equal(t.srv.calls.filter((c) => c.key.endsWith("/export")).length, 1);
  assert.equal(t.downloads.length, 1);
  assert.equal(t.downloads[0].name, "llms.txt");
  assert.equal(await t.downloads[0].blob.text(), CONTENT);
  assert.ok(status(t.els).includes("AgentReady has not changed it"));
  assert.ok(!allSerialized(t.els).includes("Handmade shoes"), "exported content is not rendered into the page");
});

test("(g) copy writes the exported text, or says copy is unavailable", async () => {
  const t = await boot();
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  await click(t, "step-fixes", "Approve");
  await click(t, "step-fixes", "Copy");
  assert.deepEqual(t.copied, [CONTENT]);
  assert.ok(status(t.els).includes("Copied. Nothing has been changed on your site."));
  const u = await boot({ clipboard: null });
  await toReport(u);
  await click(u, "step-report", "Propose fixes");
  await click(u, "step-fixes", "Approve");
  await click(u, "step-fixes", "Copy");
  assert.ok(status(u.els).includes("Copy is not available in this browser. Use Download instead."));
  const v = await boot({ clipboard: { writeText: async () => { throw new Error("denied"); } } });
  await toReport(v);
  await click(v, "step-report", "Propose fixes");
  await click(v, "step-fixes", "Approve");
  await click(v, "step-fixes", "Copy");
  assert.ok(status(v.els).includes("Copy is not available"));
});

test("revoking an approval removes Download and Copy again", async () => {
  const t = await boot();
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  await click(t, "step-fixes", "Approve");
  await click(t, "step-fixes", "Revoke approval");
  assert.equal(t.srv.calls.at(-1).key, `POST /report/${RID}/fixes/llms_txt/reject`);
  assert.equal(byText(t.els["step-fixes"], "button", "Download").length, 0);
});

test("(h) a conflict shows the server's short reason as plain text, with advice only when the fix was rejected", async () => {
  const t = await boot();
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  t.srv.conflict = true;
  t.srv.conflictDetail = "The fix changed after it was approved; approve it again";
  await click(t, "step-fixes", "Approve");
  assert.ok(status(t.els).includes("The server did not allow that: The fix changed after it was approved; approve it again"), status(t.els));
  assert.ok(!status(t.els).includes("Run a new audit"), "no advice that does not apply");
  assert.equal(byText(t.els["step-fixes"], "button", "Approve").length, 2, "nothing changed");
  t.srv.conflictDetail = "a rejected fix cannot be approved again; the only way back is a new audit";
  await click(t, "step-fixes", "Approve");
  assert.ok(status(t.els).endsWith("Run a new audit to start again."), status(t.els));
  t.srv.conflictDetail = "<img src=x onerror=alert(1)>" + "y".repeat(500);
  await click(t, "step-fixes", "Approve");
  assert.ok(status(t.els).includes("<img"), "it is shown as text");
  assert.ok(!serialize(t.els["status"]).includes("<img"), "and escaped");
  assert.ok(status(t.els).length < 300, String(status(t.els).length));
});

test("(i) re-audit sends a fresh rerun- snapshot, replaces the report and clears the fixes", async () => {
  const t = await boot();
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  await click(t, "step-report", "Re-audit with fresh pages");
  const last = t.srv.audits.at(-1);
  assert.equal(last.snapshot, "rerun-20261124090507");
  assert.match(last.snapshot, /^[A-Za-z0-9_-]{1,64}$/);
  assert.equal(last.url, "https://x.test");
  assert.ok(!shown(t.els, "step-fixes"));
  assert.equal(t.els["step-fixes"].children.length, 0);
  assert.ok(status(t.els).includes("freshly fetched pages"));
});

test("(j) a browser whose storage always throws still completes the whole flow", async () => {
  const t = await boot({ storage: broken });
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  await click(t, "step-fixes", "Approve");
  assert.equal(byText(t.els["step-fixes"], "button", "Download").length, 1);
});

test("(k) a reload with a saved id restores the report and fixes; a vanished report clears the id quietly", async () => {
  const storage = memory();
  storage.setItem("agentready.report", RID);
  const t = await boot({ storage });
  assert.ok(shown(t.els, "step-report") && shown(t.els, "step-fixes"));
  assert.ok(allText(t.els["step-report"]).includes("72"));
  const gone = memory();
  gone.setItem("agentready.report", RID);
  const u = await boot({ storage: gone, serverOpts: { reportStatus: 404 } });
  assert.ok(!shown(u.els, "step-report"));
  assert.equal(gone.m.size, 0);
  assert.equal(status(u.els), "");
  const nofixes = memory();
  nofixes.setItem("agentready.report", RID);
  const w = await boot({ storage: nofixes, serverOpts: { noFixes: true } });
  assert.ok(shown(w.els, "step-report") && !shown(w.els, "step-fixes"));
  assert.equal(nofixes.m.size, 1, "a report without proposed fixes keeps its id");
});

test("(l) double-clicking Verify and audit sends one audit request", async () => {
  const t = await boot();
  await enter(t);
  const b = button(t.els, "step-verify", "Verify and audit");
  b.click(); b.click();
  await t.app.idle();
  assert.equal(t.srv.audits.length, 1);
});

test("(m) every request goes to a known same-origin path", async () => {
  const t = await boot();
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  await click(t, "step-fixes", "Approve");
  await click(t, "step-fixes", "Download");
  await click(t, "step-fixes", "Revoke approval");
  await click(t, "step-report", "Re-audit with fresh pages");
  assert.deepEqual(t.srv.calls.map((c) => c.key), [
    "GET /ownership-token?domain=x.test", "POST /audit", `POST /report/${RID}/fixes`, `POST /report/${RID}/fixes/llms_txt/approve`,
    `GET /report/${RID}/fixes/llms_txt/export`, `POST /report/${RID}/fixes/llms_txt/reject`, "POST /audit"]);
  for (const c of t.srv.calls) assert.ok(ALLOWED.some((re) => re.test(c.key)), c.key);
});

test("network and server failures show fixed sentences and re-enable the button", async () => {
  const t = await boot();
  await enter(t);
  t.srv.networkDown = true;
  await click(t, "step-verify", "Verify and audit");
  assert.ok(status(t.els).includes("Could not reach AgentReady"));
  assert.equal(button(t.els, "step-verify", "Verify and audit").hasAttribute("disabled"), false);
});

test("a rejected address from the server is shown as short clipped text, not markup", async () => {
  const srv = server();
  const original = srv.fetch;
  srv.fetch = async (path, init) => (init.method === "POST" && path === "/audit" ? reply(422, { detail: "<img src=x onerror=alert(1)>" + "x".repeat(500) }) : original(path, init));
  const t = await boot({ server: srv });
  await enter(t);
  await click(t, "step-verify", "Verify and audit");
  assert.ok(status(t.els).includes("<img"));                       // it is text...
  assert.ok(!serialize(t.els["status"]).includes("<img"));         // ...and is escaped
  assert.ok(status(t.els).length < 260);
});

test("the default downloader clicks a temporary link with a safe name, removes it, and revokes the object address only after a delay", () => {
  const doc = createFakeDocument();
  const events = [];
  const timer = fakeTimer();
  const urlApi = { createObjectURL: () => { events.push("create"); return "blob:x"; }, revokeObjectURL: (u) => events.push("revoke " + u) };
  const down = makeDownloader(doc, urlApi, timer);
  const clicks = [];
  const realCreate = doc.createElement;
  doc.createElement = (tag) => { const el = realCreate(tag); el.click = () => { events.push("click"); clicks.push([el.tagName, el.download, el.href]); }; return el; };
  down(new Blob(["x"]), "../../evil name.txt");
  assert.equal(clicks.length, 1);
  const [tag, name, href] = clicks[0];
  assert.equal(tag, "a");
  assert.equal(href, "blob:x");
  assert.ok(!name.includes("/") && !name.startsWith(".") && name.endsWith("evil_name.txt"), name);
  assert.deepEqual(events, ["create", "click"], "the address is still valid right after the click");
  assert.equal(doc.body.children.length, 0, "the temporary link is gone");
  timer.fire(29999);
  assert.deepEqual(events, ["create", "click"]);
  timer.fire(30000);
  assert.deepEqual(events, ["create", "click", "revoke blob:x"]);
});

test("a revoke that throws is swallowed", () => {
  const doc = createFakeDocument();
  const timer = fakeTimer();
  const down = makeDownloader(doc, { createObjectURL: () => "blob:x", revokeObjectURL: () => { throw new Error("gone"); } }, timer);
  const realCreate = doc.createElement;
  doc.createElement = (tag) => { const el = realCreate(tag); el.click = () => {}; return el; };
  down(new Blob(["x"]), "a.txt");
  assert.doesNotThrow(() => timer.fire(30000));
});

// ---------------------------------------------------------------- Group A: a body that cannot be drawn never wedges the page
const THROWER = { toString() { throw new Error("cannot be turned into text"); } };
const withReply = (srv, match, fn) => { const original = srv.fetch; srv.fetch = async (path, init) => (match(path, init) ? fn() : original(path, init)); return srv; };

test("a report that cannot be drawn shows one fixed sentence, frees the button and leaves nothing half drawn", async () => {
  const srv = withReply(server({ verified: true }), (p, i) => i.method === "POST" && p === "/audit", () => reply(200, { id: RID, ...REPORT, site: THROWER }));
  const t = await boot({ server: srv });
  await enter(t);
  await click(t, "step-verify", "Verify and audit");
  assert.ok(status(t.els).includes("Something went wrong on the server"), status(t.els));
  assert.ok(!status(t.els).includes("Checking your site"));
  assert.equal(button(t.els, "step-verify", "Verify and audit").hasAttribute("disabled"), false);
  assert.ok(shown(t.els, "step-verify") && !shown(t.els, "step-report"));
  assert.equal(t.els["step-report"].children.length, 0, "no half-drawn report");
  assert.equal(t.storage.m.size, 0, "a report that was never shown is not remembered");
});

test("fixes that cannot be drawn leave the report in place, a fixed sentence and a free button", async () => {
  const srv = withReply(server({ verified: true }), (p, i) => i.method === "POST" && p === `/report/${RID}/fixes`,
    () => reply(200, { fixes: [mkFix("llms_txt")], skipped: [{ kind: "json_ld", reason: THROWER }] }));
  const t = await boot({ server: srv });
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  assert.ok(status(t.els).includes("Something went wrong on the server"), status(t.els));
  assert.equal(button(t.els, "step-report", "Propose fixes").hasAttribute("disabled"), false);
  assert.ok(shown(t.els, "step-report") && !shown(t.els, "step-fixes"));
  assert.equal(t.els["step-fixes"].children.length, 0);
});

test("a restore that cannot be drawn leaves the address form usable and the page quiet about the rest", async () => {
  const storage = memory();
  storage.setItem("agentready.report", RID);
  const srv = withReply(server({ verified: true }), (p, i) => i.method === "GET" && p === `/report/${RID}`, () => reply(200, { ...REPORT, site: THROWER }));
  const t = await boot({ storage, server: srv });
  assert.equal(byTag(t.els["step-url"], "input").length, 1, "the address form is there");
  assert.ok(!shown(t.els, "step-report"));
  assert.ok(status(t.els).includes("Something went wrong on the server"));
});

test("an unexpected failure inside a click handler frees the busy lock and shows one fixed sentence", async () => {
  let broken = true;
  const t = await boot({ serverOpts: { verified: true }, now: () => { if (broken) throw new Error("clock failure"); return new Date(Date.UTC(2026, 10, 24, 9, 5, 7)); } });
  await toReport(t);
  await click(t, "step-report", "Re-audit with fresh pages");           // snapshotName(now()) throws inside the task
  assert.ok(status(t.els).includes("Something went wrong on the server"), status(t.els));
  assert.equal(button(t.els, "step-report", "Re-audit with fresh pages").hasAttribute("disabled"), false);
  broken = false;
  await click(t, "step-report", "Propose fixes");                       // the busy lock was released
  assert.ok(shown(t.els, "step-fixes"));
});

// ---------------------------------------------------------------- Group B: printing
test("printing opens every finding row, and closes again the ones that were closed", async () => {
  const listeners = {};
  const win = { addEventListener: (type, fn) => { listeners[type] = fn; } };
  const more = (id) => ({ ...REPORT.findings[0], check_id: id, passed: id === "sitemap" });
  const report = { ...REPORT, findings: [REPORT.findings[0], more("sitemap"), more("json_ld"), more("robots_ai")] };
  const t = await boot({ win, serverOpts: { verified: true, report } });
  await toReport(t);
  const rows = findAll(t.els["step-report"], (n) => n.tagName === "details");
  assert.equal(rows.length, 4);
  rows[0].setAttribute("open", "");                         // one row the person had already opened
  listeners.beforeprint();
  assert.ok(rows.every((r) => r.hasAttribute("open")), "all rows are open for the printout");
  listeners.afterprint();
  assert.equal(rows[0].hasAttribute("open"), true, "the row the person opened stays open");
  assert.ok(rows.slice(1).every((r) => !r.hasAttribute("open")), "the others are closed again");
});

test("a page without a window object still starts", async () => {
  const t = await boot({ win: null });
  assert.equal(byTag(t.els["step-url"], "input").length, 1);
});

// ---------------------------------------------------------------- Group C: flow robustness
test("a failed address lookup leaves the report, the saved id and the audited site alone", async () => {
  const t = await boot();
  await toReport(t);
  t.srv.networkDown = true;
  await enter(t, "other.test");
  assert.ok(status(t.els).includes("Could not reach AgentReady"));
  assert.ok(shown(t.els, "step-report"), "the report is still there");
  assert.deepEqual([...t.storage.m.values()], [RID], "and so is its saved id");
  assert.equal(t.app.store.get().site, "https://x.test");
  t.srv.networkDown = false;
  await click(t, "step-report", "Re-audit with fresh pages");
  assert.equal(t.srv.audits.at(-1).url, "https://x.test", "re-audit still targets the audited site");
});

test("a successful lookup for a new site clears the old report and its saved id", async () => {
  const t = await boot();
  await toReport(t);
  await enter(t, "other.test");
  assert.ok(!shown(t.els, "step-report") && !shown(t.els, "step-fixes"));
  assert.equal(t.storage.m.size, 0);
  assert.equal(t.app.store.get().site, "https://other.test");
});

test("a restore that finishes after the person chose another site is discarded", async () => {
  const storage = memory();
  storage.setItem("agentready.report", RID);
  let release;
  const gate = new Promise((r) => { release = r; });
  const srv = server({ verified: true });
  const original = srv.fetch;
  srv.fetch = async (path, init) => { if (init.method === "GET" && path === `/report/${RID}`) await gate; return original(path, init); };
  const t = await boot({ storage, server: srv, noWait: true });
  const [input] = byTag(t.els["step-url"], "input");
  input.value = "other.test";
  byTag(t.els["step-url"], "form")[0].dispatch("submit");
  await flush();                                            // the restore is still waiting on the gate, so do not wait for idle yet
  assert.ok(shown(t.els, "step-verify"));
  release();
  await t.app.idle();
  assert.ok(!shown(t.els, "step-report") && !shown(t.els, "step-fixes"), "the late restore drew nothing");
  assert.ok(shown(t.els, "step-verify"), "the new site's steps stay");
  assert.equal(t.app.store.get().site, "https://other.test");
});

test("a click that is dropped because another action is running says so", async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const srv = server({ verified: true });
  const original = srv.fetch;
  srv.fetch = async (path, init) => { if (init.method === "POST" && path === "/audit") await gate; return original(path, init); };
  const t = await boot({ server: srv });
  await enter(t);
  button(t.els, "step-verify", "Verify and audit").click();
  await flush();
  const [input] = byTag(t.els["step-url"], "input");
  input.value = "other.test";
  byTag(t.els["step-url"], "form")[0].dispatch("submit");
  await flush();
  assert.equal(status(t.els), "Please wait for the current action to finish.");
  assert.equal(t.srv.calls.filter((c) => c.key.startsWith("GET /ownership-token")).length, 1, "the dropped click made no request");
  release();
  await t.app.idle();
  assert.ok(shown(t.els, "step-report"));
});

test("a clipboard that never answers is given up on after 10 seconds and Copy works again", async () => {
  const timer = fakeTimer();
  const t = await boot({ timer, clipboard: { writeText: () => new Promise(() => {}) } });
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  await click(t, "step-fixes", "Approve");
  button(t.els, "step-fixes", "Copy").click();
  await flush();
  assert.equal(button(t.els, "step-fixes", "Copy").hasAttribute("disabled"), true, "still waiting");
  timer.fire(10000);
  await within(t.app.idle(), 1000, "the copy action to give up");
  assert.ok(status(t.els).includes("Copy is not available in this browser. Use Download instead."));
  assert.equal(button(t.els, "step-fixes", "Copy").hasAttribute("disabled"), false);
});

test("approve, reject and revoke each say what happened, and only when the server's answer agrees", async () => {
  const t = await boot();
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  await click(t, "step-fixes", "Approve");
  assert.ok(status(t.els).startsWith("Approved. Nothing has been changed on your site."), status(t.els));
  await click(t, "step-fixes", "Revoke approval");
  assert.ok(status(t.els).startsWith("Approval revoked."), status(t.els));
  await click(t, "step-fixes", "Reject");                       // the second card is still proposed
  assert.ok(status(t.els).startsWith("Rejected. Nothing has been changed on your site."), status(t.els));
  const u = await boot();
  await toReport(u);
  await click(u, "step-report", "Propose fixes");
  u.srv.lie = "proposed";                                        // 200, but the fix is not approved
  await click(u, "step-fixes", "Approve");
  assert.ok(status(u.els).includes("Something went wrong on the server"), status(u.els));
  assert.ok(!status(u.els).includes("Approved."));
  assert.equal(byText(u.els["step-fixes"], "button", "Approve").length, 2, "the card is drawn from what the server said");
  u.srv.lie = "approved";                                        // 200 for a reject, but the fix is approved
  await click(u, "step-fixes", "Reject");
  assert.ok(status(u.els).includes("Something went wrong on the server") && !status(u.els).includes("Rejected."));
});

test("a 403 from any step but the audit is 'the server refused', never the verification message", async () => {
  const srv = withReply(server({ verified: true }), (p, i) => i.method === "POST" && p === `/report/${RID}/fixes`, () => reply(403, { detail: { token: "t" } }));
  const t = await boot({ server: srv });
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  assert.equal(status(t.els), "The server refused this request.");
});

// ---------------------------------------------------------------- Group E: the report id stays out of the state views get
test("the state a view can read never carries the report id", async () => {
  const t = await boot();
  await toReport(t);
  const state = t.app.store.get();
  assert.ok(!("id" in state.report), "the report object has no id field");
  assert.ok(!JSON.stringify(state).includes(RID));
  await click(t, "step-report", "Propose fixes");
  assert.ok(!JSON.stringify(t.app.store.get()).includes(RID));
});

test("the address form and the report refuse an address with a user name, a path trick or a second scheme", async () => {
  const t = await boot();
  for (const bad of ["user@x.test", "@x.test", "https://user:pw@x.test", "ftp://x.test", "x.test:0", "x.test:70000", "x_test"]) {
    await enter(t, bad);
    assert.ok(status(t.els).includes("does not look like a site address"), bad);
  }
  assert.equal(t.srv.calls.length, 0);
});

// ---------------------------------------------------------------- Group E: more behaviour pinned down
test("a hostile file name from the server is made safe before it reaches the downloader", async () => {
  const t = await boot({ serverOpts: { verified: true, exportName: "../../evil <name>\u202e.txt" } });
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  await click(t, "step-fixes", "Approve");
  await click(t, "step-fixes", "Download");
  assert.equal(t.downloads.length, 1);
  assert.match(t.downloads[0].name, /^[A-Za-z0-9._-]+$/);
  assert.ok(!t.downloads[0].name.includes("..") && !t.downloads[0].name.startsWith("."), t.downloads[0].name);
});

test("a refused first audit puts the keyboard on the verification steps", async () => {
  const t = await boot();
  await enter(t);
  byTag(t.els["step-url"], "input")[0].focus();            // the person clicked somewhere else in the meantime
  await click(t, "step-verify", "Verify and audit");
  assert.equal(t.doc.activeElement.getAttribute("id"), "verify-heading");
});

test("steps that cannot be drawn after a good lookup leave the old report where it is", async () => {
  const srv = server({ verified: true });
  const original = srv.fetch;
  srv.fetch = async (path, init) => (path.startsWith("/ownership-token?domain=other.test") ? reply(200, { ...INSTRUCTIONS, domain: THROWER }) : original(path, init));
  const t = await boot({ server: srv });
  await toReport(t);
  await enter(t, "other.test");
  assert.ok(status(t.els).includes("Something went wrong on the server"), status(t.els));
  assert.ok(shown(t.els, "step-report"), "the old report is still shown");
  assert.deepEqual([...t.storage.m.values()], [RID], "and its saved id is kept");
});
