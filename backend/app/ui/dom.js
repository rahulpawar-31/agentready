// The only way the page makes elements. Text from the audited site is always a text node (never parsed), tags and
// attributes come from closed lists, and links can only point at fixed places inside this page or this app.
const TAGS = new Set(["div", "span", "p", "h1", "h2", "h3", "ul", "li", "a", "button", "input", "label", "details",
  "summary", "pre", "code", "section", "header", "main", "footer", "form", "strong", "small", "table", "thead", "tbody",
  "tr", "th", "td", "progress"]);
const ATTRS = new Set(["class", "id", "type", "disabled", "hidden", "role", "for", "open", "value", "placeholder",
  "autocomplete", "spellcheck", "rel", "href", "download", "title", "max", "tabindex"]);
const PREFIXED = /^(aria|data)-[a-z0-9]+(-[a-z0-9]+)*$/;
const IN_PAGE = /^#[A-Za-z0-9_-]+$/;
const IN_APP = /^\/(?!\/)[A-Za-z0-9._~/-]*$/;

export function safeHref(value) {
  if (typeof value !== "string") return null;
  if (IN_PAGE.test(value)) return value;
  if (IN_APP.test(value) && !value.includes("..") && !value.includes("//")) return value;
  return null;
}

export function makeDom(document) {
  const text = (value) => document.createTextNode(String(value));

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function add(parent, child) {
    if (child === null || child === undefined || child === false || child === true) return;
    if (Array.isArray(child)) { child.forEach((c) => add(parent, c)); return; }
    if (typeof child === "string" || typeof child === "number") { parent.appendChild(text(child)); return; }
    if (typeof child === "object" && (child.nodeType === 1 || child.nodeType === 3)) { parent.appendChild(child); return; }
    throw new TypeError("child not allowed");
  }

  function h(tag, attrs = {}, ...children) {
    if (typeof tag !== "string" || !TAGS.has(tag)) throw new Error(`tag not allowed: ${String(tag)}`);
    const el = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs || {})) {
      if (!ATTRS.has(name) && !PREFIXED.test(name)) throw new Error(`attribute not allowed: ${name}`);
      if (value === null || value === undefined || value === false) continue;
      if (name === "href") {
        const safe = safeHref(value);
        if (safe === null) throw new Error("href not allowed");
        el.setAttribute("href", safe);
      } else {
        el.setAttribute(name, value === true ? "" : String(value));
      }
    }
    add(el, children);
    return el;
  }

  return { h, text, clear, safeHref };
}
