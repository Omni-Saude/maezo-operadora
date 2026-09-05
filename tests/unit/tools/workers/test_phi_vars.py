"""Unit tests for the worker PHI-egress redaction seam (`phi_vars.redact_phi_vars`).

One-way, class-token redaction at the engine/Kafka-facing edge (ADR-0006, GAP-XPHI-1). See
`src/maezo/tools/workers/phi_vars.py` for the invariant.
"""

from __future__ import annotations

import datetime
import itertools
from collections.abc import Callable

import pytest

from maezo.tools.workers.phi_vars import (
    _MAX_DOSSIER_DEPTH,
    PHI_FREE_TEXT_VARS,
    PHI_PROCESS_VARS,
    REDACTED_DIGITS,
    REDACTED_EMAIL,
    REDACTED_PHI,
    REDACTED_PHONE,
    redact_error_message,
    redact_free_text,
    redact_free_text_vars,
    redact_phi_vars,
)


def test_redacted_phi_is_a_class_token() -> None:
    """The sentinel is a fixed, non-empty class token — never a real value."""
    assert REDACTED_PHI == "[REDACTED_PHI]"


def test_phi_process_vars_covers_the_denial_grounding_fields() -> None:
    """The three ANS grounding fields are all PHI-named (so the denial worker's egress is covered)."""
    assert {"justificativa_clinica", "cid10_referencia", "fundamentacao_dut"} <= PHI_PROCESS_VARS


@pytest.mark.parametrize("phi_key", sorted(PHI_PROCESS_VARS))
def test_every_phi_named_value_is_redacted(phi_key: str) -> None:
    """Every PHI-named key with a non-empty value is replaced by the class token."""
    out = redact_phi_vars({phi_key: "conteudo clinico sensivel do paciente"})
    assert out[phi_key] == REDACTED_PHI


def test_non_phi_keys_pass_through_untouched() -> None:
    """Correlation identifiers a fact MUST carry are never touched."""
    payload = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-TESTE-0001",
        "business_key": "AUTH-amh-GUIA-TESTE-0001",
        "notice_type": "denial",
    }
    assert redact_phi_vars(payload) == payload


def test_empty_phi_values_pass_through() -> None:
    """None / empty / whitespace-only PHI values have nothing to redact — passed through as-is."""
    out = redact_phi_vars({"justificativa_clinica": None, "cid10_referencia": "", "fundamentacao_dut": "   "})
    assert out["justificativa_clinica"] is None
    assert out["cid10_referencia"] == ""
    assert out["fundamentacao_dut"] == "   "


def test_returns_a_copy_not_a_mutation() -> None:
    """The input mapping is never mutated (backstop on the hot path)."""
    src = {"justificativa_clinica": "texto cru"}
    out = redact_phi_vars(src)
    assert src["justificativa_clinica"] == "texto cru"  # original untouched
    assert out["justificativa_clinica"] == REDACTED_PHI


def test_mixed_payload_redacts_only_phi() -> None:
    """A realistic denial RECORD: clinical fields redacted, structural fields intact.

    The shape mirrors what `SendDenialNoticeWorker` actually returns since
    AUTH-SEND-DENIAL-NOTICE-STATUS-LITERAL — no `status`, because the worker transmits nothing.
    """
    out = redact_phi_vars(
        {
            "notice_type": "denial",
            "tenant_id": "amh",
            "numero_guia_tiss": "GUIA-0001",
            "justificativa_clinica": "Paciente com CID C50.9",
            "cid10_referencia": "C50.9",
            "fundamentacao_dut": "DUT item 3.1",
        }
    )
    assert out["notice_type"] == "denial"
    assert out["tenant_id"] == "amh"
    assert out["numero_guia_tiss"] == "GUIA-0001"
    assert out["justificativa_clinica"] == REDACTED_PHI
    assert out["cid10_referencia"] == REDACTED_PHI
    assert out["fundamentacao_dut"] == REDACTED_PHI
    # Adversarial: no fragment of the planted clinical text survives anywhere in the output.
    blob = " ".join(str(v) for v in out.values())
    assert "C50.9" not in blob
    assert "DUT item 3.1" not in blob


