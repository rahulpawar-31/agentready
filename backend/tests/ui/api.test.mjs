import test from "node:test";
import assert from "node:assert/strict";
import { createApi } from "../../app/ui/api.js";

const ID = "0123456789abcdef0123456789abcdef";

function reply(status, body, { json = true } = {}) {
  return { status, ok: status >= 200 && status < 300,
    async json() { if (!json) throw new SyntaxError("not json"); return body; },
    async text() { return JSON.stringify(body); } };
}
function scripted(response) {
  const calls = [];
  const fetchImpl = async (path, init = {}) => {
    calls.push({ path, init });
    if (typeof response === "function") return response(path, init);
    return response;
  };
  return { fetchImpl, calls };
}

test("success returns the parsed JSON", async () => {
  const { fetchImpl } = scripted(reply(200, { id: ID, score: 70 }));
  assert.deepEqual(await createApi(fetchImpl).audit("https://x.test"), { ok: true, data: { id: ID, score: 70 } });
});

test("403 is 'unverified' and carries the verification instructions", async () => {
  const detail = { domain: "x.test", token: "t", meta_tag: "<meta>", meta_tag_placement: "head", dns_txt_name: "_agentready.x.test", dns_txt_value: "agentready-verify=t" };
  const { fetchImpl } = scripted(reply(403, { detail }));
  const r = await createApi(fetchImpl).audit("https://x.test");
  assert.equal(r.ok, false);
  assert.equal(r.kind, "unverified");
  assert.equal(r.status, 403);
  assert.deepEqual(r.instructions, detail);
});

test("each HTTP status maps to one fixed kind", async () => {
  const cases = [[422, { detail: "host does not resolve" }, "invalid"], [422, { detail: [{ loc: ["body"] }] }, "invalid"],
                 [404, { detail: "Unknown report" }, "not_found"], [409, { detail: "Approve this fix before exporting it" }, "conflict"],
                 [429, {}, "busy"], [503, {}, "busy"], [500, {}, "server"], [418, {}, "server"], [302, {}, "server"]];
  for (const [status, body, kind] of cases) {
    const { fetchImpl } = scripted(reply(status, body));
    const r = await createApi(fetchImpl).report(ID);
    assert.equal(r.ok, false, String(status));
    assert.equal(r.kind, kind, String(status));
    assert.equal(r.status, status);
    assert.equal(typeof r.message, "string");
  }
});

test("invalid and conflict messages are the server's short detail string, clipped; arrays get a fixed sentence", async () => {
  let r = await createApi(scripted(reply(422, { detail: "host does not resolve to a public address" })).fetchImpl).audit("https://x.test");
  assert.equal(r.message, "host does not resolve to a public address");
  r = await createApi(scripted(reply(422, { detail: "x".repeat(1000) })).fetchImpl).audit("https://x.test");
  assert.ok(r.message.length <= 201);
  r = await createApi(scripted(reply(422, { detail: [{ msg: "SECRET-TEXT" }] })).fetchImpl).audit("https://x.test");
  assert.equal(r.message, "The request was not valid.");
});

test("other messages are fixed sentences and never contain response text", async () => {
  for (const status of [404, 429, 500, 503, 302]) {
    const { fetchImpl } = scripted(reply(status, { detail: "SECRET-TEXT /Users/x stack trace", message: "SECRET-TEXT" }));
    const r = await createApi(fetchImpl).report(ID);
    assert.ok(!r.message.includes("SECRET"), String(status));
    assert.ok(!JSON.stringify(r).includes("SECRET"), String(status));
  }
});

test("a body that is not JSON is a server error, on success and on failure", async () => {
  for (const status of [200, 500]) {
    const { fetchImpl } = scripted(reply(status, null, { json: false }));
    const r = await createApi(fetchImpl).report(ID);
    assert.equal(r.ok, false);
    assert.equal(r.kind, "server");
  }
  const { fetchImpl } = scripted(reply(200, [1, 2, 3]));         // JSON, but not an object
  assert.equal((await createApi(fetchImpl).report(ID)).kind, "server");
});

test("a rejected fetch is a network error and its text is not shown", async () => {
  const fetchImpl = async () => { throw new TypeError("SECRET-TEXT connect ECONNREFUSED"); };
  const r = await createApi(fetchImpl).audit("https://x.test");
  assert.deepEqual([r.ok, r.kind, r.status], [false, "network", 0]);
  assert.ok(!r.message.includes("SECRET"));
});

test("a request that never answers times out as a network error", async () => {
  const fetchImpl = () => new Promise(() => {});
  const started = Date.now();
  const r = await createApi(fetchImpl, { timeoutMs: 20 }).audit("https://x.test");
  assert.equal(r.kind, "network");
  assert.ok(Date.now() - started < 1000);
});

