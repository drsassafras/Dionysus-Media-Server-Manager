#!/usr/bin/env python3
"""The board's plan, from the project's devtools.toml: which integrations run, in what order, and what each block is.

    python3 dev/plan.py <request.json> <plan.json>    # writes the plan (dev/board.py's); prints the check ids

devtools (the shell script) works out what the run is — the code, where it goes, how the NAS stands, the Sync block —
and writes that as the request; everything about the checks comes from here, so the board, the UI and GitHub's gates
put the same blocks on, with the same rules (dev/policy.py). The request:

    {"want": "check",          # copy | check | insync | passed | nothing | unreachable | checks | restart | only
     "code": "local", "src": "/the/code", "root": "/the/project", "self": "/path/to/devtools",
     "orig_args": ["dev", "--tests-first"],   # (what devtools was run with: the copy's own section runs it again)
     "env": {"DEVTOOLS_ROLE": "…", …},        # what every check is told (dev/policy.py's context)
     "tty": true, "dry_run": false, "tests_first": false, "no_sample": false, "skip_tests": false, "skip_future": false,
     "only": "", "sync": {…} | null, "changes": 12, "passed": {"when": "4 minutes ago (…)", "last": {"unit": "…"}},
     "future_passed": true, "label": "Local Copy on Dev", "commit": "a1b2c3d + uncommitted", "action": "check",
     "sync_only": false, "checks_only": false, "ui": {"cmd": "python3 …/devui.py", "cwd": "…"}}

The plan's sections (dev/board.py): each integration that's on and set up, in the file's order, as a check (its exit
code read the one way: 4 a warning, 3 a note, 5 not run), a block that only tells (not set up, skipped, already passed),
or, for the dependencies and the style guide, the two-step blocks they are. The check ids printed are the sections
whose result the run's outcome is (devtools records them for next time).
"""

from __future__ import annotations

import json
import os
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from dev import policy  # noqa: E402

RECORDED = ("tests", "checks")   # (the kinds whose pass is kept for the code: not run again while the code stays the same)


def q(cmd: str) -> str:
    return cmd


def told(i: dict, head: str, line: str) -> dict:
    """A block that only tells, grey: its header ``head``, its one line ``line``."""
    return {"id": i["id"], "title": i["title"], "cmd": f"printf '%s\\n' {shlex.quote(line)}", "info": head, **page(i)}


def page(i: dict) -> dict:
    return {"page": i["letter"]} if i.get("letter") and i["kind"] not in ("audit", "style") else {}


