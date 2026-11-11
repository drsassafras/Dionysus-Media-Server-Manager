#!/usr/bin/env python3
"""The style guide: every look the app may wear, approved by the maintainer. Nothing else may be added or changed.

    python3 dev/style.py check     # what's new, changed or gone since the guide (the suite's StyleGuideTests runs the same)
    python3 dev/style.py sample    # each of those Before (as approved) and After (now), side by side: dev/style-sample/
    python3 dev/style.py sample --before <folder>   # the Before from a folder of the approved look files
    python3 dev/style.py review    # the maintainer only, at a terminal: the sample's page with Approve buttons
                                   # (or r on callboard's board: review --board, its output in dev/style-sample/review.log)
    python3 dev/style.py approve   # the maintainer only, at a terminal: the sample's looks go into the guide

The guide (dev/style-guide.json) lists each look by what it is, with what it is now:
* ``css <at-rule> | <selector>``: a rule of the stylesheet (plexcache/web/app.css) or of a page's own <style>, its
  declarations (a selector written more than once: each time's, in order, joined by " ‖ ");
* ``inline <style>``: a style="..." a page or script writes (what a script fills in shown as …);
* ``scripted <property>``: a style property a script sets itself (el.style.<property> = …, setProperty);
* ``svg <markup>``: a drawing a page or script writes (an icon, the grape arrow, a chart's marks);
* ``mark <file> <name>``: what draws the decorations: themes.py's css() and script(), as written, comments left out;
  and what plexcache/web/decor.js draws (``mark decor.js draws | <where>``: each drawing it puts on a fixed page and
  each style it sets, by its SHA-256; dev/decor-draws.js), so a change to its code that draws the same isn't a new look;
* ``theme <id>/<mode> <colour>``, ``theme <id> font|decor <key>``, ``theme font <name>``: the built-in themes
  (plexcache/themes.py);
* ``file <path>``: an image or font the app ships (plexcache/web's .svg files and fonts/), by its SHA-256.

A look that isn't in the guide, or isn't as the guide has it, or is in the guide but gone from the code, fails
StyleGuideTests. Only ``approve`` (and the review page's Approve buttons) writes the guide, and only for a sample made
of exactly the looks that differ now (its fingerprint is kept in the guide with the date): the maintainer sees each look
before it is allowed. It asks at a terminal and won't run without one. Nobody else runs it or edits the guide (CLAUDE.md).
The one exception, the maintainer's rule: a look the sample photographed that drew exactly the same Before and After,
in every theme and mode on every page that draws it, changes nothing anyone sees, so ``sample`` puts it in the guide
itself (an approval recorded "on": "drew the same"); a look drawn on no demo page still waits for the maintainer.

``sample`` starts two demos (tests/demo.py, ports of their own): today's app, and the app as approved (today's with the
approved look files over it: the copies approve keeps in git, their blob ids the guide's ``files``, or --before). It takes the pictures with Playwright (dev/style-sample.js,
like dev/screenshots.js: ``npm ci --prefix dev && npx --prefix dev playwright install chromium``), with the progress bar the dev
tools share (callboard/progress.py): each look Before and After, cropped alike and compared pixel for pixel. It writes
dev/style-sample/index.html (open it in a browser): what changed on screen side by side, what's judged by its code, and,
folded away, what drew the same; and sample.json.
"""

import ast
import datetime
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tokenize
import urllib.parse
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "plexcache", "web")
GUIDE = os.path.join(ROOT, "dev", "style-guide.json")
SAMPLE = os.path.join(ROOT, "dev", "style-sample")
REVIEW = "python3 dev/style.py review"   # the one command to give the maintainer: it makes the sample when it's out of
# date, opens the page and has the Approve buttons (approve, at a terminal, is the same without the page)
FORMAT = 1
sys.path.insert(0, ROOT)
import dev  # noqa: E402, F401 — (puts the callboard install on the path: dev/__init__.py)

from callboard import devpage, policy  # noqa: E402 — the dev tools' pages' colours, and the rules (callboard.toml)

# The rules (callboard.toml's [style]): what looks waiting for approval are held as.
SCHEMA = policy.Schema("style", "Wardrobe", {
    "waiting": policy.Setting("level", "looks not in the style guide, waiting for approval"),
}, "The looks the app wears, against the style guide.")
from callboard.progress import Progress, feeding, result, said, tally, then  # noqa: E402 — the dev tools' shared progress bar, and callboard's line under it


