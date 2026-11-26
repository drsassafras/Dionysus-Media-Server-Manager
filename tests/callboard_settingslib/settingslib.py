"""The settings format's reader (a shared library from MyStandards: settingslib; the format is MyStandards' SETTINGS.md).

A project says every setting it offers in one file (settings.toml): what each is, the field it gets, its default,
where its value is saved, and its info — what it does, when to use it and its configuration, which every setting must have. This reads
that file, holds it to the format, and answers for any setting by its id: its definition, its value, its default as it
stands, where it's saved. A value is read and written only here, by the setting's id, so none is saved that has no
definition (and so no info). The page's drawer (settings.js, beside this) draws a setting from what page() gives it.

    cat = load("settings.toml")                 the file, held to the format (SettingsError: everything wrong with it)
    cat.store("project", TomlStore(path))       where the values of the file a setting calls "project" are kept
    cat.answer("manifest_domain", fn)           what the app works out: a default_from, a choices_from, a set's
                                                members_from, a shown_when's feature — fn(member) answers it
    cat.add({...})                              settings that are themselves data (a formula's), added to the file's
    cat.value_of("server_url", "nas1")          the value that holds: saved, else the set's shared one, else the default
    cat.saved_value(id, member)                 only what's saved (None: nothing)
    cat.default_of(id, member)                  {"value", "says", "how": "fixed" | "worked_out" | "required" | "shared"}
    cat.save_value(id, value, member)           held to the setting (its kind, type, choices, limits) and written;
                                                None takes it away (back to its default)
    cat.page(members)                           everything the drawer needs, as JSON: each definition, and each
                                                setting's state (value, default, choices, shown) for each member
    cat.unknown("project")                      saved values at a key no setting has
    problems(data)                              what's wrong with a settings file's content: [(check, line)]
    orphans(cat, code)                          what's defined and never asked for, asked for and not defined…

A set's member is its id ("nas1"); a setting in two sets takes both, outer first (("unit", "push")); "*" is a
shared set's value for every member that has none of its own. A copy of the template, kept in step with it: never
edit it here.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import tomllib
from collections.abc import Callable
from typing import Any

FORMAT = 1
KINDS = ("switch", "choice", "number", "slider", "text", "lines", "list", "pairs", "secret")
# What a text is held to, by its type's name: the same in every app.
TYPES: dict[str, str] = {
    "host": r"[A-Za-z0-9\[][A-Za-z0-9._:\[\]-]*",
    "port": r"[0-9]{1,5}",
    "path": r"/[^\x00-\x1f]*",
    "url": r"https?://[^\s\x00-\x1f]+",
    "name": r"[A-Za-z0-9_][A-Za-z0-9_-]*",
    "glob": r"[^\x00-\x1f]+",
}
ID = re.compile(r"[a-z][a-z0-9_]*")
PAIR_NAME = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"   # (a name in a pairs setting: an environment variable's, a header's)
AT = "#"   # (a list set's member, in a path: its place in the list, from 1, as "#1")
SHARED = "*"   # (a shared set's member for "every one that has none of its own")
FIELDS: dict[str, type | tuple[type, ...]] = {
    "label": str, "summary": str, "info": dict, "kind": str, "group": str, "saved": dict, "under": str, "choices": list,
    "choices_from": str, "min": (int, float), "max": (int, float), "step": (int, float), "unit": str, "type": str,
    "pattern": str, "pattern_says": str, "default": object, "default_from": str, "default_says": str, "required": bool,
    "revert": bool, "revert_why": str, "shown_when": dict, "icon": str, "each": (str, list),
}
GROUP_FIELDS = {"title": str, "page": str, "info": str}
SET_FIELDS = {"title": str, "member": str, "saved": dict, "members_from": str, "shared": bool, "shared_saved": dict, "cascade": bool, "list": bool}
# A setting's info, in three parts: what the setting does, when one would use it, and its configuration (what to give it). Each has the
# fewest letters it may be said in (a few words explain nothing), and the fewest words of its own: words that aren't
# already in the setting's label and summary, nor (for when and config) in the parts before it — so no part is the label
# said again, or the part before it.
INFO_PARTS = ("does", "when", "config")
INFO_SAYS = {"does": "what it does", "when": "when to use it", "config": "its configuration: what to give it"}
INFO_LEAST = {"does": 80, "when": 80, "config": 50}     # (letters)
INFO_OWN = {"does": 6, "when": 6, "config": 4}          # (words of its own)
GIVEN = ("choice", "number", "slider", "text", "lines", "list", "pairs", "secret")   # (the kinds a value is given to: a switch is on or off)
_SMALL = frozenset("a an the of to in on is are it its it's that this and or for with as by be at from not no than each every which what when where how "
                   "they them their there here you your one can may will if so then into out up do does only also".split())


def _words(text: str) -> list[str]:
    """The words of ``text`` that say something (no small words), each as its stem-ish self: lower-case, a plural's s off."""
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in re.findall(r"[a-z0-9][a-z0-9'-]*", text.lower()) if w not in _SMALL]


