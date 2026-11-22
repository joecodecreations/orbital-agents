"""Fast, read-only lookups the desk answers from itself, instead of starting an agent per question.

What the hub holds:
  status()             the live state: agents running and their findings, machines, alerts, automations
  search(query)        pages (sections), desk task reports and findings, tracker items, alerts
  page(name)           one page as plain text
  task(task_id)        a desk task's answer, findings, notes and full report

Files on this machine (the hub), never written:
  find_files(query, project)   Spotlight (contents and names) under the read roots; with only a project,
                               the files changed most recently there
  list_folder(path)
  read_file(path, query, offset)   text, Markdown, code, JSON, CSV; PDF (pdftotext); Word, RTF, HTML (textutil)

The read roots are desk.read_roots (default ~/projects, ~/Documents, ~/Desktop, ~/Downloads) plus every
[[desk.projects]] path. Hidden folders, ~/Library and secret-looking files (.env, keys, certificates,
keychains, anything named secret or credential) are refused. Every answer is plain text sized for a
model's context.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

DEFAULT_READ_ROOTS = ("~/projects", "~/Documents", "~/Desktop", "~/Downloads")
MAX_OUT = 7000
MAX_READ = 9000
MAX_FILE_BYTES = 25 * 1024 * 1024
TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".log", ".py", ".swift", ".js", ".ts", ".tsx", ".jsx", ".gd", ".lua", ".luau", ".sh", ".zsh", ".rb", ".go",
    ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".m", ".css", ".scss", ".sql", ".xml", ".svg", ".tex", ".srt", ".vtt",
}
TEXTUTIL_SUFFIXES = {".docx", ".doc", ".rtf", ".rtfd", ".odt", ".html", ".htm", ".webarchive", ".wordml"}
SECRET_NAME = re.compile(
    r"(^\.env)|(\.(pem|key|p8|p12|pfx|keychain|keychain-db|kdbx|ovpn)$)|(^id_(rsa|dsa|ecdsa|ed25519))|"
    r"secret|credential|password|passwd|\.netrc$",
    re.I,
)
SKIP_DIRS = {"node_modules", ".git", "build", "dist", ".build", "DerivedData", ".derivedData", "Pods", ".venv",
             "venv", "__pycache__", ".next", ".cache", "target", ".gradle", "Library"}
STOP = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "you", "can", "will", "please", "about",
    "what", "when", "where", "which", "who", "how", "why", "are", "was", "were", "did", "does", "has", "have", "had",
    "any", "all", "get", "got", "tell", "show", "find", "look", "check", "give", "read", "me", "my", "our", "its",
    "is", "it", "in", "on", "of", "to", "a", "an", "at", "or", "be", "do", "there", "their", "them", "then", "than",
    "file", "files", "folder", "notes", "note",
}
# Keys in a page's JSON that hold no words worth reading out.
SKIP_KEYS = {"id", "icon", "accent", "color", "colour", "tint", "url", "image", "image_url", "src", "href", "schema",
             "revision", "order", "layout", "style", "kind", "action", "payload", "symbol", "launch_url", "series_id"}


def words(text: str) -> list[str]:
    seen: list[str] = []
    for w in re.findall(r"[a-z0-9][a-z0-9'_-]*", (text or "").lower()):
        w = w.strip("'_-")
        if len(w) > 1 and w not in STOP and w not in seen:
            seen.append(w)
    return seen


def clip(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.7 else cut).rstrip() + " …"


def one_line(text: Any, limit: int = 300) -> str:
    return clip(" ".join(str(text or "").split()), limit)


def when(epoch: float | None) -> str:
    if not epoch:
        return "unknown date"
    stamp = datetime.fromtimestamp(epoch).astimezone()
    days = (datetime.now().astimezone().date() - stamp.date()).days
    if days == 0:
        return f"today {stamp.strftime('%H:%M')}"
    if days == 1:
        return f"yesterday {stamp.strftime('%H:%M')}"
    return stamp.strftime("%Y-%m-%d")


def size_text(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def doc_text(node: Any, out: list[str], depth: int = 0) -> None:
    """Every readable string in a page's JSON, in order. Tables keep their rows on one line."""
    if depth > 14 or len(out) > 4000:
        return
    if isinstance(node, str):
        text = node.strip()
        if text and not text.startswith(("http://", "https://", "orbital://", "data:")):
            out.append(text)
        return
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        out.append(str(node))
        return
    if isinstance(node, list):
        if node and all(isinstance(c, (str, int, float)) and not isinstance(c, bool) for c in node):
            out.append(" | ".join(str(c) for c in node))
            return
        for child in node:
            doc_text(child, out, depth + 1)
        return
    if isinstance(node, dict):
        label = node.get("label") or node.get("title") or node.get("name")
        value = node.get("value")
        if isinstance(label, str) and isinstance(value, (str, int, float)) and not isinstance(value, bool):
            extra = node.get("detail") or node.get("delta") or node.get("unit") or ""
            out.append(f"{label}: {value}{(' ' + str(extra)) if extra else ''}")
            rest = {k: v for k, v in node.items() if k not in ("label", "title", "name", "value", "detail", "delta", "unit")}
        else:
            rest = node
        for key, child in rest.items():
            if key in SKIP_KEYS or key.endswith("_id") or key.endswith("_url"):
                continue
            doc_text(child, out, depth + 1)


