import test from "node:test";
import assert from "node:assert/strict";
import { createFakeDocument, serialize } from "./fake-dom.mjs";
import { makeDom } from "../../app/ui/dom.js";

const doc = createFakeDocument();
const { h, safeHref, text, clear } = makeDom(doc);

test("site text is never parsed as markup", () => {
  const evil = '<img src=x onerror=alert(1)><script>alert(2)</script>"&';
  const node = h("div", {}, evil);
  assert.equal(node.children.length, 1);
  assert.equal(node.children[0].nodeType, 3);
  assert.equal(serialize(node), "<div>&lt;img src=x onerror=alert(1)&gt;&lt;script&gt;alert(2)&lt;/script&gt;&quot;&amp;</div>");
});

test("dangerous tags are refused", () => {
  for (const tag of ["script", "iframe", "img", "style", "link", "object", "embed", "svg", "form-x", "SCRIPT", ""]) {
    assert.throws(() => h(tag, {}, "x"), /tag not allowed/, tag);
  }
});

test("dangerous attributes are refused", () => {
  for (const a of ["onclick", "onerror", "onload", "ONCLICK", "style", "srcdoc", "innerHTML", "formaction", "src", "action",
                   "data", "aria", "aria-", "data-", "data-X", "xlink:href"]) {
    assert.throws(() => h("div", { [a]: "x" }), /attribute not allowed/, a);
  }
  // refused even when the value would be skipped, so a bad name can never hide behind a null
  assert.throws(() => h("div", { onclick: null }), /attribute not allowed/);
});

test("href accepts only fixed in-app destinations", () => {
  assert.equal(safeHref("#step-2"), "#step-2");
  assert.equal(safeHref("/ui/app.css"), "/ui/app.css");
  assert.equal(safeHref("/"), "/");
  const bad = ["javascript:alert(1)", "https://evil.test/", "//evil.test/", "/a//b", "/../x", "/a/..", "data:text/html,x", " /x",
               "/x?y=1", "/x#y", "", "#", "#a b", null, undefined, 5, "/x\n", "/x\\y", "/%2e%2e/x", "HTTP://x", "/a:b"];
  for (const b of bad) assert.equal(safeHref(b), null, String(b));
  assert.throws(() => h("a", { href: "https://evil.test/" }, "x"), /href/);
  assert.equal(serialize(h("a", { href: "#step-1" }, "go")), '<a href="#step-1">go</a>');
});

test("boolean and null attributes, aria and data attributes, nested children", () => {
  const b = h("button", { disabled: true, hidden: false, "aria-label": "Go", "data-x": "1", title: null }, ["a", null, ["b", 3]]);
  assert.equal(serialize(b), '<button disabled="" aria-label="Go" data-x="1">ab3</button>');
});

test("children that are not text or nodes are refused, never stringified into the page", () => {
  assert.throws(() => h("div", {}, { toString() { return "<b>"; } }), /child not allowed/);
  assert.equal(serialize(h("div", {}, 0, false, undefined, "z")), "<div>0z</div>");
});

test("a real node passes through and text() and clear() work", () => {
  const inner = h("span", {}, "in");
  const outer = h("p", {}, inner, text("<b>"));
  assert.equal(serialize(outer), "<p><span>in</span>&lt;b&gt;</p>");
  clear(outer);
  assert.equal(outer.children.length, 0);
});

test("attribute values from data are set as plain strings", () => {
  const n = h("div", { "data-fix": '"><script>alert(1)</script>' }, "x");
  assert.equal(serialize(n), '<div data-fix="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;">x</div>');
});
