#!/usr/bin/env python3
"""The project's devtools.toml, and the one place that decides what every check is held to.

    python3 dev/policy.py env [--code local|master|dev|release|beta] [--gate push|merge|release] [--branch b] [--diff]
                                      # what devtools tells each check (KEY=value lines: DEVTOOLS_ROLE, DEVTOOLS_PHASE…)
    python3 dev/policy.py integrations [--code …]   # the integrations on the board, one a line (id, title, letter, kind…)
    python3 dev/policy.py show [--code …] [--gate …] [--diff]   # every integration's rules as they stand for that code
    python3 dev/policy.py check                                 # the file against each integration's schema
    python3 dev/policy.py init                                  # a devtools.toml for a project without one (dev/starter.toml)

The rules — which level a finding is (fail, warn, info or off), the thresholds, the windows — aren't in any tool's code:
each tool holds only a SCHEMA (what its settings are called, and what kind each is), and reads what the project's
devtools.toml sets for them through ``rules()`` here. A setting the file doesn't set is off. So the CLI, the board, the
UI and GitHub can't disagree: they all ask this module, with the same context.

The context (``context()``): the branch the code is from, and so its role (development, release, hotfix, feature, …:
``[branches]``), or the phase its newest tag puts it in (preview, freeze, alpha: ``[tags]`` and ``[phases]``), the mode
(full: the whole codebase; diff: only what changed since the base) and the gate (push, merge, release) — what devtools
worked out and put in the environment (DEVTOOLS_ROLE, DEVTOOLS_PHASE, DEVTOOLS_BRANCH, DEVTOOLS_TAG, DEVTOOLS_DIFF,
DEVTOOLS_GATE, DEVTOOLS_BASE), or, run on its own (by hand, or on GitHub), the same worked out from git here. No tool
reads those variables itself.

A check's exit code, from what it found and the levels those are (``exit_code()``): 0 nothing to say (PASS), 1 a fail
(FAIL), 2 it couldn't run (BROKEN), 3 only telling (INFO: grey on the board), 4 a warning (WARN: orange). The board reads
4 as orange and 3 as grey for every integration alike (devtools), the note beside the block's title what the tool sent
with progress.status().

The file's shape (devtools.toml, at the project's root; dev/starter.toml is one that does what devtools did before it):

    [config]              version, read_only (the UI may show but not save it), version_file (the code's version:
                          a pre-release tag of another version puts the code in no phase)
    [branches]            preset, then one table per branch name or glob: role = "…" ("*": the default role)
    [tags]                the version tag patterns: release, freeze, preview, alpha ({version}, {n})
    [phases.<phase>]      role = "…": a tag in that phase gives the code that role (unset: the phase is only said)
    [roles.<role>]        inherits = "<role>" (its rules first), cap = "warn" (no rule above that level)
    [github]              enabled, and what GitHub does with each gate
    [gates.<gate>]        diff (true: only what changed), github (true, false or "warn"), base
    [integrations.<id>]   enabled, title, kind, cmd, after (the sections it waits for), needs (files it needs), letter
    [<id>]                that integration's default rules (every role starts from them)
    [<id>.<role>]         …what's different for that role (over the role it inherits from)
    [<id>.diff]           …what's different in diff mode (and [<id>.<role>.diff])

A rule is a level ("fail", "warn", "info", "off"), a number (a threshold, a window of days, a count: the integration's
``level`` says what missing it is, or ``{value = 98, level = "fail"}`` says for that rule), a toggle (true/false), or
targets (a table: language or group to a percentage, "*" for the rest).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:  # (run as a script: dev/ is on the path, not the project)
    sys.path.insert(0, ROOT)
if __name__ == "__main__":   # (the tools import dev.policy: the same module as this one, so a Schema is a Schema)
    sys.modules.setdefault("dev.policy", sys.modules[__name__])
FILE = "devtools.toml"
STARTER = os.path.join(ROOT, "dev", "starter.toml")

LEVELS = ("fail", "warn", "info", "off")   # (worst first)
PASS, FAIL, BROKEN, INFO, WARN, NOTRUN = 0, 1, 2, 3, 4, 5   # (NOTRUN: the check couldn't be set up — nothing is known)
RC_OF = {"fail": FAIL, "warn": WARN, "info": INFO, "off": PASS}
KINDS = ("level", "threshold", "window", "count", "toggle", "targets", "text", "list")
BUILTIN_ROLES = ("development", "release", "release-line", "hotfix", "feature", "preview", "freeze", "alpha")
PHASES = ("alpha", "preview", "freeze")   # (a tag's; "release" is a tag too, but its phase is over: the code is shipped)
# Where each kind of integration's schema is (its SCHEMA), from the project's root.
KIND_FILES = {"tests": "tests/run.py", "checks": "dev/checks.py", "coverage": "dev/coverage.py", "audit": "dev/audit.py",
              "i18n": "dev/i18n.py", "release": "dev/release.py", "upkeep": "dev/upkeep.py", "style": "dev/style.py"}
GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
# The board's own keys (dev/devtools: p pushes, a opens the audits page, r the style review): no page takes them.
RESERVED_KEYS = {"p", "a", "r"}


class ConfigError(Exception):
    """devtools.toml can't be used as it is: what's wrong, and where."""


# ---------------------------------------------------------------- the schema a tool holds
@dataclass(frozen=True)
class Setting:
    kind: str                 # level, threshold, window, count, toggle, targets, text or list
    doc: str                  # what it decides, as the Config tab says it
    full_only: bool = False   # only when the whole codebase is checked: skipped in diff mode, and said so
    unit: str = ""            # "%", "days", "" (what the number is)


@dataclass(frozen=True)
class Schema:
    kind: str
    title: str
    settings: dict[str, Setting]
    doc: str = ""

    def __post_init__(self):
        for name, s in self.settings.items():
            if name in BUILTIN_ROLES or name in ("diff", "level"):
                raise ValueError(f"{self.kind}.{name}: a setting can't be named like a role, diff or level")
            if s.kind not in KINDS:
                raise ValueError(f"{self.kind}.{name}: {s.kind!r} isn't a kind of setting {KINDS}")
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise ValueError(f"{self.kind}: {name!r} isn't a setting's name (lowercase, digits, _)")


_SCHEMAS: dict[str, Schema] = {}


def schema(kind: str) -> Schema:
    """The schema of that kind of integration: its tool's SCHEMA (KIND_FILES), loaded once."""
    if kind in _SCHEMAS:
        return _SCHEMAS[kind]
    path = KIND_FILES.get(kind)
    if not path:
        raise ConfigError(f"{kind!r} isn't a kind of integration ({', '.join(KIND_FILES)})")
    import importlib.util
    full = os.path.join(ROOT, path)
    if not os.path.isfile(full):
        raise ConfigError(f"{path} isn't there: no schema for the {kind} integrations")
    spec = importlib.util.spec_from_file_location(f"devtools_schema_{kind}", full)
    if spec is None or spec.loader is None:
        raise ConfigError(f"{path} can't be loaded for its schema")
    name = "dev." + os.path.basename(path)[:-3] if path.startswith("dev/") else None
    if name and name in sys.modules:   # (a tool the process has: its SCHEMA as it stands)
        mod = sys.modules[name]
    else:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    got = getattr(mod, "SCHEMA", None)
    if got is None or type(got).__name__ != "Schema":
        raise ConfigError(f"{path} has no SCHEMA (every integration's tool holds one)")
    if got.kind != kind:
        raise ConfigError(f"{path}'s SCHEMA is for {got.kind!r}, not {kind!r}")
    _SCHEMAS[kind] = got
    return got


