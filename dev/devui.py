#!/usr/bin/env python3
"""The devtools UI: devtools' board in the browser, a page behind each block, and the buttons that run devtools again.

    devtools ui                   the last run as it was, and a fresh scan of the checks as it comes in
    python3 dev/devui.py [--record <folder>] [--open <block>] [--no-scan] [--no-browser] [--port <n>] [--from-board]

What it shows is what devtools decided: each run of devtools is a record (~/.local/state/devtools/runs/<when>/: the
plan devtools wrote, each block's output, and how the board stood as it went — dev/board.py's state.json). This serves
those records to a page (dev/devui.html) that polls them, so a run under way moves on the page as on the board. Nothing
is judged here: a block's state, its line and its bar are the board's, and a block's page shows the detail file its
tool wrote beside the record (dev/progress.py's detail: DEVTOOLS_DETAIL). The buttons run devtools itself (dev/devtools,
next to this file: Run again is ``--checks-only``, a block's Run again ``--only <block>``, Push ``push <code>
--sync-only``, Restart ``restart``), one at a time, and the page shows the record that run writes. Its Connected to says
where devtools pushes and how the NAS stands beside the code picked (``devtools status <code>``), and which Dionysus answers
there: its fingerprint and identicon, as its own pages show them (its open /peer/v1/hello, at TN_WEB_PORT; the identicon
drawn by app.js's own stIdenticon, put in the page). Opened with nothing under way, it starts a fresh scan of the checks
(``--checks-only``, as the last run was: its mode, --release), unless --no-scan.

The board opens it too (u, or a block's letter: --record is that run's, --open the block; --from-board: what it says
goes to ui.log in the record, the terminal being the board's), and tells an open page which block to show ("wanted" in
the record). The page is served as the dev tools' pages are (dev/devpage.py: this computer, the browser that opened it)
in the app's own themes (plexcache/themes.py, plexcache/web/app.css; its own looks in dev/devui.css, outside the style
guide: no approval is asked for the dev tools' pages). It closes once the page is closed and no run of its own is under way.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dev import devpage  # noqa: E402
from plexcache import themes  # noqa: E402

SCRIPT = os.path.join(ROOT, "dev", "devtools")
PAGE = os.path.join(ROOT, "dev", "devui.html")
EXTRA = os.path.join(ROOT, "dev", "devui.css")
WEB = os.path.join(ROOT, "plexcache", "web")
FRESH = 6.0     # seconds since the board last wrote (or touched) a record's state: beyond it, the run isn't under way
KEEP = 2_000_000   # the most of a block's log sent to the page
MODES = ("local", "master", "main", "dev", "release", "beta")   # (the code devtools checks or pushes)


def runs_dir() -> str:
    """Where devtools keeps the runs' records (its STATE_DIR/runs; DEVTOOLS_RUNS for a test)."""
    got = os.environ.get("DEVTOOLS_RUNS")
    if got:
        return got
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(state, "devtools", "runs")


def _json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


