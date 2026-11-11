#!/usr/bin/env python3
"""The release checks: what has to be true of dev before it's released (merged into main and tagged). callboard runs them
in a block of their own, on the code it copies; what each is held as — told (info) between releases, a failure at
release — is callboard.toml's [release] for the code's role (callboard/policy.py), so a dev run can't fail on what only a
release needs.

    python3 dev/release.py           # each check, a line each; what to do for one that fails; the exit code callboard/policy.py's
    python3 dev/release.py --callboard  # callboard's Release block, every run: the same checks as an overall readiness —
                                     # "4 of 7 ready", what's still to go — grey between releases (exit 3), red (1)
                                     # where the role holds them as failures
    python3 dev/release.py start     # tag the code a preview (v0.17.0-beta.1: callboard.toml's [tags]); push the tag to start it
    python3 dev/release.py candidate # tag a release candidate (v0.17.0-rc.1): the freeze, and the release pull request
    python3 dev/release.py hotfix <name>   # a hotfix/<name> branch off the release branch
    python3 dev/release.py tick <item>     # a manual release item done (the UI's Gates page does the same)
    python3 dev/release.py notes     # the release notes: the change notes written since the last release, joined (the GitHub Release's body)
    python3 dev/release.py bump      # after a release: __version__ to the next X.Y.0 (the next-version pull request on GitHub)

The checks:
* the version: plexcache/__init__.py's __version__ a new X.Y.0, above what's released (main's version and the newest
  release tag), and not tagged yet — dev's builds raise the last number (0.16.22), a release starts the next (0.17.0);
* the translations: every text translated, in every language (in step with the code: the suite's, as for a release);
* the MQTT settings: every setting (plexcache/config.py's DEFAULTS) either a control over MQTT or listed as kept off it,
  with why (plexcache/homeassistant.py: CONTROL_SETTINGS, PER_LIBRARY_CONTROLS, NOT_OVER_MQTT), and nothing listed that's gone;
* the MQTT docs: the README's topics and commands (MQTT – other tools) the ones the code has;
* the coverage: the release bar (callboard/coverage.py: 98% of every line run, each file at 90%), from what the tests saved;
* the security audits: dev/audit.py's release rules, as a release's code holds them;
* the looks: every look the app wears approved into the style guide (dev/style.py), none waiting;
* the change notes: one written since the last release (a file under the notes folder, callboard.toml's [release] notes_dir);
* the manual items: each of callboard.toml's [release] manual_items ticked for the version (the UI's Gates page, or tick).

The released version is read from git: the repository callboard runs in (CALLBOARD_REPO, which callboard sets, since the code it
tests may be a copy from git rather than a checkout), else this folder.
"""

import ast
import fnmatch
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:  # (run as a script: dev/ is on the path, not the project)
    sys.path.insert(0, ROOT)

import dev  # noqa: E402, F401 — (puts the callboard install on the path: dev/__init__.py)

from callboard import checks, notes, policy  # noqa: E402 — the check tools' shared code, the notes and ticks, the rules (callboard.toml)
from callboard.checks import Check, Finding, Outcome  # noqa: E402

VERSION_FILE = os.path.join(ROOT, "plexcache", "__init__.py")
# The rules (callboard.toml's [release]): what each check is held as. Between releases told (info), at release a check (fail).
SCHEMA = policy.Schema("release", "Release checks", {
    "version": policy.Setting("level", "the version a new X.Y.0 above what's released, not tagged yet"),
    "translations": policy.Setting("level", "every text translated in every language"),
    "mqtt_settings": policy.Setting("level", "every setting a control over MQTT, or listed as kept off it"),
    "mqtt_docs": policy.Setting("level", "the README's MQTT topics and commands the ones the code has"),
    "coverage": policy.Setting("level", "the coverage bars of the release role met", full_only=True),
    "audits": policy.Setting("level", "the security audits' release rules met", full_only=True),
    "looks": policy.Setting("level", "every look approved into the style guide"),
    "change_notes": policy.Setting("level", "a change note (a file under notes_dir) written since the last release"),
    "manual": policy.Setting("level", "every manual release item (manual_items) ticked, on the UI's Gates page"),
    "manual_items": policy.Setting("list", "what a person does for a release, each ticked in the UI before it goes"),
    "notes_dir": policy.Setting("text", "where the change notes are written as the work goes (changes)"),
}, "What has to be true before the code is released.")
RELEASED_ON = "origin/main"   # what's released: main, as GitHub has it
TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")   # a release tag (v0.16.0); a pre-release (v0.16.0-beta.1) isn't one


