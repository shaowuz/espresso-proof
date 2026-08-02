"""Parse JSON that a model wrote mathematics into.

Observed on a live run: an entire reviewer's feedback was thrown away and
replaced by a content-free "needs_revision" because the reply contained LaTeX
inside JSON strings —

    "the sum ... over k \\le n"

``\\l`` is not a JSON escape, so the parse failed and the adapter fell back. In
a system whose subject matter is written in LaTeX, a raw backslash in prose is
not an exotic failure; it is the normal case, and losing a paid call to it (and
reporting the loss as a reviewer verdict with zero objections) is a defect of
the parser, not of the reviewer.

There is a second, quieter half. ``\\f`` and ``\\b`` ARE valid JSON escapes, so
``\\frac`` and ``\\binom`` do not fail — they parse silently into a formfeed
and a backspace, corrupting the mathematics with no error anywhere. Nobody
writing a proof means a formfeed, so those two are treated as prose as well,
and a reply containing them is re-read even when it parsed. ``\\n``, ``\\r``,
``\\t`` are left alone: a model wrapping prose across lines really does mean a
newline.

Repairs are confined to the inside of string literals and can only change how
the model's own text is QUOTED — never which keys or values are present. A
repaired parse carries the model's content, not the parser's invention.
"""

from __future__ import annotations

import json

# Escapes that keep their JSON meaning: a model that writes one means it.
_KEPT_ESCAPES = frozenset('"\\/nrtu')
# Valid JSON escapes that no one writing mathematics intends. \f opens \frac,
# \b opens \binom; as escapes they silently become control characters.
_PROSE_ESCAPES = frozenset("fb")
_HEX = frozenset("0123456789abcdefABCDEF")
# Raw control characters a model puts inside a string when it wraps prose.
_CONTROL_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def repair_json_text(text: str) -> str:
    """Escape stray backslashes and raw control characters inside strings.

    A pure text transform: it never adds, drops, or reorders JSON structure.
    Outside string literals the input is copied verbatim, so a genuinely
    malformed object stays malformed and still fails to parse.
    """

    out: list[str] = []
    in_string = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if not in_string:
            if ch == '"':
                in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "\\":
            nxt = text[i + 1] if i + 1 < n else ""
            valid = nxt in _KEPT_ESCAPES and nxt != ""
            if valid and nxt == "u":
                # \u is only an escape when four hex digits follow; "\understand"
                # is prose, not a code point.
                valid = len(text) >= i + 6 and all(c in _HEX for c in text[i + 2 : i + 6])
            if valid:
                out.append(ch)
                out.append(nxt)
                i += 2
                continue
            # Stray (\le) or prose-shaped (\frac, \binom): keep the backslash as
            # a character of the mathematics.
            out.append("\\\\")
            i += 1
            continue
        if ch == '"':
            in_string = False
            out.append(ch)
            i += 1
            continue
        out.append(_CONTROL_ESCAPES.get(ch, ch))
        i += 1
    return "".join(out)


def loads_forgiving(text: str) -> tuple[object, bool]:
    """``json.loads`` with one repair pass. Returns (value, repaired).

    Strict parsing is tried first. A reply that parses AND survives the repair
    unchanged is returned untouched. A reply that parses but would change —
    only possible via ``\\f`` / ``\\b``, i.e. LaTeX read as control characters —
    is re-read from the repaired text, because the silent reading is the wrong
    one. If the repaired text fails, the ORIGINAL error is raised: the honest
    diagnostic is what the model actually emitted.
    """

    repaired_text = repair_json_text(text)
    changed = repaired_text != text
    try:
        value = json.loads(text)
    except json.JSONDecodeError as first_error:
        if not changed:
            raise
        try:
            return json.loads(repaired_text), True
        except json.JSONDecodeError:
            raise first_error from None
    if not changed:
        return value, False
    try:
        return json.loads(repaired_text), True
    except json.JSONDecodeError:
        # The repair would not parse; the strict reading is all there is.
        return value, False


__all__ = ["loads_forgiving", "repair_json_text"]
