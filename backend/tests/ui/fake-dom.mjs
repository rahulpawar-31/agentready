// A minimal DOM for the UI unit tests. No parsing anywhere: text is only ever stored as text, so a test can prove that
// site text never turns into markup by looking at serialize().
let ACTIVE = null;

export class Node {
  constructor(nodeType, doc) {
    this.nodeType = nodeType;
    this.ownerDocument = doc;
    this.parentNode = null;
  }
}

export class TextNode extends Node {
  constructor(doc, data) { super(3, doc); this.data = String(data); }
  get textContent() { return this.data; }
}

export class Element extends Node {
  constructor(doc, tag) {
    super(1, doc);
    this.tagName = String(tag).toLowerCase();
    this.attributes = new Map();
    this.children = [];
    this.listeners = {};
    this.disabled = false;
  }
  get firstChild() { return this.children[0] ?? null; }
  appendChild(n) { n.parentNode = this; this.children.push(n); return n; }
  removeChild(n) { this.children = this.children.filter((c) => c !== n); n.parentNode = null; return n; }
  setAttribute(k, v) {
    this.attributes.set(k, String(v));
    if (k === "id") this.ownerDocument._ids.set(String(v), this);
    if (k === "disabled") this.disabled = true;
  }
  getAttribute(k) { return this.attributes.has(k) ? this.attributes.get(k) : null; }
  hasAttribute(k) { return this.attributes.has(k); }
  removeAttribute(k) { this.attributes.delete(k); if (k === "disabled") this.disabled = false; }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.children = []; this.appendChild(new TextNode(this.ownerDocument, v)); }
  get value() { return this._value ?? this.getAttribute("value") ?? ""; }
  set value(v) { this._value = String(v); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatch(type, event = {}) {
    const e = { type, target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...event };
    if (this.disabled && type === "click") return e;
    for (const fn of this.listeners[type] || []) fn(e);
    return e;
  }
  click() { return this.dispatch("click"); }
  focus() { ACTIVE = this; this.ownerDocument.activeElement = this; }
}

export function createFakeDocument() {
  const doc = { _ids: new Map(), activeElement: null, Node };
  doc.createElement = (tag) => new Element(doc, tag);
  doc.createTextNode = (s) => new TextNode(doc, s);
  doc.getElementById = (id) => doc._ids.get(id) ?? null;
  doc.body = doc.createElement("body");
  return doc;
}

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

export function serialize(node) {
  if (node.nodeType === 3) return esc(node.data);
  const attrs = [...node.attributes].map(([k, v]) => ` ${k}="${esc(v)}"`).join("");
  return `<${node.tagName}${attrs}>${node.children.map(serialize).join("")}</${node.tagName}>`;
}

export function findAll(root, predicate, out = []) {
  if (root.nodeType === 1) {
    if (predicate(root)) out.push(root);
    for (const c of root.children) findAll(c, predicate, out);
  }
  return out;
}
export const byTag = (root, tag) => findAll(root, (n) => n.tagName === tag);
export const byText = (root, tag, text) => findAll(root, (n) => n.tagName === tag && n.textContent.trim() === text);
export const allText = (root) => (root.nodeType === 3 ? root.data : root.textContent);
