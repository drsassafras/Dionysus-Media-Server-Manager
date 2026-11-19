"""What every dev script does the same on its command line, so each answers as a command line is expected to (GNU's
options, POSIX's exit codes: the Command line checks hold them, tests/callboard_clichecks):

    -h, --help      the script's usage, on stdout: exit 0
    --version       the script's name and the project's version, on stdout: exit 0
    an option or a subcommand it doesn't have: said on stderr, nothing on stdout, exit 2

A script calls ``front`` first in its main, with its own docstring (its usage is the docstring's first paragraph and
its command lines, the ones indented and starting ``python3 dev/<script>``), and goes on when it gives None:

    rc = cli.front(sys.argv[1:], __doc__, "python3 dev/versions.py", options=("--json",))
    if rc is not None:
        return rc
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MISUSE = 2


def version() -> str:
    """The project's version (plexcache/__init__.py's __version__), read, never imported."""
    try:
        with open(os.path.join(ROOT, "plexcache", "__init__.py"), encoding="utf-8") as fh:
            m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', fh.read(), re.M)
    except OSError:
        m = None
    return m.group(1) if m else "unknown"


def usage(doc: str | None, prog: str) -> str:
    """A script's usage, from its docstring: what it is (the first paragraph), then each of its command lines (and
    the lines that carry one's comment on), then the two every script has."""
    lines = (doc or "").strip("\n").splitlines()
    about = []
    for line in lines:
        if not line.strip():
            break
        about.append(line.strip())
    out, keep = [], False
    for line in lines:
        text = line.strip()
        if line.startswith("    ") and text.startswith(prog):
            keep = True
            out.append("  " + text)
        elif keep and line.startswith("    ") and text.startswith("#"):
            out.append("  " + " " * (out[-1].find("#") - 2 if "#" in out[-1] else len(prog) + 2) + text)
        else:
            keep = False
    if not out:
        out.append(f"  {prog}")
    width = max(len(prog) + 14, 20)
    out += [f"  {prog + ' -h, --help':<{width}}   # this help", f"  {prog + ' --version':<{width}}   # the version"]
    return "\n".join([" ".join(about), "", *out])


def front(argv: list[str], doc: str | None, prog: str, options: tuple[str, ...] = (), commands: tuple[str, ...] | None = None) -> int | None:
    """Help and the version answered, and a command line the script can't read refused (0 or 2); None where it's the
    script's to go on with. ``options`` are the options it takes (one written ``--port=`` takes a value: ``--port 8650``
    or ``--port=8650``, a short one ``-j 4`` or ``-j4``, and the value isn't looked at); ``commands`` its subcommands (None: it has none, and what isn't an option is its own to read). What follows ``--`` isn't looked at."""
    args = argv[:argv.index("--")] if "--" in argv else argv
    if "-h" in args or "--help" in args or (commands is not None and args[:1] == ["help"]):
        print(usage(doc, prog))
        return 0
    if "--version" in args:
        print(f"{prog.split()[-1]} {version()}")
        return 0
    takes = {o[:-1] for o in options if o.endswith("=")}
    value = False
    for a in args:
        if value:   # (an option's value, whatever it starts with)
            value = False
            continue
        value = a in takes
        if a[:2] in takes and not a.startswith("--") and len(a) > 2:   # (-j4: a short option with its value written on it)
            value = False
            continue
        if a.startswith("-") and a != "-" and a.split("=", 1)[0] not in takes and a not in options:
            print(f"{prog.split()[-1]}: unknown option: {a} (see {prog} --help)", file=sys.stderr)
            return MISUSE
    if commands is not None and args and not args[0].startswith("-") and args[0] != "help" and args[0] not in commands:
        print(f"{prog.split()[-1]}: unknown command: {args[0]} (one of: {', '.join(commands)}; see {prog} --help)", file=sys.stderr)
        return MISUSE
    return None
