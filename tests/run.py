"""Run the test suite with a progress bar, so you can see it isn't stuck — on every core at once.

    python3 tests/run.py                  # the whole suite (tests.test_plexcache)
    python3 tests/run.py <module>         # another test module, by its import name
    python3 tests/run.py -j 1             # one test at a time, in this process (also PLEXCACHE_TEST_JOBS=1)
    python3 tests/run.py -j 4             # four workers (the default: two per core)
    python3 tests/run.py --exclude WebCheckTests   # all but that class (callboard runs its checks in a section of their own: DHTML)
    python3 tests/run.py --coverage       # and what of plexcache/ it ran, every worker's, saved for callboard/coverage.py

The tests are shared out among worker processes: a test at a time, except that a class set up once for all its tests
(setUpClass) goes as a whole, its tests together and in order in one worker. Each worker imports the module itself (its
own temporary folder, its own fake servers on their own ports) and asks for more when it's done. The slowest go first,
by how long each took last time (kept in ~/.cache/plex-cache/test-times.json), so the run isn't left waiting on one at
the end. Most tests spend their time waiting (on a fake server, a retry, a subprocess) rather than computing, so the
default is two workers per core. The warnings options the run was started with (-W, -X) are the workers' too. With one
test, or -j 1, it runs in this process, as unittest would.

PLEXCACHE_PROGRESS=bar draws the bar even when the output isn't a terminal (a log shown on one later); COLUMNS then says
how wide. PLEXCACHE_PROGRESS=feed sends it to callboard's board instead (callboard/board.py), which draws it in the tests' section.

GitHub runs the suite with unittest itself (build.yml), one test at a time: this file is for runs by hand, callboard
and dev/future.py.

On a terminal, one line is redrawn as tests finish:

    [##########----------]  57 passed · 2 failed · 1 warning · 118 total  ServerTests.test_export_has_no_secrets

The counts are the same everywhere (callboard/progress.py's ``counts``): passed, failed, the warnings the run printed, and
the total — on the bar, in its result line at the end, and on callboard's board (where the result line leaves them to the
bar). When the output isn't a terminal (CI, a log file), it prints a line every 10%.
Failures are listed in full at the end, as unittest normally does, and the
exit code is non-zero if anything failed.

A stop signal (SIGTERM: what callboard sends when you press Enter) ends the run at
once, mid-test. The tests that were running when it came aren't counted as failed:
the run says how many tests ran and how many weren't run, and exits with 130.
"""

import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
import unittest
import warnings
from collections.abc import Callable
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import dev  # noqa: E402, F401 — (puts the callboard install on the path: dev/__init__.py)

from callboard import coverage, policy  # noqa: E402 — (--coverage: what the tests ran, for callboard/coverage.py; policy: what the run is held to)
from callboard.progress import FEED as FEED_MARK  # noqa: E402 — (a record's mark: run's FEED says whether there's a board)
from callboard.progress import OFF, RED, Progress, detail, drawn, feeding, issue, short_version, tell, tested, timed  # noqa: E402 — the bar the dev tools share (callboard tool upkeep's too)
from callboard.progress import result as result_line  # noqa: E402 — (``result`` is run's: the result line every dev tool says)

FEED = feeding()  # (read before main() takes PLEXCACHE_PROGRESS out of the tests' environment)
# How this run talks to the board (callboard/board.py sets them for the block): its bars as records, and where its page's
# detail goes. Taken here and out of the environment (_run), as CONTEXT is — a test that runs a callboard tool, a check tool or
# tests/run.py itself would otherwise send the board its own records ("upstream not reached: x" in the board's Issues, the
# Unit Tests block's line a test's), and a test of what a tool prints with no board would see records instead. This run's
# own (its result's note, its page) are sent with them put back: board_says.
BOARD_KEYS = ("CALLBOARD_PROGRESS", "PLEXCACHE_PROGRESS", "CALLBOARD_DETAIL")
BOARD = {k: os.environ[k] for k in BOARD_KEYS if k in os.environ}


def board_says(fn, *args):
    """``fn(*args)`` (progress.status, progress.detail) as this run's, to the board: with how it talks to it put back."""
    was = {k: os.environ.get(k) for k in BOARD}
    os.environ.update(BOARD)
    try:
        return fn(*args)
    finally:
        for k, v in was.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
