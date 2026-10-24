"""What a feed is allowed to send, and how to repair a rejected one.

The apps render APP_BLOCKS. Any other block type is stored and skipped on screen, and
POST /v1/sections/{id}/validate warns about it.
"""

from __future__ import annotations

from orbital import __version__

APP_BLOCKS: list[tuple[str, str]] = [
    ("heading", "Title inside the page. Level 1, 2, or 3."),
    ("paragraph", "Markdown prose."),
    ("bullets", "A short list."),
    ("callout", "One notice: info, success, warning, or danger."),
    ("metrics", "A few large figures, each with an optional up or down direction."),
    ("key_value", "Label and value rows."),
    ("table", "Columns and rows."),
    ("timeline", "Dated events."),
    ("divider", "A visual break with no data."),
    ("chart", "line, area, bar, or scatter. Send real points only."),
    ("image", "A /v1/media path or an https URL."),
    ("hero", "One dominant title, short text, and up to three buttons."),
    ("buttons", "Up to six buttons. A tap opens a URL or a page, prefills the Desk (agent), opens Run agent filled in (run_agent), or posts an action back to the feed (post)."),
    ("progress", "Labelled bars: disk use, a goal, a backup. value out of max (default 100), with an optional display text and tone."),
    ("status", "Up / down rows: services, sites, devices. Each row has a status word, detail, since, and an optional link. Home lists the ones down or degraded."),
    ("links", "A short list of places to go: label, url, detail, and an SF Symbol icon."),
    ("product", "Status card for one app, game, site, or service: releases, ratings, reviews, headline metrics. Feeds the Home screen."),
    ("ledger", "Money for one source: daily rows tagged by space, product, category, and source, plus recent entries. Feeds the Money view."),
    ("subscriptions", "Subscriptions for one product: each one's plan, status, price, start, trial and renewal dates, plus failed payments. Feeds the Money and product pages."),
    ("tasks", "A checklist the app can tick (task.set_done) and add to (task.add). The owner feed writes taps back to its source."),
    ("series", "Daily numbers for one product, tagged kind, space, and product. Draws as one line chart with toggles. Feeds the dashboard charts."),
    ("insight_cards", "Findings a feed raised, one card each: severity, evidence, and up to four actions (an email preview or an agent brief) the user approves in the app."),
    ("videos", "A set of videos, each with a title, thumbnail, and the links where it can be watched, with counts. Feeds the Videos view."),
]

# Blocks the apps also read across every section, not only on their own page.
AGGREGATED_BLOCKS: dict[str, str] = {
    "product": "Home: one card per product block, pending releases, latest reviews, stale checks.",
    "ledger": "Money: totals for today, this week, and this month, a chart, and breakdowns by space, product, category, and source.",
    "subscriptions": "Money and product pages: subscribers, MRR, trials, new, cancelled, churned, failed payments, and the list of subscriptions, per product.",
    "tasks": "Home: open items across every tasks block, deduped by id, most urgent first.",
    "series": "Dashboard charts: numbers of the same kind are added up across sections (by metric, per product). Toggles filter by space and product.",
    "status": "Home: every status block with a row down or degraded, with the rows that need a look.",
    "insight_cards": "Home, very top: every open card (state new, pending, in_progress, waiting, failed) across every insight_cards block, most severe first.",
    "videos": "Videos view: the videos from every videos block, grouped by day, newest first, with a chart of how many came out each day.",
}

STALE_AFTER_HOURS = 36

_HEAL = {
    "unauthorized": [
        "Read ORBITAL_AUTOMATION_TOKEN from ~/.orbital-agents/secrets.env (or the environment).",
        "Send Authorization: Bearer <token>. Do not print the token.",
        "Retry the same request.",
    ],
    "bad_login": [
        "This route is the app sign-in, not the automation token.",
        "Feeds and scripts use the bearer token. The app uses ORBITAL_HUB_USERNAME and ORBITAL_HUB_PASSWORD.",
    ],
    "not_found": [
        "GET /v1/catalog and confirm the section id.",
        "If this id is yours and it has never been created, PUT /v1/sections/{id} once with a valid orbital.section.v1 document.",
        "Do not recreate an id that belongs to another feed.",
    ],
    "revision_conflict": [
        "GET /v1/sections/{id} and read revision.",
        "Rebuild the op against that document. Keep your block and node ids stable.",
        "Retry with if_revision set to the hub's revision.",
        "Do not retry replace_section or delete_section automatically. Those need a fresh read.",
    ],
    "bad_schema": [
        "Section documents use schema orbital.section.v1.",
        "Ops batches use schema orbital.ops.v1.",
        "Alerts use schema orbital.alert.v1.",
        "GET /v1/contract if those names have changed.",
    ],
    "unknown_op": [
        "GET /v1/contract and use an op name from ops.",
        "Address blocks with node_id and tab_id the way that section's layout requires.",
    ],
    "invalid_value": [
        "GET /v1/contract for ids, layouts, and limits.",
        "Fix the field named in error. Ids match ^[a-z0-9][a-z0-9-]{0,63}$.",
        "Retry the same op. Do not invent a second payload shape.",
    ],
    "too_large": [
        "Keep the section under 2 MB and one media file under the hub's max_media_mb.",
        "Trim chart points or split a huge table across nodes you own.",
    ],
    "forbidden": [
        "This hub only answers the networks in hub.allow_networks (loopback and Tailscale by default).",
        "Connect over Tailscale, or add your network to hub.allow_networks in config.toml and restart the hub.",
    ],
    "unavailable": [
        "The machine or service this request goes to did not answer.",
        "Check it with `orbital satellites` or `orbital status`, then retry.",
    ],
    "not_configured": [
        "This feature needs a setting or key that is not set yet.",
        "Run ./setup again, or add the key named in error to ~/.orbital-agents/secrets.env and restart the hub.",
    ],
    "rejected": [
        "GET /v1/contract and GET /v1/sections/{id}.",
        "Fix the field named in error, then retry.",
        "POST /v1/sections/{id}/validate to check without writing.",
    ],
}


