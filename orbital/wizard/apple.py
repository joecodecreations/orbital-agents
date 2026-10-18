"""Apple build settings the wizard can find on its own: the Team ID (from the Apple Development
signing certificate's OU field) and connected iPhones (`xcrun devicectl`). Read-only."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass

from orbital.services.ops import default_exec

PEM_RE = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)
OU_RE = re.compile(r"\bOU\s*=\s*([A-Z0-9]{10})\b")
O_RE = re.compile(r"\bO\s*=\s*([^,/\n]+)")


@dataclass
class Team:
    team_id: str
    label: str = ""


def parse_subject(subject: str) -> Team | None:
    m = OU_RE.search(subject)
    if not m:
        return None
    o = O_RE.search(subject)
    return Team(m.group(1), (o.group(1).strip() if o else ""))


def detect_teams(exec_fn=default_exec) -> list[Team]:
    """Team IDs of every Apple Development certificate in the keychain (usually one)."""
    r = exec_fn(["security", "find-certificate", "-a", "-c", "Apple Development", "-p"], timeout=15)
    if not r.ok or "BEGIN CERTIFICATE" not in r.stdout:
        return []
    teams: list[Team] = []
    for pem in PEM_RE.findall(r.stdout):
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as fh:
            fh.write(pem + "\n")
            name = fh.name
        try:
            s = exec_fn(["openssl", "x509", "-in", name, "-noout", "-subject"], timeout=15)
        finally:
            os.unlink(name)
        team = parse_subject(s.stdout) if s.ok else None
        if team and team.team_id not in [t.team_id for t in teams]:
            teams.append(team)
    return teams


@dataclass
class Device:
    name: str
    udid: str
    platform: str
    model: str
    state: str
    connected: bool


def parse_devicectl(data: dict) -> list[Device]:
    out: list[Device] = []
    for d in (data.get("result") or {}).get("devices") or []:
        props = d.get("deviceProperties") or {}
        hw = d.get("hardwareProperties") or {}
        conn = d.get("connectionProperties") or {}
        plat = str(hw.get("platform") or "")
        if plat and plat.lower() not in ("ios", "ipados"):
            continue
        tunnel = str(conn.get("tunnelState") or "")
        paired = str(conn.get("pairingState") or "") == "paired"
        out.append(Device(
            name=str(props.get("name") or hw.get("marketingName") or "iPhone"),
            udid=str(hw.get("udid") or d.get("identifier") or ""),
            platform=plat or "iOS",
            model=str(hw.get("marketingName") or hw.get("productType") or ""),
            state=tunnel or "unknown",
            connected=paired and tunnel not in ("unavailable", ""),
        ))
    return out


def list_devices(exec_fn=default_exec) -> list[Device]:
    """iPhones/iPads Xcode can see. `connected` = reachable now (cable or same network)."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        r = exec_fn(["xcrun", "devicectl", "list", "devices", "--quiet", "--json-output", path], timeout=30)
        if not r.ok:
            return []
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        return parse_devicectl(data)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