# What callboard told this run (the integration that runs the suite, the project's callboard.toml, the gate whose rules hold and the
# rest of its context), taken here and then out of the environment (main): the tests, and every tool they run, see none
# of it — a tool a test runs reads its own rules as it would by hand, and the suite's verdict is the same wherever
# it's run. The verdict reads what was taken.
CONTEXT_KEYS = ("CALLBOARD_INTEGRATION", "CALLBOARD_CONFIG", "CALLBOARD_CONTEXT", "CALLBOARD_RULES", "CALLBOARD_CAP", "CALLBOARD_INTO",
                "CALLBOARD_GITHUB", "CALLBOARD_PHASE", "CALLBOARD_TAG", "CALLBOARD_BRANCH",
                "CALLBOARD_DIFF", "CALLBOARD_GATE", "CALLBOARD_BASE", "CALLBOARD_CODE", "CALLBOARD_COPY", "CALLBOARD_COMMIT",
                "CALLBOARD_REPO", "CALLBOARD_HEAD")
CONTEXT = {k: os.environ[k] for k in CONTEXT_KEYS if k in os.environ}
CONFIG = policy.find_file()   # (CALLBOARD_CONFIG's, or this folder's; "" when there's none: nothing held)
# The rules (callboard.toml's [unit], [future]…: the integration that runs this suite, CALLBOARD_INTEGRATION): what a failed
# test, a warning the run printed and a skipped test are held as. Read through callboard/policy.py; nothing here decides.
SCHEMA = policy.Schema("tests", "Tests", {
    "failures": policy.Setting("level", "a test that failed (or errored)"),
    "warnings": policy.Setting("level", "a warning the run printed"),
    "skipped": policy.Setting("level", "a test skipped"),
}, "A run of the test suite.")
SKIPPED = [0]   # (the tests skipped this run: the verdict's)
MISSING: dict[str, list[str]] = {}   # (the tests skipped as missing what they need, by the need — "pages (tool)": kept at the end of a whole run, so callboard setup puts it here)
WHOLE = [False]   # (whether this run is the whole suite: only then is what it was missing kept)
SKIPS: list[tuple[str, str]] = []   # (each of them and why it was: said at the end, so a count of skipped tests is never all there is)


# A block of the checks' kind that runs tests: its findings' rules, a failed test held as an
# error is, a printed warning as a warning, a skipped test as information.
CHECKS_AS = {"failures": "errors", "warnings": "warnings", "skipped": "information"}


def missed(name: str, why: str) -> None:
    """A skip noted when it's one for want of something (callboard/needs.py's reason, read back the one way)."""
    from callboard import needs
    got = needs.missing(why)
    if got:
        MISSING.setdefault(f"{got[0]} ({got[1]})", []).append(name)


def skip_fix(why: str) -> str:
    """What puts right a skip for want of something callboard fetches or installs: callboard setup ("" for any other)."""
    from callboard import needs
    return "callboard setup" if needs.missing(why) else ""


def verdict(failed: int, warned: int, skipped: int) -> int:
    """The run's exit code, from what it's held to (callboard/policy.py's: 0 passed, 1 a failure, 3 only notes, 4 warnings held
    as such); without a callboard.toml, 1 when anything failed. Says the note the board shows (progress.status)."""
    from callboard.progress import counted, status
    for name, why in SKIPS:   # (on the board: its Issues, a notice each; off it, a line each)
        if FEED:
            issue("notice", f"{name} skipped: {why}", fix=skip_fix(why), feed=True)
        else:
            print(f"    skipped: {name}: {why}")
    del SKIPS[:]
    id_ = CONTEXT.get("CALLBOARD_INTEGRATION", "") or "unit"
    if WHOLE[0] and id_ == "unit":   # (what the whole suite was missing here, kept as callboard's own runner keeps it: the Environment block and callboard setup read it)
        from callboard.unittests import keep_missing
        keep_missing(MISSING)
    if not CONFIG:
        return 1 if failed else 0
    try:
        cfg = policy.Config(CONFIG)
        if cfg.integration(id_)["kind"] not in ("tests", "previews", "checks"):   # (another kind's block: the first tests integration's rules)
            id_ = next((i["id"] for i in cfg.integrations(all_=True) if i["kind"] == "tests"), id_)
        r = policy.rules(id_, policy.context(env=CONTEXT, cfg=cfg), cfg)
    except policy.ConfigError as e:
        print(f"    callboard.toml: {e}")
        return 2
    def levels_by(rules) -> tuple[dict, list]:
        held = {k: rules.level(CHECKS_AS.get(k, k) if rules.schema.kind == "checks" else k) for k in ("failures", "warnings", "skipped")}
        return held, [held["failures"]] * failed + [held["warnings"]] * warned + [held["skipped"]] * skipped
    held, levels = levels_by(r)
    rc = policy.exit_code(levels)
    # how every gate holds this same run (callboard's lights: the board's letters, the UI's dots): the counts above,
    # judged again with each gate's rules. (hasattr: a callboard from before the lights has none to tell)
    if FEED and hasattr(policy, "lit"):
        mine = {k: CONTEXT[k] for k in ("CALLBOARD_CODE", "CALLBOARD_CONFIG") if CONTEXT.get(k)}
        was = {k: os.environ.get(k) for k in mine}
        os.environ.update(mine)
        try:
            board_says(policy.lit, id_, lambda rules: policy.exit_code(levels_by(rules)[1]), rc, r)
        finally:
            for k, v in was.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    worst = policy.worst([lv for lv in levels if lv != "off"])
    if worst in ("warn", "info"):
        said = [counted(n, w) for n, w, lv in ((failed, "failure", held["failures"]), (warned, "warning", held["warnings"]), (skipped, "skipped test", held["skipped"])) if n and lv == worst]
        board_says(status, " · ".join(said) if FEED else "")
    return rc
