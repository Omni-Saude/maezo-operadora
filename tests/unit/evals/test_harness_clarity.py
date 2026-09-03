"""Unit tests for `tests/evals/_harness.py`'s clarity/legibility helpers (gap 10.3, WP-EVALS).

Pure-logic tests, independent of any golden JSON file or agent graph — they exercise
`score_clarity`/`assert_clarity` and the two clarity-specific mutation helpers
(`mutate_extend_last_sentence`/`mutate_replace_last_response`) directly, proving each check is
real (computed from the actual input text, never a constant/fabricated pass) BEFORE any golden
dataset depends on them. Complements, does not replace,
`tests/evals/test_helena_clarity_evals.py`'s end-to-end proof against the real Helena graph.
"""

from __future__ import annotations

import pytest

from tests.evals._harness import (
    assert_clarity,
    mutate_extend_last_sentence,
    mutate_replace_last_response,
    score_clarity,
)


class TestScoreClaritySentenceLength:
    def test_short_sentences_are_not_flagged(self) -> None:
        report = score_clarity(
            "Um profissional vai continuar seu atendimento. Fique tranquilo.",
            max_words_per_sentence=10,
        )
        assert report.long_sentences == []

    def test_a_sentence_over_the_word_limit_is_flagged_with_its_word_count(self) -> None:
        long_sentence = "Um " + "profissional " * 15 + "vai continuar."
        report = score_clarity(long_sentence, max_words_per_sentence=10)
        assert len(report.long_sentences) == 1
        sentence, word_count = report.long_sentences[0]
        assert word_count == len(sentence.split())
        assert word_count > 10

    def test_only_the_offending_sentence_is_flagged_not_the_whole_text(self) -> None:
        text = "Frase curta. " + "palavra " * 20 + ". Outra frase curta."
        report = score_clarity(text, max_words_per_sentence=10)
        assert len(report.long_sentences) == 1

    def test_boundary_word_count_equal_to_limit_is_not_flagged(self) -> None:
        exactly_ten_words = " ".join(["palavra"] * 10) + "."
        report = score_clarity(exactly_ten_words, max_words_per_sentence=10)
        assert report.long_sentences == []

    def test_boundary_word_count_one_over_limit_is_flagged(self) -> None:
        eleven_words = " ".join(["palavra"] * 11) + "."
        report = score_clarity(eleven_words, max_words_per_sentence=10)
        assert len(report.long_sentences) == 1

    def test_accented_portuguese_words_count_correctly(self) -> None:
        # 7 words, several accented — must not be undercounted by a naive ASCII word regex.
        text = "Você não está sozinho, informação e atenção garantidas."
        report = score_clarity(text, max_words_per_sentence=100)
        assert report.long_sentences == []
        # sanity: re-run with a limit BELOW the real word count to prove counting isn't a no-op.
        report_tight = score_clarity(text, max_words_per_sentence=3)
        assert report_tight.long_sentences


class TestScoreClarityForbiddenJargon:
    def test_no_hits_when_none_of_the_terms_are_present(self) -> None:
        report = score_clarity(
            "Um atendente vai te ajudar.", max_words_per_sentence=30, forbidden_jargon=["DMN", "red_flag"]
        )
        assert report.jargon_hits == []

    def test_hits_are_case_insensitive(self) -> None:
        report = score_clarity(
            "Seu caso foi classificado como RED_FLAG pela nossa tabela DMN.",
            max_words_per_sentence=30,
            forbidden_jargon=["red_flag", "dmn"],
        )
        assert set(report.jargon_hits) == {"red_flag", "dmn"}

    def test_empty_forbidden_list_never_flags_anything(self) -> None:
        report = score_clarity("qualquer texto aqui, DMN incluso", max_words_per_sentence=30)
        assert report.jargon_hits == []


class TestScoreClarityRequiredDisclaimers:
    def test_group_satisfied_by_any_single_alternative(self) -> None:
        report = score_clarity(
            "Um atendente vai continuar.",
            max_words_per_sentence=30,
            required_disclaimers=[["profissional", "humano", "atendente"]],
        )
        assert report.missing_disclaimer_groups == []

    def test_group_missing_when_no_alternative_present(self) -> None:
        report = score_clarity(
            "Obrigado pela mensagem.",
            max_words_per_sentence=30,
            required_disclaimers=[["profissional", "humano", "atendente"]],
        )
        assert report.missing_disclaimer_groups == [("profissional", "humano", "atendente")]

    def test_multiple_groups_are_checked_independently(self) -> None:
        report = score_clarity(
            "Um atendente vai continuar.",
            max_words_per_sentence=30,
            required_disclaimers=[["atendente"], ["emergencia"]],
        )
        assert report.missing_disclaimer_groups == [("emergencia",)]

    def test_no_groups_required_never_flags_anything(self) -> None:
        report = score_clarity("qualquer texto", max_words_per_sentence=30)
        assert report.missing_disclaimer_groups == []


