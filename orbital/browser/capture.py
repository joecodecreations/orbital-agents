"""Network capture on the bridge side: credential redaction, filtering, and the endpoint catalog.

The extension sends raw events (see browser-extension/lib/capture.js). Before an event is kept,
`redact()` replaces credential values (Authorization and Cookie headers, token-like query
parameters and JSON fields) with "[redacted]", unless `browser.capture_secrets = true`.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

REDACTED = "[redacted]"
SECRET_NAME = re.compile(
    r"(?i)(authorization|cookie|token|secret|passw(or)?d|passwd|session|api[-_]?key|apikey|csrf|xsrf|"
    r"signature|credential|private[-_]?key|access[-_]?key|client[-_]?secret|otp|sapisid)"
)
HEADER_FIELDS = ("headers", "requestHeaders", "responseHeaders")
BODY_FIELDS = ("requestBody", "responseBody")
MAX_FIELD_CHARS = 200_000
NETWORK_TYPES = ("network.body", "network.completed", "network.request", "network.request_headers", "network.error", "page.ready")


def _redact_mapping(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        if isinstance(key, str) and SECRET_NAME.search(key):
            out[key] = REDACTED
        elif isinstance(value, dict):
            out[key] = _redact_mapping(value)
        elif isinstance(value, list):
            out[key] = [_redact_mapping(v) if isinstance(v, dict) else v for v in value]
        else:
            out[key] = value
    return out


def redact_url(url: str) -> str:
    """Hide the values of token-like query parameters (and any user:password@ in the URL)."""
    if not url or ("?" not in url and "@" not in url):
        return url
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    netloc = parts.netloc
    if "@" in netloc:
        netloc = REDACTED + "@" + netloc.rsplit("@", 1)[1]
    query = parts.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        if any(SECRET_NAME.search(k) for k, _ in pairs):
            query = urlencode([(k, REDACTED if SECRET_NAME.search(k) else v) for k, v in pairs], safe="[]")
    return urlunparse(parts._replace(netloc=netloc, query=query))


def _redact_body(value: Any) -> Any:
    if isinstance(value, dict):
        return _redact_mapping(value)
    if not isinstance(value, str) or not value:
        return value
    text = value.strip()
    if text[:1] in "{[":
        try:
            parsed = json.loads(text)
        except ValueError:
            return value
        if isinstance(parsed, dict):
            return json.dumps(_redact_mapping(parsed), ensure_ascii=False)
        if isinstance(parsed, list):
            return json.dumps([_redact_mapping(v) if isinstance(v, dict) else v for v in parsed], ensure_ascii=False)
        return value
    if "=" in text and "&" in text and " " not in text[:200]:
        # form-encoded body
        try:
            pairs = parse_qsl(text, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            return value
        if any(SECRET_NAME.search(k) for k, _ in pairs):
            return urlencode([(k, REDACTED if SECRET_NAME.search(k) else v) for k, v in pairs])
    return value


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + "...[truncated]"
    return value


def redact(event: dict, keep_secrets: bool = False) -> dict:
    """A copy of `event` that is safe to keep: credential values hidden, long strings clipped."""
    out = {k: _clip(v) for k, v in event.items()}
    if keep_secrets:
        return out
    for field in HEADER_FIELDS:
        if isinstance(out.get(field), dict):
            out[field] = {k: (REDACTED if SECRET_NAME.search(str(k)) else v) for k, v in out[field].items()}
    for field in BODY_FIELDS:
        if field in out:
            out[field] = _redact_body(out[field])
    if isinstance(out.get("url"), str):
        out["url"] = redact_url(out["url"])
    return out


# ── reading the buffer ─────────────────────────────────────────────

def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def is_api_event(event: dict) -> bool:
    url = str(event.get("url") or "")
    if not url.startswith(("http://", "https://")):
        return False
    if event.get("type") == "network.body":
        return True
    if event.get("resourceType") in ("xmlhttprequest", "fetch", "websocket"):
        return True
    low = url.lower()
    return "/api/" in low or "/graphql" in low or "/rest/" in low or bool(re.search(r"/v\d+/", low))


def filter_network(events: Iterable[dict], host: str | None = None, path: str | None = None,
                   limit: int = 200, api_only: bool = False, types: Iterable[str] | None = None) -> list[dict]:
    """Newest first. `host` matches the URL's host (substring), `path` any part of the URL."""
    wanted = set(types or NETWORK_TYPES)
    host = (host or "").lower()
    out: list[dict] = []
    for e in reversed(list(events)):
        if e.get("type") not in wanted:
            continue
        url = str(e.get("url") or "")
        if host and host not in _host(url):
            continue
        if path and path not in url:
            continue
        if api_only and not is_api_event(e):
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


