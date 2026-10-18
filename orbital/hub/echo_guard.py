"""Tell the user's words from the speaker playing the assistant back into the mic.

A phone's voice-processing echo canceller does not remove a reply on the loud
speaker, so speech-to-text writes the assistant's sentence down. A short mismatch
at the end of that transcript used to count as the user interrupting: playback
stopped, and the accurate transcript (which arrives after playback has already
stopped) was sent with only a few leading words trimmed off. The rest of the
assistant's sentence became the user's next message.

`voice_should_drop` is the hub-side net for an app that still does that.
The app can use the same rules before it submits.
"""

from __future__ import annotations

EXPANSIONS = {
    "we've": ("we", "have"),
    "i'm": ("i", "am"),
    "i'll": ("i", "will"),
    "it's": ("it", "is"),
    "don't": ("do", "not"),
    "they're": ("they", "are"),
    "we're": ("we", "are"),
    "that's": ("that", "is"),
    "what's": ("what", "is"),
    "can't": ("can", "not"),
    "cannot": ("can", "not"),
    "won't": ("will", "not"),
    "i've": ("i", "have"),
    "you're": ("you", "are"),
    "you'll": ("you", "will"),
    "let's": ("let", "us"),
    "there's": ("there", "is"),
    "here's": ("here", "is"),
    "she's": ("she", "is"),
    "he's": ("he", "is"),
    "isn't": ("is", "not"),
    "aren't": ("are", "not"),
    "didn't": ("did", "not"),
    "doesn't": ("does", "not"),
    "wasn't": ("was", "not"),
    "weren't": ("were", "not"),
    "haven't": ("have", "not"),
    "hasn't": ("has", "not"),
    "hadn't": ("had", "not"),
    "wouldn't": ("would", "not"),
    "couldn't": ("could", "not"),
    "shouldn't": ("should", "not"),
    "i'd": ("i", "would"),
    "you'd": ("you", "would"),
    "he'd": ("he", "would"),
    "she'd": ("she", "would"),
    "we'd": ("we", "would"),
    "they'd": ("they", "would"),
    "gonna": ("going", "to"),
    "wanna": ("want", "to"),
}

# Words that do not, by themselves, prove a new sentence.
FUNCTION = {
    "a", "an", "the", "of", "to", "and", "or", "for", "in", "on", "at", "is", "it",
    "that", "this", "so", "i", "you", "we", "they", "be", "do", "not", "have", "has",
    "had", "will", "would", "can", "could", "should", "just", "my", "your", "our",
    "me", "us", "them", "she", "he", "her", "his", "with", "from", "as", "if", "but",
    "about", "up", "out", "what", "when", "how", "who", "am", "are", "was", "were",
    "been", "being", "does", "did", "there", "here", "let", "its", "than", "then",
    "too", "very", "also", "into", "over", "again",
}

INTERJECTIONS = {
    "stop", "wait", "hold", "hang", "hey", "no", "nope", "pause", "enough",
    "quiet", "actually", "sorry", "shush", "okay", "ok",
}


def _normalize(word: str) -> str:
    text = word.lower().replace("’", "'").replace("‘", "'")
    cleaned = []
    for ch in text:
        if ch == "'":
            cleaned.append(ch)
        elif ch.isalnum():
            cleaned.append(ch)
    return "".join(cleaned).strip("'")


def _expand(word: str) -> list[str]:
    return list(EXPANSIONS.get(word, (word,)))


def pieces(text: str) -> list[tuple[str, list[str]]]:
    out = []
    for raw in text.split():
        norm = _normalize(raw)
        if norm:
            out.append((raw, _expand(norm)))
    return out


def tokens(text: str) -> list[str]:
    flat: list[str] = []
    for _, parts in pieces(text):
        flat.extend(parts)
    return flat


def _match_run(heard: list[str], spoken: list[str], h0: int, s0: int) -> int:
    """How many heard tokens from h0 appear in order in spoken, allowing small gaps."""
    if not heard or not spoken:
        return 0
    matched = 0
    j = s0
    limit = min(len(spoken), s0 + (len(heard) - h0) + 4)
    for h in heard[h0:]:
        while j < limit and spoken[j] != h:
            j += 1
        if j >= limit:
            break
        matched += 1
        j += 1
    return matched