def test_never_raises_on_non_string_phi_value() -> None:
    """A non-string PHI value is redacted fail-closed (never raises, never emitted raw)."""
    out = redact_phi_vars({"laudo": {"nested": "clinical"}, "diagnostico": 12345})
    assert out["laudo"] == REDACTED_PHI
    assert out["diagnostico"] == REDACTED_PHI


# ---------------------------------------------------------------------------
# redact_error_message (T3.4 F5) — exception-message backstop before the engine's
# Cockpit-visible incident store.
# ---------------------------------------------------------------------------


def test_bare_cpf_digit_run_is_redacted() -> None:
    """An unformatted 11-digit run (a raw CPF, or any other long numeric id) is redacted."""
    out = redact_error_message(ValueError("cpf invalido: 12345678901"))
    assert "12345678901" not in out
    assert REDACTED_DIGITS in out


def test_formatted_cpf_is_redacted() -> None:
    """A canonically-punctuated CPF is redacted even though no single digit run is 11+ long."""
    out = redact_error_message(ValueError("beneficiario cpf=123.456.789-01 nao encontrado"))
    assert "123.456.789-01" not in out
    assert REDACTED_DIGITS in out


def test_formatted_cnpj_is_redacted() -> None:
    out = redact_error_message(ValueError("prestador cnpj=12.345.678/0001-90 invalido"))
    assert "12.345.678/0001-90" not in out
    assert REDACTED_DIGITS in out


# -- T3.4 R2 finding F5-1: non-canonical separator styles must be redacted too ------------------


def test_dash_only_cpf_is_redacted() -> None:
    """R2 F5-1 named miss #1: dash-only separators."""
    out = redact_error_message(ValueError("beneficiario cpf=123-456-789-01 nao encontrado"))
    assert "123-456-789-01" not in out
    assert REDACTED_DIGITS in out


def test_space_separated_cpf_is_redacted() -> None:
    """R2 F5-1 named miss #2: space separators."""
    out = redact_error_message(ValueError("cpf informado 123 456 789 01 divergente"))
    assert "123 456 789 01" not in out
    assert REDACTED_DIGITS in out


def test_dots_without_final_dash_cpf_is_redacted() -> None:
    """R2 F5-1 named miss #3: dots in every slot (no final dash)."""
    out = redact_error_message(ValueError("cpf invalido: 123.456.789.01"))
    assert "123.456.789.01" not in out
    assert REDACTED_DIGITS in out


def test_mixed_separator_cpf_is_redacted() -> None:
    """Separator slots are independent — a mixed style must not slip through the family."""
    out = redact_error_message(ValueError("documento 123.456 789-01 rejeitado"))
    assert "123.456 789-01" not in out
    assert REDACTED_DIGITS in out


def test_dash_only_cnpj_is_redacted() -> None:
    """R2 F5-1 named miss #4a: CNPJ dash-only separator variant."""
    out = redact_error_message(ValueError("prestador cnpj=12-345-678-0001-90 invalido"))
    assert "12-345-678-0001-90" not in out
    assert REDACTED_DIGITS in out


def test_space_separated_cnpj_is_redacted() -> None:
    """R2 F5-1 named miss #4b: CNPJ space-separated variant."""
    out = redact_error_message(ValueError("cnpj 12 345 678 0001 90 sem cadastro"))
    assert "12 345 678 0001 90" not in out
    assert REDACTED_DIGITS in out


# -- R2 F5-1 flip side: the widened net must NOT over-redact common non-PHI shapes --------------


def test_uuid_passes_through_readable() -> None:
    """UUIDs survive — the digit-boundary lookarounds stop the CPF/CNPJ family from partially
    matching inside their digit-heavy segments. (Caveat, deliberate: a pathological UUID whose
    final segment happens to be 11+ contiguous DIGITS is still caught by the pre-existing
    bare-digit-run arm — that arm is intentionally that broad; over-redaction is the safe
    direction here.)"""
    msg = "task 9f8b4c2d-1a2b-3c4d-5e6f-7a8b9c0d1e2f not found (correlation 550e8400-e29b-41d4)"
    assert redact_error_message(RuntimeError(msg)) == f"RuntimeError: {msg}"


def test_iso_date_and_timestamp_pass_through_readable() -> None:
    """Dates/timestamps (4-2-2 / 2-2-4 digit groups) do not match the CPF (3-3-3-2) shape."""
    msg = "deadline 2026-07-26 12:30:59 exceeded on 26/07/2026"
    assert redact_error_message(RuntimeError(msg)) == f"RuntimeError: {msg}"


