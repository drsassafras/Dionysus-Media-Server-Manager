"""Settings (a shared library from MyStandards: settingslib): the project's settings file held to the format, as part
of the project's own tests, each check a test of its own (test_<check>), and the library's own tests with them (the
copy works here as it does everywhere). ../settingslib.json says what's this project's:

    {"file": "app/settings.toml",                         its settings file
     "code": ["app/*.py", "app/web/*.js"],                the app's code (globs): where a setting is asked for
     "runtime": {"app/settingslib": ["settingslib.py", "settings.js"]},   the app's own copy of the library: what it runs
     "saved": {"project": {"path": "app.toml", "tables": ["servers"]}}}   committed files of saved values, and the part
                                                                           of each that's the settings' (all of it: no tables)

A check is left out only where the project's callboard.toml says why ([templates.settingslib.checks] <check> =
"<reason>"; all of them, a project with no settings: [templates.settingslib] opt_out = "<reason>"). A copy of the
template, kept in step with it: never edit it here."""

import copy
import glob
import importlib.util
import json
import os
import tempfile
import tomllib
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(os.path.dirname(HERE), "settingslib.json")
ROOT = os.path.dirname(os.path.dirname(HERE))
NAME = "settingslib"

_spec = importlib.util.spec_from_file_location("callboard_settingslib_engine", os.path.join(HERE, "settingslib.py"))
assert _spec is not None and _spec.loader is not None, "settingslib.py, beside this test, can't be loaded"
lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lib)

with open(os.path.join(HERE, "template.toml"), "rb") as _fh:
    CHECKS = tomllib.load(_fh)["checks"]   # (each check and what it checks: the template's own list)


def _opt_outs() -> tuple[str | None, dict]:
    """What the project's callboard.toml leaves out, and why: (the whole template's reason, {check: reason})."""
    try:
        with open(os.path.join(ROOT, "callboard.toml"), "rb") as fh:
            got = (tomllib.load(fh).get("templates") or {}).get(NAME) or {}
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return None, {}
    reason = got.get("opt_out") if isinstance(got, dict) else None
    said = got.get("checks") if isinstance(got, dict) else None
    return reason, said if isinstance(said, dict) else {}


def _plan(test: unittest.TestCase) -> dict:
    """../settingslib.json as read (the test failed where it can't be, or names no settings file)."""
    where = os.path.relpath(SETTINGS, ROOT)
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            plan = json.load(fh)
    except (OSError, ValueError) as e:
        test.fail(f"{where}: {e}: name the project's settings file there, or opt out in callboard.toml ([templates.{NAME}] opt_out = \"<why>\")")
    if not isinstance(plan, dict) or not isinstance(plan.get("file"), str) or not plan["file"]:
        test.fail(f"{where} names no settings file (\"file\"): name it, or opt out in callboard.toml ([templates.{NAME}] opt_out = \"<why>\": a project with no settings)")
    return plan


