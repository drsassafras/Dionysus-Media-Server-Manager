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

module.exports = { SHOT, isData, frozen, fontsFirst, settle };
