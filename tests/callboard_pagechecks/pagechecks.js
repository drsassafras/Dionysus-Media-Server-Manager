#!/usr/bin/env node
// Page checks: how each page of the project behaves for the person using it, drawn in Chromium by Playwright.
// callboard-template: pagechecks 1
// This file is a copy of a template (MyStandards' templates/pagechecks/, else callboard's callboard/templates/pagechecks/),
// kept in step with it: callboard says when the two differ. Say what's this project's in its settings file (the
// project's tests/pagechecks.json), never here.
//
//     node pagechecks.js [pagechecks.json] [--json]
//
// pagechecks.json: {"servers": {name: {"cmd": [...], "env": {...}, "ready": "/path"}} (each given a free port as
// {port}), "pages": [{"url"} or {"server", "path"}, with "label", "scheme": "light" | "dark", "storage": {key: value}],
// "wait_for": a selector the page has once it's drawn, "settle": ms after it, "live_wait", "settings", "probes": the
// checks to run (all of them, not set)}. It prints each finding, or with --json one object: {"pages", "findings":
// [{"severity", "message", "code"}]} or {"error"}. Exit 0 nothing found (warnings only too), 1 an error found, 2 it
// couldn't run. Playwright is the project's own (its node_modules), or NODE_PATH's.
"use strict";

const fs = require("fs");
const path = require("path");

async function startServer(name, spec, root) {
  const net = require("net"), { spawn } = require("child_process");
  const port = await new Promise((ok) => { const srv = net.createServer().listen(0, "127.0.0.1", () => { const p = srv.address().port; srv.close(() => ok(p)); }); });
  const cmd = spec.cmd.map((a) => String(a).replace("{port}", String(port)));
  const proc = spawn(cmd[0], cmd.slice(1), { cwd: root, env: { ...process.env, ...(spec.env || {}) }, stdio: "ignore" });
  const url = `http://127.0.0.1:${port}`;
  const stop = () => { try { proc.kill(); } catch { /* gone */ } };
  for (let i = 0; i < 120; i++) {
    if (proc.exitCode !== null) break;
    const up = await new Promise((ok) => {
      require("http").get(url + (spec.ready || "/"), (res) => { res.resume(); ok(res.statusCode < 500); }).on("error", () => ok(false));
    });
    if (up) return { url, stop };
    await new Promise((ok) => setTimeout(ok, 500));
  }
  stop();
  throw new Error(`the ${name} server didn't start within a minute (to see why: ${spec.cmd.join(" ")})`);
}

// How each page behaves for the person using it, drawn in Chromium by Playwright as axe-core's are (the same "a11y":
// its servers, pages, wait_for and settle), each check on a fresh copy of the page. The job's "probes" are the ones the
// project has on. "a11y" may also say "live_wait" (ms: how long the page's own updates are waited for; 4000) and
// "settings" ({"row", "info", "revert"}: selectors for a setting's row and its buttons where the page's own don't match
// the usual words; "default_ok": texts a setting's row may say "default" in).
//
//   steady_clicks       a control that opens or closes something (aria-expanded, a <summary>) stays where it was on the
//                       screen when clicked: what's above it closing doesn't move it
//   stays_open          what's opened stays open while the page updates itself
//   survives_reload     what's open, the tab picked and the place scrolled to come back after a reload
//   keeps_typing        what's typed in a field is still there after the page updates itself, and after a reload
//   search_holds        a search box keeps its text, its cursor and the focus while the page updates as it's typed in
//   focus_holds         the field in use keeps the focus while the page updates itself
//   no_surprise_scroll  what's on screen doesn't move while the page updates itself and nobody's touching it
//   keeps_selection     text selected (to copy it) stays selected while the page updates itself
//   info_buttons        each setting has a button that tells what it is (info, help, ?)
//   info_popovers       an info button opens a popover of the browser's own (the popover attribute: the top layer, so
//                       nothing covers or clips it; older browsers get it from a polyfill, or the page's own bubble)
//   info_paragraphs     an info button's bubble says each of its parts in a paragraph of its own (a <p>), led by its
//                       label in bold with a colon after it (Content: …, Warning: …, Saved as: …): no words outside a
//                       paragraph, and none that runs a second label on after its own words
//   revert_buttons      each setting has a button that puts it back (revert, reset, ↺)
//   no_default_words    a setting's own words don't say what its default is: its revert button does
const PROBE_SAYS = {
  steady_clicks: "moved on the screen when clicked", stays_open: "closed by itself while the page updated",
  survives_reload: "was lost on a reload", keeps_typing: "lost what was typed", search_holds: "was cut off while typing",
  focus_holds: "lost the focus while the page updated", no_surprise_scroll: "moved while nobody touched the page",
  keeps_selection: "lost the selection while the page updated", info_buttons: "has no info button",
  info_popovers: "has an info button that doesn't open a browser popover (the popover attribute)",
  info_paragraphs: "has an info bubble that doesn't say each part in a paragraph of its own, led by its label and a colon",
  revert_buttons: "has no revert button", no_default_words: "says its default in its own words",
};
const PROBE_SEVERITY = { info_buttons: "warning", info_popovers: "warning", revert_buttons: "warning", no_default_words: "warning" };
const MOVED = 4;   // px: more than this is a move a person sees

