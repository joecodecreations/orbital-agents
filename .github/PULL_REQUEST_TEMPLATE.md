## What and why

What this changes and why. Link the issue if there is one.

## How it was tested

The commands you ran and what they showed (for example `make test`, `orbital test --live`, a screenshot of the app or tracker for visual changes).

## Checklist

- [ ] `make test` passes, or I said which parts I could not run (for example no Xcode)
- [ ] `.venv/bin/pytest tests/test_public_safety.py -q` passes
- [ ] new behaviour has a test
- [ ] `docs/ARCHITECTURE.md` and the user docs match the change
- [ ] new settings are in `DEFAULTS`, `config/orbital.example.toml` and `docs/CONFIG.md`
- [ ] no secrets, personal paths, host names or private data in the diff
- [ ] `CHANGELOG.md` has a line under "Unreleased" if users will notice the change
