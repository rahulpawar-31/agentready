import test from "node:test";
import assert from "node:assert/strict";
import { band, clipText, confidenceLabel, domainFromUrl, sanitizeFilename, siteFromInput, snapshotName, statusLabel } from "../../app/ui/format.js";

test("score bands have the documented boundaries", () => {
  const label = (s) => band(s).label;
  assert.equal(label(0), "Poor");
  assert.equal(label(49), "Poor");
  assert.equal(label(50), "Needs work");
  assert.equal(label(89), "Needs work");
  assert.equal(label(90), "Good");
  assert.equal(label(100), "Good");
  assert.equal(band("x").label, "Not scored");
  assert.equal(band(null).label, "Not scored");
});

test("status and confidence labels are exact and unknown values become 'unknown'", () => {
  assert.equal(statusLabel("detected"), "detected");
  assert.equal(statusLabel("not detected"), "not detected");
  assert.equal(statusLabel("unreachable"), "unreachable");
  assert.equal(statusLabel("<img onerror=1>"), "unknown");
  assert.equal(statusLabel(undefined), "unknown");
  assert.equal(confidenceLabel("high"), "high");
  assert.equal(confidenceLabel("weird"), "unknown");
});

test("filenames are made safe", () => {
  const s = sanitizeFilename("../../etc/passwd");
  assert.ok(!s.includes("/") && !s.includes("\\") && !s.startsWith("."), s);
  assert.equal(sanitizeFilename(""), "agentready-file.txt");
  assert.equal(sanitizeFilename("."), "agentready-file.txt");
  assert.equal(sanitizeFilename(null), "agentready-file.txt");
  assert.equal(sanitizeFilename("llms.txt"), "llms.txt");
  assert.equal(sanitizeFilename("a".repeat(200)).length, 64);
  assert.equal(sanitizeFilename("mcp server stub.py"), "mcp_server_stub.py");
  assert.ok(!sanitizeFilename("a\u202eb\x00c").match(/[^A-Za-z0-9._-]/));
});

test("snapshot names match the server pattern", () => {
  const n = snapshotName(new Date(Date.UTC(2026, 10, 24, 9, 5, 7)));
  assert.equal(n, "rerun-20261124090507");
  assert.match(n, /^[A-Za-z0-9_-]{1,64}$/);
});

test("domainFromUrl accepts plausible hosts only", () => {
  assert.equal(domainFromUrl("https://Example.COM/path"), "example.com");
  assert.equal(domainFromUrl("x.test:8443"), "x.test:8443");
  assert.equal(domainFromUrl("  http://x.test  "), "x.test");
  assert.equal(domainFromUrl("x.test\n"), "x.test");   // surrounding whitespace is trimmed, not rejected
  assert.equal(domainFromUrl("x.test/a?b#c"), "x.test");
  for (const bad of ["not a url", "javascript:alert(1)", "https://user:pw@x.test", "", "   ", "https://", "ftp://x.test",
                     "x.test:99999", "x.test:0", "[::1]:80", "exa mple.com", "аpple.test", "x.test.", "-x.test", "x.te\nst", null, undefined, 5]) {
    assert.equal(domainFromUrl(bad), null, String(bad));
  }
});

test("siteFromInput gives the URL to audit and its domain", () => {
  assert.deepEqual(siteFromInput("x.test"), { url: "https://x.test", domain: "x.test" });
  assert.deepEqual(siteFromInput("http://X.test:8080/a"), { url: "http://x.test:8080", domain: "x.test:8080" });
  assert.equal(siteFromInput("javascript:alert(1)"), null);
});

test("clipText clips and marks", () => {
  assert.equal(clipText("abcdef", 3), "abc…");
  assert.equal(clipText("abc", 3), "abc");
  assert.equal(clipText(undefined, 3), "");
  assert.equal(clipText({ toString() { return "xy"; } }, 5), "xy");
});

// ---------------------------------------------------------------- Group D
import { displayText } from "../../app/ui/format.js";

test("displayText removes bidi controls, zero-width characters, byte order marks and line separators", () => {
  const raw = "a\u202eb\u2066c\u200bd\u200de\ufeff" + "f\u2028g\u2029h\u0085i\u00adj";
  assert.equal(displayText(raw), "abcdefghij");
  assert.equal(displayText("https://x.test/\u202etxt.exe"), "https://x.test/txt.exe");
});

test("displayText turns unusual spaces into a plain space and keeps ordinary text", () => {
  assert.equal(displayText("a b c　d"), "a b c d");
  assert.equal(displayText("Plain text, with ünïcode and 日本語 and a plain space"), "Plain text, with ünïcode and 日本語 and a plain space");
  assert.equal(displayText("line one\nline two"), "line one\nline two");
});

test("displayText clips after cleaning and handles every kind of value", () => {
  assert.equal(displayText("a\u200b".repeat(500), 10), "a".repeat(10) + "…");
  assert.equal(displayText(null), "");
  assert.equal(displayText(undefined, 5), "");
  assert.equal(displayText(42), "42");
  assert.equal(displayText({ toString() { return "x\u202ey"; } }), "xy");
});

test("diffText shows invisible characters as visible markers instead of dropping them", async () => {
  const { diffText } = await import("../../app/ui/format.js");
  assert.equal(diffText("+a\u202eb"), "+a⟦U+202E⟧b");
  assert.equal(diffText(" x\u200by\ufeff"), " x⟦U+200B⟧y⟦U+FEFF⟧");
  assert.equal(diffText("-a\u2028b"), "-a⟦U+2028⟧b");
  assert.equal(diffText("+plain line with tabs\tand text"), "+plain line with tabs\tand text");
});

test("an address with a user name, an at sign or other punctuation is refused", async () => {
  const { domainFromUrl, siteFromInput } = await import("../../app/ui/format.js");
  for (const bad of ["user@x.test", "@x.test", "x@test", "x.test@evil.test", "x_test", "x test", "x.test/ x", "[::1]", "x.test:", ":80", "-x.test"]) {
    assert.equal(domainFromUrl(bad), null, JSON.stringify(bad));
    assert.equal(siteFromInput(bad), null, JSON.stringify(bad));
  }
  assert.equal(domainFromUrl("x.test:65535"), "x.test:65535");
  assert.equal(domainFromUrl("x.test:65536"), null);
});

test("a safe file name never contains two dots in a row, whatever the server sends", async () => {
  const { sanitizeFilename } = await import("../../app/ui/format.js");
  for (const raw of ["a..b", "a...b.txt", "../../etc/passwd", "x/../../y", "..", "....hidden", "a. .b"]) {
    const name = sanitizeFilename(raw);
    assert.ok(!name.includes(".."), `${raw} -> ${name}`);
    assert.match(name, /^[A-Za-z0-9._-]+$/);
    assert.ok(!name.startsWith("."));
  }
});