# (dev/future.py's run: the image build's steps, before the tests on the board's one bar — not the tests' own)
BEFORE = int(os.environ.pop("PLEXCACHE_BAR_BEFORE", "") or 0) if FEED else 0
PINS = json.loads(os.environ.get("PLEXCACHE_PINS") or "{}")  # (dev/future.py's run: the app image's pins, to say only what's ahead)

WARNED = [0]   # the warnings this process has printed (count_warnings)


def count_warnings():
    """Counts each warning this process prints, as it prints it (what a test records itself, with catch_warnings or
    assertWarns, isn't printed, nor counted): the warnings of the run's counts."""
    shown = warnings.showwarning

    def showwarning(*args, **kwargs):
        WARNED[0] += 1
        shown(*args, **kwargs)
    warnings.showwarning = showwarning


TIMES = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "plex-cache", "test-times.json")


def ran_on() -> list[str]:
    """On callboard's board, after a run's result: what it ran on, read now (so Current Tests and Future Tests, the same
    suite on this machine and in the next Python's image, show where they differ) — the software the app and its tests
    lean on most, the most first (the board leaves the last off when there's no room): Python, Node (the web's checks),
    MKVToolNix and ffmpeg (the optimizer's tools), OpenSSL (every https and TLS connection). One that isn't there is left
    out. Nothing off the board."""
    if not FEED:
        return []
    import platform
    import ssl

    def tool(cmd: list[str], pattern: str) -> str:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
        got = re.search(pattern, out)
        return got.group(1) if got else ""
    found = [("Python", platform.python_version()), ("Node", tool(["node", "--version"], r"v(\S+)")),
             ("MKVToolNix", tool(["mkvmerge", "--version"], r"mkvmerge v(\S+)")), ("ffmpeg", tool(["ffmpeg", "-version"], r"ffmpeg version (\S+)")),
             ("OpenSSL", (re.search(r"OpenSSL (\S+)", ssl.OPENSSL_VERSION) or [None, ""])[1])]
    return [f"{name} {short_version(version)}" for name, version in found if version and ahead(version, PINS.get(name), bool(PINS))]


def ahead(version: str, pin: str | None, pinned: bool) -> bool:
    """Whether a version is worth naming on the suite's line: always, on a run of its own (``pinned`` False: what this
    machine has); in dev/future.py's image, only when it's ahead of the app image's pin — a pre-release, a nightly or
    snapshot, a newer release — so one the app ships too (MKVToolNix at its pin) is left out, and one with no pin
    (OpenSSL, Debian's) only when it's a pre-release. ``pin`` may be the release itself (103.0) or its line (3.15, 26)."""
    if not pinned:
        return True
    pre = bool(re.search(r"[a-z]", short_version(version)))   # (3.16.0a1, 28.0.0-nightly, snapshot)
    if pin is None:
        return pre
    return pre or not (version == pin or version.startswith(pin + "."))


def _name(test) -> str:
    return f"{type(test).__name__}.{getattr(test, '_testMethodName', '')}"


