#!/usr/bin/env node
// The web app's CSS, scripts and pages, built as browsers get them. The settings are dev/package.json's: "browserslist"
// (the browsers they're built for: one list, "last 5 years"; dev/future.py's run, PLEXCACHE_WEB_TARGETS=future, takes
// "futureBrowserslist" in its place: "last 4 years", the window a year on).
//
//     npm ci --prefix dev                                  # once, and again when dev/package-lock.json changes
//     node dev/web.js build <in> [<out>]                   # a .css, .js or .html built (to stdout, or out; out may be in)
//     node dev/web.js build --no-minify <in> [<out>]       # the CSS prefixed only, a script or page as it is: not minified
//
// The build: the CSS with the newer features the targets lack rewritten as CSS they read (postcss-preset-env: nesting,
// newer colour and media syntax and the like, the newer form kept beside it for the browsers that have it), the prefixes
// they still need added (Autoprefixer), then minified (cssnano); a script minified (Terser), a page
// (html-minifier-terser). The image build runs it on every stylesheet, script and page (the Dockerfile's web stage), so
// what ships is built.
//
// The checks of the same files (Stylelint, ESLint, TypeScript, html-validate, axe-core) are callboard's: its DHTML
// integration (callboard tool webcheck), which reads the same dev/package.json — "browserslist", "graceful" (what a
// target lacks but the pages can do without), "stylelint", "eslint" and "htmlvalidate" — and runs these packages.
//
// A warning from the build (a property Autoprefixer can't prefix as written, an old syntax) is an error: nothing is written,
// the warnings are listed and the exit code is 1. 2: the packages aren't installed.
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.dirname(__dirname);
const settings = require("./package.json");
// The targets: "browserslist", or "futureBrowserslist" for dev/future.py's run (the software it tries is about a year
// ahead of the image's, so the window is a year shorter: the browsers that will have dropped out by then don't count).
const TARGETS = process.env.PLEXCACHE_WEB_TARGETS === "future" ? settings.futureBrowserslist : settings.browserslist;
// The tools that read the targets themselves (cssnano: it won't merge rules into one a target would drop whole) read the
// same list, by the variable Browserslist looks at first.
process.env.BROWSERSLIST = TARGETS.join(", ");

function need(name) {
  try {
    return require(name);
  } catch {
    console.error(`${name} isn't installed: npm ci --prefix dev`);
    process.exit(2);
  }
}

function rel(p) {
  const r = path.relative(ROOT, p);
  return r && !r.startsWith("..") ? r : p;
}

const kb = (n) => `${(Buffer.byteLength(n) / 1024).toFixed(1)} KB`;

// What postcss-preset-env does for the targets: the newer CSS features they lack, rewritten as CSS they read (its stage 2
// features: the ones on their way to every browser; the prefixes are Autoprefixer's, below), the newer form kept beside it.
function presetEnv() {
  return need("postcss-preset-env")({ browsers: TARGETS, autoprefixer: false });
}

// The CSS: its newer features rewritten for the targets (postcss-preset-env), prefixed (Autoprefixer), then minified
// (cssnano). Warnings: the list of them, nothing built.
async function buildCss(src, text, minify) {
  const postcss = need("postcss");
  const plugins = [presetEnv(), need("autoprefixer")({ overrideBrowserslist: TARGETS })];
  if (minify) plugins.push(need("cssnano")({ preset: "default" }));
  const result = await postcss(plugins).process(text, { from: src, map: false });
  const warnings = result.warnings();
  if (warnings.length) return { warnings: warnings.map((w) => `${rel(src)}:${w.line || "?"}: ${w.text} (${w.plugin})`) };
  return { out: result.css };
}

// A script: minified (Terser), its names kept where other scripts see them (the page's scripts share window's).
async function buildJs(src, text, minify) {
  if (!minify) return { out: text };
  const result = await need("terser").minify(text, { compress: true, mangle: true, format: { comments: false } });
  return { out: result.code };
}

// A page: minified (html-minifier-terser): comments out, runs of white space down to one (never none: text and inline
// elements keep their gaps).
async function buildHtml(src, text, minify) {
  if (!minify) return { out: text };
  const { minify: html } = need("html-minifier-terser");
  return { out: await html(text, { collapseWhitespace: true, conservativeCollapse: true, removeComments: true }) };
}

const BUILDERS = { ".css": buildCss, ".js": buildJs, ".html": buildHtml };

async function build(args) {
  const minify = !args.includes("--no-minify");
  const [src, out] = args.filter((a) => a !== "--no-minify");
  const builder = src && BUILDERS[path.extname(src)];
  if (!builder) {
    console.error(`build: a .css, .js or .html file to build${src ? `, not ${src}` : ""}`);
    return 2;
  }
  const text = fs.readFileSync(src, "utf8");
  const result = await builder(src, text, minify);
  if (result.warnings) {
    for (const w of result.warnings) console.error(w);
    return 1;
  }
  if (out) fs.writeFileSync(out, result.out);
  else process.stdout.write(result.out);
  const targets = path.extname(src) === ".css" ? `built for ${TARGETS.join(", ")}` : "";  // (a script: as written, for them all)
  const size = minify ? `minified ${kb(text)} → ${kb(result.out)}` : "";
  console.error(`${rel(src)}: ${[targets, size].filter(Boolean).join(", ") || "unchanged"}`);
  return 0;
}

const COMMANDS = { build };
if (require.main === module) {
  const [cmd, ...args] = process.argv.slice(2);
  if (!COMMANDS[cmd]) {
    console.error("usage: node dev/web.js build [--no-minify] <in.css|.js|.html> [<out>]");
    process.exitCode = 2;
  } else {
    COMMANDS[cmd](args).then((code) => { process.exitCode = code; }, (e) => { console.error(e.message || e); process.exitCode = 1; });
  }
}
