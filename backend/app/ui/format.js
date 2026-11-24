// Small pure helpers: labels, bands, file and snapshot names, and the address check for the first step.
const STATUSES = new Set(["detected", "not detected", "unreachable"]);
const CONFIDENCES = new Set(["high", "medium", "low"]);

export function band(score) {
  if (typeof score !== "number" || !Number.isFinite(score)) return { label: "Not scored", key: "none" };
  if (score >= 90) return { label: "Good", key: "good" };
  if (score >= 50) return { label: "Needs work", key: "work" };
  return { label: "Poor", key: "poor" };
}

export const statusLabel = (value) => (STATUSES.has(value) ? value : "unknown");
export const confidenceLabel = (value) => (CONFIDENCES.has(value) ? value : "unknown");

export function sanitizeFilename(name) {
  const cleaned = String(name ?? "")
    .replace(/[^A-Za-z0-9._-]/g, "_")
    .replace(/\.{2,}/g, "_")
    .replace(/^\.+/, "")
    .slice(0, 64);
  return cleaned.replace(/_/g, "") === "" ? "agentready-file.txt" : cleaned;
}

export function snapshotName(now) {
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return "rerun-" + p(now.getUTCFullYear(), 4) + p(now.getUTCMonth() + 1) + p(now.getUTCDate()) +
    p(now.getUTCHours()) + p(now.getUTCMinutes()) + p(now.getUTCSeconds());
}

export function clipText(value, n) {
  const s = value === null || value === undefined ? "" : String(value);
  return s.length > n ? s.slice(0, n) + "…" : s;
}

// Invisible and direction-changing characters (category Cf: bidi controls, zero-width characters, byte order marks, soft
// hyphens), line and paragraph separators and U+0085. They can visually reorder or hide text in an address or a sentence.
const INVISIBLE = /[\p{Cf}\p{Zl}\p{Zp}\u0085]/gu;
const ODD_SPACE = /(?! )\p{Zs}/gu;                 // any space separator except the plain space

// Text from the audited site, made safe to show: invisible characters are dropped, odd spaces become a plain space,
// then the result is clipped to n characters.
export function displayText(value, n = Infinity) {
  const s = value === null || value === undefined ? "" : String(value);
  return clipText(s.replace(INVISIBLE, "").replace(ODD_SPACE, " "), n);
}

const marker = (ch) => `\u27e6U+${ch.codePointAt(0).toString(16).toUpperCase().padStart(4, "0")}\u27e7`;

// A diff line for review: nothing is dropped, so what is shown is what would be exported, but each invisible character
// is shown as a visible marker such as ⟦U+202E⟧.
export function diffText(line) {
  return String(line).replace(INVISIBLE, marker).replace(ODD_SPACE, marker);
}

const HOST = /^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::(\d{1,5}))?$/;

function parseInput(input) {
  if (typeof input !== "string") return null;
  const raw = input.trim();
  if (raw === "" || /[\s\u0000-\u001f\u007f]/.test(raw)) return null;
  const scheme = /^(https?):\/\//i.exec(raw);
  const rest = scheme ? raw.slice(scheme[0].length) : raw;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(rest)) return null;           // ftp://, a second scheme, and so on
  const authority = rest.split(/[/?#]/, 1)[0].toLowerCase();
  const m = HOST.exec(authority);
  if (!m) return null;
  if (m[1] !== undefined && (Number(m[1]) < 1 || Number(m[1]) > 65535)) return null;
  return { scheme: scheme ? scheme[1].toLowerCase() : "https", domain: authority };
}

export function domainFromUrl(input) {
  const parsed = parseInput(input);
  return parsed ? parsed.domain : null;
}

export function siteFromInput(input) {
  const parsed = parseInput(input);
  return parsed ? { url: `${parsed.scheme}://${parsed.domain}`, domain: parsed.domain } : null;
}