def app_type_names() -> list[str]:
    return [name for name, _summary in APP_BLOCKS]


def infer_code(message: str, status: int) -> str:
    text = message.lower()
    if status == 401:
        return "unauthorized"
    if status == 403:
        return "forbidden"
    if status == 404 or "unknown section" in text or text == "not found":
        return "not_found"
    if status == 409 or "revision conflict" in text:
        return "revision_conflict"
    if status == 413 or "larger than" in text:
        return "too_large"
    if status in (502, 504):
        return "unavailable"
    if status == 503:
        return "not_configured"
    if "schema must" in text or "bad_schema" in text:
        return "bad_schema"
    if "unknown op" in text:
        return "unknown_op"
    if text.startswith(("invalid", "duplicate")) or " must " in text or "needs " in text or "omit " in text or "required" in text or "repeats" in text:
        return "invalid_value"
    return "rejected"


def error_body(
    message: str,
    status: int = 400,
    *,
    code: str | None = None,
    section_id: str | None = None,
    revision: int | None = None,
    include_revision: bool = False,
) -> dict:
    resolved = code or infer_code(message, status)
    body: dict = {
        "ok": False,
        "error": message,
        "detail": message,
        "code": resolved,
        "status": status,
        "hint": message,
        "heal": list(_HEAL.get(resolved, _HEAL["rejected"])),
        "contract": "/v1/contract",
    }
    if section_id:
        body["section_id"] = section_id
        body["section_url"] = f"/v1/sections/{section_id}"
        body["validate_url"] = f"/v1/sections/{section_id}/validate"
    if include_revision or resolved == "revision_conflict":
        body["revision"] = revision
    return body


def render_warnings(doc: dict | None) -> list[dict]:
    if not doc:
        return []
    known = set(app_type_names())
    warnings = []
    for block in walk_blocks(doc):
        block_type = block.get("type")
        if isinstance(block_type, str) and block_type not in known:
            warnings.append(
                {
                    "code": "not_rendered",
                    "block_id": block.get("id"),
                    "type": block_type,
                    "hint": (
                        f"The apps store type {block_type!r} and do not draw it. "
                        "Write the same facts in a block from app_renders as well."
                    ),
                }
            )
    return warnings


def walk_blocks(doc: dict) -> list[dict]:
    found: list[dict] = []

    def take(blocks: object) -> None:
        if isinstance(blocks, list):
            found.extend(block for block in blocks if isinstance(block, dict))

    take(doc.get("blocks"))
    for tab in doc.get("tabs") or []:
        if isinstance(tab, dict):
            take(tab.get("blocks"))
    for node in doc.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        take(node.get("blocks"))
        for tab in node.get("tabs") or []:
            if isinstance(tab, dict):
                take(tab.get("blocks"))
    return found


