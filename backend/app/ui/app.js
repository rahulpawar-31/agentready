// Wiring: the four steps, the buttons, download and copy. All text shown to the person is a fixed sentence from MSG
// or is placed as text by render.js. Fixes are exported only from an approved card, and the exported file is never
// written into the page: it goes to a download or to the clipboard.
import { createApi } from "./api.js";
import { makeDom } from "./dom.js";
import { clipText, sanitizeFilename, siteFromInput, snapshotName } from "./format.js";
import { renderFixes, renderInstructions, renderReport, renderUrlForm } from "./render.js";
import { browserStorage, createStore } from "./state.js";

const MSG = {
  badAddress: "That does not look like a site address. Use a name like example.com.",
  checking: "Checking your site. This takes about 10 seconds.",
  notVerified: "We could not find the verification tag or DNS record yet. Add one of them and try again.",
  busy: "AgentReady is busy. Try again in a moment.",
  network: "Could not reach AgentReady. Check that it is running and try again.",
  server: "Something went wrong on the server. Try again.",
  gone: "That report is no longer available. Run the audit again.",
  conflict: "That action is not allowed right now. Run a new audit if this keeps happening.",
  auditDone: "Audit finished.",
  reauditDone: "Re-audit finished. These results use freshly fetched pages.",
  proposing: "Preparing fixes.",
  approved: "Approved. Nothing has been changed on your site. Download or copy the file when you are ready.",
  revoked: "Approval revoked. Nothing has been changed on your site.",
  rejected: "Rejected. Nothing has been changed on your site.",
  copied: "Copied. Nothing has been changed on your site.",
  noCopy: "Copy is not available in this browser. Use Download instead.",
  downloaded: "Downloaded. Add the file to your site yourself; AgentReady has not changed it.",
  exportFailed: "The file could not be exported.",
};

function failureMessage(result) {
  switch (result.kind) {
    case "invalid": return result.message;
    case "unverified": return MSG.notVerified;
    case "not_found": return MSG.gone;
    case "conflict": return MSG.conflict;
    case "busy": return MSG.busy;
    case "network": return MSG.network;
    default: return MSG.server;
  }
}

// Browser-only helper: save a Blob through a temporary link. Kept out of dom.js on purpose, because that module never
// accepts a link that points outside this app.
export function makeDownloader(document, urlApi) {
  return (blob, filename) => {
    const link = document.createElement("a");
    const url = urlApi.createObjectURL(blob);
    link.href = url;
    link.download = sanitizeFilename(filename);
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    if (link.parentNode) link.parentNode.removeChild(link);
    urlApi.revokeObjectURL(url);
  };
}

