// The style sample's pictures (dev/style.py sample starts the demos and runs this; not run by hand): each look that
// differs from the style guide, found on the demo's pages and photographed Before (the app as approved) and After (now),
// in Classic and Dionysus, light and dark, and the two compared pixel for pixel.
//
//     node dev/style-sample.js <out dir>        # reads <out>/plan.json (dev/style.py writes it)
//
// The Before is the same demo's data with the approved app's pages, scripts, styles and themes: its requests for those
// go to the Before demo (plan.before), everything else to the After one, so both sides draw the same data. Both sides
// see the same moment (plan.time), so "3 min ago" reads alike, and the same data: the first answer to each of the app's
// data requests is kept and given to every page after it (dev/still.js frozen), and asked for once the page's fonts
// are in (fontsFirst: nothing measured in a fallback font). Every picture is taken still (still.js
// SHOT: no animation, transition or caret), so a look photographed twice comes out the same.
//
// Needs Playwright, as dev/screenshots.js does (npm ci --prefix dev && npx --prefix dev playwright install chromium; style.py passes
// NODE_PATH). It says how it's going on stdout, a line of JSON each, for style.py's progress bar (callboard/progress.py):
// {"total": n} first, {"page": "<what was looked at>"} after each page, {"partial": {"shots", "done": [id, ...]}} each
// time more looks have all their pictures (dev/style.py review shows those while the rest are taken), and last
// {"shots": {"<id>": [{"label", "before", "after", "same", "diff"}, ...]}}: the pictures of each look, two by two.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const { chromium } = require("playwright");
const { SHOT, isData, frozen, fontsFirst, settle, crops } = require("./still.js");

const [OUT] = process.argv.slice(2);
const plan = JSON.parse(fs.readFileSync(path.join(OUT, "plan.json"), "utf-8"));
const say = (o) => process.stdout.write(JSON.stringify(o) + "\n");
const lookName = (l) => (l.theme === "classic" ? "Classic" : "Dionysus") + " " + l.mode;
const WAIT = 2000;   // the page's first draw and its decorations (decor.js paints on the next frames)
const SIDES = plan.before ? ["before", "after"] : ["after"];
const AFTER = new URL(plan.after).origin;

// side "before": the approved app's files over today's data (judge.only: just those paths of it; judge.except: all
// but those: "document" the page itself, a path ending in / all under it).
const ANSWERS = new Map();   // the app's data, as first answered: every page of the sample is given the same (still.js)
async function open(browser, look, page, width, side, judge, media) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 }, deviceScaleFactor: 2, colorScheme: look.mode, ...(media || {}) });
  await ctx.clock.setFixedTime(plan.time);
  await ctx.addInitScript((o) => { try { localStorage.setItem("pc-theme", JSON.stringify(o)); } catch { /* none */ } }, look);
  await fontsFirst(ctx);
  const data = frozen(ANSWERS);
  await ctx.route("**/*", async (route) => {
    const req = route.request(), u = new URL(req.url());
    if (u.origin !== AFTER) return route.continue();
    if (side === "before") {
      const app = u.pathname.startsWith("/static/") || u.pathname === "/theme.css" || u.pathname === "/theme.js";
      const j = judge || {};
      const doc = req.resourceType() === "document";
      const kept = (j.except || []).some((e) => (e === "document" ? doc : e.endsWith("/") ? u.pathname.startsWith(e) : u.pathname === e));
      const mine = j.only ? j.only.includes(u.pathname) : (app || doc) && !kept;
      if (mine) {
        try {
          return await route.fulfill({ response: await route.fetch({ url: plan.before + u.pathname + u.search }) });
        } catch { return route.abort(); }
      }
    }
    return isData(req) ? data(route) : route.continue();
  });
  const p = await ctx.newPage();
  await p.goto(plan.after + "/" + page);
  await p.waitForTimeout(WAIT);
  await settle(p);
  return { ctx, p };
}

