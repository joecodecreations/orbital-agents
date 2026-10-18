"""`orbital test`: the repo's tests, or (--live) operational checks against the installed system.

    orbital test                 unit + integration tests (pytest, live checks excluded)
    orbital test --live          the live suite (tests/live) against the running install: a PASS/FAIL table
    orbital test --all           both
    orbital test --live --json   machine-readable results; exit code 0 = everything passed

Live checks skip (with the reason) when their component is turned off on this machine.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path

from orbital.core.paths import REPO_ROOT

NAME = "test"
HELP = "Run the tests; --live checks the installed system end to end (PASS/FAIL table)"


def register(p):
    p.add_argument("--live", action="store_true", help="only the operational checks against this install")
    p.add_argument("--all", action="store_true", help="unit + integration tests and the live checks")
    p.add_argument("--json", action="store_true", help="print results as JSON")
    p.add_argument("-k", dest="keyword", help="only tests matching this pytest -k expression")
    p.add_argument("-x", "--exitfirst", action="store_true", help="stop at the first failure")
    p.add_argument("-v", "--verbose", action="store_true", help="show pytest's own output too")
    p.add_argument("pytest_args", nargs="*", help="extra arguments passed to pytest (after --)")


class Collector:
    """pytest plugin: one row per test (name from the docstring's first line)."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.names: dict[str, str] = {}
        self.order: list[str] = []

    def pytest_collection_modifyitems(self, session, config, items):
        for item in items:
            fn = getattr(item, "function", None)
            doc = (getattr(fn, "__doc__", None) or "").strip().splitlines()
            label = doc[0].strip() if doc else item.name
            if "[" in item.name:
                label += " " + item.name[item.name.index("["):]
            self.names[item.nodeid] = label
            self.order.append(item.nodeid)

    def pytest_runtest_logreport(self, report):
        row = self.rows.setdefault(report.nodeid, {"id": report.nodeid, "name": self.names.get(report.nodeid, report.nodeid),
                                                   "outcome": "passed", "detail": "", "seconds": 0.0})
        row["seconds"] = round(row["seconds"] + float(getattr(report, "duration", 0) or 0), 2)
        if report.skipped:
            row["outcome"] = "skipped"
            lr = report.longrepr
            if isinstance(lr, tuple) and len(lr) == 3:
                row["detail"] = str(lr[2]).removeprefix("Skipped: ")
            else:
                row["detail"] = str(lr or "")
        elif report.failed:
            if report.when != "call" and row["outcome"] == "passed":
                row["outcome"] = "error"
            elif report.when == "call":
                row["outcome"] = "failed"
            crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
            message = getattr(crash, "message", None) or str(report.longrepr or "").strip().splitlines()[-1:]
            if isinstance(message, list):
                message = message[0] if message else ""
            row["detail"] = _clean(str(message))

    def results(self) -> list[dict]:
        out = [self.rows[n] for n in self.order if n in self.rows]
        out += [r for n, r in self.rows.items() if n not in self.order]
        return out


def _clean(message: str) -> str:
    msg = message.strip()
    for prefix in ("AssertionError: ", "Failed: ", "assert "):
        if msg.startswith(prefix):
            msg = msg[len(prefix):]
    return msg.split("\nassert ")[0].strip()[:400]


@contextlib.contextmanager
def _env(extra: dict[str, str]):
    old = {k: os.environ.get(k) for k in extra}
    os.environ.update(extra)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


LABELS = {"passed": "PASS", "failed": "FAIL", "error": "ERROR", "skipped": "skip"}


def print_table(rows: list[dict], out=None, color: bool | None = None) -> None:
    out = out or sys.stdout
    if color is None:
        color = hasattr(out, "isatty") and out.isatty() and not os.environ.get("NO_COLOR")
    paint = {"passed": "32", "failed": "31", "error": "31", "skipped": "2"}
    sym = LABELS
    width = min(68, max([len(r["name"]) for r in rows] + [10]))
    for r in rows:
        tag = f"{sym[r['outcome']]:5}"
        if color:
            tag = f"\033[{paint[r['outcome']]}m{tag}\033[0m"
        name = r["name"] if len(r["name"]) <= width else r["name"][: width - 1] + "…"
        line = f"  {tag} {name:<{width}}"
        if r["outcome"] != "passed" and r["detail"]:
            line += f"  {r['detail']}"
        elif r["outcome"] == "passed":
            line += f"  {r['seconds']:.1f}s"
        print(line.rstrip(), file=out)
    counts = {k: sum(1 for r in rows if r["outcome"] == k) for k in ("passed", "failed", "error", "skipped")}
    print(f"\n  {counts['passed']} passed, {counts['failed'] + counts['error']} failed, {counts['skipped']} skipped",
          file=out)


def run_suite(live: bool = False, unit: bool = True, *, json_out: bool = False, keyword: str | None = None,
              exitfirst: bool = False, verbose: bool = False, extra: list[str] | None = None,
              env_extra: dict[str, str] | None = None, repo: Path = REPO_ROOT, out=None) -> int:
    """Run pytest in this process and report. Returns 0 when nothing failed."""
    out = out or sys.stdout
    tests = repo / "tests"
    if not tests.is_dir():
        print(f"orbital test: no tests folder at {tests} (run it from a git checkout)", file=sys.stderr)
        return 2
    try:
        import pytest
    except ImportError:
        print("orbital test: pytest isn't installed; run ./setup (or pip install -e '.[dev]')", file=sys.stderr)
        return 2
    args = ["--rootdir", str(repo), "-c", str(repo / "pyproject.toml"), "-p", "no:cacheprovider"]
    env = dict(env_extra or {})
    if live and unit:
        targets, marks = [str(tests)], ""
        env["ORBITAL_LIVE"] = "1"
    elif live:
        targets, marks = [str(tests / "live")], "live"
        env["ORBITAL_LIVE"] = "1"
    else:
        targets, marks = [str(tests)], "not live"
    if marks:
        args += ["-m", marks]
    if keyword:
        args += ["-k", keyword]
    if exitfirst:
        args.append("-x")
    table_mode = live and not unit
    quiet_pytest = json_out or (table_mode and not verbose)
    args += ["-q"] if not verbose else ["-v"]
    if quiet_pytest:
        args += ["--no-header", "-rN", "--tb=no"]
    args += list(extra or []) + targets
    collector = Collector()
    started = time.monotonic()
    buf = io.StringIO()
    with _env(env):
        if quiet_pytest:
            with contextlib.redirect_stdout(buf):
                rc = pytest.main(args, plugins=[collector])
        else:
            rc = pytest.main(args, plugins=[collector])
    rc = int(rc)
    rows = collector.results()
    failed = any(r["outcome"] in ("failed", "error") for r in rows)
    if rc == 5 and not failed:      # nothing collected
        rc = 0 if rows else 5
    if json_out:
        print(json.dumps({"ok": rc == 0, "exit_code": rc, "live": live, "seconds": round(time.monotonic() - started, 1),
                          "results": rows}, indent=2), file=out)
        return rc
    if table_mode:
        title = "Live checks" + (f"  ({os.environ.get('ORBITAL_HOME') or '~/.orbital-agents'})")
        print(f"\n  {title}", file=out)
        print_table(rows, out)
        if rc not in (0, 1) and not rows:
            print(buf.getvalue(), file=out)
    return rc


def run(args) -> int:
    live = bool(args.live or args.all)
    unit = not args.live or args.all
    return run_suite(live=live, unit=unit, json_out=args.json, keyword=args.keyword, exitfirst=args.exitfirst,
                     verbose=args.verbose, extra=args.pytest_args)
