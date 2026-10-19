"""The data model and wire formats shared by the app and the CLI.

Items, subagents, notes and media refs are plain dicts with the Mac app's camelCase keys
(apps/tracker/Sources/OrbitalTrackerCore/Models.swift). They are written the way Swift's
JSONEncoder writes them: sorted keys, `"key" : value` when pretty, ISO 8601 dates without
fractional seconds, unescaped slashes, and nil optionals left out.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

# MARK: ids

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")


def make_id(length: int = 8) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def valid_id(value: Any) -> bool:
    """Ids from the CLI, the HTTP API and the hub relay: 2-64 of [A-Za-z0-9_-]."""
    return isinstance(value, str) and bool(_ID_RE.match(value))


# MARK: dates


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    """Swift's ISO8601DateFormatter: 2026-01-02T03:04:05Z (UTC, whole seconds)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return iso(now_utc())


def parse_date(value: Any) -> datetime | None:
    """ISO 8601 with or without fractional seconds, or Unix seconds / milliseconds."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(seconds, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None  # Swift's formatter needs a zone
    return dt


def norm_date(value: Any) -> str | None:
    dt = parse_date(value)
    return iso(dt) if dt else None


# MARK: JSON


def _scalar(v: Any) -> str:
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))  # Swift writes 3.0 as 3
    return json.dumps(v, ensure_ascii=False)


def _encode(obj: Any, level: int, pretty: bool) -> str:
    if isinstance(obj, dict):
        keys = sorted(obj)
        if not pretty:
            return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _encode(obj[k], 0, False) for k in keys) + "}"
        pad, inner = "  " * level, "  " * (level + 1)
        if not keys:
            return "{\n\n" + pad + "}"
        return "{\n" + ",\n".join(inner + json.dumps(k, ensure_ascii=False) + " : " + _encode(obj[k], level + 1, True)
                                   for k in keys) + "\n" + pad + "}"
    if isinstance(obj, (list, tuple)):
        if not pretty:
            return "[" + ",".join(_encode(v, 0, False) for v in obj) + "]"
        pad, inner = "  " * level, "  " * (level + 1)
        if not obj:
            return "[\n\n" + pad + "]"  # Swift's prettyPrinted style for an empty array
        return "[\n" + ",\n".join(inner + _encode(v, level + 1, True) for v in obj) + "\n" + pad + "]"
    return _scalar(obj)


def dumps(obj: Any, pretty: bool = False) -> str:
    """Swift's JSONEncoder(.sortedKeys, .withoutEscapingSlashes[, .prettyPrinted]), byte for byte:
    `"key" : value`, two-space indents, empty arrays as `[\n\n  ]`, integral doubles without `.0`."""
    return _encode(obj, 0, pretty)


def write_atomic(path: Path, text: str) -> None:
    """Write to a temp file in the same folder, then rename over the target (atomic on Windows too)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # Windows: a reader (the hub) may hold the file open for a moment.
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


# MARK: agents


KNOWN_AGENTS = ("claude", "chatgpt", "grok", "gemini")


def normalize_agent(raw: Any) -> str:
    """Whatever an agent calls itself → the providers the UI draws icons for (others kept as given)."""
    s = (raw if isinstance(raw, str) else "").strip().lower()
    if not s:
        return "other"
    if "claude" in s or "anthropic" in s:
        return "claude"
    if "chatgpt" in s or "openai" in s or "codex" in s or s.startswith("gpt"):
        return "chatgpt"
    if "grok" in s or s == "xai" or "x.ai" in s:
        return "grok"
    if "gemini" in s or "google" in s or "antigravity" in s or s == "agy" or "bard" in s:
        return "gemini"
    return s


def agent_display_name(agent: Any) -> str:
    n = normalize_agent(agent)
    names = {"claude": "Claude", "chatgpt": "ChatGPT", "grok": "Grok", "gemini": "Gemini", "other": "AI agent"}
    if n in names:
        return names[n]
    # Swift's String.capitalized: first letter of each word upper, the rest lower.
    return " ".join(w[:1].upper() + w[1:].lower() for w in str(agent or "AI").split(" "))


