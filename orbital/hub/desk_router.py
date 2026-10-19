"""Which Claude model answers a desk turn while Claude stands in for Grok.

Haiku for chat, quick questions, to-dos and reminders; Sonnet for normal multi-step help, status
round-ups and work to dispatch; Opus for hard reasoning and big planning. Picked per turn from
what The user just said, in under a millisecond and without a model call:

- the user names a model ("use Opus", "Sonnet: …", "quick answer") → that model, for this turn.
- A short "yes / go ahead / do it" right after a bigger answer keeps that model, so the one that
  made the plan also writes the work it dispatches.
- Waving something off ("don't worry about the email", "never mind") is Haiku.
- Otherwise a score: one planning or reasoning phrase means Opus; status and work words mean
  Sonnet; a very long request with work in it climbs to Opus. Nothing to go on means Haiku.

`ORBITAL_DESK_CLAUDE_ROUTE` (secrets.env) pins every turn to one model: haiku, sonnet or opus.
"auto" (the default) routes. A model that fails moves the turn up a tier (Haiku → Sonnet → Opus)
and is skipped for a couple of minutes. Grok, when it can answer, is untouched by any of this.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

TIERS = ("haiku", "sonnet", "opus")
MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
# A tier that just failed (not account-wide: that takes all of Claude down) is skipped this long.
TIER_COOLDOWN = 120
# How long a short "yes, do it" still belongs to the turn before it.
FOLLOW_UP_WINDOW = 10 * 60

OVERRIDE = [
    (re.compile(r"\b(?:use|with|on|ask|switch to|get|try)\s+(?:claude\s+)?opus\b|^\s*opus\b|\bthink (?:hard|harder|deeply|really hard)\b", re.I), "opus"),
    (re.compile(r"\b(?:use|with|on|ask|switch to|get|try)\s+(?:claude\s+)?sonnet\b|^\s*sonnet\b", re.I), "sonnet"),
    (re.compile(r"\b(?:use|with|on|ask|switch to|get|try)\s+(?:claude\s+)?haiku\b|^\s*haiku\b|\bquick (?:answer|one|question)\b", re.I), "haiku"),
]

# Hard reasoning and big planning: one of these is enough for Opus.
STRONG = re.compile(
    r"\b(?:strateg\w*|roadmap|game ?plan|plan (?:out|for (?:the|this|next) (?:week|month|quarter|year))|"
    r"make (?:a|me a) plan|(?:weekly|monthly|business|launch|marketing) plan|"
    r"think (?:it |this |that )?through|deep[- ]?dive|in depth|step by step|pros and cons|trade-?offs?|"
    r"root cause|architect\w*|prioriti[sz]\w*|triage|brainstorm\w*|"
    r"figure out (?:why|how|what|whether)|analy[sz]\w*|evaluate|weigh (?:up )?(?:the )?options|"
    r"what should i (?:do|focus|work|prioriti[sz]e)|best (?:way|approach|option)|long[- ]term|forecast|"
    r"compare|comparison|decide between|which (?:one )?should i)\b",
    re.I,
)
# Normal multi-step help, status, and work the desk hands to an agent.
MEDIUM = re.compile(
    r"\b(?:status|summar\w*|recap|update me|catch me up|progress|what(?:'s| is) (?:running|happening|going on|left|new)|"
    r"check (?:on|in on)|how(?:'s| is| are) (?:the|my)|explain|why|how do|how does|how can|"
    r"draft|write|rewrite|email|review|fix|build|debug|deploy|set up|setup|change|update|research|look into|"
    r"find out|investigate|schedule|and then|after that|also)\b",
    re.I,
)
# the user waving something off ("don't worry about the email") is chat, whatever it names.
DISMISS = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|sorry|no|nah|actually)[\s,.!]+)*(?:don'?t worry about|never ?mind|forget (?:it|about)|"
    r"skip (?:it|that)|no need|not now|cancel that|hold off|leave it)\b",
    re.I,
)
FOLLOW_UP = re.compile(
    r"^\s*(?:ok(?:ay)?|yes|yeah|yep|sure|sounds good|perfect|great|do it|do that|go ahead|go for it|"
    r"run it|start it|send it|ship it|let'?s do it|make it so)\b[\s,.!]*(?:do it|go ahead|please|thanks)?[\s.!]*$",
    re.I,
)


@dataclass
class Route:
    tier: str
    reason: str

    @property
    def model(self) -> str:
        return MODELS[self.tier]


def pick(text: str) -> Route:
    """The tier for what the user said, from the words alone."""
    said = (text or "").strip()
    for pattern, tier in OVERRIDE:
        if pattern.search(said):
            return Route(tier, "asked for it")
    words = len(said.split())
    if DISMISS.match(said) and words <= 15:
        return Route("haiku", "quick")
    strong = {m.group(0).lower() for m in STRONG.finditer(said)}
    medium = {m.group(0).lower() for m in MEDIUM.finditer(said)}
    # Work words alone only reach Sonnet; Opus takes a reasoning phrase or a very long request.
    score = 3 * len(strong) + min(len(medium), 1) + (words > 45) + (words > 90) + (said.count("?") > 1)
    hits = ", ".join(sorted(strong) + sorted(medium))[:80]
    if score >= 3:
        return Route("opus", f"hard: {hits}" if hits else f"long ({words} words)")
    if score >= 1:
        return Route("sonnet", f"multi-step: {hits}" if hits else f"{words} words")
    return Route("haiku", "quick")


class ClaudeRouter:
    """Per-turn tier for the Claude stand-in: the setting, the user's words, the turn before, and
    which tiers just failed."""

    def __init__(self, mode: str | None = "auto", base: str | None = None) -> None:
        mode = (mode or "auto").strip().lower()
        self.mode = mode if mode in TIERS else "auto"
        self.models = dict(MODELS)
        if base:
            self.models["haiku"] = base  # ORBITAL_DESK_CLAUDE_MODEL is the quick tier's model
        self.down: dict[str, float] = {}
        self.last: tuple[str, float] | None = None

    def model(self, tier: str) -> str:
        return self.models[tier]

    def route(self, text: str) -> Route:
        if self.mode != "auto":
            return Route(self.mode, "pinned in settings")
        route = pick(text)
        if route.reason == "quick" and self.last and FOLLOW_UP.match(text or ""):
            tier, at = self.last
            if tier != "haiku" and time.time() - at < FOLLOW_UP_WINDOW:
                return Route(tier, "follow-up")
        return route

    def ladder(self, route: Route) -> list[str]:
        """The routed tier, then every tier above it; ones that just failed go last."""
        tiers = list(TIERS[TIERS.index(route.tier):])
        now = time.time()
        return [t for t in tiers if self.down.get(t, 0) <= now] + [t for t in tiers if self.down.get(t, 0) > now]

    def failed(self, tier: str) -> None:
        self.down[tier] = time.time() + TIER_COOLDOWN

    def answered(self, tier: str) -> None:
        self.down.pop(tier, None)
        self.last = (tier, time.time())
