"""Word prediction for the note editor.

Ships a frequency-ranked English wordlist (words.txt.gz, most-common first), so a
prefix returns the most likely completions. Loaded lazily, only when Notes runs.
"""

import gzip
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
_WORDS = None


def _load():
    global _WORDS
    if _WORDS is not None:
        return _WORDS
    _WORDS = []
    try:
        with gzip.open(os.path.join(_DIR, "words.txt.gz"), "rt",
                       encoding="utf-8") as f:
            _WORDS = [w.strip() for w in f if w.strip()]
    except OSError:
        pass
    return _WORDS


def suggest(prefix, limit=3):
    """Most-frequent completions of `prefix` (case-insensitive). The wordlist is
    in frequency order, so the first matches are the best ones."""
    if len(prefix) < 2:
        return []
    p = prefix.lower()
    out = []
    for w in _load():
        if w.startswith(p) and w != p:
            out.append(w)
            if len(out) >= limit:
                break
    return out
