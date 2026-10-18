"""Limits every component agrees on, so a request the hub accepts is never refused further down."""

from __future__ import annotations

# Longest agent prompt anyone may send: the hub relay, the satellite's HTTP model and its job
# runner all use this. Longer prompts are refused with 413.
MAX_PROMPT_CHARS = 100_000
