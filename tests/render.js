// Renders a page of the web UI in node, from a state given on stdin — for the tests (test_plexcache.py,
// PageStructureTests): the page as the browser would draw it from one /api/state answer, with no
// browser. The app's own page (plexcache/web/index.html) on the shared stand-in page every page test uses
// (tests/dom.js), with i18n.js and app.js run in it. Usage: node tests/render.js <page> < state.json  → {"html", "text", "title", "info" (what's behind every ⓘ),
// "blocks" (what every red chip that stops a row says), "menu" (the count beside Tools, and "sub": the tools under it while a tool page is open)} on stdout,
// where the state is the server's /api/state (settings included) and <page> a path such as "system".
"use strict";
const fs = require("fs"), path = require("path");
const { page } = require("./dom.js");

process.on("unhandledRejection", (e) => { console.error("render.js: " + (e && e.stack || e)); process.exit(1); });
process.on("uncaughtException", (e) => { console.error("render.js: " + (e && e.stack || e)); process.exit(1); });
const state = JSON.parse(fs.readFileSync(0, "utf-8"));
const pageName = process.argv[2] || "health";
const web = process.env.PC_WEB || path.join(__dirname, "..", "plexcache", "web");  // (PC_WEB: the scripts as built, WebBuildTests)

// one answer: the state given; every other request stays pending (nothing else is read for a first draw)
const never = new Promise(() => {});
const answer = (got) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(got) });
function fetch(url) {
  const u = new URL(url).pathname;
  if (process.env.RENDER_DEBUG) console.error("render.js: fetch " + url);
  if (u.startsWith("/api/state")) return answer(state);
  if (u.startsWith("/api/processes") && state._proc) return answer(state._proc);
  if (u.startsWith("/api/library/health") && state._health) return answer(state._health);
  if (u.startsWith("/api/optimizer/files") && state._optfiles) return answer(state._optfiles);
  if (u.startsWith("/api/tools/readiness") && state._ready) return answer(state._ready);
  return never;
}
const html = fs.readFileSync(path.join(web, "index.html"), "utf-8");
const body = /<body[^>]*>([\s\S]*?)<script\b/i.exec(html)[1];
const p = page(body, {
  fetch, base: "http://localhost/" + pageName,
  globals: { location: { pathname: "/" + pageName, hash: "", search: "", origin: "http://localhost", href: "http://localhost/" + pageName, reload() {} },
    navigator: { language: "en-CA", languages: ["en-CA"], clipboard: { writeText: () => Promise.resolve() } } },
});
p.run(fs.readFileSync(path.join(web, "i18n.js"), "utf-8"));
p.run(fs.readFileSync(path.join(web, "app.js"), "utf-8"));
if (p.errors.length) { console.error("render.js: " + (p.errors[0] && p.errors[0].stack || p.errors[0])); process.exit(1); }
// the first draw comes after PCI18N.ready and the state's promise: a few turns of the event loop
setTimeout(() => {
  if (p.errors.length) { console.error("render.js: " + (p.errors[0] && p.errors[0].stack || p.errors[0])); process.exit(1); }
  const w = p.window, $ = (id) => p.document.getElementById(id);
  if (process.env.RENDER_DEBUG) console.error("render.js: locale " + (w.PCI18N && w.PCI18N.locale) + ", main drawn: " + !!($("main") && $("main").innerHTML));
  const main = $("main") ? $("main").innerHTML : "";
  const text = main.replace(/<style[\s\S]*?<\/style>/g, " ").replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/\s+/g, " ").trim();
  const mc = $("count-tools"), ts = $("toolsub");  // (the count beside Tools in the menu, and the tools under it)
  const menu = mc ? { html: mc.innerHTML, hidden: !!mc.hidden, cls: mc.className.split(/\s+/).filter(Boolean), title: mc.getAttribute("title"), sub: ts && !ts.hidden ? ts.innerHTML : "" } : null;
  p.close();
  // (all of it out before node stops: a long page is more than a pipe takes at once)
  process.stdout.write(JSON.stringify({ html: main, text, title: p.document.title, info: w.PCINFO || {}, blocks: w.PCBLOCKS || {}, menu }), () => process.exit(0));
}, 300);
