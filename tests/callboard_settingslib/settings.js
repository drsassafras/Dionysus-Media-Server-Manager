// The settings format's drawer (a shared library from MyStandards: settingslib; the format is MyStandards' SETTINGS.md).
// One script, the same copy in every project: given a setting's id (and its member, for one in a set) it makes the
// setting's row — its info button, its label and summary, the field its kind gets, each sub-setting's field, the unit,
// the revert button. No setting can be drawn without its info button: it's made here, with the row, from the info the
// format makes every setting have (a sub-setting's is said in its parent's bubble, under its own label). The structure
// is the same everywhere; each app's stylesheet says how it looks (opts.classes names the app's own classes for each
// part). A copy of the template, kept in step with it: never edit it here.
//
//   const S = SettingsLib.make({
//     catalog,                    // what the reader's page() gave: {groups, sets, settings, states}
//     change(id, member, value),  // a value changed (undefined: reverted to its default). The app saves it, or drafts it
//     value(id, member),          // optional: the value as the app holds it now (a draft); else the catalog's state
//     state(id, member),          // optional: {default, choices, shown, set} where the app knows better than the catalog
//     classes: {row: "row", …},   // optional: the app's own class for a part (CLASSES names them all)
//                                 // (field: may be (setting) => "its classes", for a field sized by its kind)
//     t(text),                    // optional: the app's translation of a word of the page
//     readOnly,                   // optional: true, or (id, member) => true: drawn, and can't be changed
//     tag(setting, member),       // optional: a tag of the app's (a pill), drawn between the description and the field.
//                                 // Give it the attribute data-set-opens and it opens the row's own bubble when pointed at or focused
//     extra(setting, member),     // optional: paragraphs of the app's in the bubble, before "Saved as": [[label, ...words]]
//     savedSays(setting, member), // optional: how "Saved as" is said (a node or text) where the app has its own way
//   });
//   S.draw("tour_host")           // the row, an element (null where the setting isn't shown now). Anywhere, any number
//   S.draw("server_url", "nas1")  // of times: every copy of one setting shows a change made in another at once
//   S.group("tour")               // each of a group's settings in the file's order: [rows]
//   S.refresh()                   // every row on the page brought up to what value() says now
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.SettingsLib = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const CLASSES = {
    row: "set-row", info: "set-info", text: "set-text", label: "set-label", summary: "set-summary", fields: "set-fields",
    field: "set-field", sub: "set-sub", sublabel: "set-sub-label", unit: "set-unit", revert: "set-revert", error: "set-error",
    onoff: "set-onoff", tag: "set-tag", pop: "set-pop", para: "set-para", paralabel: "set-para-label", saved: "set-saved",
  };
  const SHARED = "*";
  const el = (tag, attrs, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? "" : String(v));
    }
    for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false && k !== "") e.append(k.nodeType ? k : document.createTextNode(String(k)));
    return e;
  };
  const isPairs = (v) => !!v && typeof v === "object" && !Array.isArray(v) && !v.nodeType;   // (a pairs setting's value: {name: text})
  const pairsSaid = (v, sep) => Object.entries(v).map(([k, x]) => k + "=" + x).join(sep);
  const PAIR_NAME = /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/;
  const members = (m) => m === undefined || m === null || m === "" ? [] : Array.isArray(m) ? m.map(String) : [String(m)];
  const sentence = (t) => { const s = String(t || "").trim(); return s ? s[0].toUpperCase() + s.slice(1) + (/[.!?)]$/.test(s) ? "" : ".") : s; };

  // (the one bubble, and which button has it open: the page's, whichever drawer made the button)
  let pop = null, popFor = "", popPinned = false;

  function make(opts) {
    opts = opts || {};
    const C = Object.assign({}, CLASSES, opts.classes || {});
    const t = opts.t || ((s) => s);
    let catalog = opts.catalog || {groups: {}, sets: {}, settings: {}, states: {}};
    const def = (id) => { const s = catalog.settings[id]; if (!s) throw new Error("no setting \"" + id + "\" in the settings file"); return s; };
    const keyOf = (id, m) => [id, ...members(m)].join("|");
    const stateOf = (id, m) => Object.assign({}, (catalog.states || {})[keyOf(id, m)] || {}, opts.state ? opts.state(id, m) || {} : {});
    const valueOf = (id, m) => { const v = opts.value ? opts.value(id, m) : undefined; return v !== undefined ? v : stateOf(id, m).value; };
    const has = (v) => !(v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length) || (isPairs(v) && !Object.keys(v).length));
    const ro = (id, m) => typeof opts.readOnly === "function" ? !!opts.readOnly(id, m) : !!opts.readOnly;
    const choicesOf = (s, m) => stateOf(s.id, m).choices || s.choices || [];
    const shownNow = (s, m) => {
      const when = s.shown_when;
      if (!when || when.feature) return stateOf(s.id, m).shown !== false;
      const other = def(when.setting), v = valueOf(when.setting, other.each.length ? m : null);
      return (has(v) ? v : (stateOf(when.setting, other.each.length ? m : null).default || {}).value) === when.is;
    };
    // what a value is called on the page (a choice's label, on and off, a list with commas)
    const said = (s, v, m) => {
      if (!has(v) && v !== false) return "";
      if (s.kind === "switch") return v ? t("on") : t("off");
      if (s.kind === "choice") { const c = choicesOf(s, m).find(c => c[0] === v); return c ? t(c[1]) : String(v); }
      return (Array.isArray(v) ? v.join(", ") : isPairs(v) ? pairsSaid(v, ", ") : String(v)) + (s.unit ? " " + s.unit : "");
    };
    const backTo = (s, m) => {
      const d = stateOf(s.id, m).default || {};
      if (d.how === "required") return t("nothing: it must be given");
      const now = said(s, d.value, m);
      return d.says ? t(d.says) + (now ? " (" + t("now") + " " + now + ")" : "") : now || t("blank");
    };

    // ---- the one bubble every info button opens: a popover of the browser's where it has them
    const bubble = () => {
      if (pop && pop.isConnected) return pop;
      pop = document.getElementById("set-pop") || el("div", {class: C.pop, id: "set-pop", role: "tooltip"});
      if (pop.isConnected) return pop;
      if (typeof pop.showPopover === "function") pop.setAttribute("popover", "manual"); else pop.hidden = true;
      document.body.append(pop);
      return pop;
    };
    const para = (label, ...words) => el("p", {class: C.para}, el("b", {class: C.paralabel}, label + ":"), " ", ...words);
    const paras = (s, m) => {
      // (its info's three parts, each a paragraph under its own label; a sub-setting's, in one under its name)
      const part = (x, k) => (x.info || {})[k] ? sentence(t(x.info[k])) : "";
      const out = [["does", "What it does"], ["when", "When to use it"], ["config", "Configuration"]].filter(([k]) => part(s, k)).map(([k, label]) => para(t(label), part(s, k)));
      for (const id of s.subs || []) { const sub = def(id); out.push(para(sentence(t(sub.label)).replace(/\.$/, ""), ["does", "when", "config"].map(k => part(sub, k)).filter(Boolean).join(" "))); }
      // (what it goes back to: its default as it stands, a sub-setting's after it — what the revert button does, said here too)
      out.push(para(t("Default"), sentence([s, ...(s.subs || []).map(def)].map((x, i) => (i ? t(x.label) + " " : "") + backTo(x, m)).join("; "))));
      for (const p of (opts.extra ? opts.extra(s, m) || [] : [])) if (Array.isArray(p) && p.length > 1) out.push(para(p[0], ...p.slice(1)));
      const where = opts.savedSays ? opts.savedSays(s, m) : null, ss = (s.saved_says_shared && (m || []).every(x => x === SHARED) ? s.saved_says_shared : s.saved_says) || {};
      out.push(para(t("Saved as"), ...(where ? [where] : [el("code", {class: C.saved}, ss.key || ""), ss.file ? " " + t("in") + " " + ss.file : ""])));
      return out;
    };
    const place = (b) => {
      const p = bubble(), r = b.getBoundingClientRect();
      p.style.position = "fixed"; p.style.margin = "0"; p.style.inset = "auto";
      const w = p.offsetWidth, hgt = p.offsetHeight, vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
      p.style.left = Math.max(8, Math.min(r.left, vw - w - 8)) + "px";
      p.style.top = (r.bottom + 6 + hgt > vh && r.top - 6 - hgt > 0 ? r.top - 6 - hgt : r.bottom + 6) + "px";
    };
    const hide = () => {
      if (!popFor) return;
      for (const b of document.querySelectorAll("[data-set-info]")) b.setAttribute("aria-expanded", "false");
      const p = bubble();
      if (p.hasAttribute("popover")) { try { p.hidePopover(); } catch (e) { /* not open */ } } else p.hidden = true;
      popFor = ""; popPinned = false;
    };
    const show = (b, pinned) => {   // (b: the info button, or something of the row's that opens its bubble: the bubble goes by it)
      const key = b.getAttribute("data-set-info") || b.getAttribute("data-set-opens"), [id, ...m] = key.split("|");
      if (popFor && popFor !== key) hide();
      const p = bubble();
      p.replaceChildren(...paras(def(id), m));
      if (p.hasAttribute("popover")) { try { if (!p.matches(":popover-open")) p.showPopover(); } catch (e) { /* shown already */ } } else p.hidden = false;
      popFor = key; popPinned = !!pinned;
      for (const x of document.querySelectorAll("[data-set-info]")) x.setAttribute("aria-expanded", String(x.getAttribute("data-set-info") === key));
      place(b);
    };
    const infoButton = (s, m) => el("button", {type: "button", class: C.info, "aria-label": t("About {what}").replace("{what}", t(s.label)), "aria-expanded": String(popFor === keyOf(s.id, m)),
      "aria-controls": "set-pop", "data-set-info": keyOf(s.id, m),
      onclick: (ev) => { ev.preventDefault(); ev.stopPropagation(); const b = ev.currentTarget; if (popFor === b.getAttribute("data-set-info") && popPinned) hide(); else show(b, true); }}, "i");
    if (typeof document !== "undefined" && !make.listening) {
      make.listening = true;
      const btn = (ev) => ev.target && ev.target.closest && ev.target.closest("[data-set-info], [data-set-opens]");
      const hovers = typeof matchMedia === "function" && matchMedia("(hover: hover)").matches;
      document.addEventListener("mouseover", (ev) => { const b = hovers && btn(ev); if (b && b.__setShow) b.__setShow(false); });
      document.addEventListener("mouseout", (ev) => { const b = hovers && btn(ev); if (b && b.__setHide && !(ev.relatedTarget && b.contains(ev.relatedTarget))) b.__setHide(false); });
      document.addEventListener("focusin", (ev) => { const b = btn(ev); if (b && b.__setShow) b.__setShow(false); });
      document.addEventListener("focusout", (ev) => { const b = btn(ev); if (b && b.__setHide) b.__setHide(false); });
      document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") for (const b of document.querySelectorAll("[data-set-info]")) if (b.__setHide) { b.__setHide(true); break; } });
      document.addEventListener("click", (ev) => { if (!(ev.target.closest && ev.target.closest("[data-set-info], #set-pop"))) { const b = document.querySelector("[data-set-info]"); if (b && b.__setHide) b.__setHide(true); } });
    }

    // ---- a field, by its setting's kind: its value on the page, and what's typed read back as the setting keeps it
    const field = (s, m, changed) => {
      const v = valueOf(s.id, m), off = ro(s.id, m) ? true : null, name = t(s.label);
      const dv = () => (stateOf(s.id, m).default || {}).value;   // (its default as it stands now: another setting's value may be what it goes back to)
      const common = {class: typeof C.field === "function" ? C.field(s) : C.field, "aria-label": name, disabled: off, "data-set-field": keyOf(s.id, m)};
      // (in a blank field: the default, what holds then — or how it's worked out, where that isn't known yet)
      const phNow = (lines) => { const dflt = stateOf(s.id, m).default || {}, d = dflt.value; return has(d) ? (Array.isArray(d) ? d.join(lines ? "\n" : ", ") : isPairs(d) ? pairsSaid(d, "\n") : String(d)) : dflt.how === "worked_out" ? t(dflt.says || "") : ""; };
      const ph = phNow(s.kind === "lines" || s.kind === "pairs");
      let f, read, write;
      if (s.kind === "switch") {
        const box = el("input", Object.assign({type: "checkbox"}, common)), word = el("span", {});
        f = el("label", {class: C.onoff}, box, word);
        write = (x) => { box.checked = has(x) || x === false ? !!x : !!dv(); word.textContent = box.checked ? t("on") : t("off"); };
        read = () => box.checked;
        box.addEventListener("change", () => { word.textContent = box.checked ? t("on") : t("off"); changed(read()); });
      } else if (s.kind === "choice") {
        const list = choicesOf(s, m);
        f = el("select", common, ...list.map((c, i) => el("option", {value: String(i)}, t(c[1]))));
        write = (x) => { const want = has(x) || x === false ? x : dv(), i = list.findIndex(c => c[0] === want); f.value = i < 0 ? "" : String(i); };
        read = () => { const c = list[Number(f.value)]; return c ? c[0] : undefined; };
        f.addEventListener("change", () => changed(read()));
      } else if (s.kind === "number" || s.kind === "slider") {
        f = el("input", Object.assign({type: s.kind === "slider" ? "range" : "number", min: s.min, max: s.max, step: s.step === undefined ? "any" : s.step, placeholder: ph}, common));
        write = (x) => { f.value = has(x) ? String(x) : s.kind === "slider" && has(dv()) ? String(dv()) : ""; };
        read = () => f.value === "" ? undefined : Number(f.value);
        f.addEventListener("input", () => changed(read()));
      } else if (s.kind === "lines") {
        f = el("textarea", Object.assign({rows: "4", placeholder: ph}, common));
        write = (x) => { f.value = Array.isArray(x) ? x.join("\n") : ""; };
        read = () => { const got = f.value.split("\n").map(x => x.trim()).filter(Boolean); return got.length ? got : undefined; };
        f.addEventListener("input", () => changed(read()));
      } else if (s.kind === "pairs") {   // (names, each with its text: a line each, NAME=text, as an env file says them)
        f = el("textarea", Object.assign({rows: "3", placeholder: ph || "NAME=value"}, common));
        write = (x) => { f.value = isPairs(x) ? pairsSaid(x, "\n") : ""; };
        read = () => {
          const got = {};
          for (const line of f.value.split("\n")) { const l = line.trim(), i = l.indexOf("="); if (l) got[(i < 0 ? l : l.slice(0, i)).trim()] = i < 0 ? "" : l.slice(i + 1).trim(); }
          return Object.keys(got).length ? got : undefined;
        };
        f.addEventListener("input", () => changed(read()));
      } else if (s.kind === "list") {
        f = el("input", Object.assign({type: "text", placeholder: ph}, common));
        write = (x) => { f.value = Array.isArray(x) ? x.join(", ") : has(x) ? String(x) : ""; };   // (one item said alone is shown as it is)
        read = () => { const got = f.value.split(",").map(x => x.trim()).filter(Boolean); return got.length ? got : undefined; };
        f.addEventListener("input", () => changed(read()));
      } else if (s.kind === "secret") {
        f = el("input", Object.assign({type: "password", autocomplete: "off", placeholder: stateOf(s.id, m).set ? t("set (type to replace it)") : t("not set")}, common));
        write = () => {};   // (a secret's value is never on the page)
        read = () => f.value === "" ? undefined : f.value;
        f.addEventListener("change", () => changed(read()));
      } else {
        const port = s.type === "port";
        f = el("input", Object.assign({type: "text", inputmode: port ? "numeric" : null, placeholder: ph, pattern: s.pattern || null}, common));
        write = (x) => { f.value = has(x) ? String(x) : ""; };
        read = () => { const x = f.value.trim(); return x === "" ? undefined : port && /^\d+$/.test(x) ? Number(x) : x; };
        f.addEventListener("input", () => changed(read()));
      }
      write(v);
      const input = f.matches("input, select, textarea") ? f : f.querySelector("input");
      input.__setWrite = write;
      // (what a blank field shows, brought up to its default now; a switch, a choice and a slider show the default itself)
      const place = () => { if (s.kind === "secret") return; if (input.hasAttribute("placeholder")) input.setAttribute("placeholder", phNow(s.kind === "lines" || s.kind === "pairs") || (s.kind === "pairs" ? "NAME=value" : "")); else if (!has(valueOf(s.id, m)) && valueOf(s.id, m) !== false && input !== document.activeElement) write(valueOf(s.id, m)); };
      return {node: f, input, read, place};
    };
    // what's wrong with a value as typed, "" for nothing: said beside the field, and the app is still told
    const wrong = (s, v) => {
      if (!has(v) && v !== false) return "";
      if ((s.kind === "number" || s.kind === "slider") && (Number.isNaN(v) || (s.min !== undefined && v < s.min) || (s.max !== undefined && v > s.max))) return t("From {min} to {max}").replace("{min}", s.min).replace("{max}", s.max);
      if (s.kind === "text" && s.pattern && !(new RegExp("^(?:" + s.pattern + ")$")).test(String(v))) return t(s.pattern_says || "");
      if (s.kind === "pairs" && isPairs(v) && Object.keys(v).some(k => !PAIR_NAME.test(k))) return t("Each line is a name, =, then its text: a name is letters, digits, _, - and .");
      if (s.type === "port" && !(Number(v) > 0 && Number(v) < 65536)) return t("A port: a number from 1 to 65535");
      return "";
    };

    function draw(id, member) {
      let s = def(id);
      if (s.under) s = def(s.under);   // (a sub-setting is never drawn alone: its parent's row)
      const m = members(member), key = keyOf(s.id, m);
      if (!shownNow(s, m)) return null;
      const all = [s, ...(s.subs || []).map(def)];
      const error = el("div", {class: C.error, role: "alert", hidden: true});
      const revert = s.revert === false ? null : el("button", {type: "button", class: C.revert, "aria-label": t("Revert {what}").replace("{what}", t(s.label)), "data-set-revert": key}, "↺");
      const tidy = () => {
        const set = all.some(x => { const v = valueOf(x.id, m); return has(v) || v === false || (x.kind === "secret" && stateOf(x.id, m).set); });
        const missing = all.find(x => (stateOf(x.id, m).default || {}).how === "required" && !has(valueOf(x.id, m)) && !(x.kind === "secret" && stateOf(x.id, m).set));
        const bad = all.map(x => wrong(x, valueOf(x.id, m))).find(Boolean) || (missing ? t("{what} must be given").replace("{what}", sentence(t(missing.label)).replace(/\.$/, "")) : "");
        error.textContent = bad; error.hidden = !bad;
        for (const f of made) f.place();
        if (revert) {
          revert.disabled = !set || ro(s.id, m);
          revert.title = t("Back to: {what}").replace("{what}", all.map((x, i) => (i ? t(x.label) + " " : "") + backTo(x, m)).join("; "));
        }
      };
      const made = all.map(x => field(x, m, (v) => {
        if (opts.change) opts.change(x.id, m.length > 1 ? m : m[0] === undefined ? null : m[0], v);
        sync(x.id, m, document.activeElement);
        for (const r of document.querySelectorAll("[data-setting]")) if (r.__setTidy) r.__setTidy();
      }));
      const fields = el("div", {class: C.fields}, made[0].node, s.unit && s.kind !== "switch" ? el("span", {class: C.unit}, s.unit) : null,
        ...made.slice(1).map((f, i) => el("label", {class: C.sub}, el("span", {class: C.sublabel}, t(all[i + 1].label)), f.node, all[i + 1].unit ? el("span", {class: C.unit}, all[i + 1].unit) : null)));
      if (revert) revert.addEventListener("click", () => {
        for (const x of all) if (opts.change) opts.change(x.id, m.length > 1 ? m : m[0] === undefined ? null : m[0], undefined);
        for (const x of all) sync(x.id, m, null);
        for (const r of document.querySelectorAll("[data-setting]")) if (r.__setTidy) r.__setTidy();
      });
      const info = infoButton(s, m);
      info.__setShow = (pinned) => { if (!popPinned || pinned) show(info, pinned); };
      info.__setHide = (pinned) => { if (pinned || !popPinned) hide(); };
      const tag = opts.tag ? opts.tag(s, m) : null;   // (the app's tag sits after the words, by the field it's about)
      const row = el("div", {class: C.row, "data-setting": s.id, "data-member": m.join("|") || null},
        info,
        el("div", {class: C.text}, el("div", {class: C.label}, t(s.label)), s.summary ? el("div", {class: C.summary}, t(s.summary)) : null),
        tag ? el("div", {class: C.tag}, tag) : null,
        fields, revert, error);
      row.__setTidy = tidy;
      // (a pill of the app's on the row opens the row's one bubble, as its info button does: by pointing or focus, never a tooltip of its own)
      for (const o of row.querySelectorAll("[data-set-opens]")) {
        o.setAttribute("data-set-opens", key); o.removeAttribute("title");
        if (!o.hasAttribute("tabindex")) o.setAttribute("tabindex", "0");
        o.setAttribute("aria-describedby", "set-pop");
        o.__setShow = (pinned) => { if (!popPinned || pinned) show(o, pinned); };
        o.__setHide = (pinned) => { if (pinned || !popPinned) hide(); };
      }
      tidy();
      if (popFor === key) Promise.resolve().then(() => { if (info.isConnected && popFor === key) show(info, popPinned); });   // (drawn again while its bubble is open: the bubble stays, on the new button)
      return row;
    }
    // every copy of one setting on the page, brought up to its value now (but the field being typed in)
    function sync(id, m, but) {
      const key = keyOf(id, m);
      for (const f of document.querySelectorAll("[data-set-field]")) if (f.getAttribute("data-set-field") === key && f !== but && f.__setWrite) f.__setWrite(valueOf(id, m));
    }
    function refresh(next) {
      if (next) catalog = next;
      for (const f of document.querySelectorAll("[data-set-field]")) if (f !== document.activeElement && f.__setWrite) { const [id, ...m] = f.getAttribute("data-set-field").split("|"); if (catalog.settings[id]) f.__setWrite(valueOf(id, m)); }
      for (const r of document.querySelectorAll("[data-setting]")) if (r.__setTidy) r.__setTidy();
    }
    function group(gid, member) {
      if (!catalog.groups[gid]) throw new Error("no group \"" + gid + "\" in the settings file");
      return Object.values(catalog.settings).filter(s => s.group === gid && !s.under).map(s => draw(s.id, member)).filter(Boolean);
    }
    return {draw, drawSetting: draw, group, drawGroup: group, refresh, hide, shared: SHARED, get catalog() { return catalog; }};
  }
  return {make, CLASSES, SHARED: "*"};
});
