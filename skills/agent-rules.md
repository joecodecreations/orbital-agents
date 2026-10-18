## Orbital Agents: show what you are working on

The user watches every agent's work on a floating tracker on their Mac and in the Orbital Agents app. You are **{{agent}}** on **{{machine}}**; always pass `--agent {{agent}}`.

1. Starting real work (anything multi-step: coding, research, builds, writing, file or browser work; skip one-shot questions), run once:
   `orbital-track start --agent {{agent}} --title "<2-5 word title>" --details "<1-3 sentences: the goal, which project or folder>"`
   It prints an ID. Keep it in your context; shell variables do not survive between tool calls.
2. At real milestones: `orbital-track update <ID> --note "<what just happened>"`, with `--media /abs/file.png` for screenshots or recordings you made.
3. Finished, or stopping: `orbital-track done <ID> --summary "<markdown: what changed, where, what is left>"`.
4. Visual work (an app or iOS screen, website, game, map, render): check `orbital-track evidence` before `done` and attach the screenshots or videos it asks for.
5. Spawning a subagent: add `ORBITAL_TRACK_PARENT=<ID> — you are a subagent: report only with "orbital-track sub <ID> ..." and "orbital-track done <SUB_ID> ...", never "orbital-track start".` to its prompt. If your own prompt has that line, use `orbital-track sub <ID> --agent {{agent}} --title "<your piece>"` and `orbital-track done <SUB_ID> --summary "..."`, never `start`.

Tracking never blocks work: if `orbital-track` is missing or fails, carry on.

Skills with the details, installed with these rules: `orbital-tracker` (reporting and screenshots), `orbital-feeds` (put data in the app), `orbital-satellites` (run agents on other machines), `orbital-browser` (drive Chrome; ask the user first), `orbital-ops` (status, doctor, logs, live tests). Settings and logs: `{{orbital_home}}`. Never print or copy values from its `secrets.env`.
