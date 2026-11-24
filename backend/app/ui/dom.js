// The only way the page makes elements. Text from the audited site is always a text node (never parsed), tags and
// attributes come from closed lists, and a link can only point at an anchor inside this page (no view needs more).
const TAGS = new Set(["div", "span", "p", "h1", "h2", "h3", "ul", "li", "a", "button", "input", "label", "details",
  "summary", "pre", "code", "section", "header", "main", "footer", "form", "strong", "small", "table", "thead", "tbody",
  "tr", "th", "td", "progress"]);
const ATTRS = new Set(["class", "id", "type", "disabled", "hidden", "role", "for", "open", "value", "placeholder",
  "autocomplete", "spellcheck", "rel", "href", "download", "title", "max", "tabindex"]);
const PREFIXED = /^(aria|data)-[a-z0-9]+(-[a-z0-9]+)*$/;
const IN_PAGE = /^#[A-Za-z0-9_-]+$/;

export function safeHref(value) {
  return typeof value === "string" && IN_PAGE.test(value) ? value : null;
}

export function makeDom(document) {
  // A child must be a real node of this page: an instance of the DOM's own Node class (the document's, or the global
  // one in a browser). Look-alike objects with a nodeType are refused. Only where no Node class exists at all (a bare
  // test environment) does the check fall back to nodeType.
  const NodeClass = document?.Node ?? globalThis.Node;
  const isNode = (c) => (c.nodeType === 1 || c.nodeType === 3) && (typeof NodeClass !== "function" || c instanceof NodeClass);
  const text = (value) => document.createTextNode(String(value));

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function add(parent, child) {
    if (child === null || child === undefined || child === false || child === true) return;
    if (Array.isArray(child)) { child.forEach((c) => add(parent, c)); return; }
    if (typeof child === "string" || typeof child === "number") { parent.appendChild(text(child)); return; }
    if (typeof child === "object" && isNode(child)) { parent.appendChild(child); return; }
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