class Run:
    """One record: the plan devtools wrote for it and, once the board ran, how it stood (state.json, as it goes)."""

    def __init__(self, folder: str):
        self.folder = folder
        self.name = os.path.basename(folder)
        self.plan = _json(os.path.join(folder, "plan.json")) or {}
        self.state = _json(os.path.join(folder, "state.json"))
        try:
            self.fresh = time.time() - os.path.getmtime(os.path.join(folder, "state.json")) < FRESH
        except OSError:
            self.fresh = False

    @property
    def kind(self) -> str:
        return str(self.plan.get("kind") or "run")

    @property
    def mode(self) -> str:
        return str(self.plan.get("mode") or "local")

    @property
    def done(self) -> bool:
        return bool(self.state and self.state.get("done"))

    @property
    def live(self) -> bool:
        """Under way: the board is writing its state (and hasn't said it's done)."""
        return self.state is not None and not self.done and self.fresh

    @property
    def started(self) -> float | None:
        got = (self.state or {}).get("started") or self.plan.get("started")
        return float(got) if isinstance(got, (int, float)) else None

    def sections(self) -> list[dict]:
        """Its blocks as the board last had them; before the board wrote anything, as the plan has them (waiting)."""
        if self.state and isinstance(self.state.get("sections"), list):
            return [dict(s, run=self.name) for s in self.state["sections"]]
        return [{"id": s.get("id"), "title": s.get("title"), "page": s.get("page", ""), "key": (s.get("key") or {}).get("key", ""),
                 "state": "waiting", "shown": "waiting", "status": "WAITING", "note": "", "last": "", "rc": None, "bar": None,
                 "issues": [], "started": None, "ended": None, "run": self.name}
                for s in self.plan.get("sections") or [] if isinstance(s, dict)]

    def summary(self) -> dict:
        """A line of the history: when, what, and each block's state."""
        return {"run": self.name, "kind": self.kind, "mode": self.mode, "only": self.plan.get("only") or "",
                "header": (self.state or {}).get("header") or self.plan.get("header") or [], "commit": self.plan.get("commit") or "",
                "started": self.started, "ended": (self.state or {}).get("ended"), "done": self.done, "live": self.live,
                "aborted": bool((self.state or {}).get("aborted")),
                "blocks": {s["id"]: s.get("state") for s in self.sections()}}