def info_problems(s: dict, sub: bool = False) -> list[str]:
    """What's wrong with a setting's info ([] for nothing): each part it must have (does and when; config for a kind a
    value is given to — a sub-setting's when may be left out: its parent says when), each at least its letters, a
    sentence or more, and with words of its own."""
    info = s.get("info")
    if not isinstance(info, dict) or not info:
        return ["info: missing (every setting says what it does, when to use it, and its configuration: [settings.<id>.info] does, when, config)"]
    bad = [f"info.{k}: not a part of it ({', '.join(INFO_PARTS)})" for k in info if k not in INFO_PARTS]
    told = set(_words(str(s.get("label", "")) + " " + str(s.get("summary", ""))))
    for part in INFO_PARTS:
        text = info.get(part)
        must = part == "does" or (part == "when" and not sub) or (part == "config" and s.get("kind") in GIVEN)
        if text is None:
            if must:
                bad.append(f"info.{part}: missing ({INFO_SAYS[part]})")
            continue
        if not isinstance(text, str) or not text.strip():
            bad.append(f"info.{part}: {INFO_SAYS[part]}, in words")
            continue
        text = text.strip()
        if len(text) < INFO_LEAST[part]:
            bad.append(f"info.{part}: {len(text)} letters, and {INFO_SAYS[part]} takes {INFO_LEAST[part]} at least to say")
        if not _sentences(text):
            bad.append(f"info.{part}: one or more sentences (a capital first, a full stop last)")
        own = {w for w in _words(text) if w not in told}
        if len(own) < INFO_OWN[part]:
            before = "its label and summary" if part == "does" else "its label, its summary and the parts before it"
            bad.append(f"info.{part}: says {before} again ({len(own)} words of its own, and it takes {INFO_OWN[part]}): {INFO_SAYS[part]}, which they don't say")
        told |= set(_words(text))
    return bad


CHECKS = ("format", "info", "defaults", "subsettings", "fields", "unique")   # (problems()'s; orphans and runtime_copy are the project's)


class SettingsError(ValueError):
    """A settings file that isn't in the format, a setting that isn't there, or a value a setting can't take."""


def _each(s: dict) -> list[str]:
    e = s.get("each", [])
    return [e] if isinstance(e, str) else list(e)


def _sentences(text: str) -> bool:
    t = text.strip()
    return len(t) > 1 and (t[0].isupper() or not t[0].isalpha()) and t[-1] in ".!?)"


