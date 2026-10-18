"""Side effects in one place: commands and file writes. With dry_run=True they are printed, not done.

The wizard and `orbital services` both go through Ops, so `--dry-run` shows every action and tests
can swap `exec_fn` for a fake that records the commands instead of calling launchctl or systemctl.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def default_exec(argv: list[str], timeout: float = 60) -> Result:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return Result(127, "", f"{argv[0]}: not found")
    except subprocess.TimeoutExpired:
        return Result(124, "", f"{argv[0]}: timed out after {timeout:.0f}s")
    except OSError as exc:
        return Result(126, "", f"{argv[0]}: {exc}")
    return Result(proc.returncode, proc.stdout, proc.stderr)


def show(argv: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in argv)


class Ops:
    def __init__(self, dry_run: bool = False, out: Callable[[str], None] | None = None,
                 exec_fn: Callable[..., Result] | None = None,
                 stream_fn: Callable[..., Result] | None = None):
        self.dry_run = dry_run
        self.out = out or (lambda line: print(line))
        self.exec_fn = exec_fn or default_exec
        self.stream_fn = stream_fn
        self.actions: list[str] = []
        self._made: set[str] = set()

    def note(self, action: str) -> None:
        self.actions.append(action)
        if self.dry_run:
            self.out(f"  [dry-run] {action}")

    def run(self, argv: list[str], *, mutate: bool = True, timeout: float = 60) -> Result:
        """Run a command. Read-only commands (mutate=False) run even in dry-run mode."""
        if mutate:
            self.note(f"run: {show(argv)}")
            if self.dry_run:
                return Result(0)
        return self.exec_fn(argv, timeout=timeout)

    def run_logged(self, argv: list[str], log_file: Path, *, cwd: Path | None = None,
                   env: dict[str, str] | None = None, progress: Callable[[str], None] | None = None,
                   timeout: float = 3600) -> Result:
        """Long commands (app builds): output goes to log_file, each line to `progress`.
        Returns the exit code and the last 40 lines."""
        self.note(f"run: {show(argv)}" + (f"  (in {cwd})" if cwd else ""))
        if self.dry_run:
            return Result(0)
        if self.stream_fn:
            return self.stream_fn(argv, log_file=log_file, cwd=cwd, env=env, progress=progress)
        import collections
        import time

        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        tail: collections.deque[str] = collections.deque(maxlen=40)
        full_env = dict(os.environ)
        full_env.update(env or {})
        start = time.monotonic()
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=full_env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
        except OSError as exc:
            return Result(127, "", str(exc))
        with log_file.open("a", encoding="utf-8") as log:
            log.write(f"\n$ {show(argv)}\n")
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(line)
                tail.append(line.rstrip("\n"))
                if progress:
                    progress(line.rstrip("\n"))
                if time.monotonic() - start > timeout:
                    proc.kill()
                    tail.append(f"(stopped after {timeout:.0f}s)")
                    break
        code = proc.wait()
        return Result(code, "\n".join(tail), "")

    def write(self, path: Path, data: str | bytes, mode: int = 0o644) -> bool:
        """Write a file atomically if its content changed. Returns True when it changed."""
        path = Path(path)
        raw = data.encode("utf-8") if isinstance(data, str) else data
        try:
            if path.is_file() and path.read_bytes() == raw:
                return False
        except OSError:
            pass
        self.note(f"write: {path}")
        if self.dry_run:
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        os.replace(tmp, path)
        os.chmod(path, mode)
        return True

    def remove(self, path: Path) -> bool:
        path = Path(path)
        if not path.exists() and not path.is_symlink():
            return False
        self.note(f"remove: {path}")
        if not self.dry_run:
            path.unlink()
        return True

    def rmtree(self, path: Path) -> bool:
        import shutil

        path = Path(path)
        if not path.exists():
            return False
        self.note(f"remove folder: {path}")
        if not self.dry_run:
            shutil.rmtree(path)
        return True

    def copy(self, src: Path, dst: Path, mode: int | None = None) -> bool:
        src = Path(src)
        return self.write(dst, src.read_bytes(), mode=mode if mode is not None else (src.stat().st_mode & 0o777))

    def mkdir(self, path: Path) -> None:
        path = Path(path)
        if path.is_dir() or str(path) in self._made:
            return
        self._made.add(str(path))
        self.note(f"mkdir: {path}")
        if not self.dry_run:
            path.mkdir(parents=True, exist_ok=True)
