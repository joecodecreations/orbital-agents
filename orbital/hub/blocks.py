"""Field checks for the structured block types the apps add up across sections.

`product` feeds the Home screen, `ledger` feeds the Money view, `series` feeds the
dashboard charts, `insight_cards` feeds the top of Home, `videos` feeds the Videos
view, and `tasks` feeds the open to-dos, so a bad field there would show a wrong figure
across the whole app, not on one page. `buttons` and `hero` actions are checked so a
tap always has somewhere to go, and `progress`, `status` and `links` are checked so the numbers,
colours and links they draw are right. Other block types are only checked for type and id.
Shapes: schema/orbital.section.v1.schema.json.
"""

from __future__ import annotations

import math
import re

from orbital.core.limits import MAX_PROMPT_CHARS
from orbital.hub.ops import OpsError

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CURRENCY = re.compile(r"^[A-Z]{3,8}$")
TONES = {"info", "success", "warning", "danger"}
PRODUCT_KINDS = {"app", "game", "site", "service"}
RELEASE_STATUS = {"live", "pending", "testing", "draft", "rejected", "removed"}
IMAGE_PREFIXES = ("/v1/media/", "https://")


def check_image(value: object, label: str) -> None:
    """`icon_image` and similar: a /v1/media path or an https URL."""
    if value is None:
        return
    if not isinstance(value, str) or not value.startswith(IMAGE_PREFIXES) or len(value) > 2048:
        raise OpsError(f"{label} must be a /v1/media/ path or an https URL")


def check_block(block: dict, where: str) -> None:
    kind = block.get("type")
    if kind == "product":
        _check_product(block, f"product block {block.get('id')} on {where}")
    elif kind == "ledger":
        _check_ledger(block, f"ledger block {block.get('id')} on {where}")
    elif kind == "tasks":
        _check_tasks(block, f"tasks block {block.get('id')} on {where}")
    elif kind == "series":
        _check_series(block, f"series block {block.get('id')} on {where}")
    elif kind == "insight_cards":
        _check_insight_cards(block, f"insight_cards block {block.get('id')} on {where}")
    elif kind == "videos":
        _check_videos(block, f"videos block {block.get('id')} on {where}")
    elif kind == "progress":
        _check_progress(block, f"progress block {block.get('id')} on {where}")
    elif kind == "status":
        _check_status(block, f"status block {block.get('id')} on {where}")
    elif kind == "links":
        _check_links(block, f"links block {block.get('id')} on {where}")
    elif kind == "buttons":
        _check_buttons(block, f"buttons block {block.get('id')} on {where}")
    elif kind == "hero":
        _check_hero(block, f"hero block {block.get('id')} on {where}")


def _text(value: object, label: str, *, required: bool = False, limit: int = 200) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        need = "a non-empty string" if required else "a string"
        raise OpsError(f"{label} must be {need} of at most {limit} characters")


def _number(value: object, label: str, *, required: bool = False) -> None:
    if value is None and not required:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OpsError(f"{label} must be a JSON number, not {type(value).__name__}")
    if not math.isfinite(value):
        # NaN and Infinity would be stored, then break every JSON response that includes the section.
        raise OpsError(f"{label} must be a finite number")


def _items(value: object, label: str, limit: int) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise OpsError(f"{label} must be an array")
    if len(value) > limit:
        raise OpsError(f"{label} has more than {limit} items")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise OpsError(f"{label}[{index}] must be an object")
    return value


def _unique_ids(rows: list, label: str) -> None:
    seen: set[str] = set()
    for index, row in enumerate(rows):
        ident = row.get("id")
        if ident is None:
            continue
        if ident in seen:
            raise OpsError(f"{label}[{index}].id {ident!r} repeats an earlier id in the same block")
        seen.add(ident)


def _metrics(value: object, label: str) -> None:
    for index, item in enumerate(_items(value, label, 8)):
        _text(item.get("label"), f"{label}[{index}].label", required=True, limit=60)
        _text(item.get("value"), f"{label}[{index}].value", required=True, limit=60)
        _text(item.get("caption"), f"{label}[{index}].caption", limit=120)
        _text(item.get("delta"), f"{label}[{index}].delta", limit=24)
        direction = item.get("direction")
        if direction is not None and direction not in {"up", "down", "flat"}:
            raise OpsError(f"{label}[{index}].direction must be up, down, or flat")