def problems(data: dict, extra: bool = False) -> list[tuple[str, str]]:
    """What's wrong with a settings file as read: (the check it fails, the line that says so) for each, [] where it's
    in the format. ``extra``: settings added to a file's (cat.add): no [format] of their own."""
    bad: list[tuple[str, str]] = []

    def say(check: str, line: str) -> None:
        bad.append((check, line))
    fmt = data.get("format")
    app = ""
    if not extra:
        if not isinstance(fmt, dict) or fmt.get("version") != FORMAT:
            say("format", f"[format] version = {FORMAT}: missing (the format this file is in)")
        else:
            app = fmt.get("app", "") if isinstance(fmt.get("app", ""), str) else ""
            for k in fmt:
                if k not in ("version", "app"):
                    say("format", f"[format] {k}: not a key of it (version, app)")
    else:
        app = data.get("format", {}).get("app", "") if isinstance(data.get("format"), dict) else ""
    for k in data:
        if k not in ("format", "groups", "sets", "settings"):
            say("format", f"[{k}]: not a table of a settings file (format, groups, sets, settings)")
    tables: dict[str, dict] = {k: (v if isinstance(v := data.get(k), dict) else {}) for k in ("groups", "sets", "settings")}
    groups, sets, settings = tables["groups"], tables["sets"], tables["settings"]
    for name, table, fields, must in (("groups", groups, GROUP_FIELDS, ("title",)), ("sets", sets, SET_FIELDS, ("title", "member", "saved", "members_from"))):
        for id_, g in table.items():
            where = f"[{name}.{id_}]"
            if not ID.fullmatch(id_):
                say("format", f"{where}: an id is lowercase letters, digits and _")
            if not isinstance(g, dict):
                say("format", f"{where}: a table")
                continue
            for k, v in g.items():
                if k not in fields or not isinstance(v, fields[k]):
                    say("format", f"{where} {k}: not a key of it ({', '.join(fields)}), or not the kind of value it takes")
            for k in must:
                if not g.get(k):
                    say("format", f"{where} {k}: missing")
            if name == "sets" and isinstance(g.get("saved"), dict):
                _saved(g["saved"], where, say, in_set=False, of_set=True)
            if name == "sets" and g.get("shared") is True and g.get("cascade") is True:
                say("format", f"{where}: a set is shared (one value for every member) or cascade (each member takes the one before it's), not both")
            if name == "sets" and g.get("list") is True:   # (its members are the items of a list, [[key]]: each by its place in it)
                if g.get("shared") is True:
                    say("format", f"{where}: a list's members are its items, and there's no value for every one of them (shared)")
                if not (isinstance(g.get("saved"), dict) and g["saved"].get("key")):
                    say("format", f"{where} saved.key: where its list is kept")
            if name == "sets" and "shared_saved" in g:   # (where every member's shared value is kept, where that isn't beside the members)
                if g.get("shared") is not True:
                    say("format", f"{where} shared_saved: only a set that's shared has a value for every member (shared = true)")
                if isinstance(g["shared_saved"], dict):
                    _saved(g["shared_saved"], where + " shared_saved:", say, in_set=False, of_set=True)
    seen: dict[tuple, list[str]] = {}
    for id_, s in settings.items():
        where = f"[settings.{id_}]"
        if not ID.fullmatch(id_):
            say("format", f"{where}: an id is lowercase letters, digits and _")
        if not isinstance(s, dict):
            say("format", f"{where}: a table")
            continue
        for k, v in s.items():
            own = bool(app) and k.startswith(app + "_")
            if k == "info":
                continue   # (its parts are held under "info", below)
            if not own and (k not in FIELDS or (FIELDS[k] is not object and (not isinstance(v, FIELDS[k]) or (isinstance(v, bool) and FIELDS[k] is not bool)))):
                say("format", f"{where} {k}: not a field of a setting" + (f" (one of {app}'s own is named {app}_<field>)" if app else "") + ", or not the kind of value it takes")
        parent = settings.get(s.get("under", "")) if s.get("under") else None
        for k in ("label", "kind", "saved"):
            if not s.get(k):
                say("format", f"{where} {k}: missing")
        if s.get("kind") and s["kind"] not in KINDS:
            say("format", f"{where} kind = {s['kind']!r}: one of {', '.join(KINDS)}")
        group = s.get("group") or (parent.get("group", "") if isinstance(parent, dict) else "")
        if not group:
            say("format", f"{where} group: missing")
        elif group not in groups:
            say("format", f"{where} group = {group!r}: no such group")
        for e in _each(s):
            if e not in sets:
                say("format", f"{where} each = {e!r}: no such set")
        if len(_each(s)) > 2:
            say("format", f"{where} each: two sets at the most, one inside the other")
        if isinstance(s.get("saved"), dict):
            _saved(s["saved"], where, say, in_set=bool(_each(s)))
            if _each(s) and not s["saved"].get("file") and not any(isinstance(sets.get(e), dict) and isinstance(sets[e].get("saved"), dict) and sets[e]["saved"].get("file") for e in _each(s)):
                say("format", f"{where} saved.file: neither it nor its set says which of the app's files")
        when = s.get("shown_when")
        if isinstance(when, dict) and not ((set(when) == {"setting", "is"} and when["setting"] in settings) or (set(when) == {"feature"} and isinstance(when["feature"], str) and when["feature"])):
            say("format", f"{where} shown_when: {{ setting = \"<a setting's id>\", is = <its value> }}, or {{ feature = \"<what the app answers>\" }}")
        # info: required, in its parts, each long enough and with words of its own
        for line in info_problems(s, sub="under" in s):
            say("info", f"{where} {line}")
        if isinstance(s.get("summary"), str) and re.search(r"\bdefaults?\b", s["summary"], re.I):
            say("info", f"{where} summary: says the default (the revert button does that)")
        # the default: said exactly one way
        ways = [k for k in ("default", "default_from") if k in s] + (["required"] if s.get("required") is True else [])
        if len(ways) != 1:
            say("defaults", f"{where}: says its default {'no way' if not ways else 'more than one way (' + ', '.join(ways) + ')'}: "
                            "default (a fixed value), default_from with default_says (worked out), or required = true (there's none)")
        if ("default_from" in s) != ("default_says" in s) or ("default_says" in s and not _sentences(str(s["default_says"]) + ".")):
            say("defaults", f"{where}: default_from (what works it out) and default_says (how, in words) go together")
        if s.get("revert") is False and not str(s.get("revert_why", "")).strip():
            say("defaults", f"{where} revert = false: says why (revert_why)")
        # a sub-setting
        if "under" in s:
            if not isinstance(parent, dict):
                say("subsettings", f"{where} under = {s['under']!r}: no such setting")
            else:
                if "under" in parent:
                    say("subsettings", f"{where} under = {s['under']!r}: that's under {parent['under']!r} itself (one level only)")
                if s.get("group") and s["group"] != parent.get("group"):
                    say("subsettings", f"{where} group: its parent's ({parent.get('group')!r}), or left out")
                if _each(s) != _each(parent):
                    say("subsettings", f"{where} each: its parent's set ({_each(parent)})")
                if "shown_when" in s:
                    say("subsettings", f"{where} shown_when: a sub-setting is shown when its parent is")
        # the field its kind gets
        kind = s.get("kind")
        if kind == "choice" and ("choices" in s) == ("choices_from" in s):
            say("fields", f"{where}: a choice has choices or choices_from (one of them)")
        if "choices" in s and not (s["choices"] and all(isinstance(c, list) and len(c) == 2 and isinstance(c[1], str) for c in s["choices"])):
            say("fields", f"{where} choices: [[value, \"Label\"], …]")
        if kind in ("number", "slider") and not ("min" in s and "max" in s):
            say("fields", f"{where}: a {kind} has its min and max")
        if kind in ("number", "slider") and "min" in s and "max" in s and s["min"] > s["max"]:
            say("fields", f"{where}: its min is over its max")
        if "type" in s and (kind not in ("text", "list", "lines", "pairs") or s["type"] not in TYPES):
            say("fields", f"{where} type = {s.get('type')!r}: for a text, a list, lines or pairs, one of {', '.join(TYPES)}")
        if "pattern" in s:
            if not str(s.get("pattern_says", "")).strip():
                say("fields", f"{where} pattern: says what's wanted when it doesn't match (pattern_says)")
            try:
                re.compile(s["pattern"])
            except re.error as e:
                say("fields", f"{where} pattern: {e}")
        if "default" in s and kind in KINDS and kind != "secret":
            try:
                _held(id_, s, s["default"], dict(s.get("choices", [])) if isinstance(s.get("choices"), list) and all(isinstance(c, list) and len(c) == 2 for c in s["choices"]) and not _unhashable(s["choices"]) else None)
            except SettingsError as e:
                say("fields", f"{where} default: {e}")
        if kind == "secret" and "default" in s:
            say("fields", f"{where}: a secret has no default in the file")
        # one id, one place saved
        if isinstance(s.get("saved"), dict) and isinstance(s["saved"].get("key"), str):
            at = (tuple(_each(s)), s["saved"].get("file", ""), s["saved"]["key"])
            for other in seen.get(at, []):   # (two that are never shown together — one setting's value picks between them — may be kept in one place)
                a, b = s.get("shown_when") or {}, settings[other].get("shown_when") or {}
                if not ("setting" in a and a.get("setting") == b.get("setting") and a.get("is") != b.get("is")):
                    say("unique", f"{where} saved: {other} is saved there too ({s['saved']['key']})")
            seen.setdefault(at, []).append(id_)
    for k in ("groups", "sets"):
        if k in data and not isinstance(data[k], dict):
            say("format", f"[{k}]: a table of them")
    return bad


