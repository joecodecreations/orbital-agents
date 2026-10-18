"""To-dos and reminders from the Desk go to Apple Reminders, which iCloud syncs to the phone.

macOS only. The list is desk.reminders_list in config.toml (empty: the default list).
Text goes to AppleScript as argv, never spliced into the script, so a title cannot run code.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime

TODO_LIST = ""

# argv: title, notes, list name, due (yyyy-mm-dd HH:MM or empty)
_SCRIPT = r'''
on run argv
  set theTitle to item 1 of argv
  set theNotes to item 2 of argv
  set listName to item 3 of argv
  set dueText to item 4 of argv
  tell application "Reminders"
    if listName is not "" and (exists list listName) then
      set targetList to list listName
    else
      set targetList to default list
    end if
    set props to {name:theTitle}
    if theNotes is not "" then set props to props & {body:theNotes}
    set newReminder to make new reminder at end of reminders of targetList with properties props
    if dueText is not "" then
      set y to (text 1 thru 4 of dueText) as integer
      set mo to (text 6 thru 7 of dueText) as integer
      set d to (text 9 thru 10 of dueText) as integer
      set h to (text 12 thru 13 of dueText) as integer
      set mi to (text 15 thru 16 of dueText) as integer
      set dueDate to current date
      set day of dueDate to 1
      set year of dueDate to y
      set month of dueDate to mo
      set day of dueDate to d
      set time of dueDate to (h * 3600 + mi * 60)
      set due date of newReminder to dueDate
      set remind me date of newReminder to dueDate
    end if
    return (name of targetList)
  end tell
end run
'''


class RemindersError(RuntimeError):
    pass


def parse_due(raw: str | None) -> datetime | None:
    """ISO date or datetime from the model, as local wall time. A bare date means 9 AM."""
    if not raw:
        return None
    text = str(raw).strip()
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    if len(text) == 10:
        value = value.replace(hour=9)
    return value


def add(title: str, notes: str = "", due: datetime | None = None, list_name: str = TODO_LIST) -> str:
    """Add one reminder. Returns the list it landed in."""
    if sys.platform != "darwin":
        raise RemindersError("Apple Reminders is only on macOS")
    title = " ".join(title.split())[:200]
    if not title:
        raise RemindersError("empty title")
    due_text = due.strftime("%Y-%m-%d %H:%M") if due else ""
    try:
        out = subprocess.run(
            ["osascript", "-", title, notes[:2000], list_name, due_text],
            input=_SCRIPT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemindersError(str(exc)) from exc
    if out.returncode != 0:
        raise RemindersError((out.stderr or "Reminders refused").strip()[:300])
    return out.stdout.strip() or list_name
