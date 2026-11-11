// A stand-in for a browser's page, in node, shared by every test of a page's script: enough of the DOM for the app's
// pages (tests/render.js: app.js writes them as markup) and a dev page that builds its elements itself
// (document.createElement and the rest: dev/auditgui.html's h()) to run as they do in a browser, and
// for a test to read and use what it drew (querySelector, click, typing), with no browser. What a browser lays out
// (sizes, positions, scrolling) is all 0 here: a test that needs layout, pixels or axe-core needs the real browser.
//
//   const { page } = require("./dom.js");
//   const p = page(bodyHtml, { fetch, base, globals });   // a window: p.document, p.run(script) runs the page's own script
//   p.$(sel), p.$$(sel), p.text(el), p.click(el), p.type(el, text), p.check(el), p.button(text, within), p.wait(fn)
"use strict";
const vm = require("vm");

// ---------------------------------------------------------------- selectors (the ones dev pages and their tests use)
// a list of selectors (a, b), each compounds joined by " " (anywhere under) or ">" (just under); a compound is a tag,
// #id, .class, [attr], [attr=value], :checked, :not(<list>)
function splitTop(s, sep) {
  const out = []; let depth = 0, q = "", cur = "";
  for (const ch of s) {
    if (q) { if (ch === q) q = ""; cur += ch; continue; }
    if (ch === "\"" || ch === "'") { q = ch; cur += ch; continue; }
    if (ch === "(" || ch === "[") depth++;
    if (ch === ")" || ch === "]") depth--;
    if (depth === 0 && sep.test(ch)) { out.push(cur); cur = ""; continue; }
    cur += ch;
  }
  out.push(cur);
  return out;
}
function parseCompound(s) {
  const c = { tag: "", ids: [], classes: [], attrs: [], pseudo: [] };
  let i = 0;
  const name = () => { const m = /^[\w-]+/.exec(s.slice(i)); if (!m) throw new Error("selector: " + s); i += m[0].length; return m[0]; };
  if (s[0] === "*") i = 1; else if (/[\w]/.test(s[0] || "")) c.tag = name().toLowerCase();
  while (i < s.length) {
    const ch = s[i++];
    if (ch === "#") c.ids.push(name());
    else if (ch === ".") c.classes.push(name());
    else if (ch === "[") {
      const end = s.indexOf("]", i), body = s.slice(i, end); i = end + 1;
      const m = /^([\w-]+)\s*(?:=\s*(?:"([^"]*)"|'([^']*)'|([^\]]*)))?$/.exec(body.trim());
      if (!m) throw new Error("selector: " + s);
      c.attrs.push([m[1], m[2] ?? m[3] ?? (m[4] !== undefined ? m[4].trim() : undefined)]);
    } else if (ch === ":") {
      const p = name();
      if (s[i] === "(") {
        let depth = 1, j = i + 1;
        for (; j < s.length && depth; j++) { if (s[j] === "(") depth++; else if (s[j] === ")") depth--; }
        c.pseudo.push([p, s.slice(i + 1, j - 1)]); i = j;
      } else c.pseudo.push([p]);
    } else throw new Error("selector: " + s);
  }
  return c;
}
function parse(sel) {
  return splitTop(sel, /,/).map((one) => {
    const parts = [];
    let rel = " ";
    for (const tok of splitTop(one.replace(/\s*>\s*/g, " > ").trim(), /\s/).filter(Boolean)) {
      if (tok === ">") { rel = ">"; continue; }
      parts.push({ rel, c: parseCompound(tok) }); rel = " ";
    }
    return parts;
  });
}
function matchCompound(el, c) {
  if (!(el instanceof Element)) return false;
  if (c.tag && el.localName !== c.tag) return false;
  if (c.ids.some((id) => el.getAttribute("id") !== id)) return false;
  if (c.classes.some((k) => !el.classList.contains(k))) return false;
  for (const [k, v] of c.attrs) {
    if (!el.hasAttribute(k)) return false;
    const now = k === "value" && "value" in el ? el.value : el.getAttribute(k);
    if (v !== undefined && now !== v) return false;
  }
  for (const [p, arg] of c.pseudo) {
    if (p === "checked") { if (!(el.checked || el.selected)) return false; }
    else if (p === "not") { if (matchList(el, parse(arg))) return false; }
    else throw new Error("selector: :" + p + " isn't known here");
  }
  return true;
}
function matchChain(el, parts, k) {
  if (!matchCompound(el, parts[k].c)) return false;
  if (k === 0) return true;
  if (parts[k].rel === ">") return !!el.parentNode && matchChain(el.parentNode, parts, k - 1);
  for (let a = el.parentNode; a; a = a.parentNode) if (matchChain(a, parts, k - 1)) return true;
  return false;
}
function matchList(el, list) { return list.some((parts) => matchChain(el, parts, parts.length - 1)); }