def _unhashable(choices: list) -> bool:
    try:
        {c[0] for c in choices}
    except TypeError:
        return True
    return False


def _saved(saved: dict, where: str, say: Callable, in_set: bool, of_set: bool = False) -> None:
    for k in saved:
        if k not in ("file", "key"):
            say("format", f"{where} saved.{k}: not a key of it (file, key)")
    key = saved.get("key")
    if not isinstance(key, str) or (not key and not of_set) or (key and not all(re.fullmatch(r"[A-Za-z0-9_-]+", p) for p in key.split("."))):
        say("format", f"{where} saved.key: the dotted path its value is kept at")
    if (in_set or of_set) and "file" in saved and not (isinstance(saved["file"], str) and saved["file"]):
        say("format", f"{where} saved.file: which of the app's files (project, computer, or a name it gives one)")
    if not in_set and not of_set and not (isinstance(saved.get("file"), str) and saved["file"]):
        say("format", f"{where} saved.file: which of the app's files (project, computer, or a name it gives one)")


def _held(id_: str, s: dict, value: Any, choices: dict | None) -> Any:
    """``value`` as the setting keeps it, held to its kind (SettingsError where it can't be that)."""
    kind = s.get("kind")

    def no(why: str) -> SettingsError:
        return SettingsError(f"{s.get('label') or id_}: {why}")

    def text(v: Any) -> str:
        if not isinstance(v, str) or re.search(r"[\x00-\x1f\x7f]", v.replace("\n", "") if kind == "lines" else v):
            raise no("text, with nothing in it a line can't hold")
        t = s.get("type")
        if t == "port":
            if not re.fullmatch(TYPES["port"], v) or not 0 < int(v) < 65536:
                raise no("a port: a number from 1 to 65535")
        elif t and not re.fullmatch(TYPES[t], v):
            raise no(f"doesn't look like a {t}")
        if "pattern" in s and not re.fullmatch(s["pattern"], v):
            raise no(str(s.get("pattern_says") or "isn't what's wanted here"))
        return v
    if kind == "switch":
        if not isinstance(value, bool):
            raise no("on or off (true or false)")
        return value
    if kind == "choice":
        if choices is not None and value not in choices:
            raise no(f"one of {', '.join(str(c) for c in choices)}")
        return value
    if kind in ("number", "slider"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise no("a number")
        if ("min" in s and value < s["min"]) or ("max" in s and value > s["max"]):
            raise no(f"from {s.get('min')} to {s.get('max')}")
        return value
    if kind == "pairs":   # (names, each with its text: a table of its own)
        if not isinstance(value, dict):
            raise no("a table of names, each with its text")
        for k in value:
            if not isinstance(k, str) or not re.fullmatch(PAIR_NAME, k):
                raise no(f"{k!r}: a name is letters, digits, _, - and .")
        return {k: text(v) for k, v in value.items()}
    if kind in ("list", "lines"):
        if isinstance(value, str) and kind == "list":
            value = [value]   # (one item, said alone)
        if not isinstance(value, list):
            raise no("a list")
        return [text(v) for v in value]
    if kind == "text" and s.get("type") == "port" and isinstance(value, int) and not isinstance(value, bool):
        return int(text(str(value)))
    return text(value)


# ---------------------------------------------------------------- where values are kept
class MemoryStore:
    """Values kept in a dict (a draft, a test)."""

    def __init__(self, data: dict | None = None, name: str = "memory"):
        """Values kept in ``data`` (a new dict where it's None), named ``name`` (how a save names where it went)."""
        self.data, self.name = data if data is not None else {}, name

    def read(self) -> dict:
        """The values as they're kept."""
        return self.data

    def write(self, parts: list[str], value: Any) -> None:
        """``value`` kept at ``parts`` (None takes it away)."""
        put(self.data, parts, value)


class JsonStore:
    """Values kept in a JSON file (made when the first is saved)."""

    def __init__(self, path: str, name: str = ""):
        """Values kept in the JSON file at ``path``, named ``name`` (its file's name where none is given)."""
        self.path, self.name = path, name or os.path.basename(path)

    def read(self) -> dict:
        """The file's values ({} where there's no file yet, or it isn't a table)."""
        try:
            with open(self.path, encoding="utf-8") as fh:
                got = json.load(fh)
        except FileNotFoundError:
            return {}
        return got if isinstance(got, dict) else {}

    def write(self, parts: list[str], value: Any) -> None:
        """``value`` kept at ``parts`` (None takes it away), the file written whole, in one step."""
        data = self.read()
        put(data, parts, value)
        tmp = self.path + ".new"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)


