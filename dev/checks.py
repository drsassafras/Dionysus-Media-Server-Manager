"""What every check tool shares (dev/typecheck.py's Python Validation, dev/buildcheck.py's Build Validation): each tool
fetched at its version in dev/tools.toml, what it finds said one way, the board's bar and the result line.

    from dev import checks
    exe, why = checks.command("ruff")          # the command at its pin (fetched the first time), or None and why
    sys.exit(checks.run([Check("Ruff", ruff), Check("pyright", pyright)], bar="types"))

A finding is said as ``  path:line:col - severity: message (Tool CODE)``, `` [*]`` after it when the tool's own --fix
makes it; on GitHub it's annotated on its line too (an error, a warning or a notice). Then the result line every dev
tool says (dev/progress.py: "0 errors · 0 warnings · 0 Ruff findings"): each tool's own facts, in the order the checks
are given. On devtools's board (PLEXCACHE_PROGRESS=feed) the bar is the files checked (what each check says it reads:
``files``, from project_files; a file counted once every check that reads it is done), each check named as it ends.

The exit code: 0 clean, 1 something found, 2 a tool couldn't run (not installed, no network for its download…).
"""

import errno
import hashlib
import io
import json
import os
import platform as _platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dev import policy  # noqa: E402 — what each finding is held to (devtools.toml), and the exit code that says it
from dev.progress import Progress, counted, detail, feeding, result, said, status, tell  # noqa: E402 — the dev tools' bar and result line

# The checks (DHTML, Python Validation, Build Validation: dev/typecheck.py, dev/buildcheck.py…): what a finding of each
# severity is held as. Every check's tool follows this one schema; its rules are the integration's in devtools.toml.
SCHEMA = policy.Schema("checks", "Checks", {
    "errors": policy.Setting("level", "a finding the tool calls an error"),
    "warnings": policy.Setting("level", "a finding the tool calls a warning"),
    "information": policy.Setting("level", "a finding the tool calls information (a note)"),
}, "What each finding's severity is held as: fail (red), warn (orange), info (grey) or off (not shown).")

TOOLS = os.path.join(ROOT, "dev", "tools.toml")
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "plex-cache", "tools")
ON_GITHUB = os.environ.get("GITHUB_ACTIONS") == "true"
TIMEOUT = 300   # a download or an install


def tools() -> dict[str, dict]:
    """dev/tools.toml: each check tool by its key."""
    with open(TOOLS, "rb") as fh:
        return tomllib.load(fh)


def version(key: str) -> str:
    return str(tools()[key]["version"])


def platform() -> str:
    """This computer as dev/tools.toml's ``assets`` and ``sha256`` name it: darwin-arm64, linux-x86_64, linux-arm64…"""
    machine = _platform.machine().lower()
    machine = {"aarch64": "arm64", "amd64": "x86_64"}.get(machine, machine)
    return f"{sys.platform}-{machine}"


def _whole(tmp: str, final: str, exe: str) -> None:
    """``tmp`` made ``final``, whole or not at all (a run stopped halfway leaves nothing to trust)."""
    try:
        os.rename(tmp, final)
    except OSError:
        if not os.path.exists(exe):   # (another run got there first: its copy will do)
            raise


def _scratch(final: str) -> str:
    """A folder of this fetch's own beside ``final`` (on its disk, so _whole's rename is whole): not the process's id alone,
    which two threads of one run share (actionlint fetching ShellCheck as ShellCheck's own check does)."""
    os.makedirs(CACHE, exist_ok=True)
    return tempfile.mkdtemp(prefix=f"{os.path.basename(final)}.", dir=CACHE)


_FETCHING: dict[str, threading.Lock] = {}   # one lock per tool: a run's checks wait on its one fetch, then use it
_FETCHING_LOCK = threading.Lock()


def _one_fetch(key: str) -> threading.Lock:
    with _FETCHING_LOCK:
        return _FETCHING.setdefault(key, threading.Lock())


_BAR: list[Progress] = []   # the bar run() draws, while it runs: a tool fetched the first time says so on it


def _fetching(tool: dict) -> None:
    """A tool fetched (or installed) the first time, its run waiting on that: said on the bar, so a slow first run says why."""
    for bar in _BAR:
        bar.show(f"fetching {tool['name']} {tool['version']} (the first run only)", ticking=True)


