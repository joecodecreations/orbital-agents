"""JobManager: real agent subprocesses, concurrency, timeouts, cancel, output cap, records, tracker."""

from __future__ import annotations

import json
import os
import stat
import time

import pytest

from orbital.satellite.agents import available_agents, build_argv, configured_agents, resolve_executable
from orbital.satellite.jobs import JobError, JobManager, OutputSink, trim_job_records
from orbital.satellite.tracker import TrackerReporter
from orbital.satellite.util import iso
from satellite_helpers import make_cfg, pgid_alive, wait_for


def manager(cfg, **kw):
    kw.setdefault("tracker", TrackerReporter(enabled=False))
    kw.setdefault("kill_grace", 1.0)
    return JobManager(cfg, **kw)


def test_echo_job_end_to_end(home, tmp_path):
    cfg = make_cfg(home)
    m = manager(cfg)
    job = m.submit(agent="echo", prompt="hello from the satellite", cwd=str(tmp_path))
    assert job["status"] in ("queued", "running")
    done = m.wait(job["id"], 10)
    assert done["status"] == "done"
    assert done["exit_code"] == 0
    assert done["output_tail"] == "hello from the satellite\n"
    assert done["duration_s"] is not None
    record = json.loads((cfg.paths.satellite_data / "jobs" / f"{job['id']}.json").read_text())
    assert record["status"] == "done" and record["cwd"] == str(tmp_path.resolve())
    assert m.log_text(job["id"]) == "hello from the satellite\n"
    assert [j["id"] for j in m.list()] == [job["id"]]


def test_failed_exit_code_is_recorded(home):
    cfg = make_cfg(home)
    m = manager(cfg)
    job = m.submit(agent="py", prompt="import sys; print('bad'); sys.exit(3)")
    done = m.wait(job["id"], 10)
    assert done["status"] == "failed" and done["exit_code"] == 3 and done["error"] == "exit code 3"


def test_prompt_goes_to_stdin_without_placeholder(home):
    cfg = make_cfg(home)
    m = manager(cfg)
    done = m.wait(m.submit(agent="cat", prompt="read me from stdin")["id"], 10)
    assert done["status"] == "done" and done["output_tail"] == "read me from stdin"


def test_workdir_restriction(home, tmp_path):
    allowed = tmp_path / "work"
    (allowed / "inside").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (allowed / "escape").symlink_to(outside)
    cfg = make_cfg(home, workdir=allowed)
    m = manager(cfg)
    with pytest.raises(JobError, match="outside satellite.workdirs") as err:
        m.submit(agent="echo", prompt="x", cwd=str(outside))
    assert err.value.status == 400
    with pytest.raises(JobError, match="outside satellite.workdirs"):
        m.submit(agent="echo", prompt="x", cwd=str(allowed / ".." / "elsewhere"))
    with pytest.raises(JobError, match="outside satellite.workdirs"):
        m.submit(agent="echo", prompt="x", cwd=str(allowed / "escape"))  # a symlink must not lead out
    with pytest.raises(JobError, match="is not a folder") as err:
        m.submit(agent="echo", prompt="x", cwd=str(allowed / "missing"))
    assert err.value.status == 400
    ok = m.wait(m.submit(agent="echo", prompt="x", cwd="inside")["id"], 10)
    assert ok["status"] == "done" and ok["cwd"] == str((allowed / "inside").resolve())
    default = m.wait(m.submit(agent="echo", prompt="x")["id"], 10)
    assert default["cwd"] == str(allowed.resolve())


def test_unknown_and_unavailable_agents(home):
    cfg = make_cfg(home)
    m = manager(cfg)
    assert "missing" in configured_agents(cfg)
    assert "missing" not in available_agents(cfg)
    assert "echo" in available_agents(cfg)
    with pytest.raises(JobError) as err:
        m.submit(agent="missing", prompt="x")
    assert err.value.status == 409
    with pytest.raises(JobError) as err:
        m.submit(agent="nope", prompt="x")
    assert err.value.status == 404
    with pytest.raises(JobError) as err:
        m.submit(agent="echo", prompt="   ")
    assert err.value.status == 400


def test_concurrency_limit_queues_extra_jobs(home):
    cfg = make_cfg(home)
    m = manager(cfg, max_concurrent=1)
    first = m.submit(agent="sleeper", prompt="5")
    second = m.submit(agent="sleeper", prompt="5")
    assert wait_for(lambda: m.get(first["id"], with_output=False)["status"] == "running", 5)
    time.sleep(0.3)
    assert m.get(second["id"], with_output=False)["status"] == "queued"
    assert m.counts() == {"running": 1, "queued": 1}
    assert {j["id"] for j in m.running_summary()} == {first["id"], second["id"]}
    m.cancel(first["id"])
    assert wait_for(lambda: m.get(second["id"], with_output=False)["status"] == "running", 5), "the queued job starts next"
    assert m.wait(first["id"], 5)["status"] == "cancelled"
    m.cancel(second["id"])
    assert m.wait(second["id"], 5)["status"] == "cancelled"


def test_cancel_queued_job(home):
    cfg = make_cfg(home)
    m = manager(cfg, max_concurrent=1)
    first = m.submit(agent="sleeper", prompt="5")
    queued = m.submit(agent="echo", prompt="never runs")
    out = m.cancel(queued["id"])
    assert out["status"] == "cancelled"
    assert m.get(queued["id"])["error"] == "cancelled before it started"
    m.cancel(first["id"])
    m.wait(first["id"], 5)
    assert m.log_text(queued["id"]) == ""