def test_version_string_passes_through_readable() -> None:
    msg = "engine version 2.1.0 (build 10.15.7.2) mismatch"
    assert redact_error_message(RuntimeError(msg)) == f"RuntimeError: {msg}"


def test_ip_address_passes_through_readable() -> None:
    """An IPv4 with 3-digit octets is NOT a CPF: the trailing digit-boundary lookaround refuses
    the partial 3-3-3-2 match inside `192.168.001.001`."""
    msg = "engine unreachable at 192.168.001.001:8080"
    assert redact_error_message(RuntimeError(msg)) == f"RuntimeError: {msg}"


def test_error_class_is_preserved_as_a_stable_prefix() -> None:
    """Ops must still be able to tell WHAT kind of failure occurred from the redacted message."""
    out = redact_error_message(ValueError("cpf invalido: 12345678901"))
    assert out.startswith("ValueError: ")


def test_plain_message_with_no_phi_shape_passes_through_readable() -> None:
    """No false positives: an ordinary message (short ids, prose) is not mangled."""
    exc = RuntimeError("engine unreachable: connection refused on port 8080")
    out = redact_error_message(exc)
    assert out == "RuntimeError: engine unreachable: connection refused on port 8080"


def test_plain_string_with_no_exception_has_no_class_prefix() -> None:
    """A static harness message (not derived from an exception) is passed through un-prefixed."""
    out = redact_error_message("no handler registered for topic 'operadora.test'")
    assert out == "no handler registered for topic 'operadora.test'"


def test_short_numeric_ids_below_the_digit_run_threshold_survive() -> None:
    """A 10-digit (or shorter) numeric id is NOT a false-positive match — only 11+ is redacted."""
    out = redact_error_message(ValueError("task 1234567890 not found"))
    assert "1234567890" in out


def test_long_message_is_capped() -> None:
    """An unbounded/adversarial message length is capped before reaching the incident store."""
    out = redact_error_message(ValueError("x" * 2000))
    assert len(out) < 2000
    assert out.endswith("...[TRUNCATED]")


def test_never_raises_on_a_pathological_input() -> None:
    """The backstop must never itself raise onto the failure-reporting hot path."""

    class _Unstringable:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    out = redact_error_message(_Unstringable())  # type: ignore[arg-type]
    assert out == "[REDACTED_ERROR]"


# =============================================================================
# CC-06 — `redact_free_text`: the shared identifier net (now also the agent -> engine start
# chokepoint's), and `redact_free_text_vars`: the named-key walk over process-start variables.
# Synthetic identifiers only, never real PHI.
# =============================================================================


# -- families REDACTED --------------------------------------------------------------------------


def test_free_text_redacts_a_formatted_cpf() -> None:
    out = redact_free_text("beneficiario informou CPF 123.456.789-09 no atendimento")
    assert "123.456.789-09" not in out
    assert REDACTED_DIGITS in out
    # The sentence around it survives — this is a handoff summary, not an incident message.
    assert out.startswith("beneficiario informou CPF ")


def test_free_text_redacts_a_formatted_cnpj() -> None:
    out = redact_free_text("prestador 12.345.678/0001-90 fora da rede")
    assert "12.345.678/0001-90" not in out and REDACTED_DIGITS in out


def test_free_text_redacts_a_bare_digit_run() -> None:
    out = redact_free_text("cartao nacional de saude 123456789012345")
    assert "123456789012345" not in out and REDACTED_DIGITS in out


@pytest.mark.parametrize(
    "email",
    [
        "beneficiario@exemplo.com",
        "beneficiario.teste@exemplo.com.br",
        "nome+tag@sub.dominio.org",
        "usuario_1@dominio-com-hifen.net",
    ],
)
def test_free_text_redacts_an_email_address(email: str) -> None:
    """MEASURED GAP CLOSED (CC-06): an e-mail carries no 11-digit run and no CPF/CNPJ shape, so
    the pre-CC-06 net let every one of these through unredacted."""
    out = redact_free_text(f"contato do beneficiario: {email} (retorno em 24h)")
    assert email not in out
    assert REDACTED_EMAIL in out
    assert out.startswith("contato do beneficiario: ")