class TomlStore:
    """Values read from a TOML file. Written by ``writer(parts, value)`` where the app gives one (its own way of
    writing its file, comments kept); with none it's read-only, and a save says so."""

    def __init__(self, path: str, name: str = "", writer: Callable[[list[str], Any], None] | None = None):
        """Values read from the TOML file at ``path``, named ``name``, written by ``writer`` where the app gives one."""
        self.path, self.name, self.writer = path, name or os.path.basename(path), writer

    def read(self) -> dict:
        """The file's values ({} where there's no file yet)."""
        try:
            with open(self.path, "rb") as fh:
                return tomllib.load(fh)
        except FileNotFoundError:
            return {}

    def write(self, parts: list[str], value: Any) -> None:
        """``value`` written at ``parts`` by the app's writer (SettingsError where it gave none: read-only)."""
        if self.writer is None:
            raise SettingsError(f"{self.name} is only read here: the app gives no way to write it")
        self.writer(parts, value)


def _item(at: Any, p: str) -> Any:
    """What's at ``p`` in a table, or in a list where ``p`` is a place in it ("#1": its first item); None for nothing."""
    if p.startswith(AT) and p[1:].isdigit():
        n = int(p[1:])
        return at[n - 1] if isinstance(at, list) and 0 < n <= len(at) else None
    return at.get(p) if isinstance(at, dict) else None


def put(data: dict, parts: list[str], value: Any) -> None:
    """``value`` put at ``parts`` in ``data``; None takes it away, and each table left empty by that with it (but a
    list's item, whose place says which member it is: it's left, empty)."""
    at, trail = data, []
    for i, p in enumerate(parts[:-1]):
        if not isinstance(_item(at, p), (dict, list)):
            if value is None:
                return
            new: Any = [] if parts[i + 1].startswith(AT) else {}
            if p.startswith(AT):
                at.extend({} for _ in range(int(p[1:]) - len(at)))
                at[int(p[1:]) - 1] = new
            else:
                at[p] = new
        if not p.startswith(AT):
            trail.append((at, p))
        else:
            trail = []   # (a list's item stays, empty or not: nothing above it is taken away)
        at = _item(at, p)
    if value is None:
        at.pop(parts[-1], None)
        for up, p in reversed(trail):
            if up[p]:
                break
            del up[p]
    else:
        at[parts[-1]] = value


def _at(data: Any, parts: list[str]) -> Any:
    for p in parts:
        data = _item(data, p)
        if data is None:
            return None
    return data


