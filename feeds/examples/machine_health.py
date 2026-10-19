"""Machine health: CPU load, memory, disks and uptime of the machine this runs on.

Shows: metrics tiles, intraday line/area charts kept across runs, a disk table, key/value facts, a
daily `series` (kind health) for the app's dashboard charts, a callout and an alert when a disk is
almost full. Filed under System (space "system"), where the apps show it next to the machines. No API keys; macOS and Linux.

    python feeds/examples/machine_health.py [--dry-run] [--section-id machine-health] [--disk-alert-pct 10]
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import feedkit  # noqa: E402

from orbital.feeds import blocks as b  # noqa: E402

FEED = "machine-health"
KEEP_SAMPLES = 288      # 24 h at one sample every 5 minutes
KEEP_DAYS = 30
GB = 1_000_000_000


# --------------------------------------------------------------------------- gather


def _run(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def memory() -> dict | None:
    """{"total", "used", "percent"} in bytes, or None when this OS isn't supported."""
    if sys.platform == "darwin":
        total = int((_run(["sysctl", "-n", "hw.memsize"]).strip() or "0"))
        text = _run(["vm_stat"])
        size = re.search(r"page size of (\d+) bytes", text)
        page = int(size.group(1)) if size else 4096

        def pages(label: str) -> int:
            found = re.search(rf"{label}:\s+(\d+)", text)
            return int(found.group(1)) if found else 0

        used = (pages("Pages active") + pages("Pages wired down") + pages("Pages occupied by compressor")) * page
        if not total:
            return None
        return {"total": total, "used": min(used, total), "percent": round(100 * used / total, 1)}
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        values = {}
        for line in meminfo.read_text().splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0]) * 1024
        total, available = values.get("MemTotal"), values.get("MemAvailable")
        if total and available is not None:
            used = total - available
            return {"total": total, "used": used, "percent": round(100 * used / total, 1)}
    return None


def uptime_seconds() -> float | None:
    if sys.platform == "darwin":
        found = re.search(r"sec = (\d+)", _run(["sysctl", "-n", "kern.boottime"]))
        return time.time() - int(found.group(1)) if found else None
    proc = Path("/proc/uptime")
    if proc.is_file():
        return float(proc.read_text().split()[0])
    return None


def disks(paths: list[str]) -> list[dict]:
    out, seen = [], set()
    for raw in paths:
        path = os.path.expanduser(raw)
        try:
            dev = os.stat(path).st_dev
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        if dev in seen:
            continue
        seen.add(dev)
        out.append({"mount": raw, "total": usage.total, "used": usage.used, "free": usage.free,
                    "percent": round(100 * usage.used / usage.total, 1) if usage.total else 0.0})
    return out


def gather(machine: str, disk_paths: list[str]) -> dict:
    load1, load5, load15 = os.getloadavg()
    return {
        "machine": machine,
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "cpus": os.cpu_count() or 1,
        "load": [round(load1, 2), round(load5, 2), round(load15, 2)],
        "memory": memory(),
        "disks": disks(disk_paths),
        "uptime_s": uptime_seconds(),
        "time": time.time(),
    }


# --------------------------------------------------------------------------- build


def remember(state: dict, sample: dict) -> dict:
    """Add one sample to the rolling intraday history and the per-day totals."""
    mem = (sample.get("memory") or {}).get("percent")
    root = (sample.get("disks") or [{}])[0].get("percent")
    history = state.setdefault("samples", [])
    history.append({"t": sample["time"], "load": sample["load"][0], "mem": mem, "disk": root})
    del history[:-KEEP_SAMPLES]
    day = time.strftime("%Y-%m-%d", time.localtime(sample["time"]))
    daily = state.setdefault("daily", {})
    row = daily.setdefault(day, {"n": 0, "load": 0.0, "mem": 0.0, "mem_n": 0, "disk": 0.0})
    row["n"] += 1
    row["load"] += sample["load"][0]
    if mem is not None:
        row["mem"] += mem
        row["mem_n"] += 1
    if root is not None:
        row["disk"] = max(row["disk"], root)
    for old in sorted(daily)[:-KEEP_DAYS]:
        del daily[old]
    return state


def _mount_id(mount: str) -> str:
    ident = b.slug(mount, 30)
    return "root" if ident == "item" else ident


def _gb(n: float) -> str:
    return f"{n / GB:,.0f} GB" if n >= 10 * GB else f"{n / GB:,.1f} GB"


