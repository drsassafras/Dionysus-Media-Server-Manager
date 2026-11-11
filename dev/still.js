// What makes a picture of the app the same every time the same look is photographed: the dev tools that take them
// (dev/style-sample.js, dev/screenshots.js) all do it through here, so none of them catches the page moving, or the data
// it draws changing underneath it.
//
//   SHOT            the options for every screenshot: no motion (Playwright's animations "disabled": a running animation,
//                   a comet round a busy button and the like, is put back to its start, one that ends is taken to its end,
//                   and a transition the same), and no text caret blinking in a field
//   frozen(answers) a route handler for the app's own data (GET /api/...): the first answer to each request is kept in
//                   `answers` (a Map shared by every page of a run) and every later page is given that same answer, so
//                   two pictures taken seconds apart draw the same data (a poll finishing, "just now", a light turning
//                   green in between would otherwise read as a change in the look)
//   fontsFirst(ctx) the page asks for its data only once every font it declares has loaded (an init script, on a context
//                   or a page): with font-display swap, what the page draws first is measured in the fallback font, and a
//                   comet round a badge (app.js comets) or a bar fitted to its text keeps that size until the next redraw;
//                   whether the fonts won that race changed the picture from one shot to the next
//   settle(page)    waits for the page's fonts and the frames after them, before it's photographed
//   crops(sides)    where to crop one element on each side of a comparison: the same size everywhere, and each side's
//                   crop round that side's own element at the same spot in the picture, so a page that's taller or
//                   shorter above it on one side doesn't slide the element out of that side's picture (Before and After
//                   would then show two different parts of the page, and the whole crop reads as changed)
//
// Needs Playwright, as the tools that use it do (npm ci --prefix dev && npx --prefix dev playwright install chromium).
"use strict";

const SHOT = Object.freeze({ animations: "disabled", caret: "hide" });

// Whether a request is for the app's data, which frozen() keeps: a GET under /api/ (a POST changes something: it's sent).
function isData(req) {
  return req.method() === "GET" && new URL(req.url()).pathname.startsWith("/api/");
}

function frozen(answers) {
  return async (route) => {
    const req = route.request();
    const key = req.url();
    if (!answers.has(key)) {
      answers.set(key, route.fetch().then(async (r) => {
        const headers = { ...r.headers() };
        delete headers["content-length"];     // (the body is given as it was read: decoded, its own length)
        delete headers["content-encoding"];
        delete headers["set-cookie"];
        return { status: r.status(), headers, body: await r.body() };
      }).catch(() => null));
    }
    const got = await answers.get(key);
    if (!got) { answers.delete(key); return route.abort(); }   // (nothing answered: asked again next time, not kept)
    return route.fulfill(got);
  };
}

function fontsFirst(target) {
  return target.addInitScript(() => {
    const real = window.fetch;
    let ready = null;
    const fonts = () => ready || (ready = new Promise((ok) => {
      if (document.readyState === "complete") ok(); else window.addEventListener("load", ok, { once: true });
    }).then(() => Promise.all([...document.fonts].map((f) => f.load().catch(() => null)))));
    window.fetch = function (input, init) {
      const u = new URL(typeof input === "string" ? input : input.url, location.href);
      return u.pathname.startsWith("/api/") ? fonts().then(() => real.call(this, input, init)) : real.call(this, input, init);
    };
  });
}

async function settle(page) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise((ok) => requestAnimationFrame(() => requestAnimationFrame(ok)));
  });
}

// sides: {name: {box: {x, y, w, h} (page coordinates) or null, size: {w, h} (the page's)}}; opts: {minW, maxH, pad}.
// Returns null when no side has the element, else {clips: {name: {x, y, width, height}}, aligned}: one size for every
// side (never narrower than minW nor taller than maxH, nor bigger than the smallest page), the element at the same
// offset in each side's crop (centred across, pad below the crop's top) wherever the element is on that side's page. A
// side without the element is cropped where the first side that has it has it (that spot of the page, for what was
// there). aligned is false when a page's edge kept a crop from being put round its element at that same offset: the
// pictures then don't show the element at the same spot, and the tool says so rather than comparing them as if they did.
function crops(sides, opts) {
  const { minW = 640, maxH = 440, pad = 24 } = opts || {};
  const all = Object.entries(sides);
  const found = all.filter(([, s]) => s.box);
  if (!found.length) return null;
  const bw = Math.max(...found.map(([, s]) => s.box.w)), bh = Math.max(...found.map(([, s]) => s.box.h));
  const pw = Math.min(...all.map(([, s]) => s.size.w)), ph = Math.min(...all.map(([, s]) => s.size.h));
  const width = Math.max(1, Math.min(pw, Math.max(minW, bw + 2 * pad)));
  const height = Math.max(1, Math.min(ph, bh + 2 * pad, maxH));
  const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), Math.max(lo, hi));
  // the element's offset in the crop, the one every side shares: as wanted, or as near as every page's edges allow
  const offset = (want, at, room) => {
    const lo = Math.max(...found.map(([, s]) => at(s.box) - room(s))), hi = Math.min(...found.map(([, s]) => at(s.box)));
    return { o: clamp(want, lo, hi), fits: lo <= hi };
  };
  const ox = offset((width - bw) / 2, (b) => b.x, (s) => s.size.w - width);
  const oy = offset(pad, (b) => b.y, (s) => s.size.h - height);
  const first = found[0][1].box;
  const clips = {};
  let aligned = ox.fits && oy.fits;
  for (const [name, s] of all) {
    const b = s.box || first;
    const x = clamp(b.x - ox.o, 0, s.size.w - width), y = clamp(b.y - oy.o, 0, s.size.h - height);
    if (s.box && (Math.abs(b.x - x - ox.o) > 0.5 || Math.abs(b.y - y - oy.o) > 0.5)) aligned = false;
    clips[name] = { x, y, width, height };
  }
  return { clips, aligned };
}

module.exports = { SHOT, isData, frozen, fontsFirst, settle, crops };
