"""Cache-aware prompt formatting (Onda 2, W8) — the stable-prefix contract.

Offline and pure: this module performs no I/O, so every test here is a byte-level assertion on
string assembly.

WHAT IS ACTUALLY BEING PROVEN. The single property provider-side prompt caching depends on is
that the prefix is BYTE-IDENTICAL across requests. Everything else — separators, ordering, field
rendering — matters only insofar as it feeds that. So the tests are organised around it: the
property itself, the ways it can be destroyed, and a byte-compatibility proof against the shape
the 15 in-repo assembly sites already emit (so adopting the formatter at those sites would be a
refactor, not a silent prompt change).
"""

from __future__ import annotations

import pytest

from maezo.runtime.prompt_format import (
    STABLE_SEPARATOR,
    FormattedPrompt,
    PromptFormatError,
    format_cached_prompt,
    stable_prefix_is_byte_stable,
)

_STATIC = "Voce e um assistente. Responda apenas com fatos."


# =============================================================================================
# The stable-prefix property
# =============================================================================================


def test_the_prefix_is_byte_identical_across_wildly_different_variable_content() -> None:
    """THE PROPERTY. Same static declaration, arbitrarily different per-request values."""
    first = format_cached_prompt([_STATIC], [[("caso", "A"), ("fatos", {"x": 1})]])
    second = format_cached_prompt([_STATIC], [[("caso", "B" * 500), ("fatos", {"y": [1, 2, 3]})]])

    assert first.stable_prefix == second.stable_prefix
    assert first.variable_suffix != second.variable_suffix
    assert stable_prefix_is_byte_stable([first, second])


def test_a_prompt_with_no_variable_content_is_entirely_cacheable() -> None:
    """Legitimate edge: an all-static prompt has an empty suffix, not a missing one."""
    formatted = format_cached_prompt([_STATIC])

    assert formatted.variable_suffix == ""
    assert formatted.text == formatted.stable_prefix


def test_changing_the_static_declaration_changes_the_prefix() -> None:
    """OVER-FIRE CONTROL. Without this, ``stable_prefix`` could be a constant and every
    stability assertion above would pass while proving nothing."""
    first = format_cached_prompt([_STATIC])
    second = format_cached_prompt([_STATIC + " Seja breve."])

    assert first.stable_prefix != second.stable_prefix
    assert not stable_prefix_is_byte_stable([first, second])


def test_declaring_per_request_content_as_static_destroys_the_property() -> None:
    """THE MISTAKE W8 EXISTS TO MAKE VISIBLE, at the formatting layer.

    The mis-declared arrangement produces a perfectly valid prompt — that is exactly why the
    defect is silent in production. What it stops producing is a reusable prefix.
    """
    correct = [format_cached_prompt([_STATIC], [[("caso", c)]]) for c in ("A", "B")]
    mis_declared = [format_cached_prompt([_STATIC, f"caso={c}"]) for c in ("A", "B")]

    assert stable_prefix_is_byte_stable(correct)
    assert not stable_prefix_is_byte_stable(mis_declared)
    # The two arrangements convey THE SAME CONTENT — they differ only by the trailing separator
    # the static block gets. Nothing about the prompt text signals the defect; the entire
    # difference is WHERE THE CACHE BOUNDARY SITS, which is why it has to be asserted on the
    # prefix bytes and cannot be caught by inspecting the finished prompt.
    assert mis_declared[0].text == correct[0].text + STABLE_SEPARATOR
    assert correct[0].stable_prefix_chars < mis_declared[0].stable_prefix_chars


def test_the_split_is_a_view_never_a_rewrite() -> None:
    """``stable_prefix + variable_suffix == text``, exactly.

    Prompt bytes feed ``PROMPT_VERSIONS`` audit provenance (ADR-0007); a formatter that altered
    them while claiming to only reorganise them would be a provenance defect.
    """
    formatted = format_cached_prompt([_STATIC, "Regra 2."], [[("a", 1)], [("b", 2)]])

    assert formatted.stable_prefix + formatted.variable_suffix == formatted.text
    assert formatted.stable_prefix_chars == len(formatted.stable_prefix)


def test_formatting_is_deterministic() -> None:
    """No clock, no set iteration, no dict reordering — identical inputs, identical bytes."""
    args = ([_STATIC, "Regra 2."], [[("z", 1), ("a", 2)], [("m", {"k": "v"})]])

    assert format_cached_prompt(*args) == format_cached_prompt(*args)


def test_field_order_is_preserved_and_never_sorted() -> None:
    """Declaration order is kept verbatim.

    ``agents/helena/prompts.py`` records that prompt field ORDER can matter and that the
    SME-reviewed baseline is byte-matched deliberately. A formatter that sorted keys "for
    determinism" would silently rewrite every adopted prompt.
    """
    formatted = format_cached_prompt([_STATIC], [[("zulu", 1), ("alpha", 2)]])

    assert formatted.variable_suffix == "zulu=1 alpha=2"