@pytest.mark.parametrize(
    "phone",
    [
        "(11) 98765-4321",
        "(11)98765-4321",
        "11 98765-4321",
        "+55 11 98765-4321",
        "+55 11 3456-7890",
        "(11) 3456-7890",
        "11 98765 4321",
        "98765-4321",
        "98765 4321",
    ],
)
def test_free_text_redacts_a_separated_br_phone(phone: str) -> None:
    """MEASURED GAP CLOSED (CC-06): a BR phone written with separators is 10-11 digits SPLIT by
    punctuation, so `_DIGIT_RUN_RE` (11+ CONTIGUOUS) never saw it."""
    out = redact_free_text(f"beneficiario pediu retorno no telefone {phone} pela manha")
    assert phone not in out
    assert REDACTED_PHONE in out
    assert out.endswith(" pela manha")


# -- FALSE POSITIVES: legitimate short domain numbers must survive -------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Dates, in both conventions the repo writes them.
        "internacao em 26/07/2026, alta prevista 2026-08-02",
        "competencia 08/2026 vencimento 2026-08-10",
        # BRL amounts.
        "valor estimado R$ 12.345,67 e coparticipacao R$ 1.234,56",
        # TUSS / CID / CBHPM style codes.
        "procedimento TUSS 40808010 CID J45.0 tabela 22 porte 6C",
        # Numeric ranges and versions (the shapes a naive 4+4 phone arm would eat).
        "faixa 1000-2000 elegivel; versao 2.1.0 build 10.15.7.2",
        "exercicio 2026-2027 sem reajuste",
        # CEP (5+3 — deliberately outside the mobile arm's 5+4 shape).
        "endereco de atendimento CEP 01310-100",
        # Short numeric ids below the digit-run threshold.
        "protocolo 1234567890 guia 987654 lote 55",
        # Structural references the process contract carries as prose.
        "ver process://amh/dmn/triage@3 e o anexo doc-2026-07",
    ],
)
def test_free_text_leaves_legitimate_domain_numbers_intact(text: str) -> None:
    """The net must not corrupt the clinical/administrative content it is meant to preserve.
    Over-redaction is NOT free here (unlike in an incident message): this same function runs over
    the `resumo_contexto` a human attendant reads to take the case over."""
    assert redact_free_text(text) == text


def test_free_text_caps_an_unbounded_input() -> None:
    out = redact_free_text("x" * 2000)
    assert len(out) < 2000
    assert out.endswith("...[TRUNCATED]")


def test_free_text_cap_is_configurable() -> None:
    out = redact_free_text("y" * 100, max_chars=10)
    assert out == "y" * 10 + "...[TRUNCATED]"


def test_free_text_is_idempotent() -> None:
    """Helena scrubs at the producer AND the chokepoint scrubs again (defense in depth): the
    second pass must be a no-op — the class tokens themselves match no family."""
    once = redact_free_text("CPF 123.456.789-09, mail a@b.com, fone (11) 98765-4321")
    assert redact_free_text(once) == once


# -- `redact_error_message` still behaves as before (it now DELEGATES to `redact_free_text`) -----


def test_error_message_delegates_and_keeps_the_class_prefix() -> None:
    out = redact_error_message(ValueError("beneficiario a@b.com fone (11) 98765-4321"))
    assert out.startswith("ValueError: ")
    assert REDACTED_EMAIL in out and REDACTED_PHONE in out


# -- `redact_free_text_vars` — the named-key walk over process-start variables --------------------


