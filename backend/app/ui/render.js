// Views. Each function takes the document, plain data from the API and callbacks, and returns a DOM node. Every string
// that came from the audited site or from the API is placed as text (see dom.js); class names come only from the fixed
// tables below; site-derived addresses are shown as text and are never links.
import { makeDom } from "./dom.js";
import { band, clipText, confidenceLabel, statusLabel } from "./format.js";

const KIND_TITLES = { llms_txt: "llms.txt", json_ld: "JSON-LD snippet", mcp_stub: "MCP stub (Python)" };
const CHECK_TITLES = {
  robots_ai: "AI crawler rules in robots.txt", sitemap: "Sitemap", llms_txt: "llms.txt", json_ld: "JSON-LD on the home page",
  no_js_content: "Readable text without JavaScript", labelled_forms: "Labelled form fields", no_captcha: "CAPTCHA markers",
  no_login_wall: "Login wall on the home page", openapi_public: "Public OpenAPI document", auth_declared: "Declared authentication",
  env_not_exposed: "Readable /.env file", git_not_exposed: "Readable /.git/HEAD file", security_headers: "Response security headers",
};
const CATEGORY_TITLES = { discoverability: "Discoverability", operability: "Operability", identity: "Identity and permissions", safety: "Safety" };
const CATEGORY_ORDER = ["discoverability", "operability", "identity", "safety"];
const STATUS_CLASS = { detected: "chip-detected", "not detected": "chip-not-detected", unreachable: "chip-unreachable", unknown: "chip-unknown" };
const FIX_STATUSES = new Set(["proposed", "approved", "rejected"]);
const FIX_STATUS_CLASS = { proposed: "chip-proposed", approved: "chip-approved", rejected: "chip-rejected", unknown: "chip-unknown" };

const CLIP = { check: 300, evidence: 600, url: 300, description: 300, note: 300, warning: 300, reason: 300, path: 200, name: 80 };
const MAX_DIFF_LINES = 2000;       // beyond this the diff is not shown in full, so Approve is withheld for that fix
const MAX_FINDINGS = 500;          // drawn at most; the rest are counted in a marker
const MAX_FIXES = 20;
const MAX_SKIPPED = 50;
const MAX_CATEGORY_SCORES = 50;
const TOO_BIG_SENTENCE = "This fix is too large to review here. Reject it or review it through the API.";
const APPROVAL_SENTENCE = "Approving does not change your site. After approving, download or copy the file and add it to your site yourself.";
const VERIFY_SENTENCE = "This proves you control the site. To check, AgentReady looks up the DNS record and, if that is not found, " +
  "fetches your home page once to look for the tag. It makes no other request to your site until this check passes.";

const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const list = (v) => (Array.isArray(v) ? v.filter(isObject) : []);
// Titles come from fixed tables, looked up only by own property of a real string, so a key such as "__proto__",
// "constructor" or "toString" (or an object that stringifies to a known key) falls back to plain clipped text.
const own = (table, key) => (typeof key === "string" && Object.hasOwn(table, key) ? table[key] : null);
const moreMarker = (n, one, many) => (n === 1 ? `1 more ${one} is not shown here.` : `${n} more ${many} are not shown here.`);

