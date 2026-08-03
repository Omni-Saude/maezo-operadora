"""Unit tests for maezo.tools.workers.credenciamento (SP-OP-CRED-001).

TDD London School: tests verify both adverse directions (decred + denial).
"""

import asyncio

import pytest

from maezo.a2a import DelegationResult, RejectionReason
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.credenciamento import (
    ERR_CRED_DENIAL_NOT_HUMAN,
    ERR_CRED_REGISTER_INVALID,
    ERR_DECRED_NOT_HUMAN,
    CredError,
    assess_admissibility,
    make_prepare_dossier_handler,
    notify_doc_pendente,
    notify_prestador,
    notify_sla_risk,
    register_cred_denial,
    register_credenciamento,
    register_credenciamento_workers,
    register_decred,
    register_descredenciamento,
    validate_cred,
)
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import (
    ExternalTask,
    FakeAuditSink,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerBpmnError,
    WorkerHarness,
)


def _cred_admissibility_only_fake(*, roteamento: str, motivo: str = "") -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run) — terminal admissibility
    state (not SEGUE_ANALISE), so cred_route is never consulted."""
    fake = FakeDmnTransport()
    fake.register("cred_admissibility", [{"roteamento": roteamento, "motivo": motivo}])
    return fake


def _cred_admissibility_and_route_fake(*, route_roteamento: str, route_motivo: str = "") -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run) — admissibility returns
    SEGUE_ANALISE, chained into cred_route for the final routing."""
    fake = FakeDmnTransport()
    fake.register("cred_admissibility", [{"roteamento": "SEGUE_ANALISE", "motivo": ""}])
    fake.register("cred_route", [{"roteamento": route_roteamento, "motivo": route_motivo}])
    return fake


# ---------------------------------------------------------------
# validate_cred
# ---------------------------------------------------------------


def test_validate_cred_returns_facts() -> None:
    result = validate_cred(
        {
            "prestador_id": "P-001",
            "tipo_prestador": "hospital",
            "documentos_refs": {"licenca": "ref-lic-001"},
        }
    )
    assert result["licenca_valida"] is True
    assert result["documentacao_completa"] is True


def test_validate_cred_empty_docs() -> None:
    result = validate_cred(
        {
            "prestador_id": "P-002",
            "documentos_refs": {},
        }
    )
    assert result["documentacao_completa"] is False


# ---------------------------------------------------------------
# validate_cred — FACT PRESERVATION (item-9 bucket-3 Class-C, T3.1 phase-2 finding 2):
# an already-resolved boolean fact is respected, never clobbered by the placeholder.
# ---------------------------------------------------------------


def test_validate_cred_respects_resolved_documentacao_completa_true_with_string_refs() -> None:
    """The exact overwrite-bug shape: `documentos_refs` as a STRING ref (sibling-family
    convention) with the fact already resolved True upstream — the pre-fix worker recomputed
    `isinstance(str, dict)` = False and OVERWROTE the seeded True. The fact must survive."""
    result = validate_cred(
        {
            "prestador_id": "P-010",
            "documentos_refs": "ref-docs-0001",  # Camunda String, NOT a dict
            "documentacao_completa": True,  # resolved fact seeded at start
        }
    )
    assert result["documentacao_completa"] is True


def test_validate_cred_respects_resolved_documentacao_completa_false() -> None:
    """A resolved False survives even when the placeholder computation would say True."""
    result = validate_cred(
        {
            "prestador_id": "P-011",
            "documentos_refs": {"licenca": "ref-lic-001"},  # placeholder would compute True
            "documentacao_completa": False,
        }
    )
    assert result["documentacao_completa"] is False


def test_validate_cred_respects_resolved_licenca_valida_false() -> None:
    """The fail-OPEN half of the overwrite bug is gone: a resolved `licenca_valida=False` is
    NEVER overwritten by the hardcoded placeholder True."""
    result = validate_cred(
        {
            "prestador_id": "P-012",
            "documentos_refs": {"licenca": "ref-lic-001"},
            "licenca_valida": False,
        }
    )
    assert result["licenca_valida"] is False


@pytest.mark.parametrize("junk", ["true", "false", 1, 0, [], {}, None])
def test_validate_cred_non_boolean_seeded_facts_fall_back_to_computation(junk: object) -> None:
    """Engine variables arrive untyped: a NON-boolean seeded 'fact' is not a resolved fact —
    the placeholder computation applies (fail-closed: junk never passes through as the fact)."""
    result = validate_cred(
        {
            "prestador_id": "P-013",
            "documentos_refs": {"licenca": "ref-lic-001"},
            "licenca_valida": junk,
            "documentacao_completa": junk,
        }
    )
    assert result["licenca_valida"] is True  # placeholder fallback
    assert result["documentacao_completa"] is True  # computed from the non-empty dict


