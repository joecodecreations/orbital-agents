"""`orbital agents`: start and follow agent jobs on any joined machine, through the hub.

    orbital agents run --on <machine> [--agent claude] [--cwd DIR] [--title T] [--wait] "prompt"
    orbital agents jobs [--on <machine>]       jobs across every machine (or one)
    orbital agents show <machine> <job>        one job with the end of its output
    orbital agents log <machine> <job>         its whole log
    orbital agents cancel <machine> <job>

<machine> is a satellite id or machine name (`orbital satellites`). Uses the automation token.
"""

from __future__ import annotations

import json
import sys
import time

NAME = "agents"
HELP = "Agent jobs on any machine: run --on <machine> \"prompt\" | jobs | show | log | cancel <machine> <job>"
FINISHED = {"done", "failed", "cancelled"}


def register(p):
    p.add_argument("action", choices=["run", "jobs", "show", "log", "cancel"])
    p.add_argument("rest", nargs="*", help="run: the prompt; show/log/cancel: <machine> <job>")
    p.add_argument("--on", dest="machine", help="satellite id or machine name")
    p.add_argument("--agent", help="agent name (default: that machine's satellite.default_agent)")
    p.add_argument("--cwd", help="folder to start in (must be inside that machine's satellite.workdirs)")
    p.add_argument("--title", help="short title for the tracker")
    p.add_argument("--no-track", action="store_true", help="don't report the job to the tracker")
    p.add_argument("--wait", action="store_true", help="run: wait for the job to finish and print its output")
    p.add_argument("--json", action="store_true", help="print JSON")


def _when(job: dict) -> str:
    stamp = str(job.get("started_at") or job.get("created_at") or "")
    return stamp.replace("T", " ")[:16]


def _print_jobs(jobs: list[dict]) -> None:
    if not jobs:
        print("No agent jobs.")
        return
    for job in jobs:
        machine = job.get("machine") or job.get("satellite_id") or ""
        title = job.get("title") or (job.get("prompt") or "")[:60]
        print(f"{job.get('status', '?'):9} {machine:16} {job.get('agent', ''):8} {job.get('id') or job.get('job_id', ''):14} {_when(job):16}  {title}")


def run(args) -> int:
    from orbital.feeds import HubClient, HubError

    try:
        hub = HubClient.from_config(startup_wait=0)
    except HubError as exc:
        print(f"orbital agents: {exc.message}", file=sys.stderr)
        return 1
    try:
        return _run(hub, args)
    except HubError as exc:
        print(f"orbital agents: {exc}", file=sys.stderr)
        return 1
    finally:
        hub.close()


def _run(hub, args) -> int:
    if args.action == "run":
        prompt = " ".join(args.rest).strip()
        if not args.machine or not prompt:
            print('orbital agents run: needs --on <machine> and a prompt, e.g. orbital agents run --on my-laptop "fix the tests"', file=sys.stderr)
            return 2
        answer = hub.run_agent(args.machine, prompt, args.agent, cwd=args.cwd, title=args.title,
                               track=False if args.no_track else None)
        job_id = answer.get("job_id")
        if not args.wait:
            print(json.dumps(answer, indent=2) if args.json else f"started {job_id} on {answer.get('machine') or args.machine}")
            return 0
        while True:
            job = hub.agent_job(args.machine, job_id)
            if job.get("status") in FINISHED:
                break
            time.sleep(2)
        if args.json:
            print(json.dumps(job, indent=2))
        else:
            print((job.get("output_tail") or "").rstrip())
            print(f"[{job.get('status')}, exit {job.get('exit_code')}]", file=sys.stderr)
        return 0 if job.get("status") == "done" else 1

    if args.action == "jobs":
        answer = hub.agent_jobs(args.machine)
        if args.json:
            print(json.dumps(answer, indent=2))
            return 0
        _print_jobs(answer.get("jobs") or [])
        for row in answer.get("satellites") or []:
            if row.get("source") != "live":
                note = "offline" if row.get("status") == "offline" else "didn't answer"
                print(f"({row.get('machine')}: {note}; showing {'its last heartbeat' if row.get('source') == 'cached' else 'nothing'})")
        return 0

    if len(args.rest) != 2:
        print(f"orbital agents {args.action}: needs <machine> <job>", file=sys.stderr)
        return 2
    machine, job_id = args.rest
    if args.action == "show":
        job = hub.agent_job(machine, job_id)
        if args.json:
            print(json.dumps(job, indent=2))
        else:
            print(f"{job.get('status')}  {job.get('agent')}  {job.get('title') or ''}  exit {job.get('exit_code')}")
            if job.get("output_tail"):
                print(job["output_tail"].rstrip())
        return 0
    if args.action == "log":
        answer = hub.agent_log(machine, job_id)
        if args.json:
            print(json.dumps(answer, indent=2))
        else:
            print((answer.get("log") or answer.get("text") or "").rstrip())
        return 0
    answer = hub.cancel_agent(machine, job_id)
    print(json.dumps(answer, indent=2) if args.json else f"{job_id}: {answer.get('status', 'cancel sent')}")
    return 0