def coverage(heard: list[str], spoken: list[str]) -> float:
    """Best in-order overlap of `heard` with any window of `spoken`, over the length of `heard`."""
    if not heard:
        return 1.0
    if not spoken:
        return 0.0
    best = 0
    for h0 in range(len(heard)):
        for s0 in range(len(spoken)):
            if spoken[s0] != heard[h0]:
                continue
            best = max(best, _match_run(heard, spoken, h0, s0))
            if best == len(heard):
                return 1.0
    return best / len(heard)


def novel_content(heard: list[str], spoken_set: set[str]) -> list[str]:
    return [w for w in heard if w not in spoken_set and w not in FUNCTION]


def longest_novel_run(heard: list[str], spoken_set: set[str]) -> int:
    best = 0
    run = 0
    for w in heard:
        if w not in spoken_set and w not in FUNCTION:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _leading_token_match(heard: list[str], spoken: list[str]) -> int:
    """Longest prefix of `heard` that sits inside `spoken` in order."""
    if len(heard) < 2 or not spoken:
        return 0
    best = 0
    for s0, word in enumerate(spoken):
        if word != heard[0]:
            continue
        best = max(best, _match_run(heard, spoken, 0, s0))
        if best == len(heard):
            break
    return best if best >= 2 else 0


def _drop_prefix(parts: list[tuple[str, list[str]]], count: int) -> list[tuple[str, list[str]]]:
    left = count
    kept = []
    started = False
    for raw, toks in parts:
        if not started:
            if left >= len(toks):
                left -= len(toks)
                continue
            started = True
        kept.append((raw, toks))
    return kept


def is_speaker_echo(text: str, spoken: str) -> bool:
    """True when `text` is the assistant's own line coming back through the mic.

    A real follow-up that only shares a few words is kept. So is a two-word
    command ("check email") and a deliberate interruption ("wait, show me the email").
    """
    heard = tokens(text)
    said = tokens(spoken)
    if len(heard) < 4 or not said:
        return False
    said_set = set(said)
    if len(novel_content(heard, said_set)) > 1:
        return False
    return coverage(heard, said) >= 0.65


def is_fragment(full: str, fragment: str) -> bool:
    """`fragment` is a shorter contiguous piece of something just sent, not a new sentence."""
    whole = tokens(full)
    part = tokens(fragment)
    if len(part) < 2 or len(part) >= len(whole):
        return False
    for i in range(len(whole) - len(part) + 1):
        if whole[i : i + len(part)] == part:
            return True
    return False


def voice_should_drop(text: str, spoken: str, previous_user: str | None = None) -> bool:
    if previous_user and is_fragment(previous_user, text):
        return True
    return is_speaker_echo(text, spoken)


def decide(heard: str, spoken: str, guarding: bool) -> tuple[str, bool]:
    """What the app should do with a transcript while `guarding` (she is speaking, or just was).

    Returns `(text_to_send, barge)`. An empty string means drop it.
    """
    parts = pieces(heard)
    said = tokens(spoken)
    if not parts:
        return "", False
    flat = tokens(heard)
    said_set = set(said)
    original = " ".join(raw.strip() for raw, _ in parts).strip()

    if not guarding:
        if is_speaker_echo(heard, spoken):
            return "", False
        return original, False

    first = flat[0]
    if first in INTERJECTIONS:
        rest = flat[1:]
        rest_echo = (not rest) or (coverage(rest, said) >= 0.65 and len(novel_content(rest, said_set)) <= 1)
        if rest_echo:
            return first, True
        return original, True

    matched = _leading_token_match(flat, said)
    kept = _drop_prefix(parts, matched) if matched else parts
    kept_flat = [t for _, toks in kept for t in toks]
    if not kept_flat:
        return "", False

    novel = novel_content(kept_flat, said_set)
    run = longest_novel_run(kept_flat, said_set)
    cov = coverage(kept_flat, said)
    barge = len(novel) >= 3 or run >= 3 or (len(novel) >= 2 and cov < 0.45)
    clear_echo = cov >= 0.6 and len(novel) <= 1
    if barge and not clear_echo:
        sent = " ".join(raw for raw, _ in kept).strip()
        return sent, True
    return "", False
