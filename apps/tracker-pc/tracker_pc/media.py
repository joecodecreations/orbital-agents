"""The small copy of an agent's screenshot or video that the tracker keeps (MediaArchiver.swift).

Images: longest edge 480 px, JPEG quality 42 (typically 15-40 KB), transparency on white.
Videos: a 2x2 contact sheet of frames at 8/36/64/92 % with a play badge, made with ffmpeg when
it is on PATH (the video itself is never kept). Without Pillow (or ffmpeg for videos) the ref is
kept without an archive, exactly like the Mac app does for a file it can't read.
"""

from __future__ import annotations

import base64
import io
import mimetypes
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import TrackerPaths
from .model import make_id, now_iso

try:  # Pillow is in requirements.txt, but tracking must work without it.
    from PIL import Image, ImageDraw, ImageOps
except ImportError:  # pragma: no cover - exercised only on machines without Pillow
    Image = None  # type: ignore[assignment]

MAX_PIXEL = 480
JPEG_QUALITY = 42
INLINE_MAX = 256 * 1024

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff", ".ico"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv", ".mpg", ".mpeg"}

CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".heic": "image/heic", ".webp": "image/webp", ".mp4": "video/mp4", ".m4v": "video/mp4",
    ".mov": "video/quicktime", ".webm": "video/webm", ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8", ".log": "text/plain; charset=utf-8",
}


def kind_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    mime = mimetypes.guess_type(path)[0] or ""
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    return "file"


def content_type(path: str | Path) -> str:
    return CONTENT_TYPES.get(os.path.splitext(str(path))[1].lower(), "application/octet-stream")


def safe_name(s: str) -> str:
    cleaned = "".join(c if (c.isascii() and (c.isalnum() or c in "-_")) else "_" for c in s[:64])
    return cleaned or "x"


def archive_relative_path(item_id: str, media_id: str, when: datetime | None = None) -> str:
    """media/YYYY-MM/<item>-<media>.jpg (local month, like the Mac's DateFormatter)."""
    month = (when or datetime.now()).strftime("%Y-%m")
    return f"media/{month}/{safe_name(item_id)}-{safe_name(media_id)}.jpg"


def is_safe_archive_path(rel: str) -> bool:
    """Archive refs from the CLI must stay inside media/."""
    return rel.startswith("media/") and ".." not in rel and not rel.startswith("/") and "\\" not in rel


def archive(path: str, caption: str | None, item_id: str, paths: TrackerPaths) -> dict[str, Any]:
    """Blocking. Returns a MediaRef dict; `archivePath` is missing when no copy could be made."""
    ref: dict[str, Any] = {"id": make_id(), "kind": kind_for(path), "originalPath": path, "addedAt": now_iso()}
    if caption:
        ref["caption"] = caption
    try:
        ref["originalBytes"] = os.path.getsize(path)
    except OSError:
        pass
    relative = archive_relative_path(item_id, ref["id"])
    dest = paths.archive_path(relative)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ref
    if Image is None:
        return ref
    try:
        if ref["kind"] == "image":
            img = thumbnail(path)
            if img is not None and _write_jpeg(img, dest):
                ref["archivePath"] = relative
        elif ref["kind"] == "video":
            sheet, duration = contact_sheet(path)
            if duration is not None:
                ref["durationSeconds"] = duration
            if sheet is not None and _write_jpeg(sheet, dest):
                ref["archivePath"] = relative
    except Exception:  # noqa: BLE001 - a broken file must never break tracking
        pass
    return ref


def thumbnail(path: str, max_pixel: int = MAX_PIXEL):
    if Image is None:
        return None
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((max_pixel, max_pixel), Image.LANCZOS)
            return _flatten(im)
    except Exception:  # noqa: BLE001
        return None


def _flatten(im):
    """JPEG has no alpha; put transparent screenshots on white instead of black."""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def _write_jpeg(img, dest: Path) -> bool:
    try:
        tmp = dest.with_name(f".{dest.name}.tmp")
        img.save(tmp, "JPEG", quality=JPEG_QUALITY, optimize=True)
        os.replace(tmp, dest)
        return True
    except Exception:  # noqa: BLE001
        return False


