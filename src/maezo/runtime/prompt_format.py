"""Cache-aware prompt formatting (Onda 2, W8) — stable prefix first, variability last.

WHAT PROBLEM THIS SOLVES. Provider-side prompt caching keys on a BYTE-IDENTICAL PREFIX: a
vendor can reuse the cached attention state for request N+1 only up to the first byte where
it differs from request N. Every byte of per-request variability that lands EARLY in a prompt
truncates the cacheable region for everything after it. So the layout rule is structural, not
stylistic — static/system content first, in a deterministic order, and per-request values
strictly last.

WHY A FORMATTING LAYER AND NOT A CONVENTION. The 15 in-repo assembly sites
(`agents/*/graph.py`, all of the shape ``f"{dossier_prompt()}\\n\\nroute={route}\\nfatos={facts}"``)
each happen to satisfy that rule today. They satisfy it INDEPENDENTLY, by each author having
got the order right — nothing checks it, and the failure mode is silent: a prompt that moves
one variable token above the static block still WORKS, it just quietly stops being cacheable.
This module makes the boundary an explicit, testable value instead of an emergent property of
an f-string, and hands the two sides to a transport that can declare a cache breakpoint
between them (see :class:`maezo.runtime.inference.BrRegionalRequest`, whose
``stable_prefix``/``variable_suffix`` fields exist for exactly this).

WHAT THIS MODULE DOES NOT DO. It cannot verify that a segment a caller DECLARED stable really
is stable across requests — that is a property of the caller's data, not of any string handed
to a function here. What it does is make the declaration explicit and its consequence
observable: :attr:`FormattedPrompt.stable_prefix` is exactly the region a caller is claiming
is reusable, so a mis-declared value shows up as a prefix that differs between two requests
instead of hiding inside one opaque blob.
:func:`stable_prefix_is_byte_stable` is that check, offered as a first-class function so a
test (or a future canary) asserts the property rather than eyeballing it.

PHI DISCIPLINE. Pure string assembly: no logging, no telemetry, no I/O, no hashing of content
into anything that leaves this frame. Prompt bytes travel only in the returned value.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

#: Separator BETWEEN consecutive static segments, and between the static block and the
#: variable block. Two newlines, matching the shape every in-repo assembly site already emits
#: (`f"{dossier_prompt()}\n\n..."`), so adopting this formatter is byte-neutral at those sites
#: rather than a silent prompt change — prompt bytes feed `PROMPT_VERSIONS` audit provenance
#: (ADR-0007) and eval baselines, so changing them without a version bump would be a defect.
STABLE_SEPARATOR: Final[str] = "\n\n"

#: Separator between variable LINES. One newline — again matching the existing sites.
VARIABLE_LINE_SEPARATOR: Final[str] = "\n"

#: Separator between the ``key=value`` pairs that share one variable line.
VARIABLE_FIELD_SEPARATOR: Final[str] = " "

#: One line of per-request variability: an ordered sequence of ``(key, value)`` pairs rendered
#: as ``key=value`` and joined by a space. Ordered, never sorted — prompt semantics can depend
#: on field order (`agents/helena/prompts.py` docstring: "the donor prompt is the SME-reviewable
#: baseline and LLM prompt ordering can matter"), so this module preserves what the caller wrote
#: and never reorders it on its own authority.
VariableLine = Sequence[tuple[str, object]]


class PromptFormatError(ValueError):
    """Raised when a prompt layout cannot have a meaningful cacheable prefix.

    Fail-closed rather than silently producing an uncacheable prompt: a caller that asks for
    cache-aware formatting and supplies nothing cacheable has a bug at the call site, and a
    formatter that quietly returned an empty prefix would hide it behind a working prompt.
    """


@dataclass(frozen=True, slots=True)
class FormattedPrompt:
    """A prompt split at its cache boundary.

    ``stable_prefix + variable_suffix == text``, exactly — the split is a VIEW of one string,
    never a transformation of it. That identity is what lets a transport send a cache
    breakpoint at ``len(stable_prefix)`` and still transmit the same prompt the model would
    have seen unsplit.

    Frozen + slots, mirroring `ProviderCapabilities` (`runtime/inference.py`): a layout that
    has been computed is a fact about one request, not a mutable buffer.
    """

    #: Everything a caller declared static, plus the trailing :data:`STABLE_SEPARATOR`. The
    #: separator belongs to the PREFIX on purpose: it is itself byte-stable, so including it
    #: extends the cacheable region by those bytes instead of donating them to the suffix.
    stable_prefix: str

    #: Everything a caller declared per-request. May be empty (a prompt with no variability at
    #: all is entirely cacheable, which is legitimate).
    variable_suffix: str

    @property
    def text(self) -> str:
        """The full prompt as a provider would receive it."""
        return self.stable_prefix + self.variable_suffix

    @property
    def stable_prefix_chars(self) -> int:
        """Length of the cacheable region, in characters.

        CHARACTERS, NOT TOKENS, and deliberately not an estimate of tokens: this module owns no
        tokenizer and inventing a chars-to-tokens ratio would be a fabricated number. A real
        cached-token count comes back from the provider's own usage response
        (``BrRegionalResponse.cached_prefix_tokens``), never from arithmetic here.
        """
        return len(self.stable_prefix)


def format_cached_prompt(
    stable_segments: Sequence[str],
    variable_lines: Sequence[VariableLine] = (),
) -> FormattedPrompt:
    """Assemble a prompt with the maximal byte-stable prefix the caller's declaration allows.

    Args:
        stable_segments: Static/system content, in the order it should appear. Joined by
            :data:`STABLE_SEPARATOR`. Order is preserved verbatim — see :data:`VariableLine`.
        variable_lines: Per-request content, one entry per output line, each a sequence of
            ``(key, value)`` pairs rendered ``key=value``. Values are rendered with ``str()``
            (``f"{value}"`` semantics), matching what the existing f-string sites emit for the
            dicts and scalars they pass.

    Returns:
        A :class:`FormattedPrompt` whose ``stable_prefix`` depends ONLY on ``stable_segments``.
        That is the whole contract: two calls sharing ``stable_segments`` produce byte-identical
        prefixes no matter how their ``variable_lines`` differ.

    Raises:
        PromptFormatError: no non-empty static segment was supplied, so there is no cacheable
            prefix to speak of.
    """
    kept = [segment for segment in stable_segments if segment]
    if not kept:
        raise PromptFormatError(
            "cache-aware formatting requires at least one non-empty static segment — a prompt "
            "with no stable region has no cacheable prefix, and returning one silently would "
            "hide the defect at the call site. Pass the system/instruction text as a stable "
            "segment and per-request values as variable lines."
        )

    stable_prefix = STABLE_SEPARATOR.join(kept) + STABLE_SEPARATOR
    variable_suffix = VARIABLE_LINE_SEPARATOR.join(
        VARIABLE_FIELD_SEPARATOR.join(f"{key}={value}" for key, value in line) for line in variable_lines
    )
    return FormattedPrompt(stable_prefix=stable_prefix, variable_suffix=variable_suffix)


def stable_prefix_is_byte_stable(prompts: Iterable[FormattedPrompt]) -> bool:
    """True if every prompt in ``prompts`` shares one byte-identical ``stable_prefix``.

    The property this module exists to provide, expressed as a predicate so it can be ASSERTED
    over a set of real requests rather than assumed from the layout. An empty or single-element
    input is vacuously stable, which is the honest answer: one request proves nothing about
    reuse.
    """
    prefixes = {prompt.stable_prefix for prompt in prompts}
    return len(prefixes) <= 1


__all__ = [
    "STABLE_SEPARATOR",
    "VARIABLE_FIELD_SEPARATOR",
    "VARIABLE_LINE_SEPARATOR",
    "FormattedPrompt",
    "PromptFormatError",
    "VariableLine",
    "format_cached_prompt",
    "stable_prefix_is_byte_stable",
]
