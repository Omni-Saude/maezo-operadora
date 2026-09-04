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

import copy
from typing import Any

import pytest

from maezo.runtime.prompt_format import (
    FATOS_CONTEXTO_ROTULO,
    STABLE_SEPARATOR,
    FormattedPrompt,
    PromptFormatError,
    format_cached_prompt,
    render_fatos_para_prompt,
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


# =============================================================================================
# `render_fatos_para_prompt` — nao-mutacao do dicionario do chamador (CC-11 §Delta, F1)
#
# POR QUE ISTO E' UM TESTE E NAO UMA LEITURA DE CODIGO. O dicionario de fatos que chega aqui e'
# o MESMO objeto que o dossie devolve ao registro de auditoria (ADR-0007): os 11 sitios de
# montagem passam `facts` ao prompt e o guardam adiante. Se a renderizacao poda um sub-dicionario
# no lugar, o fato apurado SOME do registro — o defeito de 24/08/2026 (`False` indistinguivel de
# ausencia) reaparece do outro lado, agora como ausencia de verdade. A copia rasa so' do topo
# escondia isso ate' profundidade 2, que e' o maximo que os adotantes de hoje usam; o primeiro
# mapa com tres niveis o acordaria em producao.
# =============================================================================================


def _fatos_aninhados(profundidade: int) -> dict[str, Any]:
    """Fatos com `profundidade` niveis: o booleano na folha, com vizinhos em cada nivel.

    Os vizinhos (`vizinho`, `irmaoN`) existem para que a poda tenha o que PRESERVAR — um
    dicionario que so' contem a chave podada nao distingue "copiou e podou" de "nao fez nada".
    """
    interno: dict[str, Any] = {"fato": False, "vizinho": 1}
    for nivel in range(profundidade - 1, 0, -1):
        interno = {f"n{nivel}": interno, f"irmao{nivel}": nivel}
    return interno


def _caminho_do_fato(profundidade: int) -> str:
    return ".".join([f"n{nivel}" for nivel in range(1, profundidade)] + ["fato"])


@pytest.mark.parametrize("profundidade", [1, 2, 3, 4])
def test_o_render_nao_muta_o_dicionario_de_fatos_do_chamador(profundidade: int) -> None:
    """O chamador recebe de volta exatamente o dicionario que entregou, em qualquer profundidade.

    As duas asercoes andam juntas de proposito: a primeira proibe a mutacao, a segunda proibe a
    "correcao" preguicosa de simplesmente parar de podar — a chave booleana tem de continuar
    saindo do contexto, senao o modelo le o mesmo fato duas vezes (`NAO  rotulo` e, logo abaixo,
    `'fato': False`), que e' o defeito que CC-11 conserta.
    """
    facts = _fatos_aninhados(profundidade)
    caminho = _caminho_do_fato(profundidade)
    antes = copy.deepcopy(facts)

    saida = render_fatos_para_prompt(facts, booleanos={caminho: "rotulo do fato"})

    assert facts == antes, f"o dicionario do chamador foi mutado: {facts!r} != {antes!r}"
    assert "NAO       rotulo do fato" in saida
    contexto = saida.split(FATOS_CONTEXTO_ROTULO, 1)[1]
    assert "'fato'" not in contexto, "a chave ja' nomeada nao pode sobrar no repr do contexto"
    assert "'vizinho': 1" in contexto, "a poda nao pode levar os vizinhos junto"


def test_dois_booleanos_irmaos_no_mesmo_pai_nao_mutam_o_chamador() -> None:
    """Duas podas no MESMO sub-dicionario: a segunda parte do resultado da primeira.

    O caso que uma copia feita uma unica vez, fora do laco, ainda erraria.
    """
    facts = {"a": {"b": {"c": False, "d": True, "resto": 9}}}
    antes = copy.deepcopy(facts)

    saida = render_fatos_para_prompt(facts, booleanos={"a.b.c": "rc", "a.b.d": "rd"})

    assert facts == antes, f"o dicionario do chamador foi mutado: {facts!r}"
    assert saida.endswith("demais dados do caso: {'a': {'b': {'resto': 9}}}")


def test_um_intermediario_que_nao_e_mapping_nao_muta_o_chamador() -> None:
    """Caminho que atravessa um valor escalar: SEM DADO, e nada e' escrito em lugar nenhum."""
    facts = {"a": {"b": "texto"}, "z": 1}
    antes = copy.deepcopy(facts)

    saida = render_fatos_para_prompt(facts, booleanos={"a.b.c": "rc"})

    assert facts == antes, f"o dicionario do chamador foi mutado: {facts!r}"
    assert "SEM DADO  rc" in saida


def test_um_caminho_inexistente_nao_muta_o_chamador() -> None:
    """A folha nao esta la': SEM DADO, e o contexto sai intacto."""
    facts = {"a": {"b": {}}, "z": 1}
    antes = copy.deepcopy(facts)

    saida = render_fatos_para_prompt(facts, booleanos={"a.b.c": "rc"})

    assert facts == antes, f"o dicionario do chamador foi mutado: {facts!r}"
    assert saida.endswith("demais dados do caso: {'a': {'b': {}}, 'z': 1}")


# =============================================================================================
# Prova de bytes: o reparo F1 corrige o EFEITO COLATERAL, nunca a saida
# =============================================================================================

#: Saidas de `render_fatos_para_prompt` capturadas com a versao ANTERIOR ao reparo F1 (topo do WP
#: `fleet/cc11-render-fatos`, `e366794`), congeladas aqui como literais. Bytes de prompt alimentam
#: proveniencia de auditoria (ADR-0007) e baselines de eval, entao o reparo tinha de ser
#: invisivel para o modelo: mudar a saida exigiria bump de `PROMPT_VERSIONS` e re-revisao de SME,
#: isto e', deixaria de ser um reparo. Congelar o literal (em vez de reexecutar a versao antiga
#: via `git show`) e' o que mantem a prova valida depois de qualquer rebase ou squash do WP.
_SAIDAS_ANTES_DO_REPARO_F1: dict[str, str] = {
    "topo_tres_estados_e_nao_booleanos": (
        "fatos apurados:\n"
        "  NAO       ra\n"
        "  SIM       rb\n"
        "  SEM DADO  rc\n"
        "  SEM DADO  rd\n"
        "  SEM DADO  re\n"
        "  SEM DADO  rf\n"
        "  SEM DADO  rg\n"
        "\n"
        "demais dados do caso: {}"
    ),
    "nivel_2": (
        "fatos apurados:\n"
        "  NAO       ry\n"
        "  SIM       rz\n"
        "\n"
        "demais dados do caso: {'x': {'resto': 3}, 'outro': 1}"
    ),
    "nivel_3": (
        "fatos apurados:\n  NAO       rc\n\ndemais dados do caso: {'a': {'b': {'outro': 2}}, 'top': 'v'}"
    ),
    "nivel_4": ("fatos apurados:\n  SIM       rd\n\ndemais dados do caso: {'a': {'b': {'c': {'e': False}}}}"),
    "irmaos_no_mesmo_pai": (
        "fatos apurados:\n  NAO       rc\n  SIM       rd\n\ndemais dados do caso: {'a': {'b': {'resto': 9}}}"
    ),
    "intermediario_nao_mapping_na_raiz": (
        "fatos apurados:\n  SEM DADO  rc\n\ndemais dados do caso: {'a': 7}"
    ),
    "intermediario_nao_mapping_no_meio": (
        "fatos apurados:\n  SEM DADO  rc\n\ndemais dados do caso: {'a': {'b': 'texto'}}"
    ),
    "folha_inexistente": (
        "fatos apurados:\n  SEM DADO  rc\n\ndemais dados do caso: {'a': {'b': {}}, 'z': 1}"
    ),
    "pai_ausente": ("fatos apurados:\n  SEM DADO  rb\n\ndemais dados do caso: {'z': 1}"),
    "com_linhas_extra": (
        "fatos apurados:\n"
        "  NAO       ra\n"
        "  APURADO PELO MOTOR  teto de aprovacao\n"
        "\n"
        "demais dados do caso: {'resto': {'k': 1}}"
    ),
    "topo_e_pontilhado_misturados": (
        "fatos apurados:\n"
        "  NAO       ra\n"
        "  SEM DADO  rk\n"
        "  NAO       rj\n"
        "\n"
        "demais dados do caso: {'n': {'m': {}}, 'outro': {'p': 1}}"
    ),
    "fatos_vazios": ("fatos apurados:\n  SEM DADO  ra\n\ndemais dados do caso: {}"),
    "chave_de_topo_e_prefixo_de_caminho": (
        "fatos apurados:\n  SEM DADO  ra\n  NAO       rb\n\ndemais dados do caso: {}"
    ),
}

#: As entradas que produziram os literais acima, na mesma ordem. Cobrem topo, niveis 2/3/4,
#: irmaos no mesmo pai, intermediario nao-`Mapping` (na raiz e no meio), folha inexistente, pai
#: ausente, `linhas_extra`, topo e pontilhado misturados, fatos vazios, e a colisao entre uma
#: chave de topo e o prefixo de um caminho.
_FORMAS_DA_PROVA_DE_BYTES: list[tuple[str, dict[str, Any], dict[str, str], tuple[str, ...]]] = [
    (
        "topo_tres_estados_e_nao_booleanos",
        {"a": False, "b": True, "c": None, "d": 1, "e": "sim", "f": [], "g": 0},
        {"a": "ra", "b": "rb", "c": "rc", "d": "rd", "e": "re", "f": "rf", "g": "rg"},
        (),
    ),
    ("nivel_2", {"x": {"y": False, "z": True, "resto": 3}, "outro": 1}, {"x.y": "ry", "x.z": "rz"}, ()),
    ("nivel_3", {"a": {"b": {"c": False, "outro": 2}}, "top": "v"}, {"a.b.c": "rc"}, ()),
    ("nivel_4", {"a": {"b": {"c": {"d": True, "e": False}}}}, {"a.b.c.d": "rd"}, ()),
    (
        "irmaos_no_mesmo_pai",
        {"a": {"b": {"c": False, "d": True, "resto": 9}}},
        {"a.b.c": "rc", "a.b.d": "rd"},
        (),
    ),
    ("intermediario_nao_mapping_na_raiz", {"a": 7}, {"a.b.c": "rc"}, ()),
    ("intermediario_nao_mapping_no_meio", {"a": {"b": "texto"}}, {"a.b.c": "rc"}, ()),
    ("folha_inexistente", {"a": {"b": {}}, "z": 1}, {"a.b.c": "rc"}, ()),
    ("pai_ausente", {"z": 1}, {"a.b": "rb"}, ()),
    (
        "com_linhas_extra",
        {"a": False, "resto": {"k": 1}},
        {"a": "ra"},
        ("  APURADO PELO MOTOR  teto de aprovacao",),
    ),
    (
        "topo_e_pontilhado_misturados",
        {"a": False, "n": {"m": {"k": None, "j": False}}, "outro": {"p": 1}},
        {"a": "ra", "n.m.k": "rk", "n.m.j": "rj"},
        (),
    ),
    ("fatos_vazios", {}, {"a": "ra"}, ()),
    ("chave_de_topo_e_prefixo_de_caminho", {"a": {"b": False}}, {"a": "ra", "a.b": "rb"}, ()),
]


@pytest.mark.parametrize(
    ("nome", "facts", "booleanos", "linhas_extra"),
    _FORMAS_DA_PROVA_DE_BYTES,
    ids=[forma[0] for forma in _FORMAS_DA_PROVA_DE_BYTES],
)
def test_o_reparo_da_copia_por_nivel_nao_muda_um_byte_da_saida(
    nome: str,
    facts: dict[str, Any],
    booleanos: dict[str, str],
    linhas_extra: tuple[str, ...],
) -> None:
    """Byte a byte contra a saida capturada antes do reparo F1, em 13 formas de entrada."""
    saida = render_fatos_para_prompt(copy.deepcopy(facts), booleanos=booleanos, linhas_extra=linhas_extra)

    assert saida == _SAIDAS_ANTES_DO_REPARO_F1[nome]


def test_a_prova_de_bytes_cobre_toda_forma_congelada() -> None:
    """Guarda da propria prova: nenhum literal congelado sem entrada que o produza, e vice-versa."""
    assert {forma[0] for forma in _FORMAS_DA_PROVA_DE_BYTES} == set(_SAIDAS_ANTES_DO_REPARO_F1)
    assert len(_FORMAS_DA_PROVA_DE_BYTES) == len(_SAIDAS_ANTES_DO_REPARO_F1) == 13