def detect_agent(env: dict[str, str] | None = None) -> str | None:
    """Best guess from the environment the CLI runs in; used only when --agent is missing."""
    keys = set((os.environ if env is None else env).keys())
    if "CLAUDECODE" in keys or "CLAUDE_CODE_ENTRYPOINT" in keys:
        return "claude"
    if any(k.startswith("CODEX_") for k in keys):
        return "chatgpt"
    if any(k.startswith("ANTIGRAVITY") or k.startswith("GEMINI_CLI") for k in keys):
        return "gemini"
    if any(k.startswith("GROK_") and "API_KEY" not in k for k in keys):
        return "grok"
    return None


# MARK: evidence level (settings.json)

EVIDENCE_LEVELS = ("off", "min1", "all")
_EVIDENCE_ALIASES = {
    "off": "off", "none": "off", "0": "off", "no": "off", "false": "off", "normal": "off",
    "min1": "min1", "min": "min1", "1": "min1", "one": "min1", "minimum": "min1", "atleast1": "min1", "atleastone": "min1",
    "all": "all", "every": "all", "everything": "all", "full": "all", "max": "all",
}
EVIDENCE_LABELS = {"off": "Off", "min1": "Min 1", "all": "All"}
EVIDENCE_BLURBS = {
    "off": "Agents attach screenshots when they normally would",
    "min1": "At least 1 screenshot for every UI, website or game change",
    "all": "Every feature screenshotted (and filmed for games) for phone review",
}
# Word for word what the Mac CLI prints (OrbitalTrackerCore/Settings.swift, agentInstruction).
EVIDENCE_INSTRUCTIONS = {
    "off": "Screenshot evidence: OFF. Nothing is required. Attach screenshots or recordings with --media when you make them anyway.",
    "min1": (
        "Screenshot evidence: MIN 1 (the user turned this on).\n"
        "If this task changes anything visual (an app or iOS screen, a website or page, a game, a map, a render, a chart), "
        "attach at least ONE screenshot of the finished result before you report done:\n"
        "  orbital-track update <ID> --media /abs/after.png    (or --media on `done`)\n"
        "Games: when you test a new feature where seeing the action matters (movement, combat, animation, physics, effects), "
        "also attach a short video of it (--media /abs/clip.mp4).\n"
        "Non-visual work (backend, data, scripts, docs) needs nothing."
    ),
    "all": (
        "Screenshot evidence: ALL (the user turned this on and reviews from the Orbital Agents app, away from the Mac).\n"
        "If this task changes anything visual (an app or iOS screen, a website or page, a game, a map, a render, a chart):\n"
        "  - Screenshot EVERY feature, screen, page and state you added or changed (before/after when it helps).\n"
        "  - Games: a short video of every new or changed feature that moves or plays (movement, combat, animation, "
        "physics, effects, UI flow).\n"
        "  - Attach each as you go: orbital-track update <ID> --note \"<what it shows>\" --media /abs/shot.png\n"
        "  - In the done summary, list each feature next to its picture: ![Settings sheet, dark mode](/abs/shot.png)\n"
        "Non-visual work (backend, data, scripts, docs) needs nothing."
    ),
}


def evidence_level(raw: Any) -> str | None:
    """'Min 1', 'min1', '1', 'ALL' → the stored level; anything else → None."""
    if not isinstance(raw, str):
        return None
    return _EVIDENCE_ALIASES.get("".join(ch for ch in raw.lower() if ch.isalnum()))


def normalize_writer(raw: Any) -> str:
    """'tracker' and 'cli' stay; everything else is the companion app."""
    s = (raw if isinstance(raw, str) else "").strip().lower()
    return s if s in ("tracker", "cli") else "app"