_ID_SEGMENT = re.compile(r"/(\d{3,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{24,})(?=/|$)", re.I)


def path_key(path: str) -> str:
    """/users/12345/posts/9f1c... -> /users/{id}/posts/{id}, so one endpoint groups its calls."""
    return _ID_SEGMENT.sub("/{id}", path or "/")


def build_endpoint_catalog(events: Iterable[dict], limit: int = 500) -> list[dict]:
    """One entry per METHOD host path (ids folded), most-called first."""
    catalog: dict[str, dict] = {}
    for e in events:
        if e.get("type") not in ("network.body", "network.request", "network.completed", "network.request_headers"):
            continue
        if not is_api_event(e):
            continue
        url = str(e.get("url") or "")
        try:
            p = urlparse(url)
        except ValueError:
            continue
        method = str(e.get("method") or "GET").upper()
        key_path = path_key(p.path)
        key = f"{method} {p.netloc}{key_path}"
        status = e.get("status") or e.get("statusCode")
        headers = e.get("requestHeaders") or e.get("headers") or {}
        entry = catalog.get(key)
        if entry is None:
            entry = catalog[key] = {
                "method": method,
                "host": p.netloc,
                "path": p.path or "/",
                "path_key": key_path,
                "count": 0,
                "statuses": [],
                "sample_urls": [],
                "query_keys": [],
                "auth_header_present": False,
                "content_type": None,
                "response_sample": None,
                "last_seen": None,
            }
        entry["count"] += 1
        if status and status not in entry["statuses"]:
            entry["statuses"].append(status)
        clean_url = f"{p.scheme}://{p.netloc}{p.path}" + (f"?{p.query}" if p.query else "")
        if clean_url not in entry["sample_urls"] and len(entry["sample_urls"]) < 5:
            entry["sample_urls"].append(clean_url)
        for k, _ in parse_qsl(p.query, keep_blank_values=True):
            if k not in entry["query_keys"] and len(entry["query_keys"]) < 30:
                entry["query_keys"].append(k)
        if isinstance(headers, dict) and any(str(h).lower() == "authorization" for h in headers):
            entry["auth_header_present"] = True
        rh = e.get("responseHeaders") or {}
        if isinstance(rh, dict) and not entry["content_type"]:
            for h, v in rh.items():
                if str(h).lower() == "content-type":
                    entry["content_type"] = str(v)[:120]
        body = e.get("responseBody")
        if body and not entry["response_sample"]:
            entry["response_sample"] = (body if isinstance(body, str) else json.dumps(body, default=str))[:2000]
        ts = e.get("ts")
        if ts and (entry["last_seen"] is None or ts > entry["last_seen"]):
            entry["last_seen"] = ts
    items = sorted(catalog.values(), key=lambda x: (-x["count"], x["host"], x["path"]))
    return items[:limit]


class EventLog:
    """Append-only JSON-lines history of captured (redacted) events, rolled at a size cap.

    Past `max_bytes` the file becomes events.jsonl.1 (replacing the older one), so at most about
    twice the cap is ever on disk. max_bytes = 0 turns the file off.
    """

    def __init__(self, path: Path, max_bytes: int):
        self.path = path
        self.max_bytes = max(0, int(max_bytes))
        self._lock = threading.Lock()

    def write(self, events: list[dict]) -> None:
        if self.max_bytes <= 0 or not events:
            return
        lines = "".join(json.dumps(e, ensure_ascii=False, default=str) + "\n" for e in events)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self.path.exists() and self.path.stat().st_size + len(lines) > self.max_bytes:
                    os.replace(self.path, self.path.with_name(self.path.name + ".1"))
            except OSError:
                pass
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(lines)

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def clear(self) -> None:
        with self._lock:
            for p in (self.path, self.path.with_name(self.path.name + ".1")):
                try:
                    p.unlink()
                except OSError:
                    pass