class Data:
    """The records, read anew at each request (the board writes them as it goes), and the runs started from the page."""

    def __init__(self, runs: str | None = None, record: str | None = None, script: str = SCRIPT):
        self.runs_dir = runs or runs_dir()
        self.record = record   # (the board's own run, when it opened the page: where "wanted" is written)
        self.script = script
        self.job: subprocess.Popen | None = None   # (the one run of devtools started here, while it runs)
        self.job_what = ""
        self.job_log = ""
        self.lock = threading.Lock()
        self.wanted = ""   # (a block the board asked the page to show, not yet taken)

    # ------------------------------------------------------------- the records
    def runs(self) -> list[Run]:
        """Every record, newest first (they're named by when they started)."""
        try:
            names = sorted((n for n in os.listdir(self.runs_dir) if n[:1].isdigit()), reverse=True)
        except OSError:
            return []
        out = []
        for n in names:
            folder = os.path.join(self.runs_dir, n)
            if os.path.isfile(os.path.join(folder, "plan.json")):
                out.append(Run(folder))
        return out

    def view(self) -> dict:
        """The board as it stands, across the records: each block from the newest record that has it (a block run again on
        its own, or the copy alone, over the last full run), what it said the time before (``before``), the run under way,
        and the history. One rule, not a judgement: the newest word on each block is the board's own."""
        runs = self.runs()
        base = next((r for r in runs if r.kind in ("run", "checks")), None) or (runs[0] if runs else None)
        order: list[str] = [s["id"] for s in base.sections()] if base else []
        for r in runs:
            if r.kind in ("run", "checks") and r is not base and all(i in order for i in (s["id"] for s in r.sections())):
                continue
            for s in r.sections():
                if s["id"] not in order and r.name >= (base.name if base else ""):
                    order.append(s["id"])
        blocks = []
        for sid in order:
            newest = next((r for r in runs if any(s["id"] == sid for s in r.sections())), None)
            if newest is None:
                continue
            block = next(s for s in newest.sections() if s["id"] == sid)
            older = [r for r in runs if r.name < newest.name and any(s["id"] == sid and s.get("state") not in ("waiting", "running") for s in r.sections())]
            before = next((s for s in older[0].sections() if s["id"] == sid), None) if older else None
            block["before"] = {"state": before.get("state"), "status": before.get("status"), "last": before.get("last"),
                               "ended": before.get("ended"), "run": older[0].name} if before else None
            block["live"] = newest.live
            block["detail"] = os.path.isfile(os.path.join(newest.folder, "detail", f"{sid}.json"))
            planned = next((s for s in newest.plan.get("sections") or [] if isinstance(s, dict) and s.get("id") == sid), {})
            block["cmd"] = str(planned.get("cmd") or "")   # (what runs it: the page says when that writes no detail)
            blocks.append(block)
        live = next((r for r in runs if r.live), None)
        with self.lock:
            job = {"what": self.job_what, "running": self.job is not None and self.job.poll() is None,
                   "rc": None if self.job is None or self.job.poll() is None else self.job.returncode}
            wanted, self.wanted = self.wanted, ""
        if self.record:   # (the board's word on which block to show: taken once)
            path = os.path.join(self.record, "wanted")
            try:
                with open(path, encoding="utf-8") as fh:
                    wanted = fh.read().strip() or "hub"
                os.unlink(path)
            except OSError:
                pass
        return {"header": (base.state or {}).get("header") if base and base.state else (base.plan.get("header") if base else []),
                "commit": ((base.state or {}).get("commit") or base.plan.get("commit", "")) if base else "", "mode": base.mode if base else "local",
                "release": bool(base.plan.get("release")) if base else False, "run": base.name if base else "",
                "started": base.started if base else None, "ended": (base.state or {}).get("ended") if base else None,
                "live": {"run": live.name, "kind": live.kind, "header": (live.state or {}).get("header") or live.plan.get("header")} if live else None,
                "blocks": blocks, "job": job, "wanted": wanted, "history": [r.summary() for r in runs[:20]],
                "project": base.plan.get("project", ROOT) if base else ROOT}

    def block(self, sid: str) -> tuple[Run, dict] | None:
        for r in self.runs():
            for s in r.sections():
                if s["id"] == sid:
                    return r, s
        return None

    def log(self, q: dict) -> dict:
        """A block's output, as the board kept it (the newest record's): the log file the board named."""
        got = self.block(str(q.get("id", "")))
        if got is None:
            raise ValueError("no such block")
        run, s = got
        path = str(s.get("log") or "")
        if not path or os.path.dirname(os.path.dirname(path)) != run.folder:   # (the record's own logs/ only)
            path = os.path.join(run.folder, "logs", f"{s['id']}.log")
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read(KEEP + 1)
        except OSError:
            text = ""
        cut = len(text) > KEEP
        return {"id": s["id"], "run": run.name, "text": text[:KEEP], "cut": cut}

    def detail(self, q: dict) -> dict:
        """A block's detail, as its tool wrote it (dev/progress.py's detail, in the newest record that has the block)."""
        got = self.block(str(q.get("id", "")))
        if got is None:
            raise ValueError("no such block")
        run, s = got
        data = _json(os.path.join(run.folder, "detail", f"{s['id']}.json"))
        return {"id": s["id"], "run": run.name, "detail": data}

    # ------------------------------------------------------------- devtools, run from here
    def busy(self) -> bool:
        with self.lock:
            return self.job is not None and self.job.poll() is None

    def start(self, args: list[str], what: str) -> dict:
        """devtools with these arguments (its mode first), one at a time, in the project; its own words (the banners, the
        board's lines without a terminal) to a log of yours alone. The record it writes is what the page then shows."""
        with self.lock:
            if self.job is not None and self.job.poll() is None:
                raise ValueError(f"{self.job_what} is still running")
            os.makedirs(self.runs_dir, exist_ok=True)
            self.job_log = os.path.join(self.runs_dir, "ui-job.log")
            out = devpage.private(self.job_log)
            env = {k: v for k, v in os.environ.items() if k not in ("PLEXCACHE_PROGRESS", "DEVTOOLS_STEP")}
            try:
                self.job = subprocess.Popen([self.script, *args], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                            stderr=subprocess.STDOUT, start_new_session=True)
            finally:
                out.close()
            self.job_what = what
        return {"ok": True, "what": what}

    def _mode(self, q: dict) -> list[str]:
        mode = str(q.get("mode") or "")
        if mode not in MODES:
            runs = self.runs()
            base = next((r for r in runs if r.kind in ("run", "checks")), None) or (runs[0] if runs else None)
            mode = base.mode if base and base.mode in MODES else "local"   # (a restart's record has no code)
            release = bool(base.plan.get("release")) if base else False
        else:
            release = bool(q.get("release"))
        return [mode] + (["--release"] if release else [])

    def scan(self, q: dict) -> dict:
        return self.start([*self._mode(q), "--checks-only"], "the checks")

    def rerun(self, q: dict) -> dict:
        sid = str(q.get("id", ""))
        if not sid.replace("_", "").isalnum():
            raise ValueError("no such block")
        return self.start([*self._mode(q), "--only", sid], f"{sid} again")

    def push(self, q: dict) -> dict:
        """Push: the code picked, copied to the NAS and the app restarted (devtools push <code> --sync-only)."""
        return self.start(["push", *self._mode(q), "--sync-only"], "the push")

    def restart(self, q: dict) -> dict:
        """Restart: the app restarted, nothing copied (devtools restart)."""
        return self.start(["restart"], "the restart")

    def status(self, q: dict) -> dict:
        """Connected to: how the NAS stands beside the code picked (devtools status <code>), and the Dionysus that answers
        at its web port (its open hello: who it is — its fingerprint and identity — and its version), each looked at now."""
        mode = str(q.get("mode") or "")
        if mode not in MODES:
            mode = self._mode({})[0]
        try:
            got = subprocess.run([self.script, "status", mode], cwd=ROOT, capture_output=True, text=True, timeout=60,
                                 stdin=subprocess.DEVNULL, env={k: v for k, v in os.environ.items() if k not in ("PLEXCACHE_PROGRESS", "DEVTOOLS_STEP")})
            st = json.loads(got.stdout or "null")
        except subprocess.TimeoutExpired:   # (a NAS slow to answer: not known yet, rather than a failure)
            return {"error": "The NAS took more than a minute to answer, so how it stands isn't known yet: refresh to ask again",
                    "slow": True, "mode": mode}
        except (OSError, ValueError) as e:
            return {"error": f"devtools status couldn't be run: {e}", "mode": mode}
        if not isinstance(st, dict):
            said = (got.stderr or "").strip().splitlines()
            return {"error": said[-1] if said else f"devtools status said nothing (exit {got.returncode})", "mode": mode}
        st["hello"] = hello(str(st.get("host") or ""), st.get("web_port"))
        return st

    def stop(self) -> None:
        with self.lock:
            if self.job is not None and self.job.poll() is None:
                try:
                    os.killpg(self.job.pid, 15)
                except OSError:
                    pass


