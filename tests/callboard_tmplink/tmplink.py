"""Temp folders through a symlink (a MyStandards template: tmplink; callboard copies it to tests/callboard_tmplink/).
Import it first in a project's tests, before anything makes a temp folder:

    import callboard_tmplink.tmplink  # noqa: F401  (the tests' temp folders are reached through a symlink)

The tests' temp folder is then reached through a symlink on every system, as a Mac's own is (/var is /private/var): a
path said as given where one as it really is was meant (or the other way round) fails there, on Linux and GitHub too,
and not only on a Mac. Every temp folder the tests make (tempfile's, and a program's TMPDIR) is in it; it's removed
when the tests end. A copy of the template, kept in step with it: never edit it here."""

import atexit
import os
import shutil
import tempfile

BASE = tempfile.mkdtemp(prefix="tmplink-")
REAL = os.path.join(BASE, "real")
TMP = os.path.join(BASE, "link")
os.mkdir(REAL)
os.symlink(REAL, TMP)
tempfile.tempdir = TMP
for _k in ("TMPDIR", "TMP", "TEMP"):
    os.environ[_k] = TMP
atexit.register(shutil.rmtree, BASE, True)