export function renderFixes(document, body, handlers = {}) {
  const { h } = makeDom(document);
  const data = isObject(body) ? body : {};
  const fixes = list(data.fixes);
  const skipped = list(data.skipped);

  const children = [
    h("h2", { id: "fixes-heading", tabindex: "-1" }, "4. Review fixes"),
    h("p", {}, "These are proposals. Read each diff, then approve or reject it."),
  ];
  if (fixes.length === 0) children.push(h("p", { class: "muted" }, "No fixes could be proposed for this report."));
  for (const fix of fixes.slice(0, MAX_FIXES)) children.push(fixCard(fix));
  if (fixes.length > MAX_FIXES) children.push(h("p", { class: "muted" }, moreMarker(fixes.length - MAX_FIXES, "fix", "fixes")));
  if (skipped.length > 0) {
    const items = skipped.slice(0, MAX_SKIPPED).map((s) => h("li", {}, h("strong", {}, kindTitle(s.kind)), " — ", clipText(s.reason, CLIP.reason)));
    if (skipped.length > MAX_SKIPPED) items.push(h("li", { class: "muted" }, moreMarker(skipped.length - MAX_SKIPPED, "entry", "entries")));
    children.push(h("details", { class: "skipped" }, h("summary", {}, "Not proposed"), h("ul", {}, items)));
  }
  return h("div", { class: "card" }, children);

  function kindTitle(kind) { return own(KIND_TITLES, kind) ?? clipText(kind, CLIP.name); }

  function button(label, kind, onClick, id) {
    const b = h("button", { type: "button", class: `btn btn-${kind}` }, label);
    b.addEventListener("click", () => { if (typeof onClick === "function") onClick(id, b); });
    return b;
  }

  function fixCard(fix) {
    const id = String(fix.id);
    const status = FIX_STATUSES.has(fix.status) ? fix.status : "unknown";
    // Anything the owner is shown in shortened form is marked, and then Approve is withheld: approval must rest on
    // a review of everything that would be exported.
    let shortened = false;
    const fit = (value, n) => {
      const s = value === null || value === undefined ? "" : String(value);
      if (s.length <= n) return [s];
      shortened = true;
      return [s.slice(0, n), h("span", { class: "muted" }, ` … ${s.length - n} characters not shown`)];
    };
    const diff = diffView(fix);
    if (diff.shortened) shortened = true;
    const parts = [
      h("div", { class: "fix-head" }, h("h3", {}, kindTitle(fix.kind)), h("span", { class: `chip ${FIX_STATUS_CLASS[status]}` }, status)),
      h("p", {}, fit(fix.description, CLIP.description)),
      h("p", { class: "muted" }, "Goes at: ", h("code", {}, fit(fix.path, CLIP.path))),
    ];
    if (fix.warning) parts.push(h("p", { class: "notice" }, h("strong", {}, "Warning: "), fit(fix.warning, CLIP.warning)));
    if (fix.note) parts.push(h("p", { class: "muted" }, fit(fix.note, CLIP.note)));
    if (fix.diff_kind === "excerpt") parts.push(h("p", { class: "muted" }, "Excerpt: the full page is not shown"));
    parts.push(diff.node);
    const actions = [];
    if (status === "proposed") {
      if (!shortened) actions.push(button("Approve", "primary", handlers.onApprove, id));
      actions.push(button("Reject", "secondary", handlers.onReject, id));
    } else if (status === "approved") {
      actions.push(button("Download", "primary", handlers.onDownload, id), button("Copy", "secondary", handlers.onCopy, id),
        button("Revoke approval", "secondary", handlers.onReject, id));
    }
    if (actions.length > 0) parts.push(h("div", { class: "actions" }, actions));
    if (status === "proposed" && shortened) parts.push(h("p", { class: "notice" }, TOO_BIG_SENTENCE));
    else if (actions.length > 0) parts.push(h("p", { class: "muted" }, APPROVAL_SENTENCE));
    return h("section", { class: "fix-card", "data-fix-status": status }, parts);
  }

  function diffView(fix) {
    const label = own(KIND_TITLES, fix.kind) ? `Diff for ${KIND_TITLES[fix.kind]}` : "Diff";
    const lines = typeof fix.diff === "string" ? fix.diff.split("\n") : [];
    if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
    // Lines are never shortened: the box scrolls sideways, so every character that would be exported can be read.
    const shown = lines.slice(0, MAX_DIFF_LINES).map((line) => h("span", { class: `diff-line ${diffClass(line)}` }, line + "\n"));
    const cut = lines.length > MAX_DIFF_LINES;
    if (cut) shown.push(h("span", { class: "diff-line diff-meta" }, `… ${lines.length - MAX_DIFF_LINES} more lines not shown\n`));
    return { node: h("pre", { class: "diff", tabindex: "0", role: "region", "aria-label": label }, shown), shortened: cut };
  }
}