def hello(host: str, port, timeout: float = 4.0) -> dict:
    """The Dionysus at that address: its open hello (/peer/v1/hello: public facts only), as {"url", "fingerprint",
    "identity", "name", "version"}, or {"url", "error"} when nothing answers there as Dionysus."""
    from plexcache import peerlink
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", host) or not isinstance(port, int):
        return {"url": "", "error": "no address to ask"}
    url = f"http://{host}:{port}"
    try:
        with urllib.request.urlopen(url + "/peer/v1/hello", timeout=timeout) as r:   # (http to the NAS, as the app's own peers ask)
            got = json.loads(r.read(1 << 20))
    except (OSError, ValueError) as e:
        return {"url": url, "error": str(getattr(e, "reason", "") or e)}
    if not isinstance(got, dict) or got.get("app") != "plexcache":
        return {"url": url, "error": "what answers there isn't Dionysus"}
    ident = got.get("identity")
    ident = ident if isinstance(ident, dict) else {}
    return {"url": url, "fingerprint": peerlink.fingerprint(str(got.get("public") or "")), "name": str(got.get("name") or ""),
            "version": str(got.get("version") or ""),
            "identity": {k: str(ident.get(k) or "") for k in ("id", "short", "source", "name")}}


def identicon_js() -> str:
    """app.js's own stIdenticon (the picture of an identity, as the app draws it everywhere), for the page: the same code,
    taken from app.js, so the two can't draw one fingerprint two ways."""
    with open(os.path.join(WEB, "app.js"), encoding="utf-8") as fh:
        js = fh.read()
    got = re.search(r"\n( *)function stIdenticon\(id\) \{\n.*?\n\1\}\n", js, re.S)
    if got is None:
        raise RuntimeError("app.js has no stIdenticon(id) for the devtools UI's Connected to")
    return got.group(0).strip()


