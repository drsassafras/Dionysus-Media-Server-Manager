"""Command line checks (a shared test from MyStandards: clichecks): each command a project gives people, held to what
a command line is expected to do everywhere, so my projects can't drift from it or from each other. Three standards,
one on top of the other:

    POSIX Utility Syntax Guidelines     short options: one dash, one letter (-n)
    GNU Coding Standards, 4.8           long options (--dry-run), --help and --version on every program, GNU's table
                                        of option names so one idea has one name
    Command Line Interface Guidelines   subcommands as a verb, or a group and a verb (git, docker); help on stdout
    (clig.dev)                          and exit 0 when asked for, a mistake said on stderr; --json for a program

A command is run for real (its --help, its --version, an option and a subcommand it can't have), and what its help
says it takes is read: every --long-option in it, each short option written beside its long one ("-n, --dry-run"),
and the subcommands on the lines that start with its name ("  ghostlight check …"). A command is anything of the
project's that's run from a shell, its scripts too: one that's run through a program is named as it's typed ("python3
dev/release.py", "node dev/web.js"). The names below are the standard:
they're the same in every project, which is the point. A copy of the template, kept in step with it: never edit it
here. What's the project's own is ../clichecks.json:

    {"commands": [{"name": "callboard", "run": ["bin/callboard"]},
                  {"name": "python3 dev/release.py", "run": ["{python}", "dev/release.py"]},
                  {"name": "ghostlight", "build": ["go", "build", "-o", "{out}", "./cmd/ghostlight"], "run": ["{out}"]}]}

A command that has to be built first says how ("build": {out} is a file in a temp folder, made once and removed after;
never `go run`, which answers every failure with exit 1). {python} is the Python the checks themselves are run with.

    python3 clichecks.py ../clichecks.json [--json]     # the findings, a line each (exit 1 when there are any)
    python3 clichecks.py -h, --help | --version
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# The option names: GNU's name for the idea, the short option that goes with it ("" where none is the custom), and
# what else it has been called (here or elsewhere), which is refused.
FLAGS: dict[str, tuple[str, list[str]]] = {
    "--help": ("-h", ["--usage"]),
    "--version": ("-V", ["--ver"]),
    "--dry-run": ("-n", ["--dry", "--dryrun", "--dry_run", "--simulate", "--pretend", "--no-act", "--noop", "--no-op", "--what-if"]),
    "--force": ("-f", ["--anyway", "--forced"]),
    "--output": ("-o", ["--out", "--outfile", "--out-file", "--output-file"]),
    "--yes": ("-y", ["--assume-yes", "--no-confirm", "--noconfirm"]),
    "--quiet": ("-q", ["--no-output"]),
    "--verbose": ("-v", ["--chatty"]),
    "--json": ("", ["--as-json", "--json-output", "--output-json"]),
    "--no-browser": ("", ["--no-open", "--nobrowser", "--no-launch"]),
}
# The subcommands every project of mine calls the same: the name, and what else it has been called.
COMMANDS: dict[str, list[str]] = {
    "ui": ["gui", "webui", "web-ui"],
    "init": ["initialize", "initialise"],
    "version": ["ver"],
}
CHECKS = ("help_flag", "help_command", "version_flag", "unknown_option", "unknown_command", "long_flags", "flag_names",
          "short_flags", "command_names", "lookalike_commands", "all_listed")
PROJECT_CHECKS = ("all_listed",)   # (asked of the project and its settings as a whole, not of each command)
# What can be run from a shell, found in the project's files (all_listed): a Python file with a main block or named
# __main__.py, a script node runs that reads its command line, a Go program (package main), and any other file that's
# executable and starts with #!. Never looked in: what isn't the project's own code.
NOT_OURS = {"node_modules", "__pycache__", "venv", "env", "vendor", "third_party", "dist", "build", "testdata", "fixtures", "site-packages"}
PY_MAIN = re.compile(r"""^if\s+__name__\s*==\s*["']__main__["']\s*:""", re.M)
JS_MAIN = re.compile(r"\bprocess\.argv\b|\brequire\.main\s*===\s*module\b")
GO_MAIN = re.compile(r"^package\s+main\b", re.M)
NO_SUCH = "no-such-thing-clichecks"   # (an option, and a subcommand, no command has)
LONG = re.compile(r"(?<![\w-])--[A-Za-z0-9][\w-]*")
KEBAB = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*\Z")
PAIR = re.compile(r"(?<![\w-])(-[A-Za-z])(?:\s*,\s*|\s*\|\s*|/)(--[A-Za-z0-9][\w-]*)")


class Unrunnable(Exception):
    """A command that couldn't be started (not built, not installed), or the settings can't be read."""


def load(settings: str) -> list[dict]:
    """The project's commands, from its clichecks.json: each a name and how it's run (from the project's folder)."""
    try:
        with open(settings, encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError) as e:
        raise Unrunnable(f"{settings}: {e}") from None
    cmds = got.get("commands") if isinstance(got, dict) else None
    if not isinstance(cmds, list):
        raise Unrunnable(f'{settings}: {{"commands": [{{"name": "<command>", "run": ["<how>", "<it\'s run>"]}}]}}')
    for c in cmds:
        if not (isinstance(c, dict) and isinstance(c.get("name"), str) and c["name"] and not set(c) - {"name", "run", "build"}
                and all(isinstance(c.get(k, [""]), list) and c.get(k, [""]) and all(isinstance(a, str) for a in c.get(k, [""])) for k in ("run", "build"))
                and "run" in c):
            raise Unrunnable(f'{settings}: each command is {{"name": "<command>", "run": ["<how>", "<it\'s run>"]}} (and "build": […], where '
                             f"it's built first): {c!r}")
    return cmds


class Command:
    """One command, run from the project's folder; each line asked of it is run once."""

    def __init__(self, spec: dict, root: str):
        self.name, self.root = spec["name"], root
        self.argv = [sys.executable if a == "{python}" else a for a in spec["run"]]
        self.build, self.built = [sys.executable if a == "{python}" else a for a in spec.get("build") or []], ""
        if self.argv[0] != "{out}" and (os.sep in self.argv[0] or "/" in self.argv[0]):   # (a file of the project's, not one on the PATH)
            self.argv[0] = os.path.join(root, self.argv[0])
        self._ran: dict[tuple, tuple[int, str, str]] = {}

    def needs(self) -> str:
        """The program that has to be on this computer for the command to be run at all (what builds it, or runs it)."""
        return (self.build or self.argv)[0]

    def _build(self) -> None:
        """The command built, once, into a temp folder that's removed when the tests end."""
        folder = tempfile.mkdtemp(prefix="clichecks-")
        atexit.register(shutil.rmtree, folder, True)
        out = os.path.join(folder, re.sub(r"[^\w.-]+", "-", self.name))
        try:
            got = subprocess.run([a.replace("{out}", out) for a in self.build], cwd=self.root, capture_output=True, text=True, timeout=900,
                                 stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Unrunnable(f"{self.name}: {' '.join(self.build)} couldn't be run: {e}") from None
        if got.returncode != 0 or not os.path.isfile(out):
            raise Unrunnable(f"{self.name}: {' '.join(self.build)} didn't build it: {_said(got.stderr or got.stdout)}")
        self.built = out
        self.argv = [a.replace("{out}", out) for a in self.argv]

    def run(self, *args: str) -> tuple[int, str, str]:
        if self.build and not self.built:
            self._build()
        if args not in self._ran:
            try:
                got = subprocess.run([*self.argv, *args], cwd=self.root, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired) as e:
                raise Unrunnable(f"{self.name}: {' '.join([*self.argv, *args])} couldn't be run: {e}") from None
            self._ran[args] = (got.returncode, got.stdout, got.stderr)
        return self._ran[args]

    def help(self) -> str:
        return self.run("--help")[1]

    def flags(self) -> list[str]:
        """Every long option its help names, once each, in the order they come."""
        return list(dict.fromkeys(LONG.findall(self.help())))

    def pairs(self) -> list[tuple[str, str]]:
        """Each short option its help writes beside a long one: ("-n", "--dry-run")."""
        return list(dict.fromkeys(PAIR.findall(self.help())))

    def subcommands(self) -> list[str]:
        """Its subcommands: the word after its name on the lines of its help that start with it (a name of several
        words, "python3 dev/audit.py", is all of them), and, where the rest of such a line is words between bars
        ("ghostlight findings | runs | version"), each of those."""
        out: list[str] = []
        named = self.name.split()
        for line in self.help().splitlines():
            words = line.split()
            if len(words) <= len(named) or words[:len(named)] != named or not re.match(r"[A-Za-z][\w-]*\Z", words[len(named)]):
                continue
            rest = words[len(named):]
            if len(rest) >= 3 and all(w == "|" for w in rest[1::2]) and all(re.match(r"[A-Za-z][\w-]*\Z", w) for w in rest[0::2]):
                out += rest[0::2]
            else:
                out.append(rest[0])
        return list(dict.fromkeys(out))


def _said(text: str) -> str:
    lines = [x for x in text.strip().splitlines() if x.strip()]
    return lines[0].strip()[:120] if lines else "nothing"


def _asked_for(c: Command, *args: str) -> list[str]:
    """What's wrong with an answer that was asked for (help, the version): stdout, exit 0, nothing on stderr."""
    rc, out, err = c.run(*args)
    line = f"{c.name} {' '.join(args)}"
    bad = []
    if rc != 0:
        bad.append(f"{line}: exit {rc}, not 0 (it was asked for: it's the answer, not a mistake)")
    if not out.strip():
        bad.append(f"{line}: nothing on stdout (said on stderr: {_said(err)})")
    elif err.strip():
        bad.append(f"{line}: it says something on stderr too ({_said(err)})")
    return bad


def _refused(c: Command, what: str, *args: str) -> list[str]:
    """What's wrong with how a mistake is refused: exit 2, said on stderr, nothing on stdout."""
    rc, out, err = c.run(*args)
    line = f"{c.name} {' '.join(args)}"
    bad = []
    if rc != 2:
        bad.append(f"{line}: {what} exits {rc}, not 2")
    if not err.strip():
        bad.append(f"{line}: {what} isn't said on stderr")
    if out.strip():
        bad.append(f"{line}: {what} puts something on stdout ({_said(out)}), where a program reading it would take it for the answer")
    return bad


def help_flag(c: Command) -> list[str]:
    bad = _asked_for(c, "--help") + _asked_for(c, "-h")
    if not bad and c.run("-h")[1] != c.run("--help")[1]:
        bad.append(f"{c.name}: -h and --help don't say the same")
    return bad


def help_command(c: Command) -> list[str]:
    if not c.subcommands():
        return []
    bad = _asked_for(c, "help")
    if not bad and c.run("help")[1] != c.help():
        bad.append(f"{c.name} help: it doesn't say what {c.name} --help says")
    return bad


def version_flag(c: Command) -> list[str]:
    bad = _asked_for(c, "--version")
    out = c.run("--version")[1].strip()
    if not bad and len(out.splitlines()) != 1:
        bad.append(f"{c.name} --version: one line, the version, not {len(out.splitlines())} lines")
    if "--version" not in c.flags():
        bad.append(f"{c.name} --help doesn't name --version")
    return bad


def unknown_option(c: Command) -> list[str]:
    return _refused(c, "an option it doesn't have", "--" + NO_SUCH)


def unknown_command(c: Command) -> list[str]:
    return _refused(c, "a subcommand it doesn't have", NO_SUCH) if c.subcommands() else []


def long_flags(c: Command) -> list[str]:
    return [f"{c.name}: {f} isn't lowercase words joined by hyphens (--{re.sub(r'[_]+', '-', re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '-', f[2:])).lower()})"
            for f in c.flags() if not KEBAB.match(f[2:])]


def flag_names(c: Command) -> list[str]:
    was = {old: name for name, (_, olds) in FLAGS.items() for old in olds}
    return [f"{c.name}: {f} is {was[f]} everywhere else" + (f" ({FLAGS[was[f]][0]}, {was[f]})" if FLAGS[was[f]][0] else "")
            for f in c.flags() if f in was]


def short_flags(c: Command) -> list[str]:
    short_of = {name: short for name, (short, _) in FLAGS.items() if short}
    long_of = {short: name for name, short in short_of.items()}
    bad = []
    for short, name in c.pairs():
        if name in short_of and short != short_of[name]:
            bad.append(f"{c.name}: {short}, {name}: the short option for {name} is {short_of[name]}")
        elif short in long_of and name != long_of[short]:
            bad.append(f"{c.name}: {short}, {name}: {short} is {long_of[short]} everywhere else")
    return bad


def command_names(c: Command) -> list[str]:
    was = {old: name for name, olds in COMMANDS.items() for old in olds}
    bad = [f"{c.name} {s}: a subcommand is lowercase words joined by hyphens" for s in c.subcommands() if not KEBAB.match(s)]
    return bad + [f"{c.name} {s}: it's {c.name} {was[s]} in every other project" for s in c.subcommands() if s in was]


def lookalike_commands(c: Command) -> list[str]:
    subs = set(c.subcommands())
    return [f"{c.name} {s} and {c.name} {s}s are one letter apart: make one a subcommand of the other ({c.name} {s}s <verb>)"
            for s in sorted(subs) if s + "s" in subs]


def runnable(root: str) -> list[str]:
    """Everything in the project that can be run from a shell, as it would be listed: a file's path from the project's
    folder ("dev/release.py", "bin/callboard"), a Go program's folder ("./cmd/ghostlight"). Not the tests themselves
    (test_*.py, *_test.py, *_test.go), a shared test's copy (tests/callboard_*: its template's project lists it), or
    anything in a folder that isn't the project's own code (hidden ones, node_modules, a virtualenv, test data)."""
    out: list[str] = []
    for folder, dirs, files in os.walk(root):
        rel = os.path.relpath(folder, root).replace(os.sep, "/")
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in NOT_OURS and not d.startswith("callboard_")
                         and not os.path.isfile(os.path.join(folder, d, "pyvenv.cfg")))
        go = False
        for name in sorted(files):
            path = os.path.join(folder, name)
            at = name if rel == "." else f"{rel}/{name}"
            stem, ext = os.path.splitext(name)
            if name.startswith(".") or os.path.islink(path):
                continue
            try:
                if ext == ".py":
                    if not (stem.startswith("test_") or stem.endswith("_test")) and (name == "__main__.py" or PY_MAIN.search(_text(path))):
                        out.append(at)
                elif ext in (".js", ".mjs", ".cjs"):
                    if not stem.endswith((".test", ".spec", ".min")) and JS_MAIN.search(_text(path)):
                        out.append(at)
                elif ext == ".go":
                    go = go or (not stem.endswith("_test") and bool(GO_MAIN.search(_text(path))))
                elif os.access(path, os.X_OK) and ext in ("", ".sh", ".bash", ".zsh", ".rb", ".pl"):
                    with open(path, "rb") as fh:
                        if fh.read(2) == b"#!":
                            out.append(at)
            except OSError:
                continue
        if go:
            out.append("." if rel == "." else f"./{rel}")
    return out


def _text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def all_listed(root: str, specs: list[dict]) -> list[str]:
    """What can be run from a shell and isn't in the settings: each must be a word of a command's "run" or "build"
    (a Python module may be named as it's run: "-m", "plexcache.optimize")."""
    words = {w for spec in specs for k in ("run", "build") for w in spec.get(k) or []}
    listed = {os.path.normpath(w).replace(os.sep, "/") for w in words}
    for spec in specs:   # (-m a.b: a/b.py, or a/b/__main__.py)
        run = spec.get("run") or []
        for i, w in enumerate(run[:-1]):
            if w == "-m":
                listed |= {run[i + 1].replace(".", "/") + ".py", run[i + 1].replace(".", "/") + "/__main__.py"}
    return [f"{at} can be run from a shell and isn't one of the settings' commands: name it there, so it's held to the same checks"
            for at in runnable(root) if os.path.normpath(at).replace(os.sep, "/") not in listed]


def run(settings: str, root: str, only: tuple[str, ...] = CHECKS) -> dict:
    """Every check on every command the settings name: {"commands": [names], "findings": [{code, command, message}]}
    (an Unrunnable where the settings can't be read or a command can't be started)."""
    specs = load(settings)
    cmds = [Command(spec, root) for spec in specs]
    findings = [{"code": check, "command": "", "message": m} for check in only if check in PROJECT_CHECKS for m in globals()[check](root, specs)]
    for c in cmds:
        for check in only:
            if check in PROJECT_CHECKS:
                continue
            findings += [{"code": check, "command": c.name, "message": m} for m in globals()[check](c)]
    return {"commands": [c.name for c in cmds], "findings": findings}


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("-")]
    if "--version" in argv and not {"-h", "--help"} & set(argv):
        try:   # (the template's own version: template.toml, beside this file in the template and in every copy)
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.toml"), encoding="utf-8") as fh:
                m = re.search(r"^version\s*=\s*(\d+)", fh.read(), re.M)
        except OSError:
            m = None
        print(f"clichecks {m.group(1) if m else 'unknown'}")
        return 0
    if len(args) != 1 or set(argv) - set(args) - {"--json"} or {"-h", "--help"} & set(argv):
        asked = bool({"-h", "--help"} & set(argv))
        print("\n".join(x.strip() for x in (__doc__ or "").strip().splitlines()[-2:]), file=sys.stdout if asked else sys.stderr)
        return 0 if asked else 2
    settings = os.path.abspath(args[0])
    try:
        got = run(settings, os.path.dirname(os.path.dirname(settings)))
    except Unrunnable as e:
        print(json.dumps({"error": str(e)}) if "--json" in argv else str(e), file=sys.stdout if "--json" in argv else sys.stderr)
        return 2
    if "--json" in argv:
        print(json.dumps(got))
    else:
        for f in got["findings"]:
            print(f"{f['code']}: {f['message']}")
        print(f"{len(got['findings'])} findings in {', '.join(got['commands']) or 'no commands'}")
    return 1 if got["findings"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