// In the page: the controls a check works on, each with a fingerprint that finds it again after the page draws it anew
function inPage() {
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden"; };
  const words = (el) => (el.getAttribute("aria-label") || el.getAttribute("title") || el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 60);
  const fp = (el, all) => { const w = words(el); return { tag: el.tagName, w, n: all.filter((x) => x.tagName === el.tagName && words(x) === w).indexOf(el) }; };
  const togglers = () => [...document.querySelectorAll("[aria-expanded], details > summary")].filter(shown);
  const typing = () => [...document.querySelectorAll("input, textarea")].filter((el) => shown(el) && !el.disabled && !el.readOnly
    && (el.tagName === "TEXTAREA" || ["", "text", "search", "email", "url", "tel", "number"].includes((el.getAttribute("type") || "").toLowerCase())));
  const isSearch = (el) => el.type === "search" || el.getAttribute("role") === "searchbox" || /search|find|filter/i.test((el.placeholder || "") + " " + (el.getAttribute("aria-label") || ""));
  window.__cb = {
    shown, words, togglers, typing, isSearch,
    list: (what) => { const all = what === "toggle" ? togglers() : typing(); return all.map((el) => fp(el, all)); },
    find: (what, f) => { const all = what === "toggle" ? togglers() : typing(); return all.filter((el) => el.tagName === f.tag && words(el) === f.w)[f.n] || null; },
    open: (el) => el.tagName === "SUMMARY" ? !!(el.parentElement && el.parentElement.open) : el.getAttribute("aria-expanded") === "true",
  };
}