def build(sample: dict, state: dict, *, section_id: str = FEED, disk_alert_pct: float = 10.0) -> tuple[dict, list[dict]]:
    machine = sample["machine"]
    load1 = sample["load"][0]
    mem = sample.get("memory")
    up = sample.get("uptime_s")
    disks_now = sample.get("disks") or []
    tiles = [
        b.metric("Load (1 min)", f"{load1:.2f}", id="load", caption=f"{sample['cpus']} cores · 5 min {sample['load'][1]:.2f}"),
    ]
    if mem:
        tiles.append(b.metric("Memory used", f"{mem['percent']:.0f}%", id="memory",
                              caption=f"{_gb(mem['used'])} of {_gb(mem['total'])}"))
    if disks_now:
        root = disks_now[0]
        tiles.append(b.metric("Disk free", _gb(root["free"]), id="disk", caption=f"of {_gb(root['total'])} on {root['mount']}"))
    if up is not None:
        tiles.append(b.metric("Uptime", feedkit.ago(up), id="uptime"))

    samples = state.get("samples") or []
    load_points = [(feedkit.local_time(s["t"]), s["load"]) for s in samples]
    mem_points = [(feedkit.local_time(s["t"]), round(s["mem"] / 100, 4)) for s in samples if s.get("mem") is not None]
    daily = state.get("daily") or {}
    series_metrics = [b.series_metric("load", "Load (daily average)", [(d, round(r["load"] / r["n"], 2)) for d, r in sorted(daily.items()) if r["n"]])]
    mem_daily = [(d, round(r["mem"] / r["mem_n"], 1)) for d, r in sorted(daily.items()) if r.get("mem_n")]
    if mem_daily:
        series_metrics.append(b.series_metric("memory", "Memory used % (daily average)", mem_daily))
    disk_daily = [(d, r["disk"]) for d, r in sorted(daily.items()) if r.get("disk")]
    if disk_daily:
        series_metrics.append(b.series_metric("disk", "Disk used % (daily peak)", disk_daily))

    page: list[dict] = []
    alerts: list[dict] = []
    for disk in disks_now:
        free_pct = 100 - disk["percent"]
        if free_pct < disk_alert_pct:
            page.append(b.callout(f"disk-low-{_mount_id(disk['mount'])}",
                                  f"{disk['mount']} has {free_pct:.0f}% free ({_gb(disk['free'])} of {_gb(disk['total'])}).",
                                  tone="warning", title="Disk almost full"))
            day = time.strftime("%Y%m%d", time.localtime(sample["time"]))
            alerts.append({"id": f"disk-low-{b.slug(machine, 30)}-{_mount_id(disk['mount'])}-{day}",
                           "title": f"Disk almost full on {machine}",
                           "body": f"{disk['mount']} has {free_pct:.0f}% free ({_gb(disk['free'])} left).",
                           "level": "warning"})
    page += [
        b.metrics("now", tiles),
        b.chart("load-24h", b.chart_series("Load (1 min)", load_points), kind="line", title="Load, last 24 hours",
                caption=f"One point per run; {sample['cpus']} cores"),
    ]
    if mem_points:
        page.append(b.chart("memory-24h", b.chart_series("Memory used", mem_points), kind="area",
                            title="Memory, last 24 hours", y_format="percent"))
    if disks_now:
        page.append(b.table("disks", ["Disk", "Used", "Free", "Size"],
                            [[d["mount"], f"{d['percent']:.0f}%", _gb(d["free"]), _gb(d["total"])] for d in disks_now],
                            title="Disks"))
    page.append(b.key_value("machine", [("Machine", machine), ("System", sample["os"]), ("CPU cores", sample["cpus"]),
                                        ("Python", sample["python"]),
                                        ("Checked", feedkit.local_time(sample["time"], "%Y-%m-%d %H:%M"))],
                            title="This machine"))
    page.append(b.series("health-daily", series_metrics, kind="health", company="Home Lab", product=machine,
                         title=f"{machine}, daily", window=30))
    summary = f"Load {load1:.2f}" + (f" · memory {mem['percent']:.0f}%" if mem else "") + \
              (f" · {_gb(disks_now[0]['free'])} free" if disks_now else "")
    section = b.section(section_id, f"{machine} health", page, icon="desktopcomputer", order=10,
                        subtitle="CPU, memory, disks and uptime", summary=summary[:80],
                        space="system", space_title="System", space_icon="cpu")
    return section, alerts


# --------------------------------------------------------------------------- run


def main(argv: list[str] | None = None, client=None) -> int:
    parser = feedkit.arg_parser(__doc__.splitlines()[0])
    parser.add_argument("--section-id", default=FEED)
    parser.add_argument("--disk", action="append", help="a folder whose disk to show (default / and ORBITAL_HOME)")
    parser.add_argument("--disk-alert-pct", type=float, default=10.0, help="alert when a disk has less free space (percent)")
    args = parser.parse_args(argv)
    cfg = feedkit.config(args.home)
    machine = cfg.machine_name or socket.gethostname().split(".")[0]
    sample = gather(machine, args.disk or ["/", str(cfg.paths.home)])
    state = remember(feedkit.load_state(FEED, args.home), sample)
    section, alerts = build(sample, state, section_id=args.section_id, disk_alert_pct=args.disk_alert_pct)
    result = feedkit.push(section, alerts, dry_run=args.dry_run, client=client, home=args.home)
    if not args.dry_run:
        feedkit.save_state(FEED, state, args.home)
    mem = (sample.get("memory") or {}).get("percent")
    feedkit.result_line(FEED, pushed=1 if not args.dry_run else 0, machine=b.slug(machine), load=sample["load"][0],
                        memory=f"{mem:.0f}%" if mem is not None else "n/a", alerts=result["alerts"],
                        revision=result["revision"])
    return 0


if __name__ == "__main__":
    sys.exit(feedkit.run_feed(main))