def test_empty_static_segments_are_dropped_not_rendered_as_blank_lines() -> None:
    """An optional segment that is absent must not perturb the prefix of the ones that remain."""
    assert format_cached_prompt([_STATIC, ""]).stable_prefix == format_cached_prompt([_STATIC]).stable_prefix


def test_a_layout_with_no_cacheable_prefix_is_refused() -> None:
    """FAIL-CLOSED: asking for cache-aware formatting with nothing cacheable is a call-site bug.

    Returning an empty prefix would hide it behind a prompt that still works.
    """
    with pytest.raises(PromptFormatError, match="at least one non-empty static segment"):
        format_cached_prompt([])

    with pytest.raises(PromptFormatError):
        format_cached_prompt(["", ""])


# =============================================================================================
# The stability predicate itself
# =============================================================================================


def test_stability_over_fewer_than_two_prompts_is_vacuously_true() -> None:
    """Honest answer for a degenerate input: one request proves nothing about reuse."""
    assert stable_prefix_is_byte_stable([])
    assert stable_prefix_is_byte_stable([format_cached_prompt([_STATIC])])


def test_one_divergent_prompt_breaks_stability_for_the_whole_set() -> None:
    """The predicate is over the SET — two matching prefixes plus one stray is not stable."""
    same = [format_cached_prompt([_STATIC], [[("i", i)]]) for i in range(2)]
    stray = format_cached_prompt(["outra instrucao"])

    assert stable_prefix_is_byte_stable(same)
    assert not stable_prefix_is_byte_stable([*same, stray])


def test_formatted_prompt_is_frozen() -> None:
    """A computed layout is a fact about one request, not a mutable buffer."""
    formatted = format_cached_prompt([_STATIC])

    with pytest.raises(AttributeError):
        formatted.stable_prefix = "mutated"  # type: ignore[misc]


# =============================================================================================
# Byte-compatibility with the assembly shape the repo already emits
# =============================================================================================


def test_the_formatter_reproduces_the_existing_call_site_layout_byte_for_byte() -> None:
    """ADOPTION SAFETY, proven rather than asserted.

    The in-repo sites build their prompts as
    ``f"{dossier_prompt()}\\n\\nroute={route} motivo_auditor={motivo}\\nfatos={facts}"``
    (`agents/rafael/graph.py`, and the same shape in eight sibling graphs). CC-11 replaced the
    trailing ``fatos={facts}`` segment with `render_fatos_para_prompt`'s block; the LAYOUT this
    test pins — one static segment, then variable lines — is the one those sites still emit and
    the one any future rewiring starts from. Rewiring those sites
    onto this formatter must not change a single byte — prompt bytes feed ``PROMPT_VERSIONS``
    audit provenance and eval baselines, so a refactor that shifted them would need a version
    bump and an SME re-review, i.e. it would not be a refactor at all.

    This test is what makes that adoption a mechanical follow-up instead of a claim. The
    expected string is written out as the literal f-string the call sites use, NOT built from
    the formatter's own separators.
    """
    instructions = "Tarefa: monte um resumo factual."
    route = "human_review"
    motivo = "valor_acima_do_teto"
    facts = {"procedimento": "X", "valor": 1234.5}

    existing = f"{instructions}\n\nroute={route} motivo_auditor={motivo}\nfatos={facts}"
    formatted = format_cached_prompt(
        [instructions],
        [[("route", route), ("motivo_auditor", motivo)], [("fatos", facts)]],
    )

    assert formatted.text == existing
    # …and the split lands exactly where caching wants it: everything before the variable block.
    assert formatted.stable_prefix == f"{instructions}\n\n"


def test_values_render_with_str_semantics_like_an_fstring() -> None:
    """``f"{value}"`` semantics, so adopted call sites keep emitting what they emit today."""
    formatted = format_cached_prompt([_STATIC], [[("d", {"a": 1}), ("n", None), ("f", 1.5)]])

    assert formatted.variable_suffix == "d={'a': 1} n=None f=1.5"


def test_a_directly_constructed_formatted_prompt_needs_no_separator() -> None:
    """The value type is usable without the builder.

    ``BrResidentInferenceProvider.generate`` constructs one directly — ``stable_prefix=""`` with
    the caller's opaque prompt as the suffix — precisely to avoid the builder's separator being
    appended to a prompt whose bytes must not change.
    """
    formatted = FormattedPrompt(stable_prefix="", variable_suffix="prompt opaco")

    assert formatted.text == "prompt opaco"
    assert formatted.stable_prefix_chars == 0
