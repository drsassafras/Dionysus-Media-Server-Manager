"""Page checks (a callboard template: pagechecks): runs pagechecks.js on the pages ../pagechecks.json names, as part of
the project's own tests. A copy of callboard's template, kept in step with it: never edit it here."""

import json
import os
import shutil
import subprocess
import unittest

try:   # (run by callboard's Unit Tests: its pinned Playwright, and a need not met said as missing, not skipped)
    from callboard import checks
    from callboard.needs import needs
except ImportError:
    checks = needs = None

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(os.path.dirname(HERE), "pagechecks.json")
ROOT = os.path.dirname(os.path.dirname(HERE))


class PageChecks(unittest.TestCase):
    """The page checks on the project's pages: an error found fails; a warning is said by callboard, never here."""

    def test_the_pages_behave(self):
        """Each page pagechecks.json names, used as a person would: nothing found that's an error."""
        if not os.path.isfile(SETTINGS):
            self.skipTest(f"no {os.path.relpath(SETTINGS)}: it names the pages to check")
        env = dict(os.environ)
        if needs is not None and checks is not None:
            needs(command="node")()
            try:
                with open(SETTINGS, encoding="utf-8") as fh:
                    pages = json.load(fh).get("pages")
            except (ValueError, AttributeError):
                pages = None   # (pagechecks.js says what's wrong with it)
            if pages and not os.path.isfile(os.path.join(ROOT, "node_modules", "playwright", "package.json")):   # (none of the project's own: callboard's, at its pin)
                needs(tool="pages")()
                folder, why = checks.packages("pages")
                if folder is None:
                    self.fail(f"the page checks didn't run: {why}")
                env["NODE_PATH"] = os.pathsep.join(p for p in (os.path.join(folder, "node_modules"), env.get("NODE_PATH", "")) if p)
        elif not shutil.which("node"):
            self.skipTest("the page checks need Node")
        got = subprocess.run(["node", os.path.join(HERE, "pagechecks.js"), SETTINGS, "--json"], capture_output=True, text=True, timeout=1800, env=env)
        try:
            said = json.loads(got.stdout)
        except ValueError:
            self.fail(f"the page checks didn't run: {got.stderr.strip() or got.stdout.strip()}")
        if said.get("error"):
            if "playwright" in said["error"].lower() and ("cannot find" in said["error"].lower() or "install" in said["error"].lower()):
                self.skipTest(f"the page checks need Playwright: {said['error']}")
            self.fail(f"the page checks didn't run: {said['error']}")
        errors = [f["message"] for f in said.get("findings", []) if f.get("severity") == "error"]
        self.assertEqual(errors, [], "\n".join(errors))


if __name__ == "__main__":
    unittest.main()
