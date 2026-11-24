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
      return reply(200, { id: RID, ...REPORT });
    }
    if (key === `GET /report/${RID}`) return s.reportStatus ? reply(s.reportStatus, { detail: "Unknown report" }) : reply(200, REPORT);
    if (key === `POST /report/${RID}/fixes`) return reply(200, { fixes: Object.values(fixes), skipped: [] });
    if (key === `GET /report/${RID}/fixes`) return s.noFixes ? reply(404, { detail: "No fixes proposed yet" }) : reply(200, { fixes: Object.values(fixes), skipped: [] });
    let m = /^POST \/report\/[0-9a-f]{32}\/fixes\/([a-z_]+)\/(approve|reject)$/.exec(key);
    if (m) {
      if (s.conflict) return reply(409, { detail: "SECRET server text" });
      fixes[m[1]].status = m[2] === "approve" ? "approved" : "rejected";
      return reply(200, fixes[m[1]]);
    }
    m = /^GET \/report\/[0-9a-f]{32}\/fixes\/([a-z_]+)\/export$/.exec(key);
    if (m) return fixes[m[1]].status === "approved" ? reply(200, { filename: fixes[m[1]].filename, content: CONTENT, diff: "d", sha256: "a".repeat(64), note: "n" })
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
  const app = await start({ document: doc, fetch: srv.fetch, storage, clipboard,
    downloadBlob: (blob, name) => downloads.push({ blob, name }), win: opts.win ?? null, now: opts.now ?? (() => new Date(Date.UTC(2026, 10, 24, 9, 5, 7))) });
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

test("(h) a conflict from the server shows one fixed sentence and never the server's text", async () => {
  const t = await boot();
  await toReport(t);
  await click(t, "step-report", "Propose fixes");
  t.srv.conflict = true;
  await click(t, "step-fixes", "Approve");
  assert.ok(status(t.els).includes("That action is not allowed right now."));
  assert.ok(!allSerialized(t.els).includes("SECRET"));
  assert.equal(byText(t.els["step-fixes"], "button", "Approve").length, 2, "nothing changed");
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

test("the default downloader clicks a temporary link with a safe name and cleans up", () => {
  const doc = createFakeDocument();
  const events = [];
  const urlApi = { createObjectURL: () => { events.push("create"); return "blob:x"; }, revokeObjectURL: (u) => events.push("revoke " + u) };
  const down = makeDownloader(doc, urlApi);
  const clicks = [];
  const realCreate = doc.createElement;
  doc.createElement = (tag) => { const el = realCreate(tag); el.click = () => clicks.push([el.tagName, el.download, el.href]); return el; };
  down(new Blob(["x"]), "../../evil name.txt");
  assert.equal(clicks.length, 1);
  const [tag, name, href] = clicks[0];
  assert.equal(tag, "a");
  assert.equal(href, "blob:x");
  assert.ok(!name.includes("/") && !name.startsWith(".") && name.endsWith("evil_name.txt"), name);
  assert.deepEqual(events, ["create", "revoke blob:x"]);
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
  const t = await boot({ win });
  await toReport(t);
  const rows = findAll(t.els["step-report"], (n) => n.tagName === "details");
  assert.ok(rows.length > 0);
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