def test_free_text_vars_covers_narrativa_and_the_prose_phi_names() -> None:
    """`narrativa` is the field EVERY `_build_dossier` produces; the prose members of
    `PHI_PROCESS_VARS` are in too. The two STRUCTURED-identifier names are deliberately out
    (documented at the definition): a substring net is the wrong control for a whole-value id.

    CC-06 §Delta: the old shape of this test asserted `PHI_FREE_TEXT_VARS - {"narrativa"} <=
    PHI_PROCESS_VARS`, i.e. that the free-text allowlist could only ever be `PHI_PROCESS_VARS`
    plus `narrativa`. That was never the real invariant — it is a coincidence of the first
    draft's coverage — and it actively FOUGHT the fix for a live leak: the prose names the agent
    graphs actually emit (`lacunas_enriquecimento`, `lacunas`, `motivo_informado`) have no reason
    to appear in the WORKER-leg key list. The invariant pinned here is the one that matters, in
    BOTH directions: every prose member of `PHI_PROCESS_VARS` is covered, and no structured
    identifier ever is."""
    assert "narrativa" in PHI_FREE_TEXT_VARS
    assert {"resumo_contexto", "justificativa_clinica", "laudo", "diagnostico"} <= PHI_FREE_TEXT_VARS
    assert PHI_FREE_TEXT_VARS & {"matricula_beneficiario", "cid10_referencia"} == set()
    # Direction 1 — no prose name of the worker leg is left behind by the agent leg.
    assert PHI_PROCESS_VARS - {"matricula_beneficiario", "cid10_referencia"} <= PHI_FREE_TEXT_VARS
    # Direction 2 — the extra names are exactly the graph-sourced prose fields, enumerated and
    # justified at the definition. A new name arriving here must be argued for, not slipped in.
    assert {
        "narrativa",
        "lacunas_enriquecimento",
        "lacunas",
        "motivo_informado",
    } == PHI_FREE_TEXT_VARS - PHI_PROCESS_VARS


def test_free_text_vars_scrubs_top_level_and_dossier_and_keeps_structure() -> None:
    out = redact_free_text_vars(
        {
            "resumo_contexto": "CPF 123.456.789-09",
            "conversation_id": "wa:amh:deadbeef",
            "numero_boleto": "34191790010104351004791020150008291070026000",
            "dossie_lucas": {"narrativa": "fone (11) 98765-4321", "fatos": {"competencia": "2026-08"}},
        }
    )
    assert "123.456.789-09" not in out["resumo_contexto"]
    assert "98765-4321" not in out["dossie_lucas"]["narrativa"]
    assert out["conversation_id"] == "wa:amh:deadbeef"
    assert out["numero_boleto"] == "34191790010104351004791020150008291070026000"
    assert out["dossie_lucas"]["fatos"]["competencia"] == "2026-08"


def test_free_text_vars_scrubs_a_list_of_free_text_strings() -> None:
    out = redact_free_text_vars({"notas_resolucao": ["ok", "CPF 123.456.789-09"]})
    assert out["notas_resolucao"][0] == "ok"
    assert "123.456.789-09" not in out["notas_resolucao"][1]


def test_free_text_vars_ignores_a_non_dossier_mapping() -> None:
    """Only `dossie_*` mappings are walked — an arbitrary structured variable is passed through
    by reference-equality of content, never rewritten."""
    dmn_refs = {"narrativa_like_key": "CPF 123.456.789-09"}
    out = redact_free_text_vars({"dmn_decision_refs": dmn_refs})
    assert out["dmn_decision_refs"] == dmn_refs


def test_free_text_vars_raises_on_an_unwalkable_payload() -> None:
    """Fail-closed: the chokepoint must be able to REFUSE the start rather than pass raw text on.
    Unlike `redact_phi_vars`/`redact_error_message`, this one is allowed to raise."""
    dossier: dict[str, object] = {"narrativa": "ok"}
    dossier["self"] = dossier
    with pytest.raises(ValueError, match="deeper than"):
        redact_free_text_vars({"dossie_rafael": dossier})


# -- CC-06 §Delta2 (REG-01 + residuo #5) — valores ESTRUTURADOS sob um nome de texto livre ---------
#
# `_redact_free_text_value` tratava `str` e `list[str]` e mais nada. Qualquer outra forma sob um
# nome de texto livre — uma TUPLA de prosa (REG-01), um mapping aninhado, uma lista de mappings,
# as mesmas formas dentro de um `dossie_*` — chegava ao engine VERBATIM, e um valor
# auto-referente sob esse nome era silenciosamente PASSADO em vez de recusado (a clausula
# "MAY RAISE ... malformed or self-referential" do docstring nao valia para essa forma). A
# correcao RECORRE por todo container encontrado sob um nome de texto livre, redige toda folha
# `str`, tolera um conjunto FIXADO de folhas escalares e RECUSA qualquer outra coisa —
# fail-closed, nunca um pass-through silencioso.
#
# Identificadores sinteticos apenas.

_CPF = "123.456.789-00"
_CPF_TEXT = f"paciente CPF {_CPF}"