test("invalid ids, domains, urls and snapshots never reach fetch", async () => {
  const { fetchImpl, calls } = scripted(reply(200, {}));
  const api = createApi(fetchImpl);
  const bad = ["", "nope", ID.toUpperCase(), ID + "0", "../x", ID + "/../x", `${ID}\n`, null, undefined, 5, {}];
  for (const id of bad) {
    for (const call of [() => api.report(id), () => api.proposeFixes(id), () => api.listFixes(id),
                        () => api.approve(id, "llms_txt"), () => api.reject(id, "llms_txt"), () => api.exportFix(id, "llms_txt")]) {
      assert.equal((await call()).kind, "invalid");
    }
  }
  for (const fix of ["", "LLMS", "llms txt", "../x", "a".repeat(33), "llms_txt/", null, 5]) {
    assert.equal((await api.approve(ID, fix)).kind, "invalid", String(fix));
    assert.equal((await api.exportFix(ID, fix)).kind, "invalid", String(fix));
  }
  for (const d of ["", null, undefined, 5, "x".repeat(400)]) assert.equal((await api.ownershipToken(d)).kind, "invalid");
  for (const u of ["", null, 5, "x".repeat(3000)]) assert.equal((await api.audit(u)).kind, "invalid");
  for (const s of ["../etc", "", "a b", "x".repeat(65), null]) assert.equal((await api.audit("https://x.test", s)).kind, "invalid");
  assert.equal(calls.length, 0);
});

test("each method sends the expected request, same-origin and relative", async () => {
  const { fetchImpl, calls } = scripted(reply(200, { ok: 1 }));
  const api = createApi(fetchImpl);
  await api.ownershipToken("x.test:8443");
  await api.audit("https://x.test", "rerun-20261124090507");
  await api.report(ID);
  await api.proposeFixes(ID);
  await api.listFixes(ID);
  await api.approve(ID, "llms_txt");
  await api.reject(ID, "json_ld");
  await api.exportFix(ID, "mcp_stub");
  const seen = calls.map((c) => [c.init.method, c.path]);
  assert.deepEqual(seen, [
    ["GET", "/ownership-token?domain=x.test%3A8443"], ["POST", "/audit"], ["GET", `/report/${ID}`],
    ["POST", `/report/${ID}/fixes`], ["GET", `/report/${ID}/fixes`], ["POST", `/report/${ID}/fixes/llms_txt/approve`],
    ["POST", `/report/${ID}/fixes/json_ld/reject`], ["GET", `/report/${ID}/fixes/mcp_stub/export`],
  ]);
  assert.deepEqual(JSON.parse(calls[1].init.body), { url: "https://x.test", snapshot: "rerun-20261124090507" });
  assert.equal(new Headers(calls[1].init.headers).get("content-type"), "application/json");
  for (const [i, c] of calls.entries()) {
    if (i !== 1) {
      assert.equal(c.init.body, undefined, c.path);
      assert.equal(new Headers(c.init.headers).get("content-type"), null, c.path);
    }
    assert.ok(c.path.startsWith("/") && !c.path.startsWith("//"), c.path);
    assert.equal(c.init.credentials, "same-origin");
    assert.equal(c.init.redirect, "error");
    assert.ok(c.init.signal, "every request has an abort signal");
  }
});

test("the default snapshot is sent as 'default'", async () => {
  const { fetchImpl, calls } = scripted(reply(200, {}));
  await createApi(fetchImpl).audit("https://x.test");
  assert.equal(JSON.parse(calls[0].init.body).snapshot, "default");
});

test("methods never throw, whatever the transport does", async () => {
  const api = createApi(() => { throw new Error("sync boom"); });
  const r = await api.report(ID);
  assert.equal(r.ok, false);
});

// ---------------------------------------------------------------- Group C
test("403 means 'unverified' only for the audit; on every other route it is 'forbidden' with a fixed sentence", async () => {
  const body = { detail: { token: "SECRET-TOKEN", meta_tag: "<meta>" } };
  const calls = {
    ownershipToken: (api) => api.ownershipToken("x.test"), report: (api) => api.report(ID), proposeFixes: (api) => api.proposeFixes(ID),
    listFixes: (api) => api.listFixes(ID), approve: (api) => api.approve(ID, "llms_txt"), reject: (api) => api.reject(ID, "llms_txt"),
    exportFix: (api) => api.exportFix(ID, "llms_txt"),
  };
  for (const [name, call] of Object.entries(calls)) {
    const r = await call(createApi(scripted(reply(403, body)).fetchImpl));
    assert.equal(r.kind, "forbidden", name);
    assert.equal(r.message, "The server refused this request.", name);
    assert.equal(r.instructions, undefined, name);
    assert.ok(!JSON.stringify(r).includes("SECRET-TOKEN"), name);
  }
  assert.equal((await createApi(scripted(reply(403, body)).fetchImpl).audit("https://x.test")).kind, "unverified");
});

test("a conflict carries the server's short detail clipped to 200 characters, and null when there is none", async () => {
  let r = await createApi(scripted(reply(409, { detail: "The fix changed after it was approved; approve it again" })).fetchImpl).approve(ID, "llms_txt");
  assert.equal(r.kind, "conflict");
  assert.equal(r.detail, "The fix changed after it was approved; approve it again");
  r = await createApi(scripted(reply(409, { detail: "d".repeat(900) })).fetchImpl).exportFix(ID, "llms_txt");
  assert.ok(r.detail.length <= 201);
  r = await createApi(scripted(reply(409, { detail: [{ msg: "SECRET-TEXT" }] })).fetchImpl).exportFix(ID, "llms_txt");
  assert.equal(r.detail, null);
  assert.ok(!JSON.stringify(r).includes("SECRET-TEXT"));
});