export async function start({ document, fetch: fetchImpl, storage = null, clipboard = null, downloadBlob, now = () => new Date(), win = null }) {
  const { clear } = makeDom(document);
  const api = createApi(fetchImpl);
  const store = createStore(storage);
  const el = {
    status: document.getElementById("status"), url: document.getElementById("step-url"), verify: document.getElementById("step-verify"),
    report: document.getElementById("step-report"), fixes: document.getElementById("step-fixes"),
  };
  const pending = new Set();
  const idle = async () => { while (pending.size > 0) await Promise.allSettled([...pending]); };
  if (Object.values(el).some((node) => node === null || node === undefined)) return { idle, store };

  let busy = false;
  const say = (message) => { el.status.textContent = message; };
  const show = (section, node) => { clear(section); section.appendChild(node); section.removeAttribute("hidden"); };
  const hide = (section) => { clear(section); section.setAttribute("hidden", ""); };
  const focus = (id) => { const node = document.getElementById(id); if (node) node.focus(); };

  function run(button, task) {
    if (busy) return;
    busy = true;
    if (button) button.setAttribute("disabled", "");
    const promise = (async () => {
      try { await task(); }
      catch { say(MSG.server); }                // whatever went wrong, the person gets one fixed sentence and a free button
      finally { busy = false; if (button) button.removeAttribute("disabled"); }
    })();
    pending.add(promise);
    promise.finally(() => pending.delete(promise));
  }

  // ---- step 1 and 2
  function showUrlForm() { show(el.url, renderUrlForm(document, { onSubmit: onSite, value: store.get().domain ?? "" })); }

  function onSite(value, button) {
    const site = siteFromInput(value);
    if (!site) { say(MSG.badAddress); focus("site-input"); return; }
    run(button, async () => {
      say("");
      hide(el.report); hide(el.fixes); store.clearReportId();
      store.set({ site: site.url, domain: site.domain, report: null, fixes: null, step: "verify" });
      const result = await api.ownershipToken(site.domain);
      if (!result.ok) { say(failureMessage(result)); return; }
      show(el.verify, renderInstructions(document, result.data, { onVerify }));
      focus("verify-heading");
    });
  }

  function onVerify(button) {
    run(button, async () => {
      say(MSG.checking);
      const result = await api.audit(store.get().site);
      if (!result.ok) { say(failureMessage(result)); if (result.kind === "unverified") focus("verify-heading"); return; }
      acceptReport(result.data);
      hide(el.verify); hide(el.fixes);
      say(MSG.auditDone);
      focus("report-heading");
    });
  }

  // ---- step 3
  function acceptReport(data) {
    const { id, ...report } = data;
    const node = renderReport(document, report, { onPropose, onReaudit });     // drawn first: if it throws, nothing below happened
    store.saveReportId(id);
    store.set({ report, fixes: null, step: "report", site: report.site ?? store.get().site });
    show(el.report, node);
  }

  function onReaudit(button) {
    run(button, async () => {
      say(MSG.checking);
      const result = await api.audit(store.get().site, snapshotName(now()));
      if (!result.ok) { say(failureMessage(result)); return; }
      acceptReport(result.data);
      hide(el.fixes);
      say(MSG.reauditDone);
      focus("report-heading");
    });
  }

  // ---- step 4
  function showFixes(data) {
    const node = renderFixes(document, data, { onApprove: (id, b) => decide(id, b, "approve"), onReject: (id, b) => decide(id, b, "reject"),
      onDownload: onDownload, onCopy: onCopy });                                // drawn first, then stored and swapped in
    store.set({ fixes: data, step: "fixes" });
    show(el.fixes, node);
  }

  function onPropose(button) {
    run(button, async () => {
      const id = store.getReportId();
      if (!id) { say(MSG.gone); return; }
      say(MSG.proposing);
      const result = await api.proposeFixes(id);
      if (!result.ok) { say(failureMessage(result)); return; }
      showFixes(result.data);
      say("");
      focus("fixes-heading");
    });
  }

  function decide(fixId, button, action) {
    run(button, async () => {
      const id = store.getReportId();
      if (!id) { say(MSG.gone); return; }
      const wasApproved = (store.get().fixes?.fixes ?? []).some((f) => f.id === fixId && f.status === "approved");
      const result = await (action === "approve" ? api.approve(id, fixId) : api.reject(id, fixId));
      if (!result.ok) { say(failureMessage(result)); return; }
      const current = store.get().fixes ?? { fixes: [], skipped: [] };
      showFixes({ ...current, fixes: current.fixes.map((f) => (f.id === fixId ? result.data : f)) });
      say(action === "approve" ? MSG.approved : wasApproved ? MSG.revoked : MSG.rejected);
      focus("fixes-heading");
    });
  }

  async function exported(fixId) {
    const id = store.getReportId();
    if (!id) { say(MSG.gone); return null; }
    const result = await api.exportFix(id, fixId);
    if (!result.ok) { say(failureMessage(result)); return null; }
    const { filename, content } = result.data;
    if (typeof content !== "string" || typeof filename !== "string") { say(MSG.exportFailed); return null; }
    return { filename, content };
  }

  function onDownload(fixId, button) {
    run(button, async () => {
      const file = await exported(fixId);
      if (!file) return;
      downloadBlob(new Blob([file.content], { type: "text/plain" }), sanitizeFilename(file.filename));
      say(MSG.downloaded);
    });
  }

  function onCopy(fixId, button) {
    run(button, async () => {
      const file = await exported(fixId);
      if (!file) return;
      try {
        if (!clipboard || typeof clipboard.writeText !== "function") throw new Error("no clipboard");
        await clipboard.writeText(file.content);
        say(MSG.copied);
      } catch {
        say(MSG.noCopy);
      }
    });
  }

  // ---- printing: CSS cannot open a closed <details>, so open every finding row for the printout and put them back after
  if (win && typeof win.addEventListener === "function") {
    let opened = [];
    const findings = (node, out = []) => {
      for (const child of node.children ?? []) {
        if (String(child.tagName).toLowerCase() === "details" && String(child.getAttribute("class") ?? "").split(/\s+/).includes("finding")) out.push(child);
        findings(child, out);
      }
      return out;
    };
    win.addEventListener("beforeprint", () => {
      opened = findings(el.report).filter((d) => !d.hasAttribute("open"));
      for (const d of opened) d.setAttribute("open", "");
    });
    win.addEventListener("afterprint", () => {
      for (const d of opened) d.removeAttribute("open");
      opened = [];
    });
  }

  // ---- start up: show the form, and bring back a report from this browser tab if there is one
  showUrlForm();
  const saved = store.loadReportId();
  if (saved) {
    const restore = (async () => {
      try {
        const result = await api.report(saved);
        if (!result.ok) { if (result.kind === "not_found") store.clearReportId(); return; }
        acceptReport({ id: saved, ...result.data });
        const fixes = await api.listFixes(saved);
        if (fixes.ok) showFixes(fixes.data);
      } catch {
        say(MSG.server);                         // the address form is already on the page and stays usable
      }
    })();
    pending.add(restore);
    await restore;
    pending.delete(restore);
  }
  return { idle, store };
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  start({ document, fetch: (...args) => window.fetch(...args), storage: browserStorage(),
    clipboard: (typeof navigator !== "undefined" && navigator.clipboard) || null, downloadBlob: makeDownloader(document, URL), win: window })
    .catch(() => {});                            // the page is static HTML plus this script: a failure here must not become an unhandled error
}