def test_validate_cred_absent_facts_keep_placeholder_behavior() -> None:
    """No resolved facts seeded: the pre-existing placeholder behavior is preserved exactly."""
    result = validate_cred(
        {
            "prestador_id": "P-014",
            "documentos_refs": "ref-docs-0002",  # string ref, no seeded facts
        }
    )
    assert result["licenca_valida"] is True
    assert result["documentacao_completa"] is False  # string ref: placeholder cannot resolve it


# ---------------------------------------------------------------
# assess_admissibility
# ---------------------------------------------------------------


def test_assess_admissibility_pendente_docs() -> None:
    fake = _cred_admissibility_only_fake(roteamento="PENDENTE_DOCUMENTACAO")
    result = assess_admissibility(
        {
            "direcao": "credenciamento",
            "documentacao_completa": False,
            "licenca_valida": True,
            "dentro_criterios_rede": True,
            "indicio_irregularidade_sinalizado": False,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "PENDENTE_DOCUMENTACAO"
    assert len(fake.calls) == 1  # terminal state — cred_route not consulted


def test_assess_admissibility_analise_humana_licenca() -> None:
    fake = _cred_admissibility_only_fake(roteamento="ANALISE_HUMANA")
    result = assess_admissibility(
        {
            "direcao": "credenciamento",
            "documentacao_completa": True,
            "licenca_valida": False,
            "dentro_criterios_rede": True,
            "indicio_irregularidade_sinalizado": False,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "ANALISE_HUMANA"


def test_assess_admissibility_descredenciamento() -> None:
    """Descredenciamento is NEVER clerical — admissibility returns SEGUE_ANALISE, chained into
    cred_route for the final ANALISE_DESCREDENCIAMENTO label (live-verified)."""
    fake = _cred_admissibility_and_route_fake(route_roteamento="ANALISE_DESCREDENCIAMENTO")
    result = assess_admissibility(
        {
            "direcao": "descredenciamento",
            "documentacao_completa": True,
            "licenca_valida": True,
            "dentro_criterios_rede": True,
            "indicio_irregularidade_sinalizado": False,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "ANALISE_DESCREDENCIAMENTO"
    assert len(fake.calls) == 2  # admissibility (SEGUE_ANALISE) + route chain


def test_assess_admissibility_irregularidade_route() -> None:
    """Indicio de irregularidade -> SEGUE_ANALISE (never clerical) -> cred_route ->
    ANALISE_DESCREDENCIAMENTO (humano decide encaminhar FRAUDE; live-verified)."""
    fake = _cred_admissibility_and_route_fake(route_roteamento="ANALISE_DESCREDENCIAMENTO")
    result = assess_admissibility(
        {
            "direcao": "credenciamento",
            "documentacao_completa": True,
            "licenca_valida": True,
            "dentro_criterios_rede": True,
            "indicio_irregularidade_sinalizado": True,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "ANALISE_DESCREDENCIAMENTO"


def test_assess_admissibility_catch_all() -> None:
    fake = _cred_admissibility_only_fake(roteamento="ANALISE_HUMANA")
    result = assess_admissibility(
        {
            "direcao": "invalida",
            "documentacao_completa": True,
            "licenca_valida": True,
            "dentro_criterios_rede": True,
            "indicio_irregularidade_sinalizado": False,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "ANALISE_HUMANA"


def test_assess_admissibility_clerical_credenciar() -> None:
    """NEW case (T1.5): documentacao completa + licenca valida + dentro criterios + sem
    indicio -> CLERICAL_CREDENCIAR (direcao favoravel, neutro — NAO adverso). Live-verified;
    was unreachable before cutover (old Python routed this combination to
    ANALISE_CREDENCIAMENTO via the direcao elif, never the clerical bypass the DMN allows)."""
    fake = _cred_admissibility_only_fake(roteamento="CLERICAL_CREDENCIAR")
    result = assess_admissibility(
        {
            "direcao": "credenciamento",
            "documentacao_completa": True,
            "licenca_valida": True,
            "dentro_criterios_rede": True,
            "indicio_irregularidade_sinalizado": False,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "CLERICAL_CREDENCIAR"


def test_assess_admissibility_dmn_unwired_raises_dmn_evaluation_error() -> None:
    with pytest.raises(DmnEvaluationError):
        assess_admissibility({"direcao": "credenciamento"}, dmn=None)


# ---------------------------------------------------------------
# notify_prestador
# ---------------------------------------------------------------


def test_notify_prestador() -> None:
    result = notify_prestador({"prestador_id": "P-001"})
    assert result["notificacao_previa_feita"] is True


# ---------------------------------------------------------------
# notify_doc_pendente — NEUTRAL (never a denial)
# ---------------------------------------------------------------


def test_notify_doc_pendente_happy_path() -> None:
    result = notify_doc_pendente(
        {
            "prestador_id": "P-003",
            "tenant_id": "amh",
            "direcao": "credenciamento",
        }
    )
    assert result["documentacao_pendente_notificada"] is True
    assert result["prestador_id"] == "P-003"


def test_notify_doc_pendente_missing_fields_does_not_raise() -> None:
    """Notify-only worker (T2.5-p2b) — fails SAFE (never raises) on missing identity, mirroring
    the proven `notify_prestador`/`cancel.notify_sla_risk` idiom (`.get(..., default)`, no guard)."""
    result = notify_doc_pendente({})
    assert result["documentacao_pendente_notificada"] is True
    assert result["prestador_id"] == ""


# ---------------------------------------------------------------
# notify_sla_risk — NEUTRAL (informational, non-interruptive)
# ---------------------------------------------------------------


def test_notify_sla_risk_happy_path() -> None:
    result = notify_sla_risk(
        {
            "prestador_id": "P-004",
            "tenant_id": "amh",
            "direcao": "descredenciamento",
        }
    )
    assert result["sla_risk_notified"] is True
    assert result["grupo_alertado"] == "coordenacao-rede"
    assert result["prestador_id"] == "P-004"


def test_notify_sla_risk_missing_fields_does_not_raise() -> None:
    """Mirrors `cancel.notify_sla_risk`/`inadimplencia.notify_sla_risk`/
    `auth.NotifySlaRiskWorker` — informational only, never raises, never alters a decision."""
    result = notify_sla_risk({})
    assert result["sla_risk_notified"] is True
    assert result["grupo_alertado"] == "coordenacao-rede"


# ---------------------------------------------------------------
# register_decred — GUARD (direcao A: descredenciamento)
# ---------------------------------------------------------------


def test_cred_guard_decred_happy_path() -> None:
    result = register_descredenciamento(
        {
            "decisao_cred": "DESCREDENCIAR",
            "responsavel_id": "gestor-001",
            "fundamentacao": "Violacao contratual",
            "referencia_regulatoria": "RN 567",
            "comprovacao_notificacao_previa": "ref-notif-001",
            "tem_beneficiarios_vinculados": False,
            "prestador_id": "P-001",
        }
    )
    assert result["descredenciamento_registrado"] is True


def test_cred_guard_rejects_wrong_decisao() -> None:
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_descredenciamento(
            {
                "decisao_cred": "MANTER",
                "responsavel_id": "x",
                "fundamentacao": "x",
                "referencia_regulatoria": "x",
                "comprovacao_notificacao_previa": "x",
            }
        )
    assert excinfo.value.error_code == ERR_DECRED_NOT_HUMAN


def test_cred_guard_rejects_missing_plano_substituicao() -> None:
    """Se ha beneficiarios vinculados, plano_substituicao e obrigatorio (RN 567)."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_descredenciamento(
            {
                "decisao_cred": "DESCREDENCIAR",
                "responsavel_id": "gestor-001",
                "fundamentacao": "x",
                "referencia_regulatoria": "RN 567",
                "comprovacao_notificacao_previa": "ref-001",
                "tem_beneficiarios_vinculados": True,
                "plano_substituicao": "",
            }
        )
    assert excinfo.value.error_code == ERR_DECRED_NOT_HUMAN
    assert "plano_substituicao" in str(excinfo.value)


def test_cred_guard_register_decred_alias() -> None:
    result = register_decred(
        {
            "decisao_cred": "DESCREDENCIAR",
            "responsavel_id": "g-001",
            "fundamentacao": "x",
            "referencia_regulatoria": "x",
            "comprovacao_notificacao_previa": "x",
        }
    )
    assert result["descredenciamento_registrado"] is True


# ---------------------------------------------------------------
# register_cred_denial — GUARD (direcao B: negativa de credenciamento)
# ---------------------------------------------------------------


def test_cred_denial_happy_path() -> None:
    result = register_cred_denial(
        {
            "decisao_cred": "NEGAR_CREDENCIAMENTO",
            "responsavel_id": "gestor-002",
            "fundamentacao": "Fora dos criterios RN 566",
            "referencia_regulatoria": "RN 566",
        }
    )
    assert result["credenciamento_negado"] is True


def test_cred_denial_rejects_missing_decisao() -> None:
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_cred_denial(
            {
                "decisao_cred": "APROVAR_CREDENCIAMENTO",
                "responsavel_id": "x",
                "fundamentacao": "x",
                "referencia_regulatoria": "x",
            }
        )
    assert excinfo.value.error_code == ERR_CRED_DENIAL_NOT_HUMAN


def test_cred_denial_rejects_missing_regulatoria() -> None:
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_cred_denial(
            {
                "decisao_cred": "NEGAR_CREDENCIAMENTO",
                "responsavel_id": "g-002",
                "fundamentacao": "x",
                "referencia_regulatoria": "",
            }
        )
    assert excinfo.value.error_code == ERR_CRED_DENIAL_NOT_HUMAN


# ---------------------------------------------------------------
# register_descredenciamento / register_cred_denial — whitespace-bypass vectors
# (t3.1-guard-input-hardening, same class as the c1377fa fix for
# pagto.register_payment_refusal: bare `if not field:` let WHITESPACE-ONLY decision +
# accountability fields through. Every vector below MUST refuse with the function's own
# error code — whitespace-only is the SAME as absent (ADR-0007/RN 566/RN 567).
# ---------------------------------------------------------------

_WHITESPACE_VARIANTS = [" ", "   ", "\t", "\n", "\t\n ", "\r\n"]
_NON_STRING_VARIANTS: list[object] = [123, True, 0.5, ["x"], {"k": "v"}]


def _decred_baseline(**overrides: object) -> dict[str, object]:
    """Happy-path baseline for register_descredenciamento (tem_beneficiarios_vinculados=True so
    plano_substituicao is exercised as an obligatory field) -- any guard failure observed in a
    test is attributable ONLY to the field under test."""
    base: dict[str, object] = {
        "decisao_cred": "DESCREDENCIAR",
        "responsavel_id": "gestor-001",
        "fundamentacao": "Violacao contratual",
        "referencia_regulatoria": "RN 567",
        "comprovacao_notificacao_previa": "ref-notif-001",
        "tem_beneficiarios_vinculados": True,
        "plano_substituicao": "Substituto equivalente hospital X",
        "prestador_id": "P-001",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "field",
    [
        "responsavel_id",
        "fundamentacao",
        "referencia_regulatoria",
        "comprovacao_notificacao_previa",
        "plano_substituicao",
    ],
)
@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_decred_whitespace_only_accountability_field_refuses(field: str, whitespace: str) -> None:
    """Bare-truthiness bypass (pre-fix): whitespace-only accountability field must refuse and
    be named in the guard's error message. plano_substituicao included: contract
    SP-OP-CRED-001.md:80,149 makes it an obligatory human-typed string when
    tem_beneficiarios_vinculados (RN 567)."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_descredenciamento(_decred_baseline(**{field: whitespace}))
    assert excinfo.value.error_code == ERR_DECRED_NOT_HUMAN
    assert field in str(excinfo.value)


@pytest.mark.parametrize(
    "field",
    [
        "responsavel_id",
        "fundamentacao",
        "referencia_regulatoria",
        "comprovacao_notificacao_previa",
        "plano_substituicao",
    ],
)
@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_decred_non_string_accountability_field_refuses(field: str, non_string: object) -> None:
    """A NON-string accountability field normalizes to '' and refuses -- the pre-fix bare
    truthiness check would have silently PASSED a truthy non-string (e.g. 123)."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_descredenciamento(_decred_baseline(**{field: non_string}))
    assert excinfo.value.error_code == ERR_DECRED_NOT_HUMAN
    assert field in str(excinfo.value)


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_decred_both_responsavel_and_fundamentacao_whitespace_refuses(whitespace: str) -> None:
    """Both responsavel_id AND fundamentacao whitespace-only -- both missing fields named."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_descredenciamento(_decred_baseline(responsavel_id=whitespace, fundamentacao=whitespace))
    assert excinfo.value.error_code == ERR_DECRED_NOT_HUMAN
    assert "responsavel_id" in str(excinfo.value)
    assert "fundamentacao" in str(excinfo.value)


@pytest.mark.parametrize("whitespace", [" ", "\t", "\n", "  \t\n"])
def test_decred_whitespace_only_decisao_refuses(whitespace: str) -> None:
    """Whitespace-only decisao_cred normalizes to '' -> != DESCREDENCIAR -> refuses."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_descredenciamento(_decred_baseline(decisao_cred=whitespace))
    assert excinfo.value.error_code == ERR_DECRED_NOT_HUMAN
    assert "decisao_cred" in str(excinfo.value)


def test_decred_padded_valid_literal_normalizes_and_registers() -> None:
    """Whitespace-PADDED but otherwise exact literal/fields normalize via `_norm_str` and still
    register (pins the normalization -- NOT a bypass, the documented `.strip()` consequence)."""
    result = register_descredenciamento(
        _decred_baseline(
            decisao_cred=" DESCREDENCIAR ",
            responsavel_id=" gestor-001 ",
            fundamentacao=" Violacao contratual ",
            referencia_regulatoria=" RN 567 ",
            comprovacao_notificacao_previa=" ref-notif-001 ",
            plano_substituicao=" Substituto equivalente hospital X ",
        )
    )
    assert result["descredenciamento_registrado"] is True


@pytest.mark.parametrize(
    "decision", ["descredenciar", "Descredenciar", "DESCREDENCIAR_X", "XDESCREDENCIAR", "MANTER "]
)
def test_decred_non_exact_decisao_literal_still_refuses(decision: str) -> None:
    """Exact-match discipline survives normalization: case variants/substrings never pass."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_descredenciamento(_decred_baseline(decisao_cred=decision))
    assert excinfo.value.error_code == ERR_DECRED_NOT_HUMAN


def _cred_denial_baseline(**overrides: object) -> dict[str, object]:
    """Happy-path baseline for register_cred_denial."""
    base: dict[str, object] = {
        "decisao_cred": "NEGAR_CREDENCIAMENTO",
        "responsavel_id": "gestor-002",
        "fundamentacao": "Fora dos criterios RN 566",
        "referencia_regulatoria": "RN 566",
        "prestador_id": "P-002",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("field", ["responsavel_id", "fundamentacao", "referencia_regulatoria"])
@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_cred_denial_whitespace_only_accountability_field_refuses(field: str, whitespace: str) -> None:
    """Bare-truthiness bypass (pre-fix): whitespace-only accountability field must refuse."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_cred_denial(_cred_denial_baseline(**{field: whitespace}))
    assert excinfo.value.error_code == ERR_CRED_DENIAL_NOT_HUMAN
    assert field in str(excinfo.value)


@pytest.mark.parametrize("field", ["responsavel_id", "fundamentacao", "referencia_regulatoria"])
@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_cred_denial_non_string_accountability_field_refuses(field: str, non_string: object) -> None:
    """A NON-string accountability field normalizes to '' and refuses."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_cred_denial(_cred_denial_baseline(**{field: non_string}))
    assert excinfo.value.error_code == ERR_CRED_DENIAL_NOT_HUMAN
    assert field in str(excinfo.value)


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_cred_denial_both_responsavel_and_fundamentacao_whitespace_refuses(
    whitespace: str,
) -> None:
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_cred_denial(_cred_denial_baseline(responsavel_id=whitespace, fundamentacao=whitespace))
    assert excinfo.value.error_code == ERR_CRED_DENIAL_NOT_HUMAN
    assert "responsavel_id" in str(excinfo.value)
    assert "fundamentacao" in str(excinfo.value)


@pytest.mark.parametrize("whitespace", [" ", "\t", "\n", "  \t\n"])
def test_cred_denial_whitespace_only_decisao_refuses(whitespace: str) -> None:
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_cred_denial(_cred_denial_baseline(decisao_cred=whitespace))
    assert excinfo.value.error_code == ERR_CRED_DENIAL_NOT_HUMAN
    assert "decisao_cred" in str(excinfo.value)


def test_cred_denial_padded_valid_literal_normalizes_and_registers() -> None:
    result = register_cred_denial(
        _cred_denial_baseline(
            decisao_cred=" NEGAR_CREDENCIAMENTO ",
            responsavel_id=" gestor-002 ",
            fundamentacao=" Fora dos criterios RN 566 ",
            referencia_regulatoria=" RN 566 ",
        )
    )
    assert result["credenciamento_negado"] is True


@pytest.mark.parametrize(
    "decision",
    [
        "negar_credenciamento",
        "Negar_Credenciamento",
        "NEGAR_CREDENCIAMENTO_X",
        "XNEGAR_CREDENCIAMENTO",
        "DESCREDENCIAR ",
    ],
)
def test_cred_denial_non_exact_decisao_literal_still_refuses(decision: str) -> None:
    """Case variants/substrings never pass; a padded DESCREDENCIAR on the denial guard is the
    WRONG literal for this function and must refuse."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_cred_denial(_cred_denial_baseline(decisao_cred=decision))
    assert excinfo.value.error_code == ERR_CRED_DENIAL_NOT_HUMAN


# ---------------------------------------------------------------
# register_credenciamento — NEUTRAL (favorable direction; EXCECAO CLERICAL, no human-gate)
# ---------------------------------------------------------------


def test_register_credenciamento_happy_path_clerical() -> None:
    """Reached via Flow_GW_ClericalCredenciar (DMN CLERICAL_CREDENCIAR) — no decisao_cred set at
    all; this is the intentional, BPMN-documented no-human-decision path (EXCECAO CLERICAL)."""
    result = register_credenciamento(
        {
            "tenant_id": "amh",
            "prestador_id": "P-005",
            "tipo_prestador": "clinica",
            "data_efeito_iso": "2026-07-15",
        }
    )
    assert result["credenciamento_registrado"] is True
    assert result["network_changed"] is True
    assert result["data_efeito_iso"] == "2026-07-15"


def test_register_credenciamento_happy_path_human_aprovar() -> None:
    """Reached via Flow_GWCred_Aprovar (decisao_cred == APROVAR_CREDENCIAMENTO, human) — also
    succeeds; this worker never requires a human decision, but does not reject one either."""
    result = register_credenciamento(
        {
            "tenant_id": "amh",
            "prestador_id": "P-006",
            "decisao_cred": "APROVAR_CREDENCIAMENTO",
            "responsavel_id": "gestao-rede-001",
        }
    )
    assert result["credenciamento_registrado"] is True


def test_register_credenciamento_rejects_missing_tenant_id() -> None:
    with pytest.raises(CredError) as excinfo:
        register_credenciamento({"prestador_id": "P-007"})
    assert excinfo.value.code == ERR_CRED_REGISTER_INVALID
    assert "tenant_id" in str(excinfo.value)


def test_register_credenciamento_rejects_missing_prestador_id() -> None:
    with pytest.raises(CredError) as excinfo:
        register_credenciamento({"tenant_id": "amh"})
    assert excinfo.value.code == ERR_CRED_REGISTER_INVALID
    assert "prestador_id" in str(excinfo.value)


def test_register_credenciamento_rejects_empty_variables() -> None:
    with pytest.raises(CredError) as excinfo:
        register_credenciamento({})
    assert excinfo.value.code == ERR_CRED_REGISTER_INVALID
    assert "tenant_id" in str(excinfo.value)
    assert "prestador_id" in str(excinfo.value)


# ---------------------------------------------------------------------------
# register_credenciamento_workers — registry/drift coverage (T2.5-p2b, mirrors
# test_cancel.py's test_register_cancel_workers_matches_bpmn_topics_exactly)
# ---------------------------------------------------------------------------

# All 9 operadora.cred.* topics declared in the BPMN (camunda:topic="operadora.cred.*", grepped from
# spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn). operadora.cred.prepare_dossier is now
# BUILT as a local stub (DL-0033, t5-workers-f2) — Carolina A2A real delegation still deferred, but
# the BPMN topic is no longer orphaned; all 9 are registered.
_BPMN_CRED_TOPICS = frozenset(
    {
        "operadora.cred.verify_credentials",
        "operadora.cred.check_network_criteria",
        "operadora.cred.check_prior_notice",
        "operadora.cred.notify_doc_pendente",
        "operadora.cred.register_descredenciamento",
        "operadora.cred.register_cred_denial",
        "operadora.cred.register_credenciamento",
        "operadora.cred.notify_sla_risk",
        "operadora.cred.prepare_dossier",
    }
)


def test_register_credenciamento_workers_registers_new_topics() -> None:
    """T2.5-p2b: register_credenciamento_workers now registers notify_doc_pendente/
    register_credenciamento/notify_sla_risk (previously a documented gap)."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_credenciamento_workers(harness, FakeKafkaPublisher(), dmn=FakeDmnTransport())
    assert "operadora.cred.notify_doc_pendente" in harness.registered_topics
    assert "operadora.cred.register_credenciamento" in harness.registered_topics
    assert "operadora.cred.notify_sla_risk" in harness.registered_topics


def test_register_credenciamento_workers_matches_bpmn_topics() -> None:
    """Registry coverage: the registered `operadora.cred.*` topic set equals EXACTLY the 9
    BPMN-declared topics — no orphan, no extra. prepare_dossier is now built (DL-0033 stub)."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_credenciamento_workers(harness, FakeKafkaPublisher(), dmn=FakeDmnTransport())
    cred_topics = {t for t in harness.registered_topics if t.startswith("operadora.cred.")}
    assert cred_topics == _BPMN_CRED_TOPICS


def test_register_credenciamento_workers_registers_prepare_dossier_raw_handler() -> None:
    """DL-0033 real wiring: prepare_dossier is now a RAW async handler (Carolina A2A delegation)
    — registered via `harness.register()` (NOT the WorkerRegistry), so it populates `_handlers`
    only. The topic is still always served, dispatcher present or not."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_credenciamento_workers(harness, FakeKafkaPublisher(), dmn=FakeDmnTransport())
    assert "operadora.cred.prepare_dossier" in harness.registered_topics
    assert harness.registry.get("operadora.cred.prepare_dossier") is None  # raw handler


# ---------------------------------------------------------------
# prepare_dossier — REAL Carolina A2A delegation (raw async handler; DL-0033 closed, DL-0037)
# ---------------------------------------------------------------


class _FakeDossierDispatcher:
    """Records the envelope; returns a programmed `DelegationResult` (or raises)."""

    def __init__(self, result: DelegationResult | None = None, exc: Exception | None = None) -> None:
        self.envelopes: list = []
        self._result = result
        self._exc = exc

    async def delegate(self, envelope) -> DelegationResult:  # noqa: ANN001 — duck-typed fake
        self.envelopes.append(envelope)
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result


def _dossier_task(variables: dict) -> ExternalTask:
    return ExternalTask(
        task_id="et-1",
        topic="operadora.cred.prepare_dossier",
        process_instance_id="pi-1",
        business_key="CRED-amh-P-001",
        worker_id="w-1",
        variables=variables,
    )


_CRED_DOSSIER_VARS = {
    "tenant_id": "amh",
    "prestador_id": "P-001",
    "direcao": "descredenciamento",
    "tipo_prestador": "clinica",
    "licenca_valida": True,
}


async def test_prepare_dossier_delegates_to_carolina_and_returns_real_outputs() -> None:
    """Dispatcher present -> await delegate -> dossier-real outputs: `dossier_prepared=True`,
    the `output_ref` reference, and Carolina's agent-produced compact summary AS-IS
    (`dossier_summary` — the SME/PO-pending UT-form seam). The envelope's task_id is the
    process business key (Guard 4 keyed to the case)."""
    dispatcher = _FakeDossierDispatcher(
        result=DelegationResult.ok(
            "CRED-amh-P-001",
            "process://CRED-amh-P-001",
            meta={"route": "human_review", "grupo_destino": "juridico-rede"},
        )
    )
    handler = make_prepare_dossier_handler(dispatcher)  # type: ignore[arg-type]

    result = await handler(_dossier_task(_CRED_DOSSIER_VARS))

    assert result is not None
    assert result["dossier_prepared"] is True
    assert result["dossier_ref"] == "process://CRED-amh-P-001"
    assert result["dossier_summary"] == {"route": "human_review", "grupo_destino": "juridico-rede"}
    (envelope,) = dispatcher.envelopes
    assert envelope.task_id == "CRED-amh-P-001"
    assert envelope.task_type == "credentialing.analyze"
    assert envelope.target == "carolina"
    assert envelope.payload_meta["direcao"] == "descredenciamento"


async def test_prepare_dossier_without_dispatcher_fail_neutrals_with_disclosed_gap() -> None:
    """DL-0037 degradation posture: dispatcher absent (degraded runtime) -> the task still
    COMPLETES (the human UT must open) with `dossier_prepared=False` + the bounded gap token —
    never a raise, never a fabricated dossier."""
    handler = make_prepare_dossier_handler(None)
    result = await handler(_dossier_task(_CRED_DOSSIER_VARS))
    assert result is not None
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "dispatcher_unavailable"


async def test_prepare_dossier_missing_identifiers_gap_and_never_delegates() -> None:
    """No tenant/prestador (incl. whitespace-only, `non_blank` discipline) -> no degenerate
    CRED task_id is ever delegated; gap marker, UT still opens."""
    dispatcher = _FakeDossierDispatcher(result=DelegationResult.ok("x", "process://x"))
    handler = make_prepare_dossier_handler(dispatcher)  # type: ignore[arg-type]
    result = await handler(_dossier_task({"tenant_id": "amh", "prestador_id": "   "}))
    assert result is not None
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "missing_business_identifiers"
    assert dispatcher.envelopes == []


async def test_prepare_dossier_delegation_failure_fail_neutrals_never_raises() -> None:
    """ANY delegation exception -> gap marker + loud log; the raw error text stays OUT of the
    engine variables (bounded class token only)."""
    handler = make_prepare_dossier_handler(
        _FakeDossierDispatcher(exc=RuntimeError("pg down: dsn=secret"))  # type: ignore[arg-type]
    )
    result = await handler(_dossier_task(_CRED_DOSSIER_VARS))
    assert result is not None
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "delegation_failed"
    assert "secret" not in str(result.values())


async def test_prepare_dossier_structured_rejection_gap_carries_bounded_reason() -> None:
    handler = make_prepare_dossier_handler(
        _FakeDossierDispatcher(  # type: ignore[arg-type]
            result=DelegationResult.rejected(
                "CRED-amh-P-001", RejectionReason.TASK_TYPE_NOT_ACCEPTED, detail="not accepted"
            )
        )
    )
    result = await handler(_dossier_task(_CRED_DOSSIER_VARS))
    assert result is not None
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "delegation_rejected:task_type_not_accepted"


# ---------------------------------------------------------------------------
# Boundary REACHABILITY (t5-workers-f2) — the two adverse guards raise WorkerBpmnError so their
# modeled BPMN boundary catches (BE_DecredNaoHumano / BE_CredDenialNaoHumano) can fire, instead of
# a CredError->ValueError reclassification that could ONLY ever demote to an uncaught engine
# incident. Mutation-minded: allowlisted -> boundary (handle_bpmn_error); NOT allowlisted -> the
# fail-closed incident (handle_failure, retries=0). Drives the REAL harness dispatch path
# (harness._handle) end-to-end, no live engine.
# ---------------------------------------------------------------------------


def _drive_guard_failure(topic: str, fn, variables: dict, *, allowlist: frozenset[str]):
    """Run a guard-failing worker through the real harness; return (bpmn_errors, failures)."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(
        transport,
        worker_id="cred-boundary-test",
        tenant="amh",
        audit_sink=FakeAuditSink(),
        bpmn_error_allowlist=allowlist,
    )
    harness.register_worker(FunctionWorker(topic, fn))
    task = ExternalTask(
        task_id="task-1",
        topic=topic,
        process_instance_id="proc-1",
        business_key="CRED-amh-P1",
        worker_id="cred-boundary-test",
        variables=variables,
    )
    asyncio.run(harness._handle(task))
    return transport.bpmn_errors, transport.failures


_DECRED_GUARD_FAIL = {
    "decisao_cred": "MANTER",  # != DESCREDENCIAR -> guard refuses
    "responsavel_id": "x",
    "fundamentacao": "x",
    "referencia_regulatoria": "x",
    "comprovacao_notificacao_previa": "x",
}
_DENIAL_GUARD_FAIL = {
    "decisao_cred": "APROVAR_CREDENCIAMENTO",  # != NEGAR_CREDENCIAMENTO -> guard refuses
    "responsavel_id": "x",
    "fundamentacao": "x",
    "referencia_regulatoria": "x",
}


def test_register_descredenciamento_raises_workerbpmnerror_not_credorror() -> None:
    """Root-cause proof: the decred guard now raises WorkerBpmnError (a MODELED bpmn error), NOT a
    CredError (which FunctionWorker reclassifies to a bare ValueError -> incident, boundary unreachable)."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_descredenciamento(_DECRED_GUARD_FAIL)
    assert excinfo.value.error_code == ERR_DECRED_NOT_HUMAN
    assert not isinstance(excinfo.value, CredError)


def test_register_cred_denial_raises_workerbpmnerror_not_crederror() -> None:
    """Root-cause proof for the denial direction (mirror of the decred proof above)."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        register_cred_denial(_DENIAL_GUARD_FAIL)
    assert excinfo.value.error_code == ERR_CRED_DENIAL_NOT_HUMAN
    assert not isinstance(excinfo.value, CredError)


def test_decred_guard_reaches_boundary_when_allowlisted() -> None:
    """ALLOWLISTED: the adverse decred guard routes to handle_bpmn_error (BE_DecredNaoHumano fires),
    NEVER to a failure/incident — the boundary is now REACHABLE (was unreachable pre-fix)."""
    bpmn_errors, failures = _drive_guard_failure(
        "operadora.cred.register_descredenciamento",
        register_descredenciamento,
        _DECRED_GUARD_FAIL,
        allowlist=frozenset({ERR_DECRED_NOT_HUMAN}),
    )
    assert bpmn_errors == [("task-1", ERR_DECRED_NOT_HUMAN, bpmn_errors[0][2])]
    assert failures == []


def test_cred_denial_guard_reaches_boundary_when_allowlisted() -> None:
    """ALLOWLISTED: the adverse denial guard routes to handle_bpmn_error (BE_CredDenialNaoHumano)."""
    bpmn_errors, failures = _drive_guard_failure(
        "operadora.cred.register_cred_denial",
        register_cred_denial,
        _DENIAL_GUARD_FAIL,
        allowlist=frozenset({ERR_CRED_DENIAL_NOT_HUMAN}),
    )
    assert bpmn_errors == [("task-1", ERR_CRED_DENIAL_NOT_HUMAN, bpmn_errors[0][2])]
    assert failures == []


def test_decred_guard_fails_closed_to_incident_when_not_allowlisted() -> None:
    """MUTATION: with the code NOT in the allowlist (today's T-E-deferred production posture), the
    guard fails CLOSED to a retries=0 incident (handle_failure) — never a silent unmodeled bpmnError.
    Pins that the boundary is gated on the allowlist, and that pre-T-E the effect is still incident."""
    bpmn_errors, failures = _drive_guard_failure(
        "operadora.cred.register_descredenciamento",
        register_descredenciamento,
        _DECRED_GUARD_FAIL,
        allowlist=frozenset(),
    )
    assert bpmn_errors == []
    assert len(failures) == 1
    task_id, _msg, retries, _timeout = failures[0]
    assert task_id == "task-1"
    assert retries == 0  # engine opens an incident (human-visible), never retried (ADR-0008)
