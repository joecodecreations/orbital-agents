"""Scheduler and watchdog.

The scheduler (`python -m orbital.scheduler`) runs every feed listed in ~/.orbital-agents/jobs.json
on its timetable and keeps the picture the hub's `/v1/jobs` endpoints serve. The watchdog
(`python -m orbital.scheduler.watchdog`) checks that every Orbital Agents service on this machine
answers, restarts the ones that don't, and trims logs and old data.
"""