def build(req: dict, cfg: policy.Config | None = None) -> tuple[dict, list[str]]:
    """The plan and the check ids, from the request."""
    cfg = cfg or policy.Config(req.get("config") or policy.find_file(req["root"]))
    want, src, root, env = req["want"], req["src"], req["root"], dict(req.get("env") or {})
    only, tty = req.get("only") or "", bool(req.get("tty"))
    ints = cfg.integrations()
    by = {i["id"]: i for i in ints}
    sections: list[dict] = []
    ids: list[str] = []

    def add(sec: dict):
        if only and sec["id"] != only:
            return
        sections.append(sec)

    def check(i: dict, **more) -> dict:
        ids.append(i["id"])
        e = dict(env, DEVTOOLS_INTEGRATION=i["id"])
        sec = {"id": i["id"], "title": i["title"], "cmd": i["cmd"], "cwd": src, "stoppable": True, "env": e,
               "warn": {"rc": policy.WARN}, "tell": {"rc": policy.INFO}, "not_run_rc": policy.NOTRUN, **page(i)}
        if i["after"]:
            sec["after"] = [a for a in i["after"] if a in by or a == "sync"]
        if i["stop_with"]:
            sec["stop_with"] = [a for a in i["stop_with"] if a in by]
        if i["kind"] == "audit":   # a, once it says there's code to audit and while the board runs: the audits page
            sec["key"] = {"key": "a", "cmd": f"{i['cmd']} gui", "cwd": root, "does": "Open Audits", "opens": "the audits page",
                          "line": "press a to open the audits page"}
        sec.update(more)
        return sec

    skip_tests, skip_future = bool(req.get("skip_tests")), bool(req.get("skip_future"))
    runnable = [i for i in ints if i["kind"] not in ("upkeep", "style")]
    if want == "passed":
        when, last = req.get("passed", {}).get("when", ""), req.get("passed", {}).get("last", {})
        for i in runnable:
            if i["kind"] in RECORDED:
                if i["id"] == "future" and not req.get("future_passed"):
                    continue
                line = f"Passed {when}" + (f" · {last[i['id']]}" if last.get(i["id"]) else "")
                add({**told(i, "already passed", line)})
            else:
                add(check(i, **({"after": []} if i["kind"] == "coverage" else {})))   # (from what the tests that passed saved: quick)
    elif not skip_tests and want != "nothing":
        for i in runnable:
            if i["id"] == "future" and skip_future:
                add(told(i, "skipped", f"{i['title']} weren't run: devtools {req['code']} --skip-future."))
                continue
            add(check(i))
        if not any(i["kind"] in RECORDED for i in runnable):
            add({"id": "checks", "title": "Tests and checks", "cmd": "printf '%s\\n' 'No tests or checks are set up in devtools.toml ([integrations]).'",
                 "info": "not set up"})
    elif skip_tests and not req.get("sync_only"):
        add({"id": "checks", "title": "Tests and checks", "cmd": "printf '%s\\n' 'None of the integrations was run (--skip-tests).'", "info": "skipped"})
    if only:
        ids = [i for i in ids if i == only]
    sync = req.get("sync")
    if sync:   # (first on the board: the copy, or how the NAS stands)
        sync = dict(sync)
        after = ids if req.get("tests_first") else []
        if "after" in sync and sync["after"] == "__AFTER__":
            sync["after"] = after
        if isinstance(sync.get("go"), dict) and sync["go"].get("after") == "__AFTER__":
            sync["go"] = dict(sync["go"], after=after)
        sections.insert(0, sync)
    upkeep = next((i for i in ints if i["kind"] == "upkeep"), None)
    if upkeep and not req.get("sync_only") and (not only or only == upkeep["id"]):
        # (in your folder, where its pins move; its CVEs are of the copy being checked: DEVTOOLS_COPY, dev/cves.py)
        e = dict(env, DEVTOOLS_INTEGRATION=upkeep["id"], DEVTOOLS_COPY=src)
        sections.append({"id": upkeep["id"], "title": upkeep["title"], "cmd": f"export DEVTOOLS_UPKEEP=look; {upkeep['cmd']}", "cwd": root,
                         "env": e, "upkeep": True, **page(upkeep),
                         "then": {"cmd": f"export DEVTOOLS_UPKEEP=move; {upkeep['cmd']}", "after": list(ids)}})
    style = next((i for i in ints if i["kind"] == "style"), None)
    if style and not skip_tests and want != "nothing" and (not only or only == style["id"]):
        # the looks to approve, the last block: every time (it's quick, and approving them changes no code tested), on
        # their own — not a check the copy waits for, nor one whose warning fails the run
        cmd, e = style["cmd"], dict(env, DEVTOOLS_INTEGRATION=style["id"])
        sec: dict = {"id": style["id"], "title": style["title"], "cmd": cmd, "cwd": src, "stoppable": True, "env": e,
                     "warn": {"rc": policy.WARN}, "tell": {"rc": policy.INFO}}
        is_check = cmd.endswith(" check")
        base = cmd[: -len(" check")] if is_check else cmd
        if tty and is_check:   # r: the review page, in your folder, where approving them writes the guide
            sec["key"] = {"key": "r", "cmd": f"{base} review --board", "cwd": root, "does": "Review Style Changes",
                          "opens": "the review page", "line": "press r to review them"}
        if req.get("no_sample") or not is_check:
            sec["env"] = dict(e, DEVTOOLS_SAMPLE="0")   # (--no-sample: the check as it was, nothing of a sample on it)
        else:
            sec["then"] = {"cmd": f"{base} sample", "cwd": root, "after": [], "doing": "looks waiting: making sample",
                           "fail_note": "looks waiting: no sample"}
            if not req.get("dry_run"):
                sec["finale"] = {"cmd": f"{base} review --step", "check": cmd, "cwd": root,
                                 "env": {"DEVTOOLS_SAMPLE": "0", "DEVTOOLS_RECHECK": "1"}, "doing": "in review",
                                 "checking": "checking again", "label": "looks · review page open", "enter": "Skip review",
                                 "pass_note": "all approved"}
        sections.append(sec)
    # the board's header: what's sent and tested, the code's role or phase, the run's modes, the commit, how to stop it
    ctx = policy.context(env=env, cfg=cfg) if env.get("DEVTOOLS_ROLE") else policy.Context()
    head = req.get("label", "")
    if ctx.phase:   # (a phase names its role: said once)
        head += f" · {ctx.phase} ({ctx.tag})"
    elif ctx.role:
        head += f" · {ctx.role}"
    if ctx.gate:
        head += f" · {ctx.gate} gate"
    if ctx.diff:
        head += " · Diff"
    if req.get("tests_first"):
        head += " · Tests first"
    if req.get("dry_run"):
        head += " · Dry run"
    if req.get("no_sample") and style:
        head += " · No sample"
    kind = "run"
    if only:
        head += " · One block again"
        kind = "only"
    elif req.get("checks_only"):
        head += " · Checks only"
        kind = "checks"
    elif req.get("action") == "restart":
        head = req.get("label", "Restart")
        kind = "restart"
    elif req.get("sync_only"):
        head += " · Push only"
        kind = "sync"
    elif req.get("action") == "push":
        head += " · Push"
    keys = [{"key": "Enter", "does": "Cancel"}] if tty and ids else []
    import time
    plan = {"header": [head], "commit": req.get("commit", ""), "keys": keys, "mode": req["code"], "kind": kind, "only": only,
            "role": ctx.role, "phase": ctx.phase, "tag": ctx.tag, "gate": ctx.gate, "diff": ctx.diff, "branch": ctx.branch,
            "started": int(time.time()), "project": root, "config": cfg.path, "ui": req.get("ui") or {},
            "letters": {i["id"]: i["letter"] for i in ints}, "hub_key": policy.letters(["Hub"])[0], "sections": sections}
    return plan, ids


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print((__doc__ or "").split("\n")[2].strip(), file=sys.stderr)
        return 2
    with open(args[0], encoding="utf-8") as fh:
        req = json.load(fh)
    try:
        plan, ids = build(req)
    except policy.ConfigError as e:
        print(f"devtools.toml: {e}", file=sys.stderr)
        return 2
    with open(args[1] + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(plan, fh)
    os.replace(args[1] + ".tmp", args[1])
    print(" ".join(ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