def test_free_text_tuple_is_scrubbed_and_keeps_its_type() -> None:
    """REG-01: a TUPLA de prosa passava intacta enquanto a MESMA lista era redigida. O tipo do
    container e' preservado — o contrato de entrada do BPMN nao pode mudar de forma por causa do
    scrub."""
    out = redact_free_text_vars({"narrativa": (_CPF_TEXT, "sem identificador")})
    assert isinstance(out["narrativa"], tuple)
    assert _CPF not in out["narrativa"][0]
    assert REDACTED_DIGITS in out["narrativa"][0]
    assert out["narrativa"][1] == "sem identificador"


def test_free_text_nested_mapping_is_scrubbed() -> None:
    """Residuo #5: `{'narrativa': {'texto': '<CPF>'}}` — toda folha `str` abaixo de um nome de
    texto livre e' prosa por construcao, qualquer que seja o nome da chave interna."""
    out = redact_free_text_vars({"narrativa": {"texto": _CPF_TEXT, "n": 3}})
    assert _CPF not in out["narrativa"]["texto"]
    assert REDACTED_DIGITS in out["narrativa"]["texto"]
    assert out["narrativa"]["n"] == 3


def test_free_text_list_of_mappings_is_scrubbed() -> None:
    """Residuo #5, segunda forma: `{'narrativa': [{'texto': '<CPF>'}]}`."""
    out = redact_free_text_vars({"lacunas_enriquecimento": [{"texto": _CPF_TEXT}, "nota simples"]})
    assert _CPF not in out["lacunas_enriquecimento"][0]["texto"]
    assert REDACTED_DIGITS in out["lacunas_enriquecimento"][0]["texto"]
    assert out["lacunas_enriquecimento"][1] == "nota simples"


def test_dossier_nested_free_text_mapping_is_scrubbed() -> None:
    """Residuo #5, terceira forma: a mesma estrutura DENTRO de um `dossie_*`, que e' o caminho
    que os 8 `_build_dossier` produtores realmente percorrem."""
    out = redact_free_text_vars({"dossie_x": {"narrativa": {"t": _CPF_TEXT}, "n_evidencia": 3}})
    assert _CPF not in out["dossie_x"]["narrativa"]["t"]
    assert out["dossie_x"]["n_evidencia"] == 3


def test_dossier_tuple_is_walked_and_keeps_its_type() -> None:
    """REG-01, segunda reproducao: uma TUPLA dentro de um dossie escondia o mapping cuja chave
    `narrativa` deveria ter sido redigida — `_walk` so conhecia `list`."""
    out = redact_free_text_vars({"dossie_rafael": ({"narrativa": _CPF_TEXT}, {"n": 1})})
    assert isinstance(out["dossie_rafael"], tuple)
    assert _CPF not in out["dossie_rafael"][0]["narrativa"]
    assert out["dossie_rafael"][1] == {"n": 1}


def test_free_text_scalar_leaves_pass_through() -> None:
    """GUARDA DE REGRESSAO (verde antes e depois): os escalares que os grafos vivos realmente
    produzem sob um nome de texto livre passam byte-identicos. `carolina/graph.py` emite
    `state.get('motivo_informado')`, que e' `None` quando o campo nao foi preenchido; recusar um
    `None` transformaria a correcao fail-closed numa negacao de servico."""
    out = redact_free_text_vars(
        {"motivo_informado": None, "narrativa": {"n": 3, "taxa": 1.5, "ok": True, "vazio": None}}
    )
    assert out["motivo_informado"] is None
    assert out["narrativa"] == {"n": 3, "taxa": 1.5, "ok": True, "vazio": None}
    assert out["narrativa"]["ok"] is True


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(b"CPF 123.456.789-00", id="bytes"),
        pytest.param({"CPF 123.456.789-00"}, id="set"),
        pytest.param(frozenset({"CPF 123.456.789-00"}), id="frozenset"),
        pytest.param(datetime.date(2026, 1, 1), id="date"),
        pytest.param(object(), id="object"),
    ],
)
def test_free_text_unscrubbable_type_is_refused(value: object) -> None:
    """Fail-closed: `redact_free_text` e' uma rede sobre `str`. Um tipo que ela nao consegue
    inspecionar sob um nome de texto livre NAO pode passar — o chokepoint converte este
    `ValueError` em `StartVariableRedactionError` e recusa o start."""
    with pytest.raises(ValueError, match="cannot be scrubbed"):
        redact_free_text_vars({"narrativa": value})