// Marks the first element on the page that wears the look, and is drawn; false if there's none.
async function find(p, f) {
  return p.evaluate((f) => {
    const seen = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    let els = [];
    if (f.how === "selector") {
      for (const s of f.selectors) { try { els = els.concat([...document.querySelectorAll(s)]); } catch { /* a selector the browser can't take */ } }
    } else if (f.how === "animation") {   // (an @keyframes: the first element running it)
      els = [...document.querySelectorAll("*")].filter((el) => getComputedStyle(el).animationName.split(/,\s*/).includes(f.name));
    } else if (f.how === "style") {
      const re = new RegExp(f.pattern);
      els = [...document.querySelectorAll("[style]")].filter((el) => re.test((el.getAttribute("style") || "").replace(/\s*([:;,])\s*/g, "$1")));
    }
    const el = els.find(seen);
    document.querySelectorAll("[data-style-sample]").forEach((x) => x.removeAttribute("data-style-sample"));
    if (!el) return false;
    el.setAttribute("data-style-sample", "");
    return true;
  }, f);
}

// What a still can't show by itself, put in front of the camera (dev/style.py finder): the states a look's selector
// names, forced on the element they're on (the browser's forced pseudo-classes, a form control's own property), and
// for a look that moves, every animation and transition paused at the same point of its run (seek). force says whether
// each state found its element: a look whose state couldn't be put on isn't pictured in it (caught: false), and the
// review never takes the same pictures for "nothing changed".
const FORCED = ["hover", "focus", "focus-visible", "focus-within", "active"];
async function force(p, f, on) {
  if (!f.states || !f.states.length) return true;
  const got = await p.evaluate(([targets, on]) => {
    const el = document.querySelector("[data-style-sample]");
    const marked = [...document.querySelectorAll("[data-style-state]")];
    if (!on) {
      for (const t of marked) {
        for (const s of ["checked", "disabled", "indeterminate"]) if (t.hasAttribute("data-was-" + s)) { t[s] = t.getAttribute("data-was-" + s) === "1"; t.removeAttribute("data-was-" + s); }
        t.removeAttribute("data-style-state");
      }
      return { ok: true, pseudo: [] };
    }
    if (!el) return { ok: false, pseudo: [] };
    let ok = true;
    const pseudo = new Set();
    for (const [sel, states] of targets) {
      let t = null;
      try {
        t = el.closest(sel);
        for (let s = el.previousElementSibling; !t && s; s = s.previousElementSibling) if (s.matches(sel)) t = s;   // (a + or ~)
        t = t || el.querySelector(sel);
      } catch { t = null; }
      if (!t) { ok = false; continue; }
      t.setAttribute("data-style-state", "");
      for (const s of states) {
        if (["checked", "disabled", "indeterminate"].includes(s)) {
          if (!(s in t)) { ok = false; continue; }
          t.setAttribute("data-was-" + s, t[s] ? "1" : "0");
          t[s] = true;
        } else pseudo.add(s);
      }
    }
    return { ok, pseudo: [...pseudo] };
  }, [f.states, on]);
  const cdp = p._sampleCdp || (p._sampleCdp = await p.context().newCDPSession(p));
  await cdp.send("DOM.enable");
  await cdp.send("CSS.enable");
  const root = (await cdp.send("DOM.getDocument", { depth: -1 })).root.nodeId;
  const ids = (await cdp.send("DOM.querySelectorAll", { nodeId: root, selector: on ? "[data-style-state]" : "[data-style-forced]" })).nodeIds;
  for (const nodeId of ids) await cdp.send("CSS.forcePseudoState", { nodeId, forcedPseudoClasses: on ? got.pseudo.filter((s) => FORCED.includes(s)) : [] });
  await p.evaluate((on) => {   // (which elements are forced, so they're let go of after)
    if (on) document.querySelectorAll("[data-style-state]").forEach((t) => t.setAttribute("data-style-forced", ""));
    else document.querySelectorAll("[data-style-forced]").forEach((t) => t.removeAttribute("data-style-forced"));
  }, on);
  await p.evaluate(() => new Promise((ok) => requestAnimationFrame(() => requestAnimationFrame(ok))));   // (its styles, and any transition, begun)
  return got.ok;
}
// Every animation and transition on the page paused ``at`` (0 to 1) of its run (one round of one that repeats):
// how many there were, so a filmstrip of a look that moves nothing says it caught nothing.
async function seek(p, at) {
  return p.evaluate((at) => {
    const all = document.getAnimations();
    for (const a of all) {
      a.pause();
      const t = a.effect ? a.effect.getComputedTiming() : null, delay = a.effect ? a.effect.getTiming().delay || 0 : 0;
      const d = t ? (isFinite(t.activeDuration) && t.activeDuration > 0 ? t.activeDuration : Number(t.duration) || 0) : 0;
      a.currentTime = delay + d * at;
    }
    return all.length;
  }, at);
}