def _pip(key: str, tool: dict) -> tuple[list[str] | None, str]:
    venv = os.path.join(CACHE, f"{key}-{tool['version']}")
    exe = os.path.join(venv, "bin", tool.get("binary") or tool["package"])
    if os.path.exists(exe):
        return [exe], ""
    tmp = ""
    _fetching(tool)
    try:
        tmp = _scratch(venv)
        subprocess.run([sys.executable, "-m", "venv", tmp], check=True, capture_output=True, text=True, timeout=TIMEOUT)
        subprocess.run([os.path.join(tmp, "bin", "python"), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                        f"{tool['package']}=={tool['version']}"], check=True, capture_output=True, text=True, timeout=TIMEOUT)
        _whole(tmp, venv, exe)
    except subprocess.CalledProcessError as e:
        lines = (e.stderr or e.stdout or "").strip().splitlines()
        return None, f"{tool['name']} {tool['version']} couldn't be installed: {lines[-1] if lines else e}"
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"{tool['name']} {tool['version']} couldn't be installed: {e}"
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return [exe], ""


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:   # (dev/tools.toml's own release URLs)
        return r.read()


def unpack(data: bytes, inside: str) -> bytes:
    """The file ``inside`` an archive (.tar.xz, .tar.gz, .zip), or the download itself when ``inside`` is empty."""
    if not inside:
        return data
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return z.read(inside)
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        got = t.extractfile(inside)
        if got is None:
            raise OSError(f"no {inside} in the archive")
        return got.read()


def asset_url(tool: dict, plat: str, version: str | None = None) -> str:
    v = version or str(tool["version"])
    return tool["url"].format(version=v, asset=tool["assets"][plat])


def _release(key: str, tool: dict) -> tuple[list[str] | None, str]:
    plat = platform()
    if plat not in tool.get("assets", {}):
        return None, f"{tool['name']} has no release for {plat} in dev/tools.toml"
    folder = os.path.join(CACHE, f"{key}-{tool['version']}-{plat}")
    inside = str(tool.get("binary", "")).format(version=tool["version"])
    exe = os.path.join(folder, os.path.basename(inside) or key)
    if os.path.exists(exe):
        return [exe], ""
    tmp = ""
    _fetching(tool)
    try:
        data = fetch(asset_url(tool, plat))
        got = hashlib.sha256(data).hexdigest()
        want = tool.get("sha256", {}).get(plat, "")
        if got != want:
            return None, f"{tool['name']} {tool['version']}'s download for {plat} isn't the one dev/tools.toml names (sha256 {got[:12]}…)"
        tmp = _scratch(folder)
        path = os.path.join(tmp, os.path.basename(exe))
        with open(path, "wb") as fh:
            fh.write(unpack(data, inside))
        os.chmod(path, 0o700)   # (a command, its owner's: the cache is theirs)
        _whole(tmp, folder, exe)
    except (OSError, KeyError, tarfile.TarError, zipfile.BadZipFile) as e:
        return None, f"{tool['name']} {tool['version']} couldn't be fetched: {e}"
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return [exe], ""


def command(key: str) -> tuple[list[str] | None, str]:
    """The command that runs tool ``key`` at its version (fetched or installed the first time), or None and why not."""
    tool = tools()[key]
    if tool["get"] == "npx":
        if not shutil.which("npx"):
            return None, f"{tool['name']} needs npx (Node), which isn't installed"
        return ["npx", "--yes", f"{tool['package']}@{tool['version']}"], ""
    with _one_fetch(key):   # (two checks wanting one tool: the second waits for the first's fetch, then finds it there)
        if tool["get"] == "pip":
            return _pip(key, tool)
        return _release(key, tool)


