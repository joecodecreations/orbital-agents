"""Nothing private ships: no keys, tokens, home paths, tailnet names or personal data in tracked files.

Generic patterns live here. Personal words to block (your name, your companies, your hostnames)
go in `.public-safety.local` at the repo root, one per line; that file is git-ignored.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".icns", ".ico", ".pdf", ".zip", ".mp4", ".mov", ".wav"}
SKIP_DIRS = {".git", ".venv", ".build", "build", "node_modules", "__pycache__", ".pytest_cache", "DerivedData"}

PATTERNS = {
    "private key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    "AWS key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "OpenAI/Anthropic/xAI key": re.compile(r"\b(sk-[A-Za-z0-9_\-]{20,}|sk-ant-[A-Za-z0-9_\-]{20,}|xai-[A-Za-z0-9]{20,})"),
    "GitHub token": re.compile(r"\b(ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}"),
    "Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "Stripe key": re.compile(r"\b(sk|rk)_live_[A-Za-z0-9]{10,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "home directory path": re.compile(r"/Users/(?!you\b|yourname\b|me\b|example\b|runner\b|Shared\b)[A-Za-z0-9._-]+/"),
    "tailnet host name": re.compile(r"\b[a-z0-9-]+\.tail[0-9a-f]{4,}\.ts\.net\b"),
    "Apple team id assignment": re.compile(r"(DEVELOPMENT_TEAM|team_id|TEAM_ID)\s*[=:]\s*\"?[A-Z0-9]{10}\b"),
}


def _build_output(rel: Path) -> bool:
    """Generated files .gitignore keeps out of git (used when this copy isn't a git checkout, e.g. a zip):
    the app's .derivedData*, its generated .xcodeproj and entitlements, .swiftpm, egg-info, local settings."""
    parts = rel.parts
    return (any(p.startswith(".derivedData") or p.endswith((".xcodeproj", ".egg-info")) or p in {".swiftpm", "xcuserdata", "dist"}
                for p in parts)
            or rel.name in {"OrbitalAgents.entitlements", ".DS_Store", "secrets.env", "config.toml"}
            or rel.suffix in {".log", ".p8"})


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
        files = [ROOT / f for f in out]
    except (OSError, subprocess.CalledProcessError):
        files = [p for p in ROOT.rglob("*") if p.is_file() and not _build_output(p.relative_to(ROOT))]
    return [f for f in files
            if f.is_file() and f.suffix.lower() not in SKIP_SUFFIXES
            and not any(part in SKIP_DIRS for part in f.relative_to(ROOT).parts)]


def local_terms() -> list[str]:
    f = ROOT / ".public-safety.local"
    if not f.is_file():
        return []
    return [t.strip() for t in f.read_text(encoding="utf-8").splitlines() if t.strip() and not t.startswith("#")]


def test_no_secrets_or_private_data():
    terms = [(t, re.compile(rf"(?i)\b{re.escape(t)}\b")) for t in local_terms()]
    problems: list[str] = []
    for f in tracked_files():
        if f.name in {"test_public_safety.py", ".public-safety.local"}:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pat in PATTERNS.items():
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{f.relative_to(ROOT)}:{line}: {label}")
        for term, pat in terms:
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{f.relative_to(ROOT)}:{line}: private term ({len(term)} chars)")
    if problems:
        pytest.fail("Private data in tracked files:\n" + "\n".join(sorted(set(problems))[:200]))