def build_contract(catalog: dict, max_media_mb: int = 8) -> dict:
    sections = [
        {**row, "scope": "Created by a feed. Only that feed writes it."}
        for row in catalog.get("sections") or []
    ]
    return {
        "schema": "orbital.contract.v1",
        "service": "orbital-hub",
        "version": __version__,
        "scheduler": {
            "registry": "~/.orbital-agents/jobs.json",
            "jobs": "/v1/jobs",
            "rule": "Feeds run from the Orbital scheduler. Add a row to jobs.json (or use the feed's own schedule); the hub never runs a feed itself.",
        },
        "versions": {
            "section": "orbital.section.v1",
            "ops": "orbital.ops.v1",
            "alert": "orbital.alert.v1",
            "action": "orbital.action.v1",
            "pulse": "orbital.pulse.v1",
        },
        "limits": {
            "section_bytes": 2_000_000,
            "nodes": 500,
            "tabs": 30,
            "blocks": 200,
            "chart_points_guidance": 400,
            "media_bytes": max_media_mb * 1024 * 1024,
            "alert_actions": 4,
            "stale_after_hours": STALE_AFTER_HOURS,
        },
        "layouts": ["page", "tabs", "sidebar"],
        "node_layouts": ["page", "tabs"],
        "layout_rules": {
            "page": "section.blocks is the page.",
            "tabs": "section.blocks are drawn above the tabs on every tab; each tab has its own blocks.",
            "sidebar": "section.blocks are drawn above the node's content on every node; each node is a page or tabs.",
            "ops": "An op without node_id and tab_id addresses section.blocks on any layout. Name tab_id (and node_id on sidebar) to reach a tab or node.",
        },
        "ops": [
            "merge_section",
            "merge_node",
            "replace_section",
            "delete_section",
            "upsert_node",
            "delete_node",
            "reorder_nodes",
            "upsert_tab",
            "delete_tab",
            "reorder_tabs",
            "upsert_block",
            "delete_block",
            "reorder_blocks",
        ],
        "app_renders": app_type_names(),
        "aggregated_blocks": AGGREGATED_BLOCKS,
        "spaces": "Set space (an id), space_title, and space_icon on a section to group it into one of the app's home tiles. GET /v1/catalog lists them under spaces.",
        "actions": {
            "post": "POST /v1/sections/{id}/actions with {schema: orbital.action.v1, type, item_id?, block_id?, node_id?, tab_id?, value}",
            "types": [
                "task.set_done {done}", "task.add {title, group?, due?, remind_days?, note?}",
                "insight.email.approve {action_id, digest?}", "insight.agent.approve {action_id, digest?}", "insight.snooze {days}", "insight.dismiss {}",
                "any.dotted.type {...}: a button with action {type: post, action, value} sends it; the button shows pending until the owner answers",
                "(no post) a button with action {type: run_agent, machine?, agent?, prompt?, cwd?, title?} opens Run agent in the app, filled in",
            ],
            "owner": "GET /v1/sections/{id}/actions?status=pending, apply to your source, POST /v1/sections/{id}/actions/results, then push the rebuilt section.",
            "wake": "data/hub/actions/{id}.json changes on every post and result. A feed can watch it, or poll on its schedule.",
            "retract": "POST /v1/alerts/retract {ids} cancels scheduled reminders in the apps.",
            "ack": "POST /v1/alerts/ack {ids} marks alerts done on every device (broadcast alerts_acked; sync lists acked).",
        },
        "realtime": {
            "post": "POST /v1/pulse with {schema: orbital.pulse.v1, items: [...], remove: [keys]}",
            "read": "GET /v1/pulse. Also in GET /v1/sync as pulse, and pushed over /v1/live as {type: pulse}",
            "item": "key, section_id, node_id?, label, value (number), display?, detail?, unit?, platform?, source?, at?, ttl_seconds (10-86400, default 180), order?, series? ([{x, y}], at most 120)",
            "rule": "One key per live number. Post the newest value each poll. A pulse never bumps a section revision and never notifies.",
        },
        "fields": {
            "icon_image": (
                "Optional on a section and on a node. A /v1/media/ path or an https URL. "
                "The apps show it in place of the SF Symbol in icon. Upload a local file with POST /v1/media; "
                "the URL is a content hash and stays the same across runs."
            ),
            "merge_node": "Change title, subtitle, icon, icon_image, or order on one node without resending its tabs.",
        },
        "block_types": [{"type": name, "app": True, "summary": summary} for name, summary in APP_BLOCKS],
        "sections": sections,
        "rules": [
            "Write only your section id. A new subject gets a new id, not a corner of someone else's section.",
            "Prefer ops that name ids. Use replace_section only when the whole section should be rewritten.",
            "Send real observations. Leave a callout when a source has no data yet. Do not invent metrics.",
            "Put the as-of time on the page. A quiet run still updates a checked-at line so the page does not look abandoned.",
            "Use a chart for a series, a table for rows, metrics for a handful of figures.",
            "Alert only when someone would act or would be sorry to miss it. A refresh is not an alert.",
            "Keep ids stable. A new server, repo, or site is a new node.",
            "On HTTP 400, follow heal and fix the payload. The hub will not guess a different shape.",
            "On HTTP 409, id-addressed ops may be retried once at the hub's revision. replace_section and delete_section may not.",
            "Compose the existing block types before adding one. A new type is a change to the hub and the apps.",
            "Never put secrets, tokens, or private paths in a section.",
        ],
        "each_run": [
            "GET /v1/sections/{your-id} (or keep your own ids stable and just send ops)",
            "Gather from the real source",
            "PUT the section or POST ops for your id only",
            "If the response is not ok, follow heal and run again",
        ],
        "errors": {
            "shape": "ok, error (also as detail), code, status, hint, heal, contract, and sometimes section_id, revision",
            "validate": "POST /v1/sections/{id}/validate checks an ops batch or a section document and does not write",
        },
    }