def page() -> str:
    """dev/devui.html with the app's themes (their CSS and the script that picks one), app.js's identicon and the UI's
    own looks put in."""
    with open(PAGE, encoding="utf-8") as fh:
        html = fh.read()
    with open(EXTRA, encoding="utf-8") as fh:
        extra = fh.read()
    return (html.replace("/*THEMES*/", themes.css(None), 1).replace("/*EXTRA*/", extra, 1)
            .replace("/*THEME_JS*/", themes.script(None), 1).replace("/*IDENTICON*/", identicon_js(), 1))


def files() -> dict[str, str]:
    """What the page loads besides itself, as the app serves them: the app's stylesheet, and what the theme loads (its
    fonts and the scripts that draw its decorations: themes.files)."""
    return {"static/app.css": os.path.join(WEB, "app.css"), **themes.files(WEB)}


def serve(data: Data, open_browser: bool = True, port: int = 0, ready=None, first: str = "", scan: bool = True,
          idle: float = 3600.0) -> int:
    """The page, until it's closed (or Ctrl-C): a fresh scan of the checks started first when nothing is under way."""
    if scan and not data.busy() and not any(r.live for r in data.runs()):
        try:
            data.scan({})
        except (ValueError, OSError) as e:
            print(f"The checks couldn't be started: {e}")
    if first:
        data.wanted = first
    html = page()

    def served(token: str) -> str:
        return html.replace("__TOKEN__", token, 1).replace("/*BEAT*/", devpage.beat(token, 5), 1)
    routes_get = {"/api/run": lambda q: data.view(), "/api/log": data.log, "/api/detail": data.detail, "/api/status": data.status}
    routes_post = {"/api/scan": data.scan, "/api/rerun": data.rerun, "/api/push": data.push, "/api/restart": data.restart}
    devpage.serve("devtools", served, routes_get, routes_post, files=files(), gone=(3.0, 30.0), busy=data.busy,
                  idle=(idle, "devtools ui"), port=port, open_browser=open_browser, ready=ready,
                  note="Ctrl-C to stop; it stops by itself once the page is closed and nothing is running")
    if data.busy():
        print(f"  {data.job_what} is still running: left to finish (its record is kept)")
    return 0


def main(args: list[str]) -> int:
    record, first, port, from_board = "", "", 0, False
    open_browser, scan = True, True
    rest = list(args)
    while rest:
        a = rest.pop(0)
        if a == "--record" and rest:
            record = rest.pop(0)
        elif a == "--open" and rest:
            first = rest.pop(0)
        elif a.startswith("--port=") or (a == "--port" and rest):
            port = int(a.split("=", 1)[1] if "=" in a else rest.pop(0))
        elif a == "--no-browser":
            open_browser = False
        elif a == "--no-scan":
            scan = False
        elif a == "--from-board":
            from_board = True
        else:
            print(f"devui: unknown option {a}", file=sys.stderr)
            return 2
    data = Data(record=record or None)
    saved = sys.stdout, sys.stderr
    if from_board and record:   # (the terminal is the board's: what this says goes to the record)
        sys.stdout = sys.stderr = devpage.private(os.path.join(record, "ui.log"))
    try:
        return serve(data, open_browser=open_browser, port=port, first=first, scan=scan and not record)
    finally:
        if from_board and record:
            sys.stdout.close()
            sys.stdout, sys.stderr = saved


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
