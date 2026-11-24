// A tiny store. The report id is the only credential for approving and exporting fixes, so it lives only in this
// module's memory and in sessionStorage (so a reload keeps working). It is never part of the state object that views
// get, never in the URL and never logged.
const KEY = "agentready.report";
const REPORT_ID = /^[0-9a-f]{32}$/;

export function browserStorage() {
  try { return globalThis.sessionStorage ?? null; } catch { return null; }   // reading it can throw in some modes
}

export function createStore(storage) {
  let state = { step: "enter", site: null, domain: null, instructions: null, report: null, fixes: null, busy: false };
  let reportId = null;
  const listeners = new Set();

  const safely = (fn) => { try { return fn(); } catch { return undefined; } };

  return {
    get: () => ({ ...state }),
    set(patch) {
      const { reportId: _ignored, ...rest } = patch || {};
      state = { ...state, ...rest };
      for (const fn of [...listeners]) fn({ ...state });
    },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    getReportId: () => reportId,
    saveReportId(id) {
      if (typeof id !== "string" || !REPORT_ID.test(id)) return;
      reportId = id;
      if (storage) safely(() => storage.setItem(KEY, id));
    },
    loadReportId() {
      const value = storage ? safely(() => storage.getItem(KEY)) : null;
      if (typeof value === "string" && REPORT_ID.test(value)) { reportId = value; return value; }
      return null;
    },
    clearReportId() {
      reportId = null;
      if (storage) safely(() => storage.removeItem(KEY));
    },
  };
}
