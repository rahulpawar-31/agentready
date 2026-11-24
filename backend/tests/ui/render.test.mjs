import test from "node:test";
import assert from "node:assert/strict";
import { allText, byTag, createFakeDocument, findAll, serialize } from "./fake-dom.mjs";
import { renderFixes, renderInstructions, renderReport, renderUrlForm } from "../../app/ui/render.js";

const doc = createFakeDocument();
const EVIL = '</script><img src=x onerror=alert(1)>"\'&';
const cls = (n) => n.getAttribute("class") || "";
const buttons = (root) => byTag(root, "button");
const labelled = (root, text) => buttons(root).filter((b) => b.textContent.trim() === text);
const noop = () => {};

const ALLOWED_TAGS = new Set(["div", "section", "h2", "h3", "p", "span", "ul", "li", "details", "summary", "progress", "button",
  "strong", "small", "code", "pre", "form", "label", "input", "a"]);

function finding(over = {}) {
  return { check_id: "llms_txt", category: "discoverability", passed: false, weight: 2, check: "GET /llms.txt and expect text",
           evidence: "/llms.txt returned 404 or was empty", url: "https://x.test/llms.txt", confidence: "high",
           status: "not detected", ...over };
}
function report(over = {}) {
  return { site: "https://x.test", score: 72, score_status: "scored", unscored: 0, category_scores: { discoverability: 40, safety: 100 },
           findings: [finding(), finding({ check_id: "sitemap", passed: true, check: "GET /sitemap.xml", evidence: "Sitemap found", status: "detected" })], ...over };
}
const hostileReport = () => report({
  site: EVIL, category_scores: { discoverability: 40, [EVIL]: 100 },
  findings: [
    finding({ check: EVIL, evidence: EVIL, url: "https://evil.test/" + EVIL, check_id: EVIL, category: EVIL }),
    finding({ check_id: "b", category: "safety", passed: true, check: "ok", evidence: "fine", url: "https://x.test/", confidence: "weird", status: "totally-new" }),
  ] });

// ---------------------------------------------------------------- report
test("hostile report data never becomes markup or a link", () => {
  const node = renderReport(doc, hostileReport(), { onPropose: noop, onReaudit: noop });
  const s = serialize(node);
  assert.ok(!s.includes("<img") && !s.includes("<script"), s.slice(0, 300));
  assert.ok(s.includes("&lt;img"));
  const all = findAll(node, () => true);
  for (const n of all) {
    assert.ok(ALLOWED_TAGS.has(n.tagName), n.tagName);
    for (const [k, v] of n.attributes) {
      assert.ok(!/^on/i.test(k), k);
      assert.ok(!/evil\.test|onerror|<|>/i.test(v) || k === "aria-label", `${k}=${v}`);
    }
  }
  assert.equal(byTag(node, "a").length, 0, "no element in a report is a link");
});

test("finding urls are shown as text, never as links, even for on-site urls", () => {
  const node = renderReport(doc, report(), { onPropose: noop, onReaudit: noop });
  assert.equal(findAll(node, (n) => n.hasAttribute("href")).length, 0);
  assert.ok(allText(node).includes("https://x.test/llms.txt"));
});

test("findings needing attention come before the ones that are fine, inside each category", () => {
  const node = renderReport(doc, report({ findings: [
    finding({ check_id: "sitemap", passed: true, status: "detected", evidence: "Sitemap found" }),
    finding({ check_id: "llms_txt", passed: false }),
  ] }), { onPropose: noop, onReaudit: noop });
  const text = allText(node);
  assert.ok(text.indexOf("Needs attention") !== -1 && text.indexOf("OK so far") !== -1);
  assert.ok(text.indexOf("Needs attention") < text.indexOf("OK so far"));
  const details = byTag(node, "details");
  assert.equal(details.length, 2);
  assert.ok(details[0].textContent.includes("llms.txt"), "the failing finding is listed first");
});

test("unknown status and confidence values are shown as 'unknown' and never used as class names", () => {
  const s = serialize(renderReport(doc, hostileReport(), { onPropose: noop, onReaudit: noop }));
  assert.ok(s.includes(">unknown<"));
  assert.ok(!s.includes("totally-new") && !s.includes("weird"));
  for (const n of findAll(renderReport(doc, hostileReport(), { onPropose: noop, onReaudit: noop }), (x) => x.hasAttribute("class"))) {
    assert.match(cls(n), /^[a-z0-9 _-]+$/, cls(n));
  }
});

