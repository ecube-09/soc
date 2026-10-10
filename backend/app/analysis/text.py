"""Small lexical helpers for checking model output against stored documents.

Deliberately crude: lowercase tokens, a stopword list, and suffix stripping so
"exhaustion" and "exhausted" meet. Good enough to tell whether a sentence has
any footing in a document; not a measure of whether it is a fair summary.
"""

import math
import re

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)*")

_STOPWORDS = frozenset(
    """
    a an the and or but nor for of to in on at by with from into onto over under
    about above below after before during since until while than then that this
    these those there here which who whom whose what when where why how
    is are was were be been being am do does did done has have had having
    it its they them their we our you your he she his her
    not no yes any all both each either neither some such same other another
    can could may might must shall should will would also only just very more
    most less least much many few own so too up down out off again further once
    if else whether because as per via vs etc
    check checks verify confirm confirms review look read compare measure inspect
    determine establish ensure examine identify investigate monitor observe watch
    see find get use using used make sure current currently whether against
    step steps case cases incident incidents similar likely possible possibly
    """.split()
)

_SUFFIXES = (
    "ations",
    "ation",
    "ions",
    "ion",
    "ings",
    "ing",
    "ies",
    "ied",
    "es",
    "ed",
    "ly",
    "s",
)


def stem(token: str) -> str:
    if token[0].isdigit():
        return token
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            token = token[: -len(suffix)]
            if suffix in ("ies", "ied"):
                token += "y"
            break
    if token.endswith("e") and len(token) >= 5:
        token = token[:-1]
    return token


def content_tokens(text: str) -> set[str]:
    """Stemmed content words, with identifiers split on `_` and `-`."""
    out: set[str] = set()
    for raw in _TOKEN.findall(text.lower()):
        if raw in _STOPWORDS:
            continue
        if len(raw) < 3 and not any(ch.isdigit() for ch in raw):
            continue
        out.add(stem(raw))
    return out


def coverage(claim: str, source: str) -> float:
    """Fraction of the claim's content words that appear in the source."""
    claimed = content_tokens(claim)
    if not claimed:
        return 1.0
    return len(claimed & content_tokens(source)) / len(claimed)


def jaccard(left: str, right: str) -> float:
    a, b = content_tokens(left), content_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_GENERIC_KNOB_WORDS = frozenset(
    {stem(w) for w in ("value", "setting", "config", "size", "count", "limit", "max", "min", "number")}
)


def mentions_knob(text: str, target: str) -> bool:
    """Does the text talk about this configuration knob?

    `redis_connection_pool_size` is mentioned by "Redis connection", by "pool
    size", or by "the client pool" (its head noun), not by "Redis" alone.
    """
    ordered = [stem(w) for w in re.split(r"[_\-\s]+", target.lower()) if w]
    words = [w for w in ordered if w not in _GENERIC_KNOB_WORDS]
    if not words:
        return False
    tokens = content_tokens(text)
    if words[-1] in tokens and not words[-1].isdigit():
        return True
    present = set(words) & tokens
    return len(present) >= max(1, math.ceil(len(set(words)) / 2))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.;!?])\s+", text) if s.strip()]