// A stylesheet's look (dev/style.py: "css <selector>", "css <at-rule> | <selector>", "css @keyframes <name>") taken off
// the page (off) and put back: what the sample photographs once more without it, to see that the picture shows it at
// all. A look the picture doesn't change for — its element styled the same way by another rule — proves nothing by
// coming out the same Before and After, so it isn't "the same" (caught: false), and the maintainer judges it. Whether
// the rule was found.
async function rule(p, look, off) {
  return p.evaluate(([look, off]) => {
    const norm = (s) => String(s || "").replace(/'/g, '"').replace(/\s*([,>+~])\s*/g, "$1").replace(/\s+/g, " ").trim();
    const body = look.slice(4), cut = body.lastIndexOf(" | ");
    const ctx = cut >= 0 ? norm(body.slice(0, cut)) : "", sel = norm(cut >= 0 ? body.slice(cut + 3) : body);
    window.__sampleOff = window.__sampleOff || [];
    if (!off) {
      for (const [r, text] of window.__sampleOff) r.style ? (r.style.cssText = text) : null;
      for (const [sheet, at, text] of window.__sampleOff.filter((x) => x.length === 3).reverse()) sheet.insertRule(text, at);
      window.__sampleOff = [];
      return true;
    }
    let found = false;
    const walk = (rules, where, sheet) => {
      for (let i = rules.length - 1; i >= 0; i--) {
        const r = rules[i];
        if (r.cssRules && r.conditionText !== undefined) { walk(r.cssRules, norm("@" + (r.constructor.name === "CSSMediaRule" ? "media " : "supports ") + r.conditionText), r); continue; }
        if (sel.startsWith("@keyframes ") && r.name !== undefined && r.type === 7 && "@keyframes " + r.name === sel) {
          window.__sampleOff.push([sheet, i, r.cssText]); sheet.deleteRule(i); found = true; continue;
        }
        if (r.selectorText !== undefined && norm(r.selectorText) === sel && where === ctx) {
          window.__sampleOff.push([r, r.style.cssText]); r.style.cssText = ""; found = true;
        }
      }
    };
    for (const s of document.styleSheets) { try { walk(s.cssRules, "", s); } catch { /* another origin's sheet */ } }
    return found;
  }, [look, off]);
}

// Where the marked element is on the page (page coordinates), or null.
async function box(p) {
  return p.evaluate(() => {
    const el = document.querySelector("[data-style-sample]");
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return r.width && r.height ? { x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height } : null;
  });
}

// A crop of the same size on every side, round the look's element on each side at the same spot in the picture
// (still.js crops), wherever the page has it: a page taller above it on one side (a chart drawn without the new rule's
// styles) doesn't slide the element out of that side's picture. A side the element isn't on is cropped where the other
// side has it (that spot of the page then, for what was there). Never narrower than MIN_W nor taller than MAX_H (CSS
// pixels), so a tall, thin element (a vine down the page's edge) comes with the page beside it, not stretched into a
// strip. The element is measured again after the pictures: if it moved (the page laid itself out again while it was
// photographed), they're taken again where it is now, and if it still won't hold still, or a page's edge kept a crop
// from lining up on it, the pair says so (aligned: false), and the review page warns that it isn't a fair comparison.
const MIN_W = 640, MAX_H = 440, PAD = 24;
async function shoot(sides, file, shot) {
  const measure = async () => {
    const at = {};
    for (const [side, s] of Object.entries(sides)) {
      const size = await s.p.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }));
      at[side] = { box: s.box ? await box(s.p) : null, size };
    }
    return at;
  };
  const moved = (a, b) => Object.keys(a).some((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k]));
  let at = await measure();
  let out = {};
  for (let tries = 0; tries < 2; tries++) {
    const c = crops(at, { minW: MIN_W, maxH: MAX_H, pad: PAD });
    if (!c) return {};
    out = { clip: null, aligned: c.aligned, clips: c.clips };
    for (const side of Object.keys(sides)) {
      const clip = c.clips[side];
      out.clip = out.clip || clip;   // (where on the page it is: dev/style.py puts crops of one spot on one card)
      try { await sides[side].p.screenshot({ ...SHOT, ...(shot || {}), path: path.join(OUT, file(side)), clip, fullPage: true, timeout: 15000 }); out[side] = file(side); }
      catch { out[side] = null; }   // (it couldn't draw)
    }
    const now = await measure();
    if (!moved(at, now)) return out;
    at = now;
    out.aligned = false;   // (kept if the second try moves too)
  }
  return out;
}