# ---------------------------------------------------------------- the settings, loaded
class Catalog:
    """A project's settings as its file says them, and the answer for each by its id."""

    def __init__(self, data: dict, where: str = "settings.toml"):
        """The catalog of the settings file read as ``data`` (from ``where``), held to the format first."""
        bad = problems(data)
        if bad:
            raise SettingsError(f"{where} isn't in the settings format:\n" + "\n".join("  " + line for _, line in bad))
        self.where = where
        self.app: str = data["format"].get("app", "")
        self.groups: dict[str, dict] = dict(data.get("groups", {}))
        self.sets: dict[str, dict] = dict(data.get("sets", {}))
        self.settings: dict[str, dict] = {k: dict(v) for k, v in data.get("settings", {}).items()}
        self.added: set[str] = set()   # (the ids added as data: no code names them, and that's not an orphan)
        self._stores: dict[str, Any] = {}
        self._answers: dict[str, Callable] = {}
        self._fill()

    def _fill(self) -> None:
        for s in self.settings.values():
            if "under" in s:
                s.setdefault("group", self.settings[s["under"]].get("group", ""))

    # -- what the app gives it
    def store(self, file: str, store: Any) -> Any:
        """Where the values of the file settings call ``file`` are kept: something with read() -> dict,
        write(parts, value) and a name (how the bubble's "Saved as" names it)."""
        self._stores[file] = store
        return store

    def answer(self, name: str, fn: Callable) -> None:
        """What the app works out by that name (a default_from, a choices_from, a members_from, a feature):
        ``fn(member)`` answers it — the default's value (None: not known yet), the choices [[value, label]…], the
        members [{"id", "name"}…], or whether the feature is there."""
        self._answers[name] = fn

    def add(self, settings: dict, groups: dict | None = None, sets: dict | None = None) -> None:
        """Settings that are themselves data (a formula's), added to the file's and held to the format with them."""
        data = {"format": {"version": FORMAT, "app": self.app}, "groups": {**self.groups, **(groups or {})}, "sets": {**self.sets, **(sets or {})},
                "settings": {**self.settings, **settings}}
        bad = problems(data)
        if bad:
            raise SettingsError("these settings aren't in the settings format:\n" + "\n".join("  " + line for _, line in bad))
        self.groups, self.sets = data["groups"], data["sets"]
        self.settings = {k: dict(v) for k, v in data["settings"].items()}
        self.added |= set(settings)
        self._fill()

    # -- one setting
    def setting_of(self, id_: str) -> dict:
        """The setting's definition (SettingsError where there's none by that id)."""
        try:
            return self.settings[id_]
        except KeyError:
            raise SettingsError(f"no setting {id_!r} in {self.where}") from None

    def subs_of(self, id_: str) -> list[str]:
        """Its sub-settings' ids, in the file's order."""
        return [k for k, s in self.settings.items() if s.get("under") == id_]

    def _members(self, s: dict, member: Any) -> list[str]:
        each = _each(s)
        got = [] if member is None else [member] if isinstance(member, str) else list(member)
        if len(got) != len(each) and not (not got and each and all(self.sets[e].get("shared") for e in each)):
            raise SettingsError(f"{s.get('label')}: it's one for each of {' and '.join(each) or 'nothing'}: asked for with {got or 'no member'}")
        return got or [SHARED] * len(each)

    def where_saved(self, id_: str, member: Any = None) -> tuple[str, list[str]]:
        """The file (by the name settings call it) and the path in it the setting's value is at."""
        s = self.setting_of(id_)
        return self._place(s, self._members(s, member))

    def _place(self, s: dict, got: list) -> tuple[str, list]:
        """The file and path of a setting's value for those members (one a set: "*" the value every member shares,
        kept where the set's shared_saved says, else as a member named "*" beside the others). The setting's own file
        where it says one (a set whose members' values are in more than one file), else its set's."""
        file, parts = s["saved"].get("file", ""), []
        for e, m in zip(_each(s), got, strict=True):
            set_ = self.sets[e]
            if m == SHARED and "shared_saved" in set_:
                file = file or set_["shared_saved"].get("file", "")
                parts += [p for p in set_["shared_saved"].get("key", "").split(".") if p]
            else:
                at = m if m is None or not set_.get("list") or str(m).startswith("<") else AT + str(m)   # (a list's item, by its place)
                parts += [p for p in set_["saved"].get("key", "").split(".") if p] + [at]
            file = file or set_["saved"].get("file", "")
        return file, parts + s["saved"]["key"].split(".")

    def _store(self, file: str) -> Any:
        try:
            return self._stores[file]
        except KeyError:
            raise SettingsError(f"the app hasn't said where the values of {file!r} are kept (cat.store)") from None

    def saved_value(self, id_: str, member: Any = None) -> Any:
        """What's saved for it (None: nothing)."""
        file, parts = self.where_saved(id_, member)
        return _at(self._store(file).read(), parts)

    def _ask(self, name: str, member: Any, what: str) -> Any:
        if name not in self._answers:
            raise SettingsError(f"the app doesn't answer {name!r} ({what}: cat.answer)")
        return self._answers[name](member)

    def default_of(self, id_: str, member: Any = None) -> dict:
        """Its default as it stands: {"value" (None: none, or not known yet), "says" (how it's worked out, "" for a
        fixed one), "how": "fixed", "worked_out", "required", or "shared" (a set's value for every member)}."""
        s = self.setting_of(id_)
        got = self._members(s, member)
        if got and any(m != SHARED for m in got) and all(self.sets[e].get("shared") for e in _each(s)):
            shared = self.saved_value(id_, [SHARED] * len(got))
            if shared is not None:
                return {"value": shared, "says": f"every {self.sets[_each(s)[-1]]['member']}'s", "how": "shared"}
        before = self._before(s, got)
        if before is not None:   # (a set that cascades: what the member before it comes to, its own or what that one takes in turn)
            return {"value": self.value_of(id_, before[0]), "says": f"{before[1]}'s", "how": "cascade"}
        if "default" in s:
            return {"value": s["default"], "says": "", "how": "fixed"}
        if "default_from" in s:
            return {"value": self._ask(s["default_from"], member, f"{id_}'s default_from"), "says": s["default_says"], "how": "worked_out"}
        return {"value": None, "says": "", "how": "required"}

    def _before(self, s: dict, got: list) -> tuple[list, str] | None:
        """In a set that cascades (its members in the order the app answers them, each taking the value of the one
        before it unless it has its own): the members a setting's value is taken from, and that member's name — None
        for the first member, and for a setting in no such set. The innermost set that cascades is the one followed."""
        each = _each(s)
        for i in range(len(each) - 1, -1, -1):
            if not self.sets[each[i]].get("cascade") or i >= len(got):
                continue
            listed = self.members_of(each[i], got[i - 1] if i else None)
            at = [m["id"] for m in listed].index(got[i]) if got[i] in [m["id"] for m in listed] else 0
            if at > 0:
                return [*got[:i], listed[at - 1]["id"], *got[i + 1:]], listed[at - 1]["name"]
        return None

    def value_of(self, id_: str, member: Any = None) -> Any:
        """The value that holds: what's saved, else its default as it stands."""
        v = self.saved_value(id_, member)
        return self.default_of(id_, member)["value"] if v is None else v

    def choices_of(self, id_: str, member: Any = None) -> list | None:
        """A choice's [[value, label]…] (the app's answer for a choices_from); None for a setting that isn't one."""
        s = self.setting_of(id_)
        if "choices" in s:
            return s["choices"]
        return [list(c) for c in self._ask(s["choices_from"], member, f"{id_}'s choices_from")] if "choices_from" in s else None

    def held(self, id_: str, value: Any, member: Any = None) -> Any:
        """``value`` as the setting keeps it (SettingsError where it can't take it)."""
        s = self.setting_of(id_)
        choices = self.choices_of(id_, member) if s["kind"] == "choice" else None
        return _held(id_, s, value, {c[0]: c[1] for c in choices} if choices is not None else None)

    def save_value(self, id_: str, value: Any, member: Any = None) -> None:
        """The value saved, held to the setting first; None (or "" or []) takes it away: back to its default."""
        file, parts = self.where_saved(id_, member)
        if value is None or value == "" or value == []:
            value = None
        else:
            value = self.held(id_, value, member)
            took = self.default_of(id_, member)
            if took["how"] == "cascade" and took["value"] == value:
                value = None   # (the same as it would take from the member before it: nothing of its own to keep)
        self._store(file).write(parts, value)

    def members_of(self, set_: str, member: Any = None) -> list[dict]:
        """The set's members as the app answers them now: [{"id", "name"}…] (a set inside another: of ``member``)."""
        if set_ not in self.sets:
            raise SettingsError(f"no set {set_!r} in {self.where}")
        return [{"id": str(m["id"]), "name": str(m.get("name") or m["id"])} if isinstance(m, dict) else {"id": str(m), "name": str(m)}
                for m in self._ask(self.sets[set_]["members_from"], member, f"the {set_} set's members_from")]

    def shown(self, id_: str, member: Any = None) -> bool:
        """Whether it's on the page at all (its shown_when; a sub-setting's parent's)."""
        s = self.setting_of(id_)
        if "under" in s:
            return self.shown(s["under"], member)
        when = s.get("shown_when")
        if not when:
            return True
        if "feature" in when:
            return bool(self._ask(when["feature"], member, f"{id_}'s shown_when"))
        other = self.setting_of(when["setting"])
        return self.value_of(when["setting"], member if _each(other) else None) == when["is"]

    def saved_says(self, id_: str, shared: bool = False) -> dict:
        """How the bubble names where it's saved: {"key": "servers.<server>.url", "file": the store's name}
        (``shared``: the value every member of its set shares)."""
        s = self.setting_of(id_)
        file, parts = self._place(s, [SHARED if shared and self.sets[e].get("shared") else f"<{self.sets[e]['member']}>" for e in _each(s)])
        store = self._stores.get(file)
        return {"key": ".".join(parts), "file": getattr(store, "name", "") or file}

    # -- for the page
    def state(self, id_: str, member: Any = None) -> dict:
        """One setting as it stands, for the drawer: its value (a secret's never: only whether one is set), its
        default, its choices where the app answers them, and whether it's shown."""
        s = self.setting_of(id_)
        v = self.saved_value(id_, member)
        out: dict[str, Any] = {"default": self.default_of(id_, member), "shown": self.shown(id_, member)}
        if s["kind"] == "secret":
            out["set"] = v is not None
        else:
            out["value"] = v
        if "choices_from" in s:
            out["choices"] = self.choices_of(id_, member)
        return out

    def page(self, members: dict[str, list] | None = None) -> dict:
        """Everything the drawer needs, as JSON: the groups and sets, each setting's definition (its sub-settings and
        how its saved place is said with it), and each setting's state — one for a setting in no set, and for one in a
        set one for each member the app answers (``members``: {set: [ids]} to say them here instead; a shared set's
        "*" with them). A state's key is the setting's id, then each member's after a "|"."""
        defs = {}
        for id_, s in self.settings.items():
            defs[id_] = {**{k: v for k, v in s.items() if k != "each"}, "id": id_, "each": _each(s), "subs": self.subs_of(id_), "saved_says": self.saved_says(id_)}
            if any(self.sets[e].get("shared") for e in _each(s)):
                defs[id_]["saved_says_shared"] = self.saved_says(id_, shared=True)
        states = {}
        for id_, s in self.settings.items():
            for m in self._every(_each(s), members or {}):
                states["|".join([id_, *m])] = self.state(id_, m or None)
        return {"format": FORMAT, "groups": self.groups, "sets": {k: {"title": v["title"], "member": v["member"], "shared": bool(v.get("shared")), "cascade": bool(v.get("cascade")), "list": bool(v.get("list"))} for k, v in self.sets.items()},
                "settings": defs, "states": states}

    def _every(self, each: list[str], members: dict[str, list], inside: list[str] | None = None) -> list[list[str]]:
        if not each:
            return [[]]
        set_, out = each[0], []
        ids = [str(m) for m in members[set_]] if set_ in members else [m["id"] for m in self.members_of(set_, (inside or [None])[-1])]
        for m in ([SHARED] if self.sets[set_].get("shared") else []) + ids:
            out += [[m, *rest] for rest in self._every(each[1:], members, [*(inside or []), m])]
        return out

    # -- what's saved that nothing defines
    def unknown(self, file: str, tables: list[str] | None = None) -> list[str]:
        """The values saved in ``file`` at a key no setting has, as dotted paths (``tables``: only under those top
        keys — the part of a file that's been given over to the settings)."""
        patterns = []
        for s in self.settings.values():
            each = _each(s)
            # (a member's place, any member's; and the shared value's, where its set keeps that elsewhere)
            for got in itertools.product(*[[None, SHARED] if "shared_saved" in self.sets[e] else [None] for e in each]):
                f, parts = self._place(s, list(got))
                if f == file:
                    patterns.append(parts)
        out: list[str] = []

        def walk(node: Any, path: list[str]) -> None:
            fits = [p for p in patterns if len(p) >= len(path) and all(a is None or a == b for a, b in zip(p, path, strict=False))]
            if any(len(p) == len(path) for p in fits):
                return
            if fits and isinstance(node, dict):
                for k, v in node.items():
                    walk(v, [*path, k])
            elif fits and isinstance(node, list):   # (a list set's items: each in a member's place)
                for i, v in enumerate(node, 1):
                    walk(v, [*path, f"{AT}{i}"])
            else:
                out.append(".".join(path))
        for k, v in self._store(file).read().items():
            if tables is None or k in tables:
                walk(v, [k])
        return out


