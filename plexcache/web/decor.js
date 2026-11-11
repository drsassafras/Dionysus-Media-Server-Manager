// Dionysus's decorations: what a theme's "decor" turns on (plexcache/themes.py), drawn on whatever page is open.
//
// theme.js marks <html> with the theme's choices before the page draws: data-spots, data-vines and data-film (on | off).
// This file draws them:
//  * spots round the app and on the menu (a tile made here in the theme's colours);
//  * vines: a wavy line with a leaf now and then round every card, and down the seam between the menu and the page
//    (held beside the menu as the page scrolls). A card's colour stops at its vine (the vine is the card's border), and
//    so do the menu's and the page's at the seam;
//  * grapes: progress bars as a vine whose grapes ripen, and checkboxes as grapes, ripe when ticked;
//  * film: a reel unspooling a band of film under every page's title, its wave (some ending folded back over
//    themselves), its symbol (a flower or a column) and their colour picked from the page's name, so a page always looks
//    the same and no two pages quite match.
// Everything is drawn from the cards and titles the shared code makes (app.js: card markup, top()), so every page and
// tool gets the same, with nothing to add page by page. app.js calls PCDECOR.paint() after it draws a page; changes
// made inside a page later are caught by watching it.
(function () {
  "use strict";
  const d = document.documentElement;
  const on = function (k) { return d.getAttribute("data-" + k) === "on"; };
  const r1 = function (n) { return Math.round(n * 100) / 100; };
  function rng(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  function hs(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
  function ease(a, b, t) { const x = Math.max(0, Math.min(1, (t - a) / (b - a))); return x * x * (3 - 2 * x); }
  function css(name) { return getComputedStyle(d).getPropertyValue(name).trim(); }
  const enc = function (s) { return 'url("data:image/svg+xml;charset=utf-8,' + encodeURIComponent(s) + '")'; };

  // ------------------------------------------------------------------ spots
  // a tile of broken rings and dots, in one colour at a low strength, that repeats without a seam
  function spotTile(color, a, seed) {
    const R = rng(seed), T = 180, n = 5; let o = "";
    for (let gy = 0; gy < n; gy++) for (let gx = 0; gx < n; gx++) {
      const cell = T / n, cx = gx * cell + cell / 2 + (R() - .5) * cell * .45 + (gy % 2 ? cell / 4 : 0), cy = gy * cell + cell / 2 + (R() - .5) * cell * .45;
      const rr = 5.5 + R() * 3, segs = 3 + Math.floor(R() * 2), start = R() * 360, sw = 1.8 + R() * 1.2;
      for (let k = 0; k < segs; k++) {
        const a0 = (start + k * 360 / segs) * Math.PI / 180, a1 = a0 + (360 / segs) * (.45 + R() * .25) * Math.PI / 180, jr = rr * (.85 + R() * .3);
        const x0 = cx + jr * Math.cos(a0), y0 = cy + jr * Math.sin(a0), x1 = cx + jr * Math.cos(a1), y1 = cy + jr * Math.sin(a1);
        [-T, 0, T].forEach(function (ox) { [-T, 0, T].forEach(function (oy) {
          if (Math.abs(cx + ox - T / 2) < T / 2 + 12 && Math.abs(cy + oy - T / 2) < T / 2 + 12)
            o += '<path d="M' + r1(x0 + ox) + " " + r1(y0 + oy) + "A" + r1(jr) + " " + r1(jr) + " 0 0 1 " + r1(x1 + ox) + " " + r1(y1 + oy) + '" stroke-width="' + r1(sw) + '"/>';
        }); });
      }
      o += '<circle cx="' + r1(cx) + '" cy="' + r1(cy) + '" r="' + r1(rr * .55) + '" fill="' + color + '" fill-opacity="' + r1(a * .45) + '" stroke="none"/>';
      if (R() < .5) o += '<circle cx="' + r1(cx + cell * .42) + '" cy="' + r1(cy - cell * .3) + '" r="' + r1(1 + R()) + '" fill="' + color + '" fill-opacity="' + a + '" stroke="none"/>';
    }
    return enc('<svg xmlns="http://www.w3.org/2000/svg" width="' + T + '" height="' + T + '"><g fill="none" stroke="' + color + '" stroke-opacity="' + a + '" stroke-linecap="round">' + o + "</g></svg>");
  }
  function spots() {
    const a = parseFloat(css("--spots-a")) || 0;
    if (!on("spots") || !a) { d.style.removeProperty("--spots-img"); d.style.removeProperty("--menu-spots-img"); return; }
    d.style.setProperty("--spots-img", spotTile(css("--spots"), a, 31));
    d.style.setProperty("--menu-spots-img", spotTile(css("--menu-spots"), Math.max(a, .06), 11));
  }

  // ------------------------------------------------------------------ vines
  const leafAt = function (x, y, ang, len, g) { return '<path d="M0 0q' + r1(len / 2) + " " + r1(-len * .42) + " " + len + " 0q" + r1(-len / 2) + " " + r1(len * .42) + " " + (-len) + ' 0z" transform="translate(' + r1(x) + " " + r1(y) + ") rotate(" + r1(ang) + ')" fill="' + g + '"/>'; };
  const curlAt = function (x, y, s, g) { return '<path d="M' + r1(x) + " " + r1(y) + "c0 " + 4 * s + " 2 " + 7 * s + " 5 " + 7 * s + "c2.4 0 3 " + (-3 * s) + " 1 " + (-3.2 * s) + '" fill="none" stroke="' + g + '" stroke-width="1.3" stroke-linecap="round"/>'; };
  // a leaf growing out of the vine: a short stalk from the line, then the leaf, so it always joins the line
  function leafOn(x, y, ang, len, c) {
    const a = ang * Math.PI / 180, cx = Math.cos(a), sy = Math.sin(a);
    return '<path d="M' + r1(x - cx * .6) + " " + r1(y - sy * .6) + "L" + r1(x + cx * 2.8) + " " + r1(y + sy * 2.8) + '" stroke="' + c + '" stroke-width="1.2" stroke-linecap="round" fill="none"/>' + leafAt(x + cx * 2.2, y + sy * 2.2, ang, len, c);
  }
  // a wavy line round a rounded box of w x h; inset keeps the whole wave inside the box (for clipping to it)
  function edge(w, h, rad, seed, inset) {
    const x0 = .5, y0 = .5, W = w - 1, H = h - 1, r = Math.min(rad, W / 2, H / 2);
    const Ls = W - 2 * r, Ms = H - 2 * r, A = Math.PI * r / 2, seg = [Ls, A, Ms, A, Ls, A, Ms, A], P = 2 * Ls + 2 * Ms + 4 * A;
    function at(s) {
      s = ((s % P) + P) % P; let i = 0; while (i < 7 && s > seg[i]) { s -= seg[i]; i++; }
      function arc(cx, cy, a0) { const a = a0 + (r ? s / r : 0); return [cx + r * Math.cos(a), cy + r * Math.sin(a), Math.cos(a), Math.sin(a)]; }
      switch (i) { case 0: return [x0 + r + s, y0, 0, -1]; case 1: return arc(x0 + W - r, y0 + r, -Math.PI / 2); case 2: return [x0 + W, y0 + r + s, 1, 0]; case 3: return arc(x0 + W - r, y0 + H - r, 0);
        case 4: return [x0 + W - r - s, y0 + H, 0, 1]; case 5: return arc(x0 + r, y0 + H - r, Math.PI / 2); case 6: return [x0, y0 + H - r - s, -1, 0]; default: return arc(x0 + r, y0 + r, Math.PI); }
    }
    const R = rng(seed), n1 = Math.max(2, Math.round(P / (80 + R() * 30))), n2 = Math.max(5, Math.round(P / (30 + R() * 8))), p1 = R() * 6.3, p2 = R() * 6.3, A1 = 1.1, A2 = .45;
    const sh = inset ? -(A1 + A2 + .7) : 0;
    function pt(s) { const q = at(s), o = sh + A1 * Math.sin(s / P * n1 * 2 * Math.PI + p1) + A2 * Math.sin(s / P * n2 * 2 * Math.PI + p2); return [q[0] + q[2] * o, q[1] + q[3] * o, q[2], q[3]]; }
    const step = P > 6000 ? 4 : 2.5; let path = "";
    for (let s = 0; s < P; s += step) { const q = pt(s); path += (s ? "L" : "M") + r1(q[0]) + " " + r1(q[1]); }
    path += "z";
    // a leaf or a curl every 200 to 620 px along the line, mostly leaning outward
    function deco(c) {
      let o = "";
      for (let s = 60 + R() * 240; s < P - 30; s += 200 + R() * 422) {
        const q = pt(s), tang = Math.atan2(q[2], -q[3]) * 180 / Math.PI, out = R() < .65;
        if (R() < .7) o += leafOn(q[0], q[1], out ? tang - (38 + R() * 20) : tang + (38 + R() * 20), 7 + R() * 2, c);
        else o += '<g transform="rotate(' + r1(tang) + " " + r1(q[0]) + " " + r1(q[1]) + ')">' + curlAt(q[0], q[1], out ? -1 : 1, c) + "</g>";
      }
      return o;
    }
    return { d: path, deco: deco };
  }
  let drawn = {};  // vines already worked out, by size and seed: a page draws again every few seconds, the same each time
  function cached(key, make) { if (!drawn[key]) { if (Object.keys(drawn).length > 400) drawn = {}; drawn[key] = make(); } return drawn[key]; }
  // No page's policy takes a data-style="…" from markup (F-2026-058), so a drawing writes its own styles as data-style="…", and
  // they're set here by script, which the policy allows: only in the drawing just made (el, decor's own element), never
  // on anything else in the page.
  function styled(el) {
    Array.prototype.forEach.call(el.querySelectorAll("[data-style]"), function (n) {
      const s = n.getAttribute("data-style");
      if (s) /** @type {HTMLElement} */ (n).style.cssText = s;
      n.removeAttribute("data-style");
    });
  }
  // (``before``: where among el's children it goes, when not at the end)
  function put(el, cls, html, before) {
    const old = el.querySelector(":scope > ." + cls);
    if (!html) { if (old) old.remove(); return; }
    if (old && old.getAttribute("data-k") === html.k) return;
    const t = document.createElement("div"); t.innerHTML = html.svg; styled(t);
    const svg = /** @type {Element} */ (t.firstChild); svg.setAttribute("data-k", html.k);
    if (old && !before) old.replaceWith(svg);
    else { if (old) { if (before === old) before = old.nextSibling; old.remove(); } el.insertBefore(svg, before || null); }
  }
  // (``only``, in cards(), meters() and boxes(): just these drawn again, the rest left as they are)
  const skip = function (only, el) { return only && only.indexOf(el) < 0; };
  // each card: its colour inside the vine, the vine on its border (its place among the page's cards picks its vine)
  function cards(root, only) {
    const list = Array.prototype.slice.call(root.querySelectorAll(".card"));
    const sizes = list.map(function (c) { return skip(only, c) ? null : [c.offsetWidth, c.offsetHeight, parseFloat(getComputedStyle(c).borderTopLeftRadius) || 12]; });
    const page = location.pathname;
    list.forEach(function (c, i) {
      if (!sizes[i]) return;
      const w = sizes[i][0], h = sizes[i][1], rad = sizes[i][2];
      if (!w || !h) return;
      const seed = hs(page + "#" + (c.id || i)), k = w + "x" + h + "x" + rad + "x" + seed;
      put(c, "vine", { k: k, svg: cached(k, function () {
        const g = edge(w, h, rad, seed, false);
        return '<svg class="vine" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + " " + h + '" aria-hidden="true" focusable="false"><path d="' + g.d +
          '" fill="var(--panel)" stroke="var(--vine)" stroke-width="1.2" stroke-linejoin="round"/>' + g.deco("var(--vine-leaf)") + "</svg>";
      }) });
    });
  }
  // ------------------------------------------------------------------ grapes: progress bars
  // every progress bar (app.js: class "meter", but a usage meter's, "usage") is the leafy, calmer vine of the Decorating
  // Dionysus marks: a gently curving vine with a grape every 24 px, some hanging just off it, loose leaves and curls. The
  // grapes ripen with the bar's per cent, the current one turning from green to wine; with no count ("busy") a ripening
  // wave runs along them. Thin bars are drawn smaller.
  function vineShape(w) {
    const R = rng(41), base = 20, amp = 2.6, n = Math.max(4, Math.floor((w - 36) / 24) + 1), X = function (i) { return 18 + i * 24; };
    const A1 = amp * (.7 + R() * .6), A2 = amp * .45 * (.5 + R()), f1 = .021 + R() * .012, f2 = .05 + R() * .02, p1 = R() * 6.3, p2 = R() * 6.3;
    const vy = function (x) { return base + A1 * Math.sin(x * f1 + p1) + A2 * Math.sin(x * f2 + p2); };
    let dPath = "M4 " + r1(vy(4)), x, i;
    for (x = 7; x < w - 4; x += 3) dPath += "L" + x + " " + r1(vy(x));
    dPath += "L" + (w - 4) + " " + r1(vy(w - 4));
    const xs = [], ys = []; let o = "";
    for (i = 0; i < n; i++) {
      const gx = X(i) + (R() - .5) * 3, v = vy(gx), k = R(), side = k < .2 ? -1 : k < .4 ? 1 : 0, gy = v + side * 9.1;
      xs.push(r1(gx)); ys.push(r1(gy));
      if (side) o += '<path d="M' + r1(gx - side * 1.5) + " " + r1(v) + "Q" + r1(gx) + " " + r1(v + side * 1.5) + " " + r1(gx) + " " + r1(gy - side * 6.5) + '" stroke="var(--leaf)" stroke-width="1.3" fill="none"/>';
    }
    for (i = 0; i < n - 1; i++) {
      if (R() > .7) continue;
      const mx = (xs[i] + xs[i + 1]) / 2, mv = vy(mx), nbr = ys[i] < vy(xs[i]) - 1 || ys[i + 1] < vy(xs[i + 1]) - 1 ? 1 : -1, sd = R() < .5 ? nbr : -nbr;
      o += R() < .85 ? leafAt(mx - 4, mv + sd * 7.5, sd < 0 ? -25 - R() * 30 : 20 + R() * 30, 9, "var(--leaf)") : curlAt(mx - 2, mv, sd, "var(--leaf)");
    }
    return { vine: '<path d="' + dPath + '" stroke="var(--leaf)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>' + o, xs: xs, ys: ys };
  }
  function vineBar(w, pct, busy) {
    const sh = cached("vshape" + w, function () { return vineShape(w); }), n = sh.xs.length, done = pct / 100 * n; let g = "";
    sh.xs.forEach(function (x, i) {
      const y = sh.ys[i];
      if (busy) { g += '<circle cx="' + x + '" cy="' + y + '" r="6.5" fill="var(--unripe)"/><circle class="ripen" cx="' + x + '" cy="' + y + '" r="6.5" data-style="animation-delay:' + r1((Math.max(0, x - 10) / 200) % 1.6 - 1.6) + 's"/>'; return; }
      const f = Math.max(0, Math.min(1, done - i));
      g += '<circle cx="' + x + '" cy="' + y + '" r="6.5" fill="var(--unripe)"/>' + (f > 0 ? '<circle cx="' + x + '" cy="' + y + '" r="6.5" fill="var(--wine)"' + (f < 1 ? ' opacity="' + r1(f) + '"' : "") + "/>" : "");
    });
    return sh.vine + g;
  }
  function meters(root, only) {
    const list = Array.prototype.slice.call(root.querySelectorAll(".meter:not(.usage)")).filter(function (m) { return !skip(only, m); });
    const widths = list.map(function (m) { return m.clientWidth; });
    list.forEach(function (m, i) {
      const s = m.classList.contains("thin") ? .7 : 1, w = Math.round(widths[i] / s), busy = m.classList.contains("busy");
      if (!w) return;
      const bar = m.querySelector(":scope > div"); let pct = parseFloat(m.getAttribute("aria-valuenow"));
      if (isNaN(pct)) pct = bar ? parseFloat(bar.style.width) || 0 : 0;
      pct = Math.round(Math.max(0, Math.min(100, pct)) * 2) / 2;
      const k = w + "x" + s + (busy ? "b" : "p" + pct);
      put(m, "mvine", { k: k, svg: '<svg class="mvine" width="' + widths[i] + '" height="' + r1(38 * s) + '" viewBox="0 0 ' + w + ' 38" aria-hidden="true" focusable="false">' + vineBar(w, pct, busy) + "</svg>" });
    });
  }

  // the Kept and Lost boxes of a side-by-side card (app.js: cmp card): the cards' own vine line on their border, without
  // leaves, in the box's colour, the box filled out to it (their titles keep their plain line)
  function boxes(root, only) {
    const list = Array.prototype.slice.call(root.querySelectorAll(".mgkeep,.mgdrop")).filter(function (b) { return !skip(only, b); });
    const sizes = list.map(function (b) { const cs = getComputedStyle(b); return [b.offsetWidth, b.offsetHeight, parseFloat(cs.borderTopLeftRadius) || 10, parseFloat(cs.borderTopWidth) || 0]; });
    list.forEach(function (b, i) {
      const w = sizes[i][0], h = sizes[i][1], rad = sizes[i][2], bw = sizes[i][3];
      if (!w || !h) return;
      const k = "b" + w + "x" + h + "x" + rad + "x" + bw;
      put(b, "edgevine", { k: k, svg: cached(k, function () {
        const g = edge(w, h, rad, 3, true);
        return '<svg class="edgevine" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + " " + h + '" data-style="left:' + (-bw) + "px;top:" + (-bw) + 'px" aria-hidden="true" focusable="false"><path d="' + g.d +
          '" fill="var(--panel)" stroke="var(--edge)" stroke-width="1.6" stroke-linejoin="round"/></svg>';
      }) });
    });
  }
  // the app goes to the window's edges (as Classic): its vine is the one down the seam between the menu and the page
  function frame() {
    const shell = document.querySelector(".shell");
    if (!shell) return;
    if (!on("vines")) { put(shell, "seamvine", null); return; }
    seam(shell);
  }
  // down the seam between the menu and the page, held beside the menu so it stays as the page scrolls (along the menu's
  // foot when the menu sits across the top, on a phone)
  function seam(shell) {
    const side = shell.querySelector(".side");
    if (!side) return;
    const across = side.offsetTop + side.offsetHeight <= (shell.querySelector(".main") || side).offsetTop + 1;
    const L = across ? side.offsetWidth : side.offsetHeight, X = across ? side.offsetHeight : 6, k = (across ? "a" : "d") + L + "x" + X;
    put(shell, "seamvine", { k: k, svg: cached("seam" + k, function () {
      const R = rng(19), n1 = Math.max(1, Math.round(L / (80 + R() * 30))), n2 = Math.max(3, Math.round(L / (30 + R() * 8))), p1 = R() * 6.3, p2 = R() * 6.3;
      const off = function (t) { return 1.1 * Math.sin(t / L * n1 * 2 * Math.PI + p1) + .45 * Math.sin(t / L * n2 * 2 * Math.PI + p2); };
      const along = function (t) { return across ? [t, X + off(t)] : [X + off(t), t]; }; let path = "";
      for (let t = 0; t <= L; t += L > 6000 ? 4 : 2.5) { const p = along(t); path += (t ? "L" : "M") + r1(p[0]) + " " + r1(p[1]); }
      // the menu's colour up to the vine and the page's from it, over the straight edge between them, so the vine divides them
      const W = 4, end = across ? "L" + L + " " + (X - W) + "L0 " + (X - W) + "z" : "L" + (X - W) + " " + L + "L" + (X - W) + " 0z";
      let o = (across ? '<rect x="0" y="' + (X - W) + '" width="' + L + '" height="' + 2 * W + '" fill="var(--bg)"/>'
        : '<rect x="' + (X - W) + '" y="0" width="' + 2 * W + '" height="' + L + '" fill="var(--bg)"/>') +
        '<path d="' + path + end + '" fill="var(--side)"/>' +
        '<path d="' + path + '" fill="none" stroke="var(--menu-vine)" stroke-width="1.3" stroke-linecap="round"/>';
      for (let t = 40 + R() * 200; t < L - 24; t += 178 + R() * 378) {
        const q = along(t), sd = R() < .5 ? -1 : 1, base = across ? 0 : 90;
        o += R() < .7 ? leafOn(q[0], q[1], base + sd * (40 + R() * 20) * (across ? -1 : 1), 7 + R() * 2, "var(--menu-vine)")
          : '<g transform="rotate(' + base + " " + r1(q[0]) + " " + r1(q[1]) + ')">' + curlAt(q[0], q[1], sd, "var(--menu-vine)") + "</g>";
      }
      // across: over the top of the page; down: an item of the app's row just after the menu, held as the menu is, 12 wide
      // (a browser doesn't draw an svg of no size) and taking no room: the seam 6 in
      return '<svg class="seamvine ' + (across ? "across" : "down") + '" width="' + (across ? L : 2 * X) + '" height="' + (across ? X + 12 : L) + '" aria-hidden="true" focusable="false">' + o + "</svg>";
    }) }, across ? null : side.nextSibling);
  }

  // ------------------------------------------------------------------ grapes: checkboxes
  // every checkbox is a grape squared off into a soft box, on its stem with a leaf: unripe when off, ripe with a tick
  // when on (a bar when partly on). Each state is a picture in the theme's colours, which app.css shows by the box's state.
  function rgb(c) {
    let m = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(c);
    if (m) c = "#" + m[1] + m[1] + m[2] + m[2] + m[3] + m[3];
    m = /^#([0-9a-f]{6})$/i.exec(c);
    if (m) return [0, 2, 4].map(function (i) { return parseInt(m[1].substr(i, 2), 16); });
    const n = c.match(/[\d.]+/g);
    return n && n.length >= 3 ? n.slice(0, 3).map(Number) : [128, 128, 128];
  }
  // a mixed into b, f of it a (0 to 1)
  function mix(a, b, f) { const x = rgb(a), y = rgb(b); return "rgb(" + x.map(function (v, i) { return Math.round(v * f + y[i] * (1 - f)); }).join(",") + ")"; }
  function grapeBox(fill, line, stem, mark, ink, shine) {
    let o = '<path d="M12 5.2C12 3.8 12.8 2.6 14.3 2.2" stroke="' + stem + '" stroke-width="1.4" fill="none" stroke-linecap="round"/>' + leafAt(14, 2.4, -14, 5.8, stem) +
      '<rect x="3.4" y="5" width="17.2" height="17.2" rx="6.6" fill="' + fill + '" stroke="' + line + '" stroke-width="1.6"/>';
    if (shine) o += '<ellipse cx="8" cy="9.4" rx="1.9" ry="1.2" transform="rotate(-35 8 9.4)" fill="#fff" fill-opacity=".35"/>';
    if (mark === "tick") o += '<path d="M7.9 13.7l2.8 2.8 5.4-5.8" fill="none" stroke="' + ink + '" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"/>';
    if (mark === "bar") o += '<path d="M8 13.6H16" stroke="' + ink + '" stroke-width="2.3" stroke-linecap="round"/>';
    return enc('<svg xmlns="http://www.w3.org/2000/svg" viewBox="1.8 0.8 22.2 22.2">' + o + "</svg>");
  }
  const CHECKS = ["off", "hover", "on", "mixed", "dis", "dis-on", "dis-mixed"];
  function checks() {
    if (!on("grapes")) { d.removeAttribute("data-checks"); CHECKS.forEach(function (k) { d.style.removeProperty("--check-" + k); }); return; }
    const wine = css("--wine"), unripe = css("--unripe"), leaf = css("--leaf"), ink = css("--accent-ink"), muted = css("--muted"), panel = css("--panel");
    const dl = mix(muted, panel, .45), df = mix(muted, panel, .1);
    const pics = {
      "off": grapeBox(unripe, leaf, leaf, "", ink, false),
      "hover": grapeBox(mix(wine, unripe, .28), wine, leaf, "", ink, false),
      "on": grapeBox(wine, wine, leaf, "tick", ink, true),
      "mixed": grapeBox(wine, wine, leaf, "bar", ink, true),
      "dis": grapeBox(df, dl, dl, "", ink, false),
      "dis-on": grapeBox(dl, dl, dl, "tick", ink, false),
      "dis-mixed": grapeBox(dl, dl, dl, "bar", ink, false),
    };
    CHECKS.forEach(function (k) { d.style.setProperty("--check-" + k, pics[k]); });
    d.setAttribute("data-checks", "on");
  }

  // ------------------------------------------------------------------ film
  const GLYPH = [-64, -32, 0, 32, 64].map(function (a) { return '<ellipse cx="0" cy="-5" rx="2.3" ry="' + (a === 0 ? 6.2 : 5.4) + '" transform="rotate(' + a + ' 0 6)" fill="var(--film-mark)"/>'; }).join("") +
    '<path d="M-7 9Q0 3 7 9" fill="none" stroke="var(--film-mark)" stroke-width="2" stroke-linecap="round"/>';
  // the same symbol as outlines in its own units (x along the film, y across it), to draw it bent with the film round a fold
  const GLYPH_OUT = (function () {
    const out = [];
    [-64, -32, 0, 32, 64].forEach(function (a) {
      const ry = a === 0 ? 6.2 : 5.4, c = Math.cos(a * Math.PI / 180), s = Math.sin(a * Math.PI / 180), ring = [];
      for (let i = 0; i < 16; i++) { const t = i / 16 * Math.PI * 2, x = 2.3 * Math.cos(t), y = -5 + ry * Math.sin(t) - 6; ring.push([x * c - y * s, x * s + y * c + 6]); }
      out.push(ring);
    });
    const arc = function (t) { return [-7 + 14 * t, 9 - 12 * t * (1 - t)]; }, top = [], bot = [];
    for (let i = 0; i <= 8; i++) { const t = i / 8, p = arc(t), dv = [14, -12 + 24 * t], l = Math.hypot(dv[0], dv[1]); top.push([p[0] - dv[1] / l, p[1] + dv[0] / l]); bot.unshift([p[0] + dv[1] / l, p[1] - dv[0] / l]); }
    out.push(top.concat(bot));
    return out;
  })();
  // the film's other symbol, the reel's Ionic column: outlines in its own units (x along the film, y across it)
  const PILLAR_OUT = (function () {
    const out = [], rect = function (x0, y0, x1, y1) { out.push([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]); };
    rect(-9, -8, 9, -6); rect(-5, -5, -3, 6); rect(-1, -5, 1, 6); rect(3, -5, 5, 6); rect(-7, 7.5, 7, 9.5);
    [-8, 8].forEach(function (cx) { const ring = []; for (let i = 0; i < 14; i++) { const t = i / 14 * Math.PI * 2; ring.push([cx + 3 * Math.cos(t), -4 + 3 * Math.sin(t)]); } out.push(ring); const hole = []; for (let i = 13; i >= 0; i--) { const t = i / 14 * Math.PI * 2; hole.push([cx + 1.4 * Math.cos(t), -4 + 1.4 * Math.sin(t)]); } out.push(hole); });
    return out;
  })();
  const PILLAR = '<path fill-rule="evenodd" fill="var(--film-mark)" d="' + PILLAR_OUT.map(function (r) { return "M" + r.map(function (g) { return r1(g[0]) + " " + r1(g[1]); }).join("L") + "z"; }).join("") + '"/>';
  // the symbols a film can carry: stamped on (or, bent with the film, its outlines: whole, one shape with holes in it)
  const FLOWER = { stamp: GLYPH, out: GLYPH_OUT }, COLUMN = { stamp: PILLAR, out: PILLAR_OUT, whole: true };
  const IONIC = '<path d="M-9-7H9M-4-5V6M0-5V6M4-5V6M-7 8.5H7" fill="none" stroke="var(--hole)" stroke-width="2" stroke-linecap="round"/><circle cx="-8" cy="-4" r="2.2" fill="none" stroke="var(--hole)" stroke-width="1.6"/><circle cx="8" cy="-4" r="2.2" fill="none" stroke="var(--hole)" stroke-width="1.6"/>';
  // the waves a film can take: [height, waves across 600px, how far it turns edge on, how often (times a wave; 2), and for
  // one that ends folded back over itself: that end's length (px), its loop's height (px), how loop-like (0: a flat fold)]
  const WAVES = [[.5, .75, .2], [.36, 2, .25], [.22, 3, .15], [.4, 1, .3, 3], [.33, 2.5, .3, 1], [.55, 1, .25, 4], [.28, 1.25, .25], [.36, 2, .35, 2, 70, 7, 0], [.36, 2, .35, 2, 45, 5, .25], [.5, 1.25, .25, 2, 60, 6.5, 0], [.3, 1.5, .25, 2, 40, 5, .25], [.22, 3, .15, 2, 55, 6, 0], [.28, 1.25, .25, 2, 50, 6, .25], [.5, .75, .2, 2, 55, 6, 0], [.4, 1, .3, 3, 60, 6.5, .25]];
  // a band of film seen in 3D: centreline c(t), width w, tilt k(t) (0: edge on); its sprocket holes and symbols foreshortened.
  // c(t) may also give the width's direction and how foreshortened its length is there ([x, y, nx, ny, f]: where the film
  // bends away into depth). With cut, the film from t = cut on lies over the rest: drawn after it (its holes and symbols too),
  // with a see-through hairline along its edges once clear of the bend, its symbols drawn bent with it, and spaced to end just short of its free end.
  function ribbon(c, w, tw, n, pitch, cut, mark) {
    mark = mark || FLOWER;
    const P = [], TH = 1.4; let i, o = "";
    for (i = 0; i <= n; i++) {
      const t = i / n, a = c(Math.max(0, t - .002)), b = c(Math.min(1, t + .002)), p = c(t); let tx = b[0] - a[0], ty = b[1] - a[1]; const L = Math.hypot(tx, ty) || 1;
      tx /= L; ty /= L;
      const q = { p: p, T: [tx, ty], N: p.length > 2 ? [p[2], p[3]] : [-ty, tx], w: w, k: tw(t), f: 1 };
      if (p.length > 2) { q.f = p[4]; q.T = [p[3] * p[4], -p[2] * p[4]]; }
      // round a fold the width's direction is given, not the bend's: kept pointing the same way as its neighbour's (its
      // tilt turned with it), or the strip there would cross over itself
      const pr = P[P.length - 1];
      if (cut && pr && q.N[0] * pr.N[0] + q.N[1] * pr.N[1] < 0) { q.N = [-q.N[0], -q.N[1]]; q.k = -q.k; }
      P.push(q);
    }
    const pt = function (q, u, v) { return [q.p[0] + q.N[0] * u + q.T[0] * v, q.p[1] + q.N[1] * u + q.T[1] * v]; };
    const poly = function (arr, fill, extra) { return '<path d="M' + arr.map(function (a) { return r1(a[0]) + " " + r1(a[1]); }).join("L") + 'z" fill="' + fill + '" ' + (extra || "") + "/>"; };
    const hh = function (q) { return Math.max(q.w / 2 * Math.abs(q.k), TH); };
    let len = 0; const marks = [0];
    for (i = 1; i <= n; i++) { len += Math.hypot(P[i].p[0] - P[i - 1].p[0], P[i].p[1] - P[i - 1].p[1]); marks.push(len); }
    const atD = function (dd) { let j = 1; while (j < marks.length && marks[j] < dd) j++; return j < marks.length ? P[j] : null; };
    // the film's frame at a distance along it, between its points (for a symbol bent with it)
    const at = function (dd) {
      let j = 1; while (j < n && marks[j] < dd) j++;
      const A = P[j - 1], B = P[j], f = Math.max(0, Math.min(1, (dd - marks[j - 1]) / ((marks[j] - marks[j - 1]) || 1))), m = function (x, y) { return x + (y - x) * f; };
      const nx = m(A.N[0], B.N[0]), ny = m(A.N[1], B.N[1]); const nl = Math.hypot(nx, ny) || 1;
      return { p: [m(A.p[0], B.p[0]), m(A.p[1], B.p[1])], N: [nx / nl, ny / nl], k: m(A.k, B.k) };
    };
    const bent = function (dd, sc) {
      const one = function (ring) { return "M" + ring.map(function (g) { const F = at(dd + g[0] * sc), u = g[1] * sc * F.k; return r1(F.p[0] + F.N[0] * u) + " " + r1(F.p[1] + F.N[1] * u); }).join("L") + "z"; };
      if (mark.whole) return '<path fill-rule="evenodd" d="' + mark.out.map(one).join("") + '" fill="var(--film-mark)"/>';
      return mark.out.map(function (ring) { return '<path d="' + one(ring) + '" fill="var(--film-mark)"/>'; }).join("");
    };
    const hstep = pitch / 4, g0 = cut ? (len - pitch * .5) % pitch : pitch * .5, gEnd = cut ? len : len - pitch * .3;
    // one stretch: [from, to] of P, its symbols on the one spacing the whole film keeps
    const piece = function (from, to, edged) {
      const d0 = marks[from], d1 = to === n ? len + 1 : marks[to]; let dd, Q;
      if (edged) [1, -1].forEach(function (sd) { o += '<path d="M' + P.slice(from, to + 1).filter(function (q, j, all) { return q.p.length < 3 && (j > 0 && all[j - 1].p.length < 3) && P[from].p[0] - q.p[0] > 5; }).map(function (q) { const e = pt(q, sd * (hh(q) + .25), 0); return r1(e[0]) + " " + r1(e[1]); }).join("L") + '" fill="none" stroke="var(--hole)" stroke-width=".9" stroke-linejoin="round"/>'; });
      for (i = from; i < to; i++) { const A = P[i], B = P[i + 1]; o += poly([pt(A, hh(A), 0), pt(B, hh(B), 0), pt(B, -hh(B), 0), pt(A, -hh(A), 0)], "var(--film)", 'stroke="var(--film)" stroke-width=".6" stroke-linejoin="round"'); }
      for (dd = hstep / 2 + Math.max(0, Math.ceil((d0 - hstep / 2) / hstep)) * hstep; dd < Math.min(d1, len); dd += hstep) {
        // every one along both edges, however the film turns: nearer edge on, closer together and smaller, inside the edge
        Q = atD(dd); if (!Q) break; const ak = Math.abs(Q.k), e = hh(Q);
        const hl = Q.w * .05 * Math.max(.3, Math.abs(Q.f)), hw = Math.max(.3, Math.min(Q.w * .045 * ak, e * .2));
        [1, -1].forEach(function (sd) { const u = sd * e * .78; o += poly([pt(Q, u - hw, -hl), pt(Q, u + hw, -hl), pt(Q, u + hw, hl), pt(Q, u - hw, hl)], "var(--hole)"); });
      }
      const reach = cut ? w / 26 * 9 : 0, e0 = from ? d0 - reach : d0, e1 = to === n ? d1 : d1 - reach;
      for (dd = g0 + Math.max(0, Math.ceil((e0 - g0) / pitch)) * pitch; dd < Math.min(e1, gEnd); dd += pitch) {
        Q = atD(dd); if (!Q) break; if (Math.abs(Q.k) < .06) continue; const sc = Q.w / 26;
        if (cut) { o += bent(dd, sc); continue; }
        o += '<g transform="matrix(' + r1(Q.T[0] * sc) + " " + r1(Q.T[1] * sc) + " " + r1(Q.N[0] * sc * Q.k) + " " + r1(Q.N[1] * sc * Q.k) + " " + r1(Q.p[0]) + " " + r1(Q.p[1]) + ')">' + mark.stamp + "</g>";
      }
    };
    if (cut) { const ic = Math.round(cut * n); piece(0, ic, false); piece(ic, n, true); } else piece(0, n, false);
    return o;
  }
  // the reel: its face in the film's colour, five column cutouts, a cut ring, the hub ring and the peg hole
  function reel(cx, cy, R) {
    const sc = R / 38; let o = '<circle cx="' + cx + '" cy="' + cy + '" r="' + R + '" fill="var(--film)"/><g transform="translate(' + r1(cx - 39 * sc) + " " + r1(cy - 39 * sc) + ") scale(" + r1(sc) + ')">';
    for (let i = 0; i < 5; i++) { const ang = -90 + i * 72, x = r1(39 + 23 * Math.cos(ang * Math.PI / 180)), y = r1(39 + 23 * Math.sin(ang * Math.PI / 180)); o += '<g transform="translate(' + x + " " + y + ") rotate(" + (ang + 90) + ') scale(.8)">' + IONIC + "</g>"; }
    const ring = function (r) { return '<circle cx="39" cy="39" r="' + r + '" fill="none" stroke="var(--hole)" stroke-width="1.6"/>'; };
    return o + ring(33.5) + '<circle cx="39" cy="39" r="4.5" fill="var(--hole)"/>' + ring(8) + "</g>";
  }
  // the reel as a browser tab's icon (the dev tools' pages: dev/icons.js draws them), simpler the smaller it's shown, as an
  // <svg> its colours are set on (--film, --hole): at 16px five round holes and the peg hole; at 32px plain columns (no
  // scrolls), the peg hole and the cut ring; from 64px the reel itself
  function tab(px) {
    const round = function (c, r, f) { let o = ""; for (let i = 0; i < 5; i++) { const ang = -90 + i * 72; o += f(r1(c + r * Math.cos(ang * Math.PI / 180)), r1(c + r * Math.sin(ang * Math.PI / 180)), ang + 90); } return o; };
    const svg = function (n, inner) { return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + n + " " + n + '" width="' + px + '" height="' + px + '">' + inner + "</svg>"; };
    if (px >= 64) return svg(80, reel(40, 40, 38));
    if (px <= 16) {
      return svg(16, '<circle cx="8" cy="8" r="7.75" fill="var(--film)"/>' + round(8, 4.6, function (x, y) { return '<circle cx="' + x + '" cy="' + y + '" r="1.55" fill="var(--hole)"/>'; }) +
        '<circle cx="8" cy="8" r="1.1" fill="var(--hole)"/>');
    }
    return svg(32, '<circle cx="16" cy="16" r="15.5" fill="var(--film)"/>' + round(16, 9.4, function (x, y, a) {
      return '<g transform="translate(' + x + " " + y + ") rotate(" + a + ') scale(.82)"><path d="M-3.6-3.2H3.6M-1.7-1.6V2.2M1.7-1.6V2.2M-3 3.6H3" fill="none" stroke="var(--hole)" stroke-width="1.8" stroke-linecap="round"/></g>';
    }) + '<circle cx="16" cy="16" r="2.4" fill="var(--hole)"/><circle cx="16" cy="16" r="14.1" fill="none" stroke="var(--hole)" stroke-width=".9"/>');
  }
  let maskN = 0;
  function band(name, W) {
    const r = 20, cy = r + 4, x0 = r + 4, L = W - x0 - 2, q = (600 - x0 - 2) / L, D = 2 * r, tr = r / L, y0 = cy - r + .8 * D, bw = .2 * D, pitch = 22 * r / 34;
    const R = rng(hs(name)), wv = WAVES[Math.floor(R() * WAVES.length)], j = function () { return .9 + R() * .2; };
    const amp = Math.min(.55, wv[0] * j()), cyc0 = wv[1] * j() / q, kd = wv[2] * j();
    // a film that loops back at its end comes up out of a trough into the loop (its wave timed to be just past one there), so
    // the loop has the room above it and its tail comes over and down, as if presented
    let cyc = cyc0;
    if (wv[4]) { const span = 1 - 10 / L - tr, ph = span * cyc0; cyc = (Math.max(1, Math.round(ph - .1)) + .1) / span; }
    const X = function (t) { return x0 + t * L; }, open = function (t) { return ease(tr * .5, tr + .1 * q, t); };
    let c = function (t) { return [X(t), y0 - (D * amp) * (.5 - .5 * Math.cos((t - tr) * Math.PI * 2 * cyc)) * ease(tr, tr + .15 * q, t)]; };
    const k = function (t) { return open(t) * (1 - kd + kd * Math.cos((t - tr) * Math.PI * 2 * (wv[3] || 2) * cyc)); };
    let kk = k, fold = 0;
    if (wv[4]) {
      // the end turns back over itself: past a dip the film runs on into a loop and comes back down across itself, its back
      // showing (its symbols the right way up), for wv[4]px in all. How loop-like (wv[6], 0 to 1): at 0 a flat fold, the film
      // bent over at full width into a loop no taller than the film is wide, coming back nearly along itself; at 1 a long
      // narrow teardrop wv[5]px across, the film twisting edge on round its end, its tail crossing back steeply. Squeezed to
      // fit the reel's height
      const base = c, be = wv[6] || 0, v0 = Math.max(.35, Math.abs(k(1 - 10 / L)));
      const rad = (bw * v0 * .55) + (wv[5] - bw * v0 * .55) * be, te = 1 - (rad + 4) / L, p1 = base(te), pa = base(te - .002), th0 = Math.atan2(p1[1] - pa[1], p1[0] - pa[0]);
      const tot = Math.max(wv[4], rad * (Math.PI + .3) + 10), bl = te * L, top = cy - r + bw / 2 + 1, bot = cy + r - bw / 2 - 1, cross = .85 + (.5 - .85) * be;
      let a = .35, Lr = rad * (Math.PI + a);
      for (let it = 0; it < 4; it++) { a = Math.atan(2 * rad / Math.max(4, cross * (tot - Lr))); Lr = rad * (Math.PI + a); }
      const TAILN = 80, tail = [[p1[0], p1[1]]]; let hx = p1[0], hy = p1[1], hi = hy, lo = hy;
      for (let i = 1; i <= TAILN; i++) {
        const sm = (i - .5) / TAILN * tot, th = th0 - Math.min(sm, Lr) / rad;
        hx += Math.cos(th) * tot / TAILN; hy += Math.sin(th) * tot / TAILN; tail.push([hx, hy]); hi = Math.min(hi, hy); lo = Math.max(lo, hy);
      }
      const up = hi < top ? (p1[1] - top) / (p1[1] - hi) : 1, dn = lo > bot ? (bot - p1[1]) / (lo - p1[1]) : 1;
      tail.forEach(function (z) { z[1] = p1[1] + (z[1] - p1[1]) * (z[1] < p1[1] ? up : dn); });
      const t1 = bl / (bl + tot), tr2 = Lr / tot;
      c = function (t) {
        if (t <= t1) return base(t / t1 * te);
        const u = (t - t1) / (1 - t1), m = u * TAILN, i = Math.min(TAILN - 1, Math.floor(m)), f = m - i;
        return [tail[i][0] + (tail[i + 1][0] - tail[i][0]) * f, tail[i][1] + (tail[i + 1][1] - tail[i][1]) * f];
      };
      fold = t1 + (1 - t1) * tr2 * .5;
      // round the loop the tilt turns over: at once at full width (a fold), or through edge on (a twist)
      const v = Math.max(.35, Math.abs(k(te))) * (k(te) < 0 ? -1 : 1), gam = Math.max(.08, be);
      kk = function (t) {
        if (t <= t1) return k(t / t1 * te);
        const u = (t - t1) / (1 - t1); if (u >= tr2) return -v;
        const cc = Math.cos(Math.PI * u / tr2); return v * (cc < 0 ? -1 : 1) * Math.pow(Math.abs(cc), gam);
      };
    }
    // and the page's name picks the film's symbol and the symbols' colour (the theme's film-mark or film-mark-2)
    const mark = R() < .5 ? FLOWER : COLUMN, second = R() < .5;
    const strip = ribbon(c, bw, kk, Math.round(W / 2.3), pitch, fold, mark);
    const inner = strip + reel(x0, cy, r), H = 2 * r + 8, id = "pcfilm" + (++maskN);
    // the sprocket holes and the reel's cutouts are see-through: a mask with them black
    return '<svg class="filmband" viewBox="0 0 ' + W + " " + H + '" width="' + W + '" height="' + H + '" aria-hidden="true" focusable="false">' +
      '<mask id="' + id + '" maskUnits="userSpaceOnUse" x="-50" y="-50" width="' + (W + 100) + '" height="' + (H + 100) + '"><g data-style="--film:#fff;--film-mark:#fff;--hole:#000">' + inner + "</g></mask>" +
      '<g mask="url(#' + id + ')" data-style="--hole:var(--film)' + (second ? ";--film-mark:var(--film-mark-2)" : "") + '">' + inner + "</g></svg>";
  }
  function film(root) {
    const top = root.querySelector(":scope > .top"), old = root.querySelector(":scope > .filmline");
    if (!on("film") || !top) { if (old) old.remove(); return; }
    const W = Math.round(top.offsetWidth), name = location.pathname, k = W + name;
    if (W < 120) return;
    if (old && old.getAttribute("data-k") === k) return;
    const div = document.createElement("div");
    div.className = "filmline"; div.setAttribute("data-k", k);
    div.innerHTML = cached("film" + k, function () { return band(name, W); }); styled(div);
    if (old) old.replaceWith(div); else top.insertAdjacentElement("afterend", div);
  }

  // ------------------------------------------------------------------ when to draw
  let main = null, busy = false;
  function paint(root) {
    root = root || main || document.querySelector(".main") || document.body;
    if (!root) return;
    main = root;
    busy = true;
    try {
      film(root);
      // the bars first: a vine bar is taller than a plain one, so a card's size is known only after them
      if (on("grapes")) meters(root);
      if (on("vines")) { cards(root); boxes(root); }
      else root.querySelectorAll(".card > .vine").forEach(function (v) { v.remove(); });
      frame();
    } finally { busy = false; }
    watch(root);
  }
  let queued = false;
  function soon() { if (queued) return; queued = true; requestAnimationFrame(function () { queued = false; paint(); }); }
  const DRAWN = ".vine,.edgevine,.mvine,.seamvine,.filmline";
  const ours = function (n) { return n.nodeType === 1 && n.matches(DRAWN); };
  const allOurs = function (nodes) { for (let j = 0; j < nodes.length; j++) if (!ours(nodes[j])) return false; return true; };
  if (window.MutationObserver) {
    new MutationObserver(function (list) {
      if (busy) return;
      // only changes to a page's own content: not the vines and film this file adds (inside them, or put in or taken out)
      for (let i = 0; i < list.length; i++) {
        const r = list[i], t = /** @type {Element} */ (r.target);
        if (t.closest && t.closest(DRAWN)) continue;
        if ((r.addedNodes.length || r.removedNodes.length) && allOurs(r.addedNodes) && allOurs(r.removedNodes)) continue;
        soon(); return;
      }
    }).observe(document.documentElement, { childList: true, subtree: true });
  }
  // A size changed (the window, a card's content wrapping, the menu folded or opened): only what's that size now is
  // drawn again (ResizeObserver), not the whole page. While the menu's width eases, nothing: the end draws it all again.
  let easing = false; const sized = new WeakMap(), ro = window.ResizeObserver ? new ResizeObserver(resized) : null;
  const size = function (el) { return el.offsetWidth + "x" + el.offsetHeight; };
  function watch(root) {
    if (!ro) return;
    ro.disconnect();
    const els = Array.prototype.slice.call(root.querySelectorAll(".card,.mgkeep,.mgdrop,.meter:not(.usage),:scope > .top"));
    const side = document.querySelector(".shell > .side");
    if (side) els.push(side);
    // (its border box: a padding or border that changes moves the vine too; a browser without the choice watches the inside)
    els.forEach(function (el) { sized.set(el, size(el)); ro.observe(el, { box: "border-box" }); });
  }
  function resized(entries) {
    if (busy || easing || !main) return;
    const c = [], b = [], m = []; let rest = false;
    entries.forEach(function (e) {
      const el = e.target, s = size(el);
      if (sized.get(el) === s || !el.isConnected) return;
      sized.set(el, s);
      const cl = el.classList;
      if (cl.contains("card")) c.push(el); else if (cl.contains("meter")) m.push(el); else if (cl.contains("mgkeep") || cl.contains("mgdrop")) b.push(el); else rest = true;
    });
    if (!c.length && !b.length && !m.length && !rest) return;
    busy = true;
    try {
      if (on("vines")) { if (c.length) cards(main, c); if (b.length) boxes(main, b); }
      if (on("grapes") && m.length) meters(main, m);
      if (rest) { film(main); frame(); }
    } finally { busy = false; }
  }
  if (!ro) window.addEventListener("resize", soon);
  const isSide = function (e) { return e.target && e.target.classList && e.target.classList.contains("side"); };
  document.addEventListener("transitionrun", function (e) { if (isSide(e)) easing = true; });
  document.addEventListener("transitioncancel", function (e) { if (isSide(e)) { easing = false; soon(); } });
  // the menu folded or opened (its width eases): the page beside it is another width, its vines drawn again
  document.addEventListener("transitionend", function (e) { if (isSide(e)) { easing = false; soon(); } });
  // after the theme changes (theme.js): the spots' and checkboxes' colours, and everything drawn again
  function themed() { drawn = {}; spots(); checks(); soon(); }
  window.PCDECOR = { paint: paint, themed: themed, tab: tab };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", themed); else themed();
})();