// Before and After pixel for pixel: the same (no pixel off), or not, with a picture of the After that marks what's
// different (the rest faded), a box round each area of it (so a few pixels off in a whole page are found), and how
// many pixels differ in how many areas.
let judge = null;
async function compare(before, after, diffFile) {
  if (!before || !after) return { same: false };
  const a = fs.readFileSync(path.join(OUT, before)), b = fs.readFileSync(path.join(OUT, after));
  if (a.equals(b)) return { same: true };
  const r = await judge.evaluate(async ([a, b]) => {
    const load = (src) => new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = no; i.src = src; });
    const [ia, ib] = await Promise.all([load(a), load(b)]);
    const w = Math.max(ia.width, ib.width), h = Math.max(ia.height, ib.height);
    const px = (img) => { const c = document.createElement("canvas"); c.width = w; c.height = h; const g = c.getContext("2d"); g.drawImage(img, 0, 0); return g.getImageData(0, 0, w, h); };
    const da = px(ia), db = px(ib), out = new ImageData(w, h);
    let off = 0;
    for (let i = 0; i < da.data.length; i += 4) {
      const d = Math.abs(da.data[i] - db.data[i]) + Math.abs(da.data[i + 1] - db.data[i + 1]) + Math.abs(da.data[i + 2] - db.data[i + 2]) + Math.abs(da.data[i + 3] - db.data[i + 3]);
      if (d > 0) { off++; out.data.set([230, 20, 60, 255], i); }
      else { const v = 255 - (255 - (db.data[i] + db.data[i + 1] + db.data[i + 2]) / 3) * 0.25; out.data.set([v, v, v, 255], i); }
    }
    if (!off && ia.width === ib.width && ia.height === ib.height) return { same: true };
    // the areas: cells of CELL px with a pixel off, those touching taken together
    const CELL = 24, cw = Math.ceil(w / CELL), ch = Math.ceil(h / CELL), cells = new Uint8Array(cw * ch);
    for (let i = 0; i < da.data.length; i += 4) {
      if (out.data[i] === 230 && out.data[i + 1] === 20) { const p = i / 4; cells[Math.floor(Math.floor(p / w) / CELL) * cw + Math.floor((p % w) / CELL)] = 1; }
    }
    const boxes = [];
    for (let k = 0; k < cells.length; k++) {
      if (cells[k] !== 1) continue;
      let x0 = cw, y0 = ch, x1 = 0, y1 = 0;
      const todo = [k];
      cells[k] = 2;
      while (todo.length) {
        const q = todo.pop(), x = q % cw, y = Math.floor(q / cw);
        x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const nx = x + dx, ny = y + dy, n = ny * cw + nx;
          if (nx >= 0 && ny >= 0 && nx < cw && ny < ch && cells[n] === 1) { cells[n] = 2; todo.push(n); }
        }
      }
      boxes.push({ x: x0 * CELL, y: y0 * CELL, w: (x1 - x0 + 1) * CELL, h: (y1 - y0 + 1) * CELL });
    }
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    const g = c.getContext("2d");
    g.putImageData(out, 0, 0);
    g.strokeStyle = "#0a5adc"; g.lineWidth = 4;
    for (const bx of boxes) g.strokeRect(bx.x - 6, bx.y - 6, bx.w + 12, bx.h + 12);
    return { same: false, png: c.toDataURL("image/png").split(",")[1], px: off, areas: boxes.length };
  }, ["data:image/png;base64," + a.toString("base64"), "data:image/png;base64," + b.toString("base64")]);
  if (r.same) return { same: true };
  fs.writeFileSync(path.join(OUT, diffFile), Buffer.from(r.png, "base64"));
  return { same: false, diff: diffFile, px: r.px, areas: r.areas };
}