def _dev(name: str, file: str):
    """Another dev tool's module (dev/ isn't a package; dev/i18n.py would clash with plexcache/i18n.py by name)."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "dev", file))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_i18n = _dev("i18n_tool", "i18n.py")  # its JavaScript tokenizer: the strings a script writes, as the translations read them

SCRIPTS = ("app.js", "decor.js", "i18n.js", "login.js")
PAGES = ("index.html", "login.html")
DRAWS = ("decor.js",)   # a script that is all decoration: a look for what it draws (dev/decor-draws.js), not for how it's written
_SHAPES = re.compile(r"<(svg|path|circle|ellipse|rect|polygon|polyline|line|g|mask|pattern|linearGradient|radialGradient|stop|use|text)\b")
HOLE = "…"   # what a script fills in


# ------------------------------------------------------------------ CSS
def _squash(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*([:;,>{}+~()])\s*", r"\1", s)
    return _wordless_css(s.strip(";"))


# Words aren't a look: what the app says is content (a card's title reworded), not style, so it never needs approving.
# In a look, every bit of text that has a letter in it is "…": the text between tags, the attributes people read
# (title, aria-label, alt, placeholder), and a stylesheet's content:"…". Text without letters (…%, a glyph, an
# arrow) stays: that's drawing.
_LETTER = re.compile(r"[^\W\d_]")
_READ = re.compile(r'(\s(?:title|aria-label|aria-description|aria-roledescription|alt|placeholder|data-tip|data-title)=")([^"]*)"')


def _word(text: str) -> str:
    return "…" if _LETTER.search(text) and text.strip() != "…" else text


def _wordless(markup: str) -> str:
    markup = _READ.sub(lambda m: m.group(1) + _word(m.group(2)) + '"', markup)
    return re.sub(r">([^<>]+)<", lambda m: ">" + (m.group(1) if not m.group(1).strip() else _word(m.group(1).strip())) + "<", markup)


def _wordless_css(decls: str) -> str:
    return re.sub(r"""(content:[^;]*?)(["'])((?:(?!\2).)*)\2""", lambda m: m.group(1) + m.group(2) + _word(m.group(3)) + m.group(2), decls)


def _head(s: str) -> str:
    """An at-rule's head: @media (max-width:600px), however it's spaced."""
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s*\)", ")", re.sub(r"\(\s*", "(", re.sub(r"\s*:\s*", ":", s)))


def _selector(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s*([,>+~])\s*", r"\1", s)


def css_looks(text: str, where: str = "") -> dict:
    """Each rule of a stylesheet: ``css <at-rules> | <selector>`` -> its declarations (" ‖ " between a selector's
    several rules). A @keyframes or @font-face is one look: its whole body."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    found: dict[str, list[str]] = {}
    i, n, ctx = 0, len(text), []

    def add(key, body):
        found.setdefault(key, []).append(body)

    while i < n:
        j = text.find("{", i)
        close = text.find("}", i)
        if close != -1 and (j == -1 or close < j):     # the end of an at-rule's block
            if ctx:
                ctx.pop()
            i = close + 1
            continue
        if j == -1:
            break
        head = text[i:j].strip()
        if head.startswith("@media") or head.startswith("@supports") or head.startswith("@layer"):
            ctx.append(_head(head))
            i = j + 1
            continue
        depth, k = 1, j + 1
        while depth and k < n:
            depth += {"{": 1, "}": -1}.get(text[k], 0)
            k += 1
        body = text[j + 1:k - 1]
        pre = " ".join(([where] if where else []) + ctx)
        if head.startswith("@"):
            add(" ".join(x for x in (pre, _head(head)) if x), _squash(body))
        else:
            add((pre + " | " if pre else "") + _selector(head), _squash(body))
        i = k
    return {"css " + k: " ‖ ".join(v) for k, v in found.items()}


# ------------------------------------------------------------------ scripts
def js_texts(src: str) -> list[str]:
    """What a script writes: each string, or strings joined with +, what's between them shown as …."""
    toks = _i18n.js_tokens(src)
    out, k, n = [], 0, len(toks)
    while k < n:
        if toks[k][0] != "str":
            k += 1
            continue
        parts = [toks[k][1]]
        k += 1
        while k < n and toks[k][0] == "p" and toks[k][1] == "+" and not (k + 1 < n and toks[k + 1][1] == "="):
            k += 1
            if k < n and toks[k][0] == "str":
                parts.append(toks[k][1])
                k += 1
                continue
            depth = 0
            while k < n:                       # an expression: to the next + (or its end) outside brackets
                kind, v = toks[k][0], toks[k][1]
                if kind == "p" and v in "([{":
                    depth += 1
                elif kind == "p" and v in ")]}":
                    if not depth:
                        break
                    depth -= 1
                elif not depth and kind == "p" and v in "+,;:?":
                    break
                k += 1
            parts.append(HOLE)
        text = re.sub(r"\$\{[^}]*\}", HOLE, "".join(parts))
        out.append(re.sub(HOLE + "+", HOLE, text))
    return out


def _inline(style: str) -> str:
    return "inline " + _squash(style)


def _svg(markup: str) -> str:
    return "svg " + _wordless(re.sub(r"\s+", " ", markup).strip())


def script_looks(src: str) -> dict:
    found = {}
    for text in js_texts(src):
        for m in re.finditer(r'\bstyle="([^"]*)"', text):
            found[_inline(m.group(1))] = ""
        if _SHAPES.search(text):
            found[_svg(text)] = ""
    code = re.sub(r"//[^\n]*|/\*.*?\*/", "", src, flags=re.S)
    for m in re.finditer(r"\.style\.([A-Za-z]+)\s*=(?!=)", code):
        found["scripted " + m.group(1)] = ""
    for m in re.finditer(r"""\.style\.setProperty\(\s*["']([^"']+)""", code):
        found["scripted " + m.group(1)] = ""
    return found


def page_looks(html: str, name: str) -> dict:
    found = {}
    for m in re.finditer(r"<style\b[^>]*>(.*?)</style>", html, flags=re.S | re.I):
        found.update(css_looks(m.group(1), name))
    for m in re.finditer(r'\bstyle="([^"]*)"', html):
        found[_inline(m.group(1))] = ""
    for m in re.finditer(r"<svg\b.*?</svg>", html, flags=re.S):
        found[_svg(m.group(0))] = ""
    return found


def mark_looks(src: str, name: str) -> dict:
    """A decoration script's parts: each top-level function or variable of its wrapper (two spaces in), as written."""
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = "\n".join(re.sub(r"(^|\s)//.*$", "", line).rstrip() for line in code.split("\n"))
    found, cur, buf = {}, "(start)", []
    for line in code.split("\n"):
        m = re.match(r"  (?:function\s+([\w$]+)|(?:var|let|const)\s+([\w$]+))", line)
        if m:
            if "".join(buf).strip():
                found[f"mark {name} {cur}"] = re.sub(r"\s+", " ", "\n".join(buf)).strip()
            cur, buf = m.group(1) or m.group(2), []
            while f"mark {name} {cur}" in found:
                cur += "'"
        buf.append(line)
    if "".join(buf).strip():
        found[f"mark {name} {cur}"] = re.sub(r"\s+", " ", "\n".join(buf)).strip()
    return found


def drawn_looks(path: str, name: str) -> dict:
    """What a decoration script draws: dev/decor-draws.js runs it in node on a fixed page (a computer's and a phone's,
    every decoration on and off); each drawing and style it puts there, by where, as its SHA-256."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError(f"node isn't installed: it runs {name} (dev/decor-draws.js) for what it draws")
    run = subprocess.run([node, os.path.join(ROOT, "dev", "decor-draws.js"), path], capture_output=True, text=True, timeout=120)
    if run.returncode:
        raise RuntimeError(f"dev/decor-draws.js couldn't run {name}: {run.stderr.strip()}")
    return {f"mark {name} draws | {where}": hashlib.sha256(what.encode()).hexdigest() for where, what in json.loads(run.stdout).items()}


# ------------------------------------------------------------------ themes and files
def theme_looks(root: str | None = None) -> dict:
    """The built-in themes: plexcache/themes.py's colours, fonts and decorations, and how its css() and script() are
    written. Read from its source as data (_themes_read), never run: another tree's (the Before's, out of git) too."""
    root = os.path.abspath(root or ROOT)
    path = own_file(root, "plexcache/themes.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        found = _theme_data(_themes_read(src))
    except (AttributeError, KeyError, TypeError, ValueError, SyntaxError) as e:
        raise RuntimeError(f"couldn't read the themes of {root}: {e}") from e
    found.update(py_marks(src, "themes.py", ("css", "script")))
    return found


def _themes_read(src: str):
    """themes.py's data — its names set to plain values (BUILT_IN, AUTHORED, FOLLOWS, _FONT_STACK and what they're made of: literals, the
    names before them, dict(name, **{…})) — worked out from the source alone; what isn't plain data is left out."""
    import types
    from typing import Any
    env: dict = {}

    def value(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return env[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            items = [value(e) for e in node.elts]
            return items if isinstance(node, ast.List) else tuple(items)
        if isinstance(node, ast.Dict):
            out: dict = {}
            for k, v in zip(node.keys, node.values, strict=True):
                if k is None:
                    out.update(value(v))
                else:
                    out[value(k)] = value(v)
            return out
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict" and len(node.args) <= 1:
            out: dict = dict(value(node.args[0])) if node.args else {}
            for kw in node.keywords:
                if kw.arg is None:
                    out.update(value(kw.value))
                else:
                    out[kw.arg] = value(kw.value)
            return out
        raise ValueError("not plain data")

    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                env[node.targets[0].id] = value(node.value)
            except (ValueError, KeyError, TypeError):
                env.pop(node.targets[0].id, None)
    return types.SimpleNamespace(**env)


def _built_colors(themes) -> dict:
    """Each built-in look's every colour, worked out from its own as themes.py's follow() does: a colour it leaves out
    that follows another (FOLLOWS) is that one where the look sets it (or has no base), else its base's (AUTHORED: each
    look's base and own colours). {} for a tree whose looks write every colour out (from before the follow defaults)."""
    authored = getattr(themes, "AUTHORED", None)
    if not authored:
        return {}
    follows, colors, out = themes.FOLLOWS, themes.COLORS, {}

    def look(key: tuple) -> dict:
        if key not in out:
            base, own = authored[key]
            b = look(tuple(base)) if base else None
            full = {k: own[k] if k in own else (b or {})[k] for k in colors if k in own or k not in follows}
            for k, lead in follows.items():
                if k not in own:
                    full[k] = full[lead] if b is None or lead in own else b[k]
            out[key] = {k: full[k] for k in colors}
        return out[key]
    for key in authored:
        look(tuple(key))
    return out


def _theme_data(themes) -> dict:
    found = {}
    built = _built_colors(themes)
    for t in themes.BUILT_IN:
        for mode in ("light", "dark"):
            for k, v in built.get((t["id"], mode), t[mode]["colors"]).items():
                found[f"theme {t['id']}/{mode} {k}"] = v
        for k, v in t["fonts"].items():
            found[f"theme {t['id']} font {k}"] = v
        for k, v in t["decor"].items():
            found[f"theme {t['id']} decor {k}"] = json.dumps(v)
    for k, v in themes._FONT_STACK.items():
        found[f"theme font {k}"] = v
    return found


def py_marks(src: str, name: str, functions) -> dict:
    """Python functions that draw a look, as written: comments and docstring left out, however it's spaced (the same
    on every Python: the source itself, not the parser's rewrite of it)."""
    lines = src.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    at = lambda row, col: starts[row - 1] + col   # noqa: E731
    cut = [(at(*t.start), at(*t.end)) for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type == tokenize.COMMENT]
    found = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            first = node.body[0]
            doc = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
            spans = cut + ([(at(first.lineno, first.col_offset), at(first.end_lineno or first.lineno, first.end_col_offset or 0))] if doc else [])
            lo, hi = at(node.lineno, node.col_offset), at(node.end_lineno or node.lineno, node.end_col_offset or 0)
            text, pos = [], lo
            for a, b in sorted(x for x in spans if lo <= x[0] < hi):
                text.append(src[pos:a])
                pos = max(pos, b)
            text.append(src[pos:hi])
            found[f"mark {name} {node.name}"] = re.sub(r"\s+", " ", "".join(text)).strip()
    return found


IMAGES = (".svg", ".woff2", ".woff", ".ttf", ".otf", ".png", ".jpg", ".gif", ".webp", ".ico")
DRAWN = (".svg", ".png", ".jpg", ".gif", ".webp", ".ico")   # (of those, what the sample draws as it is: the rest, fonts, on the pages)


def file_looks(root: str | None = None) -> dict:
    root = root or ROOT
    found = {}
    for base, _dirs, files in os.walk(os.path.join(root, "plexcache", "web")):
        if os.path.basename(base) in ("i18n",):
            continue
        for f in sorted(files):
            if f.endswith(IMAGES):
                path = own_file(root, os.path.relpath(os.path.join(base, f), root))
                with open(path, "rb") as fh:
                    found["file " + os.path.relpath(path, root).replace(os.sep, "/")] = hashlib.sha256(fh.read()).hexdigest()
    return found


def own_file(root: str, rel: str) -> str:
    """A look file's path in a tree, once it's sure to be a file of that tree's own: never a link, nor in a folder
    that's a link, so no look is read from somewhere else on this computer (and kept in the guide, or the approved copy)."""
    path = os.path.join(root, rel)
    top = os.path.realpath(root)
    if os.path.islink(path) or not os.path.realpath(path).startswith(top + os.sep):
        raise RuntimeError(f"{rel} is a link (or in a folder that is): a look file has to be a file of the app's own")
    return path


def look_files(root: str | None = None) -> list[str]:
    """The files the looks are read from (relative to the tree): what a copy of the approved app has to keep."""
    root = root or ROOT
    out = ["plexcache/themes.py", "plexcache/web/app.css"] + [f"plexcache/web/{n}" for n in SCRIPTS + PAGES]
    out += [k[len("file "):] for k in file_looks(root)]
    return sorted(set(p for p in out if os.path.lexists(own_file(root, p))))


def looks(root: str | None = None) -> dict:
    """Every look the app has now, by what it is (above) -> what it is."""
    tree = root or ROOT

    def read(name):
        with open(own_file(tree, f"plexcache/web/{name}"), encoding="utf-8") as f:
            return f.read()

    found = css_looks(read("app.css"))
    for name in SCRIPTS:
        found.update(drawn_looks(own_file(tree, f"plexcache/web/{name}"), name) if name in DRAWS else script_looks(read(name)))
    for name in PAGES:
        found.update(page_looks(read(name), name))
    found.update(theme_looks(root))
    found.update(file_looks(root))
    return dict(sorted(found.items()))


# ------------------------------------------------------------------ the guide
def seal(items: dict, files: dict | None = None) -> str:
    """The guide's seal: its looks and, once it keeps them, the approved look files' ids (``files``). An unkeyed hash:
    it catches a hand edit, not one made on purpose (files_problems holds ``files`` to git's history for that)."""
    what = {"files": files, "looks": items} if files else items
    return hashlib.sha256(json.dumps(what, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_guide(path: str = GUIDE) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compare(approved: dict, now: dict) -> dict:
    """What differs from the guide: {"new": {look: now}, "changed": {look: [approved, now]}, "gone": {look: approved}}."""
    return {
        "new": {k: v for k, v in now.items() if k not in approved},
        "changed": {k: [approved[k], v] for k, v in now.items() if k in approved and approved[k] != v},
        "gone": {k: v for k, v in approved.items() if k not in now},
    }


def fingerprint(diff: dict) -> str:
    """The sample's fingerprint: exactly these differences (a sample of other ones doesn't count)."""
    return hashlib.sha256(json.dumps(diff, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def pending(guide: dict | None = None, now: dict | None = None) -> dict:
    """What waits for approval — every look new, changed or gone since the guide (compare's) — as check, the board's
    line and review all take it: what check flags, review shows."""
    guide = (load_guide() if os.path.exists(GUIDE) else {}) if guide is None else guide
    return compare(guide.get("looks", {}), looks() if now is None else now)


# What a sample's pictures are drawn from, besides the looks: the app (all of plexcache/: its pages, scripts and
# translations, and the server that answers them), the demo and its fakes (the data drawn), and the tools that draw and
# show them (the sample's code, the review page's; MADE_WITH: the part of them that's the callboard install's). A sample made from any other version of these isn't the one a sample
# made now would be, so it's made again.
MADE_FROM = ("plexcache", "tests/demo.py", "tests/fakes.py", "tests/fixtures", "dev/style.py", "dev/style-sample.js",
             "dev/still.js", "dev/decor-draws.js", "dev/i18n.py", "dev/package-lock.json")
MADE_WITH = (devpage.__file__,)   # (and of the callboard install: the dev pages' shared code, which the review page wears)


def made_from(before: str | None = None, root: str = ROOT) -> str:
    """The sample's inputs, as one hash: every file of MADE_FROM (its path and bytes), the Python that runs the demo, and
    where its Before came from (``before``: a folder or zip, or None for the approved files in git). Kept in sample.json;
    a sample whose inputs differ from now's is out of date (sample_shows), whatever looks it shows."""
    h = hashlib.sha256()
    h.update(json.dumps({"python": sys.version, "before": os.path.abspath(before) if before else None}).encode())
    for top in MADE_FROM:
        start = os.path.join(root, top)
        if os.path.isfile(start):
            paths = [start]
        else:
            paths = []
            for d, dirs, files in os.walk(start):
                dirs[:] = sorted(x for x in dirs if x != "__pycache__" and x != "node_modules")
                paths += [os.path.join(d, f) for f in sorted(files) if not f.endswith(".pyc") and f != ".DS_Store"]
        for path in paths:
            h.update(os.path.relpath(path, root).replace(os.sep, "/").encode() + b"\0")
            try:
                with open(path, "rb") as f:
                    h.update(hashlib.sha256(f.read()).digest())
            except OSError:
                h.update(b"-")
    for path in MADE_WITH:
        h.update(b"callboard/" + os.path.basename(path).encode() + b"\0")
        try:
            with open(path, "rb") as f:
                h.update(hashlib.sha256(f.read()).digest())
        except OSError:
            h.update(b"-")
    return h.hexdigest()


def sample_shows(diff: dict, out: str = SAMPLE) -> bool:
    """Whether the sample in ``out`` shows every look of ``diff`` as it is now (approving some since doesn't make it
    out of date), made from the code as it is now (made_from: the app, the demo, the tools that draw it, and the
    approved files as its Before); else it has to be made again before it's reviewed."""
    try:
        with open(os.path.join(out, "sample.json"), encoding="utf-8") as f:
            made = json.load(f)
    except (OSError, ValueError):
        return False
    if made.get("made_from") != made_from():   # (drawn by other code, or from another Before: not what one made now draws)
        return False
    shown = {(it["state"], it["look"]): it for it in made.get("items", [])}

    def in_sample(st, k, v):
        it = shown.get((st, k))
        return it is not None and v == {"new": it["now"], "changed": [it["was"], it["now"]], "gone": it["was"]}[st]
    return os.path.exists(os.path.join(out, "index.html")) and (made.get("fingerprint") == fingerprint(diff) or
                                                                 (bool(shown) and all(in_sample(st, k, v) for st in diff for k, v in diff[st].items())))


def made_by(diff: dict, out: str = SAMPLE) -> dict | None:
    """The sample in ``out`` when it shows ``diff`` already: what sample.json says of it (when it was made, with how many
    workers — None from one made before it said), else None."""
    if not sample_shows(diff, out):
        return None
    try:
        with open(os.path.join(out, "sample.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def problems(guide: dict | None = None, now: dict | None = None) -> list[str]:
    """Each look that differs from the guide, in words, and a guide that wasn't written by approve. [] when all agree."""
    history = guide is None   # (the guide here, held to git's history: one given isn't)
    guide = load_guide() if guide is None else guide
    out = []
    looks_, files = guide.get("looks", {}), guide.get("files") or None
    # (a guide sealed before the seal took in its files: its looks' alone, until the next approval writes it again)
    if guide.get("format") != FORMAT or guide.get("seal") not in (seal(looks_, files), *([seal(looks_)] if files else [])):
        out.append("dev/style-guide.json wasn't written by dev/style.py approve (its seal doesn't match its looks): "
                   "put it back as it was (git checkout dev/style-guide.json)")
    out += files_problems(guide, history=history)
    diff = pending(guide, now)
    out += [f"new: {_short(k)}" for k in diff["new"]]
    out += [f"changed: {_short(k)}" for k in diff["changed"]]
    out += [f"gone: {_short(k)}" for k in diff["gone"]]
    return out


def _short(s: str, n: int = 160) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def write_guide(items: dict, approval: dict, path: str = GUIDE, before: dict | None = None):
    """The guide with these looks, the approval added to its record. Only approve (and approve_looks) calls it."""
    old = before if before is not None else (load_guide(path) if os.path.exists(path) else {})
    doc = {
        "about": "The style guide: every look the app may wear, each approved by the maintainer from a sample "
                 "(dev/style.py; StyleGuideTests). Written by dev/style.py approve only: never edit it by hand.",
        "format": FORMAT,
        "approvals": old.get("approvals", []) + [approval],
        "seal": seal(items, old.get("files")),
        **({"files": old["files"]} if "files" in old else {}),   # (the approved app's look files: keep_approved's)
        "looks": dict(sorted(items.items())),
    }
    _dump_guide(doc, path)


def _dump_guide(doc: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")


# ------------------------------------------------------------------ the sample
LOOKS = [("classic", "light"), ("classic", "dark"), ("dionysus", "light"), ("dionysus", "dark")]
FIXED_PAGES = ["health", "tools", "merge", "system"]   # where a mark, a colour or a font is seen
_STATE = re.compile(r"::?(hover|focus-visible|focus-within|focus|active|visited|before|after|placeholder|selection|marker|"
                    r"checked|disabled|first-letter|first-line|-?[a-z-]*scrollbar[a-z-]*)\b")


# The states the sample puts an element in for its pictures (style-sample.js force): the browser's forced pseudo-classes,
# and a form control's own properties. Another state in a selector (visited, placeholder, a scrollbar…) isn't pictured.
FORCED = ("hover", "focus", "focus-visible", "focus-within", "active")
PROPERTY_STATES = ("checked", "disabled", "indeterminate")
# The settings an @media rule can be pictured in (Playwright's context options); a rule for any other isn't.
MEDIA = {"prefers-reduced-motion:reduce": ("reducedMotion", "reduce"), "forced-colors:active": ("forcedColors", "active")}
FRAMES = (0, 0.25, 0.5, 0.75)   # how far through each animation or transition the filmstrip's pictures are taken
_PSEUDO = re.compile(r"::?([a-z-]+)")


def _state_target(sel: str) -> tuple[str, list[str]] | None:
    """A selector's element that a state is on, and the states (the compound with the first pictured state in it, its
    states taken out: ".card:hover .btn" -> (".card", ["hover"])); None when it names none."""
    m = re.search(r":(" + "|".join(FORCED + PROPERTY_STATES) + r")\b", sel)
    if not m:
        return None
    end, depth = m.end(), 0
    while end < len(sel) and (depth or sel[end] not in " >+~"):
        depth += {"(": 1, ")": -1}.get(sel[end], 0)
        end += 1
    compound = sel[:end]
    states = [s for s in _PSEUDO.findall(compound[compound.rfind(" ") + 1:]) if s in FORCED + PROPERTY_STATES]
    return _STATE.sub("", compound).strip() or "body", states


def finder(look: str, value) -> dict:
    """How the sample finds a look on the demo's pages: by selector, by style, by the animation it names, on the usual
    pages, or not at all; and what it's photographed in: a ``width`` an @media rule takes, the ``media`` settings it
    takes (reduced motion, forced colours), the ``states`` its selector names (each [element, states]), and ``frames``
    when it moves (an animation, a transition: a filmstrip of FRAMES)."""
    kind, _, rest = look.partition(" ")
    if kind == "css":
        ctx, _, sel = rest.rpartition(" | ") if " | " in rest else ("", "", rest)
        if ctx.startswith("@keyframes"):
            return {"how": "code"}
        if sel.startswith("@keyframes "):
            return {"how": "animation", "name": sel.split(" ", 1)[1].strip(), "width": 1280, "frames": list(FRAMES)}
        if sel.startswith("@") or ctx.startswith("@") and not sel:
            return {"how": "code"}
        if "print" in ctx:
            return {"how": "code"}
        m = re.search(r"max-width:(\d+)px", ctx)
        width = min(1280, int(m.group(1)) - 10) if m else 1280
        media = {}
        for feature in re.findall(r"\(([a-z-]+:[a-z-]+)\)", ctx):
            if feature in MEDIA:
                media[MEDIA[feature][0]] = MEDIA[feature][1]
        parts = [_STATE.sub("", p).strip() or "body" for p in sel.split(",")]
        out = {"how": "selector", "selectors": parts, "width": width}
        if media:
            out["media"] = media
        states = [t for t in (_state_target(p) for p in sel.split(",")) if t]
        if states:
            out["states"] = [list(t) for t in states]
        if re.search(r"(?:^|;)\s*(?:animation|transition)[\w-]*\s*:", str(value or "")):
            out["frames"] = list(FRAMES)
            if not states and re.search(r"(?:^|;)\s*transition", str(value or "")):
                out["states"] = [[parts[0], ["hover"]]]   # (a transition shows as the element changes: hovered, here)
        return out
    if kind == "inline":
        pat = re.escape(rest).replace(re.escape(HOLE), ".*")
        return {"how": "style", "pattern": "^" + pat + ";?$"}
    if kind == "svg":
        m = re.search(r'<svg\b[^>]*\bclass="([\w -]+)"', rest)
        if m and HOLE not in m.group(1):
            return {"how": "selector", "selectors": ["svg." + ".".join(m.group(1).split())], "width": 1280}
        return {"how": "code"}
    if kind in ("mark", "theme"):
        return {"how": "pages"}
    if kind == "file" and rest.endswith(DRAWN):   # (an image: itself, Before and After; a font, on the pages)
        return {"how": "image", "path": rest}
    if kind == "file":
        return {"how": "pages"}
    return {"how": "code"}   # scripted: its value is worked out as the page draws


def app_pages() -> list[str]:
    """Every page of the app (app.js's PAGES), where the sample looks for each look."""
    with open(os.path.join(WEB, "app.js"), encoding="utf-8") as f:
        m = re.search(r"(?:var|let|const) PAGES = \{(.*?)\};", f.read(), flags=re.S)
    return re.findall(r"([a-z]+):\s*page[A-Z]", m.group(1)) if m else FIXED_PAGES


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _node_path() -> str:
    try:
        top = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        top = ""
    return os.pathsep.join(x for x in (os.environ.get("NODE_PATH", ""), top) if x)


_BLOB = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def keep_approved(root: str = ROOT, guide_path: str = GUIDE, repo: str = ROOT) -> dict:
    """The files the approved looks are read from, which approve keeps with the guide (the sample's Before): each kept
    in git (``git hash-object -w``, in ``repo``), and its blob id in the guide's ``files``. So the Before is the app's own
    files as git has them, readable in its history (``git show <id>``), never a copy nobody can read in a diff: once the
    files are committed as they were approved, every clone has them."""
    files = {}
    for rel in look_files(root):
        got = subprocess.run(["git", "-C", repo, "hash-object", "-w", f"--path={rel}", "--", own_file(root, rel)],
                             capture_output=True, text=True, timeout=60)
        if got.returncode or not _BLOB.match(got.stdout.strip()):
            raise RuntimeError(f"git couldn't keep {rel}: {got.stderr.strip()}")
        files[rel] = got.stdout.strip()
    guide = load_guide(guide_path)
    guide["files"] = dict(sorted(files.items()))
    guide["seal"] = seal(guide.get("looks", {}), guide["files"])
    _dump_guide({k: guide[k] for k in ("about", "format", "approvals", "seal", "files", "looks") if k in guide} | guide, guide_path)
    return files


def look_file(rel) -> bool:
    """Whether a path is one look_files names, in any tree: themes.py, app.css, the scripts and pages, an image or font
    of plexcache/web. What the guide's ``files`` may hold, so a Before puts nothing else of plexcache in (its __init__.py,
    say, which the demo would run)."""
    rel = str(rel)
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    if rel in ("plexcache/themes.py", "plexcache/web/app.css") or rel in {f"plexcache/web/{n}" for n in SCRIPTS + PAGES}:
        return True
    return parts[:2] == ["plexcache", "web"] and len(parts) > 2 and rel.endswith(IMAGES) and parts[-2] != "i18n"


def files_problems(guide: dict, path: str = GUIDE, repo: str = ROOT, history: bool = True) -> list[str]:
    """What's wrong with the guide's ``files``, the code a sample's Before runs (tests/demo.py imports its themes.py,
    dev/decor-draws.js runs its decor.js): an entry that isn't a look file's blob id; and, held to git's history (a
    full clone: a copy from git archive or a shallow one has none to hold it to), ``files`` changed with no new approval
    (only approve writes them, with one), or a blob that isn't that file as committed with them."""
    files = guide.get("files") or {}
    out = [f"dev/style-guide.json's files has {rel!r}: {blob!r}, which isn't a look file's blob id"
           for rel, blob in sorted(files.items()) if not look_file(rel) or not _BLOB.match(str(blob))]
    if not files or not history or out:
        return out

    def git(*args):
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=60)
    top = git("rev-parse", "--show-toplevel")
    if (top.returncode or os.path.realpath(top.stdout.strip()) != os.path.realpath(repo)
            or git("rev-parse", "--is-shallow-repository").stdout.strip() != "false"):
        return out
    rel_guide = os.path.relpath(path, repo).replace(os.sep, "/")
    versions: list[tuple[str | None, dict]] = [(None, guide)]   # (newest first: the guide here, then each commit's)
    for c in git("log", "--format=%H", "--", rel_guide).stdout.split():
        try:
            versions.append((c, json.loads(git("show", f"{c}:{rel_guide}").stdout)))
        except ValueError:
            break
    k = 0   # (versions[k]: where these files came in)
    while k + 1 < len(versions) and (versions[k + 1][1].get("files") or {}) == files:
        k += 1
    older = versions[k + 1][1] if k + 1 < len(versions) else None
    # (the first files ever kept may come in on their own; after that, only with an approval)
    if older is not None and older.get("approvals") == versions[k][1].get("approvals") and any(v.get("files") for _, v in versions[k + 1:]):
        out.append("dev/style-guide.json's files changed with no approval (only dev/style.py approve writes them): "
                   "put it back as it was (git checkout dev/style-guide.json)")
    commit = versions[k][0]
    if commit is None:   # (approved here, not committed yet)
        return out
    for rel, blob in sorted(files.items()):
        if git("rev-parse", "--verify", "--quiet", f"{commit}:{rel}").stdout.strip() == blob:
            continue
        later = git("rev-list", f"{commit}..HEAD", "--", rel).stdout.split()   # (the files committed after the guide)
        if not any(git("rev-parse", "--verify", "--quiet", f"{c}:{rel}").stdout.strip() == blob for c in later):
            out.append(f"dev/style-guide.json's files has {rel} as {blob[:12]}, which isn't that file as committed with them "
                       f"({commit[:12]}) or since: put it back as it was (git checkout dev/style-guide.json)")
    return out


def approved_files(files: dict, into: str, repo: str = ROOT) -> list[str]:
    """The approved look files (the guide's ``files``: path -> blob id) written out of git into ``into``; what git
    here doesn't have (approved, never committed, and gone since) left out and named."""
    missing = []
    for rel, blob in sorted(files.items()):
        parts = str(rel).split("/")
        if not _BLOB.match(str(blob)) or not look_file(rel):
            raise RuntimeError(f"the guide's files has {rel!r}: {blob!r}, which isn't a look file's blob id")
        got = subprocess.run(["git", "-C", repo, "cat-file", "blob", blob], capture_output=True, timeout=60)
        if got.returncode:
            missing.append(rel)
            continue
        os.makedirs(os.path.join(into, *parts[:-1]), exist_ok=True)
        with open(os.path.join(into, *parts), "wb") as f:
            f.write(got.stdout)
    return missing


def before_tree(src: str | dict, tmp: str, approved: dict | None = None, repo: str = ROOT) -> str:
    """The app as it was approved: this tree's plexcache and tests, with the look files from ``src`` (the guide's
    ``files``, out of git; or a folder or zip given with --before) put over theirs, and any look file ``src`` doesn't
    have taken out. An image or font that's as the guide (``approved``) has it now stays as it is."""
    tree = os.path.join(tmp, "before")
    if isinstance(src, dict):
        missing = approved_files(src, os.path.join(tmp, "approved"), repo)
        if missing:
            raise RuntimeError(f"git here doesn't have the approved {', '.join(missing)}")
        src = os.path.join(tmp, "approved")
    elif zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            z.extractall(os.path.join(tmp, "approved"))
        src = os.path.join(tmp, "approved")
    skip = shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules")
    for d in ("plexcache", "tests"):
        shutil.copytree(os.path.join(ROOT, d), os.path.join(tree, d), ignore=skip)
    for rel in look_files(tree):
        if not os.path.exists(os.path.join(src, rel)):
            os.remove(os.path.join(tree, rel))
    for base, _dirs, files in os.walk(src):
        for f in files:
            rel = os.path.relpath(os.path.join(base, f), src)
            if rel.split(os.sep)[0] == "plexcache" and "__pycache__" not in rel:
                os.makedirs(os.path.dirname(os.path.join(tree, rel)), exist_ok=True)
                shutil.copy2(os.path.join(base, f), os.path.join(tree, rel))
    for look, now in file_looks(ROOT).items():
        rel = look[len("file "):]
        if (approved or {}).get(look) == now:
            shutil.copy2(os.path.join(ROOT, rel), os.path.join(tree, rel))
    return tree


# What the app says, and the markup it says it in: the pages and the scripts that write them, and the translations.
# The Before takes these from today's app, so a reworded title isn't a difference (words aren't a look): "document"
# is the page itself, a path ending in / everything under it.
WORDS = ["document", "/static/app.js", "/static/login.js", "/static/i18n.js", "/static/i18n/"]


def judged_by(look: str, changed_files: list[str]) -> dict:
    """What of the Before a look seen on whole pages is judged against, so a change elsewhere on the page isn't taken
    for its own: a theme's look, or a shipped image, just its own file ({"only": [paths]}) over today's app; anything
    else (decor.js, which can't run with another day's styles) the whole Before but for the shipped images that changed
    and today's words ({"except": [paths]})."""
    if look.startswith(("mark themes.py", "theme ")):
        return {"only": ["/theme.css", "/theme.js"]}
    if look.startswith("file plexcache/web/"):
        return {"only": ["/static/" + look[len("file plexcache/web/"):]]}
    return {"except": ["/static/" + f[len("plexcache/web/"):] for f in changed_files] + WORDS}


def cropped_by(look: str, new: bool = False) -> dict | None:
    """What of the Before a look photographed where it is is judged against: a stylesheet's rule that was approved
    before, the approved styles over today's pages and words (so the crop shows only the style changing); a rule that's
    ``new``, or a look written in a page or script (its markup, an inline style), the whole Before (None). A new rule
    over today's markup would draw that markup without any style at all (its class names nothing in the approved
    stylesheet), not what the approved app drew there: a style moved from the markup into a class would show as lost."""
    return {"except": WORDS} if look.startswith("css ") and not new else None


def stands_in(rel: str, tree: str) -> str | None:
    """For a new image, the image of the Before it's a variant of: the one whose name its own begins with, the longest
    (plexcache/web/theatre-light.svg -> plexcache/web/theatre.svg), of the same kind; None if there's none."""
    folder, name = os.path.split(rel)
    stem, ext = os.path.splitext(name)
    try:
        there = os.listdir(os.path.join(tree, folder))
    except OSError:
        return None
    best = max((f for f in there if f.endswith(ext) and f != name and stem.startswith(os.path.splitext(f)[0] + "-")),
               key=len, default=None)
    return f"{folder}/{best}" if best else None


def _demo(root: str, before: bool = False):
    """The demo (tests/demo.py) of a tree, on a port of its own; (process, url) once it answers, or (process, None).
    ``before``: the approved app's, whose policy takes inline styles as the app's did when they were approved (an older
    approval's look files wrote style="…", which today's policy refuses: the Before would draw them unstyled)."""
    port = _free_port()
    env = dict(os.environ, DEMO_SHOWCASE="1", **({"DEMO_INLINE_STYLES": "1"} if before else {}))
    proc = subprocess.Popen([sys.executable, os.path.join(root, "tests", "demo.py"), str(port)], cwd=root,
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, f"http://127.0.0.1:{port}"


def _answers(url: str) -> bool:
    for _ in range(120):
        try:
            urllib.request.urlopen(url + "/api/state", timeout=2)
            return True
        except OSError:
            time.sleep(0.5)
    return False


def sample(out: str = SAMPLE, before: str | None = None, live=None, stop=None) -> int:
    """Each look that differs from the guide, photographed before (as approved) and after (now) on the demo's pages, in
    each theme and mode, side by side; the looks that come out the same are set aside. It's made in a folder of its own
    (out/making) and only replaces the last sample once it's done: one that fails leaves the last one as it was.
    ``live`` is told as it goes (review's page, open while it's made: _sample); ``stop`` set stops it."""
    import fcntl
    os.makedirs(out, exist_ok=True)
    lock = open(os.path.join(out, ".sampling"), "w")  # noqa: SIM115 — (held while it's made: one sample at a time)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:   # (another is being made — callboard's board makes one: that one, once it's done, not another)
        print("A sample is already being made (callboard's Wardrobe, say): waiting for it.", flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
        diff = pending()
        if sample_shows(diff, out) or not any(diff.values()):
            lock.close()
            return 0
    work = os.path.join(out, "making")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    try:
        rc = _sample(work, before, out, live, stop)
        if rc == 0 and os.path.exists(os.path.join(work, "index.html")):
            for f in os.listdir(out):
                if f.endswith(".png") or f in ("index.html", "sample.json", "plan.json"):
                    os.remove(os.path.join(out, f))
            for f in os.listdir(work):
                os.replace(os.path.join(work, f), os.path.join(out, f))
        elif rc != 0 and os.path.exists(os.path.join(out, "index.html")):
            print(f"The last sample is still there: {os.path.relpath(os.path.join(out, 'index.html'), os.getcwd())}", file=sys.stderr)
        return rc
    finally:
        shutil.rmtree(work, ignore_errors=True)
        lock.close()


def _sample(out: str, before: str | None, shown: str, live=None, stop=None) -> int:
    """The sample, made in ``out``. ``live(state)``, if given, is told what's known as it goes — {"items", "shots", "done"
    (the ids of the looks with all their pictures), "off", "compared", "source", "fingerprint", "folder" (where their
    pictures are)} — once the looks are known (no pictures yet), then each time more of them have all their pictures."""
    started = time.time()
    inputs = made_from(before)   # (as they are as it starts: a file changed while it draws makes it out of date)
    approved = load_guide()["looks"] if os.path.exists(GUIDE) else {}
    diff = compare(approved, looks())
    count = sum(len(v) for v in diff.values())
    if not count:
        print("Every look is in the style guide: nothing to sample.")
        return 0
    # its one bar from the start (the demos take a few seconds to answer, the pictures longer): the steps before the
    # pictures, then the pages photographed on the same count (_pictures), filled once
    steps = Progress(4, unit="steps done", bar="style")
    steps.show("checking for Playwright", ticking=True)
    missing = _no_playwright()
    if missing:
        steps.clear()
        print(f"Playwright isn't installed for node ({missing}). Install it: npm ci --prefix dev && npx --prefix dev playwright install chromium",
              file=sys.stderr)
        return 1
    steps.draw("Playwright found")
    kept = (load_guide().get("files") if os.path.exists(GUIDE) else None) or None
    held = files_problems(load_guide()) if kept and not before else []
    if held:   # (code the Before would run that isn't as approved: none of it)
        print(f"  No Before: {held[0]}", file=sys.stderr)
        kept = None
    src = before or kept
    if not before and kept:   # (all of them out of git, or no Before: one missing would make a Before that isn't the approved app)
        with tempfile.TemporaryDirectory() as probe:
            missing = approved_files(kept, probe)
        if missing:
            print(f"  No Before: git here doesn't have the approved {', '.join(missing[:3])}{' …' if len(missing) > 3 else ''} "
                  "(approved, but not committed as they were).", file=sys.stderr)
            src = None
    if before and not (os.path.isdir(before) or zipfile.is_zipfile(before)):
        steps.clear()
        print(f"There's no folder (or zip) {before} to take the Before from.", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        steps.show("the Before: the approved look files", ticking=True)
        tree = before_tree(src, tmp, approved) if src else None
        steps.draw("the Before ready")
        off = compare(approved, looks(tree)) if tree else None   # (where the Before isn't quite as the guide has it)
        items = []
        images = sorted(k[len("file "):] for st in diff.values() for k in st if k.startswith("file plexcache/web/"))
        for state in ("new", "changed", "gone"):
            for look, v in diff[state].items():
                was, now = (None, v) if state == "new" else (v[0], v[1]) if state == "changed" else (v, None)
                find = finder(look, now if state != "gone" else was)
                if find["how"] == "image":
                    find["files"] = {"before": os.path.join(tree, find["path"]) if tree else None, "after": os.path.join(ROOT, find["path"])}
                    find["files"] = {k: p if p and os.path.exists(p) else None for k, p in find["files"].items()}
                    if tree and not find["files"]["before"]:
                        was_file = stands_in(find["path"], tree)
                        if was_file:   # a new image (theatre-light.svg): Before, the one it's a variant of (theatre.svg)
                            find["files"]["before"] = os.path.join(tree, was_file)
                            find["instead"] = was_file
                if find["how"] == "pages":
                    find["judge"] = judged_by(look, images)
                elif find["how"] in ("selector", "style", "animation"):
                    find["judge"] = cropped_by(look, new=state == "new")
                items.append({"id": len(items), "state": state, "look": look, "was": was, "now": now, "find": find,
                              "side": "before" if state == "gone" and tree else "after"})
        fp = fingerprint(diff)
        source = before or ("the approved files in git (dev/style-guide.json)" if src else None)
        told = {"items": items, "shots": {}, "done": set(), "off": off, "compared": bool(src), "source": source,
                "fingerprint": fp, "folder": out}
        if live:
            live(dict(told))

        def partial(shots, done):
            if live:
                live(dict(told, shots=shots, done=done))
        demos = [_demo(ROOT)] + ([_demo(tree, before=True)] if tree else [])
        try:
            steps.show(f"starting the demo{'s' if len(demos) != 1 else ''} (tests/demo.py)", ticking=True)
            if not all(_answers(url) for _p, url in demos):   # (each given a minute)
                steps.clear()
                print("The demo didn't start within a minute. To see why: python3 tests/demo.py", file=sys.stderr)
                return 1
            steps.draw("demos answering")
            steps.show("opening the pages (Playwright, Chromium)", ticking=True)
            plan = {"looks": [{"theme": t, "mode": m} for t, m in LOOKS], "pages": app_pages(), "fixed": FIXED_PAGES,
                    "items": items, "after": demos[0][1], "before": demos[1][1] if tree else None,
                    "time": int(time.time() * 1000)}   # (both sides see the same moment: "3 min ago" reads alike)
            with open(os.path.join(out, "plan.json"), "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False)
            pool: list = []
            shots = _pictures(out, steps, pool, partial, stop)
            if shots is None:
                return 1
        finally:
            for proc, _url in demos:
                proc.terminate()
                proc.wait(10)
    # a look that drew exactly the same Before and After, in every theme and mode on every page that draws it, changes
    # nothing anyone sees: it goes into the guide here, with no approval asked (the maintainer's rule); what changed what's
    # drawn, or isn't drawn on the demo pages, still waits for the review
    if src:
        accepted = accept_same(items, shots, looks())
        if accepted:
            print(f"  {len(accepted)} look{'s' if len(accepted) != 1 else ''} drew exactly the same before and after: "
                  "into the style guide, no approval needed.")
    counts = _page(out, items, shots, fp, off, bool(src), source)
    # (the pictures of what differs only: a pair the same before and after isn't shown, so isn't kept)
    files = {p for ps in shots.values() for pair in ps if not (src and pair.get("same"))
             for p in (pair.get("before"), pair.get("after"), pair.get("diff")) if p}
    for f in os.listdir(out):
        if f.endswith(".png") and f not in files:
            os.remove(os.path.join(out, f))
    with open(os.path.join(out, "sample.json"), "w", encoding="utf-8") as f:
        json.dump({"fingerprint": fp, "made_from": inputs, "made": datetime.datetime.now().isoformat(timespec="seconds"), "looks": count,
                   "workers": pool[0] if pool else None, "took": round(time.time() - started),
                   "seen": counts["seen"], "code": counts["code"], "same": counts["same"], "shots": sorted(files),
                   "items": [{k: it[k] for k in ("id", "state", "look", "was", "now")} for it in items]}, f, indent=1)
    os.remove(os.path.join(out, "plan.json"))
    if feeding() and pool:   # (on callboard's board: its block's bar, full, says how the sample was made)
        steps.workers = pool[0]
        steps.finished = f"{count} look{'s' if count != 1 else ''} · sample made with {pool[0]} worker{'s' if pool[0] != 1 else ''}"
        steps.clear()
    where = os.path.relpath(os.path.join(shown, "index.html"), os.getcwd())
    print(f"The sample of {count} look{'s' if count != 1 else ''}: {where}")
    if src:
        print(f"  {counts['seen']} changed what's drawn, {counts['code']} aren't drawn on the demo pages (judge by the code), "
              f"{counts['same']} drew exactly the same before and after.")
    else:
        print("  No Before to compare with (approve keeps the approved files in git; or give one: sample --before <folder>).")
    print(f"For the maintainer to see and approve them: {REVIEW}")
    return 0


def _no_playwright() -> str:
    """Why node can't load Playwright (dev/style-sample.js needs it), quickly, before the demos are started; "" when it can."""
    try:
        got = subprocess.run(["node", "-e", "require.resolve('playwright')"], cwd=ROOT, env=dict(os.environ, NODE_PATH=_node_path()),
                             capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return "no node"
    except subprocess.TimeoutExpired:
        return "node didn't answer"
    return "" if got.returncode == 0 else "node can't find it"


def _pictures(out: str, waiting: Progress | None = None, workers: list | None = None, partial=None, stop=None):
    """dev/style-sample.js's pictures, with the dev tools' progress bar (on ``waiting``'s, the steps before them: the
    pages added to its count once it knows how many, one bar filled once): {item id: [pair, ...]}, or None.
    ``partial(shots, done)`` is told each time more looks have all their pictures (review shows them as they come);
    ``stop`` set (review's page closed while it's made) stops it: None."""
    node = subprocess.Popen(["node", os.path.join(ROOT, "dev", "style-sample.js"), out],
                            cwd=ROOT, env=dict(os.environ, NODE_PATH=_node_path()), stdout=subprocess.PIPE, text=True)
    assert node.stdout
    bar, shots, before = None, {}, 0
    for line in node.stdout:
        if stop is not None and stop.is_set():
            node.terminate()
            break
        msg = json.loads(line)
        if msg.get("partial") is not None:
            if partial:
                partial(msg["partial"]["shots"], {int(i) for i in msg["partial"]["done"]})
        elif msg.get("total") is not None:   # (it says again once it knows which pages it needs in each look)
            if bar:
                bar.total = before + msg["total"]
            else:
                if waiting is not None:   # (the pages on the steps' count: one bar)
                    waiting.draw("pages opened")
                    before, bar = waiting.total, waiting
                    bar.more(msg["total"])
                else:
                    bar = Progress(msg["total"], unit="pages looked at")
                bar.workers = msg.get("workers")   # (the browser's pages at once: style-sample.js's pool)
                if workers is not None and isinstance(msg.get("workers"), int):
                    workers.append(msg["workers"])   # (kept with the sample: how it was made)
                bar.finished = "pages · photographed for the sample"
        elif msg.get("page") and bar:
            bar.draw(msg["page"])
        elif msg.get("shots") is not None:
            shots = msg["shots"]
    code = node.wait()
    for b in {id(b): b for b in (bar, waiting) if b}.values():
        b.clear()
    if stop is not None and stop.is_set():
        return None
    if code:
        print("Playwright couldn't take the pictures (see above). It needs: npm ci --prefix dev && npx --prefix dev playwright install chromium",
              file=sys.stderr)
        return None
    return shots


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def sort_looks(items: list, shots: dict, compared: bool) -> dict:
    """The looks by what the maintainer has to do with them: {"seen": [group], "code": [item], "same": [item]}.
    seen: drawn otherwise somewhere (or, with no Before, drawn at all), each group the looks that changed the same
    pictures, with those pictures; code: drawn on no demo page; same: drawn, and the same before and after everywhere."""
    groups, code, same = {}, [], []
    for it in items:
        pairs = shots.get(str(it["id"]), [])
        if not pairs:
            code.append(it)
            continue
        differ = [p for p in pairs if not (compared and p.get("same"))]
        if not differ:   # (a picture that couldn't show what it was for — a state not put on, nothing moving — isn't "the same")
            (same if all(p.get("caught") is not False for p in pairs) else code).append(it)
            continue
        key = tuple((p["label"], p.get("before"), p.get("after")) for p in differ)
        g = groups.setdefault(key, {"pairs": differ, "items": [], "alike": []})
        g["items"].append(it)
        g["alike"] = sorted({p["label"] for p in pairs if compared and p.get("same")} | set(g["alike"]))
    return {"seen": one_spot(list(groups.values())), "code": code, "same": same}


def _overlap(a: dict | None, b: dict | None) -> float:
    """How much two crops of a page cover the same spot: their overlap over the smaller one (0 to 1)."""
    if not a or not b:
        return 0.0
    w = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h / max(1.0, min(a["width"] * a["height"], b["width"] * b["height"]))


def one_spot(groups: list, enough: float = 0.5) -> list:
    """The groups of looks photographed at the same spot (one change made of several rules: a vine's position, size and
    colour) on one card: two groups whose crops on every page they share cover mostly the same place. A card shows, on
    each page, the biggest of its crops there (it holds the others), and lists every look."""
    def spots(g):
        return {p["label"]: p.get("clip") for p in g["pairs"]}
    parent = list(range(len(groups)))

    def top(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            a, b = spots(groups[i]), spots(groups[j])
            shared = set(a) & set(b)
            if shared and all(_overlap(a[k], b[k]) >= enough for k in shared):
                parent[top(j)] = top(i)
    merged: dict[int, list] = {}
    for i in range(len(groups)):
        merged.setdefault(top(i), []).append(groups[i])
    out = []
    for gs in merged.values():
        if len(gs) == 1:
            out.append(gs[0])
            continue
        best: dict[str, dict] = {}
        for g in gs:
            for p in g["pairs"]:
                c = p.get("clip") or {}
                if p["label"] not in best or c.get("width", 0) * c.get("height", 0) > \
                        (best[p["label"]].get("clip") or {}).get("width", 0) * (best[p["label"]].get("clip") or {}).get("height", 0):
                    best[p["label"]] = p
        order = [p["label"] for g in gs for p in g["pairs"]]
        out.append({"pairs": [best[k] for k in dict.fromkeys(order)], "items": [it for g in gs for it in g["items"]],
                    "alike": sorted({a for g in gs for a in g["alike"]})})
    return out


_LOOK_OF = {"new": "New", "changed": "Changed", "gone": "Gone"}


def _ids(items) -> str:
    return ",".join(str(it["id"]) for it in items)


def _ok(items, text: str = "Approve") -> str:
    """The button that approves these looks: shown only when the page is served by dev/style.py review."""
    return f'<button type="button" class="ok" data-ids="{_ids(items)}" hidden>{_esc(text)}</button>'


_REVIEW_BAR = ('<div class="bar"><p class="offline">To approve on this page, run <code>python3 dev/style.py review</code> at a '
               'terminal: it opens this page with Approve buttons. An approved change leaves the page.</p>'
               '<p class="online" hidden><span class="tally"><span class="pill good"></span><span class="pill left"></span></span>'
               '<button type="button" class="ok all">Approve all</button> <span class="msg" role="status"></span></p>'
               '<div class="alldone" hidden role="status"><b></b><span>Commit <code>dev/style-guide.json</code> with the '
               'files as they were approved.</span></div></div>')


def _swatch(v) -> str:
    # (its colour by a rule of the page's own <style>, _swatches': the page has no style="...", so its policy needs none)
    return f'<span class="sw" data-c="{_esc(v)}"></span>' if v and re.fullmatch(r"#[0-9A-Fa-f]{3,8}", str(v)) else ""


def _swatches(html: str) -> str:
    """The rules that colour the page's swatches (_swatch), one for each colour on it."""
    return "".join(f'.sw[data-c="{c}"]{{background:{c}}}' for c in sorted(set(re.findall(r'<span class="sw" data-c="(#[0-9A-Fa-f]{3,8})">', html))))


def _code(it) -> str:
    """A look's code, Before and After side by side (a theme colour with its swatch)."""
    def side(name, v):
        text = "(not there)" if v is None else (v or "(as named)")
        sw = _swatch(v) if it["look"].startswith("theme ") else ""
        return f'<div><div class="side{" after" if name == "After" else ""}">{name}</div>{sw}<pre>{_esc(text)}</pre></div>'
    return f'<div class="code">{side("Before", it["was"])}{side("After", it["now"])}</div>'


def _name(it) -> str:
    return f'<span class="tag {it["state"]}">{_LOOK_OF[it["state"]]}</span> <code>{_esc(_short(it["look"], 200))}</code>'


def _page(out: str, items: list, shots: dict, fp: str, off: dict | None, compared: bool, source: str | None) -> dict:
    """The review page (page_html), written to ``out``/index.html: how many looks are seen, judged by code, the same."""
    html, counts = page_html(items, shots, fp, off, compared, source)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    return counts


def _section(tag: str, cls: str, ids: str, inner: str) -> str:
    """A change on the review page: data-v, what it shows (all but its number), so a page drawn again while the sample is
    made keeps each change that's the same as it was, as the maintainer has it (its view, its slider: the page's merge)."""
    v = hashlib.sha256(re.sub(r'<span class="num">\d+</span>', "", inner).encode()).hexdigest()[:12]
    return f'<{tag} class="{cls}" data-ids="{ids}" data-v="{v}">{inner}</{tag}>'


def page_html(items: list, shots: dict, fp: str, off: dict | None, compared: bool, source: str | None,
              making: str | None = None, total: int | None = None, stuck: bool = False) -> tuple[str, dict]:
    """The review page: what changed on screen, Before and After side by side; then what can only be judged by its
    code; then, folded away, what drew the same. While the sample is made (review), ``items`` are the looks with all
    their pictures so far, of ``total``, and ``making`` says how it's going (``stuck``: it couldn't be made)."""
    s = sort_looks(items, shots, compared)
    seen_looks = sum(len(g["items"]) for g in s["seen"])
    parts = []
    for n, g in enumerate(s["seen"], 1):
        title = (_name(g["items"][0]) if len(g["items"]) == 1 else
                 f'{len(g["items"])} looks seen across whole pages (decorations, theme colours, fonts)'
                 if all(it["find"]["how"] == "pages" for it in g["items"]) else
                 f'{len(g["items"])} looks that change the same pictures')
        rows = []
        for p in g["pairs"]:
            def img(f, what):
                if not f:
                    return f'<div class="nopic">{"Not in the app before" if what == "Before" else "Gone from the app now"}</div>'
                return f'<a href="{_esc(f)}" target="_blank"><img src="{_esc(f)}" alt="{what}" loading="lazy"></a>'
            both = p.get("before") and p.get("after")
            # one view at a time, picked on a toggle: Side by side, Diff (the After with what differs marked, the rest
            # faded, boxed), Slider (Before over After, a line dragged across), Switch (Before and After full width in
            # turn, one in the other's place: on its own every 2 s, held while the pointer's on it, a click switches) —
            # each only where it can be drawn
            views = [("side", "Side by side")] + ([("diff", "Diff")] if p.get("diff") else []) + \
                ([("slider", "Slider"), ("switch", "Switch")] if both else [])
            tools = ('<span class="views" role="group" aria-label="Show">' + "".join(
                f'<button type="button" class="vw" data-view="{v}" aria-pressed="{"true" if v == "side" else "false"}">{t}</button>'
                for v, t in views) + "</span>") if len(views) > 1 else ""
            diffview = (f'<div class="diffview" hidden><figure><figcaption class="side diff">Diff</figcaption>'
                        # (not lazy: hidden until Diff is pressed, a lazy one never loads)
                        f'<a href="{_esc(p["diff"])}" target="_blank"><img src="{_esc(p["diff"])}" alt="Diff"></a></figure></div>') if p.get("diff") else ""
            slider = (f'<div class="slider" hidden><div class="sl" tabindex="0" role="slider" aria-label="Before ← → After (drag, or the arrow keys)" '
                      f'aria-valuemin="0" aria-valuemax="100" aria-valuenow="50"><img src="{_esc(p["after"])}" alt="After">'
                      # (not lazy: hidden until Slider is pressed, a lazy one never loads, and these are the side by side's own files)
                      f'<img class="slb" src="{_esc(p["before"])}" alt="Before">'
                      '<span class="sll side">Before</span><span class="slr side after">After</span><span class="slx"></span></div></div>') if both else ""
            switch = (f'<div class="switch" hidden><div class="flp" tabindex="0" role="button" data-on="before" '
                      'aria-label="Before and After in turn (point at it to stop; click, Enter or Space to switch)">'
                      # (not lazy, as the slider's; both in one spot, the one not showing kept invisible so nothing moves)
                      f'<img class="swb" src="{_esc(p["before"])}" alt="Before"><img class="swa" src="{_esc(p["after"])}" alt="After">'
                      '<span class="swl side">Before</span><span class="swh">Click to switch</span></div></div>') if both else ""
            px = (f'<span class="px">{p["px"]:,} px differ, in {p["areas"]} area{"s" if p.get("areas") != 1 else ""} '
                  '(boxed in Diff)</span>') if p.get("px") else ""
            # (the crops couldn't be put round the look at the same spot on both sides: dev/style-sample.js shoot)
            px += ('<span class="px warn">Not lined up: the look isn\'t at the same spot in Before and After, so the '
                   'difference may be only where it was cropped</span>') if p.get("aligned") is False else ""
            rows.append(f'<div class="pair"><div class="where">{_esc(p["label"])}{px}{tools}</div>'
                        f'<div class="ba" title="Click to swap Before and After (Cmd- or Ctrl-click a picture to open it)">'
                        f'<span class="bah">Click to swap</span><figure><figcaption class="side">Before</figcaption>{img(p.get("before"), "Before")}</figure>'
                        f'<figure><figcaption class="side after">After</figcaption>{img(p.get("after"), "After")}</figure></div>'
                        f'{diffview}{slider}{switch}</div>')
        alike = f'<p class="alike">The same before and after in: {_esc(", ".join(g["alike"]))}.</p>' if g["alike"] else ""
        looks_list = "".join(f'<details class="look"><summary>{_name(it)}</summary>{_code(it)}</details>' for it in g["items"])
        parts.append(_section("section", "change", _ids(g["items"]), f'<h3><span class="num">{n}</span>{title}{_ok(g["items"])}</h3>{"".join(rows)}{alike}'
                              f'<details class="why"><summary>What changed in the code ({len(g["items"])})</summary>{looks_list}</details>'
                              # (its Approve again at its foot: once its pictures are looked over, no scrolling back up)
                              f'<div class="okfoot">{_ok(g["items"])}</div>'))
    code = "".join(_section("section", "change", str(it["id"]), f'<h3>{_name(it)}{_ok([it])}</h3>{_code(it)}') for it in s["code"])
    same = "".join(_section("section", "change", str(it["id"]), f'<h3>{_name(it)} <span class="px">No visible change</span>{_ok([it])}</h3>'
                            f'<details class="why"><summary>What changed in the code</summary>{_code(it)}</details>') for it in s["same"])
    note = ""
    if not compared:
        note = ('<p class="warn">There\'s no Before to compare with, so this shows each look as it is now. approve keeps '
                'the approved app\'s files in git for next time; for now one can be given with '
                '<code>python3 dev/style.py sample --before &lt;folder&gt;</code>.</p>')
    elif off and any(off.values()):
        k = sum(len(v) for v in off.values())
        listing = "".join(f"<li>{_esc(st)}: <code>{_esc(_short(x, 160))}</code></li>" for st in off for x in off[st])
        drawn = sum(1 for st in off for x in off[st] if x.startswith("mark decor.js "))
        if drawn == k and any(x.startswith("mark decor.js draws | ") for x in off["new"]):
            why = (f"The Before matches the style guide in every look but decor.js's {k}: the guide still has decor.js "
                   "by its code, and it is now judged by what it draws, so those two can't be lined up until this approval.")
        else:
            why = (f"The Before ({_esc(source)}) differs from the style guide in {k} look{'s' if k != 1 else ''}, so those "
                   "may not show exactly as approved.")
        note = f'<details class="warn"><summary>{why}</summary><ul>{listing}</ul></details>'
    total = len(items) if total is None else total
    n_pics = sum(len(g["pairs"]) for g in s["seen"])
    pics = f" ({n_pics} picture{'s' if n_pics != 1 else ''} to compare)" if n_pics else ""
    summary = (f'<div class="sum"><div><b>{seen_looks}</b> look{"s" if seen_looks != 1 else ""} changed what you see'
               f'{pics}</div>'
               f'<div><b>{len(s["code"])}</b> aren\'t drawn on the demo pages: judge by the code</div>'
               f'<div><b>{len(s["same"])}</b> drew exactly the same before and after</div></div>') if compared else ""
    body = ("<h1>Wardrobe</h1>"
            f"<p class=\"lead\">{total} look{'s' if total != 1 else ''} differ from the style guide. Each change is shown "
            "<b>Before</b> (as approved) and <b>After</b> (now), side by side, in Classic and Dionysus, light and dark, "
            "only where it looks different.</p>" +
            (f'<p class="making{" stuck" if stuck else ""}" role="status">{_esc(making)}</p>' if making else "") + summary + note +
            _REVIEW_BAR.replace('<div class="bar">', f'<div class="bar" data-all="{total}">', 1) +
            (f"<div class=\"part\"><h2>Changed on screen</h2>{''.join(parts)}</div>" if parts else
             ("<h2>Changed on screen</h2><p class=\"none\">Nothing on the demo pages looks different.</p>" if compared and not making else "")) +
            (f"<div class=\"part\"><h2>Not drawn on the demo pages</h2><p class=\"none\">These only show in a state the demo doesn't open in "
             f"(a hover, a print, an animation). Before and After are their code.</p>{code}</div>" if code else "") +
            ("<div class=\"part\"><h2>No visible change</h2>" + _section("div", "change bare", _ids(s['same']),
             f"{_ok(s['same'], 'Approve these ' + str(len(s['same'])))}<details class=\"same\"><summary>{len(s['same'])} look{'s' if len(s['same']) != 1 else ''} "
             "whose code changed but which drew exactly the same, pixel for pixel, everywhere they're seen: nothing to "
             f"look at, so no pictures.</summary>{same}</details>") + "</div>" if same else "") +
            f"<p class=\"foot\">To approve them, the maintainer runs <code>{REVIEW}</code> at a terminal. "
            f"Fingerprint {fp}</p>")
    html = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>Wardrobe</title><style>" + _CSS + _swatches(body) + "</style></head><body><main>" + body +
            "</main><script>" + _JS + "</script></body></html>")
    return html, {"seen": seen_looks, "code": len(s["code"]), "same": len(s["same"])}


_CSS = (devpage.THEME + devpage.BASE +   # (the dev tools' pages' colours: callboard/devpage.py)
        "main{max-width:1500px;margin:0 auto;padding:20px 16px 40px}h1{margin:0 0 6px;font-size:26px}"
        "h2{font-size:19px;margin:28px 0 10px}.lead{margin:0 0 14px;max-width:80ch}"
        ".sum{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 14px}.sum div{background:var(--card);border:1px solid var(--line);"
        "border-radius:10px;padding:8px 14px}.sum b{font-size:18px;margin-right:4px}"
        ".change{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:0 0 16px}"
        "h3{font-size:15px;margin:0 0 10px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;word-break:break-word}"
        ".num{display:inline-grid;place-items:center;min-width:26px;height:26px;border-radius:99px;background:var(--ink);"
        "color:var(--card);font-size:13px}"
        ".pair{margin:0 0 14px}.where{font-weight:600;font-size:13px;color:var(--mute);margin:0 0 6px;display:flex;gap:10px;"
        "align-items:center;flex-wrap:wrap}.ba{position:relative;display:grid;grid-template-columns:1fr 1fr;gap:12px;cursor:pointer}"
        ".ba.swapped figure:first-of-type{order:2}"
        ".bah{position:absolute;top:0;right:0;z-index:1;font-size:12px;background:var(--ink);color:var(--card);border-radius:99px;"
        "padding:2px 10px;opacity:0;transition:opacity .15s;pointer-events:none}.ba:hover .bah{opacity:1}"
        "@media (max-width:760px){.ba{grid-template-columns:1fr}}figure{margin:0;min-width:0}"
        "figure img{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:6px;background:#fff}"
        ".side{display:inline-block;font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;"
        "padding:2px 10px;border-radius:99px;margin:0 0 5px;background:var(--before-bg);color:var(--before)}"
        ".side.after{background:var(--after-bg);color:var(--after)}"
        ".nopic{border:1px dashed var(--line);border-radius:6px;padding:24px;color:var(--mute);text-align:center;font-style:italic}"
        ".px{font-size:12px;color:var(--mute);font-weight:400;margin:0 8px}"
        ".px.warn{color:var(--gone);font-weight:600}"
        ".views{display:inline-flex;border:1px solid var(--line);border-radius:99px;overflow:hidden}"
        ".vw{font:inherit;font-size:12px;border:0;border-left:1px solid var(--line);background:var(--code);color:var(--ink);"
        "padding:2px 12px;cursor:pointer}.vw:first-child{border-left:0}.vw[aria-pressed=true]{background:var(--ink);color:var(--card)}"
        ".side.diff{background:var(--code);color:var(--ink)}"
        ".sl{position:relative;border:1px solid var(--line);border-radius:6px;overflow:hidden;background:#fff;cursor:ew-resize;"
        "touch-action:pan-y;user-select:none;-webkit-user-select:none}"
        ".sl img{display:block;width:100%;height:auto;pointer-events:none}.sl .slb{position:absolute;inset:0;clip-path:inset(0 50% 0 0)}"
        ".slx{position:absolute;top:0;bottom:0;left:50%;width:2px;margin-left:-1px;background:var(--ink);box-shadow:0 0 0 1px var(--card);pointer-events:none}"
        ".sll,.slr{position:absolute;top:8px;margin:0;pointer-events:none}.sll{left:8px}.slr{right:8px}"
        ".flp{position:relative;display:grid;grid-template-columns:minmax(0,1fr);border:1px solid var(--line);border-radius:6px;overflow:hidden;background:#fff;cursor:pointer}"
        ".flp img{grid-area:1/1;display:block;width:100%;height:auto;pointer-events:none}"
        ".flp[data-on=before] .swa,.flp[data-on=after] .swb{visibility:hidden}"
        ".swl{position:absolute;top:8px;left:8px;margin:0;pointer-events:none}"
        ".swh{position:absolute;top:8px;right:8px;font-size:12px;background:var(--ink);color:var(--card);border-radius:99px;"
        "padding:2px 10px;opacity:0;transition:opacity .15s;pointer-events:none}"
        ".flp:hover .swh,.flp:focus-visible .swh,.flp.still .swh{opacity:1}"
        ".stalenote{background:var(--before-bg);color:var(--ink);border-radius:8px;padding:8px 12px;margin:0 0 10px}"
        ".ok{font:inherit;font-size:13px;font-weight:600;border:0;border-radius:99px;padding:4px 14px;cursor:pointer;"
        "background:var(--after);color:var(--card);margin-left:auto}.ok:disabled{opacity:.5}.ok.all{margin:0}"
        ".tally{display:inline-flex;gap:6px;margin-right:10px;vertical-align:middle}"
        ".pill{font-size:13px;font-weight:700;border-radius:99px;padding:4px 12px;color:var(--card)}"
        ".pill.good{background:var(--after)}.pill.left{background:var(--before)}"
        ".alldone{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;background:var(--after);color:var(--card);"
        "border-radius:12px;padding:16px 20px;font-size:16px}.alldone b{font-size:20px}.alldone code{color:inherit}"
        ".bar{margin:0 0 14px}.bar p{margin:0}.offline{color:var(--mute);font-size:13px}.msg{color:var(--mute);margin-left:10px}"
        ".bare{background:none;border:0;padding:0}.bare>.ok{float:right}"
        ".alike{color:var(--mute);font-size:13px;margin:0 0 8px}"
        "details{margin:6px 0}summary{cursor:pointer}.why>summary,.same>summary{color:var(--mute)}"
        ".look{margin:6px 0 6px 14px}.look>summary{word-break:break-word}"
        ".code{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:6px 0}@media (max-width:760px){.code{grid-template-columns:1fr}}"
        ".code>div{min-width:0}pre{white-space:pre-wrap;word-break:break-all;background:var(--code);padding:8px 10px;"
        "border-radius:6px;font-size:12.5px;margin:0}"
        ".tag{font-size:11px;font-weight:700;text-transform:uppercase;padding:2px 8px;border-radius:99px;background:var(--after-bg);"
        "color:var(--after)}.tag.changed{background:var(--before-bg);color:var(--before)}.tag.gone{background:var(--gone-bg);color:var(--gone)}"
        "code{font-size:13px;word-break:break-word}.sw{display:inline-block;width:28px;height:28px;border-radius:6px;"
        "border:1px solid var(--mute);vertical-align:middle;margin:0 8px 5px}"
        ".none{color:var(--mute)}.warn{background:var(--before-bg);color:var(--ink);border-radius:10px;padding:10px 14px}"
        ".foot{color:var(--mute);font-size:13px;margin-top:28px;word-break:break-all}"
        # (the sample on its way, review's page open while it's made: what's ready so far, and a spinner — still, with
        # reduced motion asked for; red once it couldn't be made)
        ".making{background:var(--after-bg);color:var(--ink);border-radius:10px;padding:10px 14px;font-weight:600;max-width:80ch}"
        ".making:not(.stuck)::before{content:\"\";display:inline-block;width:12px;height:12px;margin-right:9px;vertical-align:-1px;"
        "border:2px solid var(--after);border-right-color:transparent;border-radius:50%;animation:making 1s linear infinite}"
        ".okfoot{display:flex;justify-content:flex-end;margin-top:12px}"
        ".making.stuck{background:var(--gone-bg);color:var(--gone)}@keyframes making{to{transform:rotate(360deg)}}"
        "@media (prefers-reduced-motion:reduce){.making::before{animation:none}}")

_JS = ("var R=window.REVIEW;if(R){document.querySelectorAll('.ok,.online').forEach(function(x){x.hidden=false});"
       "document.querySelector('.offline').hidden=true;}"
       # (the tally: green, the looks approved; orange, the ones left; and once none are left, all of it says so in green)
       "function idsOf(cs){var s={};[].forEach.call(cs,function(c){c.dataset.ids.split(',').filter(Boolean).forEach(function(i){s[i]=1})});return s;}"
       "var ALL=Object.keys(idsOf(document.querySelectorAll('.change[data-ids]'))).length,DONE={};"
       # (every look, the bar says: those still on their way too, while the sample's made; the ones approved here, DONE)
       "function left(){if(!R)return;var b=document.querySelector('.bar[data-all]');if(b)ALL=Number(b.dataset.all);"
       "var n=Math.max(0,ALL-Object.keys(DONE).length);"
       "document.querySelector('.pill.good').textContent=(ALL-n)+' approved';"
       "var l=document.querySelector('.pill.left');l.textContent=n+' remaining';l.hidden=!n;"
       "document.querySelectorAll('.part').forEach(function(p){p.hidden=!p.querySelector('.change[data-ids]')});"
       "if(!n&&!document.querySelector('.making')){document.querySelector('.alldone b').textContent='All '+ALL+' looks approved.';"
       "document.querySelector('.alldone').hidden=false;"
       "document.querySelectorAll('.online,.lead,.sum,.warn,.foot').forEach(function(x){x.hidden=true});}}"
       "left();"
       "function gone(done){document.querySelectorAll('.change[data-ids]').forEach(function(c){"
       "if(c.dataset.ids.split(',').filter(Boolean).every(function(i){return done[i]}))c.remove();});}"
       "if(R&&R.done&&R.done.length){R.done.forEach(function(i){DONE[i]=1});gone(DONE);left();}"
       "document.addEventListener('click',function(e){var a=e.target.closest('.ok');if(!a||!R)return;"
       "var ids=a.classList.contains('all')?[].concat.apply([],[].map.call(document.querySelectorAll('.change[data-ids]'),"
       "function(c){return c.dataset.ids.split(',')})):a.dataset.ids.split(',');"
       "ids=ids.filter(Boolean).map(Number);a.disabled=true;var msg=document.querySelector('.msg');msg.textContent='Approving…';"
       "fetch('/approve',{method:'POST',headers:{'Content-Type':'application/json','X-Page-Token':R.token},"
       "body:JSON.stringify({ids:ids})}).then(function(r){return r.json()}).then(function(d){"
       "var done={};d.approved.forEach(function(i){done[i]=1;DONE[i]=1});"
       "gone(done);"
       "var bad=Object.keys(d.refused||{});"
       # (a change whose code moved on since the sample: said on it, its Approve off, until a new sample)
       "document.querySelectorAll('.change[data-ids]').forEach(function(c){"
       "if(c.dataset.ids.split(',').some(function(i){return d.refused&&d.refused[i]})){"
       "c.classList.add('stale');c.querySelectorAll('.ok').forEach(function(k){k.disabled=true;k.textContent='Changed since this sample';});"
       "if(!c.querySelector('.stalenote')){var n=document.createElement('p');n.className='stalenote';"
       "n.textContent='Its code has changed since this sample was made, so what you see here is not what it looks like now. '"
       "+'To see it as it is now, close this page and run: python3 dev/style.py review';c.insertBefore(n,c.children[1]||null);}}});"
       "var bad=Object.keys(d.refused||{});msg.textContent=d.approved.length+' approved'+(bad.length?'; '+bad.length+"
       "' not, because their code changed since this sample. To make it again: python3 dev/style.py review':'');"
       "if(!a.closest('.stale'))a.disabled=false;left();}).catch(function(){msg.textContent='The review server didn\\'t answer: is python3 dev/style.py review still running?';"
       "a.disabled=false;});});"
       # (a change's buttons act on every theme of it at once: all its pairs)
       # (the looks with no visible change: listed open, each with its code)
       "document.querySelectorAll('details.same').forEach(function(d){d.open=true;});"
       "function pairs(x){var c=x.closest('.change');return c?[].slice.call(c.querySelectorAll('.pair')):[x.closest('.pair')];}"
       # (every control of a pair's views — the view picked, the slider's line, Switch's side, and waiting on their
       # pictures — does what it does to each of its change's pairs through this one, never to the one clicked alone:
       # every theme under the change's Approve button moves together. StyleGuideTests holds every control to it)
       "function allThemes(x,fn){pairs(x).forEach(fn);}"
       # (the toggle: one view of each pair — Side by side, Diff or Slider; a pair without that view keeps side by side)
       # (the button clicked stays where it was on the screen: the other themes' pairs above it change height with it,
       # and so do pictures still loading, so the page is scrolled to keep it put, and again as each of them loads)
       "function hold(b,y){var until=Date.now()+3000,fix=function(){if(Date.now()>until)return;"
       "var d=b.getBoundingClientRect().top-y;if(Math.abs(d)>=0.5)window.scrollBy(0,d);};fix();"
       "allThemes(b,function(pair){pair.querySelectorAll('img').forEach(function(i){if(!i.complete)i.addEventListener('load',fix,{once:true});});});}"
       "document.addEventListener('click',function(e){var b=e.target.closest('.vw');if(!b)return;var v=b.dataset.view,y=b.getBoundingClientRect().top;"
       "allThemes(b,function(pair){var has=pair.querySelector('.vw[data-view=\"'+v+'\"]'),w=has?v:'side';"
       "pair.querySelectorAll('.vw').forEach(function(k){k.setAttribute('aria-pressed',k.dataset.view===w?'true':'false')});"
       "pair.querySelector('.ba').hidden=w!=='side';"
       "var d=pair.querySelector('.diffview');if(d)d.hidden=w!=='diff';"
       "var s=pair.querySelector('.slider');if(s)s.hidden=w!=='slider';"
       "var sw=pair.querySelector('.switch');if(sw)sw.hidden=w!=='switch';});hold(b,y);});"
       # (Switch: Before and After in turn every 2 s, but not a change the pointer or the keyboard is on, which a click,
       # Enter or Space switches; with reduced motion asked for, none switches on its own: each says click to switch.
       # Like every view's controls, it acts on all of its change's pairs at once, every theme under its Approve button)
       "function flip(x){var on=x.dataset.on==='before'?'after':'before';"
       "allThemes(x,function(pair){var sw=pair.querySelector('.flp');if(!sw)return;var l=sw.querySelector('.swl');sw.dataset.on=on;"
       "l.textContent=on==='before'?'Before':'After';l.className='swl side'+(on==='after'?' after':'');});}"
       "var still=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;"
       "if(still)document.querySelectorAll('.flp').forEach(function(sw){sw.classList.add('still');});"
       "else setInterval(function(){var done=[];document.querySelectorAll('.switch:not([hidden]) .flp').forEach(function(sw){"
       "var c=sw.closest('.change')||sw,busy=false;if(done.indexOf(c)>=0)return;done.push(c);"
       "allThemes(sw,function(pair){var f=pair.querySelector('.flp');if(f&&(f.matches(':hover')||f.matches(':focus-visible')))busy=true;});"
       "if(!busy)flip(sw);});},2000);"
       "document.addEventListener('click',function(e){var sw=e.target.closest('.flp');if(sw)flip(sw);});"
       # (Side by side: a click swaps Before and After round, on every theme of the change; Cmd-, Ctrl- or Shift-click,
       # or the middle button, still opens the picture)
       "function swap(x){var on=!x.classList.contains('swapped');allThemes(x,function(pair){pair.querySelector('.ba').classList.toggle('swapped',on);});}"
       "document.addEventListener('click',function(e){var ba=e.target.closest('.ba');if(!ba||e.metaKey||e.ctrlKey||e.shiftKey||e.button)return;"
       "e.preventDefault();swap(ba);});"
       "document.addEventListener('keydown',function(e){var sw=e.target.closest&&e.target.closest('.flp');"
       "if(!sw||(e.key!=='Enter'&&e.key!==' '))return;e.preventDefault();flip(sw);});"
       "function slide(box,v){allThemes(box,function(pair){var b=pair.querySelector('.slider');if(!b)return;"
       "var sl=b.querySelector('.sl');sl.setAttribute('aria-valuenow',Math.round(v));"
       "sl.querySelector('.slb').style.clipPath='inset(0 '+(100-v)+'% 0 0)';sl.querySelector('.slx').style.left=v+'%';});}"
       "document.addEventListener('keydown',function(e){var sl=e.target.closest&&e.target.closest('.sl');if(!sl)return;"
       "var d={ArrowLeft:-5,ArrowRight:5,Home:-100,End:100}[e.key];if(d===undefined)return;e.preventDefault();"
       "slide(sl.parentNode,Math.max(0,Math.min(100,Number(sl.getAttribute('aria-valuenow'))+d)));});"
       # (a click or a drag on the picture moves the line there too)
       "function at(sl,e){var b=sl.getBoundingClientRect();slide(sl.parentNode,Math.max(0,Math.min(100,(e.clientX-b.left)/b.width*100)));}"
       "document.addEventListener('pointerdown',function(e){var sl=e.target.closest('.sl');if(!sl||e.button)return;"
       "e.preventDefault();sl.setPointerCapture(e.pointerId);sl.dataset.drag='1';at(sl,e);});"
       "document.addEventListener('pointermove',function(e){var sl=e.target.closest('.sl');if(sl&&sl.dataset.drag)at(sl,e);});"
       "['pointerup','pointercancel'].forEach(function(n){document.addEventListener(n,function(e){var sl=e.target.closest('.sl');"
       "if(sl)delete sl.dataset.drag;});});"
       # (while the sample's made — review, with make — the page asks /live how it's going, and once there's more it
       # draws itself again from the page as it is now: every change that's the same as it was (data-v) kept as it is,
       # with its view, its slider, Switch's side, where it's scrolled; the rest, new. Nothing here acts on a pair: the
       # views' controls above, through allThemes, do what they did)
       "function ready(){if(R){document.querySelectorAll('.ok,.online').forEach(function(x){x.hidden=false});"
       "var o=document.querySelector('.offline');if(o)o.hidden=true;}"
       "document.querySelectorAll('details.same').forEach(function(d){d.open=true;});"
       "if(still)document.querySelectorAll('.flp').forEach(function(sw){sw.classList.add('still');});}"
       "function redraw(html){var main=document.querySelector('main'),got=new DOMParser().parseFromString(html,'text/html').querySelector('main');"
       "if(!main||!got)return;var fresh=document.importNode(got,true),was={},anchor=null,top=0;"
       "main.querySelectorAll('[data-v]').forEach(function(c){was[c.dataset.v]=c;"
       "if(!anchor&&c.getBoundingClientRect().bottom>0){anchor=c;top=c.getBoundingClientRect().top;}});"
       "[].slice.call(fresh.querySelectorAll('[data-v]')).forEach(function(c){var o=was[c.dataset.v];if(!o||!fresh.contains(c))return;"
       "var n=c.querySelector('.num'),m=o.querySelector('.num');if(n&&m)m.textContent=n.textContent;c.replaceWith(o);});"
       "main.replaceChildren.apply(main,[].slice.call(fresh.childNodes));ready();gone(DONE);left();"
       "if(anchor&&main.contains(anchor)){var d=anchor.getBoundingClientRect().top-top;if(Math.abs(d)>=0.5)window.scrollBy(0,d);}}"
       # (a change new to the page comes onto it with its pictures: they're loaded first, so none shows as an empty card —
       # each given 20 s, then it's drawn all the same)
       "function loaded(html){var doc=new DOMParser().parseFromString(html,'text/html'),have={},srcs=[];"
       "document.querySelectorAll('[data-v]').forEach(function(c){have[c.dataset.v]=1;});"
       "doc.querySelectorAll('main [data-v]').forEach(function(c){if(have[c.dataset.v])return;"
       "c.querySelectorAll('img').forEach(function(i){var u=i.getAttribute('src');if(u&&srcs.indexOf(u)<0)srcs.push(u);});});"
       "return Promise.all(srcs.map(function(u){return new Promise(function(ok){var i=new Image(),t=setTimeout(ok,20000);"
       "i.onload=i.onerror=function(){clearTimeout(t);ok();};i.src=u;});}));}"
       "var V=R&&R.v||0;"
       "function poll(){if(!R||!document.querySelector('.making:not(.stuck)'))return;"
       "fetch('/live',{headers:{'X-Page-Token':R.token}}).then(function(r){return r.json()}).then(function(d){"
       "if(d.v===V)return setTimeout(poll,1000);"
       "return fetch('/').then(function(r){return r.text()}).then(function(h){return loaded(h).then(function(){V=d.v;redraw(h);setTimeout(poll,300);});});"
       "}).catch(function(){setTimeout(poll,3000);});}"
       "poll();")


# ------------------------------------------------------------------ approving
def approve(out: str = SAMPLE, ask=input, terminal: bool | None = None, guide_path: str = GUIDE, now: dict | None = None,
            keep=None) -> int:
    """The sample's looks into the guide: only at a terminal, only for a sample of exactly what differs now, only when
    the maintainer types approve. With the guide it keeps the approved app's look files in git (the guide's ``files``),
    the next sample's Before."""
    terminal = (sys.stdin.isatty() and sys.stdout.isatty()) if terminal is None else terminal
    if not terminal:
        print("approve asks the maintainer at a terminal, and won't run without one.", file=sys.stderr)
        return 2
    before = load_guide(guide_path) if os.path.exists(guide_path) else {}
    current = looks() if now is None else now
    diff = compare(before.get("looks", {}), current)
    count = sum(len(v) for v in diff.values())
    if not count:
        print("Every look is in the style guide: nothing to approve.")
        return 0
    try:
        with open(os.path.join(out, "sample.json"), encoding="utf-8") as f:
            made = json.load(f)
    except (OSError, ValueError):
        made = {}
    fp = fingerprint(diff)
    if made.get("fingerprint") != fp or made.get("made_from") != made_from() or not os.path.exists(os.path.join(out, "index.html")) or \
            any(not os.path.exists(os.path.join(out, p)) for p in made.get("shots", [])):
        print("There's no sample of the looks that differ now (or the code changed since it was made). Look at them first "
              f"(it makes the sample, and has the Approve buttons): {REVIEW}", file=sys.stderr)
        return 1
    for state in ("new", "changed", "gone"):
        for k in diff[state]:
            print(f"  {state}: {_short(k, 110)}")
    print(f"The sample: {os.path.join(out, 'index.html')}")
    if ask(f"Type approve to add these {count} look{'s' if count != 1 else ''} to the style guide: ").strip() != "approve":
        print("Nothing approved.")
        return 1
    write_guide(current, {"date": datetime.date.today().isoformat(), "sample": fp, "looks": count}, guide_path, before)
    if keep is None and guide_path == GUIDE:
        keep = keep_approved
    if keep:
        keep()
    print(f"Approved: dev/style-guide.json has {len(current)} looks. Commit it with the change, the files as they are now.")
    return 0


# What the sample's pictures can't show: a state it can't put an element in (visited, a placeholder, a scrollbar…: the
# ones it can are FORCED and PROPERTY_STATES), a rule for a screen or setting it can't take (@supports, print, an @media
# other than a width or MEDIA), and what only behaves (the cursor, what can be clicked or selected). Motion is pictured
# as a filmstrip (finder's frames), a state forced on (style-sample.js force), a width or setting taken for its rule.
STILL_PSEUDO = {"::before", "::after", ":first-child", ":last-child", ":first-of-type", ":last-of-type", ":nth-child",
                ":nth-last-child", ":nth-of-type", ":only-child", ":not", ":is", ":where", ":has", ":root", ":empty", ":scope",
                *(":" + s for s in FORCED + PROPERTY_STATES)}
UNSEEN_PROPS = re.compile(r"(?:^|[;\s\"'{])(?:cursor|will-change|pointer-events|user-select|caret-color|"
                          r"scroll-[\w-]+|touch-action)\s*:|<animate|<set\b", re.I)


def stills_show(item: dict) -> bool:
    """Whether a change to this look is all in what the sample's pictures show, so the same pictures before and after
    mean it changed nothing anyone sees (accept_same): its states forced on, its width or setting taken, what moves
    as a filmstrip. Never for a theme's colour, a decoration's code or a script's style (the pictures show only what the
    demo's pages use of them), a state or a rule for a screen or setting the sample can't take, or a style that only
    behaves. The pictures themselves must show it too (style-sample.js: caught; sort_looks)."""
    look = str(item.get("look") or "")
    kind = look.split(" ", 1)[0]
    if kind not in ("css", "inline", "svg", "file"):
        return False
    if kind == "css":
        ctx, _, sel = look[4:].rpartition(" | ") if " | " in look else ("", "", look[4:])
        if sel.startswith("@") and not sel.startswith("@keyframes "):
            return False
        for media in re.findall(r"\(([^()]*)\)", ctx) if ctx else []:
            m = re.fullmatch(r"(max|min)-width:(\d+)px", media)
            if (not m and media not in MEDIA) or (m and m.group(1) == "min" and int(m.group(2)) > 1280):
                return False   # (the pictures are 1280 wide, or as narrow as a max-width takes)
        if ctx and (not ctx.startswith("@media") or set(re.sub(r"\([^()]*\)", " ", ctx[6:]).split()) - {"and", "screen", "all"}):
            return False   # (print, or another medium the pictures aren't of)
        if any(p not in STILL_PSEUDO for p in re.findall(r"::?[a-z-]+", sel.split(" ", 1)[1] if sel.startswith("@") else sel)):
            return False
    text = look + " " + " ".join(str(item.get(k) or "") for k in ("was", "now"))
    return not UNSEEN_PROPS.search(text)


def accept_same(items: list, shots: dict, now: dict, guide_path: str = GUIDE, keep=None) -> list:
    """The looks a sample photographed that drew exactly the same Before and After everywhere (sort_looks's "same"):
    into the guide with no approval asked, as the maintainer ruled — they change nothing anyone sees. Not a look drawn on
    no demo page (sort_looks's "code"), nor one whose change a still picture can't show (stills_show): those still wait
    for the maintainer. The ids taken in."""
    same = [it["id"] for it in sort_looks(items, shots, True)["same"] if stills_show(it)]
    return approve_looks(items, same, now, guide_path, keep, on="drew the same")[0] if same else []


def approve_looks(items: list, ids, now: dict, guide_path: str = GUIDE, keep=None, on: str = "review page") -> tuple[list, dict]:
    """Some of a sample's looks into the guide (the review page's Approve buttons; or, ``on`` "drew the same", the
    sample's own for what drew exactly the same before and after): each only if it still differs from the guide exactly
    as the sample showed it. (approved ids, {refused id: why})."""
    before = load_guide(guide_path) if os.path.exists(guide_path) else {}
    looks_ = dict(before.get("looks", {}))
    diff = compare(looks_, now)
    by_id = {it["id"]: it for it in items}
    done, refused, already = [], {}, []
    for i in ids:
        it = by_id.get(i)
        if it is None:
            refused[i] = "not in this sample"
            continue
        k, st = it["look"], it["state"]
        if (k not in looks_ if st == "gone" else looks_.get(k) == it["now"]) and now.get(k) == it["now"]:
            already.append(i)   # (approved already, on another change that shows it too: done)
            continue
        shown = {"new": it["now"], "changed": [it["was"], it["now"]], "gone": it["was"]}[st]
        if k not in diff[st] or diff[st][k] != shown:
            refused[i] = "its code changed since the sample"
            continue
        if st == "gone":
            looks_.pop(k, None)
        else:
            looks_[k] = it["now"]
        done.append(i)
    if done:
        fp = fingerprint({st: {it["look"]: diff[it["state"]][it["look"]] for it in (by_id[i] for i in done) if it["state"] == st}
                          for st in ("new", "changed", "gone")})
        write_guide(looks_, {"date": datetime.date.today().isoformat(), "sample": fp, "looks": len(done), "on": on},
                    guide_path, before)
        if not any(compare(looks_, now).values()):   # all of it approved: the app as it is now is the next Before
            if keep is None and guide_path == GUIDE:
                keep = keep_approved
            if keep:
                keep()
    return sorted(done + already), refused


IDLE = 3600   # seconds the review page waits unused before it closes (opened from callboard's board, there's no Ctrl-C)


def review(out: str = SAMPLE, terminal: bool | None = None, open_browser: bool = True, guide_path: str = GUIDE,
           ready=None, log: str | None = None, idle: float = IDLE, gone: tuple[float, float] = (3.0, 10.0), step: bool = False,
           make=None) -> int:
    """The sample's page with Approve buttons, served to this computer only (127.0.0.1) while it runs: Approve all, or a
    change at a time, and what's approved leaves the page. Like approve: only at a terminal, and only for looks as the
    sample showed them. It closes once everything is approved, once its page is closed (each open tab beats every 2 s and
    says goodbye as it goes: ``gone`` is how long after a goodbye — a reload comes back sooner — and how long without a beat
    it stops), or after an hour unused (the beats don't count as using it: a tab left open and forgotten doesn't keep it
    open for ever). With log (callboard's board, the
    r key: the board is on the terminal), what it says goes to that file instead. With ``make`` (main's, when the sample
    doesn't show what's waiting: ``make(live, stop)`` makes it, sample's), the page opens at once and the sample is made
    while it's open: each change shows on it as soon as all its pictures are taken (the page asks /live, and draws
    itself again keeping what's the same), and closing the page stops it."""
    terminal = (sys.stdin.isatty() and sys.stdout.isatty()) if terminal is None else terminal
    if not terminal:
        print("review is the maintainer's, at a terminal, and won't run without one.", file=sys.stderr)
        return 2
    def made_in(folder):
        try:
            with open(os.path.join(folder, "sample.json"), encoding="utf-8") as f:
                made = json.load(f)
            with open(os.path.join(folder, "index.html"), encoding="utf-8") as f:
                page = f.read()
        except (OSError, ValueError):
            return {}, ""
        return made, page
    if make is None:
        made, page = made_in(out)
        if not page or "items" not in made:
            print(f"There's no sample to review, and no look waits for approval (when one does, {REVIEW} makes it).", file=sys.stderr)
            return 1
    else:   # (the page at once, the sample on its way)
        made, page = {"items": []}, page_html([], {}, "", None, True, None, total=0, making=(
            "Making the sample: the demos are starting. Each change shows here as soon as its pictures are taken."))[0]
    # its pictures, as they were when it opened: a sample made again meanwhile (callboard's board, say) replaces the
    # folder's, and the page open here would lose its own (and, while it's made here, each as it's taken)
    pictures = tempfile.mkdtemp(prefix="style-review-")
    files: dict = {}

    def take(folder, names):
        for f in names:
            if os.path.exists(os.path.join(folder, f)):
                shutil.copy2(os.path.join(folder, f), pictures)
                files[f] = os.path.join(pictures, f)
    if make is None:
        take(out, [f for f in os.listdir(out) if f.endswith(".png")])
    saved = sys.stdout, sys.stderr
    if log is not None:   # (the terminal is the board's: say it to the file, yours alone to read)
        sys.stdout = sys.stderr = devpage.private(log)
    import threading
    lock = threading.Lock()
    ended = threading.Event()
    approved = [0]               # (how many approved here: what a step on callboard's board says once it's closed)
    cur = {"page": page, "made": made, "v": 0, "making": make is not None}   # (what the page is now: drawn again as the sample's made)
    stop = threading.Event()     # (the page closed while the sample's made: it stops)

    def html(token: str) -> str:
        # the token the Approve buttons send, set before the page's own script reads it
        # and the changes approved already (since the sample, here or before): left off the page
        # and the page's beat while it's open, and its goodbye as it goes, so the review stops once the last tab is closed
        guide = load_guide(guide_path)["looks"]
        page, made, v = cur["page"], cur["made"], cur["v"]
        done = [it["id"] for it in made["items"] if guide.get(it["look"]) == it["now"]]
        return page.replace("<script>var R=window.REVIEW;",
                            f"<script>window.REVIEW={{token:{json.dumps(token)},done:{json.dumps(done)},v:{v}}};{devpage.beat(token, 2)}var R=window.REVIEW;", 1)

    def approve(q: dict) -> dict:
        try:
            ids = [int(i) for i in q["ids"]]
        except (ValueError, KeyError, TypeError) as e:
            raise ValueError("ids: a list of the changes' numbers") from e
        with lock:   # (an approval under way finishes before the page closes)
            now = looks()
            items = cur["made"]["items"]
            done, refused = approve_looks(items, ids, now, guide_path)
            left = any(compare(load_guide(guide_path)["looks"], now).values())
        approved[0] += len(done)
        for i in done:
            it = next(x for x in items if x["id"] == i)
            print(f"  approved {it['state']}: {_short(it['look'], 110)}")
        if not left:   # (everything approved: nothing more to do here)
            ended.set()
        return {"approved": done, "refused": refused}

    def live(st):   # (the sample as it's made: the looks with all their pictures so far, on the page)
        items = [it for it in st["items"] if it["id"] in st["done"]]
        shots = {k: v for k, v in st["shots"].items() if int(k) in st["done"]}
        take(st["folder"], {p[k] for ps in shots.values() for p in ps for k in ("before", "after", "diff") if p.get(k)})
        n, total = len(items), len(st["items"])
        said_now = (f"Making the sample: {n} of {total} look{'s' if total != 1 else ''} ready. The rest show here as soon as their "
                    "pictures are taken." if not st.get("stuck") else
                    f"The sample couldn't be made (what python3 dev/style.py review said has why). {n} of {total} are here to approve; "
                    f"for the rest, close this page and run {REVIEW} again.")
        page = page_html(items, shots, st["fingerprint"], st["off"], st["compared"], st["source"], making=said_now, total=total,
                         stuck=bool(st.get("stuck")))[0]
        keep = [{k: it[k] for k in ("id", "state", "look", "was", "now")} for it in st["items"]]
        cur.update(page=page, made={"items": keep}, v=cur["v"] + 1, last=st)

    def making():
        assert make is not None   # (started only with it: up)
        print("The sample doesn't show every look the check flags as it is now, or the code that draws them has changed "
              "since: making it again, each change on the page as soon as its pictures are taken.", flush=True)
        try:
            rc = make(live, stop)
        except Exception as e:   # noqa: BLE001 — (said, and the page says so: what's there can still be approved)
            print(f"The sample couldn't be made: {e!r}", file=sys.stderr)
            rc = 1
        if stop.is_set():
            return
        made, page = made_in(out) if rc == 0 else ({}, "")
        if rc == 0 and not any(compare(load_guide(guide_path)["looks"], looks()).values()):
            ended.set()   # (nothing to sample after all: every look approved meanwhile)
        elif page and made.get("items"):
            take(out, [f for f in os.listdir(out) if f.endswith(".png")])
            cur.update(page=page, made=made, v=cur["v"] + 1, making=False)
            print("The sample's made: every change is on the page.", flush=True)
        elif cur.get("last"):
            live(dict(cur["last"], stuck=True))
            cur["making"] = False
        else:
            cur.update(page=page_html([], {}, "", None, True, None, total=0, stuck=True, making=(
                f"The sample couldn't be made (what python3 dev/style.py review said has why). Close this page and run {REVIEW} again."))[0],
                v=cur["v"] + 1, making=False)
    worker = threading.Thread(target=making, name="style-sample", daemon=True)

    def up(server, url, token):
        if make is not None:   # (the page's address said first, then the sample made)
            worker.start()
        if step:   # (a step of callboard's board, its Wardrobe block: the page's address its last line; Enter there
            # skips it, ending it as Ctrl-C would — its pictures' copy removed all the same)
            said(f"http://{urllib.parse.urlsplit(url).netloc}/ · close the page when done")
            import signal

            def skipped(*_):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, skipped)
        if ready:
            ready(server, url, token)
    # (its pictures: served from the copy; its script and styles only by this response's nonce, so the sample has no style="..." of its own)
    try:
        devpage.serve("review", html, {"/live": lambda q: {"v": cur["v"], "making": cur["making"]}}, {"/approve": approve}, files=files,
                      img="'self'", gone=gone, idle=(idle, "python3 dev/style.py review"), open_browser=open_browser, ready=up, ended=ended)
    finally:
        stop.set()
        if worker.is_alive():   # (the page closed before the sample was made: it stops, and leaves the last one as it was)
            print("Stopping the sample (the page was closed before it was made)…", flush=True)
            worker.join(60)
        shutil.rmtree(pictures, ignore_errors=True)
    left = sum(len(v) for v in compare(load_guide(guide_path)["looks"], looks()).values())
    print("Every look is in the style guide. Commit dev/style-guide.json, and the files as they are now." if not left else
          f"{left} look{'s' if left != 1 else ''} still to approve. Commit dev/style-guide.json with what you approved.")
    if step and not left:   # (what the block says once its check again finds nothing waiting)
        said(f"{approved[0]} approved just now · commit dev/style-guide.json" if approved[0] else
             "every look approved · commit dev/style-guide.json")
    if log is not None:
        sys.stdout.close()
        sys.stdout, sys.stderr = saved
    return 0


def review_line(n: int, out: str = SAMPLE, now: dict | None = None) -> str:
    """callboard's line for looks waiting: how many, and the one command that opens the page to approve them (in another
    terminal, while the other checks run), to paste as it is — review makes the sample again first when it doesn't show
    what's waiting (sample_shows; ``out`` and ``now`` are the sample's folder and the looks, as review would take them)."""
    return f"{n} look{'s need' if n != 1 else ' needs'} your approval: {REVIEW}"


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "check":
        now = looks()
        found = problems(now=now)
        diff = pending(now=now)
        waiting = sum(len(v) for v in diff.values())
        made = made_by(diff) if waiting and os.environ.get("CALLBOARD_SAMPLE") != "0" else None
        if feeding():   # (on callboard's board: its bar — every look compared with the guide, by this process alone; or,
            # the looks waiting already in a sample, that sample and the workers that made it)
            if made is not None:
                bar = Progress(waiting, unit="looks sampled", bar="style")
                bar.workers = made.get("workers") if isinstance(made.get("workers"), int) else None
                bar.at(waiting, waiting, "looks · sample from " + str(made.get("made") or "")[11:16])
            else:
                bar = Progress(len(now), unit="looks compared", bar="style")
                bar.workers = 1
                bar.at(len(now), len(now), "looks · against the style guide")
        for p in found:
            print("  " + p)
        if found:
            print(f"{len(found)} look{'s' if len(found) != 1 else ''} not in the style guide, for the maintainer to look at and "
                  f"approve: {REVIEW}")
            said(review_line(len(found), now=now))
            then(0 if made is not None or sample_shows(diff) else 1)   # (on the board: its next step makes the sample, unless it shows them)
        else:
            print("Every look is in the style guide.")
            if os.environ.get("CALLBOARD_RECHECK") != "1":   # (checked again after the review: its line stands, what was approved)
                said(result(tally(len(now), "look", "approved")))
            then(0)
        if not found:
            return 0
        from callboard import checks, policy
        r = checks.levels("style")
        level = r.level("waiting") if r else "warn"
        from callboard.progress import status
        status("looks waiting")
        return policy.exit_code([level])
    if cmd == "sample":
        rest = argv[2:]
        if rest and (rest[0] != "--before" or len(rest) != 2):
            print("python3 dev/style.py sample [--before <folder>]", file=sys.stderr)
            return 2
        return sample(before=os.path.abspath(rest[1]) if rest else None)
    if cmd == "approve":
        return approve()
    if cmd == "review":
        if argv[2:] not in ([], ["--board"], ["--step"]):
            print("python3 dev/style.py review [--board | --step]", file=sys.stderr)
            return 2
        # --board: opened from callboard's board (its r key), which is on the terminal: what it says goes to a file
        # --step: a step of callboard's board (its Wardrobe, once the rest is done): on the board's terminal, what it
        # says sent to the board
        step = argv[2:] == ["--step"]
        log = os.path.join(SAMPLE, "review.log") if argv[2:] == ["--board"] else None
        diff = pending()
        if any(diff.values()) and not sample_shows(diff, SAMPLE) and not step:
            # (what check flags, the page shows: the page opened at once, the sample made again while it's open)
            os.makedirs(SAMPLE, exist_ok=True)
            return review(log=log, make=lambda live, stop: sample(live=live, stop=stop))
        if any(diff.values()) and not sample_shows(diff, SAMPLE):   # (on callboard's board, a step: made before it, as before)
            import contextlib
            os.makedirs(SAMPLE, exist_ok=True)
            with (devpage.private(log) if log else open(os.devnull, "w", encoding="utf-8")) as fh, \
                    contextlib.redirect_stdout(fh if log else sys.stdout):
                print("The sample doesn't show every look the check flags as it is now, or the code that draws them has changed "
                      "since: making it again.", flush=True)
                if sample():
                    print("The sample couldn't be made: see above.", file=sys.stderr)
                    return 1
        return review(log=log, terminal=True if step else None, step=step)
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:   # (Ctrl-C: stopped, said so, no trace — callboard's review at the end, say, skipped)
        print("\nStopped.", file=sys.stderr)
        sys.exit(130)