@dataclass
class Result:
    name: str
    ok: bool
    line: str                                         # what came of it, in a line
    todo: list[str] = field(default_factory=list)     # for one that failed: what's wrong, and what to do


def _repo() -> str:
    """The repository worked on: CALLBOARD_REPO (callboard, the UI: the project the page serves), else this one."""
    return os.environ.get("CALLBOARD_REPO") or ROOT


def _version_file() -> str:
    return os.path.join(_repo(), "plexcache", "__init__.py") if os.environ.get("CALLBOARD_REPO") else VERSION_FILE


def _git(*args: str) -> str | None:
    repo = _repo()
    try:
        got = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return got.stdout if got.returncode == 0 else None


def version_of(text: str) -> str | None:
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    return m.group(1) if m else None


def parse(version: str) -> tuple[int, int, int] | None:
    m = TAG.match(version.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def released() -> tuple[tuple[int, int, int] | None, str]:
    """The newest version released — main's, or a release tag's, whichever is newer — and where it was read."""
    found: list[tuple[tuple[int, int, int], str]] = []
    main = _git("show", f"{RELEASED_ON}:plexcache/__init__.py")
    v = parse(version_of(main) or "") if main else None
    if v:
        found.append((v, f"{RELEASED_ON.split('/')[-1]} has {'.'.join(map(str, v))}"))
    for tag in (_git("tag", "-l") or "").split():
        t = parse(tag)
        if t:
            found.append((t, f"tag {tag}"))
    if not found:
        return None, ""
    return max(found)


def check_version(text: str | None = None) -> Result:
    name = "Version"
    if text is None:
        try:
            with open(VERSION_FILE, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as e:
            return Result(name, False, f"can't read plexcache/__init__.py: {e.strerror}")
    have = version_of(text)
    if not have:
        return Result(name, False, "no __version__ in plexcache/__init__.py")
    v = parse(have)
    if not v:
        return Result(name, False, f"{have} isn't a release version (X.Y.Z)",
                      [f"Set __version__ in plexcache/__init__.py to the release, a new X.Y.0 (say {have.split('-')[0]})."])
    top, where = released()
    if top is None:
        return Result(name, False, f"{have}: can't tell what's released (no {RELEASED_ON}, no release tags)",
                      [f"git fetch origin, so {RELEASED_ON} is there to compare with."])
    nxt = f"{top[0]}.{top[1] + 1}.0"
    if v[2] != 0:
        return Result(name, False, f"{have} isn't a new X.Y.0 ({where})",
                      [f"A release starts a new minor (or major) version with .0: set __version__ in plexcache/__init__.py to {nxt}."])
    if v[:2] <= top[:2]:
        return Result(name, False, f"{have} isn't above what's released ({where})",
                      [f"Set __version__ in plexcache/__init__.py to {nxt} (or a new major, {top[0] + 1}.0.0)."])
    if (_git("tag", "-l", f"v{have}") or "").strip():
        return Result(name, False, f"{have} is tagged already", [f"v{have} exists: set __version__ to {nxt}."])
    return Result(name, True, f"{have}, a new release ({where})")


def check_translations() -> Result:
    """Every text translated in every language. (Only that: the catalogs in step with the code, and each translation
    matching its English, are the suite's TranslationTests, which callboard --release runs as for a release.)"""
    from dev import i18n
    name = "Translations"
    keys = i18n.all_keys([])
    left = [(code, len(i18n.untranslated(keys, i18n.load(code)))) for code in i18n.TRANSLATED]
    left = [(code, n) for code, n in left if n]
    if not left:
        return Result(name, True, f"all {len(i18n.TRANSLATED)} languages complete: {len(keys)} texts, every one translated")
    todo = [f"{code}: {n} untranslated" for code, n in left]
    todo += ["For the release: python3 dev/i18n.py sync (every catalog in step), then translate what's null;",
             "python3 dev/i18n.py status says how far each language is."]
    return Result(name, False, f"{len(left)} of {len(i18n.TRANSLATED)} languages not complete "
                               f"(up to {max(n for _, n in left)} of {len(keys)} texts untranslated)", todo)


def check_mqtt_settings() -> Result:
    from plexcache import config
    from plexcache import homeassistant as ha
    name = "MQTT settings"
    settings = [f"{sec}.{key}" for sec, keys in config.DEFAULTS.items() for key in keys]
    controls = {f"{sec}.{key}" for sec, key in ha.CONTROL_SETTINGS.values()} | set(ha.PER_LIBRARY_CONTROLS)
    kept_off = list(ha.NOT_OVER_MQTT)
    unplaced = [p for p in settings if p not in controls and not any(fnmatch.fnmatchcase(p, k) for k in kept_off)]
    gone = sorted([k for k in kept_off if not any(fnmatch.fnmatchcase(p, k) for p in settings)]
                  + [c for c in controls if c not in settings])
    if not unplaced and not gone:
        over = sum(1 for p in settings if p in controls)
        return Result(name, True, f"each of the {len(settings)} settings placed: {over} over MQTT, the rest kept off it, each with why")
    todo = [f"{p}: not a control over MQTT, nor in NOT_OVER_MQTT" for p in unplaced]
    todo += [f"{k}: listed in plexcache/homeassistant.py, but there's no such setting" for k in gone]
    todo += ["Make each new setting a control (CONTROL_SETTINGS, and its entity) or add it to NOT_OVER_MQTT with why;",
             "take out what's no longer a setting."]
    said = [f"{len(unplaced)} settings not placed" if unplaced else "", f"{len(gone)} listed that are gone" if gone else ""]
    return Result(name, False, ", ".join(x for x in said if x), todo)


README = os.path.join(ROOT, "README.md")


def mqtt_commands(source: str) -> set[str]:
    """The commands homeassistant.py's _command takes: each name it compares with, and those it takes by their start and
    end, as the README writes them (name.startswith("library_") and name.endswith("_prefetch_start"):
    library_<key>_prefetch_start)."""
    names: set[str] = set()
    patterns: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "_command":
            for n in ast.walk(node):
                if isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) and n.left.id == "name":
                    for c in n.comparators:
                        for v in (c.elts if isinstance(c, ast.Tuple) else [c]):
                            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                                names.add(v.value)
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("startswith", "endswith")
                        and isinstance(n.func.value, ast.Name) and n.func.value.id == "name"):
                    for a in n.args:
                        if isinstance(a, ast.Constant) and isinstance(a.value, str):
                            patterns.add(f"{n.func.attr}:{a.value}")
    starts = sorted(p.split(":", 1)[1] for p in patterns if p.startswith("startswith:"))
    ends = [p.split(":", 1)[1] for p in patterns if p.startswith("endswith:")]
    ends = [e for e in ends if not any(o != e and o.endswith(e) for o in ends)]   # ("_start" is "_prefetch_start"'s end)
    names |= {f"{s}<key>{e}" for s in starts for e in ends}
    return names


