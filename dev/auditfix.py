#!/usr/bin/env python3
"""Fixing the findings: what whoever fixes them (a person, or an AI assistant) reads and reports, and the audits page's
Confirm (dev/audit.py fixes, fixed, found).

    dev/audit.py fixes                      the open findings, and how to fix and report them
    dev/audit.py fixed F-YYYY-NNN [--mitigated | --attempted | --not-real] [--test Class.test_name]… [--note TEXT]
                                            a fix done (or tried): its tests and what was done, kept with the finding
    dev/audit.py found --via F-YYYY-NNN --title TEXT --lines path:start-end… (--cvss VECTOR | --hardening --priority P)
                       [--test Class.test_name]… --detail TEXT
                                            another case a fix's test found: a new finding, found by that test

A fixer never closes a finding: what it reports waits on the audits page's Findings ("Fixed, to confirm") until Confirm
runs its tests (and an auditor re-checks it, when there is one) and closes it.
"""

import contextlib
import datetime
import fcntl
import os
import subprocess
import sys
import tomllib
from typing import Any

from dev import audit

HOW = """# Fixing the security findings

These are the project's open security findings. Work in three steps: report on them first and wait for the
maintainer's decisions, then fix what they decide, then record each fix in their records.

Securing the code comes first. A fix that leaves a finding partly open is not a fix. Among fixes that are equally
secure, prefer one that breaks nothing.

## 1. Report first, and wait

Before you change anything, read each finding and the code it points to. Then report to the maintainer, as your reply
to whoever asked for this work. The report is for a person deciding which way each fix goes, so write it in plain
prose, in this order:

- **Decisions needed:** the findings with a real choice, because there's more than one way to fix them or the only fix
  breaks something. For each one, say what's wrong, give each option, and say whether it fully closes the finding and
  what it breaks. Give your recommendation, then the question you need answered.
- **Mitigations:** when there's no good fix, when any fix would clearly cause severe breakage, or when the fix must
  wait for something upstream to change, one option you can suggest is to mitigate the finding instead: lessen the
  problem, and record it as mitigated rather than fixed:

      python3 dev/audit.py fixed F-YYYY-NNN --mitigated --test TestClass.test_name --note "what you changed, and what is still open"

- **Will just do:** the findings with one obvious fix that breaks nothing, one line each.
- **Not real:** a finding that turns out not to be real gets no fix. Say why, one line each.
- **Checks:** the checks you'll write (step 2), one line each: what it looks for, and which findings it covers.

A change is breaking if it:
- refuses something that used to work;
- changes a default, or stored settings or data;
- changes a protocol or an API shape another install or version uses;
- needs the user to do something when they upgrade.

Report every break: what stops working, who it hits, what they'll notice, and how it could be softened (a migration, a
warning, a setting, a one-time step). The maintainer decides how to deal with each break, so never leave one out.

Don't change any code, tests or records until the maintainer answers. Then fix only what they agreed to, the way they
agreed.

## 2. Fix

Fix them one at a time. Keep each fix to what the finding needs, and don't change unrelated code. Keep the existing
tests passing. If an agreed fix turns out to break something you didn't report, or can't be done as agreed, stop and
tell the maintainer before you go on.

Add a **check**: a test that catches the same kind of problem anywhere in the code, not just where this finding was.
Don't add a test for only the spot you fixed: the fix removes it, and the check covers it anyway.
- One check per kind of problem. If one check catches several findings, write it once and name it on each of them.
- A check mustn't raise false alarms. If an important check can't avoid a few, mark each one in the check, with why it
  isn't a real case, so it stays visible.
- If there's no practical check for a finding, skip it, and say why in your report and in the finding's note.

If the check finds another case, don't fix it. Record it as a new finding (step 3), and leave the check failing on it:
the check is there to catch these, and it passes once they're fixed. Never hide a case from a test (an exclusion list,
a skip, a narrower pattern) unless the maintainer tells you to. If they do, say in the new finding exactly what to put
back.

## 3. Record each one as you go

Record each one with a command, as soon as it's done: the audits page shows your progress. The commands write to the
private security records; never edit them yourself.

    python3 dev/audit.py fixed F-YYYY-NNN --test TestClass.test_name --note "what you changed, and why it fixes it"
    python3 dev/audit.py fixed F-YYYY-NNN --mitigated --test TestClass.test_name --note "what you changed, and what is still open"
    python3 dev/audit.py fixed F-YYYY-NNN --attempted --note "what you tried, and what's left"
    python3 dev/audit.py fixed F-YYYY-NNN --not-real --note "why it isn't real"
    python3 dev/audit.py found --via F-YYYY-NNN --title "what's wrong, in a line" --lines path:start-end \\
        (--cvss CVSS:3.1/... | --hardening --priority low|medium|high) --test TestClass.test_name \\
        --detail "what is wrong, how it could be used, and how the check catches it"

Give `fixed` the check that covers it, as `--test` (leave `--test` out when there's no check). Give each new case's
`found` the same check. The check fails until every case it catches is fixed, so a finding is confirmed only once its
problem is gone everywhere the check looks. In `found`, `--via` is the finding you were fixing.

Run these in the maintainer's own checkout, on the maintainer's machine. They write to the records folder that
`private` names in dev/audit.toml, beside that checkout, and the audits page reads only that folder. In a copy (a clone,
a sandbox, a cloud workspace), they still succeed, but they write to the copy's records, and the maintainer never sees
them. Recording is your job, not the maintainer's: if you're working in a copy, use whatever way you have of running
commands on the maintainer's machine. Only if none works, tell the maintainer what you tried and why it failed, give
them the commands, and say nothing is recorded yet. Never copy record files into the maintainer's folder.

Before you say a fix is recorded, run `python3 dev/audit.py fixes` in the maintainer's checkout: it must list it under
"Reported fixed", and each new finding must be listed too. Then tell the maintainer whether recording succeeded, for
each fix and each new finding.

You can't close a finding, and don't try: the maintainer confirms each fix on the audits page, which runs its tests. When
you've been through them all, tell the maintainer:
- what you fixed, and what you mitigated;
- every break the fixes bring, and what to do about each one;
- how many new cases your checks found (run `python3 dev/audit.py fixes` again: it lists them), and which tests now
  fail because of them.

Then ask whether to fix those too.
"""


