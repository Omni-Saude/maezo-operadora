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


# ---------------------------------------------------------------------------
# REP-EVALS repair (VERIFY-WP-EVALS.md #2a/#2b/#2c): three hardening fixes to the reusable
# `score_clarity` primitive, each with its own RED->GREEN proof. These tests must FAIL against
# the pre-repair `_harness.py` (the whole point of the exercise) and PASS after it.
# ---------------------------------------------------------------------------


class TestScoreClarityMinWords:
    """VERIFY-WP-EVALS.md #2a: an empty `response_text` passed vacuously whenever a golden's
    `required_disclaimers` was `[]` (exactly EVL-HELENA-CLAREZA-02's shape). Fixed with a
    `min_words` floor (default 3) surfaced on `ClarityReport.word_count`/`.min_words`."""

    def test_empty_text_is_flagged_too_short_even_with_no_other_rules(self) -> None:
        report = score_clarity("", max_words_per_sentence=20, required_disclaimers=[])
        assert report.word_count == 0
        assert report.word_count < report.min_words

    def test_assert_clarity_raises_on_empty_text_with_no_required_disclaimers(self) -> None:
        """The exact CLAREZA-02 shape the verifier flagged: an empty response_text with
        `required_disclaimers=[]` must NOT pass vacuously."""
        report = score_clarity("", max_words_per_sentence=20, forbidden_jargon=[], required_disclaimers=[])
        with pytest.raises(AssertionError, match="empty or too short"):
            assert_clarity(report)

    def test_whitespace_only_text_is_also_too_short(self) -> None:
        report = score_clarity("   \n  ", max_words_per_sentence=20)
        with pytest.raises(AssertionError, match="empty or too short"):
            assert_clarity(report)

    def test_default_min_words_does_not_penalize_a_normal_short_reply(self) -> None:
        """Regression guard: the new floor must not make a legitimate short reply fail."""
        report = score_clarity("Um atendente vai ajudar.", max_words_per_sentence=20)
        assert report.word_count >= report.min_words
        assert_clarity(report)  # must not raise

    def test_custom_min_words_can_be_tightened(self) -> None:
        report = score_clarity("Oi tudo bem", max_words_per_sentence=20, min_words=5)
        assert report.word_count == 3
        with pytest.raises(AssertionError, match="empty or too short"):
            assert_clarity(report)

    def test_custom_min_words_can_be_relaxed_to_allow_a_one_word_reply(self) -> None:
        report = score_clarity("Ok.", max_words_per_sentence=20, min_words=1)
        assert_clarity(report)  # must not raise


class TestSentenceSplitterAbbreviations:
    """VERIFY-WP-EVALS.md #2b: the `.`-based sentence splitter was fooled by PT-BR title
    abbreviations ("Dra.", "Sr.", "Sra.", "Dr.", "p. ex.", "etc.") -- a too-long run-on could
    fragment at the abbreviation's period into two under-cap pieces. Fixed by protecting a
    documented abbreviation list (plus digit-dot-digit decimals) before splitting."""

    @pytest.mark.parametrize("abbrev", ["Dr.", "Dra.", "Sr.", "Sra.", "Srta."])
    def test_title_abbreviation_before_a_name_does_not_create_a_false_boundary(self, abbrev: str) -> None:
        text = (
            f"Um profissional muito atencioso, o(a) {abbrev} Fulano, vai revisar "
            "seu caso com cuidado e atencao."
        )
        report = score_clarity(text, max_words_per_sentence=1000)
        assert len(report.sentences) == 1

    def test_etc_abbreviation_does_not_create_a_false_boundary(self) -> None:
        text = (
            "Voce pode trazer identidade, cartao do plano, etc. para agilizar "
            "o atendimento no dia da consulta."
        )
        report = score_clarity(text, max_words_per_sentence=1000)
        assert len(report.sentences) == 1

    def test_p_ex_abbreviation_does_not_create_a_false_boundary(self) -> None:
        text = "Alguns sintomas, p. ex. febre e tosse, podem indicar necessidade de avaliacao."
        report = score_clarity(text, max_words_per_sentence=1000)
        assert len(report.sentences) == 1

    def test_decimal_number_does_not_create_a_false_boundary(self) -> None:
        text = "O valor de referencia e 37.5 e esta dentro do esperado para o caso."
        report = score_clarity(text, max_words_per_sentence=1000)
        assert len(report.sentences) == 1

    def test_the_verifiers_exact_exploit_text_is_now_correctly_flagged_as_one_long_sentence(self) -> None:
        """Reproduces VERIFY-WP-EVALS.md #2b byte-for-byte: a 27-word run-on that used to
        fragment at "Dra." into two under-cap pieces must now be read as ONE sentence and trip
        a 20-word cap."""
        text = (
            "Um profissional de saude muito experiente e cuidadoso vai atender voce em breve "
            "sendo provavelmente a Dra. Fernanda que vai revisar tudo com atencao redobrada e cuidado."
        )
        report = score_clarity(text, max_words_per_sentence=20)
        assert len(report.sentences) == 1
        assert report.long_sentences
        _sentence, word_count = report.long_sentences[0]
        assert word_count == 27

    def test_abbreviation_period_is_restored_verbatim_in_the_returned_sentence(self) -> None:
        text = "A Dra. Ana vai continuar."
        report = score_clarity(text, max_words_per_sentence=1000)
        assert report.sentences == ["A Dra. Ana vai continuar."]

    def test_a_real_sentence_boundary_right_after_an_abbreviation_clause_still_splits(self) -> None:
        """Regression guard: protecting the abbreviation's OWN period must not swallow the NEXT,
        genuinely terminal period too."""
        text = "A Dra. Ana vai te atender. Aguarde um momento."
        report = score_clarity(text, max_words_per_sentence=1000)
        assert report.sentences == ["A Dra. Ana vai te atender.", "Aguarde um momento."]

    def test_two_genuinely_separate_sentences_still_split_when_no_abbreviation_is_involved(self) -> None:
        """Regression guard: the fix must not collapse normal multi-sentence prose into one."""
        report = score_clarity("Frase curta um. Frase curta dois.", max_words_per_sentence=1000)
        assert len(report.sentences) == 2