def _ffmpeg() -> tuple[str, str] | None:
    ff, probe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    return (ff, probe) if ff and probe else None


def _duration(probe: str, path: str) -> float | None:
    try:
        out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=15, **_no_window())
        value = float(out.stdout.strip().splitlines()[0])
        return value if value == value and value > 0 else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _frame(ff: str, path: str, at: float, max_pixel: int):
    try:
        out = subprocess.run(
            [ff, "-v", "error", "-ss", f"{at:.2f}", "-i", path, "-frames:v", "1",
             "-vf", f"scale='min({max_pixel},iw)':-2", "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True, timeout=20, **_no_window())
        if not out.stdout:
            return None
        return Image.open(io.BytesIO(out.stdout)).convert("RGB")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _no_window() -> dict:
    """Windows: don't flash a console window for each ffmpeg call."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def poster_frame(path: str, max_pixel: int = 360):
    tools = _ffmpeg()
    if tools is None or Image is None:
        return None
    duration = _duration(tools[1], path)
    return _frame(tools[0], path, (duration or 0) * 0.1, max_pixel)


def contact_sheet(path: str):
    """(2x2 sheet image or None, duration seconds or None)."""
    tools = _ffmpeg()
    if tools is None or Image is None:
        return None, None
    ff, probe = tools
    duration = _duration(probe, path)
    times = [duration * f for f in (0.08, 0.36, 0.64, 0.92)] if duration and duration > 1 else [0.0]
    frames = [f for f in (_frame(ff, path, t, 360) for t in times) if f is not None]
    return _tile(frames), duration


def _tile(frames: list):
    if not frames:
        return None
    first = frames[0]
    columns = 2 if len(frames) > 1 else 1
    rows = (len(frames) + columns - 1) // columns
    gap = 3
    tile_w = (MAX_PIXEL - gap * (columns + 1)) // columns
    tile_h = max(1, int(tile_w * first.height / max(first.width, 1)))
    width = columns * tile_w + (columns + 1) * gap
    height = rows * tile_h + (rows + 1) * gap
    sheet = Image.new("RGB", (width, height), (18, 18, 23))
    for i, frame in enumerate(frames):
        col, row = i % columns, i // columns
        x = gap + col * (tile_w + gap)
        y = gap + row * (tile_h + gap)
        sheet.paste(frame.resize((tile_w, tile_h), Image.LANCZOS), (x, y))
    # Small play badge so the sheet reads as "this was a video".
    draw = ImageDraw.Draw(sheet, "RGBA")
    cx, cy, r = width / 2, height / 2, 16
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(0, 0, 0, 140))
    draw.polygon([(cx - 5, cy - 8), (cx - 5, cy + 8), (cx + 9, cy)], fill=(255, 255, 255, 242))
    return sheet


def inline_data(ref: dict, paths: TrackerPaths, max_bytes: int = INLINE_MAX) -> str | None:
    """Base64 of the archived copy, for commands relayed to the hub (None when missing or too big)."""
    rel = ref.get("archivePath")
    if not rel:
        return None
    try:
        data = paths.archive_path(rel).read_bytes()
    except OSError:
        return None
    if not data or len(data) > max_bytes:
        return None
    return base64.b64encode(data).decode("ascii")


_B64_RE = re.compile(r"^[A-Za-z0-9+/=\s]*$")


def store_inline(b64: str, ref: dict, item_id: str, paths: TrackerPaths, max_bytes: int = INLINE_MAX) -> dict:
    """Stores an archived copy that arrived inline (hub relay). Only JPEG data up to max_bytes."""
    out = dict(ref)
    out.pop("archivePath", None)
    if len(b64) > max_bytes * 4 // 3 + 8 or not _B64_RE.match(b64):
        return out
    try:
        data = base64.b64decode(b64, validate=False)
    except (ValueError, TypeError):
        return out
    if not data or len(data) > max_bytes or not data.startswith(b"\xff\xd8\xff"):
        return out
    relative = archive_relative_path(item_id, ref["id"])
    dest = paths.archive_path(relative)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    except OSError:
        return out
    out["archivePath"] = relative
    return out