def _check_product(block: dict, label: str) -> None:
    kind = block.get("kind")
    if kind is not None and kind not in PRODUCT_KINDS:
        raise OpsError(f"{label}: kind must be one of {', '.join(sorted(PRODUCT_KINDS))}")
    tone = block.get("tone")
    if tone is not None and tone not in TONES:
        raise OpsError(f"{label}: tone must be info, success, warning, or danger")
    _space(block, label)
    _text(block.get("headline"), f"{label}: headline", limit=200)
    _text(block.get("checked_at"), f"{label}: checked_at", limit=40)
    _unique_ids(_items(block.get("releases"), f"{label}: releases", 20), f"{label}: releases")
    _unique_ids(_items(block.get("reviews"), f"{label}: reviews", 50), f"{label}: reviews")
    for index, release in enumerate(_items(block.get("releases"), f"{label}: releases", 20)):
        where = f"{label}: releases[{index}]"
        _text(release.get("platform"), f"{where}.platform", required=True, limit=40)
        if release.get("status") not in RELEASE_STATUS:
            raise OpsError(f"{where}.status must be one of {', '.join(sorted(RELEASE_STATUS))}")
        for key in ("id", "store", "version", "build", "state", "since", "note"):
            _text(release.get(key), f"{where}.{key}", limit=200 if key == "note" else 80)
        url = release.get("url")
        if url is not None and (not isinstance(url, str) or not url.startswith("https://")):
            raise OpsError(f"{where}.url must be an https URL")
    for index, rating in enumerate(_items(block.get("ratings"), f"{label}: ratings", 6)):
        where = f"{label}: ratings[{index}]"
        _number(rating.get("value"), f"{where}.value", required=True)
        _number(rating.get("max"), f"{where}.max")
        count = rating.get("count")
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
            raise OpsError(f"{where}.count must be a whole number")
        _text(rating.get("store"), f"{where}.store", limit=40)
        _text(rating.get("label"), f"{where}.label", limit=40)
    for index, review in enumerate(_items(block.get("reviews"), f"{label}: reviews", 50)):
        where = f"{label}: reviews[{index}]"
        stars = review.get("rating")
        if stars is not None and (isinstance(stars, bool) or not isinstance(stars, int) or not 0 <= stars <= 5):
            raise OpsError(f"{where}.rating must be a whole number from 0 to 5")
        _text(review.get("at"), f"{where}.at", required=True, limit=40)
        _text(review.get("body"), f"{where}.body", limit=4000)
        for key in ("id", "store", "title", "author", "territory", "version"):
            _text(review.get(key), f"{where}.{key}", limit=200)
    _metrics(block.get("metrics"), f"{label}: metrics")
    for index, link in enumerate(_items(block.get("links"), f"{label}: links", 6)):
        where = f"{label}: links[{index}]"
        _text(link.get("label"), f"{where}.label", required=True, limit=40)
        url = link.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise OpsError(f"{where}.url must be an https URL")


def _space(row: dict, label: str, *, prefix: str = ": ") -> None:
    """`space` groups numbers across sections (Home, Money, charts). `company` is its older name."""
    for key in ("space", "company"):
        _text(row.get(key), f"{label}{prefix}{key}", limit=80)


def _tags(row: dict, where: str) -> None:
    for key in ("product", "category", "source", "space", "company"):
        _text(row.get(key), f"{where}.{key}", limit=80)


def _check_ledger(block: dict, label: str) -> None:
    currency = block.get("currency", "USD")
    if not isinstance(currency, str) or not CURRENCY.match(currency):
        raise OpsError(f"{label}: currency must be an uppercase code such as USD")
    connected = block.get("connected", True)
    if not isinstance(connected, bool):
        raise OpsError(f"{label}: connected must be true or false")
    _space(block, label)
    for key in ("title", "source", "note", "as_of", "timezone"):
        _text(block.get(key), f"{label}: {key}", limit=400 if key == "note" else 80)
    for index, day in enumerate(_items(block.get("days"), f"{label}: days", 3000)):
        where = f"{label}: days[{index}]"
        date = day.get("date")
        if not isinstance(date, str) or not DATE.match(date):
            raise OpsError(f"{where}.date must be YYYY-MM-DD")
        _number(day.get("amount"), f"{where}.amount", required=True)
        _number(day.get("net"), f"{where}.net")
        count = day.get("count")
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
            raise OpsError(f"{where}.count must be a whole number")
        _tags(day, where)
    for index, entry in enumerate(_items(block.get("entries"), f"{label}: entries", 200)):
        where = f"{label}: entries[{index}]"
        _text(entry.get("at"), f"{where}.at", required=True, limit=40)
        _number(entry.get("amount"), f"{where}.amount", required=True)
        _number(entry.get("net"), f"{where}.net")
        for key in ("id", "label", "status"):
            _text(entry.get(key), f"{where}.{key}", limit=120)
        _tags(entry, where)
    _metrics(block.get("metrics"), f"{label}: metrics")