async function run(plan, opts = {}) {
  const want = new Set(opts.probes || plan.probes || Object.keys(PROBE_SAYS));
  const live = Number(plan.live_wait) || 4000;
  const root = opts.root || process.cwd();
  if (!(plan.pages || []).length) return { pages: 0, findings: [] };   // (no page named: nothing to draw, no browser started)
  const { chromium } = opts.playwright || require("playwright");
  let browser;
  try {
    browser = await chromium.launch(opts.chromium ? { executablePath: opts.chromium } : {});
  } catch (e) {
    return { error: `Chromium won't start for Playwright (npx playwright install chromium): ${String(e.message || e).split("\n")[0]}` };
  }
  const servers = {};
  const found = new Map();   // probe + what -> { probe, what, where: [] }
  const say = (probe, what, page) => {
    const key = `${probe}\t${what}`;
    if (!found.has(key)) found.set(key, { probe, what, where: [] });
    if (!found.get(key).where.includes(page)) found.get(key).where.push(page);
  };
  let pages = 0;
  const fresh = async (job) => {
    const ctx = await browser.newContext({ colorScheme: job.scheme === "dark" ? "dark" : "light", viewport: { width: 1280, height: 900 } });
    if (job.storage) await ctx.addInitScript((o) => { try { for (const [k, v] of Object.entries(o)) localStorage.setItem(k, v); } catch { /* none */ } }, job.storage);
    await ctx.addInitScript(inPage);
    const p = await ctx.newPage();
    p.on("dialog", (d) => d.dismiss().catch(() => {}));
    await p.goto(job.url || `${servers[job.server].url}${job.path}`);
    await drawn(p);
    return { ctx, p };
  };
  const drawn = async (p) => {
    if (plan.wait_for) await p.waitForSelector(plan.wait_for, { timeout: 30000 }).catch(() => {});
    await p.waitForTimeout(plan.settle || 300);
  };
  const top = (p, what, f) => p.evaluate(([w, x]) => { const el = window.__cb.find(w, x); return el ? el.getBoundingClientRect().top : null; }, [what, f]);
  const name = (f) => f.w ? `"${f.w}"` : `a ${f.tag.toLowerCase()}`;

  const CHECKS = {
    async steady_clicks(p, label) {
      for (const f of (await p.evaluate(() => window.__cb.list("toggle"))).slice(0, 10)) {
        const url = p.url();
        await p.evaluate(([x]) => { const el = window.__cb.find("toggle", x); if (el) el.scrollIntoView({ block: "center" }); }, [f]);
        const was = await top(p, "toggle", f);
        if (was === null) continue;
        await p.evaluate(([x]) => window.__cb.find("toggle", x).click(), [f]);
        await p.waitForTimeout(400);
        if (p.url() !== url) { await p.goBack().catch(() => {}); await drawn(p); continue; }
        const now = await top(p, "toggle", f);
        if (now !== null && Math.abs(now - was) > MOVED) say("steady_clicks", `${name(f)} (${Math.round(now - was)}px)`, label);
      }
    },
    async stays_open(p, label) {
      const shut = [];
      for (const f of (await p.evaluate(() => window.__cb.list("toggle"))).slice(0, 5)) {
        const opened = await p.evaluate(([x]) => { const el = window.__cb.find("toggle", x); if (!el) return false; if (!window.__cb.open(el)) el.click(); return true; }, [f]);
        if (opened) { await p.waitForTimeout(200); if (await p.evaluate(([x]) => { const el = window.__cb.find("toggle", x); return !!el && window.__cb.open(el); }, [f])) shut.push(f); }
      }
      await p.waitForTimeout(live);
      for (const f of shut) if (!(await p.evaluate(([x]) => { const el = window.__cb.find("toggle", x); return !!el && window.__cb.open(el); }, [f]))) say("stays_open", name(f), label);
    },
    async survives_reload(p, label) {
      const f = (await p.evaluate(() => window.__cb.list("toggle").filter((x) => !window.__cb.open(window.__cb.find("toggle", x)))))[0];
      if (f) await p.evaluate(([x]) => window.__cb.find("toggle", x).click(), [f]);
      const tab = await p.evaluate(() => { const tabs = [...document.querySelectorAll('[role="tab"]')].filter(window.__cb.shown); const t = tabs.find((x) => x.getAttribute("aria-selected") !== "true"); if (!t) return null; t.click(); return window.__cb.words(t); });
      await p.waitForTimeout(300);
      const y = await p.evaluate(() => { const y = Math.floor((document.scrollingElement.scrollHeight - innerHeight) / 2); scrollTo(0, y); return scrollY; });
      await p.waitForTimeout(300);
      await p.reload();
      await drawn(p);
      if (f && !(await p.evaluate(([x]) => { const el = window.__cb.find("toggle", x); return !!el && window.__cb.open(el); }, [f]))) say("survives_reload", `${name(f)}, opened`, label);
      if (tab && !(await p.evaluate(([w]) => [...document.querySelectorAll('[role="tab"]')].some((t) => window.__cb.words(t) === w && t.getAttribute("aria-selected") === "true"), [tab]))) say("survives_reload", `the "${tab}" tab, picked`, label);
      if (y > 100 && Math.abs((await p.evaluate(() => scrollY)) - y) > 50) say("survives_reload", "the place scrolled to", label);
    },
    async keeps_typing(p, label) {
      const fields = (await p.evaluate(() => window.__cb.list("type").filter((x) => !window.__cb.isSearch(window.__cb.find("type", x))))).slice(0, 5);
      const typed = [];
      for (const [i, f] of fields.entries()) {
        const el = await p.evaluateHandle(([x]) => window.__cb.find("type", x), [f]);
        const text = /number/.test(await el.evaluate((e) => e.type)) ? String(40 + i) : `callboard ${i}`;
        await el.asElement()?.fill("").catch(() => {});
        await el.asElement()?.type(text).catch(() => {});
        typed.push([f, text]);
      }
      await p.waitForTimeout(live);
      const kept = (f, text) => p.evaluate(([x, t]) => { const el = window.__cb.find("type", x); return !!el && el.value === t; }, [f, text]);
      for (const [f, text] of typed) if (!(await kept(f, text))) say("keeps_typing", `${name(f)}, while the page updated`, label);
      await p.reload();
      await drawn(p);
      for (const [f, text] of typed) if (!(await kept(f, text))) say("keeps_typing", `${name(f)}, on a reload`, label);
    },
    async search_holds(p, label) {
      for (const f of (await p.evaluate(() => window.__cb.list("type").filter((x) => window.__cb.isSearch(window.__cb.find("type", x))))).slice(0, 2)) {
        const el = await p.evaluateHandle(([x]) => window.__cb.find("type", x), [f]);
        await el.asElement()?.click().catch(() => {});
        const text = "callboard";
        await p.keyboard.type(text, { delay: Math.ceil(live / text.length) });
        const got = await p.evaluate(([t]) => { const a = document.activeElement; return { value: a && "value" in a ? a.value : null, at: a && "selectionStart" in a ? a.selectionStart : null }; }, [text]);
        if (got.value !== text) say("search_holds", `${name(f)}: typed "${text}", has "${got.value === null ? "lost the focus" : got.value}"`, label);
        else if (got.at !== text.length) say("search_holds", `${name(f)}: the cursor moved`, label);
      }
    },
    async focus_holds(p, label) {
      const f = (await p.evaluate(() => window.__cb.list("type")))[0];
      if (!f) return;
      await p.evaluate(([x]) => window.__cb.find("type", x).focus(), [f]);
      await p.waitForTimeout(live);
      if (!(await p.evaluate(([x]) => document.activeElement === window.__cb.find("type", x), [f]))) say("focus_holds", name(f), label);
    },
    async no_surprise_scroll(p, label) {
      const at = await p.evaluate(() => {
        scrollTo(0, Math.floor((document.scrollingElement.scrollHeight - innerHeight) / 2));
        const el = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
        if (!el) return null;
        el.setAttribute("data-callboard-here", "");
        return { w: window.__cb.words(el), top: el.getBoundingClientRect().top };
      });
      if (!at) return;
      await p.waitForTimeout(live);
      const now = await p.evaluate(([w]) => { const el = document.querySelector("[data-callboard-here]") || [...document.querySelectorAll("body *")].find((x) => window.__cb.words(x) === w); return el ? el.getBoundingClientRect().top : null; }, [at.w]);
      if (now !== null && Math.abs(now - at.top) > MOVED) say("no_surprise_scroll", `the page, by ${Math.round(now - at.top)}px`, label);
    },
    async keeps_selection(p, label) {
      const text = await p.evaluate(() => {
        const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, { acceptNode: (n) => n.textContent.trim().length >= 12 && n.parentElement && window.__cb.shown(n.parentElement)
          && !n.parentElement.closest("button, a, input, textarea, select, script, style") ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT });
        const n = walk.nextNode();
        if (!n) return "";
        const r = document.createRange();
        r.setStart(n, 0); r.setEnd(n, Math.min(n.textContent.length, 40));
        getSelection().removeAllRanges(); getSelection().addRange(r);
        return getSelection().toString();
      });
      if (!text) return;
      await p.waitForTimeout(live);
      if ((await p.evaluate(() => getSelection().toString())) !== text) say("keeps_selection", `"${text.trim().slice(0, 40)}"`, label);
    },
  };
  // A setting's row: the page's own selector where the plan names one, else around each field the largest box that has
  // no other field; then its buttons and its words
  // Whether a setting's info button, clicked, shows its words in a popover of the browser's (an element with the
  // popover attribute, open: :popover-open). null where it can't be told (the page drew the button anew, or a popover
  // was open already); the bubble is closed again after (Escape, else the button once more).
  async function opensPopover(p, n, look) {
    const open = () => p.evaluate(() => [...document.querySelectorAll("[popover]")].some((x) => { try { return x.matches(":popover-open"); } catch { return false; } }));
    const mark = p.locator(`[data-cb-info="${n}"]`).first();
    if (!(await mark.count()) || (await open())) return null;
    try { await mark.click({ timeout: 2000 }); } catch { return null; }
    await p.waitForTimeout(150);
    const ok = await open();
    if (look) await look();
    await p.keyboard.press("Escape");
    await p.waitForTimeout(50);
    if ((await open()) && (await mark.count())) { try { await mark.click({ timeout: 2000 }); } catch { /* gone: nothing to close */ } }
    return ok;
  }
  // What's wrong with the open bubble's parts (info_paragraphs), "" for nothing, null where no bubble is open: the
  // browser popover open, else the page's own (role=tooltip) shown
  const bubbleParts = (p) => p.evaluate(() => {
    const open = (x) => { try { return x.matches(":popover-open"); } catch { return false; } };
    const pop = [...document.querySelectorAll("[popover]")].find(open) || [...document.querySelectorAll('[role="tooltip"]')].find((x) => window.__cb.shown(x));
    if (!pop) return null;
    const said = (x) => x.textContent.replace(/\s+/g, " ").trim();
    const paras = [...pop.querySelectorAll("p")];
    if (!paras.length) return said(pop) ? "isn't in paragraphs" : null;
    const loose = (el) => [...el.childNodes].some((x) => x.nodeType === 3 ? x.textContent.trim() : x.nodeType === 1 && x.tagName !== "P" && (x.querySelector("p") ? loose(x) : said(x)));
    if (loose(pop)) return "has words outside its paragraphs";
    for (const para of paras) {
      const text = said(para), lead = para.firstElementChild, label = lead ? said(lead) : "";
      if (!lead || !/^(B|STRONG)$/.test(lead.tagName) || !label || !text.startsWith(label)) return `has a paragraph with no bold label first ("${text.slice(0, 40)}")`;
      if (!/^[^:]+:$/.test(label)) return `has the label "${label}" with no colon after it`;
      const rest = text.slice(label.length);
      if ([...para.querySelectorAll("b, strong")].slice(1).some((x) => /:$/.test(said(x))) || /[.!?]\s+[A-Z][A-Za-z]*( [a-z]+)?:\s/.test(rest))
        return `runs a second part on in the "${label}" paragraph`;
    }
    return "";
  });
  const SETTING_CHECKS = ["info_buttons", "info_popovers", "info_paragraphs", "revert_buttons", "no_default_words"];
  async function settings(p, label) {
    const s = plan.settings || {};
    const rows = await p.evaluate(([s]) => {
      const C = window.__cb;
      const fields = [...document.querySelectorAll("input, select, textarea")].filter((el) => C.shown(el) && el.type !== "hidden" && !C.isSearch(el));
      const rows = s.row ? [...document.querySelectorAll(s.row)].filter(C.shown) : [...new Set(fields.map((el) => {
        let row = el;
        while (row.parentElement && row.parentElement !== document.body && row.parentElement.querySelectorAll("input, select, textarea").length === 1) row = row.parentElement;
        return row;
      }))];
      const find = (row, sel, re) => sel ? row.querySelector(sel) : [...row.querySelectorAll("button, a, [role=button]")].find((b) =>
        re.test(`${b.getAttribute("aria-label") || ""} ${b.getAttribute("title") || ""} ${b.textContent || ""}`.trim()));
      const button = (row, sel, re) => !!find(row, sel, re);
      return rows.map((row, n) => {
        const mark = find(row, s.info, /\b(info|help|about|details?|what)\b|ⓘ|ℹ|^\?$/i);
        if (mark) mark.setAttribute("data-cb-info", String(n));   // (found again to be clicked: info_popovers)
        const copy = row.cloneNode(true);
        copy.querySelectorAll("button, a, [role=button], option, select, input, textarea").forEach((x) => x.remove());
        const said = copy.textContent.replace(/\s+/g, " ").trim();
        return { name: said.slice(0, 60) || C.words(row), said, n,
          info: button(row, s.info, /\b(info|help|about|details?|what)\b|ⓘ|ℹ|^\?$/i),
          revert: button(row, s.revert, /\b(revert|reset|restore|undo)\b|back to|↺|⟲/i) };
      });
    }, [s]);
    const ok = (plan.settings || {}).default_ok || [];
    for (const r of rows) {
      if (!r.info && want.has("info_buttons")) say("info_buttons", `"${r.name}"`, label);
      if (r.info && (want.has("info_popovers") || want.has("info_paragraphs"))) {
        let parts = null;
        const opened = await opensPopover(p, r.n, async () => { parts = await bubbleParts(p); });
        if (want.has("info_popovers") && opened === false) say("info_popovers", `"${r.name}"`, label);
        if (want.has("info_paragraphs") && parts) say("info_paragraphs", `"${r.name}" (${parts})`, label);
      }
      if (!r.revert && want.has("revert_buttons")) say("revert_buttons", `"${r.name}"`, label);
      if (want.has("no_default_words") && /\bdefaults?\b/i.test(r.said) && !ok.some((t) => r.said.includes(t))) say("no_default_words", `"${r.name}"`, label);
    }
  }
  try {
    for (const [n, spec] of Object.entries(plan.servers || {})) servers[n] = await startServer(n, spec, root);
    for (const job of plan.pages || []) {
      const label = job.label || job.url || job.path;
      const run = [...Object.keys(CHECKS).filter((k) => want.has(k)).map((k) => [k, CHECKS[k]]),
                   ...(SETTING_CHECKS.some((k) => want.has(k)) ? [["settings", settings]] : [])];
      for (const [, check] of run) {
        const { ctx, p } = await fresh(job);
        try { await check(p, label); } finally { await ctx.close(); }
      }
      pages++;
    }
  } finally {
    await browser.close();
    for (const s of Object.values(servers)) s.stop();
  }
  const list = [...found.values()];
  return { pages, findings: list.map((f) => {
    const shown = f.where.slice(0, 3).join(", ") + (f.where.length > 3 ? ` and ${f.where.length - 3} more` : "");
    return { path: "", line: 0, col: 0, severity: PROBE_SEVERITY[f.probe] || "error", message: `${f.what} ${PROBE_SAYS[f.probe]}, on ${shown}`, code: f.probe };
  }) };
}


