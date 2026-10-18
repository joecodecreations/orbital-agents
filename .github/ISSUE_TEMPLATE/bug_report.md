---
name: Bug report
about: Something does not work as the docs say
title: ""
labels: bug
---

**What happened**

A clear description of the problem, and what you expected instead.

**How to reproduce**

1.
2.
3.

**Where**

- Orbital Agents version (`orbital --version`):
- Machine role (hub or satellite) and OS (macOS / Linux version):
- Part affected (hub, satellite, scheduler, tracker, app, browser controller, setup, docs):
- For the app: iPhone, iPad or Mac, and iOS / macOS version:

**Diagnostics**

Paste the relevant parts of these, after checking them for anything private (names, host names, paths, tokens):

```
orbital doctor --json
orbital logs --level WARNING --since 1h
```

Never paste the contents of `secrets.env`, tokens, passwords or API keys.

**Anything else**

Screenshots, `orbital test --live` output, or what you already tried.

Security problems: do not open an issue. Report them privately as described in SECURITY.md.
