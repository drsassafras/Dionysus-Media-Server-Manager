"""Command line checks (a shared test from MyStandards: clichecks): runs clichecks.py on the commands ../clichecks.json
names, as part of the project's own tests, each check a test of its own (test_<check>). A check is left out only where
the project's callboard.toml says why ([templates.clichecks.checks] <check> = "<reason>"; all of them:
[templates.clichecks] opt_out = "<reason>"). A copy of the template, kept in step with it: never edit it here."""

import importlib.util
import os
import shutil
import tomllib
import unittest

try:   # (run by callboard's Unit Tests: a need not met is said as missing, not skipped)
    from callboard.needs import needs
except ImportError:
    needs = None

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(os.path.dirname(HERE), "clichecks.json")
ROOT = os.path.dirname(os.path.dirname(HERE))
NAME = "clichecks"

_spec = importlib.util.spec_from_file_location("callboard_clichecks_engine", os.path.join(HERE, "clichecks.py"))
assert _spec is not None and _spec.loader is not None, "clichecks.py, beside this test, can't be loaded"
clichecks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clichecks)

with open(os.path.join(HERE, "template.toml"), "rb") as _fh:
    CHECKS = tomllib.load(_fh)["checks"]   # (each check and what it checks: the template's own list)


def _opt_outs() -> tuple[str | None, dict]:
    """What the project's callboard.toml leaves out, and why: (the whole template's reason, {check: reason})."""
    try:
        with open(os.path.join(ROOT, "callboard.toml"), "rb") as fh:
            got = (tomllib.load(fh).get("templates") or {}).get(NAME) or {}
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return None, {}
    reason = got.get("opt_out") if isinstance(got, dict) else None
    said = got.get("checks") if isinstance(got, dict) else None
    return reason, said if isinstance(said, dict) else {}


_COMMANDS: dict = {}


def _commands(test: unittest.TestCase) -> list:
    """The project's commands, each made once for every test here (so a line asked of one is run once): the test
    failed where the settings can't be read or name none, and skipped (missing, under callboard) where what runs a
    command isn't on this computer."""
    if "got" not in _COMMANDS:
        try:
            _COMMANDS["got"] = [clichecks.Command(spec, ROOT) for spec in clichecks.load(SETTINGS)]
        except clichecks.Unrunnable as e:
            _COMMANDS["got"] = str(e)
    got = _COMMANDS["got"]
    if isinstance(got, str):
        test.fail(got)
    if not got:
        test.fail(f"{os.path.relpath(SETTINGS, ROOT)} names no commands: name the ones this project gives people, or opt out in "
                  f"callboard.toml ([templates.{NAME}] opt_out = \"<why>\")")
    for c in got:
        exe = c.needs()
        if os.path.isabs(exe):
            if not os.path.isfile(exe):
                test.fail(f"{os.path.relpath(SETTINGS, ROOT)}: {c.name} is run by {os.path.relpath(exe, ROOT)}, which isn't in the project")
        elif needs is not None:
            needs(command=exe)()
        elif not shutil.which(exe):
            test.skipTest(f"the command line checks need {exe} to run {c.name}")
    return got


def _check(name: str):
    def test(self):
        reason, said = _opt_outs()
        if isinstance(reason, str) and reason.strip():
            self.skipTest(f"opted out in callboard.toml: {reason.strip()}")
        if name in said:
            if not (isinstance(said[name], str) and said[name].strip()):
                self.fail(f"callboard.toml [templates.{NAME}.checks] {name}: left out with no reason: say why")
            self.skipTest(f"opted out in callboard.toml: {said[name].strip()}")
        if not os.path.isfile(SETTINGS):
            self.fail(f"no {os.path.relpath(SETTINGS, ROOT)}: name the project's commands there, or opt out in callboard.toml "
                      f"([templates.{NAME}] opt_out = \"<why>\")")
        found = []
        if name in clichecks.PROJECT_CHECKS:   # (asked of the project as a whole: nothing is run for it)
            try:
                found = getattr(clichecks, name)(ROOT, clichecks.load(SETTINGS))
            except clichecks.Unrunnable as e:
                self.fail(str(e))
            self.assertEqual(found, [], "\n".join(found))
            return
        for c in _commands(self):
            try:
                found += getattr(clichecks, name)(c)
            except clichecks.Unrunnable as e:
                self.fail(str(e))
        self.assertEqual(found, [], "\n".join(found))
    test.__name__ = f"test_{name}"
    test.__doc__ = CHECKS[name]
    return test


class CommandLineChecks(unittest.TestCase):
    """The command line checks on the project's commands, a test each."""

    def test_the_checks_are_the_templates(self):
        """Every check the template lists is one clichecks.py makes, and the other way round."""
        self.assertEqual(sorted(CHECKS), sorted(clichecks.CHECKS))


for _name in CHECKS:
    setattr(CommandLineChecks, f"test_{_name}", _check(_name))


if __name__ == "__main__":
    unittest.main()