# tasks: a checklist the app ticks (task.set_done) and adds to (task.add). The owner feed writes
# the taps back to its own source and pushes the list again.
TASK_STATUS = {"ready", "waiting", "blocked", "contingent", "later", "done"}


def _flag(value: object, label: str) -> None:
    if value is not None and not isinstance(value, bool):
        raise OpsError(f"{label} must be true or false")


def _day(value: object, label: str, *, required: bool = False) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not DATE.match(value):
        raise OpsError(f"{label} must be YYYY-MM-DD")


def _whole(value: object, label: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise OpsError(f"{label} must be a whole number")


def _check_tasks(block: dict, label: str) -> None:
    _text(block.get("title"), f"{label}: title", limit=120)
    _text(block.get("subtitle"), f"{label}: subtitle", limit=200)
    _text(block.get("empty_text"), f"{label}: empty_text", limit=200)
    _flag(block.get("collapsed"), f"{label}: collapsed")
    _whole(block.get("limit"), f"{label}: limit")
    add = block.get("add")
    if add is not None:
        if not isinstance(add, dict):
            raise OpsError(f"{label}: add must be an object")
        _text(add.get("label"), f"{label}: add.label", limit=40)
        _text(add.get("group"), f"{label}: add.group", limit=64)
        for index, row in enumerate(_items(add.get("groups"), f"{label}: add.groups", 60)):
            _text(row.get("id"), f"{label}: add.groups[{index}].id", required=True, limit=128)
            _text(row.get("label"), f"{label}: add.groups[{index}].label", required=True, limit=80)
        for index, row in enumerate(_items(add.get("remind_options"), f"{label}: add.remind_options", 8)):
            days = row.get("days")
            if not isinstance(days, list) or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 120 for d in days):
                raise OpsError(f"{label}: add.remind_options[{index}].days must be whole numbers from 0 to 120")
            _text(row.get("label"), f"{label}: add.remind_options[{index}].label", required=True, limit=60)
    items = _items(block.get("items"), f"{label}: items", 300)
    _unique_ids(items, f"{label}: items")
    for index, item in enumerate(items):
        where = f"{label}: items[{index}]"
        _text(item.get("id"), f"{where}.id", required=True, limit=128)
        _text(item.get("title"), f"{where}.title", required=True, limit=300)
        _day(item.get("due"), f"{where}.due")
        for key in ("group", "due_label", "done_at"):
            _text(item.get(key), f"{where}.{key}", limit=80)
        _text(item.get("note"), f"{where}.note", limit=4000)
        status = item.get("status")
        if status is not None and status not in TASK_STATUS:
            raise OpsError(f"{where}.status must be one of {', '.join(sorted(TASK_STATUS))}")
        for key in ("done", "can_toggle", "pending"):
            _flag(item.get(key), f"{where}.{key}")
        tags = item.get("tags")
        if tags is not None and (not isinstance(tags, list) or len(tags) > 8 or any(not isinstance(t, str) or len(t) > 24 for t in tags)):
            raise OpsError(f"{where}.tags must be up to 8 short strings")


# series: daily numbers for one thing you watch, drawn as one chart with toggles and added up
# across sections by kind, space and product.
SERIES_KINDS = {"social", "site", "usage", "players", "search", "health", "money", "other"}
SERIES_FORMATS = {"number", "usd", "percent", "duration"}