class ProgressResult(unittest.TextTestResult):
    """A run in this process (-j 1, or one class): unittest's result, drawing the bar as tests finish."""

    def __init__(self, stream, descriptions, verbosity, total: int, tty: bool):
        super().__init__(stream, descriptions, 0)
        self.progress = Progress(total, tty, feed=FEED, bar="tests", tests=True)
        self.progress.before = BEFORE
        self.progress.workers = 1   # (this process alone: on the board, at the bar's right)
        self.on_stop: Callable[..., Any] | None = None   # the SIGTERM handler main() sets, kept in place between tests
        self.drawn: set[str] = set()   # (the tests the bar has counted)

    @property
    def done(self) -> int:
        return self.progress.done

    def _draw(self, test):
        self.drawn.add(test.id())
        self.progress.draw(_name(test), len(self.failures) + len(self.errors), WARNED[0], len(self.skipped))   # (a skip: counted as skipped, never as passed)

    def stopTest(self, test):
        super().stopTest(test)
        # A test whose subtest failed gets no addSuccess/addFailure of its own: it's drawn here, so the bar counts it.
        if isinstance(test, unittest.TestCase) and test.id() not in self.drawn:
            self._draw(test)

    def startTest(self, test):
        # A test can leave its own SIGTERM handler behind (the app's main() sets one); put ours back,
        # so a stop still stops the run.
        if self.on_stop and signal.getsignal(signal.SIGTERM) is not self.on_stop:
            signal.signal(signal.SIGTERM, self.on_stop)
        super().startTest(test)
        if isinstance(test, unittest.TestCase):
            self.progress.show(_name(test), ticking=True)   # (the test under way, its seconds counting)

    def addSuccess(self, test):
        super().addSuccess(test)
        self._draw(test)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._draw(test)

    def addError(self, test, err):
        super().addError(test, err)
        # A setUpClass/tearDownModule error isn't one of the counted tests.
        if isinstance(test, unittest.TestCase):
            self._draw(test)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._draw(test)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._draw(test)

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._draw(test)


# ----------------------------------------------------------------- one process
def _cleanup_module(module: str):
    tests_module = sys.modules.get(module)
    if tests_module is not None and getattr(tests_module, "TMP", None):
        shutil.rmtree(tests_module.TMP, ignore_errors=True)   # what tearDownModule would have removed


def run_here(module: str, suite, total: int, tty: bool, started: float) -> int:
    runner = unittest.TextTestRunner(
        stream=sys.stdout, verbosity=0,
        resultclass=lambda stream, desc, verb: ProgressResult(stream, desc, verb, total, tty))
    # TextTestRunner prints its own summary; keep ours instead.
    result = runner._makeResult()
    assert isinstance(result, ProgressResult)   # (its resultclass's)
    unittest.registerResult(result)

    def stop(*_):
        # Stopped from outside (callboard's Enter): end now, without waiting for the test that's running.
        # That test didn't finish, so it isn't a failure; only the tests that finished are counted.
        if tty:
            sys.stdout.write("\r\033[K")
        bad = len(result.failures) + len(result.errors)
        if bad:
            result.printErrors()
        print(f"\n    {result_line('stopped', tested(result.done, bad, WARNED[0], total, feed=FEED, skipped=len(result.skipped)), f'{total - result.done} not run')}")
        sys.stdout.flush()
        _cleanup_module(module)
        os._exit(130)
    result.on_stop = stop
    signal.signal(signal.SIGTERM, stop)
    result.startTestRun()
    try:
        suite(result)
    finally:
        result.stopTestRun()
    if tty:
        sys.stdout.write("\r\033[K")
    took = time.time() - started
    bad = len(result.failures) + len(result.errors)
    blocks = [f"{result.separator1}\nFAIL: {result.getDescription(t)}\n{result.separator2}\n{text}" for t, text in result.failures]
    blocks += [f"{result.separator1}\nERROR: {result.getDescription(t)}\n{result.separator2}\n{text}" for t, text in result.errors]
    detailed(result.testsRun - bad - len(result.skipped), blocks, WARNED[0], total, took, 1, skipped=len(result.skipped))
    SKIPS.extend((t.id().split(".", 1)[-1], str(why)) for t, why in result.skipped)
    for t, why in result.skipped:
        missed(t.id().split(".", 1)[-1], str(why))
    if bad:
        result.printErrors()
        print(f"\n    {RED if tty else ''}{result_line(tested(result.testsRun, bad, WARNED[0], feed=FEED, skipped=len(result.skipped)), *ran_on())}{OFF if tty else ''}{timed(f'{took:.0f} s', feed=FEED)}")
        return verdict(bad, WARNED[0], len(result.skipped))
    print(f"    {result_line(tested(result.testsRun, 0, WARNED[0], feed=FEED, skipped=len(result.skipped)), *ran_on())}{timed(f'{took:.0f} s', feed=FEED)}")
    return verdict(0, WARNED[0], len(result.skipped))


# ----------------------------------------------------------------- workers
def _class_fixtures(cls) -> bool:
    """Whether the class sets itself up once for all its tests (setUpClass, tearDownClass): its tests then stay together."""
    return any(getattr(cls, n).__func__ is not getattr(unittest.TestCase, n).__func__ for n in ("setUpClass", "tearDownClass"))