# ---------------------------------------------------------------- the file
def find_file(start: str | None = None) -> str:
    """The project's devtools.toml: DEVTOOLS_CONFIG when it names one (devtools: the folder's, whichever copy of the code a
    check runs in), else the nearest one from ``start`` (this folder) up to the repository's top. "" when there's none."""
    named = os.environ.get("DEVTOOLS_CONFIG", "")
    if named and named.endswith(".toml") and start is None:   # (a folder asked for by name is that folder's)
        return named if os.path.isfile(named) else ""   # (devtools checks the project's is there before a run)
    here = os.path.realpath(start or os.getcwd())
    top = git(["rev-parse", "--show-toplevel"], here) or here
    top = os.path.realpath(top)
    d = here
    while True:
        p = os.path.join(d, FILE)
        if os.path.isfile(p):
            return p
        if d == top or os.path.dirname(d) == d:
            return ""
        d = os.path.dirname(d)


def git(args: list[str], cwd: str | None = None) -> str:
    """What git says, stripped; "" when it can't (not a repository, no git)."""
    try:
        got = subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return got.stdout.strip() if got.returncode == 0 else ""


def level_of(v: Any, where: str) -> str:
    if isinstance(v, str) and v in LEVELS:
        return v
    if v is False:
        return "off"
    raise ConfigError(f"{where}: {v!r} isn't a level ({', '.join(LEVELS)})")