// A shipped image (theatre.svg and the like) as it draws, Before and After, at 2x on a plain card.
async function drawFile(browser, file, out) {
  if (!file) return null;
  const p = await browser.newPage({ viewport: { width: 200, height: 200 }, deviceScaleFactor: 2 });
  const ext = path.extname(file).slice(1);
  const mime = { svg: "image/svg+xml", ico: "image/x-icon", jpg: "image/jpeg" }[ext] || "image/" + ext;
  await p.setContent(`<body style="margin:0;display:grid;place-items:center;height:100vh;background:#fff"><img style="max-width:160px;max-height:160px" src="data:${mime};base64,${fs.readFileSync(file).toString("base64")}"></body>`);
  await p.waitForTimeout(100);
  await p.screenshot({ ...SHOT, path: path.join(OUT, out) });
  await p.close();
  return out;
}

// Several pages at once, in one browser (a context each), from one queue: the task with the lowest rank first (of
// those ranked alike, the first queued), and a task can queue more (a page looked through: its pictures). The sample
// is the same however they're shared out: each task keeps its result in its own place.
const WORKERS = Math.max(1, Math.min(8, os.cpus().length));
const tasks = [];
const idle = [];   // (workers waiting for a task to be queued, or for the last to finish)
let busy = 0, seq = 0, failed = null;
const wake = () => { while (idle.length) idle.shift()(); };
function queue(rank, fn) { tasks.push({ rank, seq: seq++, fn }); wake(); }
async function drained() {
  const worker = async () => {
    for (;;) {
      if (failed) throw failed;
      if (!tasks.length) {
        if (!busy) { wake(); return; }
        await new Promise((ok) => idle.push(ok));
        continue;
      }
      tasks.sort((a, b) => a.rank - b.rank || a.seq - b.seq);
      const t = tasks.shift();
      busy++;
      try { await t.fn(); } catch (e) { failed = failed || e; } finally { busy--; wake(); }
    }
  };
  await Promise.all(Array.from({ length: WORKERS }, worker));
  if (failed) throw failed;
}

