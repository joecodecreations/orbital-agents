# Changelog

All notable changes to Orbital Agents are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Money tab:** a built-in Money tab on iPhone (and in the iPad and Mac sidebar) adds up every ledger: today, this week, this month, this year and all time, a chart, and a row per business. Tap a business for its products and sources, a product for its sources and categories, and on down to every entry. Filter by business, product, source, category, date range (or custom dates), and income, refunds or payouts.
- **`subscriptions` block:** a product's subscriptions (status, plan, start, trial end, renewal, cancellation, failed payments). The app shows subscribers, MRR, trials and when they end, new, cancelled and churned, and every subscription, from Money and the product and space pages. `orbital.feeds.blocks.subscriptions()` builds one.

### Fixed

- **Home Lab, Projects and System data:** the machine-health daily chart shows today's load and memory instead of adding 30 daily averages together (series metrics take `level`, and `blocks.series_metric(level=True)` sets it). The GitHub example feed reads private repositories through the GitHub CLI sign-in when `GITHUB_TOKEN` is unset, and shows commits per day, the latest commits and the last push; stars and forks only for public repositories.
- **Agent:** an action written on the same line as the reply (`On it. >>> {"type":"task",…}`) is now run instead of being shown and read aloud as text.
- **Alerts:** a device's first sync gets every waiting alert, not only the newest 200.
- **iPhone app:** a space with a `money` series no longer shows two Money tabs, and a product named only on a series or ledger row shows its page's icon instead of a blank square.
- **iPhone app:** a **Sections** control on the bottom bar opens the spaces-and-pages drawer. While typing in the Agent field (or any other field), a **Done** strip stays above the keyboard so you can leave the composer and use the tab bar again without force-closing the app.

## [0.1.0] - 2026-10-17

The first public release.

### Added

- **Hub** (`orbital/hub/`): the data API the apps read (sections of blocks, partial updates by id, validation with repair hints, alerts, actions that come back to feeds, media, live "pulse" values, shared preferences, a live websocket), the satellite registry and relays for agent runs and browser commands, the tracker view and relay, the Agent desk, and optional direct Apple push. Answers only loopback and Tailscale addresses.
- **Satellite service** (`orbital/satellite/`): starts agent CLIs (Claude, Codex, Grok, Gemini, an `echo` test agent, or any command you configure) on request, with work-folder limits, a concurrency limit, timeouts and capped output; joins a hub over Tailscale with a one-time join token and sends heartbeats.
- **Scheduler** (`orbital/scheduler/`): runs feeds from `jobs.json` on a fixed grid with offsets, lanes, timeouts, file watches, catch-up after sleep, ok / warn / error outcomes from each feed's result line, and run, cancel, pause and interval controls from the CLI and the app.
- **Watchdog and janitor**: health checks for every service, spaced-out restarts through launchd or systemd with self-heal alerts, and trimming of logs, outputs and old data.
- **Agent Satellite Tracker** (`apps/tracker/`): a floating macOS panel showing every agent's work with details, notes, screenshots, videos and subagents, a history, an optional on-device voice mode, global shortcuts, the screenshot evidence level, and the `orbital-track` CLI with offline queueing and a hub relay for other machines.
- **Orbital Agents app** (`apps/orbital-app/`): iPhone, iPad and Mac (Catalyst) app with Home, pages for every feed, Agents (machines and jobs, run an agent anywhere), the Agent desk, the tracker and the alerts inbox; local notifications and background refresh.
- **Orbital Agents Browser Controller** (`browser-extension/`, `orbital/browser/`): a Chrome extension and bridge to navigate, click, type, read, run JavaScript, upload and download files, take screenshots and capture network traffic, with per-agent background windows, here or on another machine over Tailscale.
- **CLI** `orbital`: `status`, `doctor`, `logs`, `config`, `push`, `jobs`, `satellite`, `satellites`, `agents`, `browser`, `services`, `skills`, `setup`, `test`.
- **Python SDK** `orbital.feeds`: `HubClient` and block builders for writing feeds.
- **Example feeds**: machine health, website uptime, GitHub repository stats, weather, RSS headlines, and a tour of every block type.
- **Setup wizard** (`./setup`): hub and satellite setup with discovery over Tailscale, an answers file for unattended setup, dry runs, re-runs that keep your answers, login services on macOS and Linux, app builds, agent skills and a live end-to-end check.
- **Agent skills** (`skills/`): `orbital-tracker`, `orbital-feeds`, `orbital-satellites`, `orbital-browser` and `orbital-ops`, plus an always-on rule block, installed for Claude Code, Codex, Grok and Gemini.
- **Tests**: Python unit and integration tests, tracker Swift tests, app build and unit tests, live operational checks (`orbital test --live`), a public-safety scan, and GitHub Actions CI.
- **Docs**: setup, data pipeline, feeds, satellites, browser controller, app, notifications, configuration, logging, self-healing, testing, troubleshooting, architecture and security.

[Unreleased]: https://github.com/joecodecreations/orbital-agents/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/joecodecreations/orbital-agents/releases/tag/v0.1.0