function diffClass(line) {
  if (line.startsWith("--- ") || line.startsWith("+++ ") || line.startsWith("@@")) return "diff-meta";
  if (line.startsWith("+")) return "diff-add";
  if (line.startsWith("-")) return "diff-del";
  return "diff-ctx";
}

export function renderUrlForm(document, { onSubmit, value } = {}) {
  const { h } = makeDom(document);
  const input = h("input", { id: "site-input", type: "text", autocomplete: "off", spellcheck: "false", placeholder: "example.com", value });
  const button = h("button", { type: "submit", class: "btn btn-primary" }, "Get verification steps");
  const form = h("form", {}, h("label", { for: "site-input" }, "Your site's address"), input,
    h("p", { class: "muted" }, "Use a site you control, for example example.com."), h("div", { class: "actions" }, button));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (typeof onSubmit === "function") onSubmit(input.value, button);
  });
  return h("div", { class: "card" }, h("h2", { id: "url-heading" }, "1. Your site"), form);
}

export function renderInstructions(document, instructions, { onVerify } = {}) {
  const { h } = makeDom(document);
  const i = isObject(instructions) ? instructions : {};
  const text = (v, n = CLIP.path) => clipText(v, n);
  const dns = i.dns_txt_name
    ? [h("p", {}, h("strong", {}, "Option B: add a DNS TXT record")),
       h("p", { class: "muted" }, "Name"), h("pre", { class: "snippet" }, h("code", {}, text(i.dns_txt_name))),
       h("p", { class: "muted" }, "Value"), h("pre", { class: "snippet" }, h("code", {}, text(i.dns_txt_value)))]
    : [h("p", {}, h("strong", {}, "Option B: DNS TXT record")), h("p", { class: "muted" }, text(i.dns_note, CLIP.note))];
  const verify = h("button", { type: "button", class: "btn btn-primary" }, "Verify and audit");
  verify.addEventListener("click", () => { if (typeof onVerify === "function") onVerify(verify); });
  return h("div", { class: "card" },
    h("h2", { id: "verify-heading", tabindex: "-1" }, "2. Prove you control the site"),
    h("p", {}, "Site: ", h("code", {}, text(i.domain))),
    h("p", {}, h("strong", {}, "Option A: add this tag")),
    h("p", { class: "muted" }, text(i.meta_tag_placement)),
    h("pre", { class: "snippet" }, h("code", {}, text(i.meta_tag, 400))),
    dns,
    h("p", {}, VERIFY_SENTENCE),
    h("div", { class: "actions" }, verify));
}