def _units(suite) -> list[tuple[str, list[str]]] | None:
    """The suite's tests by class, in the order unittest would run them: [(class, [test ids])]. None when it holds
    something that can't be shared out by id (a module that didn't import, a test that isn't a TestCase)."""
    units: dict[str, list[str]] = {}

    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                if walk(t) is False:
                    return False
            elif isinstance(t, unittest.TestCase) and type(t).__module__ != "unittest.loader":
                cls = type(t)
                # a class set up once for its tests (setUpClass) is one unit; any other, a unit per test, so one long class
                # is shared out too and the run isn't left waiting on it at the end
                key = f"{cls.__module__}.{cls.__qualname__}" if _class_fixtures(cls) else t.id()
                units.setdefault(key, []).append(t.id())
            else:
                return False
        return True
    return list(units.items()) if walk(suite) else None


def _without(suite, classes: set[str]):
    """The suite without the tests of the classes named (by their names alone: WebCheckTests)."""
    kept = unittest.TestSuite()
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            kept.addTest(_without(t, classes))
        elif type(t).__name__ not in classes:
            kept.addTest(t)
    return kept


def _load_times() -> dict:
    try:
        with open(TIMES, encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_times(times: dict):
    try:
        os.makedirs(os.path.dirname(TIMES), exist_ok=True)
        with open(TIMES + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(times, fh)
        os.replace(TIMES + ".tmp", TIMES)
    except OSError:
        pass


def _python() -> list[str]:
    """This interpreter, with the warnings (-W) and -X options this run was started with."""
    args = [sys.executable, *(f"-W{w}" for w in sys.warnoptions)]
    for k, v in getattr(sys, "_xoptions", {}).items():
        args += ["-X", k if v is True else f"{k}={v}"]
    return args


class Worker:
    def __init__(self, module: str):
        to_r, self.to_w = os.pipe()     # classes to run, one per line
        self.from_r, from_w = os.pipe()  # what happened, one JSON line each
        env = dict(os.environ, PLEXCACHE_TEST_WORKER=f"{to_r},{from_w}")
        self.proc = subprocess.Popen([*_python(), os.path.abspath(__file__), "--worker", module], env=env,
                                     stdin=subprocess.DEVNULL, pass_fds=(to_r, from_w))
        os.close(to_r)
        os.close(from_w)
        self.buf = b""
        self.unit: tuple[str, list[str]] | None = None  # the class it's running
        self.reported: set[str] = set()                # its tests that have finished

    def give(self, unit: tuple[str, list[str]] | None):
        self.unit, self.reported = unit, set()
        try:
            if unit is None:
                os.close(self.to_w)
                self.to_w = -1
            else:
                os.write(self.to_w, (json.dumps(unit[1]) + "\n").encode())
        except OSError:
            pass  # (it has gone: its end is read as such)

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


def failure_name(text: str) -> tuple[str, str]:
    """A failure block's kind and test ("FAIL", "tests.test_x.ATests.test_it"), as unittest prints its head."""
    head = next((x for x in text.split("\n") if x.startswith(("FAIL: ", "ERROR: "))), "")
    m = re.match(r"(FAIL|ERROR): (\S+)(?: \(([\w.]+)\))?", head)
    return (m.group(1), m.group(3) or m.group(2)) if m else ("", "")


def detailed(passed: int, failed: list[str], warned: int, total: int, took: float, workers: int, stopped: bool = False,
             times: dict | None = None, skipped: int = 0) -> None:
    """The run's page in the callboard UI (callboard/progress.py's detail): the counts, each failure as unittest printed it, what
    the suite ran on, and the slowest classes (the times run_parallel keeps for its sharing-out)."""
    board_says(detail, {"kind": "tests", "passed": passed, "failed": len(failed), "warnings": warned, "skipped": skipped, "total": total, "not_run": max(0, total - passed - len(failed) - skipped),
            "took": round(took, 1), "workers": workers, "stopped": stopped, "ran_on": ran_on(),
            "failures": [{"kind": failure_name(t)[0], "test": failure_name(t)[1], "text": t} for t in failed],
            "times": sorted(((k, v) for k, v in (times or {}).items()), key=lambda kv: -kv[1])[:25]})


def run_parallel(module: str, units: list, total: int, jobs: int, tty: bool, started: float) -> int:
    times = _load_times()
    # the longest first (last run's times; a class not timed yet, by its number of tests), so none is left to the end
    queue = sorted(units, key=lambda u: -times.get(u[0], len(u[1]) * 0.5))
    progress = Progress(total, tty, feed=FEED, bar="tests", tests=True)
    progress.before = BEFORE
    failures: list[str] = []

    def failed(text: str):
        """A failure, kept for the end; on callboard's board said now too (a "tell" record: the board puts it above itself as
        it comes, not once the run is over), so it isn't printed again at the end there."""
        failures.append(text)
        if FEED:
            # (and in the board's Issues: the test, and the command that runs it alone)
            kind, name = failure_name(text)
            issue = {"level": "error", "text": f"{kind}: {name}" if kind else "a test failed",
                     "fix": f"python3 -m unittest {name}" if name.startswith("tests.") else ""}
            tell(text, feed=True)
            sys.stdout.write(FEED_MARK + json.dumps({"issue": issue}) + "\n")
            sys.stdout.flush()
    workers: list[Worker] = []
    sel = selectors.DefaultSelector()
    unit_started: dict[str, float] = {}
    respawns = 3
    running: dict = {}   # worker -> (the test it's running, when it started)
    started_workers = min(jobs, len(queue))  # (what the summary says: a worker started again in one's place isn't another)
    progress.workers = started_workers   # (on the board: at the bar's right)

    def finish(code: int, stopped: bool = False) -> int:
        for w in workers:
            w.kill()
        if tty:
            sys.stdout.write("\r\033[K")
        bad = len(failures)
        if bad and not FEED:   # (on the board each was said as it came: ``failed``)
            print()
            for block in failures:
                print(block)
        took = time.time() - started
        detailed(progress.done - bad - SKIPPED[0], failures, progress.warned, total, took, started_workers, stopped, times, SKIPPED[0])
        if stopped:
            print(f"\n    {result_line('stopped', tested(progress.done, bad, progress.warned, total, feed=FEED, skipped=SKIPPED[0]), f'{total - progress.done} not run')}")
        elif bad:
            print(f"\n    {RED if tty else ''}{result_line(tested(progress.done, bad, progress.warned, feed=FEED, skipped=SKIPPED[0]), *ran_on())}{OFF if tty else ''}{timed(f'{took:.0f} s', f'{started_workers} workers', feed=FEED)}")
        else:
            print(f"    {result_line(tested(progress.done, 0, progress.warned, feed=FEED, skipped=SKIPPED[0]), *ran_on())}{timed(f'{took:.0f} s', f'{started_workers} workers', feed=FEED)}")
        sys.stdout.flush()
        if stopped:
            return code
        return verdict(bad, progress.warned, SKIPPED[0])

    def stop(*_):
        # callboard's Enter: every worker ends now, mid-test (they have the signal too, as its process group); the
        # tests that were running aren't failures.
        os._exit(finish(130, stopped=True))
    signal.signal(signal.SIGTERM, stop)

    def spawn():
        w = Worker(module)
        workers.append(w)
        sel.register(w.from_r, selectors.EVENT_READ, w)

    for _ in range(started_workers):
        spawn()
    live = len(workers)
    try:
        while live:
            for key, _ in sel.select():
                w: Worker = key.data
                chunk = os.read(w.from_r, 65536)
                if not chunk:  # the worker has ended
                    sel.unregister(w.from_r)
                    os.close(w.from_r)
                    live -= 1
                    code = w.proc.wait()
                    running.pop(w, None)
                    if w.unit is not None:  # mid-class: the tests it hadn't finished are errors, and someone else goes on
                        lost = [t for t in w.unit[1] if t not in w.reported]
                        failed(f"{'=' * 70}\nERROR: {w.unit[0]}\n{'-' * 70}\nThe worker running it ended (exit code {code}) "
                                        f"with {len(lost)} of its tests not finished:\n  " + "\n  ".join(lost) + "\n")
                        for t in lost:
                            progress.draw(t.rsplit(".", 2)[-2] + "." + t.rsplit(".", 1)[-1], len(failures), progress.warned, SKIPPED[0])
                        w.unit = None
                        if queue and respawns:
                            respawns -= 1
                            spawn()
                            live += 1
                    continue
                w.buf += chunk
                while b"\n" in w.buf:
                    line, w.buf = w.buf.split(b"\n", 1)
                    msg = json.loads(line)
                    progress.warned += msg.get("w", 0)   # (the warnings it has printed since it last said)
                    SKIPPED[0] += msg.get("skipped", 0)
                    if msg.get("skipped"):   # (said as it comes on the board, its Issues; off it, at the end)
                        missed(str(msg.get("name") or ""), str(msg.get("why") or ""))
                        if FEED:
                            issue("notice", f"{msg.get('name') or ''} skipped: {msg.get('why') or ''}", fix=skip_fix(str(msg.get("why") or "")), feed=True)
                        else:
                            SKIPS.append((str(msg.get("name") or ""), str(msg.get("why") or "")))
                    if msg["t"] == "want":
                        if w.unit is not None:
                            times[w.unit[0]] = round(time.time() - unit_started[w.unit[0]], 2)
                        unit = queue.pop(0) if queue else None
                        if unit:
                            unit_started[unit[0]] = time.time()
                        w.give(unit)
                    elif msg["t"] == "start":   # (a test under way: the one the bar names, the newest started)
                        running[w] = (msg["name"], time.time())
                        progress.show(msg["name"], ticking=True)
                    elif msg["t"] == "test":
                        if msg.get("text"):
                            failed(msg["text"])
                        if msg.get("id"):
                            w.reported.add(msg["id"])
                            running.pop(w, None)
                            progress.draw(msg["name"], len(failures), progress.warned, SKIPPED[0])
                            if running:   # (another still under way: named again, its seconds from when it started)
                                name, since = max(running.values(), key=lambda r: r[1])
                                progress.show(name, ticking=True, since=since)
                        elif msg.get("text"):   # (an error that isn't a test's — a module's or a class's set-up or tear-down: counted as one more, so a run that fails never shows nothing failed)
                            progress.total += 1
                            progress.draw(next((ln.split(": ", 1)[1].split(" (")[0] for ln in msg["text"].splitlines() if ln.startswith(("ERROR: ", "FAIL: "))), msg["name"]), len(failures), progress.warned, SKIPPED[0])
    except KeyboardInterrupt:
        return finish(130, stopped=True)
    _save_times(times)
    return finish(1 if failures else 0)


def worker(module: str) -> int:
    """A worker: runs the classes it's sent, one at a time, saying how each test went (run_parallel); with --coverage,
    what of plexcache/ it ran, into the run's folder (PLEXCACHE_COVERAGE) once it's done."""
    to_r, from_w = (int(x) for x in os.environ.pop("PLEXCACHE_TEST_WORKER").split(","))
    into = os.environ.pop("PLEXCACHE_COVERAGE", "")
    tracer = coverage.Tracer().start() if into else None
    jobs = os.fdopen(to_r, "rb", buffering=0)
    out = os.fdopen(from_w, "wb", buffering=0)

    told = [0]   # (the warnings it had printed when it last said)

    def say(**msg):
        if WARNED[0] > told[0]:   # (the warnings printed since: the run's count)
            msg["w"], told[0] = WARNED[0] - told[0], WARNED[0]
        out.write((json.dumps(msg) + "\n").encode())

    def stop(*_):
        _cleanup_module(module)
        os._exit(130)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    class Result(unittest.TextTestResult):
        def __init__(self):
            super().__init__(unittest.runner._WritelnDecorator(sys.stderr), True, 0)
            self.said_ids: set[str] = set()   # (the tests said as done)

        def startTest(self, test):
            if signal.getsignal(signal.SIGTERM) is not stop:  # (a test can leave a handler of its own behind)
                signal.signal(signal.SIGTERM, stop)
            super().startTest(test)
            if isinstance(test, unittest.TestCase):
                say(t="start", name=_name(test))

        def _said(self, test, flavour=None, err=None, skipped: int = 0, why: str = ""):
            text = None
            if err is not None:
                text = f"{self.separator1}\n{flavour}: {self.getDescription(test)}\n{self.separator2}\n{self._exc_info_to_string(err, test)}"  # pyright: ignore[reportAttributeAccessIssue] — unittest's own way of saying a failure, not in its stubs
            counted = isinstance(test, unittest.TestCase)  # (a setUpClass/tearDownModule error isn't a test)
            if counted:
                self.said_ids.add(test.id())
            say(t="test", id=test.id() if counted else None, name=_name(test), text=text, skipped=skipped, **({"why": why} if why else {}))

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:   # (a subtest that failed: a failure of its own, said now; the test is counted once it stops)
                flavour = "FAIL" if err[0] is not None and issubclass(err[0], test.failureException) else "ERROR"
                text = f"{self.separator1}\n{flavour}: {self.getDescription(subtest)}\n{self.separator2}\n{self._exc_info_to_string(err, test)}"  # pyright: ignore[reportAttributeAccessIssue] — unittest's own way of saying a failure, not in its stubs
                say(t="test", id=None, name=_name(test), text=text)

        def stopTest(self, test):
            super().stopTest(test)
            # A test whose subtest failed gets no addSuccess/addFailure of its own: said here, so it's counted.
            if isinstance(test, unittest.TestCase) and test.id() not in self.said_ids:
                self.said_ids.add(test.id())
                say(t="test", id=test.id(), name=_name(test))

        def addSuccess(self, test):
            super().addSuccess(test)
            self._said(test)

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self._said(test, "FAIL", err)

        def addError(self, test, err):
            super().addError(test, err)
            self._said(test, "ERROR", err)

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            self._said(test, skipped=1, why=str(reason))

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            self._said(test)

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            self._said(test)

    loader = unittest.TestLoader()

    class Asked(unittest.TestSuite):
        """The classes as they're sent: one suite for the worker's whole life, so the module is set up and torn
        down once (its fake servers, its temporary folder), and each class once."""
        _cleanup = False

        def __iter__(self):
            while True:
                say(t="want")
                line = jobs.readline()
                if not line:
                    return
                yield loader.loadTestsFromNames(json.loads(line))

        def _removeTestAtIndex(self, index):
            pass

    result = Result()
    Asked()(result)
    if tracer is not None:
        with open(os.path.join(into, f"worker-{os.getpid()}.json"), "w", encoding="utf-8") as fh:
            json.dump(tracer.stop(), fh)
    say(t="end")   # (the warnings printed after its last test: the module's teardown's)
    jobs.close()
    out.close()
    return 0


def main() -> int:
    args = sys.argv[1:]
    count_warnings()
    if args[:1] == ["--worker"]:
        return worker(args[1])
    jobs = os.environ.get("PLEXCACHE_TEST_JOBS") or ""
    rest, excluded, measure = [], set(), False
    while args:
        a = args.pop(0)
        if a == "--coverage":
            measure = True
        elif a in ("-j", "--jobs") and args:
            jobs = args.pop(0)
        elif a == "--exclude" and args:
            excluded.add(args.pop(0))
        elif a.startswith("-j") and a[2:].isdigit():
            jobs = a[2:]
        else:
            rest.append(a)
    WHOLE[0] = not rest
    module = rest[0] if rest else "tests.test_plexcache"
    try:
        jobs_n = max(1, int(jobs)) if jobs else 2 * (os.cpu_count() or 1)  # (two per core: most tests wait more than they compute)
    except ValueError:
        print(f"    -j takes a number of workers, not {jobs!r}", file=sys.stderr)
        return 2
    started = time.time()
    if measure:   # (the workers' each into this folder; this process's own, for a run here)
        import tempfile
        into = tempfile.mkdtemp(prefix="plexcache-coverage-")
        os.environ["PLEXCACHE_COVERAGE"] = into
        tracer = coverage.Tracer().start()
        try:
            code = _run(module, jobs_n, excluded, started)
        finally:
            here = tracer.stop()
            os.environ.pop("PLEXCACHE_COVERAGE", None)
        parts = [here]
        for name in os.listdir(into):
            try:
                with open(os.path.join(into, name), encoding="utf-8") as fh:
                    parts.append(json.load(fh))
            except (OSError, ValueError):
                continue
        shutil.rmtree(into, ignore_errors=True)
        if code != 130:   # (a run stopped part way says nothing of what the tests reach)
            coverage.save(coverage.merge(parts), "passed" if code == 0 else "failed")
        return code
    return _run(module, jobs_n, excluded, started)


def _run(module: str, jobs_n: int, excluded: set[str], started: float) -> int:
    suite = unittest.defaultTestLoader.loadTestsFromName(module)
    if excluded:
        suite = _without(suite, excluded)
    total = suite.countTestCases()
    tty = drawn(sys.stdout)
    for k in BOARD_KEYS:   # (this run's, not the tests': one that runs tests/run.py itself says its own)
        os.environ.pop(k, None)
    for k in CONTEXT_KEYS:   # (callboard's context: this run's, taken above — not the tests')
        os.environ.pop(k, None)
    units = _units(suite) if jobs_n > 1 else None
    if not units or len(units) < 2:
        return run_here(module, suite, total, tty, started)
    del suite  # (the workers load their own)
    # what importing the module set up here (its fake servers, its temporary folder) is the workers' now: taken down
    for name in {u[1][0].rsplit(".", 2)[0] for u in units}:  # (a test's id: module.Class.test)
        mod = sys.modules.get(name)
        try:
            getattr(mod, "tearDownModule", lambda: None)()
        except Exception:  # noqa: BLE001 — (the workers' runs say what's wrong)
            pass
        _cleanup_module(name)
    return run_parallel(module, units, total, jobs_n, tty, started)


if __name__ == "__main__":
    from dev import cli  # (dev/cli.py: what every script's command line does the same)
    _answered = cli.front(sys.argv[1:], __doc__, "python3 tests/run.py", options=("--coverage", "-j=", "--jobs=", "--exclude=", "--worker="))
    sys.exit(main() if _answered is None else _answered)
