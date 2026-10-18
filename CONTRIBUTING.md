# Contributing

Thanks for helping. Bug reports, fixes, docs and new example feeds are all welcome.

## Development setup

```bash
git clone https://github.com/joecodecreations/orbital-agents && cd orbital-agents
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

That is enough for the Python code and tests; it does not touch your machine's services. Run `./setup` only when you also want the hub, satellites and apps running on this machine.

For the other parts:

| Part | Needs | Build |
|---|---|---|
| tracker (`apps/tracker/`) | macOS 14+, Swift 6 (Xcode 16 or its Command Line Tools) | `cd apps/tracker && swift build && swift test` |
| app (`apps/orbital-app/`) | full Xcode | `apps/orbital-app/scripts/build.sh` (unsigned, no account needed); `scripts/test.sh` for its unit tests |
| browser extension (`browser-extension/`) | Chrome 116+, Node for linting | load the folder unpacked; no build step |

The app's Xcode project is generated (`python3 apps/orbital-app/generate_xcodeproj.py`) and not committed, because it carries each developer's Team ID.

## Tests

```bash
make test                                     # Python tests, tracker swift build + test, unsigned app build, lint
make test-python                              # pytest -m "not live"
.venv/bin/pytest tests/hub -q                 # one area
.venv/bin/pytest tests/test_public_safety.py -q
make lint
```

Tests must never touch `~/.orbital-agents`, your LaunchAgents or systemd units, or ports 7700–7704: use a temporary `ORBITAL_HOME` and a free port (see the `conftest.py` files). Mark tests that start real servers `@pytest.mark.slow`. Live checks against an installed system go in `tests/live/` and are marked `live`. Details: [docs/TESTING.md](docs/TESTING.md).

## Style

- **Contracts first.** `docs/ARCHITECTURE.md` is the contract between components. If your change makes the code and that file disagree, update one of them in the same pull request. API changes also update `docs/DATA_PIPELINE.md` or the relevant doc, and the JSON schemas in `schema/`.
- **Python:** 3.11+, standard library plus the dependencies in `pyproject.toml`, type hints, small modules. Settings come from `orbital.core.config` (add new ones to `DEFAULTS` and `config/orbital.example.toml`), secrets from `orbital.core.secrets`, paths from `orbital.core.paths`, logging from `orbital.core.get_logger(component)`.
- **CLI:** one file per subcommand in `orbital/cli/commands/` with `NAME`, `HELP`, `register()` and `run()`; nothing else needs to change.
- **Errors say what to do.** An error message names the problem and the fix (`… run orbital satellites invite on the hub`).
- **Never log secrets**, tokens, passwords or full request bodies; use `orbital.core.secrets.mask()` when you must refer to one.
- **No shell.** Run external programs with argument lists.
- **Docs and messages:** plain, short sentences; tables where they help; no marketing language. Examples stay generic: a home lab, website uptime, a GitHub repo, the weather, a reading list. Never real people, companies or private data.
- **Swift:** match the existing code; keep `OrbitalTrackerCore` free of AppKit so the CLI and tests can use it.

## Public safety

This is a public repository. `tests/test_public_safety.py` fails on private keys, API key patterns, home-folder paths, Tailscale host names and Apple team id assignments. Put words that must never ship (your name, company, host names) in `.public-safety.local` at the repo root, one per line; it is git-ignored and the test reads it. Use placeholders in docs: `/Users/you/`, `my-mac.your-tailnet.ts.net`, `com.yourname`, `ABCDE12345`.

## Pull requests

Before you open one:

- [ ] `make test` passes (or say which parts you could not run, for example no Xcode)
- [ ] `.venv/bin/pytest tests/test_public_safety.py -q` passes
- [ ] new behaviour has a test
- [ ] `docs/ARCHITECTURE.md` and the user docs match the change
- [ ] new settings are in `DEFAULTS`, `config/orbital.example.toml` and `docs/CONFIG.md`
- [ ] no secrets, personal paths or private data anywhere in the diff
- [ ] a line in `CHANGELOG.md` under "Unreleased" for anything users will notice

Keep pull requests focused: one fix or feature each. For a large change, open an issue first to agree on the approach.

## Reporting bugs and security problems

Open an issue with the bug report template; include `orbital --version`, your OS, and the relevant output of `orbital doctor --json` and `orbital logs --level WARNING --since 1h` (check it for anything private first). Security problems go privately through GitHub's vulnerability reporting, as described in [SECURITY.md](SECURITY.md), never in a public issue.

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