@pytest.mark.parametrize("container", ["dict", "list", "tuple"])
def test_free_text_self_referential_value_is_refused(container: str) -> None:
    """Residuo #5: um valor auto-referente sob um nome de texto livre era silenciosamente
    PASSADO (o `_redact_free_text_value` antigo devolvia qualquer nao-`str`/nao-`list` intacto),
    contradizendo a propria clausula MAY RAISE do docstring. Agora e' recusado de imediato pelo
    guarda de identidade, sem depender do teto de profundidade.

    A assercao casa a mensagem ESPECIFICA do guarda ("contains itself"), nao um "self-referential"
    solto: a mensagem do teto de profundidade tambem contem a palavra "self-referential", entao a
    forma frouxa passava com o guarda REMOVIDO (medido: a mutacao que apaga o guarda deixava os
    141 testes verdes). O teto continua sendo a rede de seguranca; esta cerca pina o guarda."""
    cycle: object
    if container == "dict":
        dict_cycle: dict[str, object] = {}
        dict_cycle["texto"] = dict_cycle
        cycle = dict_cycle
    elif container == "list":
        list_cycle: list[object] = []
        list_cycle.append(list_cycle)
        cycle = list_cycle
    else:
        inner: list[object] = []
        tuple_cycle: tuple[object, ...] = (inner,)
        inner.append(tuple_cycle)
        cycle = tuple_cycle
    with pytest.raises(ValueError, match="contains itself"):
        redact_free_text_vars({"narrativa": cycle})


def test_free_text_shared_but_acyclic_value_is_not_refused() -> None:
    """O guarda e' de CAMINHO, nao de visitados: o mesmo mapping referenciado por dois ramos
    IRMAOS nao e' um ciclo e nao pode virar uma recusa falsa (isso seria uma negacao de servico
    sobre um dossie perfeitamente valido)."""
    shared = {"texto": _CPF_TEXT}
    out = redact_free_text_vars({"narrativa": {"a": shared, "b": shared}})
    assert _CPF not in out["narrativa"]["a"]["texto"]
    assert _CPF not in out["narrativa"]["b"]["texto"]


def test_free_text_deeper_than_the_bound_is_refused() -> None:
    """O teto de recursao reusado e' `_MAX_DOSSIER_DEPTH` (o mesmo do walk do dossie): uma
    estrutura mais funda e' malformada, e parar o scrub no meio da arvore seria exatamente o
    pass-through silencioso que esta correcao fecha. Uma estrutura rasa NAO e' recusada."""
    shallow: object = _CPF_TEXT
    for _ in range(3):
        shallow = {"n": shallow}
    assert _CPF not in str(redact_free_text_vars({"narrativa": shallow}))

    deep: object = _CPF_TEXT
    for _ in range(_MAX_DOSSIER_DEPTH + 3):
        deep = {"n": deep}
    with pytest.raises(ValueError, match="deeper than"):
        redact_free_text_vars({"narrativa": deep})


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(f"CPF {_CPF}", id="str-phi-shaped"),
        pytest.param("12345678901", id="str-11-digitos"),
        pytest.param(12345678901, id="int-11-digitos"),
        pytest.param(12345678901.0, id="float-11-digitos"),
        pytest.param((f"CPF {_CPF}",), id="tuple"),
        pytest.param(b"123.456.789-00", id="bytes"),
        pytest.param(True, id="bool"),
        pytest.param(None, id="none"),
    ],
)
def test_free_text_mapping_with_a_phi_shaped_key_is_refused(key: object) -> None:
    """As CHAVES de um mapping sob um nome de texto livre nao sao redigidas (redigir duas chaves
    para o mesmo token de classe fundiria as duas entradas e perderia um valor em silencio) — por
    isso uma chave PHI-shaped e' RECUSADA, usando o detector que ja existe no modulo
    (`looks_like_phi_text`).

    §Delta F1: o detector e' `str`-only por contrato ("Non-strings and empty strings are NOT
    flagged"), entao a chave `int` GEMEA da chave `str` que ja era recusada atravessava o scrub
    inteira; `json.dumps` a devolve como a string `"12345678901"` do outro lado, isto e', a mesma
    corrida de 11 digitos que a recusa de chave existe para barrar. E uma chave `tuple`/`bytes`
    morria como `TypeError` sem tipo em `gateway/audit.py::hash_input`, e nao pela recusa
    ratificada. Por isso QUALQUER chave nao-`str` e' inescrutavel e e' recusada aqui: a
    assimetria de tipo dentro do proprio scrub e' a mesma especie do REG-01 (`list` redigida /
    `tuple` nao)."""
    with pytest.raises(ValueError, match="key"):
        redact_free_text_vars({"narrativa": {key: "nota"}})