class TestDisclaimerWordBoundary:
    """VERIFY-WP-EVALS.md #2c: the disclaimer check matched ANY substring, so "sobre-humano"
    satisfied the "humano" alternative. Fixed with a word/phrase-boundary match that treats a
    hyphen as word-joining."""

    def test_hyphenated_compound_does_not_satisfy_a_bare_word_alternative(self) -> None:
        """Reproduces VERIFY-WP-EVALS.md #2c byte-for-byte: "sobre-humano" must NOT satisfy the
        "humano" disclaimer alternative."""
        report = score_clarity(
            "Isso exige um esforco sobre-humano da nossa equipe, mas nao teremos como ajudar agora.",
            max_words_per_sentence=1000,
            required_disclaimers=[["profissional", "humano", "atendente"]],
        )
        assert report.missing_disclaimer_groups == [("profissional", "humano", "atendente")]

    def test_a_standalone_disclaimer_word_still_satisfies_its_group(self) -> None:
        """Regression guard: the fix must not make legitimate matches disappear."""
        report = score_clarity(
            "Um atendente humano vai continuar seu caso.",
            max_words_per_sentence=1000,
            required_disclaimers=[["profissional", "humano", "atendente"]],
        )
        assert report.missing_disclaimer_groups == []

    def test_disclaimer_match_is_still_case_insensitive(self) -> None:
        report = score_clarity(
            "UM ATENDENTE HUMANO VAI CONTINUAR.",
            max_words_per_sentence=1000,
            required_disclaimers=[["humano"]],
        )
        assert report.missing_disclaimer_groups == []

    def test_disclaimer_alternative_as_a_multi_word_phrase_matches_literally(self) -> None:
        report = score_clarity(
            "Por favor, fale com um atendente humano assim que possivel.",
            max_words_per_sentence=1000,
            required_disclaimers=[["fale com um atendente"]],
        )
        assert report.missing_disclaimer_groups == []

    def test_multi_word_phrase_partial_word_overlap_does_not_falsely_match(self) -> None:
        """The phrase alternative requires the whole phrase, not a lucky partial overlap."""
        report = score_clarity(
            "Um atendente vai continuar.",
            max_words_per_sentence=1000,
            required_disclaimers=[["fale com um atendente"]],
        )
        assert report.missing_disclaimer_groups == [("fale com um atendente",)]

    def test_other_hyphenated_humano_compounds_also_do_not_satisfy_humano(self) -> None:
        """Same collision class as "sobre-humano": any hyphen-joined compound ending in
        "-humano" must not satisfy the bare "humano" alternative either."""
        for collision in ("quase-humano", "pos-humano", "super-humano"):
            report = score_clarity(
                f"Isso seria um esforco {collision} da nossa equipe.",
                max_words_per_sentence=1000,
                required_disclaimers=[["humano"]],
            )
            assert report.missing_disclaimer_groups == [("humano",)], collision