def load(path: str) -> Catalog:
    """The settings file at ``path``, held to the format (SettingsError: it can't be read, or everything wrong with it)."""
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SettingsError(f"{path}: {e}") from None
    return Catalog(data, os.path.basename(path))


# ---------------------------------------------------------------- nothing orphaned
ASKS = r"(?:value_of|saved_value|save_value|default_of|setting_of|choices_of|where_saved|draw_setting|drawSetting|DrawSetting|ValueOf|SavedValue|SaveValue|DefaultOf|SettingOf|ChoicesOf|WhereSaved)"
GROUP_ASKS = r"(?:draw_group|drawGroup|DrawGroup|group_of|GroupOf)"
ANSWERS = r"\.(?:answer|Answer)"
_Q = r"""\(\s*["'`]([A-Za-z0-9_]+)["'`]"""


def orphans(cat: Catalog, code: dict[str, str]) -> list[str]:
    """What's orphaned, each a line ([] for nothing): a setting no code asks for (its id is nowhere in ``code``, nor
    its group's asked for whole, nor is it one added as data), an id the code asks for that isn't defined, a group or
    a set with no setting in it, a name the settings ask the app for that it never answers, and one it answers that no
    setting asks for. ``code``: the app's code, {path: text}."""
    everything = "\n".join(code.values())
    quoted = set(re.findall(r"""["'`]([a-z][a-z0-9_]*)["'`]""", everything))
    whole = set(re.findall(GROUP_ASKS + _Q, everything))
    out = []
    for id_, s in cat.settings.items():
        top = s.get("under", id_)
        if id_ in cat.added or id_ in quoted or top in quoted or cat.settings[top].get("group") in whole:
            continue
        out.append(f"[settings.{id_}]: no code asks for it (it's defined, and never drawn or read)")
    for found, where in sorted({(m.group(1), path) for path, text in code.items() for m in re.finditer(r"\b" + ASKS + _Q, text)}):
        if found not in cat.settings:
            out.append(f"{where}: asks for the setting {found!r}, which isn't defined")
    for found in sorted(whole - set(cat.groups)):
        out.append(f"the code asks for the group {found!r}, which isn't defined")
    used_groups = {s.get("group") for s in cat.settings.values()}
    used_sets = {e for s in cat.settings.values() for e in _each(s)}
    file_sets = {e for id_, s in cat.settings.items() if id_ not in cat.added for e in _each(s)}
    out += [f"[groups.{g}]: no setting is in it" for g in cat.groups if g not in used_groups]
    out += [f"[sets.{g}]: no setting is one for each of it" for g in cat.sets if g not in used_sets]
    asked = {}
    for id_, s in cat.settings.items():
        if id_ in cat.added:
            continue   # (one added as data: what it asks for is answered as it's added, by no name the code spells)
        for k in ("default_from", "choices_from"):
            if k in s:
                asked[s[k]] = f"[settings.{id_}] {k}"
        if "feature" in (s.get("shown_when") or {}):
            asked[s["shown_when"]["feature"]] = f"[settings.{id_}] shown_when"
    for g in file_sets:
        asked[cat.sets[g]["members_from"]] = f"[sets.{g}] members_from"
    answered = set(re.findall(ANSWERS + _Q, everything))
    out += [f"{where} = {name!r}: the app never answers it (no answer(\"{name}\", …) in its code)" for name, where in sorted(asked.items()) if name not in answered]
    out += [f"the app answers {name!r}, which no setting asks for" for name in sorted(answered - set(asked))]
    return out