class Settings:
    """settings.json: {"evidence", "updatedAt", "updatedBy"}. Unknown levels read as off."""

    def __init__(self, evidence: str = "off", updated_at: str | None = None, updated_by: str | None = None) -> None:
        self.evidence = evidence if evidence in EVIDENCE_LEVELS else "off"
        self.updated_at = updated_at
        self.updated_by = normalize_writer(updated_by) if updated_by is not None else None

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Settings) and self.file_dict() == other.file_dict()

    @classmethod
    def from_dict(cls, obj: Any) -> "Settings":
        if not isinstance(obj, dict):
            return cls()
        return cls(evidence_level(obj.get("evidence")) or "off", norm_date(obj.get("updatedAt")),
                   obj.get("updatedBy") if isinstance(obj.get("updatedBy"), str) else None)

    @classmethod
    def load(cls, file: Path) -> "Settings":
        try:
            return cls.from_dict(json.loads(file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return cls()

    @classmethod
    def from_reply(cls, body: bytes) -> "Settings | None":
        """The `settings` object (or a bare settings object) of an HTTP reply."""
        try:
            obj = json.loads(body)
        except ValueError:
            return None
        if not isinstance(obj, dict):
            return None
        node = obj.get("settings") if isinstance(obj.get("settings"), dict) else obj
        level = evidence_level(node.get("evidence"))
        if level is None:
            return None
        return cls(level, norm_date(node.get("updatedAt")), node.get("updatedBy") if isinstance(node.get("updatedBy"), str) else None)

    def file_dict(self) -> dict:
        out: dict[str, Any] = {"evidence": self.evidence}
        if self.updated_at:
            out["updatedAt"] = self.updated_at
        if self.updated_by:
            out["updatedBy"] = self.updated_by
        return out

    def save(self, file: Path) -> None:
        write_atomic(file, dumps(self.file_dict(), pretty=True))

    def json(self) -> dict:
        """What GET/POST /v1/settings and `orbital-track evidence --json` return."""
        out = {"evidence": self.evidence, "label": EVIDENCE_LABELS[self.evidence], "blurb": EVIDENCE_BLURBS[self.evidence]}
        out.update({k: v for k, v in self.file_dict().items() if k != "evidence"})
        return out

    @staticmethod
    def newest(a: "Settings", b: "Settings | None") -> "Settings":
        """Of two copies (this machine and the hub), the one changed last."""
        if b is None:
            return a
        da, db = parse_date(a.updated_at), parse_date(b.updated_at)
        if da and db:
            return b if db > da else a
        if da is None and db is not None:
            return b
        return a


# MARK: commands

_OPS = {"start": "start", "add": "start", "sub": "sub", "update": "update", "note": "update",
        "done": "done", "complete": "done", "finish": "done"}
COMMAND_KEYS = ("op", "id", "parent", "agent", "agentLabel", "machine", "title", "details", "appendDetails",
                "note", "summary", "workspace", "media", "progress", "usage", "sentAt")


class CommandError(ValueError):
    pass


def canonical_op(raw: Any) -> str | None:
    return _OPS.get(raw.lower()) if isinstance(raw, str) else None


def _opt_str(obj: dict, key: str) -> str | None:
    v = obj.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise CommandError(f"{key} must be a string")
    return v


def _media_ref(obj: Any) -> dict | None:
    """A MediaRef from the wire; None when it isn't one."""
    if not isinstance(obj, dict) or not isinstance(obj.get("id"), str) or not isinstance(obj.get("originalPath"), str):
        return None
    kind = obj.get("kind") if obj.get("kind") in ("image", "video", "file") else None
    added = norm_date(obj.get("addedAt"))
    if kind is None or added is None:
        return None
    ref: dict[str, Any] = {"id": obj["id"], "kind": kind, "originalPath": obj["originalPath"], "addedAt": added}
    for key in ("archivePath", "caption", "machine"):
        if isinstance(obj.get(key), str):
            ref[key] = obj[key]
    if isinstance(obj.get("originalBytes"), int) and not isinstance(obj.get("originalBytes"), bool):
        ref["originalBytes"] = obj["originalBytes"]
    if isinstance(obj.get("durationSeconds"), (int, float)) and not isinstance(obj.get("durationSeconds"), bool):
        ref["durationSeconds"] = float(obj["durationSeconds"])
    return ref


def parse_command(obj: Any) -> dict:
    """A TrackerCommand from JSON (HTTP, inbox file, hub relay). Raises CommandError like the
    Swift decoder would (a missing op, wrong types)."""
    if not isinstance(obj, dict):
        raise CommandError("the body must be a JSON object")
    if not isinstance(obj.get("op"), str):
        raise CommandError("op is required")
    cmd: dict[str, Any] = {"op": obj["op"]}
    for key in ("id", "parent", "agent", "agentLabel", "machine", "title", "details", "note", "summary", "workspace"):
        v = _opt_str(obj, key)
        if v is not None:
            cmd[key] = v
    if obj.get("appendDetails") is not None:
        if not isinstance(obj["appendDetails"], bool):
            raise CommandError("appendDetails must be true or false")
        cmd["appendDetails"] = obj["appendDetails"]
    if obj.get("progress") is not None:
        p = obj["progress"]
        if isinstance(p, bool) or not isinstance(p, (int, float)):
            raise CommandError("progress must be a number from 0 to 100")
        cmd["progress"] = p
    if obj.get("usage") is not None:
        if not isinstance(obj["usage"], dict):
            raise CommandError("usage must be a JSON object")
        cmd["usage"] = obj["usage"]
    if obj.get("sentAt") is not None:
        sent = norm_date(obj["sentAt"])
        if sent is None:
            raise CommandError(f"Bad date: {obj['sentAt']}")
        cmd["sentAt"] = sent
    if obj.get("media") is not None:
        if not isinstance(obj["media"], list):
            raise CommandError("media must be a list")
        media = []
        for m in obj["media"]:
            if not isinstance(m, dict) or not isinstance(m.get("path"), str):
                raise CommandError("each media entry needs a path")
            entry: dict[str, Any] = {"path": m["path"]}
            if isinstance(m.get("caption"), str):
                entry["caption"] = m["caption"]
            ref = _media_ref(m.get("archived"))
            if ref is not None:
                entry["archived"] = ref
            if isinstance(m.get("inlineArchive"), str):
                entry["inlineArchive"] = m["inlineArchive"]
            media.append(entry)
        cmd["media"] = media
    return cmd


def validate_command(cmd: dict) -> None:
    """Ids and parents must be plain tokens: they end up in archive file names."""
    if canonical_op(cmd.get("op")) is None:
        raise CommandError(f"unknown op '{cmd.get('op')}' (use start, sub, update, done)")
    for name in ("id", "parent"):
        if cmd.get(name) is not None and not valid_id(cmd[name]):
            raise CommandError(f"{name} must be 2-64 letters, digits, '-' or '_'")


def command_from_route(route: str, path_id: str | None, body: bytes) -> dict:
    """The friendly curl form: POST /v1/<route>[/<id>]; the op comes from the path.
    POST /v1/sub/<parent> sets the parent; the others set the id. start/sub get an id when none is given."""
    obj: dict = {}
    if body:
        try:
            parsed = json.loads(body)
        except ValueError as exc:
            raise CommandError(f"bad JSON: {exc}") from None
        if not isinstance(parsed, dict):
            raise CommandError("the body must be a JSON object")
        obj = parsed
    obj["op"] = route
    if path_id:
        obj["parent" if route == "sub" else "id"] = path_id
    if route in ("start", "sub", "add") and obj.get("id") is None:
        obj["id"] = make_id()
    return parse_command(obj)


# MARK: inbox files

_inbox_lock = threading.Lock()
_inbox_last = 0
_inbox_seq = 0


def inbox_name() -> str:
    """`<unix microseconds, 16 digits>-<sequence, 4>-<random 4>.json`: sorts in write order."""
    global _inbox_last, _inbox_seq
    with _inbox_lock:
        micros = int(time.time() * 1_000_000)
        if micros <= _inbox_last:
            micros = _inbox_last + 1
        _inbox_last = micros
        _inbox_seq = (_inbox_seq + 1) % 10_000
        return f"{micros:016d}-{_inbox_seq:04d}-{make_id(4)}.json"


def inbox_write(data: bytes, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    name = inbox_name()
    tmp = folder / f".{name}.tmp"
    tmp.write_bytes(data)
    final = folder / name
    os.replace(tmp, final)
    return final


def _sort_key(name: str) -> float:
    digits = ""
    for ch in name:
        if not ch.isdigit():
            break
        digits += ch
    if not digits:
        return 0
    n = float(digits)
    return n * 1000 if n < 1e14 else n


def inbox_pending(folder: Path) -> list[Path]:
    """Queued command files, oldest first (13-digit ms and 16-digit µs names on one scale)."""
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".json") and not n.startswith(".")]
    except OSError:
        return []
    names.sort(key=lambda n: (_sort_key(n), n))
    return [folder / n for n in names]


# MARK: markdown images

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def local_path(raw: str) -> str | None:
    """An image source as a normalized local path (None for web URLs and relative paths)."""
    if raw.startswith("file://"):
        p = urlparse(raw)
        path = unquote(p.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        return os.path.normpath(path)
    if raw.startswith("/") or raw.startswith("~") or re.match(r"^[A-Za-z]:[\\/]", raw) or raw.startswith("\\\\"):
        return os.path.normpath(os.path.expanduser(raw))
    return None


def markdown_paths(markdown: str | None) -> list[str]:
    """Local files referenced as markdown images: ![alt](/path/to/shot.png)."""
    if not markdown:
        return []
    return [p for p in (local_path(m.group(1)) for m in _IMAGE_RE.finditer(markdown)) if p]


def normalize_path(raw: str, cwd: str | None = None) -> str:
    s = raw.strip()
    if s.startswith("file://"):
        s = local_path(s) or s
    s = os.path.expanduser(s)
    if not os.path.isabs(s):
        s = os.path.join(cwd or os.getcwd(), s)
    return os.path.normpath(s)


# MARK: items as stored in items.json


def _str(v: Any, default: str) -> str:
    return v if isinstance(v, str) else default


def _opt(out: dict, src: dict, key: str) -> None:
    if isinstance(src.get(key), str):
        out[key] = src[key]


def clamp_progress(v: Any) -> int | None:
    """A percent 0-100 from the wire or items.json; None when it isn't a number."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    return max(0, min(100, int(_round_half_up(v))))


def _round_half_up(x: float) -> float:
    """Swift's .rounded() (Python's round() is banker's rounding)."""
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def _opt_progress(out: dict, src: dict) -> None:
    p = clamp_progress(src.get("progress"))
    if p is not None:
        out["progress"] = p
    if isinstance(src.get("usage"), dict):
        out["usage"] = src["usage"]


# MARK: the progress bar (same rule as ShownProgress in Models.swift and the iPhone app)

_CHECKBOX_RE = re.compile(r"^[ \t]*[-*+] +\[([ xX])\]")
PROGRESS_CAPTIONS = {
    "done": "done",
    "reported": "reported by the agent",
    "checklist": "estimated from the task list",
    "subagents": "estimated from subagents",
}


def checklist(markdown: str | None) -> tuple[int, int] | None:
    """(checked, total) markdown task boxes: `- [ ]`, `* [x]`, `+ [X]`; None when there are none."""
    checked = total = 0
    for line in (markdown or "").split("\n"):
        m = _CHECKBOX_RE.match(line)
        if m:
            total += 1
            checked += m.group(1) != " "
    return (checked, total) if total else None


def shown_progress(node: dict, subagents: list[dict] | None = None) -> dict | None:
    """What the bar shows for an item (pass its subagents) or a subagent (pass none):
    done → 100; else the agent's reported figure; else checked task boxes in the details; else the
    subagents' average. Estimates stop at 99 until the work is done and show as "~40%".
    Returns {"percent", "source", "estimate", "label", "caption"} or None."""
    if node.get("status") == "done":
        percent, source = 100, "done"
    elif clamp_progress(node.get("progress")) is not None:
        percent, source = clamp_progress(node.get("progress")), "reported"
    elif (boxes := checklist(node.get("details"))) is not None:
        percent, source = min(99, int(_round_half_up(boxes[0] / boxes[1] * 100))), "checklist"
    elif subagents:
        total = sum(100 if s.get("status") == "done" else (clamp_progress(s.get("progress")) or 0) for s in subagents)
        percent, source = min(99, int(_round_half_up(total / len(subagents)))), "subagents"
    else:
        return None
    estimate = source in ("checklist", "subagents")
    return {"percent": percent, "source": source, "estimate": estimate,
            "label": ("~" if estimate else "") + f"{percent}%", "caption": PROGRESS_CAPTIONS[source]}


def normalize_note(n: Any) -> dict | None:
    if not isinstance(n, dict) or not isinstance(n.get("text"), str):
        return None
    return {"at": norm_date(n.get("at")) or now_iso(), "text": n["text"]}


def normalize_sub(s: Any) -> dict | None:
    """A SubTask read the way Swift's tolerant decoder reads it (missing fields → defaults)."""
    if not isinstance(s, dict) or not isinstance(s.get("id"), str):
        return None
    created = norm_date(s.get("createdAt")) or now_iso()
    out: dict[str, Any] = {
        "id": s["id"],
        "agent": _str(s.get("agent"), "other"),
        "title": _str(s.get("title"), "Subtask"),
        "details": _str(s.get("details"), ""),
        "status": s.get("status") if s.get("status") in ("active", "done") else "active",
        "createdAt": created,
        "updatedAt": norm_date(s.get("updatedAt")) or created,
        "notes": [n for n in map(normalize_note, s.get("notes") or []) if n],
        "media": [m for m in map(_media_ref, s.get("media") or []) if m],
    }
    for key in ("agentLabel", "machine", "summary"):
        _opt(out, s, key)
    _opt_progress(out, s)
    if norm_date(s.get("completedAt")):
        out["completedAt"] = norm_date(s.get("completedAt"))
    return out


def normalize_item(i: Any) -> dict | None:
    """A TrackedItem read the way Swift's tolerant decoder reads it."""
    if not isinstance(i, dict) or not isinstance(i.get("id"), str):
        return None
    created = norm_date(i.get("createdAt")) or now_iso()
    color = i.get("colorIndex")
    out: dict[str, Any] = {
        "id": i["id"],
        "title": _str(i.get("title"), "Untitled"),
        "details": _str(i.get("details"), ""),
        "source": i.get("source") if i.get("source") in ("voice", "manual", "agent") else "manual",
        "colorIndex": color if isinstance(color, int) and not isinstance(color, bool) else 0,
        "status": i.get("status") if i.get("status") in ("active", "done") else "active",
        "createdAt": created,
        "updatedAt": norm_date(i.get("updatedAt")) or created,
        "dismissed": i.get("dismissed") is True,
        "processing": i.get("processing") is True,
        "notes": [n for n in map(normalize_note, i.get("notes") or []) if n],
        "media": [m for m in map(_media_ref, i.get("media") or []) if m],
        "subagents": [s for s in map(normalize_sub, i.get("subagents") or []) if s],
    }
    for key in ("transcript", "agent", "agentLabel", "machine", "workspace", "completedBy", "summary"):
        _opt(out, i, key)
    _opt_progress(out, i)
    if norm_date(i.get("completedAt")):
        out["completedAt"] = norm_date(i.get("completedAt"))
    return out


def finished_at(item: dict) -> str:
    return item.get("completedAt") or item.get("updatedAt") or item.get("createdAt") or ""
