# Security

Orbital Agents lets you start AI agents on your machines and drive your browser from your phone. That is powerful, so the defaults are strict. This page explains what protects what, where secrets live, how to rotate them, and how to report a problem.

## Threat model in one paragraph

Everything runs on machines you own and talks over your **Tailscale** tailnet. Nothing listens on the public internet, and there is no cloud service of ours in the middle. The things we defend against are: anyone outside your tailnet (they cannot reach the ports at all), other devices on your local network (refused by the allow lists), web pages you visit (refused by the bridge's origin checks), and a device on your tailnet that has no token (refused by every server). The things we cannot defend against are someone who already holds your tokens or your user account on one of your machines, and what an agent does once you ask it to work: agents act with your account's permissions.

## Layers

1. **Network allow lists.** The hub (`hub.allow_networks`) and every satellite (`satellite.allow_networks`) answer only loopback and the Tailscale ranges (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`). A request from any other address gets 403 before routing or auth runs. The browser bridge and the tracker listen on `127.0.0.1` unless you choose otherwise (`browser.bind = "tailscale"`); the tracker always stays on loopback.
2. **Tailscale** encrypts and authenticates every connection between your machines. Use Tailscale ACLs if you want to limit which devices may reach which ports.
3. **Tokens.** Every call that does something carries a token, compared in constant time:

| Caller → callee | Credential |
|---|---|
| App (iPhone, Mac) → hub | sign in with `ORBITAL_HUB_USERNAME` / `ORBITAL_HUB_PASSWORD`; the hub issues a session token (stored hashed, signed out after `hub.data_retention_days` unused) |
| Feeds, scripts, CLI → hub | `ORBITAL_AUTOMATION_TOKEN` |
| New machine → hub, once | `ORBITAL_JOIN_TOKEN` |
| Satellite → hub | its `ORBITAL_HUB_TOKEN`, issued at join, stored hashed on the hub |
| Hub → satellite | that machine's `ORBITAL_SATELLITE_TOKEN`, sent at join |
| Hub or another machine → a browser bridge | `ORBITAL_BROWSER_TOKEN`, one value for the whole fleet: the hub hands it to every machine when it joins (calls from the bridge's own machine need none) |
| Agents → tracker | none; loopback only, and requests with a browser `Origin` header are refused |

Open endpoints say nothing secret: `GET /v1/health` and `GET /v1/hub/info` on the hub (used for discovery), `GET /v1/health` on a satellite, `GET /health` on a bridge.

## What a satellite's token may do

A joined machine's hub token may list machines, use the agent and browser relays (so you can orchestrate from any machine), post alerts (the hub stamps them with the machine name), and use the tracker relay and evidence setting. It **cannot** read or write sections, actions, media, the Agent desk or sync, and cannot remove machines. A compromised satellite can therefore start agents on your other machines and drive their browsers, within each machine's own limits below, but cannot read your dashboard data.

Every joined machine also receives the fleet's **browser token** (`ORBITAL_BROWSER_TOKEN`) in the join answer and stores it in its `secrets.env`. So any joined machine can drive any browser bridge in your fleet that listens with `browser.bind = "tailscale"`, directly, without the hub. Bridges on `bind = "loopback"` (the config default, and the wizard's default on the hub) answer only their own machine; the wizard suggests `tailscale` on satellites. Turn on `tailscale` binding only on machines whose browser you want your other machines to use.

## Remote agent runs are opt-in and fenced

- A machine runs agents for the hub only while `satellite.enabled = true` (the default). Turn it off on machines that should never run remote work.
- `cwd` must be inside `satellite.workdirs` (default your home folder). Narrow it, for example `workdirs = ["~/code"]`.
- Commands are argument lists from `[agents.<name>]` with the prompt as one argument. No shell is ever used, so a prompt cannot inject shell syntax.
- Jobs are limited in number (`max_concurrent_jobs`), time (`job_timeout_minutes`) and output (`max_output_bytes`).
- An agent CLI that runs with "skip permissions" style flags can do anything your account can. Configure the agent commands you add with that in mind.

## The browser controller

Anyone who can call a bridge can act in your logged-in Chrome tabs. So:

- The bridge listens on loopback unless you set `browser.bind = "tailscale"`, and then answers only Tailscale addresses that send the browser token. No token set means no remote access at all. The token is shared by the whole fleet (every machine gets it when it joins), so every joined machine can drive a bridge that binds to Tailscale.
- Web pages cannot reach it: no CORS headers, requests with a web `Origin` are refused, preflights are refused, and loopback requests with a foreign `Host` are refused (DNS rebinding). The extension accepts control messages only from its own popup, options page and side panel.
- Network capture is off by default. When on, credential values (Authorization, Cookie, token, session, key and password headers, query parameters, form fields and JSON keys) are replaced with `[redacted]` before anything is stored.
- Pause the extension (Options → Take commands from the bridge) whenever you do not want agents driving the browser.

Agent skills tell agents to ask you before driving a browser unless you already approved it for the task. Details: [docs/BROWSER_CONTROLLER.md](docs/BROWSER_CONTROLLER.md).

## What is stored where

| Where | What | Protection |
|---|---|---|
| `~/.orbital-agents/` | everything below | mode 0700 |
| `secrets.env` | passwords, tokens, API keys | mode 0600, git-ignored, never logged |
| `apns.p8` | optional Apple push key | keep it 0600; git-ignored (`*.p8`) |
| `data/hub/sessions.json` | app sessions: SHA-256 of each token, user name, times | mode 0600 |
| `data/hub/satellites.json` | each machine's address, capabilities, its satellite token (the hub needs it to call back) and a hash of its hub token | mode 0600 |
| `data/hub/identity.json` | the hub id and the key that signs tracker media URLs | mode 0600 |
| `data/hub/sections/`, `alerts.json`, `media/` | what your feeds pushed | your account only |
| `data/satellite/jobs/`, `out/` | agent prompts and output | trimmed after `satellite.job_retention_days` |
| `data/tracker/` | tracker items and archived screenshots | trimmed after the tracker's retention settings |
| `data/browser/` | staged uploads, network captures (redacted) | trimmed after `staged_max_age_hours`, capped in size |
| `logs/` | JSON-line logs | never contain secrets, tokens, request bodies or browser page contents |
| the app (iPhone, Mac) | the hub address and your session token | the session token is in the Keychain |

`config.toml` and `config.resolved.json` hold no secrets.

## Rotating tokens

Make a new random value with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

and edit `~/.orbital-agents/secrets.env` (keep it mode 0600). Never paste a token into a chat, an issue or a commit.

| Token | Steps |
|---|---|
| App password | change `ORBITAL_HUB_PASSWORD` (and `ORBITAL_HUB_USERNAME` if you like) on the hub. The hub re-reads the file; no restart. Existing app sessions stay signed in: to sign every device out, delete `~/.orbital-agents/data/hub/sessions.json`, then sign in again on each device |
| `ORBITAL_AUTOMATION_TOKEN` | set the new value on the hub (picked up at once) and on every machine or script that pushes data; scheduled feeds read it on their next run |
| `ORBITAL_JOIN_TOKEN` | set a new value on the hub. Machines already joined are not affected; new ones need the new token (`orbital satellites invite`) |
| A satellite's `ORBITAL_HUB_TOKEN` | run `orbital satellite join <hub_url> <join_token>` on that machine: the hub issues a new token and forgets the old one. To revoke it outright, `orbital satellites remove <id>` |
| A machine's `ORBITAL_SATELLITE_TOKEN` | set the new value on that machine, `orbital services restart satellite`, then `orbital satellite join <hub_url> <join_token>` there so the hub learns it |
| `ORBITAL_BROWSER_TOKEN` | set a new value on the hub (the hub's relay picks it up at once; run `orbital services restart browser-bridge` if the hub runs a bridge). Then on every other machine run `orbital satellite join <hub_url> <join_token>`: the join answer carries the new token, the machine stores it and restarts its bridge. Until a machine rejoins, its bridge keeps the old token and refuses the hub |
| APNs key | revoke the key on developer.apple.com, create a new one, replace `apns.p8` and `APNS_KEY_ID`, `orbital services restart hub` |
| `XAI_API_KEY`, `FISH_API_KEY` | rotate at the provider, update `secrets.env` on the hub, `orbital services restart hub` |

If you think a machine is compromised: remove it (`orbital satellites remove <id>`), rotate the automation, join and browser tokens, and change the app password and sign everyone out.

## For contributors

- Never commit `secrets.env`, `config.toml`, `*.p8`, logs or anything from `~/.orbital-agents` (all git-ignored).
- `tests/test_public_safety.py` fails on private keys, API key patterns, home-folder paths, tailnet host names and Apple team id assignments in tracked files. Put words that must never ship (your name, company, host names) in `.public-safety.local` at the repo root, one per line; that file is git-ignored.
- Never log a secret or a full request body; use `orbital.core.secrets.mask()` when you must refer to one.

## Reporting a vulnerability

Please report security problems privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**. Include what you found, how to reproduce it, and which version (`orbital --version`). Please do not open a public issue for a security problem. We will acknowledge the report, work on a fix, and credit you in the changelog unless you prefer otherwise.