def call(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """subprocess.run for a check tool's command. A tool just fetched can be 'Text file busy' (ETXTBSY) for a moment: a
    subprocess another check's thread started while the download was being written holds the file open for writing until
    it execs its own program, and Linux won't run a file that's open for writing. That's waited out, not said as a failure."""
    for wait in (0.05, 0.1, 0.2, 0.4, 0.8, 1.6):
        try:
            return subprocess.run(cmd, **kw)
        except OSError as e:
            if e.errno != errno.ETXTBSY:
                raise
            time.sleep(wait)
    return subprocess.run(cmd, **kw)


# ---------------------------------------------------------------- what they find, said one way
SEVERITIES = ("error", "warning", "information")


@dataclass
class Finding:
    tool: str          # who found it, as the result line names it ("Ruff")
    path: str          # the file, from the project's root
    line: int
    col: int
    severity: str      # error, warning or information
    message: str
    code: str = ""     # the tool's own name for the rule (F401, SC2034, reportArgumentType)
    fixable: bool = False   # the tool's --fix makes it
    level: str = ""    # what it's held as (devtools.toml: fail, warn, info, off); "" until run() sets it from its severity


def rel(path: str) -> str:
    """A path from the project's root (as the tools are run there), however the tool said it."""
    r = os.path.relpath(path, ROOT) if os.path.isabs(path) else path
    return r if not r.startswith("..") else path


WORD_OF = {"fail": "error", "warn": "warning", "info": "information"}   # (a finding's word, from what it's held as)


def say(f: Finding) -> str:
    code = f" ({f.tool} {f.code})" if f.code else f" ({f.tool})"
    where = f"{rel(f.path)}:{f.line}:{f.col} - " if f.path else ""
    word = WORD_OF.get(f.level, f.severity) if f.level else f.severity   # (as the rules hold it: a preview's error is a warning)
    return f"  {where}{word}: {f.message}{code}{' [*]' if f.fixable else ''}"


def annotate(f: Finding) -> str:
    """The line GitHub reads as an annotation on the finding's line."""
    word = WORD_OF.get(f.level, f.severity) if f.level else f.severity
    kind = {"error": "error", "warning": "warning"}.get(word, "notice")
    msg = f.message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    title = f"{f.tool} {f.code}".strip()
    return f"::{kind} file={rel(f.path)},line={f.line},col={f.col},title={title}::{msg}"


@dataclass
class Outcome:
    tool: str
    version: str
    findings: list[Finding] | None = None   # None: it couldn't say (``why``)
    why: str = ""
    facts: list[str] = field(default_factory=list)   # its part of the result line; empty: "N <tool> findings"
    failed: bool | None = None   # (no longer read: what fails is each finding's level, devtools.toml's; kept for callers)

    def said(self) -> list[str]:
        if self.findings is None:
            return [f"{self.tool} didn't run"]
        return self.facts or [counted(len(self.findings), f"{self.tool} finding")]

    def level(self) -> str:
        """The worst level among what it found ("off" of nothing)."""
        return policy.worst([f.level for f in self.findings or [] if f.level])

    def bad(self) -> bool:
        """Whether what it found fails the check: a finding held as fail."""
        if self.findings is None:
            return False
        return self.level() == "fail"


@dataclass
class Check:
    name: str
    run: Callable[[], Outcome]
    files: Callable[[], list[str]] | None = None   # what it reads (project_files): the bar counts them; None: the checks


def project_files(*patterns: str, under: tuple[str, ...] = ("",), skip: tuple[str, ...] = ()) -> list[str]:
    """The project's files (from its root) whose names match one of ``patterns`` (fnmatch: "*.py", "Dockerfile"), in the
    folders ``under`` ("" the whole project), none in ``skip``: what git keeps or would (not what it ignores), or, with
    no git, every file but .git, node_modules and __pycache__."""
    import fnmatch
    try:
        got = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "-co", "--exclude-standard"], capture_output=True, timeout=30)
        listed = [p for p in got.stdout.decode("utf-8", "replace").split("\0") if p] if got.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        listed = None
    if listed is None:
        listed = []
        for dirpath, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__")]
            listed += [os.path.relpath(os.path.join(dirpath, n), ROOT).replace(os.sep, "/") for n in names]

    def within(path: str, folders: tuple[str, ...]) -> bool:
        return any(not f or path == f or path.startswith(f.rstrip("/") + "/") for f in folders)
    return sorted(p for p in listed if within(p, under) and not within(p, skip) and os.path.isfile(os.path.join(ROOT, p))
                  and any(fnmatch.fnmatch(os.path.basename(p), pat) for pat in patterns))