class TestAssertClarity:
    def test_clean_report_does_not_raise(self) -> None:
        report = score_clarity(
            "Um profissional de saude vai continuar seu atendimento agora. "
            "Se os sintomas piorarem, procure atendimento de emergencia.",
            max_words_per_sentence=20,
            forbidden_jargon=["red_flag", "DMN"],
            required_disclaimers=[["profissional", "humano"], ["emergencia"]],
        )
        assert_clarity(report)  # must not raise

    def test_long_sentence_violation_names_the_sentence_and_count(self) -> None:
        long_sentence = " ".join(["palavra"] * 25) + "."
        report = score_clarity(long_sentence, max_words_per_sentence=10)
        with pytest.raises(AssertionError, match="max-words-per-sentence"):
            assert_clarity(report)

    def test_jargon_violation_names_the_leaked_terms(self) -> None:
        report = score_clarity(
            "Seu caso e red_flag.", max_words_per_sentence=30, forbidden_jargon=["red_flag"]
        )
        with pytest.raises(AssertionError, match="forbidden jargon"):
            assert_clarity(report)

    def test_missing_disclaimer_violation_names_the_group(self) -> None:
        report = score_clarity(
            "Obrigado.", max_words_per_sentence=30, required_disclaimers=[["humano", "atendente"]]
        )
        with pytest.raises(AssertionError, match="missing mandatory disclaimer"):
            assert_clarity(report)

    def test_all_three_violation_kinds_are_named_together(self) -> None:
        long_sentence = " ".join(["palavra"] * 25) + " red_flag."
        report = score_clarity(
            long_sentence,
            max_words_per_sentence=10,
            forbidden_jargon=["red_flag"],
            required_disclaimers=[["humano"]],
        )
        with pytest.raises(AssertionError) as exc_info:
            assert_clarity(report)
        message = str(exc_info.value)
        assert "max-words-per-sentence" in message
        assert "forbidden jargon" in message
        assert "missing mandatory disclaimer" in message


class TestMutateExtendLastSentence:
    def test_appends_filler_words_to_the_final_sentence_only(self) -> None:
        case = {"id": "TEST", "recorded_llm": ["classify-json", "Um profissional vai continuar."]}
        mutated = mutate_extend_last_sentence(case, extra_words=5)
        extended = mutated["recorded_llm"][-1]
        assert extended.count(".") == 1  # still ONE sentence — no new terminal punctuation
        assert extended.startswith("Um profissional vai continuar")
        assert len(extended.split()) > len(case["recorded_llm"][-1].split())

    def test_earlier_recorded_llm_entries_are_untouched(self) -> None:
        case = {"id": "TEST", "recorded_llm": ["classify-json", "resumo", "resposta final."]}
        mutated = mutate_extend_last_sentence(case, extra_words=3)
        assert mutated["recorded_llm"][0] == "classify-json"
        assert mutated["recorded_llm"][1] == "resumo"

    def test_does_not_mutate_the_original_case(self) -> None:
        case = {"id": "TEST", "recorded_llm": ["resposta original."]}
        mutate_extend_last_sentence(case, extra_words=5)
        assert case["recorded_llm"] == ["resposta original."]

    def test_raises_on_empty_recorded_llm(self) -> None:
        with pytest.raises(ValueError, match="no `recorded_llm` entries"):
            mutate_extend_last_sentence({"id": "TEST", "recorded_llm": []}, extra_words=5)

    def test_extension_actually_crosses_a_realistic_threshold(self) -> None:
        """End-to-end sanity for the intended USE of this helper: a short reply that passes a
        20-word limit today must FAIL that same limit after the mutation."""
        case = {"id": "TEST", "recorded_llm": ["Um profissional vai continuar seu atendimento."]}
        before = score_clarity(case["recorded_llm"][-1], max_words_per_sentence=20)
        assert before.long_sentences == []
        mutated = mutate_extend_last_sentence(case, extra_words=20)
        after = score_clarity(mutated["recorded_llm"][-1], max_words_per_sentence=20)
        assert after.long_sentences


class TestMutateReplaceLastResponse:
    def test_replaces_only_the_final_entry(self) -> None:
        case = {"id": "TEST", "recorded_llm": ["classify-json", "original response"]}
        mutated = mutate_replace_last_response(case, "replacement response")
        assert mutated["recorded_llm"] == ["classify-json", "replacement response"]

    def test_does_not_mutate_the_original_case(self) -> None:
        case = {"id": "TEST", "recorded_llm": ["classify-json", "original response"]}
        mutate_replace_last_response(case, "replacement response")
        assert case["recorded_llm"] == ["classify-json", "original response"]

    def test_raises_on_empty_recorded_llm(self) -> None:
        with pytest.raises(ValueError, match="no `recorded_llm` entries"):
            mutate_replace_last_response({"id": "TEST", "recorded_llm": []}, "x")

    def test_replacement_actually_drops_a_realistic_disclaimer(self) -> None:
        """End-to-end sanity: a reply that satisfies a disclaimer group today must FAIL that
        same requirement after the mutation."""
        case = {"id": "TEST", "recorded_llm": ["Um profissional vai continuar seu atendimento."]}
        before = score_clarity(
            case["recorded_llm"][-1], max_words_per_sentence=30, required_disclaimers=[["profissional"]]
        )
        assert before.missing_disclaimer_groups == []
        mutated = mutate_replace_last_response(case, "Obrigado pela mensagem.")
        after = score_clarity(
            mutated["recorded_llm"][-1], max_words_per_sentence=30, required_disclaimers=[["profissional"]]
        )
        assert after.missing_disclaimer_groups == [("profissional",)]
