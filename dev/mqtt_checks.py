#!/usr/bin/env python3
"""Two release checks that are this project's own (callboard.toml's [release] own_checks: callboard's Release checks
run each, and hold it as its ``own`` rule says at the gate):

    python3 dev/mqtt_checks.py settings   # every setting a control over MQTT, or listed as kept off it
    python3 dev/mqtt_checks.py docs       # the README's MQTT topics and commands the ones the code has

Each exits 0 when it's met. Its last line is what came of it; the lines before, what to do.
"""

import ast
import fnmatch
import os
import re
import sys
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:  # (run as a script: dev/ is on the path, not the project)
    sys.path.insert(0, ROOT)


@dataclass
class Result:
    """One check's result: its name, whether it passed, and what came of it in a line."""
    name: str
    ok: bool
    line: str                                         # what came of it, in a line
    todo: list[str] = field(default_factory=list)     # for one that failed: what's wrong, and what to do


def check_mqtt_settings() -> Result:
    """The MQTT settings check: every setting Home Assistant reads is in the app's settings."""
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
    """The MQTT docs check: the README says every entity the app publishes."""
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


CHECKS = {"settings": check_mqtt_settings, "docs": check_mqtt_docs}


def main(argv: list[str]) -> int:
    """``python3 dev/mqtt_checks.py settings|docs``: the check run, what to do said first, what came of it last."""
    if len(argv) != 2 or argv[1] not in CHECKS:
        print("python3 dev/mqtt_checks.py settings|docs", file=sys.stderr)
        return 2
    got = CHECKS[argv[1]]()
    for line in got.todo:
        print(line)
    print(got.line)
    return 0 if got.ok else 1


if __name__ == "__main__":
    import cli  # (dev/cli.py: what every dev script's command line does the same)
    _answered = cli.front(sys.argv[1:], __doc__, "python3 dev/mqtt_checks.py", commands=("settings", "docs"))
    sys.exit(main(sys.argv) if _answered is None else _answered)
