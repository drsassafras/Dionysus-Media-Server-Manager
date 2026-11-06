#!/usr/bin/env python3
"""A gate: the checks run as a moment in the code's life holds them — push (the pre-push hook), merge (a pull request),
release (a release tag, the release pull request) — the same way everywhere: by hand, from the hook, and on GitHub.

    python3 dev/gate.py push            # the checks as the push gate holds them (diff mode when [gates.push] says so)
    python3 dev/gate.py merge --ci      # on GitHub: no terminal, a line per block, the exit code the gate's verdict
    python3 dev/gate.py release
    python3 dev/gate.py --github        # what the workflow asks: [github] enabled, and each gate's github setting, as JSON
    python3 dev/gate.py --outputs       # the same for this GitHub event, as a job's outputs: enabled, gate, run (true, false or warn)

What's on the board and what each block is held to come from devtools.toml, as for any devtools run (dev/plan.py,
dev/policy.py): the context is the gate's (the branch's role or the tag's phase; on GitHub, from its refs; a merge
gate judges the code as the branch it's going to), and the base of the diff the gate's. Exit 0 when every block
passed (warnings and notes included), 1 when one failed, 2 when the gate couldn't be run; with --ci, a gate whose
github setting is "warn" exits 0 whatever it found (it reports, it doesn't block), and one that's off, or with
[github] enabled = false, is skipped (exit 0, said so). The run is kept as a record like any other (the UI shows it).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from dev import plan, policy  # noqa: E402

GATES = ("push", "merge", "release")


def runs_dir() -> str:
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.environ.get("DEVTOOLS_RUNS") or os.path.join(state, "devtools", "runs")


def github_config(cfg: policy.Config) -> dict:
    """What GitHub's workflow asks: whether it runs the gates at all, and each gate's setting (true, false, "warn")."""
    on = bool(cfg.table("github").get("enabled"))
    out: dict = {"enabled": on}
    for g in GATES:
        v = cfg.table("gates", g).get("github", False)
        out[g] = "warn" if v == "warn" else bool(v)
    return out


def event_gate(env: dict | None = None) -> str:
    """The gate a GitHub event is: a pull request the merge gate, a tag's push the release gate, anything else the push gate."""
    e = os.environ if env is None else env
    if e.get("GITHUB_EVENT_NAME", "").startswith("pull_request"):
        return "merge"
    if e.get("GITHUB_REF_TYPE") == "tag" or e.get("GITHUB_REF", "").startswith("refs/tags/"):
        return "release"
    return "push"


def outputs(cfg: policy.Config, env: dict | None = None) -> dict[str, str]:
    """The config job's outputs (.github/workflows/gates.yml): whether GitHub runs the gates, which gate this event is,
    and whether it runs — true, false, or warn (it reports, it doesn't block)."""
    gh = github_config(cfg)
    gate = event_gate(env)
    run = gh[gate] if gh["enabled"] else False
    return {"enabled": str(gh["enabled"]).lower(), "gate": gate, "run": run if isinstance(run, str) else str(run).lower()}


def run(gate: str, ci: bool = False, code: str = "local", stream=None) -> int:
    stream = stream or sys.stdout
    try:
        cfg = policy.Config(policy.find_file())
        cfg.gate(gate)   # (a gate the file hasn't got: said so)
        gh = github_config(cfg)
        if ci and (not gh["enabled"] or gh[gate] is False):
            stream.write(f"the {gate} gate is skipped on GitHub (devtools.toml: " + ("[github] enabled = false" if not gh["enabled"] else f"[gates.{gate}] github = false") + ")\n")
            return 0
        ctx = policy.context(code=code, gate=gate, cfg=cfg)
    except policy.ConfigError as e:
        stream.write(f"devtools.toml: {e}\n")
        return 2
    env = {**ctx.env(), "DEVTOOLS_REPO": cfg.root}
    if ctx.commit:
        env["DEVTOOLS_COMMIT"] = ctx.commit
    tty = sys.stdin.isatty() and not ci
    req = {"want": "checks", "code": code, "src": cfg.root, "root": cfg.root, "config": cfg.path, "self": os.path.join(ROOT, "dev", "devtools"),
           "orig_args": [], "env": env, "tty": tty, "dry_run": False, "tests_first": False, "no_sample": True, "skip_tests": False,
           "skip_future": True, "only": "", "sync": None, "changes": 0, "passed": {}, "future_passed": False,
           "label": ctx.head() if ctx.branch or ctx.phase else "Gate", "commit": ctx.commit[:7] if ctx.commit else "",
           "action": "check", "sync_only": False, "checks_only": True, "ui": {"cmd": f"python3 {os.path.join(ROOT, 'dev', 'devui.py')}", "cwd": cfg.root}}
    try:
        the_plan, ids = plan.build(req, cfg)
    except policy.ConfigError as e:
        stream.write(f"devtools.toml: {e}\n")
        return 2
    the_plan["header"] = [the_plan["header"][0] + (" · against " + ctx.base if ctx.base else "")]
    the_plan["sections"] = [s for s in the_plan["sections"] if s["id"] in ids]   # (the checks alone: no dependencies, no style review)
    os.makedirs(runs_dir(), exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    n = 1
    while True:
        where = os.path.join(runs_dir(), f"{stamp}-{n:02d}")
        try:
            os.mkdir(where)
            break
        except FileExistsError:
            n += 1
    with open(os.path.join(where, "plan.json"), "w", encoding="utf-8") as fh:
        json.dump(the_plan, fh)
    results = os.path.join(where, "results")
    got = subprocess.run([sys.executable, os.path.join(ROOT, "dev", "board.py"), os.path.join(where, "plan.json"), results],
                         cwd=cfg.root, stdin=None if tty else subprocess.DEVNULL)
    if got.returncode not in (0, 1):
        stream.write(f"the board ended with exit code {got.returncode}\n")
        return 2
    states: dict[str, str] = {}
    try:
        with open(results, encoding="utf-8") as fh:
            for line in fh:
                k, _, v = line.rstrip("\n").partition("=")
                if k in ids:
                    states[k] = v
    except OSError:
        return 2
    failed = [i for i in ids if states.get(i) not in ("pass", "info", "warn", "notrun")]
    verdict = "passed" if not failed else "FAILED: " + ", ".join(failed)
    stream.write(f"{gate} gate ({ctx.mode} mode, {ctx.role}" + (f", {ctx.phase} {ctx.tag}" if ctx.phase else "") + f"): {verdict}\n")
    if failed and ci and gh[gate] == "warn":
        stream.write(f"(reported only: [gates.{gate}] github = \"warn\")\n")
        return 0
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["--github"], ["--outputs"]):
        try:
            cfg = policy.Config(policy.find_file())
            if args[0] == "--github":
                print(json.dumps(github_config(cfg)))
            else:
                for k, v in outputs(cfg).items():
                    print(f"{k}={v}")
        except policy.ConfigError as e:
            print(f"devtools.toml: {e}", file=sys.stderr)
            return 2
        return 0
    if not args or args[0] not in GATES:
        print((__doc__ or "").split("\n\n")[1], file=sys.stderr)
        return 2
    gate, rest = args[0], args[1:]
    code = "local"
    if "--code" in rest:
        code = rest[rest.index("--code") + 1]
    return run(gate, ci="--ci" in rest, code=code)


if __name__ == "__main__":
    sys.exit(main())
