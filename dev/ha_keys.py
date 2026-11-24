#!/usr/bin/env python3
"""Home Assistant's MQTT discovery rules, held against the keys the app's discovery messages use (DISCOVERY_KEYS in
plexcache/homeassistant.py): each one still taken by a Home Assistant release, not deprecated or removed.

    python3 dev/ha_keys.py 2026.11.2    # that release's sources read; exits 1 naming each key it no longer takes

Callboard's Dependencies runs it before it moves HA_CHECKED to a new release (callboard.toml's [upkeep] also: the
"Home Assistant (MQTT discovery)" line's check=): the pin moves only when every key is still good.
"""

import ast
import os
import sys
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:  # (run as a script: dev/ is on the path, not the project)
    sys.path.insert(0, ROOT)
import dev  # noqa: E402, F401 — (puts the callboard install on the path: dev/__init__.py)

from callboard import upkeep  # noqa: E402 — its connections to upstream (every address tried, the proxy's settings)

_get = upkeep._get

HA = os.path.join(ROOT, "plexcache", "homeassistant.py")   # what the MQTT discovery messages use (DISCOVERY_KEYS), checked as of HA_CHECKED
HA_SOURCE = "https://raw.githubusercontent.com/home-assistant/core/{tag}/homeassistant/{path}"
HA_FILES = ("components/mqtt/abbreviations.py", "components/mqtt/const.py", "const.py", "components/mqtt/schemas.py",
            "components/mqtt/entity.py", "components/mqtt/config.py", "components/mqtt/sensor.py",
            "components/mqtt/binary_sensor.py", "components/mqtt/switch.py", "components/mqtt/button.py",
            "components/mqtt/number.py", "components/mqtt/select.py", "components/mqtt/update.py")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def discovery_keys() -> dict[str, set[str]]:
    """DISCOVERY_KEYS from plexcache/homeassistant.py, read (not imported: upkeep runs without the app's imports)."""
    for node in ast.walk(ast.parse(_read(HA))):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DISCOVERY_KEYS" for t in node.targets):
            return {k: set(v) for k, v in ast.literal_eval(node.value).items()}
    raise ValueError("no DISCOVERY_KEYS in plexcache/homeassistant.py")


def ha_rules(sources: dict[str, str]) -> tuple[set[str], set[str]]:
    """From Home Assistant's MQTT sources (path -> text): every discovery key it takes (its abbreviations' full names and
    every CONF_ name the MQTT component defines), and those deprecated or removed (cv.deprecated / cv.removed)."""
    conf: dict[str, str] = {}
    taken: set[str] = set()
    for path, text in sources.items():
        tree = ast.parse(text)
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id.startswith("CONF_"):
                        conf[t.id] = node.value.value
                        if "mqtt" in path:
                            taken.add(node.value.value)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) and path.endswith("abbreviations.py"):
                taken |= {v.value for v in node.value.values if isinstance(v, ast.Constant) and isinstance(v.value, str)}
    gone: set[str] = set()
    for text in sources.values():
        for node in ast.walk(ast.parse(text)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("deprecated", "removed")
                    and node.args):
                a = node.args[0]
                key = conf.get(a.id) if isinstance(a, ast.Name) else a.value if isinstance(a, ast.Constant) else None
                if isinstance(key, str):
                    gone.add(key)
    return taken - gone, gone


def ha_check(version: str) -> list[str]:
    """The keys of DISCOVERY_KEYS Home Assistant ``version`` doesn't take, or has deprecated: what's wrong, [] when none."""
    sources: dict[str, str] = {}
    for path in HA_FILES:
        try:
            sources[path] = _get(HA_SOURCE.format(tag=version, path=path))
        except urllib.error.HTTPError as e:   # (a file the release doesn't have: its keys are elsewhere; the abbreviations it must)
            e.close()   # (its response closed: an error left open warns when it's dropped)
            if e.code != 404 or path == HA_FILES[0]:
                raise
    taken, gone = ha_rules(sources)
    out = []
    for where, keys in discovery_keys().items():
        for k in sorted(keys):
            if k in gone:
                out.append(f"{k} ({where}): deprecated or removed in Home Assistant {version}")
            elif k not in taken:
                out.append(f"{k} ({where}): not a discovery key Home Assistant {version} knows")
    return out


def main(argv: list[str]) -> int:
    """``python3 dev/ha_keys.py <version>``: what's wrong with the keys for that release, a line each; 0 when nothing."""
    if len(argv) != 2:
        print("python3 dev/ha_keys.py <Home Assistant's version>", file=sys.stderr)
        return 2
    try:
        wrong = ha_check(argv[1])
    except (OSError, ValueError) as e:
        print(f"Home Assistant {argv[1]}'s sources couldn't be read: {getattr(e, 'reason', e)}")
        return 1
    if wrong:
        print("the discovery messages need changing first: " + "; ".join(wrong))
    return 1 if wrong else 0


if __name__ == "__main__":
    import cli  # (dev/cli.py: what every dev script's command line does the same)
    _answered = cli.front(sys.argv[1:], __doc__, "python3 dev/ha_keys.py", commands=None)
    sys.exit(main(sys.argv) if _answered is None else _answered)