def page_text(doc: dict) -> str:
    lines: list[str] = []
    head = [doc.get("title") or doc.get("id"), doc.get("subtitle"), doc.get("summary")]
    lines.append(" — ".join(str(h) for h in head if h))
    if doc.get("updated_at"):
        lines.append(f"(updated {doc['updated_at']})")
    body: list[str] = []
    doc_text(doc.get("blocks") or [], body)
    for group_key, title_key in (("tabs", "title"), ("nodes", "title")):
        for group in doc.get(group_key) or []:
            if not isinstance(group, dict):
                continue
            body.append(f"## {group.get(title_key) or group.get('id') or group_key}")
            doc_text(group.get("blocks") or [], body)
            for child in group.get("children") or []:
                if isinstance(child, dict):
                    body.append(f"### {child.get('title') or child.get('id')}")
                    doc_text(child.get("blocks") or [], body)
    deduped: list[str] = []
    for line in body:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    lines.extend(deduped)
    return "\n".join(lines)


def score(text: str, terms: list[str], title: str = "") -> float:
    if not terms:
        return 0.0
    low = text.lower()
    title_low = title.lower()
    hits = sum(1 for t in terms if t in low)
    if not hits:
        return 0.0
    return hits / len(terms) + 0.5 * sum(1 for t in terms if t in title_low) / len(terms)


