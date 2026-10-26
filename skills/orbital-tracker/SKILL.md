---
name: orbital-tracker
description: Report what you are working on to the Orbital Agents - Agent Satellite Tracker (floating bubbles on the user's Mac, mirrored in the Orbital Agents app) with the orbital-track CLI. Load it at the start of any multi-step task, when posting progress or screenshots, before finishing UI / website / game work (the Off / Min 1 / All screenshot evidence level and how to capture), when finishing a task, and when spawning a subagent or when your prompt contains ORBITAL_TRACK_PARENT.
---

# Agent Satellite Tracker: report your work

The user runs many agents at once, often on several machines. A floating panel on their Mac shows one bubble per piece of work, with the agent's icon, details, progress notes, screenshots, subagents and the machine it runs on. The Orbital Agents app on their phone shows the same items. Finished items move to a history.

Always pass `--agent` with your own short name: `claude`, `codex`, `grok`, `gemini`, or the name of your tool.

## Main agent: start, update, finish

```bash
# 1. Start real work. Prints the ITEM ID (stdout is only the id): keep it in your context,
#    shell variables do not survive between tool calls.
orbital-track start --agent claude --title "Fix Login Redirect" --progress 5 \
  --details "Login sends users to /404 after OAuth. Fixing in **web/app**."

# 2. Every milestone: a note AND --progress (how much of the WHOLE job is done, 0-100)
orbital-track update k7m2x9qa --note "Root cause: callback URL built from the wrong env var" --progress 40
orbital-track update k7m2x9qa --note "Fix in, before and after" --progress 75 --media /abs/before.png --media /abs/after.png

# 3. Finish, or stop. Always close the item.
orbital-track done k7m2x9qa --summary "Fixed the redirect in \`auth/callback.ts\`; added a test. Deploy pending." \
  --media /abs/final.png
```

- **Percent complete is required.** The tracker and the phone draw a progress bar for every item, and the user watches it. Put `--progress <0-100>` on every `update` (and `sub` updates too), your honest estimate for the whole job, and raise it as steps finish. A rough guide: understood the task and planned 10, first real change 25-40, most of the work in 60-75, tests passing or checked 85-95; `done` sets 100. Never leave it flat for long: if you are stuck, say so in the note and keep the figure where it is. An `update` without `--progress` prints a reminder.
- **When:** anything with several steps or more than a minute or two of work (coding, research, builds, writing, file or browser work). Skip one-shot questions.
- **Title:** 2 to 5 words in Title Case that name the thing ("Stripe Webhook Retry", not "Working on stuff").
- **Details and summary:** markdown; headings, lists, tables, code blocks and links render. Inline images: `![caption](/absolute/path.png)`.
- **One item per task.** Follow-ups on the same task reuse the id. A different task gets a new item.
- **Always `done`**, on success and also when you stop, fail or hand back to the user; say which in the summary. `done` removes the bubble from the panel; the item stays in history.
- Lost the id after a context reset? `orbital-track list` shows active items with their ids.

## Subagents

Only the main agent creates top-level bubbles. Subagents show inside the parent item.

**When you spawn a subagent**, put this line in its prompt:

```
ORBITAL_TRACK_PARENT=<ITEM ID> — you are a subagent: report only with "orbital-track sub <ITEM ID> ..." and "orbital-track done <SUB_ID> ...", never "orbital-track start".
```

**When your prompt contains `ORBITAL_TRACK_PARENT=<ID>`**, you are a subagent:

```bash
orbital-track sub <ID> --agent claude --title "Audit Auth Routes" --details "Reading every route under app/api/auth"
# prints SUB_ID
orbital-track update <SUB_ID> --note "Half the routes read" --progress 50
orbital-track done <SUB_ID> --summary "3 routes build the callback URL by hand; listed in the report."
```

Never run `start` as a subagent. A subagent whose prompt has no `ORBITAL_TRACK_PARENT` line does nothing with the tracker. As a safety net, `start` turns into `sub` when the `ORBITAL_TRACK_PARENT` environment variable is set.

Jobs the hub starts on a satellite are tracked by the satellite itself; such an agent gets `ORBITAL_TRACK_ID` in its environment and must report its progress on that item: `orbital-track update "$ORBITAL_TRACK_ID" --note "..." --progress 50` at each milestone (don't `start` a second item).

**Started from the Agent desk** (`ORBITAL_DESK_TASK` is set): the user is talking to the desk, often by voice, and it knows only what you send it. Report with `orbital desk` instead of `orbital-track update`; `note` moves the tracker item too:

```bash
orbital desk note "Found the failing test; fixing it" --progress 40   # each milestone
orbital desk finding "The checkout test fails on an expired fixture date"  # facts they may ask about
orbital desk answer "Two tests failed; both are fixed and the suite passes."  # read aloud at once
orbital desk ask "Use the staging or the live account?" --wait 300     # prints their reply
orbital desk blocked "Needs the store account's two-factor code"        # then stop
orbital desk inbox                                                     # anything they added
```

Every desk command prints what the user added since your last one: treat it as part of your brief. If `orbital` isn't on PATH, use `"$ORBITAL_CLI"`. A failed desk command never stops the work.

**Progress and cost.** `--progress <0-100>` is how far along the whole job is; send it with every update (done sets 100). With no figure yet, the bar is estimated from `- [x]` checkboxes in your details, then from your subagents, and shows as "~40%". You don't report tokens: the satellite that runs an agent measures them (Grok from its CLI log, Claude from its session transcript) along with the share of the subscription plan used, and the tracker, the app's Tracker tab and System → Automations show them. Scripts can send totals themselves with `--usage-json '{"engine":"grok","inputTokens":1200,"outputTokens":300}'`.

## Media

- `--media <path>` on `start`, `update`, `sub` or `done`, repeatable; images and videos.
- Use absolute paths.
- The tracker keeps a small compressed copy at once (images: a 480 px JPEG; videos: a 2×2 frame sheet), so the record survives when the original is deleted. On a satellite the copy also goes to the hub. The phone shows and streams the original while it exists, so keep originals in a lasting folder.

## Screenshot evidence (UI, website, game and visual work)

The user picks one level for every agent, on the tracker or in the app. Read it before you finish visual work; it can change while you work:

```bash
orbital-track evidence          # the level now and exactly what it asks of you
orbital-track evidence --json   # {"evidence": "off|min1|all", ...}
```

| Level | What you attach for visual work |
|---|---|
| **Off** | Nothing required. Attach media you made anyway. |
| **Min 1** | At least one screenshot of the finished result for any change to an app or iOS screen, website or page, game, map, render or chart. A new game feature where the motion matters (movement, combat, animation, physics, effects) also gets a short video. |
| **All** | A screenshot of every feature, screen, page and state you added or changed (before and after when it helps), and a video of every game feature that moves. Attach each as you go with a note saying what it shows, and list every feature beside its picture in the `done` summary. |

Visual work means the user can see the change. Backend, data, scripts and docs need nothing at any level. `start`, `sub` and `done` print the level on stderr when it is not Off, and `done` warns when the item has no pictures yet (the item stays done; attach them with `update`).

```bash
mkdir -p ~/Pictures/agent-evidence/shop
orbital-track update <ID> --note "Checkout page, mobile width" --media ~/Pictures/agent-evidence/shop/checkout-mobile.png
orbital-track done <ID> --summary "$(cat <<'EOF'
Added the new checkout.
- Checkout, mobile: ![Checkout, mobile width](/Users/you/Pictures/agent-evidence/shop/checkout-mobile.png)
EOF
)"
```

### How to capture

Capture the real running thing, not a mock-up.

| What | Screenshot | Video |
|---|---|---|
| Website or web app | your browser tool's screenshot, `orbital browser screenshot --out shot.png`, or Playwright `page.screenshot({path, fullPage: true})` | Playwright `recordVideo`, or your browser tool's recorder |
| iOS app (Simulator) | `xcrun simctl io booted screenshot shot.png` | `xcrun simctl io booted recordVideo --codec=h264 clip.mp4` (stop with Ctrl-C or `kill -INT`) |
| Mac app or window | `screencapture -x -o -l <windowID> shot.png` (or `-R x,y,w,h`) | `screencapture -x -v -V 10 clip.mov` |
| Game | the engine's screenshot command or editor capture | the engine's movie writer, or a short screen recording; convert with `ffmpeg -i in.avi -c:v libx264 -pix_fmt yuv420p clip.mp4` |

- Save to a folder that lasts: `~/Pictures/agent-evidence/<project>/`. Files in a temp folder vanish from the user's review later.
- Videos: `.mp4` or `.mov` (H.264), 5 to 30 seconds, focused on the feature.
- Follow the project's own capture rules (for example, test runs that must not take window focus).
- If you cannot capture (headless machine, sandbox, no display), say so in the `done` summary and describe what the user should look at.

## Other commands

```bash
orbital-track list [--all] [--json]                 # active items (works without the app)
orbital-track ping                                  # is the tracker running? on a satellite: the hub relay too
orbital-track update <ID> --details "new markdown" [--append]
orbital-track update <ID> --title "Better Title"
orbital-track start ... --details-file notes.md     # or --details - to read stdin; also --summary-file, --note-file
orbital-track start ... --workspace /abs/project --label "Claude Code"
orbital-track help
```

`--quiet` silences warnings. `ORBITAL_TRACK_DISABLE=1` makes every command a no-op that still prints an id.

## HTTP, when you cannot run the CLI

The tracker listens on `127.0.0.1:7701` (`[tracker] port` in `~/.orbital-agents/config.toml`). Send JSON, and no `Origin` header:

```bash
curl -s -X POST localhost:7701/v1/start -H 'Content-Type: application/json' \
  -d '{"agent":"claude","title":"Fix Login Redirect","details":"..."}'          # {"ok":true,"id":"..."}
curl -s -X POST localhost:7701/v1/sub/<ID>    -H 'Content-Type: application/json' -d '{"agent":"claude","title":"..."}'
curl -s -X POST localhost:7701/v1/update/<ID> -H 'Content-Type: application/json' -d '{"note":"...","media":[{"path":"/abs/shot.png"}]}'
curl -s -X POST localhost:7701/v1/done/<ID>   -H 'Content-Type: application/json' -d '{"summary":"..."}'
curl -s localhost:7701/v1/items
```

## If it fails

Tracking never blocks your work. The CLI tries the tracker over HTTP, then queues the command in `~/.orbital-agents/data/tracker/inbox/` (or `/tmp/orbital-tracker-inbox-<uid>/` in a sandbox), which the tracker applies when it starts. On a satellite every command also goes to the hub, queued while the hub is unreachable. Delivery problems print a warning, still print an id and exit 0. If `orbital-track` is missing or blocked, carry on without tracking.
