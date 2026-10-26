#!/usr/bin/env python3
"""orbital-track: how AI agents put their work on the Agent Satellite Tracker (Windows / Linux / macOS).

A Python port of apps/tracker/Sources/orbital-track/main.swift with the same subcommands, flags,
output and exit codes. Delivery: HTTP to this machine's tracker (127.0.0.1:<tracker.port>) → its
inbox folder → the per-user temp inbox (for sandboxed agents). On a satellite (system.role =
"satellite" with hub_url set) every command also goes to the hub's tracker. Tracking must never
block an agent's real work: delivery problems only warn on stderr, the id is still printed, and the
exit code is 0. Only usage errors exit 2.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tracker_pc import VERSION  # noqa: E402
from tracker_pc import media as media_mod  # noqa: E402
from tracker_pc.config import OrbitalConfig, TrackerPaths, tmp_inbox  # noqa: E402
from tracker_pc.model import (  # noqa: E402
    EVIDENCE_INSTRUCTIONS, Settings, agent_display_name, detect_agent, dumps, evidence_level, inbox_write,
    make_id, markdown_paths, normalize_agent, normalize_item, normalize_path, now_iso, valid_id,
)
from tracker_pc.relay import HubRelay, http  # noqa: E402

HELP = """orbital-track — report AI work to Orbital Agents - Agent Satellite Tracker

