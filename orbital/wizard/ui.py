"""Plain-ANSI terminal UI for the setup wizard and the CLI: steps, questions with defaults in
brackets, masked secret input, status lines and a summary table. No dependencies.

Non-interactive (stdin not a terminal, `--yes`, or an answers file value): every question prints
what it chose and why ("default", "answers file", "kept") instead of waiting for input.
"""

from __future__ import annotations

import getpass
import os
import sys
from typing import Any, Callable, Sequence

STYLES = {
    "bold": "1", "dim": "2", "underline": "4",
    "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36",
}


class Abort(Exception):
    """The person chose to stop the wizard."""


def _supports_unicode(stream) -> bool:
    enc = (getattr(stream, "encoding", None) or "").lower()
    return "utf" in enc


class UI:
    def __init__(self, out=None, interactive: bool | None = None, color: bool | None = None,
                 input_fn: Callable[[str], str] | None = None,
                 secret_fn: Callable[[str], str] | None = None):
        self.out = out or sys.stdout
        if interactive is None:
            interactive = sys.stdin.isatty() and self._isatty()
        self.interactive = interactive
        if color is None:
            color = self._isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
        self.color = color
        self.input_fn = input_fn or input
        self.secret_fn = secret_fn
        u = _supports_unicode(self.out)
        self.sym = {"ok": "✓" if u else "+", "warn": "!" if u else "!", "fail": "✗" if u else "x",
                    "info": "·" if u else "-", "arrow": "→" if u else "->", "dot": "•" if u else "*"}

    def _isatty(self) -> bool:
        try:
            return self.out.isatty()
        except (AttributeError, ValueError):
            return False

    # ----------------------------------------------------------------- output
    def style(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        codes = ";".join(STYLES[s] for s in styles if s in STYLES)
        return f"\033[{codes}m{text}\033[0m"

    def line(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def banner(self, title: str, subtitle: str = "") -> None:
        width = max(len(title), len(subtitle)) + 4
        bar = "─" * width if _supports_unicode(self.out) else "-" * width
        self.line(self.style(bar, "cyan"))
        self.line(self.style(f"  {title}", "bold", "cyan"))
        if subtitle:
            self.line(self.style(f"  {subtitle}", "dim"))
        self.line(self.style(bar, "cyan"))

    def step(self, n: int, total: int, title: str) -> None:
        self.line("")
        self.line(self.style(f"[{n}/{total}] {title}", "bold", "blue"))

    def ok(self, text: str) -> None:
        self.line(f"  {self.style(self.sym['ok'], 'green')} {text}")

    def warn(self, text: str) -> None:
        self.line(f"  {self.style(self.sym['warn'], 'yellow', 'bold')} {text}")

    def fail(self, text: str) -> None:
        self.line(f"  {self.style(self.sym['fail'], 'red', 'bold')} {text}")

    def info(self, text: str) -> None:
        self.line(f"  {self.style(self.sym['info'], 'dim')} {text}")

    def hint(self, text: str) -> None:
        self.line(self.style(f"    {text}", "dim"))

    def status(self, level: str, text: str) -> None:
        {"ok": self.ok, "warn": self.warn, "fail": self.fail}.get(level, self.info)(text)

    def table(self, rows: Sequence[tuple[str, str]], indent: int = 2) -> None:
        if not rows:
            return
        width = max(len(k) for k, _ in rows)
        for k, v in rows:
            self.line(" " * indent + self.style(f"{k:<{width}}", "bold") + "  " + str(v))

    # ----------------------------------------------------------------- questions
    def _chosen(self, label: str, shown: str, why: str) -> None:
        tag = self.style(f"({why})", "dim") if why else ""
        self.line(f"  {label}: {self.style(shown, 'cyan')} {tag}".rstrip())

    def _read(self, prompt: str) -> str:
        try:
            return self.input_fn(prompt)
        except EOFError:
            return ""
        except KeyboardInterrupt:
            self.line("")
            raise Abort()

    def ask(self, label: str, default: str = "", *, preset: Any = None, why: str = "",
            validate: Callable[[str], str | None] | None = None, help: str = "") -> str:
        """Ask for a value. `preset` (answers file / kept value) skips the question."""
        if preset is not None:
            value = str(preset)
            self._chosen(label, value or "(empty)", why or "answers file")
            return value
        if not self.interactive:
            self._chosen(label, default or "(empty)", "default")
            return default
        if help:
            self.hint(help)
        while True:
            suffix = f" [{default}]" if default != "" else ""
            raw = self._read(f"  {label}{self.style(suffix, 'dim')}: ").strip()
            value = raw or default
            problem = validate(value) if validate else None
            if problem:
                self.warn(problem)
                continue
            return value

    def ask_int(self, label: str, default: int, *, preset: Any = None, why: str = "",
                lo: int = 1, hi: int = 65535) -> int:
        def check(v: str) -> str | None:
            try:
                n = int(v)
            except ValueError:
                return "please type a number"
            return None if lo <= n <= hi else f"between {lo} and {hi}, please"
        return int(self.ask(label, str(default), preset=preset, why=why, validate=check))

    def ask_bool(self, label: str, default: bool = True, *, preset: Any = None, why: str = "",
                 help: str = "") -> bool:
        if preset is not None:
            value = _as_bool(preset)
            self._chosen(label, "yes" if value else "no", why or "answers file")
            return value
        if not self.interactive:
            self._chosen(label, "yes" if default else "no", "default")
            return default
        if help:
            self.hint(help)
        hint = "Y/n" if default else "y/N"
        while True:
            raw = self._read(f"  {label} {self.style('[' + hint + ']', 'dim')}: ").strip().lower()
            if not raw:
                return default
            if raw in ("y", "yes", "1", "true"):
                return True
            if raw in ("n", "no", "0", "false"):
                return False
            self.warn("y or n, please")

    def ask_choice(self, label: str, options: Sequence[tuple[str, str]], default: str, *,
                   preset: Any = None, why: str = "") -> str:
        """options: [(value, description)]. Returns the chosen value."""
        values = [v for v, _ in options]
        if preset is not None and str(preset) in values:
            self._chosen(label, str(preset), why or "answers file")
            return str(preset)
        if not self.interactive:
            self._chosen(label, default, "default")
            return default
        self.line(f"  {label}:")
        for i, (value, desc) in enumerate(options, 1):
            mark = self.style(" (default)", "dim") if value == default else ""
            self.line(f"    {i}. {desc}{mark}")
        dflt = values.index(default) + 1 if default in values else 1
        while True:
            raw = self._read(f"  Choose 1-{len(options)} {self.style('[' + str(dflt) + ']', 'dim')}: ").strip()
            if not raw:
                return values[dflt - 1]
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return values[int(raw) - 1]
            if raw in values:
                return raw
            self.warn(f"a number from 1 to {len(options)}, please")

    def ask_multi(self, label: str, options: Sequence[tuple[str, str]], defaults: Sequence[str], *,
                  preset: Any = None, why: str = "") -> list[str]:
        """Pick several. Input: numbers separated by commas or spaces, `all`, `none`, Enter = defaults."""
        values = [v for v, _ in options]
        if preset is not None:
            chosen = _multi_from(preset, values, defaults)
            self._chosen(label, ", ".join(chosen) or "none", why or "answers file")
            return chosen
        if not self.interactive:
            self._chosen(label, ", ".join(defaults) or "none", "default")
            return list(defaults)
        self.line(f"  {label}:")
        for i, (value, desc) in enumerate(options, 1):
            on = self.sym["ok"] if value in defaults else " "
            self.line(f"    {i}. [{on}] {desc}")
        while True:
            raw = self._read(f"  Numbers, `all` or `none` {self.style('[Enter = the ticked ones]', 'dim')}: ").strip()
            if not raw:
                return list(defaults)
            if raw.lower() == "all":
                return values
            if raw.lower() == "none":
                return []
            picks = raw.replace(",", " ").split()
            if all(p.isdigit() and 1 <= int(p) <= len(values) for p in picks):
                return [values[int(p) - 1] for p in dict.fromkeys(picks)]
            self.warn("numbers from the list, separated by commas")

    def ask_secret(self, label: str, *, current: str = "", preset: Any = None, why: str = "",
                   allow_empty: bool = True, mask_fn: Callable[[str], str] | None = None,
                   help: str = "") -> str:
        """Hidden input (stars). Enter keeps `current`. Never echoes the value back."""
        masker = mask_fn or _mask
        if preset is not None:
            self._chosen(label, masker(str(preset)) if preset else "(empty)", why or "answers file")
            return str(preset)
        if not self.interactive:
            self._chosen(label, masker(current) if current else "(not set)", "kept" if current else "default")
            return current
        if help:
            self.hint(help)
        suffix = f" [Enter keeps {masker(current)}]" if current else (" [Enter to skip]" if allow_empty else "")
        while True:
            value = self._read_secret(f"  {label}{self.style(suffix, 'dim')}: ").strip()
            if not value and current:
                return current
            if value or allow_empty:
                return value
            self.warn("this one is required")

    def _read_secret(self, prompt: str) -> str:
        if self.secret_fn:
            return self.secret_fn(prompt)
        try:
            return _masked_input(prompt, self.out)
        except KeyboardInterrupt:
            self.line("")
            raise Abort()
        except Exception:
            try:
                return getpass.getpass(prompt)
            except KeyboardInterrupt:
                self.line("")
                raise Abort()

    def pause(self, text: str = "Press Enter to continue") -> None:
        if self.interactive:
            self._read(f"  {self.style(text, 'dim')} ")


def _mask(value: str) -> str:
    from orbital.core.secrets import mask
    return mask(value)


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _multi_from(preset: Any, values: Sequence[str], defaults: Sequence[str]) -> list[str]:
    if isinstance(preset, str):
        p = preset.strip().lower()
        if p == "all":
            return list(values)
        if p in ("none", ""):
            return []
        if p == "default":
            return list(defaults)
        preset = [x.strip() for x in preset.replace(",", " ").split()]
    if isinstance(preset, (list, tuple)):
        return [v for v in values if v in [str(x) for x in preset]]
    return list(defaults)


def _masked_input(prompt: str, out) -> str:
    """Read a line from the terminal, echoing * per character."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    out.write(prompt)
    out.flush()
    chars: list[str] = []
    try:
        tty.setraw(fd, termios.TCSANOW)      # keep typeahead (a pasted token)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                break
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch == "\x04":
                if not chars:
                    break
                continue
            if ch in ("\x7f", "\x08"):
                if chars:
                    chars.pop()
                    out.write("\b \b")
                    out.flush()
                continue
            if ch == "\x15":  # Ctrl-U clears the line
                out.write("\b \b" * len(chars))
                chars.clear()
                out.flush()
                continue
            if ch.isprintable():
                chars.append(ch)
                out.write("*")
                out.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        out.write("\r\n")
        out.flush()
    return "".join(chars)