def check_mqtt_docs(readme: str | None = None, source: str | None = None) -> Result:
    name = "MQTT docs"
    try:
        if readme is None:
            with open(README, encoding="utf-8") as fh:
                readme = fh.read()
        if source is None:
            with open(os.path.join(ROOT, "plexcache", "homeassistant.py"), encoding="utf-8") as fh:
                source = fh.read()
    except OSError as e:
        return Result(name, False, f"can't read {e.filename}: {e.strerror}")
    topics_code = set(re.findall(r"\{base\}/([a-z_]+)", source))
    rows = re.findall(r"^\| `plexcache/([a-z_]+)[^`]*` \|(.*)$", readme, re.M)
    topics_doc = {t for t, _ in rows}
    commands = mqtt_commands(source)
    said_row = next((rest for t, rest in rows if t == "set"), "")
    words = set(re.findall(r"`([a-z_<>]+)`", said_row))
    todo = [f"topic plexcache/{t}: published, not in the README's table" for t in sorted(topics_code - topics_doc)]
    todo += [f"topic plexcache/{t}: in the README's table, not published" for t in sorted(topics_doc - topics_code)]
    todo += [f"command {c}: taken, not in the README's plexcache/set/<name> row" for c in sorted(commands - words)]
    todo += [f"command {w}: in the README's plexcache/set/<name> row, not taken" for w in sorted(w for w in words - commands if "_" in w)]
    if not rows:
        todo.insert(0, "no table of plexcache/… topics in the README (MQTT – other tools)")
    if not todo:
        return Result(name, True, f"the README has the {len(topics_code)} topics and {len(commands)} commands the code has")
    todo.append("README.md → MQTT – other tools: bring its table in step with plexcache/homeassistant.py.")
    return Result(name, False, f"the README's MQTT table differs from the code ({len(todo) - 1})", todo)