MAIN agents (talking to the user directly):
  orbital-track start --agent <claude|chatgpt|grok|gemini|NAME> --title "2-5 word title" \\
                      [--details "markdown"] [--progress 0-100] [--workspace DIR] [--label "Claude Code"] [--media FILE]...
      → prints the new ITEM ID. Remember it (shell variables don't survive between tool calls).
  orbital-track update <ID> --progress 0-100 [--note "what just happened"] [--details "markdown" [--append]] [--title "..."] [--media FILE]...
      --progress is REQUIRED at every update: how far along the WHOLE job is, 0-100 (your honest
      estimate). It drives the bar on the tracker and the phone. Raise it as steps complete, e.g.
      plan 10 → first change 30 → most done 60 → tests passing 85; `done` sets 100.
  orbital-track done   <ID> [--summary "markdown: what changed, where, what's next"] [--media FILE]...
      Done items leave the floating panel immediately and stay in history.

SUBAGENTS (only when your prompt contains ORBITAL_TRACK_PARENT=<ID>):
  orbital-track sub <PARENT_ID> --agent <name> --title "..." [--details "..."]   → prints SUB ID
  orbital-track update <SUB_ID> --progress 0-100 [--note "..."]   (at each milestone)
  orbital-track done <SUB_ID> --summary "..."
  Subagents never call `start`.

SCREENSHOT EVIDENCE (set on the tracker, or in the Orbital Agents app):
  orbital-track evidence                  the current level and exactly what it asks of you
  orbital-track evidence [--json]
  orbital-track evidence set <off|min1|all>
      off   nothing required (attach media when you make it anyway)
      min1  at least 1 screenshot for any UI / website / game change (+ a video for game features in motion)
      all   every visual feature screenshotted, every game feature filmed, for review from the phone
  `start`, `sub` and `done` print the instruction on stderr whenever the level isn't off.

Other:
  orbital-track list [--all] [--json]     active items (or everything)
  orbital-track ping                      is the tracker app running? (also shows the hub relay)
  orbital-track help                      this text

Options for every command: --quiet (no warnings), --id ID (start/sub: choose the id yourself).
Text options also accept --details-file F / --summary-file F / --note-file F, or "-" for stdin.
--media takes images or videos (screenshots, recordings). The tracker keeps a small compressed
copy, so it still shows after the original temp file is gone. Markdown images
![alt](/abs/path.png) inside details/summary are archived the same way.

Settings come from $ORBITAL_HOME/config.resolved.json (default ~/.orbital-agents).
Environment: ORBITAL_TRACK_PARENT=<id> turns `start` into `sub`; ORBITAL_TRACK_DISABLE=1 makes
every command a no-op (it still prints an id).
"""

PROGRESS_NUDGE = "add --progress <0-100> to every update so the tracker's bar moves (e.g. --progress 40)."

FLAG_NAMES = {"append", "all", "json", "help", "quiet"}


class UsageError(Exception):
    pass


class Args:
    """Positionals, --key value, --key=value, and flags (CLIArguments.parse)."""

    def __init__(self, argv: list[str]) -> None:
        self.positional: list[str] = []
        self.options: dict[str, list[str]] = {}
        self.flags: set[str] = set()
        i = 0
        while i < len(argv):
            a = argv[i]
            if a == "--":
                self.positional += argv[i + 1:]
                break
            if a.startswith("--") and len(a) > 2:
                key, eq, inline = a[2:].partition("=")
                if key in FLAG_NAMES:
                    self.flags.add(key)
                elif eq:
                    self.options.setdefault(key, []).append(inline)
                elif i + 1 < len(argv):
                    self.options.setdefault(key, []).append(argv[i + 1])
                    i += 1
                else:
                    raise UsageError(f"--{key} needs a value")
            elif a == "-h":
                self.flags.add("help")
            else:
                self.positional.append(a)
            i += 1

    def value(self, key: str) -> str | None:
        v = self.options.get(key)
        return v[-1] if v else None

    def values(self, key: str) -> list[str]:
        return self.options.get(key, [])

    @property
    def verb(self) -> str | None:
        return self.positional[0].lower() if self.positional else None


class CLI:
    def __init__(self, argv: list[str], env: dict[str, str] | None = None) -> None:
        self.env = dict(os.environ if env is None else env)
        self.cfg = OrbitalConfig.load(self.env)
        self.paths = TrackerPaths.from_config(self.cfg)
        self.port = self.cfg.tracker_port
        self.cfg.ensure_home()
        self.p = Args(argv)
        self.quiet = "quiet" in self.p.flags
        self.relay = HubRelay.from_config(self.cfg, self.paths, self.env)

    # Output helpers

    @staticmethod
    def fail(message: str, code: int = 2):
        sys.stderr.write(f"orbital-track: {message}\n")
        raise SystemExit(code)

    def warn(self, message: str) -> None:
        if not self.quiet:
            sys.stderr.write(f"orbital-track: {message}\n")

    def text(self, key: str) -> str | None:
        """--details "text" | --details - (stdin) | --details-file path"""
        file = self.p.value(f"{key}-file")
        if file is not None:
            if file == "-":
                return sys.stdin.read()
            try:
                return Path(normalize_path(file)).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                self.fail(f"can't read {file}")
        v = self.p.value(key)
        if v is None:
            return None
        return sys.stdin.read() if v == "-" else v

    # Talking to the local tracker

    def local(self, method: str, path: str, body: bytes | None = None, timeout: float = 2.0):
        if not self.cfg.tracker_enabled:
            return None
        return http(method, f"http://127.0.0.1:{self.port}{path}", body, timeout)

    def deliver(self, cmd: dict) -> None:
        body = dumps(cmd).encode()
        if self.cfg.tracker_enabled:
            self.deliver_local(body)
        if self.relay is not None:
            for_hub = dict(cmd)
            if cmd.get("media"):
                media = []
                for entry in cmd["media"]:
                    m = dict(entry)
                    if entry.get("archived"):
                        inline = media_mod.inline_data(entry["archived"], self.paths)
                        if inline:
                            m["inlineArchive"] = inline
                    media.append(m)
                for_hub["media"] = media
            self.relay.relay(dumps(for_hub).encode())

    def deliver_local(self, body: bytes) -> None:
        reply = self.local("POST", "/v1/command", body)
        if reply is not None:
            status, data = reply
            if not 200 <= status < 300:
                self.warn(f"tracker rejected the command ({status}): {data[:300].decode('utf-8', 'replace')}")
            return
        for folder in (self.paths.inbox_dir, tmp_inbox()):
            try:
                inbox_write(body, folder)
            except OSError:
                continue
            self.warn(f"tracker app isn't running; queued in {folder}")
            return
        self.warn("couldn't reach the tracker app or write to its inbox; continuing without tracking")

    def media_inputs(self, item_id: str, markdown: list[str | None]) -> list[dict] | None:
        seen: set[str] = set()
        inputs: list[dict] = []
        explicit = [normalize_path(m) for m in self.p.values("media")]
        inline = [p for text in markdown for p in markdown_paths(text)]
        for path in explicit + inline:
            if path in seen:
                continue
            seen.add(path)
            if not os.path.exists(path):
                self.warn(f"media not found, skipped: {path}")
                continue
            # Archive right now: agents' screenshots often live in temp dirs. If the data folder
            # isn't writable (a sandbox), the app archives on receipt.
            ref = media_mod.archive(path, None, item_id, self.paths)
            ref["machine"] = self.cfg.machine_name
            entry: dict = {"path": path}
            if ref.get("archivePath"):
                entry["archived"] = ref
            inputs.append(entry)
        return inputs or None

    def current_settings(self) -> Settings:
        """The running app first (it may have just changed), then settings.json; on a satellite,
        the hub's copy when it was changed more recently."""
        reply = self.local("GET", "/v1/settings", timeout=1.0)
        settings = Settings.from_reply(reply[1]) if reply and reply[0] == 200 else None
        if settings is None:
            settings = Settings.load(self.paths.settings_file)
        if self.relay is not None:
            settings = Settings.newest(settings, self.relay.fetch_settings())
        return settings

    def remind_evidence(self, level: str, item_id: str, extra: str | None = None) -> None:
        """On stderr, so ID=$(orbital-track start ...) still gets just the id."""
        if level == "off" or self.quiet:
            return
        text = EVIDENCE_INSTRUCTIONS[level].replace("<ID>", item_id)
        if extra:
            text += "\n" + extra
        sys.stderr.write("\n" + text + "\n")

    def load_items(self, all_items: bool) -> list[dict] | None:
        reply = self.local("GET", "/v1/items?all=1" if all_items else "/v1/items")
        if reply and reply[0] == 200:
            try:
                parsed = json.loads(reply[1])
                if isinstance(parsed, list):
                    return [i for i in map(normalize_item, parsed) if i]
            except ValueError:
                pass
        try:
            parsed = json.loads(self.paths.items_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(parsed, list):
            return None
        items = [i for i in map(normalize_item, parsed) if i]
        self.warn(f"tracker app not running; reading {self.paths.items_file}")
        return items if all_items else [i for i in items if i["status"] == "active" and not i["dismissed"]]

    def media_count(self, item_id: str) -> int | None:
        items = self.load_items(True)
        if items is None:
            return None
        for item in items:
            if item["id"] == item_id:
                return len(item["media"]) + sum(len(s["media"]) for s in item["subagents"])
        for item in items:
            for sub in item["subagents"]:
                if sub["id"] == item_id:
                    return len(sub["media"])
        return None

    def progress(self) -> int | None:
        """--progress 40 | 40% | 40.5 → 0-100; anything else is a usage error."""
        raw = self.p.value("progress")
        if raw is None:
            return None
        try:
            value = float(str(raw).strip().rstrip("%"))
        except ValueError:
            value = -1
        if not 0 <= value <= 100:
            self.fail("--progress takes a percent from 0 to 100")
        return int(value + 0.5)

    def resolve_agent(self) -> str:
        return normalize_agent(self.p.value("agent") or detect_agent(self.env) or "other")

    def resolve_origin(self, is_sub: bool) -> str | None:
        raw = (self.p.value("origin") or self.env.get("ORBITAL_TRACK_ORIGIN") or "").strip().lower()
        aliases = {
            "ios": "ios", "iphone": "ios", "ipad": "ios",
            "mac": "mac", "macos": "mac", "os": "mac", "catalyst": "mac", "desktop": "mac", "app": "mac",
            "direct": "direct", "chat": "direct", "cli": "direct", "ai": "direct",
            "agent": "agent", "satellite": "agent", "automation": "agent", "api": "agent", "sub": "agent",
        }
        if raw:
            return aliases.get(raw)
        return "agent" if is_sub else "direct"

    def resolve_model(self) -> str | None:
        raw = (self.p.value("model") or self.env.get("ORBITAL_TRACK_MODEL") or "").strip()
        return raw[:120] if raw else None

    # Commands

    def run(self) -> int:
        p = self.p
        if "help" in p.flags or not p.positional or p.verb == "help":
            print(HELP.rstrip("\n"))
            return 2 if not p.positional and "help" not in p.flags else 0
        verb = p.verb or ""
        off = self.env.get("ORBITAL_TRACK_DISABLE", "")
        if off and off != "0" and verb not in ("list", "ls", "ping", "status", "evidence", "screenshots", "settings"):
            print(make_id() if verb in ("start", "sub", "add") else "ok")
            return 0
        env_parent = (self.env.get("ORBITAL_TRACK_PARENT") or "").strip() or None

        if verb in ("start", "add", "sub"):
            return self.cmd_start(verb, env_parent)
        if verb in ("update", "note", "done", "complete", "finish"):
            return self.cmd_update(verb)
        if verb in ("list", "ls"):
            return self.cmd_list()
        if verb in ("evidence", "screenshots", "settings"):
            return self.cmd_evidence()
        if verb in ("ping", "status"):
            return self.cmd_ping()
        self.fail(f"unknown command '{verb}'. Try: orbital-track help")
        return 2

    def cmd_start(self, verb: str, env_parent: str | None) -> int:
        p = self.p
        parent = p.value("parent")
        if verb == "sub":
            parent = parent or (p.positional[1] if len(p.positional) > 1 else None) or env_parent
        # Safety net: a subagent that inherited ORBITAL_TRACK_PARENT can't create a top-level bubble.
        if verb != "sub" and parent is None and env_parent:
            parent = env_parent
        if verb == "sub" and parent is None:
            self.fail("sub needs a parent id: orbital-track sub <PARENT_ID> --title ...")
        title_args = " ".join(p.positional[1:]) if verb != "sub" and len(p.positional) > 1 else None
        title = p.value("title") or title_args
        if not title or not title.strip():
            self.fail("--title is required")
        item_id = p.value("id") or make_id()
        if not valid_id(item_id):
            self.fail("--id must be 2-64 letters, digits, '-' or '_'")
        if parent is not None and not valid_id(parent):
            self.fail("parent id must be 2-64 letters, digits, '-' or '_'")
        cmd: dict = {"op": "start" if parent is None else "sub", "id": item_id, "agent": self.resolve_agent(),
                     "machine": self.cfg.machine_name, "title": title, "sentAt": now_iso()}
        if parent is not None:
            cmd["parent"] = parent
        if p.value("label") is not None:
            cmd["agentLabel"] = p.value("label")
        details = self.text("details")
        if details is not None:
            cmd["details"] = details
        progress = self.progress()
        if progress is not None:
            cmd["progress"] = progress
        origin = self.resolve_origin(parent is not None)
        if origin:
            cmd["origin"] = origin
        model = self.resolve_model()
        if model:
            cmd["model"] = model
        workspace = p.value("workspace")
        cmd["workspace"] = normalize_path(workspace) if workspace is not None else os.getcwd()
        media = self.media_inputs(parent or item_id, [details])
        if media:
            cmd["media"] = media
        self.deliver(cmd)
        print(item_id)
        sys.stdout.flush()
        self.remind_evidence(self.current_settings().evidence, item_id)
        return 0

    def cmd_update(self, verb: str) -> int:
        p = self.p
        if len(p.positional) < 2:
            self.fail(f"{verb} needs an item id")
        item_id = p.positional[1]
        if not valid_id(item_id):
            self.fail("item ids are 2-64 letters, digits, '-' or '_'")
        is_done = verb in ("done", "complete", "finish")
        cmd: dict = {"op": "done" if is_done else "update", "id": item_id, "machine": self.cfg.machine_name,
                     "sentAt": now_iso()}
        # Only an explicit --agent; otherwise the item keeps its own agent (env guesses can mislabel).
        if p.value("agent") is not None:
            cmd["agent"] = normalize_agent(p.value("agent"))
        if p.value("title") is not None:
            cmd["title"] = p.value("title")
        details = self.text("details")
        if details is not None:
            cmd["details"] = details
        if "append" in p.flags:
            cmd["appendDetails"] = True
        note = self.text("note")
        if note is None and verb == "note" and len(p.positional) > 2:
            note = " ".join(p.positional[2:])
        if note is not None:
            cmd["note"] = note
        summary = self.text("summary")
        if summary is not None:
            cmd["summary"] = summary
        progress = self.progress()
        if progress is not None:
            cmd["progress"] = progress
        # Origin/model on update only when explicitly passed (do not invent a default).
        if p.value("origin") or self.env.get("ORBITAL_TRACK_ORIGIN"):
            raw = (p.value("origin") or self.env.get("ORBITAL_TRACK_ORIGIN") or "").strip().lower()
            aliases = {
                "ios": "ios", "iphone": "ios", "ipad": "ios",
                "mac": "mac", "macos": "mac", "os": "mac", "catalyst": "mac", "desktop": "mac", "app": "mac",
                "direct": "direct", "chat": "direct", "cli": "direct", "ai": "direct",
                "agent": "agent", "satellite": "agent", "automation": "agent", "api": "agent", "sub": "agent",
            }
            if raw in aliases:
                cmd["origin"] = aliases[raw]
        model = self.resolve_model()
        if model:
            cmd["model"] = model
        raw = p.value("usage-json")
        if raw is not None:
            try:
                usage = json.loads(raw)
            except json.JSONDecodeError:
                usage = None
            if not isinstance(usage, dict):
                self.fail('--usage-json must be a JSON object like {"engine":"grok","inputTokens":1200,"outputTokens":300}')
            cmd["usage"] = usage
        media = self.media_inputs(item_id, [details, note, summary])
        if media:
            cmd["media"] = media
        self.deliver(cmd)
        print("ok")
        sys.stdout.flush()
        if not is_done and progress is None:
            self.warn(PROGRESS_NUDGE)
        if is_done:
            level = self.current_settings().evidence
            if level != "off":
                count = self.media_count(item_id)
                if count is None:
                    count = len(media or [])
                if count == 0:
                    self.remind_evidence(level, item_id, extra=(
                        "⚠︎ This item has NO screenshots or videos. If the work was visual, attach them now "
                        f"(orbital-track update {item_id} --media /abs/shot.png); the item stays done."))
                elif level == "all" and not self.quiet:
                    sys.stderr.write(
                        f"Screenshot evidence: ALL. {count} picture(s)/video(s) on this item. If any visual feature you "
                        f"changed has none yet, add it: orbital-track update {item_id} --media /abs/shot.png\n")
        return 0

    def cmd_list(self) -> int:
        all_items = "all" in self.p.flags
        items = self.load_items(all_items)
        if items is None:
            self.fail("no items available", code=1)
        if not all_items:
            items = [i for i in items if i["status"] == "active"]
        if "json" in self.p.flags:
            print(dumps(items, pretty=True))
        elif not items:
            print("(nothing being tracked)")
        else:
            for item in items:
                who = agent_display_name(item.get("agent")) if item["source"] == "agent" else "You"
                running = sum(1 for s in item["subagents"] if s["status"] == "active")
                subs = f"  [{running}/{len(item['subagents'])} subagents running]" if item["subagents"] else ""
                machine = f"  @{item['machine']}" if item.get("machine") else ""
                mark = "✓" if item["status"] == "done" else "•"
                print(f"{item['id']}  {mark} {who}: {item['title']}{subs}{machine}")
        return 0

    def cmd_evidence(self) -> int:
        args = self.p.positional[1:]
        if args and args[0].lower() == "set":
            args = args[1:]
        if args:
            level = evidence_level(" ".join(args))
            if level is None:
                self.fail(f"unknown level '{args[0]}'. Use off, min1 or all")
            saved = Settings(level, now_iso(), "cli")
            reply = self.local("POST", "/v1/settings", dumps({"evidence": level, "by": "cli"}).encode())
            if not (reply and 200 <= reply[0] < 300):
                try:
                    saved.save(self.paths.settings_file)
                except OSError as exc:
                    self.fail(f"couldn't save {self.paths.settings_file}: {exc}", code=1)
            if self.relay is not None:
                self.relay.push_settings(saved, "cli")
        settings = self.current_settings()
        if "json" in self.p.flags:
            print(dumps(settings.json(), pretty=True))
        else:
            print(EVIDENCE_INSTRUCTIONS[settings.evidence])
        return 0

    def cmd_ping(self) -> int:
        out: dict = {"machine": self.cfg.machine_name, "role": self.cfg.role, "port": self.port, "cli_version": VERSION}
        ok = False
        reply = self.local("GET", "/v1/health", timeout=1.5)
        health = None
        if reply and reply[0] == 200:
            try:
                health = json.loads(reply[1])
            except ValueError:
                health = None
        if isinstance(health, dict):
            out["tracker"] = health
            ok = True
        else:
            out["tracker"] = {"ok": False, "reason": (
                f"tracker app not running on port {self.port}; commands queue in {self.paths.inbox_dir}"
                if self.cfg.tracker_enabled else "tracker.enabled is false on this machine")}
        if self.relay is not None:
            out["hub"] = {"url": self.cfg.hub_url,
                          "token": "set" if self.relay.token else "missing (ORBITAL_HUB_TOKEN)",
                          "queued": len(self.relay.queued), "cooling_down": self.relay.in_cooldown()}
            if not self.cfg.tracker_enabled:
                ok = self.relay.token is not None
        out["ok"] = ok
        print(dumps(out, pretty=True))
        return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy code page; the help and list output use • ✓ → ⚠︎.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    try:
        cli = CLI(sys.argv[1:] if argv is None else argv)
    except UsageError as exc:
        CLI.fail(str(exc))
        return 2
    return cli.run()


if __name__ == "__main__":
    sys.exit(main())