export function renderReport(document, report, handlers = {}) {
  const { h } = makeDom(document);
  const r = isObject(report) ? report : {};
  const findings = list(r.findings);
  const scored = r.score_status !== "not_scored" && typeof r.score === "number" && Number.isFinite(r.score);
  const unscored = Number.isFinite(r.unscored) ? Math.max(0, Math.floor(r.unscored)) : 0;

  const children = [h("h2", { id: "report-heading", tabindex: "-1" }, "3. Score and evidence"),
    h("p", { class: "muted" }, "Site: ", h("code", {}, clipText(r.site, CLIP.url))), scoreBlock()];
  if (unscored > 0) {
    children.push(h("p", { class: "muted" }, unscored === 1
      ? "1 check could not be reached and is not in the score."
      : `${unscored} checks could not be reached and are not in the score.`));
  }
  const cats = categoryScores();
  if (cats.length > 0) children.push(h("ul", { class: "categories" }, cats));
  children.push(h("h3", {}, "Findings"), ...categorySections());
  if (findings.length > MAX_FINDINGS) {
    children.push(h("p", { class: "muted" }, moreMarker(findings.length - MAX_FINDINGS, "finding", "findings") + " The score already includes them."));
  }
  children.push(h("div", { class: "actions" }, button("Propose fixes", "primary", handlers.onPropose),
    button("Re-audit with fresh pages", "secondary", handlers.onReaudit)));
  return h("div", { class: "card" }, children);

  function button(label, kind, onClick) {
    const b = h("button", { type: "button", class: `btn btn-${kind}` }, label);
    b.addEventListener("click", () => { if (typeof onClick === "function") onClick(b); });
    return b;
  }

  function scoreBlock() {
    if (!scored) {
      return h("div", { class: "score" }, h("p", { class: "score-band" }, "Not scored"),
        h("p", { class: "muted" }, "None of the checks could be completed, so there is no score."));
    }
    const value = Math.max(0, Math.min(100, Math.round(r.score)));
    return h("div", { class: "score" },
      h("span", { class: "score-number" }, String(value)), h("span", { class: "score-of" }, " out of 100"),
      h("p", { class: "score-band" }, band(value).label),
      h("progress", { value: String(value), max: "100", "aria-label": `Score ${value} out of 100` }));
  }

  function categoryScores() {
    const scores = isObject(r.category_scores) ? r.category_scores : {};
    const entries = Object.entries(scores).filter(([, v]) => typeof v === "number");
    const items = entries.slice(0, MAX_CATEGORY_SCORES).map(([name, v]) =>
      h("li", {}, h("strong", {}, categoryTitle(name)), ` ${Math.round(v)} out of 100`));
    if (entries.length > MAX_CATEGORY_SCORES) items.push(h("li", { class: "muted" }, moreMarker(entries.length - MAX_CATEGORY_SCORES, "category", "categories")));
    return items;
  }

  function categoryTitle(name) { return own(CATEGORY_TITLES, name) ?? clipText(name, CLIP.name); }

  function categorySections() {
    // One pass over at most MAX_FINDINGS findings: each goes straight into its category's group.
    const groups = new Map();
    for (const f of findings.slice(0, MAX_FINDINGS)) {
      const name = String(f.category);
      let g = groups.get(name);
      if (!g) { g = { attention: [], fine: [], unreachable: [] }; groups.set(name, g); }
      if (f.passed) g.fine.push(f);
      else if (f.status === "unreachable") g.unreachable.push(f);
      else g.attention.push(f);
    }
    const names = [...groups.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
    return names.map((name) => {
      const g = groups.get(name);
      const parts = [h("h3", {}, categoryTitle(name))];
      for (const [label, group] of [["Needs attention", g.attention], ["OK so far", g.fine], ["These could not be checked, not in the score", g.unreachable]]) {
        if (group.length > 0) parts.push(h("p", { class: "group-label" }, label), ...group.map(findingRow));
      }
      return h("section", { class: "category" }, parts);
    });
  }

  function findingRow(f) {
    const status = statusLabel(f.status);
    const title = own(CHECK_TITLES, f.check_id) ?? clipText(f.check_id, CLIP.name);
    return h("details", { class: "finding" },
      h("summary", {}, h("span", { class: "finding-title" }, title),
        h("span", { class: `chip ${STATUS_CLASS[status]}` }, status),
        h("span", { class: "chip chip-confidence" }, `confidence: ${confidenceLabel(f.confidence)}`)),
      h("div", { class: "finding-body" },
        h("p", {}, h("strong", {}, "What was checked: "), clipText(f.check, CLIP.check)),
        h("p", {}, h("strong", {}, "What was observed: "), clipText(f.evidence, CLIP.evidence)),
        h("p", {}, h("strong", {}, "Where: "), h("code", {}, clipText(f.url, CLIP.url)))));
  }
}

function rank(name) {
  const i = CATEGORY_ORDER.indexOf(name);
  return i === -1 ? CATEGORY_ORDER.length : i;
}