test("the score band text matches the documented boundaries", () => {
  const want = { 0: "Poor", 49: "Poor", 50: "Needs work", 89: "Needs work", 90: "Good", 100: "Good" };
  for (const [score, label] of Object.entries(want)) {
    const node = renderReport(doc, report({ score: Number(score) }), { onPropose: noop, onReaudit: noop });
    assert.ok(allText(node).includes(label), `${score} -> ${label}`);
    const bar = byTag(node, "progress")[0];
    assert.equal(bar.getAttribute("value"), String(score));
    assert.equal(bar.getAttribute("max"), "100");
    assert.match(bar.getAttribute("aria-label"), new RegExp(`${score} out of 100`));
  }
});

test("a report that could not be scored says so and shows no score or bar", () => {
  const node = renderReport(doc, report({ score: 0, score_status: "not_scored", findings: [finding({ status: "unreachable", passed: false })] }), { onPropose: noop, onReaudit: noop });
  assert.ok(allText(node).includes("Not scored"));
  assert.equal(byTag(node, "progress").length, 0);
  assert.equal(findAll(node, (n) => cls(n).includes("score-number")).length, 0);
});

test("unreachable checks are counted and labelled as outside the score", () => {
  const text = (u) => allText(renderReport(doc, report({ unscored: u }), { onPropose: noop, onReaudit: noop }));
  assert.ok(text(2).includes("2 checks could not be reached and are not in the score"));
  assert.ok(text(1).includes("1 check could not be reached and is not in the score"));
  assert.ok(!/could not be reached/.test(text(0)));
  const node = renderReport(doc, report({ findings: [finding({ status: "unreachable", passed: false, evidence: "Could not fetch: timeout" })] }), { onPropose: noop, onReaudit: noop });
  assert.ok(allText(node).includes("could not be checked, not in the score"));
  assert.ok(!allText(node).includes("Needs attention"), "an unreachable check is not a failing check");
});

test("evidence wording: 'not detected' is shown as such and no claim of absence or success is made", () => {
  const node = renderReport(doc, report(), { onPropose: noop, onReaudit: noop });
  const text = allText(node);
  assert.ok(text.includes("not detected") && text.includes("detected"));
  for (const word of ["absent", "missing", "passed", "secure"]) assert.ok(!text.toLowerCase().includes(word), word);
});

test("each finding row shows what was checked, what was observed and where", () => {
  const node = renderReport(doc, report(), { onPropose: noop, onReaudit: noop });
  const row = byTag(node, "details")[0].textContent;
  for (const piece of ["GET /llms.txt and expect text", "/llms.txt returned 404 or was empty", "https://x.test/llms.txt", "high"]) assert.ok(row.includes(piece), piece);
});

test("very long finding text is clipped for display", () => {
  const node = renderReport(doc, report({ findings: [finding({ evidence: "e".repeat(5000), check: "c".repeat(5000), url: "u".repeat(5000) })] }), { onPropose: noop, onReaudit: noop });
  assert.ok(allText(node).length < 3000);
});

test("the report offers one primary action and one secondary action and calls the handlers", () => {
  const calls = [];
  const node = renderReport(doc, report(), { onPropose: (b) => calls.push(["propose", b.tagName]), onReaudit: (b) => calls.push(["reaudit", b.tagName]) });
  const bs = buttons(node);
  assert.deepEqual(bs.map((b) => b.textContent), ["Propose fixes", "Re-audit with fresh pages"]);
  assert.equal(bs.filter((b) => cls(b).includes("btn-primary")).length, 1);
  assert.ok(cls(bs[1]).includes("btn-secondary"));
  bs[0].click(); bs[1].click();
  assert.deepEqual(calls, [["propose", "button"], ["reaudit", "button"]]);
});

test("the report heading can take focus", () => {
  const h = byTag(renderReport(doc, report(), { onPropose: noop, onReaudit: noop }), "h2")[0];
  assert.equal(h.getAttribute("tabindex"), "-1");
  assert.ok(h.getAttribute("id"));
});

// ---------------------------------------------------------------- instructions and form
const INSTR = { domain: "x.test", token: "tok", meta_tag: '<meta name="agentready-verification" content="tok">', meta_tag_placement: "inside <head> on the home page",
                dns_txt_name: "_agentready.x.test", dns_txt_value: "agentready-verify=tok" };