@contextlib.contextmanager
def _locked(root: str):
    """The private records, one writer at a time (the audits page, and these commands, can both change them)."""
    fd = os.open(os.path.join(root, "audits"), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def edit(root: str, aid: str, change) -> Any:
    """One audit's private half, read, changed by ``change(rec)`` (what it returns is returned), checked and written."""
    path = os.path.join(root, "audits", f"{os.path.basename(aid)}.toml")
    with _locked(root):
        with open(path, "rb") as fh:
            rec = tomllib.load(fh)
        got = change(rec)
        wrong = audit.problems(rec, "private")
        if wrong:
            raise ValueError("; ".join(wrong))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(audit.dump(rec, audit.PRIVATE_KEYS, "finding"))
    return got


def where_is(root: str, fid: str) -> tuple[audit.Audit, dict]:
    """A finding, and the audit whose record keeps it."""
    for a in audit.load(root)[0]:
        for f in a.findings:
            if f.get("id") == fid:
                return a, f
    raise ValueError(f"no finding {fid}")


def note(f: dict, said: str) -> None:
    f["checks"] = [*list(f.get("checks") or []), f"{datetime.date.today().isoformat()}: {said}"]


def waiting(f: dict) -> bool:
    """Reported fixed (or mitigated) by whoever fixed it, and not confirmed yet: the Findings tab's "To confirm"."""
    return f.get("status") == "open" and f.get("reported") in audit.DONE


def fixes_text(root: str) -> str:
    """What a fixer reads: how to fix and report them, then each open finding not yet reported fixed."""
    items = [(a, f) for a in audit.load(root)[0] for f in a.findings if f.get("status") == "open" and not waiting(f)]
    done = [(a, f) for a in audit.load(root)[0] for f in a.findings if waiting(f)]
    out = [HOW]
    if done:
        out.append(f"Reported fixed or mitigated, waiting for the maintainer to confirm: {', '.join(str(f['id']) for _, f in done)}.\n")
    new = [f for _, f in items if not audit.by_audit(f)]
    if new:
        out.append(f"New cases a fix's test found: {', '.join(str(f['id']) for f in new)} (ask before fixing these).\n")
    if not items:
        out.append("No open findings to fix.\n")
        return "\n".join(out)
    out.append(audit.brief(items).split("\n", 2)[-1].lstrip())
    return "\n".join(out)


def fixed(root: str, fid: str, status: str = "fixed", tests: list[str] | None = None, said: str = "") -> dict:
    """A fix reported: its status left open (only Confirm closes it), its tests and what was done kept with it."""
    if status not in audit.REPORTS:
        raise ValueError(f"a fix is {', '.join(audit.REPORTS)}, not {status!r}")
    a, f = where_is(root, fid)
    if f.get("status") != "open":
        raise ValueError(f"{fid} is {f.get('status')}, not open")
    guard, lost = audit.guards(tests or [], audit.test_names())
    if lost:
        raise ValueError(f"not tests in tests/: {', '.join(lost)} (name each as TestClass.test_name)")

    def change(rec):
        g = next(x for x in rec["finding"] if x.get("id") == fid)
        if guard:
            g["tests"] = sorted({*g.get("tests", []), *guard})
        g["reported"] = status
        g["reported_on"] = datetime.date.today().isoformat()
        note(g, f"fix reported ({status})" + (f", guarded by {', '.join(guard)}" if guard else ", no test named")
             + (f": {said.strip()[:800]}" if said.strip() else ""))
        return {"id": fid, "reported": status, "tests": g.get("tests", [])}
    return edit(root, a.id, change)


def found(root: str, via: str, x: dict) -> str:
    """Another case a fix's test found: a new open finding, kept in the record of the finding whose fix it was, found by
    that test (never by that record's audit, nor counted for its auditor)."""
    from dev import auditrun  # (its tidy: findings as records keep them)
    a, _ = where_is(root, via)
    guard, lost = audit.guards(x.get("tests", []), audit.test_names())
    if lost:
        raise ValueError(f"not tests in tests/: {', '.join(lost)} (name each as TestClass.test_name)")
    day = datetime.date.today()

    def change(rec):
        _, first = audit.next_ids(day, root)
        f = auditrun.tidy([x], day.year, first, [])[0]
        f.update(source="fix-test", via=via, found=day.isoformat(), main_by="fix report",
                 prints=audit.spot_prints(f["lines"], audit.ROOT))
        where = audit._repo()
        if where:   # (what missed it: the audits that read these lines before a fix's test found them)
            head = audit._rev("HEAD", where[0])
            f["introduced_in"] = audit.introduced(str(f["lines"][0]), head, where[0]) if head else ""
            f["missed_by"] = audit.missed({"id": "~", "date": day}, f, audit.load(root)[0], where[0])
        if guard:
            f["tests"] = guard
        note(f, f"found by a test written for {via}'s fix" + (f" ({', '.join(guard)})" if guard else ""))
        audit.scored(f)
        rec.setdefault("finding", []).append(f)
        return f["id"]
    return edit(root, a.id, change)


def run_tests(names: list[str]) -> tuple[bool, str]:
    """The tests named (Class.test_name), run here: whether they all passed, and what they said."""
    ids = audit.test_ids()
    missing = [n for n in names if n not in ids]
    if missing:
        return False, f"not in tests/ any more: {', '.join(missing)}"
    got = subprocess.run([sys.executable, "-m", "unittest", *[ids[n] for n in names]], cwd=audit.ROOT, capture_output=True,
                         text=True, timeout=1800, stdin=subprocess.DEVNULL)
    tail = (got.stderr or got.stdout).strip().splitlines()[-12:]
    return got.returncode == 0, "\n".join(tail)


def confirm(root: str, fids: list[str], tests=run_tests) -> list[dict]:
    """Confirm: each fix's tests run; passed, the finding is closed (where its fix is, and its commit, from git); failed,
    it's open again (not reported fixed), saying why."""
    out = []
    for fid in fids:
        a, f = where_is(root, fid)
        if not waiting(f):
            out.append({"id": fid, "ok": False, "said": "not waiting to be confirmed"})
            continue
        names = list(f.get("tests", []))
        ok, said = tests(names) if names else (True, "")
        at, sha = audit.fix_where(f, a)

        def change(rec, fid=fid, ok=ok, said=said, at=at, sha=sha, names=names):
            g = next(x for x in rec["finding"] if x.get("id") == fid)
            if ok:   # (a mitigation stays on record as mitigated, for a proper fix later: never just closed as fixed)
                done = "mitigated" if g.get("reported") == "mitigated" else "fixed"
                g.update(status=done, closed_by="confirm")
                if sha:
                    g["fixed_in"] = sha
                note(g, ("confirmed mitigated: " if done == "mitigated" else "confirmed: ") + (f"its tests passed ({', '.join(names)})" if names else "no test guards it")
                     + f"; the {'mitigation' if done == 'mitigated' else 'fix'} is {at}")
            else:
                g.pop("reported", None)
                note(g, f"not confirmed, open again: its tests failed: {said[-600:]}")
            return {"id": fid, "ok": ok, "where": at, "said": said if not ok else "", "status": g.get("status")}
        out.append(edit(root, a.id, change))
    return out


def _flag(args: list[str], name: str) -> list[str]:
    got, it = [], iter(args)
    for x in it:
        if x == name:
            got.append(next(it, ""))
        elif x.startswith(name + "="):
            got.append(x.split("=", 1)[1])
    return got


def main(args: list[str]) -> int:
    """dev/audit.py fixes / fixed / found."""
    root = audit.private_root()
    if not os.path.isdir(os.path.join(root, "audits")):
        print(f"  no private audits repo at {root}: the findings are kept there")
        return 2
    try:
        if args[0] == "fixes":
            print(fixes_text(root))
            return 0
        if args[0] == "fixed":
            ids = [x for x in args[1:2] if audit.FINDING_ID.match(x)]
            if not ids:
                print("usage: dev/audit.py fixed F-YYYY-NNN [--mitigated | --attempted | --not-real] [--test TestClass.test_name]… [--note TEXT]")
                return 2
            status = ("mitigated" if "--mitigated" in args else "attempted" if "--attempted" in args else "not-real" if "--not-real" in args
                      else "fixed")
            got = fixed(root, ids[0], status, _flag(args, "--test"), " ".join(_flag(args, "--note")))
            print(f"  {got['id']}: reported {got['reported']}" + (f", guarded by {', '.join(got['tests'])}" if got["tests"] else "")
                  + ". The maintainer confirms it on the audits page.")
            return 0
        if args[0] == "found":
            via = (_flag(args, "--via") or [""])[0]
            x = {"title": " ".join(_flag(args, "--title")), "lines": _flag(args, "--lines"), "tests": _flag(args, "--test"),
                 "detail": "\n".join(_flag(args, "--detail")), "type": "hardening" if "--hardening" in args else "vulnerability"}
            if "--hardening" in args:
                x["severity"] = (_flag(args, "--priority") or ["medium"])[0]
            else:
                x["cvss"] = (_flag(args, "--cvss") or [""])[0]
            if not audit.FINDING_ID.match(via) or not x["title"] or not x["lines"] or not x["detail"]:
                print("usage: dev/audit.py found --via F-YYYY-NNN --title TEXT --lines path:start-end… "
                      "(--cvss VECTOR | --hardening --priority low|medium|high) [--test TestClass.test_name]… --detail TEXT")
                return 2
            if x["type"] == "vulnerability" and not x["cvss"]:
                print("  a vulnerability needs its CVSS 3.1 vector (--cvss), or it's --hardening with a --priority")
                return 2
            fid = found(root, via, x)
            print(f"  {fid}: a new finding, found by a test written for {via}'s fix. Ask the maintainer before fixing it.")
            return 0
    except ValueError as e:
        print(f"  {e}")
        return 2
    return 2