def _check_series(block: dict, label: str) -> None:
    kind = block.get("kind")
    if kind not in SERIES_KINDS:
        raise OpsError(f"{label}: kind must be one of {', '.join(sorted(SERIES_KINDS))}")
    _space(block, label)
    _text(block.get("product"), f"{label}: product", required=True, limit=80)
    _text(block.get("site"), f"{label}: site", limit=120)
    _text(block.get("title"), f"{label}: title", limit=120)
    _text(block.get("caption"), f"{label}: caption", limit=300)
    _text(block.get("as_of"), f"{label}: as_of", limit=40)
    window = block.get("window")
    if window is not None and (isinstance(window, bool) or not isinstance(window, int) or not 1 <= window <= 90):
        raise OpsError(f"{label}: window must be a whole number of days from 1 to 90")
    metrics = _items(block.get("metrics"), f"{label}: metrics", 60)
    if not metrics:
        raise OpsError(f"{label}: metrics must hold at least one metric")
    _unique_ids(metrics, f"{label}: metrics")
    for index, metric in enumerate(metrics):
        where = f"{label}: metrics[{index}]"
        _text(metric.get("id"), f"{where}.id", required=True, limit=64)
        _text(metric.get("label"), f"{where}.label", required=True, limit=60)
        _text(metric.get("platform"), f"{where}.platform", limit=24)
        _text(metric.get("stat"), f"{where}.stat", limit=32)
        _text(metric.get("group"), f"{where}.group", limit=40)
        fmt = metric.get("format")
        if fmt is not None and fmt not in SERIES_FORMATS:
            raise OpsError(f"{where}.format must be one of {', '.join(sorted(SERIES_FORMATS))}")
        _flag(metric.get("level"), f"{where}.level")
        points = _items(metric.get("points"), f"{where}.points", 120)
        seen: set[str] = set()
        for row, point in enumerate(points):
            _day(point.get("x"), f"{where}.points[{row}].x", required=True)
            _number(point.get("y"), f"{where}.points[{row}].y", required=True)
            if point["x"] in seen:
                raise OpsError(f"{where}.points[{row}].x {point['x']} repeats an earlier day")
            seen.add(point["x"])


# insight_cards: findings an owner feed raises, each with actions the user approves in the app.
# Home shows the open ones above everything else.
INSIGHT_SEVERITY = {"critical", "high", "medium", "low", "good"}
INSIGHT_STATE = {"new", "pending", "in_progress", "waiting", "done", "failed", "snoozed", "dismissed", "resolved"}
INSIGHT_ACTION_KINDS = {"email", "agent", "note"}
INSIGHT_ACTION_STATE = {"ready", "pending", "running", "waiting", "done", "failed", "blocked"}
INSIGHT_CARD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _check_insight_action(action: dict, where: str) -> None:
    _text(action.get("id"), f"{where}.id", required=True, limit=40)
    if action.get("kind") not in INSIGHT_ACTION_KINDS:
        raise OpsError(f"{where}.kind must be one of {', '.join(sorted(INSIGHT_ACTION_KINDS))}")
    _text(action.get("label"), f"{where}.label", required=True, limit=40)
    _text(action.get("approve_label"), f"{where}.approve_label", limit=40)
    state = action.get("state")
    if state is not None and state not in INSIGHT_ACTION_STATE:
        raise OpsError(f"{where}.state must be one of {', '.join(sorted(INSIGHT_ACTION_STATE))}")
    for key, limit in (("status_text", 400), ("note", 600), ("blocked_reason", 400), ("digest", 80), ("updated_at", 40)):
        _text(action.get(key), f"{where}.{key}", limit=limit)
    email = action.get("email")
    if email is not None:
        if not isinstance(email, dict):
            raise OpsError(f"{where}.email must be an object")
        for key, limit, required in (("to", 200, True), ("from", 200, False), ("reply_to", 200, False),
                                     ("subject", 160, True), ("body", 6000, True)):
            _text(email.get(key), f"{where}.email.{key}", required=required, limit=limit)
    brief = action.get("brief")
    if brief is not None:
        if not isinstance(brief, dict):
            raise OpsError(f"{where}.brief must be an object")
        _text(brief.get("prompt"), f"{where}.brief.prompt", required=True, limit=20000)
        for key in ("title", "repo", "branch", "worktree", "session_id", "resume", "log", "tracker_id", "commits"):
            _text(brief.get(key), f"{where}.brief.{key}", limit=400)
    if action.get("kind") == "email" and email is None and action.get("state") != "blocked":
        raise OpsError(f"{where}: an email action needs email (to, subject, body) or state blocked")
    if action.get("kind") == "agent" and brief is None:
        raise OpsError(f"{where}: an agent action needs brief.prompt")