def test_timeout_kills_the_whole_process_group(home):
    cfg = make_cfg(home)
    m = manager(cfg, timeout_s=0.8)
    # A shell that starts a grandchild: both must die when the job times out.
    job = m.submit(agent="py", prompt="import subprocess, time; subprocess.Popen(['/bin/sleep', '30']); time.sleep(30)")
    running = wait_for(lambda: m.get(job["id"], with_output=False)["pid"], 5)
    assert running
    started = time.monotonic()
    done = m.wait(job["id"], 10)
    assert time.monotonic() - started < 6
    assert done["status"] == "failed" and done["timed_out"] is True
    assert "timed out" in done["error"]
    assert wait_for(lambda: not pgid_alive(running), 5), "the job's process group is gone"


def test_output_cap_keeps_head_and_tail(home):
    cfg = make_cfg(home)
    m = manager(cfg, output_cap=20_000)
    code = "print('FIRST-LINE'); [print('x' * 99) for _ in range(3000)]; print('LAST-LINE')"
    done = m.wait(m.submit(agent="py", prompt=code)["id"], 20)
    assert done["status"] == "done"
    assert done["output_truncated"] is True
    assert done["output_bytes"] > 300_000
    path = cfg.paths.satellite_data / "out" / f"{done['id']}.log"
    assert path.stat().st_size < 21_000
    text = m.log_text(done["id"])
    assert text.startswith("FIRST-LINE")
    assert text.rstrip().endswith("LAST-LINE")
    assert "bytes of output dropped" in text
    assert done["output_tail"].rstrip().endswith("LAST-LINE")


def test_output_sink_live_text_while_truncated(tmp_path):
    sink = OutputSink(tmp_path / "o.log", cap=2048, head=512)
    sink.write(b"A" * 600)
    sink.write(b"B" * 3000)
    sink.write(b"END")
    live = sink.text()
    assert live.startswith("A" * 512) and live.endswith("END") and "dropped" in live
    sink.close()
    assert (tmp_path / "o.log").read_text() == live


def test_records_left_running_are_closed_after_restart(home):
    cfg = make_cfg(home)
    jobs_dir = cfg.paths.satellite_data / "jobs"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "jold-1.json").write_text(json.dumps({"id": "jold-1", "status": "running", "agent": "echo",
                                                      "pid": 999_999, "executable": "/bin/echo",
                                                      "created_at": iso(time.time() - 60)}))
    m = manager(cfg)
    record = m.get("jold-1")
    assert record["status"] == "failed" and "restarted" in record["error"]


def test_tracker_reports_start_and_done(home, tmp_path):
    calls = tmp_path / "calls.jsonl"
    script = tmp_path / "orbital-track"
    script.write_text(f"""#!/bin/sh
python3 -c 'import json,sys; open("{calls}","a").write(json.dumps(sys.argv[1:])+"\\n")' "$@"
if [ "$1" = "start" ]; then echo "trk42"; fi
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    cfg = make_cfg(home)
    m = JobManager(cfg, tracker=TrackerReporter(bin_path=str(script)))
    job = m.submit(agent="echo", prompt="tracked work", title="Tracked title")
    done = m.wait(job["id"], 10)
    assert done["tracker_id"] == "trk42"
    # `done` is reported right after the record closes.
    assert wait_for(lambda: len(calls.read_text().splitlines()) >= 2, 10)
    rows = [json.loads(line) for line in calls.read_text().splitlines()]
    assert rows[0][:5] == ["start", "--agent", "echo", "--title", "Tracked title"]
    assert rows[1][:2] == ["done", "trk42"] and rows[1][2] == "--summary"
    assert rows[1][3].startswith("Finished")


def test_tracker_missing_is_skipped(home, monkeypatch):
    monkeypatch.setenv("ORBITAL_TRACK_BIN", "/nonexistent/orbital-track")
    cfg = make_cfg(home)
    m = JobManager(cfg)
    done = m.wait(m.submit(agent="echo", prompt="no tracker here")["id"], 10)
    assert done["status"] == "done" and done["tracker_id"] is None


def test_argv_substitution_never_uses_a_shell(home, tmp_path):
    cfg = make_cfg(home)
    agent = configured_agents(cfg)["echo"]
    argv, on_stdin = build_argv(agent, "$(touch hacked); `id`", str(tmp_path))
    assert argv == [resolve_executable("/bin/echo"), "$(touch hacked); `id`"] and on_stdin is False
    m = manager(cfg)
    done = m.wait(m.submit(agent="echo", prompt="$(touch hacked)", cwd=str(tmp_path))["id"], 10)
    assert done["output_tail"] == "$(touch hacked)\n"
    assert not (tmp_path / "hacked").exists()


def test_trim_job_records(home):
    cfg = make_cfg(home)
    base = cfg.paths.satellite_data
    (base / "jobs").mkdir(parents=True)
    (base / "out").mkdir(parents=True)
    old, new = time.time() - 40 * 86400, time.time() - 3600
    for i in range(25):
        job_id = f"jt{i:02d}"
        stamp = old if i < 22 else new
        (base / "jobs" / f"{job_id}.json").write_text(json.dumps({"id": job_id, "status": "done", "finished_at": iso(stamp - i)}))
        (base / "out" / f"{job_id}.log").write_text("output")
    (base / "jobs" / "jrun.json").write_text(json.dumps({"id": "jrun", "status": "running", "created_at": iso(old)}))
    orphan = base / "out" / "jorphan.log"
    orphan.write_text("no record")
    os.utime(orphan, (old, old))
    removed = trim_job_records(base, retention_days=14, keep_min=20)
    left = sorted(p.stem for p in (base / "jobs").glob("*.json"))
    assert "jrun" in left, "running jobs are never trimmed"
    assert len(left) == 21  # newest 20 finished + the running one
    assert removed["records"] == 5
    assert not orphan.exists()
    assert all((base / "out" / f"{stem}.log").exists() for stem in left if stem != "jrun")