def check_coverage() -> Result:
    """The release's coverage bar (callboard/coverage.py): 98% of every line run, each file at 90%, from what the tests saved."""
    from callboard import coverage
    name = "Coverage"
    saved, why = coverage.load()
    if saved is None:
        return Result(name, False, "not measured", [why])
    r = policy.rules("coverage", policy.context().__class__(**{**policy.context().__dict__, "role": "release", "diff": False}))
    files, _ = coverage.measure(saved["hit"], fresh_days=r.value("fresh_days"))
    outcome = coverage.release_check(files, r.value("every_line"), r.value("each_file"))
    if r.value("every_line") is None and r.value("each_file") is None:
        return Result(name, True, "no coverage bar set for the release role (callboard.toml [coverage.release])")
    line = outcome.facts[0] if outcome.facts else ""
    if not outcome.findings:
        return Result(name, True, line)
    todo = [f.message if f.path == "plexcache" else f"{f.path}: {f.message}" for f in outcome.findings[:15]]
    todo.append("callboard tool coverage --missing says which lines; add tests (or run where the optimizer's tools are).")
    return Result(name, False, line, todo)


def check_audits() -> Result:
    """The security audits' release rules (dev/audit.py --release), as a release run holds them."""
    from callboard.progress import FEED
    name = "Security audits"
    try:
        env = {k: v for k, v in os.environ.items() if k != "PLEXCACHE_PROGRESS"}   # (its words for a reader, not the board's feed)
        env.update(policy.context().__class__(**{**policy.context().__dict__, "role": "release", "diff": False}).env())   # (as the release holds it)
        env["CALLBOARD_INTEGRATION"] = "audit"
        got = subprocess.run([sys.executable, os.path.join(ROOT, "dev", "audit.py")],
                             capture_output=True, text=True, timeout=300, cwd=ROOT, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Result(name, False, f"couldn't be checked: {e}")
    lines = [ln.strip() for ln in got.stdout.splitlines() if not ln.startswith(FEED) and ln.strip()]
    line = lines[-1] if lines else f"exit {got.returncode}"
    if got.returncode in (policy.PASS, policy.INFO):
        return Result(name, True, line)
    return Result(name, False, line, lines[:-1][:15] + ["python3 dev/audit.py gui, and work through what the release rules still need."])


def check_looks() -> Result:
    """Every look the app wears approved into the style guide (dev/style.py): none waiting for the maintainer."""
    from dev import style
    name = "Looks"
    diff = style.pending()
    todo = [f"{state}: {look}" for state in diff for look in diff[state]]
    if not todo:
        return Result(name, True, "every look is in the style guide")
    return Result(name, False, f"{len(todo)} looks waiting for approval",
                  todo[:15] + ["python3 dev/style.py review, and approve what's right."])


def notes_dir() -> str:
    r = checks.levels("release")
    return (r.text("notes_dir") if r else "") or "changes"


def last_release_tag() -> str:
    """The newest release tag ([tags] release), "" when there's none yet."""
    try:
        cfg = policy.Config(policy.find_file())
    except policy.ConfigError:
        return ""
    return policy.newest_tag(_repo(), cfg, pre=False)


def change_notes() -> list[str]:
    """The change notes written since the last release: the .md files added under the notes folder since the release tag
    (committed or not), in name order (callboard/notes.py: what the UI's Gates page lists)."""
    return notes.change_notes(_repo(), notes_dir(), last_release_tag())


def release_notes() -> str:
    """The release notes: each change note's text, in name order, joined with a blank line."""
    parts = []
    for rel in change_notes():
        try:
            with open(os.path.join(_repo(), rel), encoding="utf-8") as fh:
                parts.append(fh.read().strip())
        except OSError:
            continue
    return "\n\n".join(p for p in parts if p) + ("\n" if parts else "")


def bump() -> str:
    """__version__ to the next minor (X.Y+1.0) after a release: what dev's work goes on as. Returns the new version."""
    with open(VERSION_FILE, encoding="utf-8") as fh:
        text = fh.read()
    have = parse(version_of(text) or "")
    if not have:
        raise ValueError(f"no X.Y.Z __version__ in {VERSION_FILE}")
    new = f"{have[0]}.{have[1] + 1}.0"
    text = re.sub(r'^(__version__\s*=\s*["\'])[^"\']+(["\'])', lambda m: f"{m.group(1)}{new}{m.group(2)}", text, count=1, flags=re.M)
    with open(VERSION_FILE, "w", encoding="utf-8") as fh:
        fh.write(text)
    return new


def check_change_notes() -> Result:
    """A change note written since the last release: a file under the notes folder added (committed or not) since the
    release tag — the notes are written as the work goes, and the release's notes are made of them."""
    name = "Change notes"
    where = notes_dir()
    tag = last_release_tag()
    added = change_notes()
    if added:
        return Result(name, True, f"{len(added)} written since {tag or 'the start'}: " + ", ".join(sorted(os.path.basename(a) for a in added)[:5]))
    return Result(name, False, f"none written since {tag or 'the start'}",
                  [f"Write what changed, for the release notes, as a file under {where}/ (one per change: {where}/<name>.md)."])


def manual_file(version: str) -> str:
    return notes.manual_file(_repo(), notes_dir(), version)


def manual_state(version: str) -> dict[str, bool]:
    """What's ticked of the manual release items for that version (the UI's Gates page writes it: callboard/notes.py)."""
    return notes.manual_state(_repo(), notes_dir(), version)


def check_manual() -> Result:
    """Every manual release item ticked (callboard.toml's [release] manual_items; the ticks per version, in the notes folder)."""
    name = "Manual items"
    r = checks.levels("release")
    items = r.items("manual_items") if r else []
    if not items:
        return Result(name, True, "none to do (callboard.toml's [release] manual_items)")
    try:
        with open(VERSION_FILE, encoding="utf-8") as fh:
            version = version_of(fh.read()) or ""
    except OSError:
        version = ""
    ticked = manual_state(version)
    left = [i for i in items if not ticked.get(i)]
    if not left:
        return Result(name, True, f"all {len(items)} ticked for {version}")
    return Result(name, False, f"{len(left)} of {len(items)} to do for {version}",
                  [*left, "Tick each one on the callboard UI's Gates page once it's done (or python3 dev/release.py tick <item>)."])


CHECKS: list[Callable[[], Result]] = [check_version, check_translations, check_mqtt_settings, check_mqtt_docs,
                                      check_coverage, check_audits, check_looks, check_change_notes, check_manual]
KEYS = {check_version: "version", check_translations: "translations", check_mqtt_settings: "mqtt_settings",
        check_mqtt_docs: "mqtt_docs", check_coverage: "coverage", check_audits: "audits", check_looks: "looks",
        check_change_notes: "change_notes", check_manual: "manual"}


def summary(results: list[Result]) -> str:
    """The block's line on callboard's board: the ones that failed, else that all passed."""
    failed = [r for r in results if not r.ok]
    if not failed:
        return f"all {len(results)} passed: " + "; ".join(f"{r.name.lower()} {r.line}" for r in results)
    return "; ".join(f"{r.name}: {r.line}" for r in failed)


def main(argv: list[str] | None = None) -> int:
    args = ["--callboard" if a == "--tnsync" else a for a in (sys.argv[1:] if argv is None else argv)]   # (the flag's old name still read)
    if args[:1] == ["notes"]:
        sys.stdout.write(release_notes())
        return 0
    if args[:1] == ["bump"]:
        try:
            print(bump())
        except (OSError, ValueError) as e:
            print(e)
            return 1
        return 0
    if args[:1] and args[0] in ("start", "candidate", "hotfix", "tick"):
        try:
            return step(args[0], args[1:])
        except policy.ConfigError as e:
            print(f"callboard.toml: {e}")
            return 2
    board = "--callboard" in args   # (callboard's Release block, every run: readiness between releases, the check on one)
    rules = checks.levels("release")
    levels = {k: (rules.level(k) if rules else "fail") for k in KEYS.values()}
    skipped = [k for k in KEYS.values() if rules and rules.skipped(k)]
    results: list[Result] = []
    for check in CHECKS:
        key = KEYS.get(check) or check.__name__.removeprefix("check_")
        levels.setdefault(key, (rules.default_level if rules else "fail"))   # (a check without a rule of its own: the integration's level)
        if levels[key] == "off":
            continue
        try:
            results.append(check())
        except Exception as e:  # noqa: BLE001 — (a check that breaks is a check that failed, said as such)
            results.append(Result(check.__name__.removeprefix("check_").title(), False, f"couldn't be checked: {e}"))
    keys = {r.name: (KEYS.get(c) or c.__name__.removeprefix("check_")) for c in CHECKS for r in results if r.name == _name(c)}
    for r in results:
        print(f"{'✔' if r.ok else '✘'} {r.name}: {r.line}")
        for t in r.todo:
            print(f"    {t}")
    for k in skipped:
        print(f"  ({k} is skipped in diff mode: it holds for the whole codebase)")
    strict = any(lv == "fail" for lv in levels.values())
    ready = sum(1 for r in results if r.ok)
    togo = ", ".join(r.name.lower() for r in results if not r.ok)
    page = {"kind": "release", "strict": strict, "ready": ready, "total": len(results), "skipped": skipped,
            "checks": [{"name": r.name, "ok": r.ok, "line": r.line, "todo": list(r.todo), "level": levels.get(keys.get(r.name, ""), "")} for r in results]}
    if strict or not board:
        facts = [summary(results)]
    else:
        facts = [f"{ready} of {len(results)} ready" + (f" · to go: {togo}" if togo else " · ready to release")]
    # each requirement its own check, so the bar counts them: met, or missed at its level (the shared checks code)
    return checks.run([Check(r.name, unit(r, keys.get(r.name, ""), levels.get(keys.get(r.name, ""), "info"))) for r in results],
                      bar="release", rules=rules, more=page, met="requirements met", facts=facts,
                      notes={"info": "in progress", "fail": "not ready", "warn": "to go"})


def unit(r: Result, key: str, level: str) -> Callable[[], Outcome]:
    """A requirement as the shared checks code runs it: met (nothing found), or its line a finding held at its level."""
    found = [] if r.ok else [Finding("Release", "", 0, 0, "error" if level == "fail" else "warning", f"{r.name}: {r.line}", key, level=level)]
    return lambda: Outcome(r.name, "", found)


def _name(check: Callable[[], Result]) -> str:
    return {"check_version": "Version", "check_translations": "Translations", "check_mqtt_settings": "MQTT settings",
            "check_mqtt_docs": "MQTT docs", "check_coverage": "Coverage", "check_audits": "Security audits", "check_looks": "Looks",
            "check_change_notes": "Change notes", "check_manual": "Manual items"}.get(check.__name__, check.__name__.removeprefix("check_").title())


# ---------------------------------------------------------------- the release's steps
def tag_named(cfg: policy.Config, phase: str, version: str, n: int) -> str:
    pat = cfg.table("tags").get(phase)
    if not isinstance(pat, str) or not pat:
        raise policy.ConfigError(f"[tags] has no {phase} pattern")
    return pat.replace("{version}", version).replace("{n}", str(n))


def next_tag(cfg: policy.Config, phase: str, version: str) -> str:
    """The next tag of that phase for the version: the number after the highest tagged (1 when none)."""
    pat = cfg.tag_pattern(phase)
    highest = 0
    for t in policy.tags_sorted(_repo()):
        m = re.match(pat, t)
        if m and m.group("version") == version:
            highest = max(highest, int(m.group("n")))
    return tag_named(cfg, phase, version, highest + 1)


def step(cmd: str, args: list[str]) -> int:
    """start: tag the code a preview (a beta); candidate: a release candidate (the freeze); hotfix <name>: a branch off
    the release branch for a fix to what's released; tick <item>: a manual item done. The tag is made here, not pushed:
    the push is yours (it starts the release process on GitHub), and the command to run is printed."""
    cfg = policy.Config(policy.find_file())
    with open(_version_file(), encoding="utf-8") as fh:
        version = version_of(fh.read()) or ""
    if cmd in ("start", "candidate"):
        if not parse(version) or parse(version)[2] != 0:   # type: ignore[index]
            print(f"__version__ is {version!r}: a release is a new X.Y.0 (set it in plexcache/__init__.py first)")
            return 1
        phase = "preview" if cmd == "start" else "freeze"
        if not cfg.phase_role(phase):
            print(f"[phases.{phase}] has no role in callboard.toml: a {phase} tag would change nothing")
            return 1
        tag = next_tag(cfg, phase, version)
        msg = f"{version} {'preview' if phase == 'preview' else 'release candidate'}"
        got = subprocess.run(["git", "-C", ROOT, "tag", "-a", tag, "-m", msg], capture_output=True, text=True)
        if got.returncode != 0:
            print(got.stderr.strip() or f"git tag {tag} failed")
            return 1
        print(f"tagged {tag} ({msg}): the code is in its {phase} phase from here — the {cfg.phase_role(phase)} role's rules hold.")
        print(f"To start it on GitHub: git push origin {tag}")
        return 0
    if cmd == "hotfix":
        if not args:
            print("usage: python3 dev/release.py hotfix <name>")
            return 2
        base = next((n for n, spec in cfg.table("branches").items() if isinstance(spec, dict) and spec.get("role") == "release" and "*" not in n), "main")
        name = f"hotfix/{args[0]}"
        got = subprocess.run(["git", "-C", ROOT, "checkout", "-b", name, f"origin/{base}" if _git("rev-parse", "--verify", f"origin/{base}") else base],
                             capture_output=True, text=True)
        if got.returncode != 0:
            print(got.stderr.strip() or f"git checkout -b {name} failed")
            return 1
        print(f"on {name}, from {base}: fix, bump the patch version, then open a pull request into {base}; merging it is the release, and {base} merges back into dev.")
        return 0
    if cmd == "tick":
        if not args:
            print("usage: python3 dev/release.py tick <item> [--undo]")
            return 2
        print(notes.tick(_repo(), notes_dir(), version, args[0], "--undo" not in args))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
