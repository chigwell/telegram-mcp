"""Pure alias normalization and conservative token matching."""

from difflib import SequenceMatcher
import os
import re
from typing import Dict, List
import unicodedata

_HANDLE_RE = re.compile(r"^@?[a-zA-Z0-9_]{5,}$")
_SELF_REFS = {"me", "self"}


def alias_key(text: str) -> str:
    """Normalize an alias so visually identical spellings collide on purpose."""
    key = unicodedata.normalize("NFC", text).strip().lstrip("@").lower()
    key = key.replace("ё", "е")
    return " ".join(key.split())


def is_handle_like(value: str) -> bool:
    """True for anything that could be a real username/phone/id/self reference."""
    candidate = value.strip()
    bare = candidate.lstrip("@")
    return bool(
        candidate.startswith("+")
        or bare.lstrip("-").isdigit()
        or bare.lower() in _SELF_REFS
        or _HANDLE_RE.match(candidate)
    )


def _same_word(a: str, b: str) -> bool:
    """True when two tokens are the same word, tolerating an inflected ending.

    Russian inflects at the end ("Андрею"/"андрей", "главному"/"главный"), so a real
    inflection keeps a long shared stem and swaps a few trailing characters. Three
    conditions, each pinned by a table of name pairs in tests/test_aliases.py: a stem
    of >=4 chars (or a one-character swap on equal-length words, so "лена"/"лене"
    works without letting "олег"/"олеся" through), endings of at most three
    characters, and a similarity backstop.
    """
    if a == b:
        return True
    shared = len(os.path.commonprefix([a, b]))
    if len(a) - shared > 3 or len(b) - shared > 3:
        return False
    if not (shared >= 4 or (len(a) == len(b) and shared == len(a) - 1)):
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.65


def _covers(query_tokens: List[str], alias_tokens: List[str]) -> bool:
    """True when every query token claims a DISTINCT alias token.

    Without the distinctness two query words could land on the same alias word, so
    "андрей андреев" matched a stored "андрей" and the surname the user added to
    name someone else was free. ponytail: Kuhn's algorithm, lists are 1-3 tokens.
    """
    if len(query_tokens) > len(alias_tokens):
        return False
    taken: Dict[int, str] = {}

    def assign(token: str, seen: set) -> bool:
        for index, alias_token in enumerate(alias_tokens):
            if index in seen or not _same_word(token, alias_token):
                continue
            seen.add(index)
            if index not in taken or assign(taken[index], seen):
                taken[index] = token
                return True
        return False

    return all(assign(token, set()) for token in query_tokens)