class Config:
    """devtools.toml, read: the integrations it lists, the branches' roles, and each integration's rules resolved for a
    context (``rules``)."""

    def __init__(self, path: str = "", data: dict | None = None):
        self.path = path
        if data is None:
            if not path:
                raise ConfigError(f"no {FILE} here or above (in your project, run: devtools init)")
            with open(path, "rb") as fh:
                try:
                    data = tomllib.load(fh)
                except tomllib.TOMLDecodeError as e:
                    raise ConfigError(f"{path}: {e}") from None
        self.data: dict = data
        self.root = os.path.dirname(os.path.realpath(path)) if path else ROOT

    def check(self) -> list[str]:
        """Everything the file says, read: every integration, role, gate and rule as its schema has it (ConfigError on the
        first thing wrong). The rules' names, each said as "<integration>.<role>.<setting>", for the UI."""
        return _check(self)

    # -- what it says
    def table(self, *keys: str) -> dict:
        d: Any = self.data
        for k in keys:
            d = d.get(k) if isinstance(d, dict) else None
            if d is None:
                return {}
        return d if isinstance(d, dict) else {}

    @property
    def read_only(self) -> bool:
        return bool(self.table("config").get("read_only"))

    def version(self) -> str:
        """The code's version, from the file [config] version_file names (a ``__version__ = "X.Y.Z"`` or ``version =
        "X.Y.Z"`` line); "" when the file doesn't say, or none is named. A pre-release tag of another version is stale:
        it puts the code in no phase (``phase_tag``)."""
        rel = str(self.table("config").get("version_file") or "")
        if not rel:
            return ""
        try:
            with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return ""
        m = re.search(r'^\s*(?:__version__|version)\s*[=:]\s*["\']([^"\']+)["\']', text, re.M)
        return m.group(1) if m else ""

    @property
    def roles(self) -> list[str]:
        return list(dict.fromkeys([*BUILTIN_ROLES, *self.table("roles")]))

    def integrations(self, all_: bool = False) -> list[dict]:
        """The integrations, in the file's order, each as {"id", "title", "kind", "cmd", "after", "needs", "letter",
        "enabled", "ready"}: the ones on the board (enabled, and set up: every file in ``needs`` there, a cmd given),
        or every one with ``all_`` (the Integrations page). Their letters follow the menu (``letters``)."""
        out = []
        for id_, spec in self.table("integrations").items():
            if not isinstance(spec, dict):
                raise ConfigError(f"[integrations.{id_}] isn't a table")
            if not re.fullmatch(r"[a-z][a-z0-9_]*", id_):
                raise ConfigError(f"[integrations.{id_}]: an id is lowercase letters, digits and _")
            kind = str(spec.get("kind") or id_)
            if kind not in KIND_FILES:
                raise ConfigError(f"[integrations.{id_}] kind = {kind!r}: not one of {', '.join(KIND_FILES)}")
            needs = spec.get("needs") or []
            if not isinstance(needs, list) or not all(isinstance(n, str) for n in needs):
                raise ConfigError(f"[integrations.{id_}] needs: a list of files")
            after = spec.get("after") or []
            if not isinstance(after, list) or not all(isinstance(n, str) for n in after):
                raise ConfigError(f"[integrations.{id_}] after: a list of integrations")
            stop_with = spec.get("stop_with") or []
            if not isinstance(stop_with, list) or not all(isinstance(n, str) for n in stop_with):
                raise ConfigError(f"[integrations.{id_}] stop_with: a list of integrations")
            cmd = str(spec.get("cmd") or "")
            enabled = bool(spec.get("enabled", True))
            ready = bool(cmd) and all(os.path.exists(os.path.join(self.root, n)) for n in needs)
            out.append({"id": id_, "title": str(spec.get("title") or id_.title()), "kind": kind, "cmd": cmd,
                        "after": list(after), "stop_with": list(stop_with), "needs": list(needs), "enabled": enabled, "ready": ready,
                        "letter": str(spec.get("letter") or "")[:1].lower(), "doc": str(spec.get("doc") or "")})
        shown = [i for i in out if i["enabled"] and i["ready"]]
        for i, letter in zip(shown, letters(["Hub", "Sync"] + [i["title"] for i in shown], ["", ""] + [i["letter"] for i in shown])[2:], strict=True):
            i["letter"] = letter
        for i in out:
            if i not in shown:
                i["letter"] = ""   # (hidden: no key)
        return out if all_ else shown

    def integration(self, id_: str) -> dict:
        for i in self.integrations(all_=True):
            if i["id"] == id_:
                return i
        raise ConfigError(f"no [integrations.{id_}] in {self.path or FILE}")

    # -- branches, tags and phases
    def branch_role(self, branch: str) -> str:
        """That branch's role: the most specific [branches."…"] whose name or glob matches it, else "*"'s, else feature."""
        best, best_len = "", -1
        for pat, spec in self.table("branches").items():
            if not isinstance(spec, dict):
                continue
            if pat == branch or (pat != "*" and fnmatchcase(branch, pat)):
                plain = len(pat.replace("*", "").replace("?", ""))
                if pat == branch:
                    plain += 1000
                if plain > best_len:
                    best, best_len = str(spec.get("role") or ""), plain
        if not best:
            best = str(self.table("branches", "*").get("role") or "feature")
        if best not in self.roles:
            raise ConfigError(f"[branches] role = {best!r} for {branch!r}: not a role ({', '.join(self.roles)})")
        return best

    def branches_of(self, role: str) -> list[str]:
        """The branches named in [branches] (no glob) whose code has that role, in the file's order."""
        return [pat for pat, spec in self.table("branches").items()
                if isinstance(spec, dict) and spec.get("role") == role and "*" not in pat and "?" not in pat]

    def tag_pattern(self, phase: str) -> str:
        """The regex a tag of that phase matches (release, freeze, preview, alpha): [tags]'s pattern, {version} and {n}
        filled in; "" when the file has none for it."""
        pat = self.table("tags").get(phase)
        if not isinstance(pat, str) or not pat:
            return ""
        out = ""
        for part in re.split(r"(\{version\}|\{n\})", pat):
            if part == "{version}":
                out += r"(?P<version>\d+(?:\.\d+)*)"
            elif part == "{n}":
                out += r"(?P<n>\d+)"
            else:
                out += re.escape(part)
        return "^" + out + "$"

    def tag_phase(self, tag: str) -> tuple[str, str]:
        """(phase, version) of that tag: release, freeze, preview or alpha; ("", "") when it's none of them."""
        for phase in ("release", *PHASES):
            pat = self.tag_pattern(phase)
            m = re.match(pat, tag) if pat else None
            if m:
                return phase, m.group("version")
        return "", ""

    def phase_role(self, phase: str) -> str:
        """The role a tag's phase gives the code ([phases.<phase>] role), "" when the phase isn't configured."""
        role = str(self.table("phases", phase).get("role") or "")
        if role and role not in self.roles:
            raise ConfigError(f"[phases.{phase}] role = {role!r}: not a role ({', '.join(self.roles)})")
        return role

    def role_chain(self, role: str) -> list[str]:
        """The role, after the ones it inherits from (the root first): each one's rules laid over the one before."""
        chain: list[str] = []
        while role and role not in chain:
            chain.insert(0, role)
            role = str(self.table("roles", role).get("inherits") or "")
        return chain

    def role_cap(self, role: str) -> str:
        """The level no rule of that role goes above ([roles.<role>] cap = "warn": a preview's release rules, as warnings)."""
        cap = ""
        for r in self.role_chain(role):
            own = self.table("roles", r).get("cap")
            if own is not None:
                cap = level_of(own, f"[roles.{r}] cap")
        return cap

    # -- gates
    def gate(self, name: str) -> dict:
        spec = self.table("gates", name)
        if not spec:
            raise ConfigError(f"no [gates.{name}] in {self.path or FILE}")
        return spec

    # -- the rules
    def rules(self, id_: str, ctx: Context) -> Rules:
        """That integration's rules for the context: [<id>], then each role's table down the role's chain, with the
        .diff tables laid over in diff mode, and the role's cap."""
        spec = self.integration(id_)
        sch = schema(spec["kind"])
        reserved = set(self.roles) | {"diff"}
        merged: dict[str, Any] = {}

        def lay(table: dict, where: str):
            for k, v in table.items():
                if k in reserved:
                    continue
                if k == "level":
                    merged[k] = level_of(v, f"{where} level")
                    continue
                if k not in sch.settings:
                    raise ConfigError(f"{where} {k}: not a setting of a {sch.kind} integration ({', '.join(sch.settings)})")
                merged[k] = v

        base = self.table(id_)
        lay(base, f"[{id_}]")
        if ctx.diff:
            lay(self.table(id_, "diff"), f"[{id_}.diff]")
        for role in self.role_chain(ctx.role):
            lay(self.table(id_, role), f"[{id_}.{role}]")
            if ctx.diff:
                lay(self.table(id_, role, "diff"), f"[{id_}.{role}.diff]")
        return Rules(id_, sch, merged, ctx, self.role_cap(ctx.role))