def _data(test: unittest.TestCase, plan: dict) -> dict:
    try:
        with open(os.path.join(ROOT, plan["file"]), "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        test.fail(f"{plan['file']}: {e}")


def project_orphans(root: str, plan: dict) -> list[str]:
    """What's orphaned in the project at ``root`` (lib.orphans on its code), and each saved value in its committed
    files at a key no setting has."""
    cat = lib.load(os.path.join(root, plan["file"]))
    code = {}
    for pattern in plan.get("code") or []:
        for path in sorted(glob.glob(os.path.join(root, pattern), recursive=True)):
            if os.path.isfile(path) and os.path.abspath(path) != os.path.abspath(os.path.join(root, plan["file"])):
                with open(path, encoding="utf-8", errors="replace") as fh:
                    code[os.path.relpath(path, root)] = fh.read()
    if not code:
        return [f"{os.path.relpath(SETTINGS, ROOT)}: \"code\" names none of the app's code (globs from the project's folder): nothing asks for a setting"]
    out = lib.orphans(cat, code)
    for file, spec in (plan.get("saved") or {}).items():
        path = os.path.join(root, spec["path"])
        cat.store(file, lib.JsonStore(path) if path.endswith(".json") else lib.TomlStore(path))
        out += [f"{spec['path']}: {key} is saved there, and no setting has that key" for key in cat.unknown(file, spec.get("tables"))]
    return out


def runtime_copy(root: str, plan: dict) -> list[str]:
    """Each file of the app's own copy that isn't this copy's, file for file ([] where they're one)."""
    said = plan.get("runtime")
    if not isinstance(said, dict) or not said:
        return [f"{os.path.relpath(SETTINGS, ROOT)}: \"runtime\" doesn't say where the app's own copy of the library is ({{\"<folder>\": [\"settingslib.py\", \"settings.js\"]}})"]
    out = []
    for folder, files in said.items():
        for name in files:
            mine, theirs = os.path.join(HERE, name), os.path.join(root, folder, name)
            if not os.path.isfile(mine):
                out.append(f"{folder}/{name}: not a file of the library")
            elif not os.path.isfile(theirs):
                out.append(f"{folder}/{name}: not there (copy it from {os.path.relpath(HERE, root)}/{name})")
            else:
                with open(mine, "rb") as a, open(theirs, "rb") as b:
                    if a.read() != b.read():
                        out.append(f"{folder}/{name}: not the library's copy (copy it again from {os.path.relpath(HERE, root)}/{name}: never edit it there)")
    return out


def _check(name: str):
    def test(self):
        reason, said = _opt_outs()
        if isinstance(reason, str) and reason.strip():
            self.skipTest(f"opted out in callboard.toml: {reason.strip()}")
        if name in said:
            if not (isinstance(said[name], str) and said[name].strip()):
                self.fail(f"callboard.toml [templates.{NAME}.checks] {name}: left out with no reason: say why")
            self.skipTest(f"opted out in callboard.toml: {said[name].strip()}")
        plan = _plan(self)
        if name == "runtime_copy":
            found = runtime_copy(ROOT, plan)
        elif name == "orphans":
            try:
                found = project_orphans(ROOT, plan)
            except lib.SettingsError as e:
                found = [str(e)]   # (said as what it found)
        else:
            found = [line for check, line in lib.problems(_data(self, plan)) if check == name]
        self.assertEqual(found, [], "\n".join(found))
    test.__name__ = f"test_{name}"
    test.__doc__ = CHECKS[name]
    return test


class SettingsChecks(unittest.TestCase):
    """The project's settings held to the format, a test each."""

    def test_the_checks_are_the_templates(self):
        """Every check the template lists is one the library makes, and the other way round."""
        self.assertEqual(sorted(CHECKS), sorted([*lib.CHECKS, "orphans", "runtime_copy"]))


for _name in CHECKS:
    setattr(SettingsChecks, f"test_{_name}", _check(_name))


# ---------------------------------------------------------------- the library's own tests
with open(os.path.join(HERE, "sample.toml"), encoding="utf-8") as _fh:
    SAMPLE = _fh.read()   # (the library's own tests' settings file, beside this: the Go reader's tests read it too)


def sample(**stores):
    cat = lib.Catalog(tomllib.loads(SAMPLE), "sample.toml")
    cat.store("project", lib.MemoryStore(stores.get("project", {}), "app.toml"))
    cat.store("computer", lib.MemoryStore(stores.get("computer", {}), "app.local.toml"))
    cat.answer("manifest_domain", lambda member: "my_thing")
    cat.answer("formulas", lambda member: [["nas", "TrueNAS app"], ["ha", "Home Assistant"]])
    cat.answer("servers", lambda member: [{"id": "nas1", "name": "The NAS"}, "nas2"])
    cat.answer("gates", lambda member: ["push", "merge"])
    return cat


class LibraryTests(unittest.TestCase):
    """The reader itself: the format held, a value by its id, defaults, sets, what's saved, nothing orphaned."""

    def broken(self, change) -> list[tuple[str, str]]:
        data = tomllib.loads(SAMPLE)
        change(data["settings"], data)
        return lib.problems(data)

    def test_a_file_in_the_format_has_nothing_wrong(self):
        """The sample is in the format, and is read."""
        self.assertEqual(lib.problems(tomllib.loads(SAMPLE)), [])
        self.assertEqual(sample().subs_of("host"), ["port"])

    def test_a_setting_with_no_info_cant_be_loaded(self):
        """A setting's info is three parts — what it does, when to use it, its configuration — and each is held: there
        (a switch needs no "config", a sub-setting no "when"), long enough to explain something, a sentence, and with
        words of its own: not its label and summary again, nor the part before it. What isn't is said under "info",
        a sub-setting's as any other's, and the file can't be loaded."""
        long = "Tells a push which machine on your network to copy the project to and then restart it on, over one connection."
        for id_ in ("host", "port", "server_limit"):
            for change, why in ((lambda s, d, i=id_: s[i].pop("info"), "info: missing"),
                                (lambda s, d, i=id_: s[i].update(info="The machine's address. Its Network page shows it."), "info: missing"),   # (one line of text: not the three parts)
                                (lambda s, d, i=id_: s[i]["info"].pop("does"), r"info.does: missing \(what it does\)"),
                                (lambda s, d, i=id_: s[i]["info"].pop("config"), r"info.config: missing \(its configuration: what to give it\)"),
                                (lambda s, d, i=id_: s[i]["info"].update(does="  "), "info.does: what it does, in words"),
                                (lambda s, d, i=id_: s[i]["info"].update(does="An open finding."), r"info.does: 16 letters, and what it does takes 80 at least"),
                                (lambda s, d, i=id_: s[i]["info"].update(does=long.rstrip(".")), "info.does: one or more sentences"),
                                (lambda s, d, i=id_: s[i]["info"].update(why="Because."), "info.why: not a part of it"),
                                (lambda s, d, i=id_: s[i]["info"].update(does=(" ".join([s[i]["label"]] * 30)).capitalize() + "."), r"info.does: says its label and summary again \(0 words of its own, and it takes 6\)"),
                                (lambda s, d, i=id_: s[i]["info"].update(config=s[i]["info"]["does"]), r"info.config: says its label, its summary and the parts before it again \(0 words")):
                with self.subTest(id=id_, why=why):
                    got = self.broken(change)
                    self.assertEqual({c for c, _ in got} - {"format"}, {"info"}, got)
                    self.assertRegex("\n".join(line for c, line in got if c == "info"), rf"\[settings.{id_}\] {why}")
        self.assertRegex(self.broken(lambda s, d: s["host"]["info"].pop("when"))[0][1], r"\[settings.host\] info.when: missing \(when to use it\)")
        self.assertRegex(self.broken(lambda s, d: s["host"]["info"].update(when=s["host"]["info"]["does"]))[0][1], r"info.when: says its label, its summary and the parts before it again")
        self.assertEqual(self.broken(lambda s, d: s["port"]["info"].pop("when", None)), [])   # (a sub-setting: its parent says when)
        switch = {"label": "Hold syncs", "info": {"does": long, "when": "Turn it on while people are watching in the evening, and off again once the house has gone to bed for the night."},
                  "kind": "switch", "group": "net", "saved": {"file": "project", "key": "hold"}, "default": False}
        self.assertEqual(self.broken(lambda s, d: s.update(hold=dict(switch))), [])   # (a switch is on or off: no configuration to say)
        self.assertRegex(self.broken(lambda s, d: s.update(hold={**switch, "kind": "text"}))[0][1], r"\[settings.hold\] info.config: missing")
        data = tomllib.loads(SAMPLE)
        del data["settings"]["port"]["info"]
        with self.assertRaisesRegex(lib.SettingsError, r"\[settings.port\] info: missing"):
            lib.Catalog(data)
        self.assertEqual([c for c, _ in self.broken(lambda s, d: s["host"].update(summary="the address (default: nas)"))], ["info"])

    def test_each_rule_of_the_format_is_held(self):
        """What the format doesn't allow is said, under the check it fails."""
        for change, check, why in (
                (lambda s, d: s["host"].update(colour="red"), "format", "colour: not a field of a setting"),
                (lambda s, d: s["host"].update(kind="dial"), "format", "kind = 'dial'"),
                (lambda s, d: s["host"].update(group="nowhere"), "format", "no such group"),
                (lambda s, d: s["host"].pop("saved"), "format", "saved: missing"),
                (lambda s, d: s["server_url"].update(each="racks"), "format", "no such set"),
                (lambda s, d: s["server_url"].update(saved={"file": "", "key": "url"}), "format", "which of the app's files"),
                (lambda s, d: d.update(extras={}), "format", r"\[extras\]: not a table"),
                (lambda s, d: d["format"].update(version=9), "format", "version = 1: missing"),
                (lambda s, d: s["domain"].update(shown_when={"setting": "nothing", "is": 1}), "format", "shown_when"),
                (lambda s, d: s["host"].update(default="nas.local"), "defaults", "more than one way"),
                (lambda s, d: s["port"].pop("default"), "defaults", "no way"),
                (lambda s, d: s["domain"].pop("default_says"), "defaults", "go together"),
                (lambda s, d: s["token"].pop("revert_why"), "defaults", "says why"),
                (lambda s, d: s["port"].update(under="nothing"), "subsettings", "no such setting"),
                (lambda s, d: s["domain"].update(under="port", shown_when=None) or s["domain"].pop("shown_when"), "subsettings", "one level only"),
                (lambda s, d: s["port"].update(group="server"), "subsettings", "its parent's"),
                (lambda s, d: s["mode"].update(choices=[["nas", "NAS"]]), "fields", "choices or choices_from"),
                (lambda s, d: s["server_limit"].pop("max"), "fields", "its min and max"),
                (lambda s, d: s["server_limit"].update(default=500), "fields", "default: Limit: from 1 to 100"),
                (lambda s, d: s["host"].update(pattern="[a-z]+"), "fields", "pattern_says"),
                (lambda s, d: s["port"].update(default=99999), "fields", "a port"),
                (lambda s, d: s["domain"].update(saved={"file": "computer", "key": "tour.host"}), "unique", "host is saved there too"),
                (lambda s, d: s.update(domain2={**s["domain"], "shown_when": {"setting": "mode", "is": "ha"}}), "unique", "domain is saved there too")):
            with self.subTest(why=why):
                got = self.broken(change)
                self.assertTrue(any(c == check for c, _ in got), got)
                self.assertRegex("\n".join(line for c, line in got if c == check), why)
        self.assertEqual(self.broken(lambda s, d: s["host"].update(demo_colour="red")), [])   # (a field of the app's own)
        # two settings one value picks between (never shown together) may be kept in one place: a formula's host, and another's
        self.assertEqual(self.broken(lambda s, d: s.update(domain2={**s["domain"], "shown_when": {"setting": "mode", "is": "nas"}})), [])

    def test_a_value_is_asked_for_and_saved_by_its_id(self):
        """value_of: what's saved, else the default as it stands (fixed, worked out, none); save_value holds a value to
        the setting and writes it where the setting says, and None takes it away."""
        cat = sample(computer={"tour": {"host": "nas.local"}})
        self.assertEqual((cat.value_of("host"), cat.value_of("port"), cat.value_of("domain")), ("nas.local", 22, "my_thing"))
        self.assertEqual(cat.default_of("port"), {"value": 22, "says": "", "how": "fixed"})
        self.assertEqual(cat.default_of("domain"), {"value": "my_thing", "says": "Read from the integration's manifest.json", "how": "worked_out"})
        self.assertEqual(cat.default_of("host")["how"], "required")
        cat.save_value("port", 2222)
        self.assertEqual(cat._stores["computer"].data, {"tour": {"host": "nas.local", "port": 2222}})
        cat.save_value("port", None)
        cat.save_value("host", "")
        self.assertEqual(cat.held("port", "22"), "22")
        self.assertEqual(cat._stores["computer"].data, {})
        for id_, value, why in (("port", 70000, "a port"), ("host", "-oProxyCommand=x", "doesn't look like a host"), ("mode", "windows", "one of nas, ha"),
                                ("host", 7, "text"), ("nothing", 1, "no setting 'nothing'")):
            with self.subTest(id=id_), self.assertRaisesRegex(lib.SettingsError, why):
                cat.save_value(id_, value)
        self.assertEqual(cat.shown("domain"), False)
        cat.save_value("mode", "ha")
        self.assertEqual((cat.shown("domain"), cat.where_saved("mode")), (True, ("project", ["tour", "formula"])))

    def test_a_setting_in_a_set_has_a_value_for_each_member(self):
        """One definition, a value a member: saved inside its member, asked for with it; a shared set's "*" is every
        member's that has none of its own, and what a member's revert goes back to; two sets, one inside the other."""
        cat = sample()
        cat.save_value("server_url", "https://nas1:32400", "nas1")
        cat.save_value("server_limit", 80, ("nas1", "push"))
        self.assertEqual(cat._stores["project"].data, {"servers": {"nas1": {"url": "https://nas1:32400", "push": {"limit": 80}}}})
        self.assertEqual((cat.value_of("server_limit", ("nas1", "push")), cat.value_of("server_limit", ("nas1", "merge")), cat.saved_value("server_url", "nas2")), (80, 50, None))
        cat.save_value("server_url", "https://every.one", "*")
        self.assertEqual(cat.value_of("server_url", "nas2"), "https://every.one")
        self.assertEqual(cat.default_of("server_url", "nas2"), {"value": "https://every.one", "says": "every server's", "how": "shared"})
        self.assertEqual(cat.default_of("server_url", "*")["how"], "required")
        self.assertEqual(cat.value_of("server_url"), "https://every.one")   # (no member, in a shared set: the shared one)
        with self.assertRaisesRegex(lib.SettingsError, "one for each of servers and gates"):
            cat.value_of("server_limit", "nas1")
        self.assertEqual(cat.members_of("servers"), [{"id": "nas1", "name": "The NAS"}, {"id": "nas2", "name": "nas2"}])
        self.assertEqual(cat.saved_says("server_limit"), {"key": "servers.<server>.<gate>.limit", "file": "app.toml"})

    def test_a_sets_shared_value_may_be_kept_apart_and_a_setting_of_it_in_a_file_of_its_own(self):
        """A shared set may say where the value every member shares is kept (shared_saved), where that isn't beside the
        members; and a setting of a set may say its own file, where the members' values are in more than one. Both are
        said where it's saved, on the page, and in what's saved that nothing defines."""
        data = tomllib.loads(SAMPLE)
        data["sets"]["servers"]["shared_saved"] = {"key": "every"}
        data["settings"]["server_url"]["saved"] = {"file": "computer", "key": "url"}
        cat = lib.Catalog(data, "sample.toml")
        project, computer = cat.store("project", lib.MemoryStore({}, "app.toml")), cat.store("computer", lib.MemoryStore({}, "app.local.toml"))
        for name, says in (("servers", ["nas1", "nas2"]), ("gates", ["push"]), ("manifest_domain", "my_thing"), ("formulas", [["nas", "TrueNAS app"]])):
            cat.answer(name, lambda member, says=says: says)
        cat.save_value("server_url", "https://every.one", "*")
        cat.save_value("server_url", "https://nas1:32400", "nas1")
        cat.save_value("token", "s3cret", "*")
        cat.save_value("server_limit", 9, ("*", "push"))
        self.assertEqual(computer.data, {"every": {"url": "https://every.one"}, "servers": {"nas1": {"url": "https://nas1:32400"}}})
        self.assertEqual(project.data, {"every": {"token": "s3cret", "push": {"limit": 9}}})
        self.assertEqual(cat.default_of("server_url", "nas2"), {"value": "https://every.one", "says": "every server's", "how": "shared"})
        self.assertEqual((cat.value_of("server_url", "nas1"), cat.value_of("server_url", "nas2"), cat.value_of("server_url")), ("https://nas1:32400", "https://every.one", "https://every.one"))
        self.assertEqual(cat.saved_says("server_url"), {"key": "servers.<server>.url", "file": "app.local.toml"})
        self.assertEqual(cat.saved_says("server_url", shared=True), {"key": "every.url", "file": "app.local.toml"})
        self.assertEqual(cat.saved_says("token", shared=True), {"key": "every.token", "file": "app.toml"})
        page = cat.page()
        self.assertEqual(page["settings"]["server_url"]["saved_says_shared"], {"key": "every.url", "file": "app.local.toml"})
        self.assertEqual(page["states"]["server_url|nas2"]["default"]["how"], "shared")
        project.data["every"]["colour"] = "red"
        project.data["servers"] = {"nas1": {"token": "x", "merge": 1}}
        self.assertEqual(sorted(cat.unknown("project")), ["every.colour", "servers.nas1.merge"])
        # held to the format: only a shared set has a shared value's place; a setting of a set has a file, its own or its set's
        for change, why in ((lambda d: d["sets"]["gates"].update(shared_saved={"key": "all"}), r"\[sets.gates\] shared_saved: only a set that's shared"),
                            (lambda d: d["sets"]["servers"]["saved"].pop("file"), r"\[settings.token\] saved.file: neither it nor its set says"),
                            (lambda d: d["settings"]["token"]["saved"].update(file=""), r"\[settings.token\] saved.file: which of the app's files")):
            bad = tomllib.loads(SAMPLE)
            change(bad)
            with self.subTest(why=why):
                self.assertRegex("\n".join(line for _, line in lib.problems(bad)), why)

    def test_a_set_that_cascades_gives_each_member_the_value_of_the_one_before_it(self):
        """cascade = true on a set: its members are in the order the app answers them, a member with no value of its
        own takes what the one before it comes to (the first: the setting's default), its default says whose that is,
        and a value saved the same as it would take anyway isn't kept."""
        data = tomllib.loads(SAMPLE)
        data["sets"]["gates"]["cascade"] = True
        cat = lib.Catalog(data, "sample.toml")
        project = cat.store("project", lib.MemoryStore({}, "app.toml"))
        cat.answer("gates", lambda member: [{"id": "dev", "name": "Dev"}, "push", "merge"])
        cat.store("computer", lib.MemoryStore({}, "app.local.toml"))
        for name, says in (("servers", ["nas1"]), ("manifest_domain", "my_thing"), ("formulas", [["nas", "TrueNAS app"]])):
            cat.answer(name, lambda member, says=says: says)

        def at(gate: str) -> tuple[str, str]:
            return ("nas1", gate)
        self.assertEqual([cat.value_of("server_limit", at(g)) for g in ("dev", "push", "merge")], [50, 50, 50])
        self.assertEqual(cat.default_of("server_limit", at("dev")), {"value": 50, "says": "", "how": "fixed"})
        cat.save_value("server_limit", 80, at("dev"))
        self.assertEqual([cat.value_of("server_limit", at(g)) for g in ("dev", "push", "merge")], [80, 80, 80])
        self.assertEqual(cat.default_of("server_limit", at("push")), {"value": 80, "says": "Dev's", "how": "cascade"})
        cat.save_value("server_limit", 90, at("push"))
        self.assertEqual(cat.default_of("server_limit", at("merge")), {"value": 90, "says": "push's", "how": "cascade"})
        cat.save_value("server_limit", 90, at("merge"))   # (what it takes from push anyway: nothing of its own is kept)
        self.assertEqual(project.data, {"servers": {"nas1": {"dev": {"limit": 80}, "push": {"limit": 90}}}})
        cat.save_value("server_limit", 80, at("push"))   # (now the same as Dev's: its own is taken away, and merge follows)
        self.assertEqual(project.data, {"servers": {"nas1": {"dev": {"limit": 80}}}})
        self.assertEqual([cat.value_of("server_limit", at(g)) for g in ("dev", "push", "merge")], [80, 80, 80])
        self.assertEqual(cat.page(members={"servers": ["nas1"]})["sets"]["gates"], {"title": "Gates", "member": "gate", "shared": False, "cascade": True, "list": False})
        bad = tomllib.loads(SAMPLE)
        bad["sets"]["servers"]["cascade"] = True
        self.assertRegex("\n".join(line for _, line in lib.problems(bad)), r"\[sets.servers\]: a set is shared .* or cascade .*, not both")

    def test_the_page_is_given_every_setting_and_never_a_secret(self):
        """page(): each definition with its sub-settings and where it's saved, a state for each setting and member,
        and of a secret only whether one is set."""
        cat = sample(project={"servers": {"nas1": {"token": "s3cret"}}})
        page = json.loads(json.dumps(cat.page()))
        self.assertEqual(page["settings"]["host"]["subs"], ["port"])
        self.assertEqual(page["settings"]["port"]["group"], "net")
        self.assertEqual(page["settings"]["host"]["saved_says"], {"key": "tour.host", "file": "app.local.toml"})
        self.assertEqual(page["states"]["mode"]["choices"], [["nas", "TrueNAS app"], ["ha", "Home Assistant"]])
        self.assertEqual(sorted(k for k in page["states"] if k.startswith("server_url")), ["server_url|*", "server_url|nas1", "server_url|nas2"])
        self.assertIn("server_limit|nas2|merge", page["states"])
        self.assertEqual(page["states"]["token|nas1"], {"default": {"value": None, "says": "", "how": "required"}, "shown": True, "set": True})
        self.assertNotIn("s3cret", json.dumps(page))
        self.assertTrue(all(s["info"]["does"].strip() for s in page["settings"].values()))

    def test_settings_that_are_data_are_added_and_held_to_the_format(self):
        """add(): a formula's settings join the file's, held to the same format (one with no info can't be added)."""
        cat = sample()
        new = {"app_name": {"label": "app", "info": {"does": "Names the app on the NAS that a push restarts once the files are copied, so the new code is what runs.", "when": "Give it when the project is first set up to push, and again if the app is ever installed afresh under another name.", "config": "The name the Apps page lists it under, in small letters."}, "kind": "text", "group": "net", "saved": {"file": "project", "key": "tour.app"}, "required": True}}
        cat.add(new)
        self.assertEqual(cat.default_of("app_name")["how"], "required")
        del new["app_name"]["info"]
        with self.assertRaisesRegex(lib.SettingsError, r"\[settings.app_name\] info: missing"):
            sample().add(new)

    def test_a_saved_value_no_setting_has_is_found(self):
        """unknown(): a value saved at a key no setting has, a member's too; only under the tables named."""
        cat = sample(project={"tour": {"formula": "ha", "colour": "red"}, "servers": {"nas1": {"url": "https://x", "push": {"limit": 5, "speed": 1}, "merge": 7}}, "other": {"x": 1}})
        self.assertEqual(sorted(cat.unknown("project")), ["other", "servers.nas1.merge", "servers.nas1.push.speed", "tour.colour"])
        self.assertEqual(cat.unknown("project", ["tour"]), ["tour.colour"])

    def test_nothing_is_orphaned(self):
        """orphans(): a setting no code asks for, an id asked for that isn't defined, an empty group or set, a name
        never answered, and one answered that nothing asks for — and nothing, where each is used."""
        cat = sample()
        good = {"app.py": 'cat.answer("manifest_domain", f); cat.answer("formulas", f); cat.answer("servers", f); cat.answer("gates", f)\n'
                          'cat.value_of("host"); cat.value_of("mode"); cat.value_of("domain"); cat.value_of("token", m)',
                "app.js": 'S.drawGroup("server")'}
        self.assertEqual(lib.orphans(cat, good), [])
        gocode = {"main.go": 'cat.Answer("manifest_domain", f)\ncat.Answer(`formulas`, f)\ncat.Answer("servers", f)\ncat.Answer("gates", f)\n'
                             'v, err := cat.ValueOf("host")\nc, err := cat.ChoicesOf("mode")\nf, p, err := cat.WhereSaved("domain")\n_, err = cat.SavedValue("token", m)\n'
                             'rows := S.DrawGroup("server")'}
        self.assertEqual(lib.orphans(sample(), gocode), [])   # (Go: the same asks, by their Go names, and a `raw string`)
        self.assertRegex("\n".join(lib.orphans(sample(), {"main.go": gocode["main.go"] + '\ncat.SaveValue("hots", 1)\ncat.ChoicesOf("mood")\ncat.WhereSaved("dommain")'})),
                         r"main.go: asks for the setting 'dommain'[^\n]*\nmain.go: asks for the setting 'hots'[^\n]*\nmain.go: asks for the setting 'mood'")
        cat.add({"extra_one": {"label": "x", "info": {"does": "Stands in for a setting that came as data, as a formula's do, and is drawn with its group on the page.", "when": "Never by name in the code, which is the point of it here, since nothing orphaned should be said of one added so."}, "kind": "switch", "group": "net", "saved": {"file": "project", "key": "x"}, "default": False}})
        self.assertEqual(lib.orphans(cat, good), [])   # (one added as data: no code names it)
        bad = {"app.py": 'cat.answer("manifest_domain", f); cat.answer("servers", f); cat.answer("gates", f); cat.answer("weather", f)\ncat.value_of("host"); cat.save_value("hots", 1)',
               "app.js": 'S.drawGroup("servers")'}
        data = tomllib.loads(SAMPLE)
        data["groups"]["empty"] = {"title": "Empty"}
        data["sets"]["racks"] = {"title": "Racks", "member": "rack", "saved": {"file": "project", "key": "racks"}, "members_from": "racks"}
        got = "\n".join(lib.orphans(lib.Catalog(data), bad))
        for why in (r"\[settings.mode\]: no code asks for it", r"\[settings.server_url\]: no code asks for it", "app.py: asks for the setting 'hots', which isn't defined",
                    "asks for the group 'servers', which isn't defined", r"\[groups.empty\]: no setting is in it", r"\[sets.racks\]: no setting is one for each of it",
                    r"choices_from = 'formulas': the app never answers it", "the app answers 'weather', which no setting asks for"):
            self.assertRegex(got, why)
        self.assertNotRegex(got, r"\[settings.(host|port)\]")   # (a sub-setting is asked for with its parent)

    def test_the_project_checks_find_what_a_project_gets_wrong(self):
        """project_orphans and runtime_copy on a project made here: its code, a committed file's unknown key, and an
        app's copy of the library that was edited or isn't there."""
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "app", "lib"))
            with open(os.path.join(root, "app", "settings.toml"), "w", encoding="utf-8") as fh:
                fh.write(SAMPLE)
            with open(os.path.join(root, "app", "main.py"), "w", encoding="utf-8") as fh:
                fh.write('for n in ("manifest_domain", "formulas", "servers", "gates"): pass\ncat.answer("manifest_domain", f); cat.answer("formulas", f); cat.answer("servers", f); cat.answer("gates", f)\n'
                         'cat.value_of("host"); cat.value_of("mode"); cat.value_of("domain"); draw_group("server")\n')
            with open(os.path.join(root, "app.toml"), "w", encoding="utf-8") as fh:
                fh.write('[tour]\nformula = "ha"\ncolour = "red"\n[other]\nx = 1\n')
            plan = {"file": "app/settings.toml", "code": ["app/*.py"], "saved": {"project": {"path": "app.toml", "tables": ["tour"]}}, "runtime": {"app/lib": ["settingslib.py", "settings.js"]}}
            self.assertEqual(project_orphans(root, plan), ["app.toml: tour.colour is saved there, and no setting has that key"])
            self.assertIn("names none of the app's code", project_orphans(root, {**plan, "code": ["nowhere/*.py"]})[0])
            self.assertRegex("\n".join(runtime_copy(root, plan)), r"app/lib/settingslib.py: not there[^\n]*\napp/lib/settings.js: not there")
            for name in ("settingslib.py", "settings.js"):
                with open(os.path.join(HERE, name), "rb") as a, open(os.path.join(root, "app", "lib", name), "wb") as b:
                    b.write(a.read())
            self.assertEqual(runtime_copy(root, plan), [])
            with open(os.path.join(root, "app", "lib", "settings.js"), "a", encoding="utf-8") as fh:
                fh.write("// edited\n")
            self.assertEqual(runtime_copy(root, plan), ["app/lib/settings.js: not the library's copy (copy it again from " + os.path.relpath(HERE, root) + "/settings.js: never edit it there)"])
            self.assertIn("doesn't say where the app's own copy", runtime_copy(root, {**plan, "runtime": {}})[0])

    def test_a_list_sets_members_are_its_items_and_pairs_are_names_with_their_text(self):
        """A set kept as a list ([[watch]]): each member is an item, by its place from 1, read and written there, and
        an item's key no setting has is found; it has no shared value. A pairs setting: names, each with its text."""
        data = tomllib.loads(SAMPLE)
        data["sets"]["watches"] = {"title": "Watches", "member": "watch", "saved": {"file": "project", "key": "watch"}, "members_from": "watches", "list": True}
        info = {"does": "Names the folder of one project this machine keeps an eye on, so its changes are noticed while nobody is there.",
                "when": "Add one for each project that should be checked by itself, and take it away once the project is no longer worked on.",
                "config": "The path to the project's folder, starting at your home folder or absolute."}
        data["settings"]["watch_project"] = {"label": "Project", "kind": "text", "group": "server", "each": "watches", "saved": {"key": "project"}, "required": True, "info": info}
        data["settings"]["env"] = {"label": "Environment", "kind": "pairs", "group": "server", "saved": {"file": "project", "key": "deploy.env"}, "default": {},
                                   "info": {**info, "config": "Each variable's name, with the text it's given, such as DEBUG with 1."}}
        self.assertEqual(lib.problems(data), [])
        cat = lib.Catalog(data, "sample.toml")
        project = cat.store("project", lib.MemoryStore({"watch": [{"project": "~/a"}, {"project": "~/b", "every": "1m"}]}, "app.toml"))
        cat.answer("watches", lambda member: ["1", "2"])
        cat.store("computer", lib.MemoryStore({}, "app.local.toml"))
        for name in ("servers", "gates", "formulas"):
            cat.answer(name, lambda member: [])
        cat.answer("manifest_domain", lambda member: None)
        self.assertEqual((cat.page()["sets"]["watches"]["list"], cat.page()["sets"]["servers"]["list"]), (True, False))   # (the drawer's to know)
        self.assertEqual((cat.value_of("watch_project", "1"), cat.value_of("watch_project", "2"), cat.saved_value("watch_project", "3")), ("~/a", "~/b", None))
        cat.save_value("watch_project", "~/c", "3")
        cat.save_value("watch_project", "~/z", "1")
        self.assertEqual(project.data["watch"], [{"project": "~/z"}, {"project": "~/b", "every": "1m"}, {"project": "~/c"}])
        cat.save_value("watch_project", None, "1")
        self.assertEqual(project.data["watch"][0], {})   # (an item keeps its place: the members after it are still themselves)
        self.assertEqual(cat.unknown("project", ["watch"]), ["watch.#2.every"])
        self.assertEqual(cat.saved_says("watch_project"), {"key": "watch.<watch>.project", "file": "app.toml"})
        fresh = lib.MemoryStore({}, "x.toml")
        cat.store("project", fresh)
        cat.save_value("watch_project", "~/d", "2")
        self.assertEqual(fresh.data, {"watch": [{}, {"project": "~/d"}]})
        cat.save_value("env", {"DEBUG": "1", "APP_KEY": "x"})
        self.assertEqual(fresh.data["deploy"], {"env": {"DEBUG": "1", "APP_KEY": "x"}})
        for value, why in (("DEBUG=1", "a table of names"), ({"BAD NAME": "1"}, "a name is"), ({"A": 1}, "text")):
            with self.subTest(why=why), self.assertRaisesRegex(lib.SettingsError, why):
                cat.save_value("env", value)
        for change, why in ((lambda d: d["sets"]["watches"].update(shared=True), r"\[sets.watches\]: a list's members are its items"),
                            (lambda d: d["sets"]["watches"]["saved"].update(key=""), r"\[sets.watches\] saved.key: where its list is kept")):
            bad = copy.deepcopy(data)
            change(bad)
            with self.subTest(why=why):
                self.assertRegex("\n".join(line for _, line in lib.problems(bad)), why)

    def test_a_json_file_keeps_what_is_saved(self):
        """JsonStore: made when the first value is saved, a table left empty taken away with its last value."""
        with tempfile.TemporaryDirectory() as tmp:
            cat = sample()
            store = cat.store("project", lib.JsonStore(os.path.join(tmp, "values.json")))
            self.assertEqual(store.read(), {})
            cat.save_value("server_limit", 9, ("nas1", "push"))
            cat.save_value("mode", "ha")
            self.assertEqual(store.read(), {"servers": {"nas1": {"push": {"limit": 9}}}, "tour": {"formula": "ha"}})
            cat.save_value("server_limit", None, ("nas1", "push"))
            self.assertEqual(store.read(), {"tour": {"formula": "ha"}})
            with self.assertRaisesRegex(lib.SettingsError, "only read here"):
                cat.store("computer", lib.TomlStore(os.path.join(tmp, "x.toml"))).write(["a"], 1)


if __name__ == "__main__":
    unittest.main()
