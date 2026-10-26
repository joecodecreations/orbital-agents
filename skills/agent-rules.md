## Orbital Agents: show what you are working on

The user watches every agent's work on a floating tracker on their Mac and in the Orbital Agents app. You are **{{agent}}** on **{{machine}}**; always pass `--agent {{agent}}`.

1. Starting real work (anything multi-step: coding, research, builds, writing, file or browser work; skip one-shot questions), run once:
   `orbital-track start --agent {{agent}} --title "<2-5 word title>" --details "<1-3 sentences: the goal, which project or folder>"`
   It prints an ID. Keep it in your context; shell variables do not survive between tool calls.
2. At every milestone: `orbital-track update <ID> --note "<what just happened>" --progress <0-100>`. `--progress` is required on every update: your honest estimate of how much of the WHOLE job is done (planned ~10, first change ~30, most done ~70, checked ~90; `done` sets 100). It drives the progress bar the user watches on the tracker and the phone. Attach screenshots or recordings you made with `--media /abs/file.png`.
3. Finished, or stopping: `orbital-track done <ID> --summary "<markdown: what changed, where, what is left>"`.
4. Visual work (an app or iOS screen, website, game, map, render): check `orbital-track evidence` before `done` and attach the screenshots or videos it asks for.
5. Spawning a subagent: add `ORBITAL_TRACK_PARENT=<ID> — you are a subagent: report only with "orbital-track sub <ID> ..." and "orbital-track done <SUB_ID> ...", never "orbital-track start".` to its prompt. If your own prompt has that line, use `orbital-track sub <ID> --agent {{agent}} --title "<your piece>"`, `orbital-track update <SUB_ID> --progress <0-100>` as your piece moves, and `orbital-track done <SUB_ID> --summary "..."`, never `start`. If `ORBITAL_TRACK_ID` is set in your environment, the job is already tracked: report on it with `orbital-track update "$ORBITAL_TRACK_ID" --note "..." --progress <0-100>` instead of starting a new item.

6. Started from the Agent desk (`ORBITAL_DESK_TASK` is set in your environment): the user hears about your work only through the desk. Report with `orbital desk note "<milestone>" --progress <0-100>` (it moves the tracker item too, so skip `orbital-track update`), `orbital desk finding "<fact>"` for numbers, names, links and quotes they may ask about, `orbital desk answer "<the result>"` the moment you have the answer (it is read aloud to them), `orbital desk ask "<question>" --wait 300` only when you can't go on without them, and `orbital desk blocked "<what you need>"`. Every desk command prints what the user added since: treat it as part of your brief.

Tracking never blocks work: if `orbital-track` is missing or fails, carry on.

Skills with the details, installed with these rules: `orbital-tracker` (reporting and screenshots), `orbital-feeds` (put data in the app), `orbital-satellites` (run agents on other machines), `orbital-browser` (drive Chrome; ask the user first), `orbital-ops` (status, doctor, logs, live tests). Settings and logs: `{{orbital_home}}`. Never print or copy values from its `secrets.env`.