def letters(titles: list[str], wanted: list[str] | None = None) -> list[str]:
    """Each menu title's key, in menu order: the letter asked for it when it's free, else the first letter of its first
    word, then of its next words, then any letter in it, that no title before took. "" when every letter is taken."""
    taken: set[str] = set(RESERVED_KEYS)
    out = []
    for n, title in enumerate(titles):
        want = (wanted[n] if wanted and n < len(wanted) else "").lower()
        words = re.findall(r"[A-Za-z0-9]+", title)
        tries = [want] + [w[0].lower() for w in words] + [c.lower() for w in words for c in w]
        pick = next((c for c in tries if c and c not in taken), "")
        if pick:
            taken.add(pick)
        out.append(pick)
    return out


# ---------------------------------------------------------------- the context
@dataclass
class Context:
    root: str = ""          # the project's folder (the repository)
    config: str = ""        # its devtools.toml
    code: str = "local"     # what devtools was asked to check (local, master, dev, release, beta), or local
    branch: str = ""        # the branch the code is from
    role: str = "feature"
    phase: str = ""         # the newest tag's phase, when it's in one (alpha, preview, freeze)
    tag: str = ""           # that tag
    diff: bool = False      # diff mode: only what changed since ``base``
    gate: str = ""          # push, merge or release, when this is a gate's run
    base: str = ""          # the git ref the diff is against
    commit: str = ""        # the commit checked (a copy from git), "" for the folder as it is

    @property
    def mode(self) -> str:
        return "diff" if self.diff else "full"

    def head(self) -> str:
        """The header's word for it: "Dev · freeze (v0.17.0-rc.2)", "Main", "Feature (login)"."""
        name = (self.branch or self.role).replace("-", " ")
        name = name[:1].upper() + name[1:]
        if self.phase:
            return f"{name} · {self.phase} ({self.tag})"
        return name

    def env(self) -> dict[str, str]:
        """What devtools puts in each check's environment, so every tool sees this same context."""
        return {"DEVTOOLS_CONFIG": self.config, "DEVTOOLS_CODE": self.code, "DEVTOOLS_BRANCH": self.branch,
                "DEVTOOLS_ROLE": self.role, "DEVTOOLS_PHASE": self.phase, "DEVTOOLS_TAG": self.tag,
                "DEVTOOLS_DIFF": "1" if self.diff else "0", "DEVTOOLS_GATE": self.gate, "DEVTOOLS_BASE": self.base}


