// The page's only network layer. It talks to this app's own REST API, same origin, relative paths only. Every method
// resolves to { ok: true, data } or { ok: false, kind, status, message, instructions? } and never throws. Messages are
// fixed sentences (the server's short `detail` string is passed through, clipped, only for `invalid` and `conflict`);
// response bodies and exception text are never shown.
import { clipText } from "./format.js";

const REPORT_ID = /^[0-9a-f]{32}$/;
const FIX_ID = /^[a-z_]{1,32}$/;
const SNAPSHOT = /^[A-Za-z0-9_-]{1,64}$/;

const MESSAGES = {
  invalid: "The request was not valid.",
  unverified: "Control of this site has not been proven yet.",
  not_found: "That report was not found. It may have been removed when the server restarted.",
  conflict: "That action is not allowed right now.",
  busy: "AgentReady is busy. Try again in a moment.",
  network: "Could not reach AgentReady. Check that it is running and try again.",
  server: "Something went wrong on the server. Try again.",
};

const failure = (kind, status = 0, message = MESSAGES[kind], extra = {}) => ({ ok: false, kind, status, message, ...extra });
const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

export function createApi(fetchImpl, { timeoutMs = 60000 } = {}) {
  async function request(method, path, body) {
    const controller = new AbortController();
    let timer;
    const timedOut = new Promise((_, reject) => {
      timer = setTimeout(() => { controller.abort(); reject(new Error("timeout")); }, timeoutMs);
    });
    const init = { method, headers: { accept: "application/json" }, credentials: "same-origin", redirect: "error",
                   cache: "no-store", signal: controller.signal };
    if (body !== undefined) {
      init.headers["content-type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    try {
      const res = await Promise.race([Promise.resolve().then(() => fetchImpl(path, init)), timedOut]);
      let data = null;
      try { data = await Promise.race([res.json(), timedOut]); } catch { data = null; }
      return interpret(res.status, data);
    } catch {
      return failure("network");
    } finally {
      clearTimeout(timer);
      timedOut.catch(() => {});
    }
  }

  function interpret(status, data) {
    if (status >= 200 && status < 300) {
      return isObject(data) ? { ok: true, data } : failure("server", status);
    }
    const detail = isObject(data) ? data.detail : undefined;
    if (status === 403) return failure("unverified", 403, MESSAGES.unverified, { instructions: isObject(detail) ? detail : null });
    if (status === 422) return failure("invalid", 422, typeof detail === "string" ? clipText(detail, 200) : MESSAGES.invalid);
    if (status === 409) return failure("conflict", 409, typeof detail === "string" ? clipText(detail, 200) : MESSAGES.conflict);
    if (status === 404) return failure("not_found", 404);
    if (status === 429 || status === 503) return failure("busy", status);
    return failure("server", status);
  }

  const validReport = (id) => typeof id === "string" && REPORT_ID.test(id);
  const validFix = (id) => typeof id === "string" && FIX_ID.test(id);
  const reportPath = (id) => `/report/${encodeURIComponent(id)}`;
  const fixPath = (id, fix) => `${reportPath(id)}/fixes/${encodeURIComponent(fix)}`;

  return {
    ownershipToken(domain) {
      if (typeof domain !== "string" || domain === "" || domain.length > 300) return Promise.resolve(failure("invalid"));
      return request("GET", `/ownership-token?domain=${encodeURIComponent(domain)}`);
    },
    audit(url, snapshot = "default") {
      if (typeof url !== "string" || url === "" || url.length > 2048) return Promise.resolve(failure("invalid"));
      if (typeof snapshot !== "string" || !SNAPSHOT.test(snapshot)) return Promise.resolve(failure("invalid"));
      return request("POST", "/audit", { url, snapshot });
    },
    report: (id) => (validReport(id) ? request("GET", reportPath(id)) : Promise.resolve(failure("invalid"))),
    proposeFixes: (id) => (validReport(id) ? request("POST", `${reportPath(id)}/fixes`) : Promise.resolve(failure("invalid"))),
    listFixes: (id) => (validReport(id) ? request("GET", `${reportPath(id)}/fixes`) : Promise.resolve(failure("invalid"))),
    approve: (id, fix) => (validReport(id) && validFix(fix) ? request("POST", `${fixPath(id, fix)}/approve`) : Promise.resolve(failure("invalid"))),
    reject: (id, fix) => (validReport(id) && validFix(fix) ? request("POST", `${fixPath(id, fix)}/reject`) : Promise.resolve(failure("invalid"))),
    exportFix: (id, fix) => (validReport(id) && validFix(fix) ? request("GET", `${fixPath(id, fix)}/export`) : Promise.resolve(failure("invalid"))),
  };
}