def snippet(text: str, terms: list[str], width: int = 360) -> str:
    flat = " ".join(text.split())
    low = flat.lower()
    starts = [low.find(t) for t in terms if low.find(t) >= 0]
    if not starts:
        return clip(flat, width)
    start = max(0, min(starts) - width // 3)
    piece = flat[start : start + width]
    return ("… " if start else "") + piece + (" …" if start + width < len(flat) else "")


class Lookup:
    """Bound to one hub. `desk` supplies the live state and tasks; everything else is read here."""

    def __init__(self, hub: Any, desk: Any = None) -> None:
        self.hub = hub
        self.desk = desk

    # Where files may be read

    def roots(self) -> list[Path]:
        raw = self.hub.cfg.get("desk.read_roots")
        names = [str(r) for r in raw] if isinstance(raw, list) and raw else list(DEFAULT_READ_ROOTS)
        root = self.hub.cfg.get("desk.projects_root")
        if root:
            names.append(str(root))
        for row in self.hub.cfg.get("desk.projects", []) or []:
            if isinstance(row, dict) and isinstance(row.get("path"), str):
                names.append(row["path"])
        out: list[Path] = []
        for name in names:
            try:
                path = Path(name).expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            if path.is_dir() and path not in out:
                out.append(path)
        return out

    def allowed(self, path: Path) -> str | None:
        """None when the path may be read, else why not."""
        try:
            real = path.expanduser().resolve()
        except (OSError, RuntimeError):
            return "that path can't be resolved"
        roots = self.roots()
        root = next((r for r in roots if real == r or r in real.parents), None)
        if root is None:
            listed = ", ".join(_home_short(r) for r in roots) or "none"
            return f"outside the folders the desk may read ({listed})"
        rel = real.relative_to(root).parts
        if any(part.startswith(".") for part in rel):
            return "hidden files and folders are off limits"
        if any(SECRET_NAME.search(part) for part in rel):
            return "that looks like a secret or key file, so the desk won't read it"
        return None

    def resolve_path(self, raw: str) -> Path:
        text = (raw or "").strip().strip("'\"")
        if not text:
            return self.roots()[0] if self.roots() else Path.home()
        path = Path(text).expanduser()
        if path.is_absolute():
            return path
        project = self.desk.projects().resolve(text) if self.desk is not None else None
        if project:
            return Path(project["path"]).expanduser()
        for root in self.roots():
            candidate = root / text
            if candidate.exists():
                return candidate
        first, _, rest = text.partition("/")
        project = self.desk.projects().resolve(first) if self.desk is not None else None
        if project:
            return Path(project["path"]).expanduser() / rest
        loose = self._loose_folder(text)
        if loose is not None:
            return loose
        return (self.roots()[0] if self.roots() else Path.home()) / text

    def _loose_folder(self, text: str) -> Path | None:
        """A top-level folder whose name holds every word said ("jane smith folder" → jane_smith)."""
        terms = [t for t in words(text) if t not in ("folder", "project", "projects", "directory", "file")]
        if not terms:
            return None
        hits: list[Path] = []
        for root in self.roots():
            try:
                children = [c for c in root.iterdir() if c.is_dir() and not c.name.startswith(".")]
            except OSError:
                continue
            for child in children:
                name = re.sub(r"[^a-z0-9]+", " ", child.name.lower())
                squashed = name.replace(" ", "")
                if all(t in name.split() or t in squashed for t in terms):
                    hits.append(child)
        hits.sort(key=lambda p: len(p.name))
        return hits[0] if hits else None

    # The hub's own data

    def status(self) -> str:
        if self.desk is None:
            return "No desk on this hub."
        return clip("\n".join(self.desk.live_state_lines()), MAX_OUT * 2)

    def page(self, name: str) -> str:
        store = self.hub.store
        name = (name or "").strip()
        doc = store.get(name) if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name or "") else None
        if doc is None:
            terms = words(name)
            best, best_score = None, 0.0
            for candidate in store.list_docs():
                s = score(f"{candidate.get('title')} {candidate.get('id')} {candidate.get('subtitle') or ''}", terms,
                          str(candidate.get("title") or ""))
                if s > best_score:
                    best, best_score = candidate, s
            doc = best
        if doc is None:
            titles = ", ".join(str(d.get("title") or d.get("id")) for d in store.list_docs()[:40])
            return f"No page matches “{name}”. Pages: {titles or 'none'}."
        return clip(page_text(doc), MAX_OUT)

    def task(self, task_id: str) -> str:
        if self.desk is None:
            return "No desk on this hub."
        runner = self.desk.runner
        task = runner.get(str(task_id or "").strip())
        if task is None:
            terms = words(task_id)
            ranked = sorted(runner.tasks, key=lambda t: score(f"{t.get('title')} {t.get('brief')}", terms, str(t.get("title"))), reverse=True)
            task = ranked[0] if ranked and terms and score(f"{ranked[0].get('title')} {ranked[0].get('brief')}", terms) > 0 else None
        if task is None:
            return f"No desk task {task_id}."
        lines = [f"{task['id']} — {task.get('title')} [{task.get('status')}"
                 f"{', ' + str(task.get('progress')) + '%' if task.get('progress') else ''}]"
                 f"{' in ' + task['project'] if task.get('project') else ''}"
                 f"{' on ' + task['machine'] if task.get('machine') else ''}"
                 f"{' (' + task['agent'] + ')' if task.get('agent') else ''}"]
        lines.append(f"Asked: {one_line(task.get('brief'), 600)}")
        if task.get("waiting"):
            lines.append(f"Waiting: {task['waiting']}")
        if task.get("question"):
            lines.append(f"ASKS THE USER: {task['question']}")
        if task.get("blocked"):
            lines.append(f"Blocked, needs the user: {task['blocked']}")
        if task.get("answer"):
            lines.append(f"Answer: {task['answer']}")
        findings = [f["text"] for f in task.get("findings") or []]
        if findings:
            lines.append("Findings:")
            lines.extend(f"- {one_line(f, 500)}" for f in findings[-12:])
        notes = [n["text"] for n in task.get("notes") or []]
        if notes:
            lines.append("Latest notes: " + " | ".join(one_line(n, 200) for n in notes[-4:]))
        if task.get("error") and task.get("status") != "done":
            lines.append(f"Why it stopped: {one_line(task['error'], 400)}")
        if task.get("summary"):
            lines.append("Report:")
            lines.append(task["summary"])
        return clip("\n".join(lines), MAX_OUT)

    def search(self, query: str, limit: int = 8) -> str:
        terms = words(query)
        if not terms:
            return "Say what to search for."
        hits: list[tuple[float, str]] = []
        for doc in self.hub.store.list_docs():
            text = page_text(doc)
            s = score(text, terms, str(doc.get("title") or ""))
            if s:
                hits.append((s + 0.05, f"Page “{doc.get('title')}” (read_page {doc.get('id')}): {snippet(text, terms)}"))
        if self.desk is not None:
            for task in self.desk.runner.tasks[-200:]:
                findings = " ".join(f.get("text", "") for f in task.get("findings") or [])
                text = f"{task.get('title')} {task.get('brief')} {task.get('answer') or ''} {findings} {task.get('summary') or ''}"
                s = score(text, terms, str(task.get("title") or ""))
                if s:
                    body = task.get("answer") or findings or task.get("summary") or task.get("brief") or ""
                    hits.append((s, f"Agent task {task['id']} “{task.get('title')}” [{task.get('status')}, "
                                    f"{task.get('finished_at') or task.get('created_at') or ''}]: {snippet(body, terms)}"))
        for item in self.hub.tracker.items():
            notes = " ".join(str(n.get("text") or "") for n in item.get("notes") or [])
            text = f"{item.get('title')} {item.get('details') or ''} {notes} {item.get('summary') or ''}"
            s = score(text, terms, str(item.get("title") or ""))
            if s:
                hits.append((s - 0.05, f"Tracker “{item.get('title')}” [{item.get('status')}]: "
                                       f"{snippet(item.get('summary') or notes or item.get('details') or '', terms)}"))
        try:
            alerts = self.hub.store.load_alerts()
        except Exception:
            alerts = []
        for alert in alerts[-300:]:
            text = f"{alert.get('title')} {alert.get('subtitle') or ''} {alert.get('body') or ''}"
            s = score(text, terms, str(alert.get("title") or ""))
            if s:
                hits.append((s - 0.1, f"Alert “{alert.get('title')}” ({alert.get('created_at')}): {snippet(text, terms, 240)}"))
        if not hits:
            return (f"Nothing on the hub mentions {' '.join(terms)}. "
                    "Try find_files for documents on this Mac, or start an agent to look further.")
        hits.sort(key=lambda h: h[0], reverse=True)
        return clip("\n".join(f"- {line}" for _, line in hits[:limit]), MAX_OUT)

    # Files

    async def find_files(self, query: str = "", project: str = "", limit: int = 20) -> str:
        if project:
            base = self.resolve_path(project)
            why = self.allowed(base)
            if why:
                return f"Can't look in {project}: {why}."
            if not base.is_dir():
                return f"There's no folder for {project}."
            scopes = [base]
        else:
            scopes = self.roots()
        if not scopes:
            return "The desk has no folders it may read."
        terms = words(query)
        if not terms:
            if not project:
                return "Say what to look for, or which project folder."
            return self._recent_files(scopes[0], limit)
        found: dict[Path, float] = {}
        mdfind = shutil.which("mdfind") or ("/usr/bin/mdfind" if Path("/usr/bin/mdfind").exists() else None)
        if mdfind:
            spot = " ".join(terms)
            results = await asyncio.gather(
                *[_run([mdfind, "-onlyin", str(scope), spot], 6) for scope in scopes],
                *[_run([mdfind, "-onlyin", str(scope), "-name", terms[0]], 6) for scope in scopes],
            )
            for out in results:
                for line in out.splitlines()[:400]:
                    path = Path(line.strip())
                    if line.strip() and path not in found:
                        found[path] = 0.0
        if len(found) < 3:
            for scope in scopes:
                for path in _walk_names(scope, terms, 400):
                    found.setdefault(path, 0.0)
        rows = []
        for path in found:
            if self.allowed(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            name = path.name.lower()
            name_hits = sum(1 for t in terms if t in name)
            path_hits = sum(1 for t in terms if t in str(path).lower())
            rank = name_hits * 3 + path_hits + (0.5 if path.is_file() else 0) + min(1.0, max(0.0, 1 - (time.time() - stat.st_mtime) / (90 * 86400)))
            rows.append((rank, path, stat))
        if not rows:
            where = project or ", ".join(_home_short(s) for s in scopes)
            return f"No files about {' '.join(terms)} in {where}."
        rows.sort(key=lambda r: r[0], reverse=True)
        lines = [f"{len(rows)} match{'es' if len(rows) != 1 else ''}; best first:"]
        for _, path, stat in rows[:limit]:
            kind = "folder" if path.is_dir() else size_text(stat.st_size)
            lines.append(f"- {_home_short(path)} ({kind}, changed {when(stat.st_mtime)})")
        return clip("\n".join(lines), MAX_OUT)

    def _recent_files(self, folder: Path, limit: int) -> str:
        rows = []
        for path in _walk(folder, 6000):
            if path.is_file() and not self.allowed(path):
                try:
                    rows.append((path.stat().st_mtime, path))
                except OSError:
                    continue
        if not rows:
            return f"{_home_short(folder)} has no readable files."
        rows.sort(reverse=True)
        lines = [f"Most recently changed in {_home_short(folder)}:"]
        for mtime, path in rows[:limit]:
            lines.append(f"- {_home_short(path)} (changed {when(mtime)})")
        return "\n".join(lines)

    def list_folder(self, path: str) -> str:
        folder = self.resolve_path(path)
        why = self.allowed(folder)
        if why:
            return f"Can't open {path}: {why}."
        if not folder.is_dir():
            return f"{_home_short(folder)} isn't a folder."
        rows = []
        try:
            children = list(folder.iterdir())
        except OSError as exc:
            return f"Can't list {_home_short(folder)}: {exc.strerror or exc}."
        for child in children:
            if child.name.startswith(".") or self.allowed(child):
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            rows.append((child.is_dir(), stat.st_mtime, child, stat))
        if not rows:
            return f"{_home_short(folder)} is empty."
        rows.sort(key=lambda r: (not r[0], -r[1]))
        lines = [f"{_home_short(folder)} — {len(rows)} item{'s' if len(rows) != 1 else ''} (folders first, newest first):"]
        for is_dir, mtime, child, stat in rows[:70]:
            kind = "folder" if is_dir else size_text(stat.st_size)
            lines.append(f"- {child.name}{'/' if is_dir else ''} ({kind}, changed {when(mtime)})")
        if len(rows) > 70:
            lines.append(f"… and {len(rows) - 70} more")
        return "\n".join(lines)

    async def read_file(self, path: str, query: str = "", offset: int = 0) -> str:
        target = self.resolve_path(path)
        why = self.allowed(target)
        if why:
            return f"Can't read {path}: {why}."
        if target.is_dir():
            return self.list_folder(str(target))
        if not target.is_file():
            hint = await self.find_files(Path(path).stem or path) if words(Path(path).stem) else ""
            return f"There's no file at {_home_short(target)}." + (f" Close matches:\n{hint}" if hint and "No files" not in hint else "")
        try:
            stat = target.stat()
        except OSError as exc:
            return f"Can't read {_home_short(target)}: {exc}."
        if stat.st_size > MAX_FILE_BYTES:
            return f"{_home_short(target)} is {size_text(stat.st_size)}, too big to read here. Start an agent for it."
        text = await _extract(target)
        if text is None:
            return (f"{_home_short(target)} is a {target.suffix or 'binary'} file the desk can't read as text "
                    f"({size_text(stat.st_size)}). Start an agent to look at it.")
        text = text.replace("\r\n", "\n")
        head = f"{_home_short(target)} ({len(text):,} characters, changed {when(stat.st_mtime)})"
        terms = words(query)
        if terms and len(text) > MAX_READ:
            return head + "\nParts that mention " + " ".join(terms) + ":\n" + _passages(text, terms, MAX_READ)
        start = max(0, int(offset or 0))
        piece = text[start : start + MAX_READ]
        tail = ""
        if start + MAX_READ < len(text):
            tail = f"\n[{len(text) - start - MAX_READ:,} more characters: read_file again with offset={start + MAX_READ}, or pass query to jump to a topic]"
        prefix = f" from character {start:,}" if start else ""
        return f"{head}{prefix}:\n{piece}{tail}"


# Helpers

def _home_short(path: Path) -> str:
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home):] if text.startswith(home) else text