def _check_insight_cards(block: dict, label: str) -> None:
    _space(block, label)
    for key, limit in (("title", 120), ("as_of", 40), ("next_run", 40), ("note", 400), ("empty_text", 200)):
        _text(block.get(key), f"{label}: {key}", limit=limit)
    cards = _items(block.get("cards"), f"{label}: cards", 80)
    _unique_ids(cards, f"{label}: cards")
    for index, card in enumerate(cards):
        where = f"{label}: cards[{index}]"
        ident = card.get("id")
        if not isinstance(ident, str) or not INSIGHT_CARD_ID.match(ident):
            raise OpsError(f"{where}.id must be 1-128 letters, numbers, or . _ : - (it is the action item_id)")
        _text(card.get("kind"), f"{where}.kind", required=True, limit=40)
        if card.get("severity") not in INSIGHT_SEVERITY:
            raise OpsError(f"{where}.severity must be one of {', '.join(sorted(INSIGHT_SEVERITY))}")
        state = card.get("state")
        if state is not None and state not in INSIGHT_STATE:
            raise OpsError(f"{where}.state must be one of {', '.join(sorted(INSIGHT_STATE))}")
        _text(card.get("title"), f"{where}.title", required=True, limit=160)
        for key, limit in (("summary", 400), ("product", 80), ("deadline_label", 40), ("count_label", 40),
                           ("status_text", 400), ("first_seen", 40), ("updated_at", 40), ("snoozed_until", 40)):
            _text(card.get(key), f"{where}.{key}", limit=limit)
        _day(card.get("deadline"), f"{where}.deadline")
        _whole(card.get("count"), f"{where}.count")
        _flag(card.get("pending"), f"{where}.pending")
        person = card.get("person")
        if person is not None:
            if not isinstance(person, dict):
                raise OpsError(f"{where}.person must be an object")
            for key in ("label", "email", "user_id", "brand"):
                _text(person.get(key), f"{where}.person.{key}", limit=200)
        evidence = card.get("evidence")
        if evidence is not None and (
            not isinstance(evidence, list) or len(evidence) > 16
            or any(not isinstance(line, str) or len(line) > 400 for line in evidence)
        ):
            raise OpsError(f"{where}.evidence must be up to 16 strings of at most 400 characters")
        _metrics(card.get("metrics"), f"{where}.metrics")
        for row, link in enumerate(_items(card.get("links"), f"{where}.links", 4)):
            _text(link.get("label"), f"{where}.links[{row}].label", required=True, limit=40)
            url = link.get("url")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise OpsError(f"{where}.links[{row}].url must be an https URL")
        actions = _items(card.get("actions"), f"{where}.actions", 4)
        _unique_ids(actions, f"{where}.actions")
        for row, action in enumerate(actions):
            _check_insight_action(action, f"{where}.actions[{row}]")


# videos: one channel's videos and the public link of every post. The Videos view lists
# every block of this type by day.
VIDEO_STATUS = {"posted", "posting", "checking", "failed", "removed"}
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")