test("the verification card shows the tag and the DNS record as text and one primary button", () => {
  const calls = [];
  const node = renderInstructions(doc, INSTR, { onVerify: (b) => calls.push(b.tagName) });
  const codes = byTag(node, "code").map((c) => c.textContent);
  assert.ok(codes.includes(INSTR.meta_tag) && codes.includes("_agentready.x.test") && codes.includes("agentready-verify=tok"));
  assert.ok(allText(node).includes("This proves you control the site. To check, AgentReady looks up the DNS record and, if that is not found, fetches your home page once to look for the tag. It makes no other request to your site until this check passes."));
  const bs = buttons(node);
  assert.deepEqual(bs.map((b) => b.textContent), ["Verify and audit"]);
  assert.ok(cls(bs[0]).includes("btn-primary"));
  bs[0].click();
  assert.deepEqual(calls, ["button"]);
  assert.ok(!serialize(node).includes("<meta"), "the tag is text, not markup");
});

test("hostile instructions are text, and a missing DNS name shows the note instead", () => {
  const node = renderInstructions(doc, { ...INSTR, domain: EVIL, meta_tag: EVIL, dns_txt_name: null, dns_txt_value: null, dns_note: EVIL + " note" }, { onVerify: noop });
  const s = serialize(node);
  assert.ok(!s.includes("<img") && !s.includes("<script"));
  assert.ok(allText(node).includes(EVIL + " note"));
  assert.ok(!allText(node).includes("_agentready."));
});

test("incomplete instructions do not break the card", () => {
  for (const bad of [{}, null, { token: 5 }]) assert.doesNotThrow(() => renderInstructions(doc, bad, { onVerify: noop }));
});

test("the address form has a labelled input, one primary button and submits the typed value", () => {
  const seen = [];
  const node = renderUrlForm(doc, { onSubmit: (v, b) => seen.push([v, b.tagName]), value: "x.test" });
  const [input] = byTag(node, "input");
  const [label] = byTag(node, "label");
  assert.equal(label.getAttribute("for"), input.getAttribute("id"));
  assert.equal(input.getAttribute("autocomplete"), "off");
  assert.equal(input.getAttribute("spellcheck"), "false");
  assert.deepEqual(buttons(node).map((b) => b.textContent), ["Get verification steps"]);
  input.value = "y.test";
  const [form] = byTag(node, "form");
  const e = form.dispatch("submit");
  assert.equal(e.defaultPrevented, true);
  assert.deepEqual(seen, [["y.test", "button"]]);
});

// ---------------------------------------------------------------- fixes
const DIFF = "--- /dev/null\n+++ b/llms.txt\n@@ -0,0 +1,2 @@\n+# Acme\n+> hi\n-old line\n context line\n";
function fix(over = {}) {
  return { id: "llms_txt", kind: "llms_txt", path: "/llms.txt", filename: "llms.txt", description: "Adds a plain-text summary of the site.",
           finding_id: "llms_txt", status: "proposed", diff: DIFF, diff_kind: "full", warning: "", note: "", sha256: "a".repeat(64), ...over };
}
const handlers = (calls = []) => ({
  onApprove: (id, b) => calls.push(["approve", id, b.tagName]), onReject: (id) => calls.push(["reject", id]),
  onDownload: (id) => calls.push(["download", id]), onCopy: (id) => calls.push(["copy", id]),
});

test("a proposed fix has one primary action, Approve, and a quiet Reject; nothing can be downloaded yet", () => {
  const calls = [];
  const node = renderFixes(doc, { fixes: [fix()], skipped: [] }, handlers(calls));
  const bs = buttons(node);
  assert.deepEqual(bs.map((b) => b.textContent), ["Approve", "Reject"]);
  assert.equal(bs.filter((b) => cls(b).includes("btn-primary")).length, 1);
  assert.ok(cls(bs[0]).includes("btn-primary") && cls(bs[1]).includes("btn-secondary"));
  assert.equal(labelled(node, "Download").length + labelled(node, "Copy").length, 0);
  bs[0].click(); bs[1].click();
  assert.deepEqual(calls, [["approve", "llms_txt", "button"], ["reject", "llms_txt"]]);
});

test("an approved fix offers Download (primary), Copy and Revoke approval, and no Approve", () => {
  const calls = [];
  const node = renderFixes(doc, { fixes: [fix({ status: "approved" })], skipped: [] }, handlers(calls));
  const bs = buttons(node);
  assert.deepEqual(bs.map((b) => b.textContent), ["Download", "Copy", "Revoke approval"]);
  assert.ok(cls(bs[0]).includes("btn-primary") && bs.filter((b) => cls(b).includes("btn-primary")).length === 1);
  bs[0].click(); bs[1].click(); bs[2].click();
  assert.deepEqual(calls, [["download", "llms_txt"], ["copy", "llms_txt"], ["reject", "llms_txt"]]);
});