(async () => {
  const browser = await chromium.launch();
  judge = await browser.newPage();
  const items = plan.items.filter((i) => i.find.how === "selector" || i.find.how === "style");
  const images = plan.items.filter((i) => i.find.how === "image");
  const pages = plan.items.some((i) => i.find.how === "pages");
  const first = plan.looks.find((l) => l.theme === "dionysus" && l.mode === "light") || plan.looks[0];
  // Where each look is: the first page (in Dionysus light) that draws it, after (or, for a look that's gone, before).
  const where = {};
  const sides = [...new Set(items.map((i) => i.side))];
  let total = (items.length ? plan.pages.length * sides.length : 0) + (pages ? plan.fixed.length * plan.looks.length : 0);
  say({ total: total, workers: WORKERS });
  // Each look's pictures as soon as all of them are taken: {"partial": {"shots", "done"}} says the looks done so far
  // (review shows them while the rest are photographed), the last {"shots"} all of them — the same, in the same order,
  // however the pages were shared out (assemble: from the tasks' results, in the order they were asked for).
  let comparing = Promise.resolve();   // (one comparison at a time on the one judging page, as one after another)
  const compared = (before, after, diffFile) => {
    const c = comparing.then(() => compare(before, after, diffFile));
    comparing = c.catch(() => {});
    return c;
  };
  const pair = async (label, before, after, tag, clip, aligned, caught) => {
    const c = await compared(before, after, tag + "-diff.png");
    return { label, before, after, same: c.same, diff: c.diff || null, px: c.px || 0, areas: c.areas || 0, clip: clip || null,
      ...(aligned === false ? { aligned: false } : {}), ...(caught === false ? { caught: false } : {}) };
  };
  const done = new Set();
  const drawn = new Array(images.length), shot = [], fixed = [];
  function assemble(only) {
    const shots = {};
    const add = (id, p) => { if (only.has(id)) (shots[id] = shots[id] || []).push(p); };
    for (const d of drawn) if (d && d.pair) add(d.im.id, d.pair);
    // (in the order of before: look by look, its pages in turn)
    for (const look of plan.looks) {
      for (const x of shot) if (x && x.look === look) for (const g of x.got) if (g.pair) add(g.it.id, g.pair);
      for (const x of fixed) {
        if (!x || x.look !== look) continue;
        for (const it of plan.items) {
          if (it.find.how !== "pages") continue;
          const m = /^theme (\w+)(?:\/(\w+))? /.exec(it.look);   // a theme's look: only in that theme (and mode)
          if (m && m[1] !== "font" && (m[1] !== look.theme || (m[2] && m[2] !== look.mode))) continue;
          const b = x.pair[JSON.stringify(it.find.judge)];
          add(it.id, { label: lookName(look) + " · /" + x.pg, before: b.before, after: x.after, same: b.same, diff: b.diff, px: b.px, areas: b.areas });
        }
      }
    }
    return shots;
  }
  let told = -1;
  const tell = () => { if (done.size !== told) { told = done.size; say({ partial: { shots: assemble(done), done: [...done] } }); } };
  tell();
  // One queue for all of it, the most wanted first (queue): the files; then each page found, and photographed in all
  // four looks as soon as it and the pages before it have been looked through — so the first change shows within
  // seconds, while the rest are still being looked for; the whole pages last. A change is shown once every look of
  // its page is taken: those on one page that changed the same spot are one change on the review page (dev/style.py
  // one_spot), so none is drawn again as another arrives. However they're shared out, the pictures come out the same,
  // in the same order (assemble).
  images.forEach((im, i) => queue(-1, async () => {
    const tag = "look" + im.id + "-file";
    const before = await drawFile(browser, im.find.files.before, tag + "-before.png"), after = await drawFile(browser, im.find.files.after, tag + "-after.png");
    const label = "the file " + im.find.path + (im.find.instead ? " (Before: " + im.find.instead + ", which it stands in for)" : "");
    drawn[i] = { im, pair: before || after ? await pair(label, before, after, tag) : null };
    done.add(im.id);
    tell();
  }));
  // Where each look is: the first page (in Dionysus light) that draws it, after (or, for a look that's gone, before) —
  // known for a page once it and every page before it have been looked through (settled)
  const found = plan.pages.map(() => ({})), waiting = {};   // (found: each page's looks, by side; waiting: its pictures still to take)
  let settled = 0;
  const settle = () => {
    while (settled < plan.pages.length && sides.every((sd) => found[settled][sd])) {
      const pg = plan.pages[settled], groups = {};
      for (const sd of sides) for (const id of found[settled][sd]) if (!(id in where)) where[id] = pg;
      for (const it of items) {
        if (where[it.id] !== pg) continue;
        const key = pg + " " + (it.find.width || 1280) + " " + JSON.stringify([it.find.judge || null, it.find.media || null]);   // (one Before per kind)
        (groups[key] = groups[key] || []).push(it);
      }
      for (const [key, list] of Object.entries(groups)) for (const look of plan.looks) shootPage(settled, pg, key, list, look);
      if (Object.keys(groups).length) { total += Object.keys(groups).length * plan.looks.length; say({ total: total, more: true }); }
      settled++;
    }
    if (settled === plan.pages.length) {   // (every page looked through: the looks on none of them, judged by their code)
      for (const it of items) if (!(it.id in where)) done.add(it.id);
      tell();
    }
  };
  function shootPage(at, pg, key, list, look) {
    const tag = look.theme + "-" + look.mode;
    waiting[pg] = (waiting[pg] || 0) + 1;
    const n = shot.length;
    shot.push(null);
    queue(at, async () => {
      const [, width, ...rest] = key.split(" ");
      const [judge, media] = JSON.parse(rest.join(" "));
      const open_ = {};
      for (const side of SIDES) open_[side] = await open(browser, look, pg, Number(width), side, judge, media);
      const got = [];
      for (const it of list) {
        const ss = {};
        let caught = true;
        for (const side of SIDES) {
          const p = open_[side].p;
          const there = await find(p, it.find);
          if (there && !(await force(p, it.find, true))) caught = false;   // (its states: on where it's drawn)
          ss[side] = { p, box: there ? await box(p) : null };
        }
        const states = (it.find.states || []).map((t) => t[1].join("+")).join(", ");
        const frames = it.find.frames || [null];
        for (const at of frames) {
          let moving = 0;
          if (at !== null) for (const side of SIDES) moving += await seek(ss[side].p, at);
          const part = (at === null ? "" : "-f" + Math.round(at * 100)) + (states ? "-state" : "");
          const opts = at === null ? null : { animations: "allow" };   // (a filmstrip's frame: everything paused just there, by seek)
          const g = await shoot(ss, (side) => "look" + it.id + "-" + tag + part + "-" + side + ".png", opts);
          const label = (states ? " · " + states : "") + (at === null ? "" : " · " + Math.round(at * 100) + "% through");
          // (a stylesheet's look: photographed again without it, After; the same picture means this spot doesn't show it)
          let shows = true;
          if (it.look.startsWith("css ") && g.after && ss.after && ss.after.box) {
            const p = ss.after.p;
            if (await rule(p, it.look, true)) {
              if (at !== null) await seek(p, at);
              const bare = "look" + it.id + "-" + tag + part + "-bare.png";
              let took = true;   // (the same crop as the After's, so the two line up pixel for pixel)
              try { await p.screenshot({ ...SHOT, ...(opts || {}), path: path.join(OUT, bare), clip: g.clips.after, fullPage: true, timeout: 15000 }); }
              catch { took = false; }
              await rule(p, it.look, false);
              if (at !== null) await seek(p, at);
              shows = !took || !(await compared(g.after, bare, bare.replace(".png", "-diff.png"))).same;
              for (const f of [bare, bare.replace(".png", "-diff.png")]) fs.rmSync(path.join(OUT, f), { force: true });
            } else shows = false;   // (its rule not found on the page to take off: nothing proves the picture shows it)
          }
          got.push({ it, pg, g, label, part, caught: caught && shows && (at === null || moving > 0) });
        }
        for (const side of SIDES) await force(ss[side].p, it.find, false);
      }
      for (const side of SIDES) await open_[side].ctx.close();
      say({ page: lookName(look) + ": " + pg });
      for (const x of got) {
        x.pair = x.g.before || x.g.after ? await pair(lookName(look) + " · /" + pg + x.label, x.g.before || null, x.g.after || null, "look" + x.it.id + "-" + tag + x.part, x.g.clip, x.g.aligned, x.caught) : null;
      }
      shot[n] = { look, got };
      if (!--waiting[pg]) {
        for (const x of got) done.add(x.it.id);
        for (const it of items) if (where[it.id] === pg) done.add(it.id);
        tell();
      }
    });
  }
  if (!items.length) settle();
  for (const side of sides) {
    plan.pages.forEach((pg, at) => queue(at + 0.5, async () => {
      const { ctx, p } = await open(browser, first, pg, 1280, side);
      const ids = [];
      for (const it of items) if (it.side === side && await find(p, it.find)) ids.push(it.id);
      await ctx.close();
      say({ page: "finding the looks: " + pg + (side === "before" ? " (before)" : "") });
      found[at][side] = ids;
      settle();
    }));
  }
  // Each look seen on whole pages is judged against a Before of its own (item.find.judge: dev/style.py judged_by).
  const variants = {};
  for (const it of plan.items) if (it.find.how === "pages") variants[JSON.stringify(it.find.judge)] = it.find.judge;
  for (const look of plan.looks) {
    const tag = look.theme + "-" + look.mode;
    if (!pages) continue;
    for (const pg of plan.fixed) {
      const n = fixed.length;
      fixed.push(null);
      queue(Infinity, async () => {
        const file = (side, v) => "page-" + pg + "-" + tag + "-" + side + (v ? "-" + v : "") + ".png";
        const after = file("after");
        let ctx, p;
        ({ ctx, p } = await open(browser, look, pg, 1280, "after"));
        await p.screenshot({ ...SHOT, path: path.join(OUT, after) });
        await ctx.close();
        const pair = {};
        let v = 0;
        for (const [key, judge] of Object.entries(variants)) {
          if (!plan.before) { pair[key] = { before: null, same: false, diff: null }; continue; }
          const k = String(++v);
          let before = file("before", k);
          ({ ctx, p } = await open(browser, look, pg, 1280, "before", judge));
          try { await p.screenshot({ ...SHOT, path: path.join(OUT, before), timeout: 15000 }); } catch { before = null; }   // (it couldn't draw)
          await ctx.close();
          const c = await compared(before, after, file("diff", k));
          pair[key] = { before, same: c.same, diff: c.diff || null, px: c.px || 0, areas: c.areas || 0 };
        }
        say({ page: lookName(look) + ": " + pg });
        fixed[n] = { look, pg, after, pair };
      });
    }
  }
  await drained();
  for (const it of plan.items) if (it.find.how === "pages") done.add(it.id);   // (seen on every fixed page: once they're all taken)
  tell();
  await browser.close();
  say({ shots: assemble(new Set(plan.items.map((it) => it.id))) });
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