def levels(integration: str = "", rules: policy.Rules | None = None) -> policy.Rules | None:
    """The rules a tool's findings are held to: ``rules`` as given, else the integration's (DEVTOOLS_INTEGRATION, what
    devtools names each block's command; ``integration`` when the tool knows its own), from the project's devtools.toml.
    None when there's no file (a tool run outside a project: every finding as its severity says)."""
    if rules is not None:
        return rules
    id_ = os.environ.get("DEVTOOLS_INTEGRATION", "") or integration
    if not id_ or not policy.find_file():
        return None
    try:
        cfg = policy.Config(policy.find_file())
        if integration and id_ != integration and cfg.integration(id_)["kind"] != integration:
            # the block's integration is another tool's (this tool run from inside it, as the unit tests run the others):
            # its own rules, not the block's — the first integration of its kind, else the one named like it
            id_ = next((i["id"] for i in cfg.integrations(all_=True) if i["kind"] == integration), integration)
        return policy.rules(id_, cfg=cfg)
    except policy.ConfigError as e:
        raise SystemExit(f"devtools.toml: {e}") from None


SEVERITY_RULE = {"error": "errors", "warning": "warnings", "information": "information"}


def held(f: Finding, rules: policy.Rules | None) -> str:
    """What a finding is held as: its own level when the tool set one (a rule's), else its severity's rule in the
    integration's config (the checks schema); with no config, as its severity says (error fail, warning warn, else info)."""
    if f.level:
        return f.level
    if rules is not None and rules.schema.kind == "checks":
        return rules.level(SEVERITY_RULE.get(f.severity, "information"))
    return {"error": "fail", "warning": "warn"}.get(f.severity, "info")


