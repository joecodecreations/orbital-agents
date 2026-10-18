"""`python -m orbital.satellite` as a real process: serve, run the echo agent, stop cleanly on SIGTERM."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import httpx
import pytest

from orbital.core.secrets import Secrets
from satellite_helpers import free_port, make_cfg, wait_for

pytestmark = pytest.mark.slow


def test_satellite_process_end_to_end(home, tmp_path):
    port = free_port()
    cfg = make_cfg(home)
    (home / "config.toml").write_text((home / "config.toml").read_text().replace("[satellite]", f"[satellite]\nport = {port}"))
    Secrets(cfg.paths).set("ORBITAL_SATELLITE_TOKEN", "process-test-token-0123456789")
    env = dict(os.environ, ORBITAL_HOME=str(home), ORBITAL_TRACK_BIN="off")
    proc = subprocess.Popen([sys.executable, "-m", "orbital.satellite", "--no-heartbeat"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    headers = {"Authorization": "Bearer process-test-token-0123456789"}
    try:
        def up():
            try:
                return httpx.get(f"{base}/v1/health", timeout=1).json()
            except httpx.HTTPError:
                return None
        health = wait_for(up, 20, 0.2)
        assert health and health["service"] == "orbital-satellite", proc.stdout.read1(4000) if proc.poll() is not None else ""
        run_file = home / "run" / "satellite.json"
        assert run_file.is_file()
        job = httpx.post(f"{base}/v1/agents/run", headers=headers,
                         json={"agent": "echo", "prompt": "from a real process", "cwd": str(tmp_path)}).json()
        final = wait_for(lambda: (j := httpx.get(f"{base}/v1/agents/jobs/{job['job_id']}", headers=headers).json())
                         ["status"] == "done" and j, 10)
        assert final["output_tail"] == "from a real process\n"
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=20) == 0
        assert not run_file.exists(), "the run file is removed on a clean stop"
        log = (home / "logs" / "satellite.log").read_text()
        assert "satellite listening" in log and "satellite stopped" in log
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        time.sleep(0.05)
