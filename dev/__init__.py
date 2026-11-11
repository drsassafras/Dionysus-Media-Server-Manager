"""Plex Cache's own dev tools (the dependency check, the style guide, the future tests, the translations, the release
checks…), built on callboard: the board, the rules (callboard.toml), the check tools' shared code and the callboard UI,
which are a project of their own (~/Code/callboard), installed once on a computer and used by every project.

Importing ``dev`` puts that install on Python's path, so ``from callboard import policy`` works wherever these tools and
the tests are run from (on callboard's board it's there already). The install is, in this order: CALLBOARD_HOME (callboard
sets it for every block it runs; GitHub's workflows set it to their checkout), where the ``callboard`` on your PATH
really is, or a ``callboard`` checkout beside this one.
"""

import os
import shutil
import sys


def _is_home(path: str) -> bool:
    return bool(path) and os.path.isfile(os.path.join(path, "callboard", "policy.py"))


def callboard_home() -> str:
    """The callboard install, or "" when there's none on this computer."""
    named = os.environ.get("CALLBOARD_HOME", "")
    if _is_home(named):
        return os.path.realpath(named)
    script = shutil.which("callboard")
    if script:
        home = os.path.dirname(os.path.dirname(os.path.realpath(script)))   # (<home>/bin/callboard, through any symlink)
        if _is_home(home):
            return home
    beside = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "callboard")
    return os.path.realpath(beside) if _is_home(beside) else ""


HOME = callboard_home()
if not HOME:
    raise ImportError("callboard isn't installed here: Plex Cache's dev tools and tests are built on it. Check it out beside "
                      "this project (git clone https://github.com/drsassafras/callboard ../callboard) and put its "
                      "bin/callboard on your PATH, or set CALLBOARD_HOME to the checkout (dev/README.md).")
if HOME not in sys.path:
    sys.path.insert(0, HOME)