def context(code: str = "", gate: str = "", branch: str = "", diff: bool | None = None, cfg: Config | None = None,
            env: dict | None = None, repo: str = "") -> Context:
    """The context the checks run in. From the environment when devtools (or a gate) set it (DEVTOOLS_ROLE…: nothing
    asked of git again, so a copy of the code is judged as the folder it came from); else worked out here, from git:
    the branch (``branch``, GitHub's ref, or HEAD's), its newest tag's phase, and the gate asked for."""
    e = os.environ if env is None else env
    cfg = cfg or Config(find_file(repo or None))
    ctx = Context(root=cfg.root, config=cfg.path)
    if e.get("DEVTOOLS_ROLE") and not (code or gate or branch):
        ctx.code = e.get("DEVTOOLS_CODE") or "local"
        ctx.branch, ctx.role, ctx.phase, ctx.tag = e.get("DEVTOOLS_BRANCH", ""), e["DEVTOOLS_ROLE"], e.get("DEVTOOLS_PHASE", ""), e.get("DEVTOOLS_TAG", "")
        ctx.diff, ctx.gate, ctx.base = e.get("DEVTOOLS_DIFF") == "1", e.get("DEVTOOLS_GATE", ""), e.get("DEVTOOLS_BASE", "")
        ctx.commit = e.get("DEVTOOLS_COMMIT", "")
        if ctx.role not in cfg.roles:
            raise ConfigError(f"DEVTOOLS_ROLE={ctx.role!r}: not a role in {cfg.path}")
        return ctx
    root = cfg.root
    ctx.code, ctx.gate = code or "local", gate
    ref = "HEAD"
    if code in ("master", "main"):
        ref = default_branch(root)
        branch = branch or ref.split("/", 1)[-1]
    elif code == "dev":
        ref, branch = "origin/dev", branch or "dev"
    elif code in ("release", "beta"):
        ctx.tag = newest_tag(root, cfg, pre=code == "beta")
        ref = ctx.tag or "HEAD"
    if not branch:
        if gate == "merge" and e.get("GITHUB_BASE_REF"):
            branch = e["GITHUB_BASE_REF"]   # (a pull request: the code is judged as the branch it's going to)
        elif gate == "merge":
            branch = gate_base(root, cfg, "merge", git(["rev-parse", "--abbrev-ref", "HEAD"], root)).removeprefix("origin/")
        elif e.get("GITHUB_HEAD_REF"):
            branch = e["GITHUB_HEAD_REF"]
        elif e.get("GITHUB_REF_TYPE") == "branch" and e.get("GITHUB_REF_NAME"):
            branch = e["GITHUB_REF_NAME"]
        elif e.get("GITHUB_REF_TYPE") == "tag" and e.get("GITHUB_REF_NAME"):
            ctx.tag = ctx.tag or e["GITHUB_REF_NAME"]
            branch = branch_of_tag(root, ctx.tag)
        else:
            branch = git(["rev-parse", "--abbrev-ref", "HEAD"], root) or git(["symbolic-ref", "--quiet", "--short", "HEAD"], root)   # (or a branch with no commit yet)
            if branch == "HEAD":   # (a detached HEAD: a tag's, or none)
                branch = branch_of_tag(root, git(["describe", "--tags", "--exact-match", "HEAD"], root))
    if code in ("release", "beta") and not branch:
        branch = branch_of_tag(root, ctx.tag)
    ctx.branch = branch
    if not ctx.tag:
        ctx.tag = phase_tag(root, cfg, ref)
    phase, _version = cfg.tag_phase(ctx.tag) if ctx.tag else ("", "")
    if phase in PHASES:
        ctx.phase = phase
    elif phase == "release":
        ctx.tag = ctx.tag if code == "release" else ""   # (shipped: no phase)
    else:
        ctx.tag = ""
    ctx.role = cfg.phase_role(ctx.phase) if ctx.phase else ""
    ctx.role = ctx.role or cfg.branch_role(branch or "*")
    if gate:
        spec = cfg.gate(gate)
        ctx.diff = bool(spec.get("diff")) if diff is None else diff
        ctx.base = str(spec.get("base") or "") or gate_base(root, cfg, gate, branch)
    elif diff:
        ctx.diff, ctx.base = True, gate_base(root, cfg, "push", branch)
    if ctx.code != "local":
        ctx.commit = git(["rev-parse", ref], root)
    return ctx


def default_branch(root: str) -> str:
    ref = git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], root)
    if ref:
        return ref
    for b in ("main", "master"):
        if git(["rev-parse", "--verify", "--quiet", f"origin/{b}"], root):
            return f"origin/{b}"
        if git(["rev-parse", "--verify", "--quiet", b], root):
            return b
    return "HEAD"


def tags_sorted(root: str, merged: str = "") -> list[str]:
    """The tags, newest version first (1.0.0-beta.1 before 1.0.0's order: after it), those merged into ``merged`` alone when given."""
    args = ["-c", "versionsort.suffix=-", "tag", "-l", "--sort=-v:refname"]
    if merged:
        args += ["--merged", merged]
    return [t for t in git(args, root).splitlines() if t.strip()]


def newest_tag(root: str, cfg: Config, pre: bool) -> str:
    """The newest release tag ([tags] release), or with ``pre`` the newest of any phase."""
    for t in tags_sorted(root):
        phase, _v = cfg.tag_phase(t)
        if phase == "release" or (pre and phase):
            return t
    return ""


def phase_tag(root: str, cfg: Config, ref: str) -> str:
    """The tag that puts that code in a phase: the newest version tag reachable from ``ref``, when it's a pre-release of
    a version that hasn't been released since (a freeze holds through its fixes until the release tag) and is still the
    code's version ([config] version_file, when it's named: a beta of 0.16.0 says nothing about 0.16.22); else the
    release tag itself when ``ref`` is at one, else ""."""
    current = cfg.version()
    released: set[str] = set()
    for t in tags_sorted(root):
        phase, v = cfg.tag_phase(t)
        if phase == "release":
            released.add(v)
    for t in tags_sorted(root, ref):
        phase, v = cfg.tag_phase(t)
        if not phase:
            continue
        if phase == "release":
            return t
        if v not in released and (not current or v == current):
            return t
        return ""
    return ""


