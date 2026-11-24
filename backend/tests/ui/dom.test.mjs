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

// ---------------------------------------------------------------- Group D
test("only real nodes of this document family are accepted as children, never look-alike objects", () => {
  for (const fake of [{ nodeType: 1 }, { nodeType: 3, data: "x" }, { nodeType: 1, tagName: "script", children: [] }, Object.create(null, { nodeType: { value: 1 } })]) {
    assert.throws(() => h("div", {}, fake), /child not allowed/);
  }
  assert.doesNotThrow(() => h("div", {}, h("span", {}, "ok"), text("t")));
});

test("href accepts only an in-page anchor, because no view links anywhere else", () => {
  assert.equal(safeHref("#step-2"), "#step-2");
  for (const bad of ["/", "/ui/app.css", "/report/abc", "/x?y=1", "https://evil.test/", "//evil.test/", "javascript:alert(1)", "#", "#a b", "#a/b", "", null, 5, " #x", "#x\n"]) {
    assert.equal(safeHref(bad), null, String(bad));
  }
  assert.throws(() => h("a", { href: "/ui/app.css" }, "x"), /href/);
  assert.equal(serialize(h("a", { href: "#step-1" }, "go")), '<a href="#step-1">go</a>');
});
