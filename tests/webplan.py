#!/usr/bin/env python3
"""What DHTML checks beyond the files in plexcache/web: callboard.toml's [dhtml] plan, the command callboard's web
check (callboard tool webcheck) runs first and reads as JSON.

    python3 tests/webplan.py        # with CALLBOARD_WEB_OUT=<folder> set: where the files go

"files": what the app makes, written into that folder for ESLint and html-validate to read — the script the server
makes (/theme.js) and every page as the app draws it (tests/render.js: PageStructureTests.PAGES_DRAWN), before the
first checks have run and after. "a11y": the pages axe-core draws — every page of the app (app.js's PAGES) on the two
demos (tests/demo.py: the showcase, mostly set up well, and the one with problems to show), in each look (Classic and
Dionysus, light and dark), each waited for until its title is drawn and its decorations painted (decor.js paints on
the next frames).
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "plexcache", "web")
LOOKS = (("classic", "Classic", "light"), ("classic", "Classic", "dark"), ("dionysus", "Dionysus", "light"), ("dionysus", "Dionysus", "dark"))
DEMOS = {"showcase": {"DEMO_SHOWCASE": "1"}, "problems": {"DEMO_PROBLEMS": "1"}}
DRAWN = 1500   # ms: the page's first draw and its decorations


def app_pages() -> list[str]:
    """Every page of the app, by its name in app.js's PAGES."""
    with open(os.path.join(WEB, "app.js"), encoding="utf-8") as fh:
        m = re.search(r"(?:var|let|const) PAGES = \{([\s\S]*?)\};", fh.read())
    if not m:
        raise SystemExit("app.js has no PAGES")
    return re.findall(r"([a-z]+):\s*page[A-Z]", m.group(1))


def a11y() -> dict:
    """The accessibility check's pages: each page of the app, on each demo, in each look."""
    servers = {name: {"cmd": [sys.executable, os.path.join(ROOT, "tests", "demo.py"), "{port}"], "env": env, "ready": "/api/state"} for name, env in DEMOS.items()}
    pages = [{"server": demo, "path": f"/{page}", "label": f"/{page} ({demo}) {name} {mode}", "scheme": mode,
              "storage": {"pc-theme": json.dumps({"theme": theme, "mode": mode})}}
             for demo in DEMOS for page in app_pages() for theme, name, mode in LOOKS]
    return {"servers": servers, "pages": pages, "wait_for": "h1", "settle": DRAWN}


def made(out: str) -> list[dict]:
    """What the app makes, written into ``out``: the theme script, and every page drawn while checking and once checked."""
    sys.path.insert(0, ROOT)
    from plexcache import themes
    from tests import test_plexcache as suite  # (the app on its fakes, and the pages drawn as the tests draw them)
    try:
        files = [{"path": os.path.join(out, "theme.js"), "as": "/theme.js"}]
        with open(files[0]["path"], "w", encoding="utf-8") as fh:
            fh.write(themes.script({}))
        pages, app = suite.PageStructureTests(), suite._new_app()
        for when in ("checking", "checked"):
            app.checking = app.testing = when == "checking"
            state = app.state()
            for page in suite.PageStructureTests.PAGES_DRAWN:
                files.append({"path": os.path.join(out, f"{page}-{when}.html"), "as": f"/{page} ({when})"})
                with open(files[-1]["path"], "w", encoding="utf-8") as fh:
                    fh.write(pages._render(state, page)["html"])
        return files
    finally:
        suite.tearDownModule()


def main() -> int:
    out = os.environ.get("CALLBOARD_WEB_OUT", "")
    if not out or not os.path.isdir(out):
        print("CALLBOARD_WEB_OUT isn't a folder: callboard's web check sets it (callboard tool webcheck)", file=sys.stderr)
        return 2
    stdout, sys.stdout = sys.stdout, sys.stderr   # (whatever the app or its fakes print: never in the plan)
    try:
        plan = {"files": made(out), "a11y": a11y()}
    finally:
        sys.stdout = stdout
    json.dump(plan, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    from dev import cli  # (dev/cli.py: what every script's command line does the same)
    _answered = cli.front(sys.argv[1:], __doc__, "python3 tests/webplan.py")
    sys.exit(main() if _answered is None else _answered)