test("a rejected fix, or one with an unknown status, has no actions", () => {
  for (const status of ["rejected", "weird", undefined]) {
    const node = renderFixes(doc, { fixes: [fix({ status })], skipped: [] }, handlers());
    assert.equal(buttons(node).length, 0, String(status));
  }
  const s = serialize(renderFixes(doc, { fixes: [fix({ status: "weird" })], skipped: [] }, handlers()));
  assert.ok(s.includes(">unknown<") && !s.includes("weird"));
});

test("the approval sentence is shown on cards that can still be acted on", () => {
  const sentence = "Approving does not change your site. After approving, download or copy the file and add it to your site yourself.";
  for (const status of ["proposed", "approved"]) {
    assert.ok(allText(renderFixes(doc, { fixes: [fix({ status })], skipped: [] }, handlers())).includes(sentence), status);
  }
});

test("diff lines keep their own +/- prefix and get a class that does not depend on colour alone", () => {
  const node = renderFixes(doc, { fixes: [fix()], skipped: [] }, handlers());
  const pre = byTag(node, "pre")[0];
  const lines = pre.children.map((span) => [span.textContent, cls(span)]);
  const kind = (prefix) => lines.filter(([t]) => t.startsWith(prefix));
  assert.ok(kind("+# Acme").every(([, c]) => c.includes("diff-add")));
  assert.ok(kind("-old line").every(([, c]) => c.includes("diff-del")));
  assert.ok(kind(" context line").every(([, c]) => c.includes("diff-ctx")));
  for (const p of ["--- /dev/null", "+++ b/llms.txt", "@@ -0,0 +1,2 @@"]) assert.ok(kind(p).every(([, c]) => c.includes("diff-meta")), p);
  assert.equal(pre.getAttribute("tabindex"), "0");
  assert.ok(pre.textContent.includes("+# Acme\n+> hi"));
});

test("very long diff lines are clipped for display", () => {
  const node = renderFixes(doc, { fixes: [fix({ diff: "+" + "x".repeat(5000) + "\n" })], skipped: [] }, handlers());
  const longest = Math.max(...byTag(node, "pre")[0].children.map((s) => s.textContent.length));
  assert.ok(longest <= 402, String(longest));
});

test("excerpt diffs are labelled, and warnings, notes and paths are shown as text", () => {
  const node = renderFixes(doc, { fixes: [fix({ diff_kind: "excerpt", warning: "The existing page contains a secret-like value (jwt)", note: "an HTML page was found at /llms.txt" })], skipped: [] }, handlers());
  const text = allText(node);
  assert.ok(text.includes("Excerpt: the full page is not shown"));
  assert.ok(text.includes("The existing page contains a secret-like value (jwt)"));
  assert.ok(text.includes("an HTML page was found at /llms.txt"));
  assert.ok(text.includes("/llms.txt"));
  assert.ok(!allText(renderFixes(doc, { fixes: [fix()], skipped: [] }, handlers())).includes("Excerpt"));
});

test("skipped kinds are listed with their reasons under 'Not proposed'", () => {
  const node = renderFixes(doc, { fixes: [], skipped: [{ kind: "mcp_stub", reason: "no OpenAPI document was found" }, { kind: EVIL, reason: EVIL }] }, handlers());
  const text = allText(node);
  assert.ok(text.includes("Not proposed") && text.includes("no OpenAPI document was found") && text.includes("MCP stub (Python)"));
  assert.ok(!serialize(node).includes("<img"));
});

test("hostile fix fields never become markup, and handlers get the id from the data", () => {
  const calls = [];
  const node = renderFixes(doc, { fixes: [fix({ id: "json_ld", kind: EVIL, description: EVIL, diff: EVIL + "\n+" + EVIL, warning: EVIL, note: EVIL, path: EVIL, filename: EVIL })], skipped: [] }, handlers(calls));
  const s = serialize(node);
  assert.ok(!s.includes("<img") && !s.includes("<script"));
  assert.equal(byTag(node, "a").length, 0);
  buttons(node)[0].click();
  assert.deepEqual(calls, [["approve", "json_ld", "button"]]);
});

test("a body with nothing in it still renders", () => {
  for (const body of [{ fixes: [], skipped: [] }, {}, null]) assert.doesNotThrow(() => renderFixes(doc, body, handlers()));
  assert.ok(allText(renderFixes(doc, { fixes: [], skipped: [] }, handlers())).includes("No fixes could be proposed"));
});
