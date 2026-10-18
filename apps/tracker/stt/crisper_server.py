#!/usr/bin/env python3
"""CrisperWhisper 2.0 speech-to-text sidecar for Orbital Agents - Agent Satellite Tracker (optional).

Keeps nyralabs/CrisperWhisper2.0_medium resident on Apple Metal (PyTorch MPS,
float16) and transcribes short utterances over loopback HTTP:

  GET  /health      -> {"ready": true, "device": "mps", "attn": "sdpa", ...}
  POST /transcribe  body: raw little-endian float32 mono PCM
                    headers: X-Sample-Rate (default 16000), X-Mode (intended|verbatim)
                    -> {"text": "...", "audio_seconds": 2.4, "latency_seconds": 0.38}

Metal tuning:
  * device=mps, float16 weights and activations.
  * SDPA attention instead of the engine's default eager attention. Eager is only
    needed for word timestamps, which short utterances never request. Falls back
    to eager if the installed transformers can't switch.
  * Warm-up passes at load so Metal shader compilation happens before the first
    real phrase, then the MPS cache is trimmed.
  * One resident model, one decode at a time (a lock), no per-request reloads.
  * PYTORCH_ENABLE_MPS_FALLBACK=1 so an op missing on MPS runs on CPU instead
    of crashing; HF_HUB_OFFLINE=1 because the weights are already cached.

The tracker starts this with --port <tracker.stt_port> --parent-pid <its pid> only when
`tracker.voice = true`; the server exits when the tracker goes away. It answers loopback only.
Set up the Python env and download the model with apps/tracker/scripts/setup-voice.sh.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
import time

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

import numpy as np  # noqa: E402

log = logging.getLogger("crisper")

STATE: dict = {
    "ready": False,
    "loading": True,
    "error": None,
    "engine": "CrisperWhisper 2.0",
    "model": None,
    "device": None,
    "compute_type": None,
    "attn": None,
    "torch": None,
    "load_seconds": None,
    "transcriptions": 0,
    "last_latency_seconds": None,
}
MODEL = None
DECODE_LOCK = threading.Lock()

# Whisper-family models invent these on near-silence. Dropped when the whole
# utterance is just one of them.
HALLUCINATIONS = {
    "thank you", "thank you.", "thanks for watching", "thanks for watching!",
    "thank you for watching", "thank you for watching.", "you", "bye", "bye.",
    "subtitles by the amara.org community", ".", "", "okay.", "uh", "um",
}


def _clean(text: str, mode: str) -> str:
    text = (text or "").strip()
    if mode == "intended":
        text = re.sub(r"\[[^\]]+\]", "", text)  # stray event tags like [UM]
    return re.sub(r"\s+", " ", text).strip()


def load_model(args: argparse.Namespace) -> None:
    global MODEL
    started = time.time()
    try:
        import torch
        from crisperwhisper import CrisperWhisperModel

        use_mps = args.device != "cpu" and torch.backends.mps.is_available()
        device = "mps" if use_mps else "cpu"
        compute = "float16" if use_mps else "float32"
        log.info("loading %s on %s/%s", args.model, device, compute)
        model = CrisperWhisperModel(
            args.model, backend="transformers", device=device, compute_type=compute
        )

        attn = "eager"
        if args.attn == "sdpa":
            try:
                model._engine.model.set_attn_implementation("sdpa")
                attn = "sdpa"
            except Exception as exc:  # older transformers: keep eager
                log.warning("SDPA switch failed, staying on eager attention: %s", exc)

        # Build the Metal kernels now so the first spoken phrase is fast.
        rng = np.random.default_rng(0)
        for seconds in (1.5, 4.0)[: max(args.warmup, 0)]:
            noise = (rng.standard_normal(int(16000 * seconds)) * 0.002).astype(np.float32)
            model.transcribe(noise, sr=16000, language=args.language, mode=args.mode,
                             max_new_tokens=32)
        if use_mps:
            torch.mps.synchronize()
            torch.mps.empty_cache()

        MODEL = model
        STATE.update(
            ready=True, model=args.model, device=device, compute_type=compute, attn=attn,
            torch=torch.__version__, load_seconds=round(time.time() - started, 2),
        )
        log.info("ready in %.1fs (attn=%s)", time.time() - started, attn)
    except Exception as exc:
        STATE["error"] = f"{type(exc).__name__}: {exc}"
        log.exception("model load failed")
    finally:
        STATE["loading"] = False


def transcribe(pcm: np.ndarray, sr: int, mode: str, language: str) -> dict:
    if MODEL is None:
        raise RuntimeError(STATE["error"] or "model is still loading")
    audio_seconds = len(pcm) / float(sr)
    started = time.time()
    with DECODE_LOCK:
        result = MODEL.transcribe(
            pcm, sr=sr, language=language, mode=mode, max_new_tokens=192,
            word_timestamps=False,
        )
    text = getattr(result, "text", None)
    if text is None and isinstance(result, dict):
        text = result.get("text", "")
    text = _clean(str(text or ""), mode)
    if text.lower() in HALLUCINATIONS:
        text = ""
    latency = round(time.time() - started, 3)
    STATE["transcriptions"] += 1
    STATE["last_latency_seconds"] = latency
    return {"text": text, "audio_seconds": round(audio_seconds, 2), "latency_seconds": latency}


class Handler(BaseHTTPRequestHandler):
    server_version = "CrisperSidecar/1.0"
    args: argparse.Namespace

    def log_message(self, fmt, *a):  # keep the log quiet; errors are logged explicitly
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json(200, STATE)
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/transcribe"):
            self._json(404, {"error": "not found"})
            return
        if self.headers.get("Origin"):
            self._json(403, {"error": "browser requests are not accepted"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 16000 * 4 * 120:
            self._json(400, {"error": "expected 1 byte to 120 s of float32 PCM"})
            return
        raw = self.rfile.read(length)
        sr = int(self.headers.get("X-Sample-Rate") or 16000)
        mode = (self.headers.get("X-Mode") or self.args.mode).lower()
        if mode not in ("intended", "verbatim"):
            mode = "intended"
        pcm = np.frombuffer(raw[: len(raw) - len(raw) % 4], dtype="<f4").astype(np.float32)
        try:
            self._json(200, transcribe(pcm, sr, mode, self.args.language))
        except Exception as exc:
            log.exception("transcribe failed")
            self._json(503, {"error": f"{type(exc).__name__}: {exc}"})


def watch_parent(pid: int) -> None:
    while True:
        time.sleep(2)
        try:
            os.kill(pid, 0)
        except OSError:
            log.info("parent %d is gone, exiting", pid)
            os._exit(0)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=7702)
    p.add_argument("--model", default="nyralabs/CrisperWhisper2.0_medium")
    p.add_argument("--device", default="mps", choices=["mps", "cpu"])
    p.add_argument("--attn", default="sdpa", choices=["sdpa", "eager"])
    p.add_argument("--mode", default="intended", choices=["intended", "verbatim"])
    p.add_argument("--language", default="en")
    p.add_argument("--warmup", type=int, default=2)
    p.add_argument("--parent-pid", type=int, default=0)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s crisper %(levelname)s %(message)s")
    if args.parent_pid:
        threading.Thread(target=watch_parent, args=(args.parent_pid,), daemon=True).start()

    Handler.args = args
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    threading.Thread(target=load_model, args=(args,), daemon=True).start()
    log.info("listening on 127.0.0.1:%d", args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