async function main(argv) {
  const json = argv.includes("--json");
  const file = argv.find((a) => !a.startsWith("--")) || path.join(__dirname, "pagechecks.json");
  let plan;
  try {
    plan = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (e) {
    const got = { error: `${file}: ${e.message}` };
    process.stdout.write(json ? JSON.stringify(got) : got.error + "\n");
    return 2;
  }
  let got;
  try {
    got = await run(plan, { root: process.env.PAGECHECKS_ROOT || process.cwd(), chromium: process.env.CALLBOARD_CHROMIUM || "" });
  } catch (e) {
    got = { error: String((e && e.message) || e).split("\n")[0] };
  }
  if (json) process.stdout.write(JSON.stringify(got));
  else if (got.error) process.stdout.write(`page checks couldn't run: ${got.error}\n`);
  else {
    for (const f of got.findings) process.stdout.write(`${f.severity}: ${f.message} (${f.code})\n`);
    process.stdout.write(`${got.pages} page${got.pages === 1 ? "" : "s"} checked, ${got.findings.length} found\n`);
  }
  return got.error ? 2 : got.findings.some((f) => f.severity === "error") ? 1 : 0;
}

module.exports = { run, startServer, PROBE_SAYS };
if (require.main === module) main(process.argv.slice(2)).then((code) => { process.exitCode = code; }, (e) => { console.error(e.message || e); process.exitCode = 2; });