def run(checks: list[Check], bar: str = "main", stream=None, more: dict | None = None, rules: policy.Rules | None = None,
        notes: dict[str, str] | None = None, met: str = "", facts: list[str] | None = None) -> int:
    """Every check at once; what each found, in their order, then the result line. The exit code: dev/policy.py's, from
    the level each finding is held at (``held``: a level the tool set on it, else its severity's rule in the integration's
    config, ``rules`` or levels()) — 0 nothing, 1 a fail, 2 a check couldn't run, 3 only notes, 4 warnings; a finding held
    off isn't shown. ``notes``: the note the board shows beside the title per level ({"warn": "tests to add"}); else a
    count. ``more``: what the tool adds to its page's detail beyond the checks (dev/coverage.py's files; its "kind"
    names the page). ``met``: each check is a requirement (dev/release.py's, a language of dev/i18n.py's), and once they're
    done the bar counts the ones met, in that unit ("requirements met"): a check that found nothing is met, one whose miss
    is held as fail counts failed, as warn a warning, as info neither (the bar's grey rest). ``facts``: the result line's
    facts, in place of each check's own (each requirement's own line is its finding)."""
    stream = stream or sys.stdout
    rules = levels(rules=rules)
    changed = None   # (diff mode, for the checks: the lines changed since the gate's base, per file)
    if rules is not None and rules.ctx.diff and rules.ctx.base and rules.schema.kind == "checks":
        changed = policy.changed_lines(rules.ctx.base, ROOT)
    # what the bar counts: the files the checks read (each once), when every check says which; else the checks
    read = [c.files() if c.files else None for c in checks] if feeding() else []
    readers: dict[str, set[int]] = {}
    if read and all(r is not None for r in read):
        for i, r in enumerate(read):
            for f in r or []:
                readers.setdefault(f, set()).add(i)
    progress = None
    if feeding():
        progress = Progress(len(readers), unit="files checked", bar=bar, stream=stream) if readers else \
            Progress(len(checks), unit="checks done", bar=bar, stream=stream)
        progress.workers = len(checks)
        progress.show(", ".join(c.name for c in checks), ticking=True)
        _BAR.append(progress)
    outcomes: list[Outcome | None] = [None] * len(checks)
    told = [False] * len(checks)   # (what a check found, told as it came: progress.tell)
    lock = threading.Lock()

    def found(o: Outcome) -> str:
        if o.findings is None:
            return f"  {o.why}\n"
        return "".join(say(f) + "\n" + (annotate(f) + "\n" if ON_GITHUB else "") for f in o.findings)

    def one(i: int, c: Check):
        try:
            got = c.run()
        except Exception as e:   # noqa: BLE001 — one tool falling over is said as that, the others still report
            got = Outcome(c.name, "", None, f"{c.name} fell over: {e!r}")
        if got.findings is not None:   # (each finding held at its level; one held off isn't a finding)
            for f in got.findings:
                f.level = held(f, rules)
            got.findings = [f for f in got.findings if f.level != "off"]
            if changed is not None:   # (diff mode: a finding on a line that didn't change isn't this change's)
                kept = [f for f in got.findings if f.line == 0 or f.line in changed.get(rel(f.path), set())]
                if len(kept) != len(got.findings):
                    got.facts = [*got.facts, f"{len(got.findings) - len(kept)} outside the change"]
                got.findings = kept
        with lock:
            outcomes[i] = got
            # a check that failed (or couldn't run) says what it found now, not once the slowest is done: on the board,
            # above it as it comes, as the tests' failures are
            if got.bad() or got.findings is None:
                told[i] = tell(found(got), stream)
            bad = sum(1 for o in outcomes if o and (o.bad() or o.findings is None))
            name = f"{c.name} {got.version}".strip()
            if progress and readers:   # (a file is checked once every check that reads it is done)
                done = sum(1 for who in readers.values() if all(outcomes[j] is not None for j in who))
                progress.bad = bad
                progress.at(done, len(readers), name)
            elif progress:
                progress.draw(name, bad=bad)

    threads = [threading.Thread(target=one, args=(i, c), name=c.name) for i, c in enumerate(checks)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    done = [o for o in outcomes if o is not None]
    for i, o in enumerate(outcomes):
        if o is not None and not told[i]:
            stream.write(found(o))
    if progress and met:   # (each requirement: met, or missed at its level)
        missed = [o.level() if o.findings else ("fail" if o.findings is None else "") for o in done]
        progress.warned = sum(1 for lv in missed if lv == "warn")
        progress.bad = sum(1 for lv in missed if lv == "fail")
        progress.unit = met
        progress.at(sum(1 for lv in missed if not lv) + progress.bad, len(checks), met)
    if progress:
        _BAR.remove(progress)
        progress.finished = met or " · ".join(f"{o.tool} {o.version}".strip() for o in done)
        progress.clear()
    line = result(*(facts if facts is not None else [fact for o in done for fact in o.said()]))
    stream.write(line + "\n")
    stream.flush()
    said(line, stream)
    findings = [f for o in done for f in o.findings or []]
    rc = policy.exit_code([f.level for f in findings], broken=any(o.findings is None for o in done))
    worst = policy.worst([f.level for f in findings])
    note = (notes or {}).get(worst, "")
    if not note and worst in ("warn", "info"):
        note = counted(sum(1 for f in findings if f.level == worst), "warning" if worst == "warn" else "note")
    if note:
        status(note, stream)
    # (its page in the devtools UI: each tool, what it found, and the files the checks read)
    detail({"kind": "checks", "result": line, "files": sorted(readers), "level": worst, "rc": rc, "note": note,
            "context": policy.context().__dict__ if rules is not None else {},
            "rules": rules.said() if rules is not None else [], "skipped": list(rules.skipped_rules) if rules is not None else [],
            "tools": [
        {"name": checks[i].name, "tool": o.tool, "version": o.version, "ran": o.findings is not None, "why": o.why, "facts": [f for f in o.said() if f.strip()],
         "failed": o.bad(), "level": o.level(), "findings": [asdict(f) for f in o.findings or []]}
        for i, o in enumerate(outcomes) if o is not None], **(more or {})})
    return rc


def repo_at() -> tuple[str, str] | None:
    """Where git knows the project's lines, for a check that asks git about them (dev/audit.py's blame): this folder when
    it's a checkout (its working tree, so uncommitted lines count as not committed yet), else the repository devtools copied
    it from, at the commit it copied (DEVTOOLS_REPO, DEVTOOLS_COMMIT); None: no history here."""
    try:
        got = subprocess.run(["git", "-C", ROOT, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
        if got.returncode == 0 and os.path.realpath(got.stdout.strip()) == os.path.realpath(ROOT):
            return ROOT, ""
    except (OSError, subprocess.TimeoutExpired):
        return None
    repo, commit = os.environ.get("DEVTOOLS_REPO", ""), os.environ.get("DEVTOOLS_COMMIT", "")
    return (repo, commit) if repo and commit else None


def json_out(cmd: list[str], **kw) -> tuple[object | None, subprocess.CompletedProcess]:
    """A tool's JSON output (its findings), or None when it didn't give any."""
    got = call(cmd, cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT, **kw)
    try:
        return json.loads(got.stdout), got
    except ValueError:
        return None, got


def last_line(got: subprocess.CompletedProcess) -> str:
    lines = (got.stderr or got.stdout or "").strip().splitlines()
    return lines[-1] if lines else f"exit {got.returncode}"