def test_free_text_mapping_with_an_ordinary_str_key_is_not_refused() -> None:
    """O lado VERDE da mesma regra (a recusa tem de ser estreita): uma chave estrutural comum,
    `str` e sem forma de identificador, passa — e o valor sob ela continua sendo redigido."""
    out = redact_free_text_vars({"narrativa": {"cid10_referencia": "nota"}})
    assert out["narrativa"] == {"cid10_referencia": "nota"}
    scrubbed = redact_free_text_vars({"narrativa": {"texto": _CPF_TEXT}})
    assert _CPF not in scrubbed["narrativa"]["texto"]
    assert REDACTED_DIGITS in scrubbed["narrativa"]["texto"]


# -- CERCAS (falham sob mutacao; ver §Prova do relatorio) ------------------------------------------

_SHAPE_BUILDERS: dict[str, Callable[[object], object]] = {
    "dict": lambda inner: {"k": inner},
    "list": lambda inner: [inner],
    "tuple": lambda inner: (inner,),
}

_SHAPES: list[tuple[str, ...]] = [
    combo for depth in (1, 2, 3) for combo in itertools.product(sorted(_SHAPE_BUILDERS), repeat=depth)
]


@pytest.mark.parametrize("shape", _SHAPES, ids=["-".join(s) for s in _SHAPES])
def test_no_container_shape_under_a_free_text_name_can_carry_an_identifier(
    shape: tuple[str, ...],
) -> None:
    """CERCA EXAUSTIVA de forma: para TODA combinacao de `dict`/`list`/`tuple` ate profundidade 3
    sob um nome de texto livre, ZERO ocorrencia do identificador plantado pode sobreviver. E' a
    generalizacao do defeito: REG-01 era uma celula desta matriz (`tuple`), o residuo #5 eram
    outras tres (`dict`, `list-dict`, `dict-dict`). Reverter qualquer braco de recursao deixa
    esta cerca VERMELHA."""
    payload: object = _CPF_TEXT
    for name in reversed(shape):
        payload = _SHAPE_BUILDERS[name](payload)

    out = redact_free_text_vars({"narrativa": payload})

    assert _CPF not in repr(out), f"forma {'-'.join(shape)} vazou {_CPF!r}: {out!r}"
    assert REDACTED_DIGITS in repr(out), f"forma {'-'.join(shape)} nao foi redigida: {out!r}"


def test_free_text_passthrough_types_are_pinned() -> None:
    """CERCA DE MEMBRESIA (o padrao que o proprio relatorio F1-A1 apontou como o correto para uma
    allowlist): os tipos de folha NAO-`str` tolerados sob um nome de texto livre sao fixados aqui.
    Alargar `_FREE_TEXT_PASSTHROUGH_TYPES` — acrescentar `bytes`, por exemplo — deixa esta cerca
    VERMELHA, entao a decisao vira um ato deliberado em vez de um deslize. Lido por `getattr`
    porque a ausencia da constante e' exatamente o que esta cerca precisa reportar como uma
    ASSERCAO legivel, e nao como um `ImportError` do modulo inteiro."""
    import maezo.tools.workers.phi_vars as phi_vars_module

    pinned = getattr(phi_vars_module, "_FREE_TEXT_PASSTHROUGH_TYPES", None)
    assert pinned is not None, "_FREE_TEXT_PASSTHROUGH_TYPES ausente — a allowlist nao esta fixada"
    assert pinned == (bool, int, float, type(None)), (
        "os tipos de folha tolerados sob um nome de texto livre mudaram — justifique no docstring "
        f"da constante antes de alterar esta cerca: {pinned!r}"
    )