async def _run(argv: list[str], timeout: float) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL
        )
    except OSError:
        return ""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return ""
    return out.decode("utf-8", "replace")


def _walk(root: Path, limit: int):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        for name in filenames:
            if name.startswith("."):
                continue
            count += 1
            if count > limit:
                return
            yield Path(dirpath) / name
        for name in dirnames:
            yield Path(dirpath) / name


def _walk_names(root: Path, terms: list[str], limit: int) -> list[Path]:
    out = []
    for path in _walk(root, 30000):
        name = path.name.lower()
        if all(t in name for t in terms) or (len(terms) > 1 and sum(t in name for t in terms) >= 2):
            out.append(path)
            if len(out) >= limit:
                break
    return out


async def _extract(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        tool = shutil.which("pdftotext") or next((p for p in ("/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext") if Path(p).exists()), None)
        if tool:
            out = await _run([tool, "-layout", "-l", "60", str(path), "-"], 20)
            return out if out.strip() else None
        return None
    if suffix in TEXTUTIL_SUFFIXES:
        tool = shutil.which("textutil") or ("/usr/bin/textutil" if Path("/usr/bin/textutil").exists() else None)
        if tool:
            out = await _run([tool, "-convert", "txt", "-stdout", str(path)], 20)
            return out if out.strip() else None
        return None
    try:
        data = path.read_bytes()[:3 * 1024 * 1024]
    except OSError:
        return None
    if suffix not in TEXT_SUFFIXES and b"\x00" in data[:4096]:
        return None
    text = data.decode("utf-8", "replace")
    if suffix in (".json",) and len(text) < 400_000:
        try:
            text = json.dumps(json.loads(text), indent=1, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    return text


def _passages(text: str, terms: list[str], budget: int) -> str:
    """The paragraphs that mention the most terms, in document order."""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    ranked = sorted(range(len(paras)), key=lambda i: sum(t in paras[i].lower() for t in terms), reverse=True)
    keep: list[int] = []
    used = 0
    for index in ranked:
        if not any(t in paras[index].lower() for t in terms):
            break
        size = len(paras[index])
        if used + size > budget and keep:
            continue
        keep.append(index)
        used += min(size, budget)
        if used >= budget:
            break
    if not keep:
        return clip(text, budget)
    return "\n…\n".join(clip(paras[i].strip(), budget) for i in sorted(keep))


ToolFn = Callable[..., Any]