def branch_of_tag(root: str, tag: str) -> str:
    """The branch a tag is on: the first local or remote branch that has it (main before the rest), "" when none."""
    if not tag:
        return ""
    got = git(["branch", "-a", "--format=%(refname:short)", "--contains", tag], root).splitlines()
    names = [b.strip().removeprefix("origin/") for b in got if b.strip() and "HEAD" not in b]
    for b in ("main", "master"):
        if b in names:
            return b
    return names[0] if names else ""


def gate_base(root: str, cfg: Config, gate: str, branch: str) -> str:
    """What a gate's diff is against: push, the branch's upstream (what GitHub hasn't got; or the default branch when it
    has none); merge, the branch the code merges into (the default branch, unless [branches] says a release branch);
    release, the newest release tag."""
    if gate == "release":
        return newest_tag(root, cfg, pre=False) or default_branch(root)
    if gate == "merge":
        for name, spec in cfg.table("branches").items():
            if isinstance(spec, dict) and spec.get("role") == "release" and "*" not in name and name != branch:
                return f"origin/{name}" if git(["rev-parse", "--verify", "--quiet", f"origin/{name}"], root) else name
        return default_branch(root)
    up = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], root)
    return up or default_branch(root)


def changed_lines(base: str, root: str = "", paths: list[str] | None = None) -> dict[str, set[int]]:
    """The lines the code has that ``base`` hasn't (committed since, and uncommitted), per file from the project's root:
    what a diff-mode check looks at. A file with none isn't in it."""
    out: dict[str, set[int]] = {}
    args = ["diff", "-U0", "--no-color", "--no-ext-diff", "--merge-base", base, "--", *(paths or [])]
    text = git(args, root or ROOT)
    if not text and base:
        text = git(["diff", "-U0", "--no-color", "--no-ext-diff", base, "--", *(paths or [])], root or ROOT)
    path = ""
    for line in text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            path = path[2:] if path.startswith("b/") else path
            if path == "/dev/null":
                path = ""
        elif line.startswith("@@") and path:
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start, n = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
                if n:
                    out.setdefault(path, set()).update(range(start, start + n))
    return out


# ---------------------------------------------------------------- the rules a tool reads
class Rules:
    """One integration's rules, resolved: ``level(name)`` for a level setting, ``value(name)`` and ``value_level(name)`` for
    a number, ``on(name)`` for a toggle, ``targets(name)`` for targets; ``skipped(name)`` when the mode leaves it out."""

    def __init__(self, id_: str, sch: Schema, merged: dict[str, Any], ctx: Context, cap: str = ""):
        self.id, self.schema, self.ctx, self.cap = id_, sch, ctx, cap
        self._v = merged
        self.default_level = merged.get("level", "warn")   # (what a number not met is, when the rule doesn't say)
        self.skipped_rules: list[str] = [n for n, s in sch.settings.items() if s.full_only and ctx.diff and n in merged]

    def _capped(self, level: str) -> str:
        if self.cap and LEVELS.index(level) < LEVELS.index(self.cap):
            return self.cap
        return level

    def skipped(self, name: str) -> bool:
        """Set, but not in this mode (a full-codebase rule in diff mode): the tool says so, never passes it quietly."""
        return name in self.skipped_rules

    def raw(self, name: str) -> Any:
        return self._v.get(name)

    def level(self, name: str) -> str:
        """A level setting: fail, warn, info or off (off when the file doesn't set it, or the mode skips it)."""
        self._setting(name, "level")
        v = self._v.get(name)
        if v is None or self.skipped(name):
            return "off"
        return self._capped(level_of(v, f"[{self.id}] {name}"))

    def value(self, name: str) -> float | None:
        """A threshold, window or count: its number, None when it's off (not set, or skipped in this mode)."""
        self._setting(name, "threshold", "window", "count")
        v = self._v.get(name)
        if v is None or v is False or self.skipped(name):
            return None
        if isinstance(v, dict):
            v = v.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ConfigError(f"[{self.id}] {name} = {v!r}: a number is wanted")
        return v

    def value_level(self, name: str) -> str:
        """What a number not met is: the rule's own level ({value = 98, level = "fail"}), else the integration's ``level``."""
        v = self._v.get(name)
        if v is None or self.skipped(name):
            return "off"
        if isinstance(v, dict) and "level" in v:
            return self._capped(level_of(v["level"], f"[{self.id}] {name}.level"))
        return self._capped(self.default_level)

    def on(self, name: str) -> bool:
        self._setting(name, "toggle")
        v = self._v.get(name)
        return bool(v) and not self.skipped(name)

    def text(self, name: str) -> str:
        self._setting(name, "text")
        v = self._v.get(name)
        return "" if v is None else str(v)

    def items(self, name: str) -> list[str]:
        """A list setting: its strings, [] when off."""
        self._setting(name, "list")
        v = self._v.get(name)
        if v is None or self.skipped(name):
            return []
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ConfigError(f"[{self.id}] {name}: a list of strings is wanted")
        return list(v)

    def targets(self, name: str) -> dict[str, float]:
        """Targets: {language or group: percentage}, "*" the rest; {} when off."""
        self._setting(name, "targets")
        v = self._v.get(name)
        if v is None or self.skipped(name):
            return {}
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return {"*": float(v)}
        if not isinstance(v, dict):
            raise ConfigError(f"[{self.id}] {name}: a table of targets is wanted ({{\"*\" = 100}})")
        out = {}
        for k, t in v.items():
            if k == "level":
                continue
            if isinstance(t, bool) or not isinstance(t, (int, float)):
                raise ConfigError(f"[{self.id}] {name}.{k} = {t!r}: a percentage is wanted")
            out[str(k)] = float(t)
        return out

    def target_for(self, name: str, language: str, groups: dict[str, list[str]] | None = None) -> float | None:
        """That language's target: its own, else its group's, else "*"'s; None when none applies."""
        t = self.targets(name)
        if language in t:
            return t[language]
        for g, members in (groups or {}).items():
            if language in members and g in t:
                return t[g]
        return t.get("*")

    def targets_level(self, name: str) -> str:
        v = self._v.get(name)
        if v is None or self.skipped(name):
            return "off"
        if isinstance(v, dict) and "level" in v:
            return self._capped(level_of(v["level"], f"[{self.id}] {name}.level"))
        return self._capped(self.default_level)

    def _setting(self, name: str, *kinds: str) -> Setting:
        s = self.schema.settings.get(name)
        if s is None:
            raise KeyError(f"{self.id}: no setting {name!r} in the {self.schema.kind} schema")
        if s.kind not in kinds:
            raise TypeError(f"{self.id}.{name} is a {s.kind}, not {' or '.join(kinds)}")
        return s

    def said(self) -> list[str]:
        """Each rule as it stands, for ``policy.py show`` and the Config tab: "fresh_days 14 (warn)", "warnings fail"."""
        out = []
        for name, s in self.schema.settings.items():
            if self.skipped(name):
                out.append(f"{name}: skipped in {self.ctx.mode} mode")
            elif s.kind == "level":
                out.append(f"{name}: {self.level(name)}")
            elif s.kind in ("threshold", "window", "count"):
                v = self.value(name)
                out.append(f"{name}: off" if v is None else f"{name}: {v:g}{s.unit if s.unit == '%' else ' ' + s.unit} ({self.value_level(name)})")
            elif s.kind == "toggle":
                out.append(f"{name}: {'on' if self.on(name) else 'off'}")
            elif s.kind == "list":
                out.append(f"{name}: " + (", ".join(self.items(name)) or "off"))
            elif s.kind == "targets":
                t = self.targets(name)
                out.append(f"{name}: off" if not t else f"{name}: " + ", ".join(f"{k} {v:g}%" for k, v in t.items()) + f" ({self.targets_level(name)})")
            else:
                out.append(f"{name}: {self.text(name) or 'off'}")
        return out


