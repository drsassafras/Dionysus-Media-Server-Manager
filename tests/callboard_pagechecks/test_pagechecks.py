"""Page checks (a shared test from MyStandards: pagechecks): runs pagechecks.js on the pages ../pagechecks.json names, as
part of the project's own tests, each check a test of its own (test_<check>). A check is left out only where the
project's callboard.toml says why ([templates.pagechecks.checks] <check> = "<reason>"; all of them: [templates.pagechecks]
opt_out = "<reason>"): left out any other way (pagechecks.json's "probes" or "skip"), it fails. What the checks pass
over on every page is said there too, each with why ([templates.pagechecks.leave_out] "<CSS selector>" = "<reason>").
A copy of the template,
kept in step with it: never edit it here."""

import json
import os
import shutil
import subprocess
import tomllib
import unittest

try:   # (run by callboard's Unit Tests: its pinned Playwright, and a need not met said as missing, not skipped)
    from callboard import checks
    from callboard.needs import needs
except ImportError:
    checks = needs = None

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(os.path.dirname(HERE), "pagechecks.json")
ROOT = os.path.dirname(os.path.dirname(HERE))
NAME = "pagechecks"

with open(os.path.join(HERE, "template.toml"), "rb") as _fh:
    CHECKS = tomllib.load(_fh)["checks"]   # (each check and what it checks: the template's own list)


def _leave_out() -> dict:
    """What the checks pass over on every page, and why: callboard.toml's [templates.pagechecks.leave_out]."""
    try:
        with open(os.path.join(ROOT, "callboard.toml"), "rb") as fh:
            got = ((tomllib.load(fh).get("templates") or {}).get(NAME) or {}).get("leave_out")
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return {}
    return got if isinstance(got, dict) else {}


def _opt_outs() -> tuple[str | None, dict]:
    """What the project's callboard.toml leaves out, and why: (the whole template's reason, {check: reason})."""
    try:
        with open(os.path.join(ROOT, "callboard.toml"), "rb") as fh:
            got = (tomllib.load(fh).get("templates") or {}).get(NAME) or {}
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return None, {}
    if not isinstance(got, dict):
        return None, {}
    reason, said = got.get("opt_out"), got.get("checks")
    return (reason if isinstance(reason, str) or reason is None else ""), (said if isinstance(said, dict) else {})


def _plan() -> dict:
    with open(SETTINGS, encoding="utf-8") as fh:
        got = json.load(fh)
    if not isinstance(got, dict):
        raise ValueError("not a JSON object")
    return got


_RAN: dict = {}


def _run(test: unittest.TestCase) -> dict:
    """pagechecks.js run once for every test here (each reads its own check's findings): {"findings": […]}, or the
    test skipped or failed as the run couldn't be made."""
    if "got" in _RAN:
        got = _RAN["got"]
    else:
        got = _RAN["got"] = _launch()
    if got.get("skip"):
        test.skipTest(got["skip"])
    if got.get("fail"):
        test.fail(got["fail"])
    return got


def _launch() -> dict:
    env = dict(os.environ)
    try:
        pages = _plan().get("pages")
    except (OSError, ValueError):
        pages = None   # (pagechecks.js says what's wrong with it)
    if needs is not None and checks is not None:
        needs(command="node")()
        folder = checks.own_packages("playwright", ROOT) if pages else ""   # (the project's own, in its root or its dev folder)
        if pages and not folder:   # (none of the project's own: callboard's, at its pin)
            needs(tool="pages")()
            folder, why = checks.packages("pages")
            if folder is None:
                return {"fail": f"the page checks didn't run: {why}"}
        if folder:
            env["NODE_PATH"] = os.pathsep.join(p for p in (os.path.join(folder, "node_modules"), env.get("NODE_PATH", "")) if p)
    elif not shutil.which("node"):
        return {"skip": "the page checks need Node"}
    _, said = _opt_outs()
    left = ",".join(k for k, v in said.items() if isinstance(v, str) and v.strip())
    over = [f"--leave-out={k}" for k, v in _leave_out().items() if isinstance(v, str) and v.strip()]
    got = subprocess.run(["node", os.path.join(HERE, "pagechecks.js"), SETTINGS, "--json", *([f"--skip={left}"] if left else []), *over],
                         capture_output=True, text=True, timeout=1800, env=env, cwd=ROOT)
    try:
        said_back = json.loads(got.stdout)
    except ValueError:
        return {"fail": f"the page checks didn't run: {got.stderr.strip() or got.stdout.strip()}"}
    if said_back.get("error"):
        err = said_back["error"]
        if "playwright" in err.lower() and ("cannot find" in err.lower() or "install" in err.lower()):
            return {"skip": f"the page checks need Playwright: {err}"}
        return {"fail": f"the page checks didn't run: {err}"}
    return said_back


def _check(name: str):
    def test(self):
        reason, said = _opt_outs()
        if isinstance(reason, str) and reason.strip():
            self.skipTest(f"opted out in callboard.toml: {reason.strip()}")
        if name in said:
            if not (isinstance(said[name], str) and said[name].strip()):
                self.fail(f"callboard.toml [templates.{NAME}.checks] {name}: left out with no reason: say why")
            self.skipTest(f"opted out in callboard.toml: {said[name].strip()}")
        for what, why in _leave_out().items():
            if not (isinstance(why, str) and why.strip()):
                self.fail(f"callboard.toml [templates.{NAME}.leave_out] {what}: passed over with no reason: say why")
        if not os.path.isfile(SETTINGS):
            self.fail(f"no {os.path.relpath(SETTINGS, ROOT)}: name the pages to check there, or opt out in callboard.toml "
                      f"([templates.{NAME}] opt_out = \"<why>\")")
        try:
            plan = _plan()
        except (OSError, ValueError) as e:
            raise self.failureException(f"{os.path.relpath(SETTINGS, ROOT)}: {e}") from e
        only, skip = plan.get("probes"), plan.get("skip") or []
        if (isinstance(only, list) and name not in only) or name in skip:
            self.fail(f"{os.path.relpath(SETTINGS, ROOT)} leaves {name} out ({'skip' if name in skip else 'probes'}) with no reason: "
                      f"say why in callboard.toml [templates.{NAME}.checks] {name} = \"<why>\", or let it run")
        if not plan.get("pages"):
            self.fail(f"{os.path.relpath(SETTINGS, ROOT)} names no pages: name them, or opt out in callboard.toml "
                      f"([templates.{NAME}] opt_out = \"<why>\")")
        got = _run(self)
        errors = [f["message"] for f in got.get("findings", []) if f.get("code") == name and f.get("severity") == "error"]
        self.assertEqual(errors, [], "\n".join(errors))   # (a warning is said by callboard, never here)
    test.__name__ = f"test_{name}"
    test.__doc__ = CHECKS[name]
    return test


class PageChecks(unittest.TestCase):
    """The page checks on the project's pages, a test each: an error found fails; a warning is said by callboard, never here."""


for _name in CHECKS:
    setattr(PageChecks, f"test_{_name}", _check(_name))


if __name__ == "__main__":
    unittest.main()