def _stamp(value: object, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not STAMP.match(value):
        raise OpsError(f"{label} must be an ISO 8601 time like 2026-10-11T22:24:57Z")


def _https(value: object, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.startswith("https://") or len(value) > 600:
        raise OpsError(f"{label} must be an https URL of at most 600 characters")


def _check_videos(block: dict, label: str) -> None:
    _space(block, label)
    _text(block.get("product"), f"{label}: product", required=True, limit=80)
    _text(block.get("title"), f"{label}: title", limit=120)
    _text(block.get("timezone"), f"{label}: timezone", limit=64)
    _text(block.get("note"), f"{label}: note", limit=300)
    _stamp(block.get("as_of"), f"{label}: as_of")
    links = _items(block.get("experiments"), f"{label}: experiments", 6)
    _unique_ids(links, f"{label}: experiments")
    for index, link in enumerate(links):
        where = f"{label}: experiments[{index}]"
        _text(link.get("id"), f"{where}.id", required=True, limit=64)
        _text(link.get("label"), f"{where}.label", required=True, limit=60)
        _text(link.get("detail"), f"{where}.detail", limit=160)
        _https(link.get("url"), f"{where}.url")
        action = link.get("action")
        if action is not None:
            _check_tap(action, f"{where}.action")
        if link.get("url") is None and action is None:
            raise OpsError(f"{where} needs an https url or an action")
    items = _items(block.get("items"), f"{label}: items", 400)
    _unique_ids(items, f"{label}: items")
    for index, item in enumerate(items):
        where = f"{label}: items[{index}]"
        _text(item.get("id"), f"{where}.id", required=True, limit=80)
        _text(item.get("title"), f"{where}.title", required=True, limit=160)
        _text(item.get("caption"), f"{where}.caption", limit=400)
        _text(item.get("file"), f"{where}.file", limit=300)
        _text(item.get("experiment"), f"{where}.experiment", limit=160)
        _stamp(item.get("made_at"), f"{where}.made_at")
        check_image(item.get("thumbnail"), f"{where}.thumbnail")
        _number(item.get("duration"), f"{where}.duration")
        tags = item.get("tags")
        if tags is not None and (not isinstance(tags, list) or len(tags) > 8 or any(not isinstance(t, str) or len(t) > 32 for t in tags)):
            raise OpsError(f"{where}.tags must be up to 8 strings of at most 32 characters")
        for row, post in enumerate(_items(item.get("posts"), f"{where}.posts", 20)):
            at = f"{where}.posts[{row}]"
            _text(post.get("platform"), f"{at}.platform", required=True, limit=24)
            status = post.get("status", "posted")
            if status not in VIDEO_STATUS:
                raise OpsError(f"{at}.status must be one of {', '.join(sorted(VIDEO_STATUS))}")
            _https(post.get("url"), f"{at}.url")
            _text(post.get("post_id"), f"{at}.post_id", limit=120)
            _text(post.get("account"), f"{at}.account", limit=80)
            _text(post.get("note"), f"{at}.note", limit=200)
            _stamp(post.get("at"), f"{at}.at")
            _number(post.get("views"), f"{at}.views")


# Buttons: where a tap goes. `post` sends an action back to the section's owner feed
# (POST /v1/sections/{id}/actions), which reads it with GET .../actions and answers with results.
BUTTON_STYLES = {"primary", "secondary", "link", "destructive"}
TAP_TYPES = {"agent", "open", "url", "post", "run_agent"}
# Other names the app also understands for the same taps.
TAP_ALIASES = {"desk": "agent", "run": "run_agent"}
ACTION_TYPE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


def _check_tap(action: object, where: str) -> None:
    if not isinstance(action, dict) or (action.get("type") not in TAP_TYPES and action.get("type") not in TAP_ALIASES):
        raise OpsError(f"{where}.type must be one of {', '.join(sorted(TAP_TYPES))}")
    kind = TAP_ALIASES.get(action["type"], action["type"])
    if kind == "open":
        _text(action.get("section_id"), f"{where}.section_id", required=True, limit=64)
    elif kind == "url":
        _https(action.get("url"), f"{where}.url")
        if action.get("url") is None:
            raise OpsError(f"{where}.url is required")
    elif kind == "agent":
        _text(action.get("prompt"), f"{where}.prompt", required=True, limit=4000)
    elif kind == "post":
        name = action.get("action")
        if not isinstance(name, str) or not ACTION_TYPE.match(name):
            raise OpsError(f"{where}.action must be a dotted lowercase action type such as server.restart")
        value = action.get("value")
        if value is not None and not isinstance(value, dict):
            raise OpsError(f"{where}.value must be an object")
        _text(action.get("confirm"), f"{where}.confirm", limit=200)
    elif kind == "run_agent":
        # Opens Run agent in the app, filled in; the person checks it and starts it.
        for key, limit in (("machine", 64), ("agent", 40), ("cwd", 1000), ("title", 120)):
            _text(action.get(key), f"{where}.{key}", limit=limit)
        _text(action.get("prompt"), f"{where}.prompt", limit=MAX_PROMPT_CHARS)


def _check_button(item: dict, where: str) -> None:
    _text(item.get("label"), f"{where}.label", required=True, limit=40)
    style = item.get("style", "primary")
    if style not in BUTTON_STYLES:
        raise OpsError(f"{where}.style must be one of {', '.join(sorted(BUTTON_STYLES))}")
    url = item.get("url")
    if url is not None and (not isinstance(url, str) or not url.startswith(("https://", "http://")) or len(url) > 600):
        raise OpsError(f"{where}.url must be an http(s) URL of at most 600 characters")
    _flag(item.get("pending"), f"{where}.pending")
    if item.get("action") is not None:
        _check_tap(item["action"], f"{where}.action")


def _check_buttons(block: dict, label: str) -> None:
    items = _items(block.get("items"), f"{label}: items", 6)
    _unique_ids(items, f"{label}: items")
    for index, item in enumerate(items):
        _check_button(item, f"{label}: items[{index}]")


def _check_hero(block: dict, label: str) -> None:
    actions = _items(block.get("actions"), f"{label}: actions", 3)
    _unique_ids(actions, f"{label}: actions")
    for index, item in enumerate(actions):
        _check_button(item, f"{label}: actions[{index}]")


# progress: labelled bars (disk use, a goal, a backup). `value` out of `max` (default 100).
def _check_progress(block: dict, label: str) -> None:
    _text(block.get("title"), f"{label}: title", limit=120)
    items = _items(block.get("items"), f"{label}: items", 30)
    _unique_ids(items, f"{label}: items")
    for index, item in enumerate(items):
        where = f"{label}: items[{index}]"
        _text(item.get("id"), f"{where}.id", limit=64)
        _text(item.get("label"), f"{where}.label", required=True, limit=80)
        _number(item.get("value"), f"{where}.value", required=True)
        _number(item.get("max"), f"{where}.max")
        if item.get("max") is not None and item["max"] <= 0:
            raise OpsError(f"{where}.max must be more than 0")
        _text(item.get("display"), f"{where}.display", limit=40)
        _text(item.get("caption"), f"{where}.caption", limit=120)
        tone = item.get("tone")
        if tone is not None and tone not in TONES:
            raise OpsError(f"{where}.tone must be info, success, warning, or danger")


# status: up / down rows (services on a server, sites behind a monitor). Home lists the blocks
# with something down or degraded. Any word works; these are the ones the app colours.
STATUS_WORDS = {
    "up", "ok", "online", "healthy", "running", "pass", "passing", "green",
    "down", "error", "offline", "failed", "fail", "failing", "red", "critical",
    "degraded", "warn", "warning", "slow", "partial", "yellow", "unknown",
}


def _check_status(block: dict, label: str) -> None:
    _text(block.get("title"), f"{label}: title", limit=120)
    items = _items(block.get("items"), f"{label}: items", 100)
    _unique_ids(items, f"{label}: items")
    for index, item in enumerate(items):
        where = f"{label}: items[{index}]"
        _text(item.get("id"), f"{where}.id", limit=64)
        _text(item.get("label"), f"{where}.label", required=True, limit=80)
        status = item.get("status")  # missing reads as unknown
        if status is not None and (not isinstance(status, str) or status.strip().lower() not in STATUS_WORDS):
            raise OpsError(f"{where}.status must be a status word such as up, down, degraded, or unknown")
        _text(item.get("detail"), f"{where}.detail", limit=200)
        _stamp(item.get("since"), f"{where}.since")
        _link(item.get("url"), f"{where}.url")


# links: a short list of places to go (a dashboard, docs, a status page).
LINK_SCHEME = re.compile(r"^(https?://|orbital://|mailto:|tel:|ssh://|vnc://|smb://)", re.I)


def _link(value: object, label: str, *, required: bool = False) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not LINK_SCHEME.match(value) or len(value) > 600:
        raise OpsError(f"{label} must be an https, http, orbital, mailto, tel, ssh, vnc, or smb link of at most 600 characters")


def _check_links(block: dict, label: str) -> None:
    _text(block.get("title"), f"{label}: title", limit=120)
    items = _items(block.get("items"), f"{label}: items", 30)
    seen: set[str] = set()
    for index, item in enumerate(items):
        where = f"{label}: items[{index}]"
        _link(item.get("url"), f"{where}.url", required=True)
        if item["url"] in seen:
            raise OpsError(f"{where}.url repeats an earlier link in the same block")
        seen.add(item["url"])
        _text(item.get("label"), f"{where}.label", limit=80)
        _text(item.get("detail"), f"{where}.detail", limit=200)
        _text(item.get("icon"), f"{where}.icon", limit=64)