def rules(id_: str, ctx: Context | None = None, cfg: Config | None = None) -> Rules:
    """That integration's rules in the current context: what every tool calls."""
    cfg = cfg or Config(find_file())
    return cfg.rules(id_, ctx or context(cfg=cfg))


def worst(levels: list[str]) -> str:
    """The worst of those levels ("off" of none)."""
    return min(levels, key=LEVELS.index) if levels else "off"


def exit_code(levels: list[str], broken: bool = False) -> int:
    """A check's exit code from the levels of what it found: BROKEN when it couldn't run, else FAIL, WARN, INFO or PASS."""
    if broken:
        return BROKEN
    return RC_OF[worst([lv for lv in levels if lv != "off"])]


def state_of(rc: int) -> str:
    return {PASS: "pass", FAIL: "fail", BROKEN: "fail", INFO: "info", WARN: "warn", NOTRUN: "notrun"}.get(rc, "fail")


# ---------------------------------------------------------------- writing the file (the UI's save)
def dumps(data: dict, roles: tuple[str, ...] | list[str] = BUILTIN_ROLES) -> str:
    """The file as TOML (the UI's save: the whole file written again, comments not kept). A table for each top-level
    table, each entry of [integrations], [branches], [tags], [phases], [roles] and [gates], and each role's and diff's
    table under an integration's rules; everything else inline ({value = 98, level = "fail"}, {"*" = 100})."""
    lines: list[str] = []
    grouped = ("integrations", "branches", "phases", "roles", "gates")

    def key(k: str) -> str:
        return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k)

    def value(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, list):
            return "[" + ", ".join(value(x) for x in v) + "]"
        if isinstance(v, dict):
            return "{" + ", ".join(f"{key(k)} = {value(x)}" for k, x in v.items()) + "}"
        raise TypeError(f"{v!r} can't be written to TOML")

    def is_table(path: list[str], k: str, v: Any) -> bool:
        if not isinstance(v, dict):
            return False
        if not path:
            return True
        if path[0] in grouped:
            return len(path) == 1
        if path[0] == "tags":
            return False
        return k in roles or k == "diff"   # (an integration's rules: its role and diff tables)

    def table(path: list[str], d: dict):
        plain = {k: v for k, v in d.items() if not is_table(path, k, v)}
        subs = {k: v for k, v in d.items() if is_table(path, k, v)}
        if path and (plain or not subs):
            lines.append(("" if not lines else "\n") + "[" + ".".join(key(p) for p in path) + "]")
        for k, v in plain.items():
            lines.append(f"{key(k)} = {value(v)}")
        for k, v in subs.items():
            table([*path, k], v)

    table([], data)
    return "\n".join(lines) + "\n"


