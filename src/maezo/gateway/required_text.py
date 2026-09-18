"""Presence of a required textual field, decided one way for the whole codebase.

`str.strip()` does not remove zero-width and BOM code points — their `isspace()` is
False — so a "required" field containing only `"​"` reads as present to naive
code. Two independent denial guards depend on getting this right
(`SendDenialNoticeWorker`'s grounding fields and the portal-decision owner's custody
references), and a rule that exists twice is a rule that eventually differs in one
place. It lives here once.

Fail-closed by construction: anything that is not a non-empty, non-invisible string is
blank. A required field whose presence cannot be established is absent.
"""

from __future__ import annotations

from typing import Any

#: Zero-width / BOM code points that carry NO visible content: zero-width space, ZWNJ,
#: ZWJ, word joiner, BOM / zero-width no-break space.
ZERO_WIDTH_CHARS = "​‌‍⁠﻿"
ZERO_WIDTH_TRANSLATION = dict.fromkeys(map(ord, ZERO_WIDTH_CHARS))


def is_blank(value: Any) -> bool:
    """True when `value` cannot serve as a present, required textual field.

    Any non-`str` value (`None`, `int`, `list`, `dict`, `bool`, `0`, `False`, …) is
    blank: a required field that is not textual is unusable, and "cannot decide
    presence = not present". A `str` is blank when it is empty or whitespace-only once
    the invisible code points above are removed.
    """
    if not isinstance(value, str):
        return True
    return not value.translate(ZERO_WIDTH_TRANSLATION).strip()


__all__ = ["ZERO_WIDTH_CHARS", "ZERO_WIDTH_TRANSLATION", "is_blank"]