// ---------------------------------------------------------------- nodes
const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"]);
const RAW = new Set(["script", "style", "textarea", "title"]);   // (their text is text, not markup)
const ENT = { amp: "&", lt: "<", gt: ">", quot: "\"", apos: "'", nbsp: "\u00a0", hellip: "…", mdash: "—", ndash: "–", middot: "·", times: "×", rarr: "→", larr: "←", copy: "©" };
const unescape = (s) => s.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (m, e) => (e[0] === "#" ? String.fromCodePoint(e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : +e.slice(1)) : ENT[e] ?? m));
class Node {
  constructor(doc) { this.ownerDocument = doc; this.parentNode = null; this.childNodes = []; }
  get parentElement() { return this.parentNode instanceof Element ? this.parentNode : null; }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get nextSibling() { const s = this.parentNode && this.parentNode.childNodes; return s ? s[s.indexOf(this) + 1] || null : null; }
  get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === this.ownerDocument; }
  remove() {
    const p = this.parentNode;
    if (p) { p.childNodes.splice(p.childNodes.indexOf(this), 1); this.parentNode = null; p._mut = (p._mut || 0) + 1; }
  }
  removeChild(k) { k.remove(); return k; }
  insertBefore(k, ref) { if (!ref) this.append(k); else this._insert(this.childNodes.indexOf(ref), [k]); return k; }
  contains(k) { for (let n = k; n; n = n.parentNode) if (n === this) return true; return false; }
  _kids(items) {
    return items.flat().filter((k) => k != null).map((k) => (k instanceof Node ? k : this.ownerDocument.createTextNode(String(k))));
  }
  _insert(at, items) {
    const kids = this._kids(items);
    for (const k of kids) k.remove();
    this.childNodes.splice(at, 0, ...kids);
    for (const k of kids) k.parentNode = this;
    this._mut = (this._mut || 0) + 1;
  }
  append(...items) { this._insert(this.childNodes.length, items); }
  appendChild(k) { this.append(k); return k; }
  prepend(...items) { this._insert(0, items); }
  after(...items) { const p = this.parentNode; if (p) p._insert(p.childNodes.indexOf(this) + 1, items); }
  before(...items) { const p = this.parentNode; if (p) p._insert(p.childNodes.indexOf(this), items); }
  replaceWith(...items) { const p = this.parentNode; if (p) { const at = p.childNodes.indexOf(this); this.remove(); p._insert(at, items); } }
  replaceChildren(...items) {
    const kids = this._kids(items);
    for (const k of this.childNodes.slice()) k.remove();
    this._insert(0, kids);
  }
  get textContent() { return this.childNodes.map((k) => k.textContent).join(""); }
  set textContent(v) { this.replaceChildren(String(v)); }
  // events: to the target, then up through its parents to the document and the window, unless stopped
  addEventListener(type, fn, o) { (this._on = this._on || []).push({ type, fn, once: !!(o && o.once) }); }
  removeEventListener(type, fn) { this._on = (this._on || []).filter((l) => l.type !== type || l.fn !== fn); }
  dispatchEvent(e) {
    e.target = e.target || this;
    const path = [];
    for (let n = this; n; n = n.parentNode) path.push(n);
    if (this.isConnected) path.push(this.ownerDocument.defaultView);
    for (const n of path) {
      e.currentTarget = n;
      for (const l of (n._on || []).filter((x) => x.type === e.type)) {
        if (l.once) n.removeEventListener(l.type, l.fn);
        l.fn.call(n, e);
      }
      if (e._stopped || !e.bubbles) break;
    }
    return !e.defaultPrevented;
  }
}
class Text extends Node {
  constructor(doc, data) { super(doc); this.data = data; this.nodeType = 3; }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
}
function event(type, props) {
  return Object.assign({ type, bubbles: true, defaultPrevented: false, _stopped: false,
    preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this._stopped = true; } }, props || {});
}
const camel = (s) => s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
const kebab = (s) => s.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
class Element extends Node {
  constructor(doc, tag) {
    super(doc);
    this.localName = tag.toLowerCase(); this.tagName = tag.toUpperCase(); this.nodeType = 1;
    this._attrs = new Map(); this._style = "";
    const el = this;
    this.dataset = new Proxy({}, {
      get: (_, k) => (typeof k === "string" ? el.getAttribute("data-" + kebab(k)) ?? undefined : undefined),
      set: (_, k, v) => { el.setAttribute("data-" + kebab(k), String(v)); return true; },
      deleteProperty: (_, k) => { el.removeAttribute("data-" + kebab(k)); return true; },
      has: (_, k) => el.hasAttribute("data-" + kebab(String(k))),
      ownKeys: () => [...el._attrs.keys()].filter((a) => a.startsWith("data-")).map((a) => camel(a.slice(5))),
      getOwnPropertyDescriptor: () => ({ enumerable: true, configurable: true }),
    });
    this.classList = {
      contains: (c) => el.className.split(/\s+/).includes(c),
      add: (...cs) => { for (const c of cs) if (!el.classList.contains(c)) el.className = (el.className + " " + c).trim(); },
      remove: (...cs) => { el.className = el.className.split(/\s+/).filter((x) => x && !cs.includes(x)).join(" "); },
      toggle: (c, on) => { const want = on === undefined ? !el.classList.contains(c) : !!on; want ? el.classList.add(c) : el.classList.remove(c); return want; },
    };
    // its style: each property, kept in its style attribute as the browser does (cssText, setProperty, el.style.color)
    const props = new Map();
    const write = () => { el._style = [...props].map(([k, v]) => k + ": " + v).join("; ") + (props.size ? ";" : ""); el.setAttribute("style", el._style); };
    const read = (text) => { props.clear(); for (const d of String(text).split(";")) { const i = d.indexOf(":"); if (i > 0) props.set(d.slice(0, i).trim().toLowerCase(), d.slice(i + 1).trim()); } };
    const api = {
      setProperty: (k, v) => { if (v === "" || v == null) props.delete(k); else props.set(k, String(v)); write(); },
      removeProperty: (k) => { const was = props.get(k) || ""; props.delete(k); write(); return was; },
      getPropertyValue: (k) => props.get(k) || "",
    };
    this.style = new Proxy({}, {
      get: (_, k) => (k === "cssText" ? el._style : k in api ? api[k] : typeof k === "string" ? props.get(kebab(k)) || "" : undefined),
      set: (_, k, v) => { if (k === "cssText") { read(v); write(); } else api.setProperty(kebab(k), v); return true; },
    });
  }
  getAttribute(k) { return this._attrs.has(k) ? this._attrs.get(k) : null; }
  setAttribute(k, v) { this._attrs.set(k, String(v)); }
  removeAttribute(k) { this._attrs.delete(k); }
  hasAttribute(k) { return this._attrs.has(k); }
  toggleAttribute(k, on) { const want = on === undefined ? !this.hasAttribute(k) : !!on; want ? this.setAttribute(k, "") : this.removeAttribute(k); return want; }
  get id() { return this.getAttribute("id") || ""; }
  set id(v) { this.setAttribute("id", v); }
  get className() { return this.getAttribute("class") || ""; }
  set className(v) { this.setAttribute("class", v); }
  get children() { return this.childNodes.filter((k) => k instanceof Element); }
  get childElementCount() { return this.children.length; }
  get firstElementChild() { return this.children[0] || null; }
  get nextElementSibling() {
    const s = this.parentNode ? this.parentNode.children : [];
    return s[s.indexOf(this) + 1] || null;
  }
  // the properties a page sets and reads, each kept as its attribute (as the browser does for these)
  get hidden() { return this.hasAttribute("hidden"); }
  set hidden(v) { this.toggleAttribute("hidden", !!v); }
  get disabled() { return this.hasAttribute("disabled"); }
  set disabled(v) { this.toggleAttribute("disabled", !!v); }
  get open() { return this.hasAttribute("open"); }
  set open(v) { this.toggleAttribute("open", !!v); }
  get title() { return this.getAttribute("title") || ""; }
  set title(v) { this.setAttribute("title", v); }
  get type() { return this.getAttribute("type") || (this.localName === "input" ? "text" : ""); }
  get name() { return this.getAttribute("name") || ""; }
  get checked() { return this._checked !== undefined ? this._checked : this.hasAttribute("checked"); }
  set checked(v) {
    this._checked = !!v;
    if (v && this.type === "radio" && this.name) {   // (one of a group of radios at a time)
      for (const r of this.ownerDocument.querySelectorAll("input[type=radio]")) if (r !== this && r.name === this.name) r._checked = false;
    }
  }
  get selected() { return this._selected !== undefined ? this._selected : this.hasAttribute("selected"); }
  set selected(v) { this._selected = !!v; }
  get options() { return this.querySelectorAll("option"); }
  get value() {
    if (this.localName === "select") {
      const o = this.options, on = o.filter((x) => x.selected);
      return (on[on.length - 1] || o[0] || { value: "" }).value;
    }
    if (this._value !== undefined) return this._value;
    if (this.localName === "textarea") return this.textContent;
    if (this.localName === "option") return this.hasAttribute("value") ? this.getAttribute("value") : this.textContent;
    return this.getAttribute("value") || "";
  }
  set value(v) {
    if (this.localName === "select") { for (const o of this.options) o.selected = o.value === String(v); return; }
    this._value = String(v);
  }
  get offsetParent() {   // (null when it isn't drawn: hidden, or under something hidden, or not on the page)
    if (!this.isConnected) return null;
    for (let n = this; n instanceof Element; n = n.parentNode) if (n.hidden) return null;
    return this.parentNode;
  }
  get offsetWidth() { return 0; }
  get offsetHeight() { return 0; }
  getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 }; }
  scrollIntoView() {}
  select() {}
  setSelectionRange() {}
  focus() { this.ownerDocument.activeElement = this; }
  blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = this.ownerDocument.body; }
  // a click, and what the browser does after it unless it's prevented: a summary opens or shuts its details, a
  // checkbox flips and a radio is picked (each saying so with change)
  click() {
    if (this.disabled) return;
    const box = this.localName === "input" && (this.type === "checkbox" || this.type === "radio");
    const was = this.checked;
    if (box) this.checked = this.type === "radio" ? true : !was;
    const go = this.dispatchEvent(event("click"));
    if (!go) { if (box) this.checked = was; return; }
    if (box && this.checked !== was) { this.dispatchEvent(event("input")); this.dispatchEvent(event("change")); }
    const sum = this.closest("summary");
    if (sum && sum.parentNode && sum.parentNode.localName === "details") sum.parentNode.open = !sum.parentNode.open;
  }
  matches(sel) { return matchList(this, parse(sel)); }
  closest(sel) { const list = parse(sel); for (let n = this; n instanceof Element; n = n.parentNode) if (matchList(n, list)) return n; return null; }
  querySelectorAll(sel) {
    const list = parse(sel), out = [];
    const walk = (n) => { for (const k of n.childNodes) if (k instanceof Element) { if (matchList(k, list)) out.push(k); walk(k); } };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  get outerHTML() {
    const attrs = [...this._attrs].map(([k, v]) => " " + k + (v === "" ? "" : '="' + v.replace(/&/g, "&amp;").replace(/"/g, "&quot;") + '"')).join("");
    if (VOID.has(this.localName)) return "<" + this.localName + attrs + ">";
    return "<" + this.localName + attrs + ">" + this.innerHTML + "</" + this.localName + ">";
  }
  // the markup given, as it was given, until its children change; then what they are now, written out
  get innerHTML() {
    if (this._raw !== undefined && this._rawAt === this._mut) return this._raw;
    const raw = RAW.has(this.localName);
    return this.childNodes.map((k) => (k instanceof Element ? k.outerHTML
      : raw ? k.textContent : k.textContent.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"))).join("");
  }
  set innerHTML(html) {
    this.replaceChildren(...parseHtml(this.ownerDocument, String(html), this.localName));
    this._raw = String(html); this._rawAt = this._mut;
  }
  set outerHTML(html) { const p = this.parentNode; if (p) { const at = p.childNodes.indexOf(this); this.remove(); p._insert(at, parseHtml(this.ownerDocument, String(html))); } }
  insertAdjacentHTML(where, html) {
    const kids = parseHtml(this.ownerDocument, String(html)), w = String(where).toLowerCase();
    if (w === "beforeend") this.append(...kids);
    else if (w === "afterbegin") this.prepend(...kids);
    else if (w === "beforebegin") this.before(...kids);
    else if (w === "afterend") this.after(...kids);
  }
}
class Document extends Element {
  constructor() {
    super(null, "#document");
    this.ownerDocument = this;
    this.documentElement = this.createElement("html");
    this.head = this.createElement("head"); this.body = this.createElement("body");
    this.documentElement.append(this.head, this.body);
    this.append(this.documentElement);
    this.activeElement = this.body;
    this.readyState = "complete"; this.title = ""; this.hidden = false; this.currentScript = null;
    this.fonts = { ready: Promise.resolve(), [Symbol.iterator]: function* () {} };
  }
  get isConnected() { return true; }
  createElement(tag) { return new Element(this, tag); }
  createTextNode(t) { return new Text(this, t); }
  importNode(n) { return n instanceof Text ? this.createTextNode(n.data) : parseHtml(this, n.outerHTML)[0]; }   // (a copy, as deep)
  getElementById(id) {
    const walk = (n) => { for (const k of n.childNodes) if (k instanceof Element) { if (k.getAttribute("id") === id) return k; const got = walk(k); if (got) return got; } return null; };
    return walk(this);
  }
}

// markup: tags, attributes, text and comments (the markup a page's own script writes, and its body's start)
function parseHtml(doc, html, into) {
  const top = doc.createElement("template"), stack = [top];
  if (RAW.has(into)) { top.append(html); return top.childNodes.slice(); }
  const re = /<!--[\s\S]*?-->|<!doctype[^>]*>|<\/([\w-]+)\s*>|<([\w-]+)((?:\s+[^\s"'>\/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*\/?>|([^<]+|<)/gi;
  let m;
  while ((m = re.exec(html))) {
    const cur = stack[stack.length - 1];
    if (m[1]) { const tag = m[1].toLowerCase(); if (stack.some((e) => e.localName === tag)) while (stack.length > 1 && stack.pop().localName !== tag); }
    else if (m[2]) {
      const el = doc.createElement(m[2]);
      for (const a of m[3].matchAll(/([^\s"'>\/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) el.setAttribute(a[1].toLowerCase(), unescape(a[2] ?? a[3] ?? a[4] ?? ""));
      cur.append(el);
      if (RAW.has(el.localName)) {   // (to its end tag, as text)
        const end = html.toLowerCase().indexOf("</" + el.localName, re.lastIndex);
        const stop = end < 0 ? html.length : end;
        if (stop > re.lastIndex) el.append(el.localName === "textarea" || el.localName === "title" ? unescape(html.slice(re.lastIndex, stop)) : html.slice(re.lastIndex, stop));
        re.lastIndex = stop;
      } else if (!VOID.has(el.localName) && !/\/\s*>$/.test(m[0])) stack.push(el);
    } else if (m[4]) cur.append(unescape(m[4]));
  }
  return top.childNodes.slice();
}

// ---------------------------------------------------------------- a window
function page(bodyHtml, opts) {
  const o = opts || {};
  const document = new Document();
  document.body.replaceChildren(...parseHtml(document, bodyHtml || ""));
  const store = new Map();
  const errors = [];
  const timers = new Set();
  const later = (fn, ms, ...a) => { const t = setTimeout(() => { timers.delete(t); try { fn(...a); } catch (e) { errors.push(e); } }, ms); timers.add(t); return t; };
  const win = {
    document, Node, Element, Text, console, URL, URLSearchParams, JSON, Math, Date, Promise, Object, Array, Set, Map, String, Number, Boolean, Error, RegExp, Symbol, Proxy,
    Intl, encodeURIComponent, decodeURIComponent, isNaN, parseInt, parseFloat, structuredClone,
    setTimeout: later, clearTimeout: (t) => { clearTimeout(t); timers.delete(t); },
    setInterval: () => 0, clearInterval: () => {},   // (nothing here repeats on its own: a test steps the page itself)
    requestAnimationFrame: (fn) => later(fn, 0), scrollX: 0, scrollY: 0, scrollTo() {}, scrollBy() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) },
    navigator: { clipboard: { writeText: async () => {} }, language: "en-US" },
    location: new URL(o.base || "http://localhost/"),
    fetch: (u, init) => (o.fetch || fetch)(new URL(typeof u === "string" ? u : u.url, o.base || "http://localhost/").href, init),
  };
  Object.assign(win, { TypeError, isFinite, TextEncoder, TextDecoder, sessionStorage: win.localStorage, innerWidth: 1280, innerHeight: 800,
    isSecureContext: true, open() {}, history: { pushState() {}, replaceState() {} },
    getSelection: () => ({ toString: () => "", rangeCount: 0 }),
    // (markup read as a page of its own: a whole document's, its parts in its body, as found)
    DOMParser: function () { return { parseFromString: (html) => { const d = new Document(); d.body.replaceChildren(...parseHtml(d, String(html))); return d; } }; },
    MutationObserver: function () { return { observe() {}, disconnect() {} }; }, ResizeObserver: function () { return { observe() {}, disconnect() {} }; } });
  Object.assign(win, o.globals || {});   // (what a page needs besides: a test's own answers, say)
  win.window = win; win.self = win; win.globalThis = win;
  document.defaultView = win;
  win.addEventListener = Node.prototype.addEventListener.bind(win);   // (bound: a page calls them bare too)
  win.removeEventListener = Node.prototype.removeEventListener.bind(win);
  vm.createContext(win);
  const text = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : "");
  const p = {
    window: win, document, errors,
    // the page's own script, in its window (an error thrown in it, or later in a handler or a promise, is kept)
    run(script) {
      try { vm.runInContext(script, win, { filename: "page.js" }); } catch (e) { errors.push(e); }
    },
    $: (sel, within) => (within || document).querySelector(sel),
    $$: (sel, within) => (within || document).querySelectorAll(sel),
    text,
    click(el) { if (!el) throw new Error("nothing to click"); el.focus(); el.click(); },
    type(el, value) { if (!el) throw new Error("nothing to type in"); el.focus(); el.value = value; el.dispatchEvent(event("input")); el.dispatchEvent(event("change")); },
    check(el) { if (!el) throw new Error("nothing to check"); if (!el.checked) el.click(); },
    // the button (or link) with these words in it, the first under within
    button(words, within) {
      return (within || document).querySelectorAll("button, a, summary").find((b) => text(b).includes(words)) || null;
    },
    // waits for fn() to be true, as the page fetches and draws (each answer from its server takes a moment)
    async wait(fn, what) {
      const until = Date.now() + 10000;
      for (;;) {
        let got = null;
        try { got = fn(); } catch (e) { got = null; }
        if (got) return got;
        if (Date.now() > until) {
          throw new Error("waited 10 s for " + (what || fn.toString()) +
            (errors.length ? "; the page's errors: " + errors.map((e) => String(e && e.stack || e)).join("\n") : ""));
        }
        await new Promise((ok) => setTimeout(ok, 20));
      }
    },
    close() { for (const t of timers) clearTimeout(t); timers.clear(); },
  };
  return p;
}

module.exports = { page, parse, Element, Document };