def save(cfg: Config, data: dict) -> str:
    """Writes the file whole, checked first (Config(data).check()); returns the diff the UI shows."""
    if cfg.read_only:
        raise ConfigError(f"{cfg.path} is read only ([config] read_only): change the file itself")
    Config(cfg.path, data).check()
    text = dumps(data, Config(cfg.path, data).roles)
    with open(cfg.path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(cfg.path + ".tmp", cfg.path)
    return text


def _check(self: Config) -> list[str]:
    """What's wrong with the file against the schemas: every integration's every table, every role's, every gate's.
    Raises ConfigError on the first."""
    for i in self.integrations(all_=True):
        for role in ["", *self.roles]:
            for diff in (False, True):
                ctx = Context(role=role or "feature", diff=diff)
                self.rules(i["id"], ctx).said()
    for name, spec in self.table("branches").items():
        if isinstance(spec, dict):
            self.branch_role(name if name != "*" else "whatever")
    for phase in self.table("phases"):
        if phase not in PHASES:
            raise ConfigError(f"[phases.{phase}]: not a phase ({', '.join(PHASES)})")
        self.phase_role(phase)
    for gate, spec in self.table("gates").items():
        if gate not in ("push", "merge", "release"):
            raise ConfigError(f"[gates.{gate}]: not a gate (push, merge, release)")
        if spec.get("github") not in (None, True, False, "warn"):
            raise ConfigError(f"[gates.{gate}] github: true, false or \"warn\"")
    for role in self.table("roles"):
        self.role_chain(role)
        self.role_cap(role)
    ids = {i["id"] for i in self.integrations(all_=True)}
    for i in self.integrations(all_=True):
        for a in i["after"] + i["stop_with"]:
            if a not in ids and a != "sync":
                raise ConfigError(f"[integrations.{i['id']}] after = {a!r}: no such integration")
    known = {"config", "branches", "tags", "phases", "roles", "github", "gates", "integrations", *ids}
    for k in self.data:
        if k not in known:
            raise ConfigError(f"[{k}]: not a table of {FILE} (an integration's rules go under its id in [integrations])")
    return []




# ---------------------------------------------------------------- the command line
def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print((__doc__ or "").split("\n\n")[1])
        return 0
    cmd, rest = args[0], args[1:]
    opts: dict[str, str] = {"code": "", "gate": "", "branch": "", "diff": "", "repo": ""}
    while rest:
        a = rest.pop(0)
        if a == "--diff":
            opts["diff"] = "1"
        elif a in ("--code", "--gate", "--branch", "--repo") and rest:
            opts[a[2:]] = rest.pop(0)
        else:
            print(f"policy.py: unknown option {a}", file=sys.stderr)
            return 2
    if cmd == "init":
        target = os.path.join(opts["repo"] or git(["rev-parse", "--show-toplevel"], os.getcwd()) or os.getcwd(), FILE)
        if os.path.exists(target):
            print(f"{target} already exists")
            return 0
        with open(STARTER, encoding="utf-8") as fh, open(target, "w", encoding="utf-8") as out:
            out.write(fh.read())
        print(f"wrote {target}")
        return 0
    try:
        cfg = Config(find_file(opts["repo"] or None))
        if cmd == "check":
            cfg.check()
            print(f"{cfg.path}: ok")
            return 0
        ctx = context(code=opts["code"], gate=opts["gate"], branch=opts["branch"], diff=True if opts["diff"] else None, cfg=cfg)
        if cmd == "env":
            for k, v in ctx.env().items():
                print(f"{k}={v}")
            print(f"DEVTOOLS_HEAD={ctx.head()}")
            print(f"DEVTOOLS_COMMIT_REF={ctx.commit}")
            return 0
        if cmd == "integrations":
            for i in cfg.integrations():
                print("\t".join([i["id"], i["title"], i["letter"], i["kind"], ",".join(i["after"]), i["cmd"]]))
            return 0
        if cmd == "show":
            print(f"{ctx.head()} · role {ctx.role} · {ctx.mode} mode" + (f" · {ctx.gate} gate (against {ctx.base})" if ctx.gate else ""))
            for i in cfg.integrations(all_=True):
                state = "" if i["enabled"] and i["ready"] else " (hidden: " + ("off" if not i["enabled"] else "not set up") + ")"
                print(f"\n[{i['id']}] {i['title']}{state}")
                for line in cfg.rules(i["id"], ctx).said():
                    print("  " + line)
            return 0
        if cmd == "json":   # (the UI: the file, the integrations and the context, as one object)
            print(json.dumps({"path": cfg.path, "read_only": cfg.read_only, "data": cfg.data, "context": ctx.__dict__,
                              "integrations": cfg.integrations(all_=True), "roles": cfg.roles,
                              "schemas": {k: {"title": schema(k).title, "doc": schema(k).doc, "settings": {n: s.__dict__ for n, s in schema(k).settings.items()}}
                                          for k in {i["kind"] for i in cfg.integrations(all_=True)}}}, default=str))
            return 0
    except ConfigError as e:
        print(f"devtools.toml: {e}", file=sys.stderr)
        return 2
    print(f"policy.py: unknown command {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
