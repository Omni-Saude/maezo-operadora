"""Unit tests for maezo.tools.workers.programa (SP-OP-PROGRAMA-001).

TDD London School: tests verify the consent chokepoint and clinical discharge guard.
"""

import pytest
import structlog.testing

from maezo.tools.workers import programa as programa_module
from maezo.tools.workers.harness import (
    ExternalTask,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerBpmnError,
    WorkerHarness,
)
from maezo.tools.workers.programa import (
    ERR_PROGRAM_DISCHARGE_NOT_HUMAN,
    ERR_PROGRAMA_NO_CONSENT,
    GAP_ENROLL_A2A_NAO_LIGADO,
    RISCO_FAIL_CLOSED_DEFAULT,
    ProgramaError,
    check_consent,
    enroll_beneficiario,
    make_notify_sla_risk_handler,
    make_proactive_contact_handler,
    make_stop_processing_handler,
    make_stratify_risk_handler,
    notify_sla_risk,
    proactive_contact,
    register_discharge,
    register_program_discharge,
    register_programa_workers,
    stop_processing,
    stratify_risk,
)


def _task(
    *,
    topic: str = "operadora.programa.probe",
    business_key: str = "PROG-amh-cronicos-b-001-2026",
    variables: dict | None = None,
) -> ExternalTask:
    return ExternalTask(
        task_id="task-1",
        topic=topic,
        process_instance_id="proc-1",
        business_key=business_key,
        worker_id="w-1",
        variables=variables or {},
    )


# T3.1 (mirrors lgpd.py's ADR-0031 `identidade_verificada` fail-closed matrix): the consent
# chokepoint (`consentimento_ativo`/`consent_checked`) is pinned to the explicit boolean `True` —
# NOT bare truthiness. Absent / False / None / any truthy junk (string incl. whitespace-only, int,
# list, dict) must NEVER be read as active/verified consent — this chokepoint "gates ALL PHI
# processing" (module docstring; contract SP-OP-PROGRAMA-001.md:15). Shared vectors reused across
# check_consent's and stratify_risk's own parametrized fail-closed tests below.
_CONSENT_NON_TRUE_VECTORS: list[dict[str, object]] = [
    {"consentimento_ativo": False},  # explicit False (consent_checked left True)
    {"consentimento_ativo": None},  # explicit None
    {"consentimento_ativo": "true"},  # garbage: truthy string, not the bool True
    {"consentimento_ativo": " "},  # garbage: whitespace-only truthy string
    {"consentimento_ativo": 1},  # garbage: truthy int, not the bool True
    {"consentimento_ativo": [1]},  # garbage: truthy list, not the bool True
    {"consentimento_ativo": {"ok": True}},  # garbage: truthy dict, not the bool True
    {"consent_checked": False},  # explicit False (consentimento_ativo left True)
    {"consent_checked": None},  # explicit None
    {"consent_checked": "true"},  # garbage: truthy string, not the bool True
    {"consent_checked": " "},  # garbage: whitespace-only truthy string
    {"consent_checked": 1},  # garbage: truthy int, not the bool True
    {"consent_checked": [1]},  # garbage: truthy list, not the bool True
    {"consent_checked": {"ok": True}},  # garbage: truthy dict, not the bool True
]

# ---------------------------------------------------------------
# check_consent — CHOKEPOINT
# ---------------------------------------------------------------


def test_programa_consent_gate_happy_path() -> None:
    result = check_consent(
        {
            "consentimento_ativo": True,
            "consent_checked": True,
            "consent_scope": "programa_cuidado",
            "beneficiario_pseudo_id": "b-001",
        }
    )
    assert result["consentimento_ativo"] is True


def test_check_consent_nao_afirma_timestamp_de_verificacao() -> None:
    """FAB-PROGRAMA-NOW-TIMESTAMPS: `consent_verified_at` era o literal `"now"`.

    Nao foi substituido por um relogio real: a chave tinha ZERO consumidores (nenhuma
    `conditionExpression`, `inputExpression` de DMN, worker a jusante, golden ou linha de contrato —
    `docs/processes/contracts/SP-OP-PROGRAMA-001.md` nunca a declarou em "Variaveis de saida") e o
    instante em que o gate rodou e passou ja e um fato do engine (`activity-instance` de
    `ST_CheckConsent`), entao um segundo carimbo no escopo do processo seria uma fonte de verdade
    redundante — nao uma correcao. A chave foi REMOVIDA; `consentimento_ativo` (o fato booleano
    real, com consumidor a jusante em `stratify_risk`/`proactive_contact`) permanece.
    """
    result = check_consent(
        {
            "consentimento_ativo": True,
            "consent_checked": True,
            "consent_scope": "programa_cuidado",
            "beneficiario_pseudo_id": "b-001",
        }
    )
    assert "consent_verified_at" not in result
    assert "now" not in result.values()


def test_check_consent_retorno_tem_exatamente_as_chaves_honestas() -> None:
    """Particao fechada: `check_consent` so afirma `consentimento_ativo` no caminho feliz."""
    result = check_consent(
        {
            "consentimento_ativo": True,
            "consent_checked": True,
            "consent_scope": "programa_cuidado",
            "beneficiario_pseudo_id": "b-001",
        }
    )
    assert result == {"consentimento_ativo": True}


def test_programa_consent_gate_blocks_no_consent() -> None:
    """Without consent, the chokepoint blocks ALL PHI processing.

    check_consent signals the MODELED boundary error (ADR-0030 §2): a WorkerBpmnError carrying
    ERR_PROGRAMA_NO_CONSENT so BE_SemConsentimento -> End_SemConsentimento can fire.
    """
    with pytest.raises(WorkerBpmnError) as excinfo:
        check_consent(
            {
                "consentimento_ativo": False,
                "consent_checked": False,
                "consent_scope": "programa_cuidado",
            }
        )
    assert excinfo.value.error_code == ERR_PROGRAMA_NO_CONSENT


def test_programa_consent_gate_blocks_revoked() -> None:
    """Revoked consent (ativo=False) blocks processing."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        check_consent(
            {
                "consentimento_ativo": False,
                "consent_checked": True,
                "consent_scope": "programa_cuidado",
            }
        )
    assert excinfo.value.error_code == ERR_PROGRAMA_NO_CONSENT


def test_programa_consent_gate_blocks_not_checked() -> None:
    """consent_checked=False means consent was never verified."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        check_consent(
            {
                "consentimento_ativo": True,
                "consent_checked": False,
                "consent_scope": "programa_cuidado",
            }
        )
    assert excinfo.value.error_code == ERR_PROGRAMA_NO_CONSENT


@pytest.mark.parametrize("vars_extra", _CONSENT_NON_TRUE_VECTORS)
def test_programa_consent_gate_fail_closed_rejects_non_true_signal(
    vars_extra: dict[str, object],
) -> None:
    """FAIL-CLOSED (T3.1): the chokepoint only accepts the literal `is True` for each consent flag.

    False/None/truthy-junk (string incl. whitespace-only, int, list, dict) on EITHER flag must
    NEVER be read as active/verified consent — closes the fail-OPEN class this change fixes.
    """
    variables: dict[str, object] = {
        "consentimento_ativo": True,
        "consent_checked": True,
        "consent_scope": "programa_cuidado",
        **vars_extra,
    }

    with pytest.raises(WorkerBpmnError) as excinfo:
        check_consent(variables)
    assert excinfo.value.error_code == ERR_PROGRAMA_NO_CONSENT


# ---------------------------------------------------------------
# stratify_risk — in-zone risk-band delegation stub (care.stratify — Valentina A2A)
# ---------------------------------------------------------------


def _consented_variables(**overrides: object) -> dict[str, object]:
    variables: dict[str, object] = {
        "consentimento_ativo": True,
        "consent_checked": True,
        "consent_scope": "programa_cuidado",
        "beneficiario_pseudo_id": "b-001",
        "programa_id": "cronicos",
    }
    variables.update(overrides)
    return variables


def test_stratify_risk_happy_path_echoes_pre_resolved_band() -> None:
    """A pre-resolved, valid risk band is echoed through unchanged (instrui, nao decide)."""
    result = stratify_risk(_consented_variables(risco_estratificado="moderado"))
    assert result["risco_estratificado"] == "moderado"
    assert result["risco_estratificado_origem"] == "pre_resolvido"


def test_stratify_risk_normalizes_case_and_whitespace() -> None:
    """Only mechanical coercion (case/whitespace) — never business derivation."""
    result = stratify_risk(_consented_variables(risco_estratificado="  ALTO  "))
    assert result["risco_estratificado"] == "alto"
    assert result["risco_estratificado_origem"] == "pre_resolvido"


def test_stratify_risk_refuses_without_active_consent() -> None:
    """Defense-in-depth: estratificacao de risco is PHI processing gated by consent (Invariante A).

    Same ERR_PROGRAMA_NO_CONSENT code as check_consent's chokepoint — this is the SAME invariant,
    checked a second time in case this task is ever reached without the gate having passed.
    """
    with pytest.raises(ProgramaError) as excinfo:
        stratify_risk(_consented_variables(consentimento_ativo=False))
    assert excinfo.value.code == ERR_PROGRAMA_NO_CONSENT


def test_stratify_risk_refuses_without_consent_checked() -> None:
    with pytest.raises(ProgramaError) as excinfo:
        stratify_risk(_consented_variables(consent_checked=False))
    assert excinfo.value.code == ERR_PROGRAMA_NO_CONSENT


@pytest.mark.parametrize("vars_extra", _CONSENT_NON_TRUE_VECTORS)
def test_stratify_risk_fail_closed_rejects_non_true_signal(vars_extra: dict[str, object]) -> None:
    """FAIL-CLOSED (T3.1): this defense-in-depth guard only accepts the literal `is True` for each
    consent flag — same invariant/vectors as check_consent's own parametrized fail-closed test."""
    with pytest.raises(ProgramaError) as excinfo:
        stratify_risk(_consented_variables(**vars_extra))
    assert excinfo.value.code == ERR_PROGRAMA_NO_CONSENT


def test_stratify_risk_fail_closed_default_when_missing() -> None:
    """No pre-resolved band available => fail-closed to the DMN's lowest-autonomy path ("alto"),
    which programa_routing ALWAYS routes to ANALISE_HUMANA (never auto-elegivel/auto-alta)."""
    result = stratify_risk(_consented_variables())
    assert result["risco_estratificado"] == RISCO_FAIL_CLOSED_DEFAULT
    assert result["risco_estratificado_origem"] == "fail_closed_default"


def test_stratify_risk_fail_closed_default_when_invalid_value() -> None:
    """A band outside the DMN's known vocabulary is treated as unresolved, not guessed at."""
    result = stratify_risk(_consented_variables(risco_estratificado="urgentissimo"))
    assert result["risco_estratificado"] == RISCO_FAIL_CLOSED_DEFAULT
    assert result["risco_estratificado_origem"] == "fail_closed_default"


def test_stratify_risk_never_sets_decisao_programa() -> None:
    """Invariant C: no worker in this module (esp. stratify_risk) may set decisao_programa."""
    result = stratify_risk(_consented_variables(risco_estratificado="alto"))
    assert "decisao_programa" not in result


# ---------------------------------------------------------------
# enroll_beneficiario
# ---------------------------------------------------------------


def test_enroll_beneficiario_nao_afirma_enrollment_realizado() -> None:
    """ENROLL-BENEFICIARIO-SEM-EFEITO-REAL: `enrollment_realizado=True` era afirmado por uma
    funcao que nao executa enrollment algum.

    O corpo faz SO `logger.info` — nenhuma escrita, nenhuma chamada A2A. O contrato
    (`docs/processes/contracts/SP-OP-PROGRAMA-001.md`, linha do topico
    `operadora.programa.build_care_plan`) e o proprio modulo (`programa.py:687`, "spec match: task
    name says 'care.enroll'") declaram uma delegacao A2A `care.enroll` a Valentina que o codigo
    nunca chama. Mesma especie que BEA-09 corrigiu para `dossie_montado`/`referral_executado`.
    """
    result = enroll_beneficiario(
        {
            "programa_id": "cronicos",
            "beneficiario_pseudo_id": "b-001",
        }
    )
    assert "enrollment_realizado" not in result
    assert True not in result.values()


def test_enroll_beneficiario_declara_a_lacuna_do_a2a() -> None:
    """A lacuna chega DECLARADA (token fechado, sem PHI) em vez de uma afirmacao falsa."""
    result = enroll_beneficiario(
        {
            "programa_id": "cronicos",
            "beneficiario_pseudo_id": "b-001",
        }
    )
    assert result["enrollment_gap"] == GAP_ENROLL_A2A_NAO_LIGADO
    assert GAP_ENROLL_A2A_NAO_LIGADO == "enroll_a2a_nao_ligado"


def test_enroll_beneficiario_loga_a_lacuna() -> None:
    """Observabilidade sobrevive ao fix: a etapa continua logando, e o log declara a lacuna em vez
    de afirmar um enrollment que nao ocorreu (mesma disciplina de `assemble_dossier`/BEA-09)."""
    with structlog.testing.capture_logs() as logs:
        enroll_beneficiario({"programa_id": "cronicos", "beneficiario_pseudo_id": "b-001"})
    events = [entry for entry in logs if entry.get("event") == "programa_enroll_gap"]
    assert events, "a etapa TEM de continuar observavel no log"
    assert events[0]["enrollment_asserted"] is False
    assert events[0]["gap"] == GAP_ENROLL_A2A_NAO_LIGADO
    assert events[0]["programa_id"] == "cronicos"


def test_enroll_beneficiario_nao_afirma_timestamp_de_enrollment() -> None:
    """FAB-PROGRAMA-NOW-TIMESTAMPS: `data_enrollment` era o literal `"now"`.

    Mesma especie e mesmo tratamento de `consent_verified_at` (`check_consent`, acima): ZERO
    consumidores (nenhuma `conditionExpression`, `inputExpression`, worker a jusante, golden ou
    linha de contrato) e o instante do enrollment ja e fato do engine (`activity-instance` de
    `ST_BuildCarePlan`) — um segundo carimbo seria redundante, nao correcao. A chave foi REMOVIDA;
    `enrollment_realizado` (o fato booleano real que a funcao de fato executa/loga) permanece.
    """
    result = enroll_beneficiario(
        {
            "programa_id": "cronicos",
            "beneficiario_pseudo_id": "b-001",
        }
    )
    assert "data_enrollment" not in result
    assert "now" not in result.values()


def test_enroll_beneficiario_retorno_tem_exatamente_a_chave_honesta() -> None:
    """Particao fechada: a UNICA saida e a lacuna declarada."""
    result = enroll_beneficiario(
        {
            "programa_id": "cronicos",
            "beneficiario_pseudo_id": "b-001",
        }
    )
    assert result == {"enrollment_gap": GAP_ENROLL_A2A_NAO_LIGADO}


# ---------------------------------------------------------------
# register_discharge / register_program_discharge — L0-hard GUARD
# ---------------------------------------------------------------


def test_programa_discharge_happy_path() -> None:
    result = register_program_discharge(
        {
            "decisao_programa": "DESLIGAR_CLINICO",
            "motivo_desligamento_clinico": "Alta apos conclusao do ciclo terapeutico",
            "referencia_clinica": "Protocolo HCPA 2023",
            "responsavel_clinico_id": "med-001",
        }
    )
    assert result["desligamento_clinico_registrado"] is True


def test_programa_discharge_sets_desfecho_desligamento_clinico_humano() -> None:
    """GAP-PROG-2 completion (item-9 wave-5, item D): the ONLY producer of the adverse
    `desfecho=desligamento_clinico_humano` value that ST_PublishCompleted's `event_desfecho`
    ternary (BPMN:323) depends on — set ONLY on this success path, never on a guard refusal."""
    result = register_program_discharge(
        {
            "decisao_programa": "DESLIGAR_CLINICO",
            "motivo_desligamento_clinico": "Alta apos conclusao do ciclo terapeutico",
            "referencia_clinica": "Protocolo HCPA 2023",
            "responsavel_clinico_id": "med-001",
        }
    )
    assert result["desfecho"] == "desligamento_clinico_humano"


def test_programa_discharge_rejects_enroll() -> None:
    """ENROLL is not DESLIGAR — guard must reject."""
    with pytest.raises(ProgramaError) as excinfo:
        register_program_discharge(
            {
                "decisao_programa": "ENROLL",
                "motivo_desligamento_clinico": "x",
                "referencia_clinica": "x",
                "responsavel_clinico_id": "med-001",
            }
        )
    assert excinfo.value.code == ERR_PROGRAM_DISCHARGE_NOT_HUMAN


def test_programa_discharge_rejects_missing_clinico() -> None:
    """Clinical decision without responsible clinician."""
    with pytest.raises(ProgramaError) as excinfo:
        register_program_discharge(
            {
                "decisao_programa": "DESLIGAR_CLINICO",
                "motivo_desligamento_clinico": "x",
                "referencia_clinica": "x",
                "responsavel_clinico_id": "",
            }
        )
    assert excinfo.value.code == ERR_PROGRAM_DISCHARGE_NOT_HUMAN


def test_programa_register_discharge_alias() -> None:
    result = register_discharge(
        {
            "decisao_programa": "DESLIGAR_CLINICO",
            "motivo_desligamento_clinico": "Alta clinica",
            "referencia_clinica": "Protocolo X",
            "responsavel_clinico_id": "med-001",
        }
    )
    assert result["desligamento_clinico_registrado"] is True


# ---------------------------------------------------------------
# register_program_discharge — whitespace-bypass vectors (t3.1-guard-input-hardening,
# same class as the c1377fa fix for pagto.register_payment_refusal: bare `if not field:`
# let WHITESPACE-ONLY decision + accountability fields through. L0-HARD CLINICAL decision
# (ADR-0005/0008): a discharge NEVER registers without a genuine human clinician behind it.
# Every vector below MUST refuse with ERR_PROGRAM_DISCHARGE_NOT_HUMAN — whitespace-only is
# the SAME as absent (ADR-0007).
# ---------------------------------------------------------------

_WHITESPACE_VARIANTS = [" ", "   ", "\t", "\n", "\t\n ", "\r\n"]
_NON_STRING_VARIANTS: list[object] = [123, True, 0.5, ["x"], {"k": "v"}]
_DISCHARGE_ACCOUNTABILITY_FIELDS = [
    "motivo_desligamento_clinico",
    "referencia_clinica",
    "responsavel_clinico_id",
]


def _discharge_baseline(**overrides: object) -> dict[str, object]:
    """Happy-path baseline for register_program_discharge -- any guard failure observed in a
    test is attributable ONLY to the field under test."""
    base: dict[str, object] = {
        "decisao_programa": "DESLIGAR_CLINICO",
        "motivo_desligamento_clinico": "Alta apos conclusao do ciclo terapeutico",
        "referencia_clinica": "Protocolo HCPA 2023",
        "responsavel_clinico_id": "med-001",
        "beneficiario_pseudo_id": "b-001",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("field", _DISCHARGE_ACCOUNTABILITY_FIELDS)
@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_discharge_whitespace_only_accountability_field_refuses(field: str, whitespace: str) -> None:
    """Bare-truthiness bypass (pre-fix): whitespace-only accountability field must refuse and
    be named in the guard's error message."""
    with pytest.raises(ProgramaError) as excinfo:
        register_program_discharge(_discharge_baseline(**{field: whitespace}))
    assert excinfo.value.code == ERR_PROGRAM_DISCHARGE_NOT_HUMAN
    assert field in excinfo.value.message


@pytest.mark.parametrize("field", _DISCHARGE_ACCOUNTABILITY_FIELDS)
@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_discharge_non_string_accountability_field_refuses(field: str, non_string: object) -> None:
    """A NON-string accountability field normalizes to '' and refuses -- the pre-fix bare
    truthiness check would have silently PASSED a truthy non-string (e.g. 123), registering
    an L0-hard clinical discharge with a non-identifying clinician."""
    with pytest.raises(ProgramaError) as excinfo:
        register_program_discharge(_discharge_baseline(**{field: non_string}))
    assert excinfo.value.code == ERR_PROGRAM_DISCHARGE_NOT_HUMAN
    assert field in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_discharge_both_motivo_and_responsavel_whitespace_refuses(whitespace: str) -> None:
    """Both motivo_desligamento_clinico AND responsavel_clinico_id whitespace-only -- both
    named in the guard's error message."""
    with pytest.raises(ProgramaError) as excinfo:
        register_program_discharge(
            _discharge_baseline(motivo_desligamento_clinico=whitespace, responsavel_clinico_id=whitespace)
        )
    assert excinfo.value.code == ERR_PROGRAM_DISCHARGE_NOT_HUMAN
    assert "motivo_desligamento_clinico" in excinfo.value.message
    assert "responsavel_clinico_id" in excinfo.value.message


@pytest.mark.parametrize("whitespace", [" ", "\t", "\n", "  \t\n"])
def test_discharge_whitespace_only_decisao_refuses(whitespace: str) -> None:
    """Whitespace-only decisao_programa normalizes to '' -> != DESLIGAR_CLINICO -> refuses
    (ST_PublishReceived default-initializes decisao_programa='' — BPMN:98-104; '' is NEVER a
    decision value)."""
    with pytest.raises(ProgramaError) as excinfo:
        register_program_discharge(_discharge_baseline(decisao_programa=whitespace))
    assert excinfo.value.code == ERR_PROGRAM_DISCHARGE_NOT_HUMAN
    assert "decisao_programa" in excinfo.value.message


def test_discharge_padded_valid_literal_normalizes_and_registers() -> None:
    """Whitespace-PADDED but otherwise exact literal/fields normalize via `_norm_str` and still
    register (pins the normalization -- NOT a bypass, the documented `.strip()` consequence)."""
    result = register_program_discharge(
        _discharge_baseline(
            decisao_programa=" DESLIGAR_CLINICO ",
            motivo_desligamento_clinico=" Alta apos conclusao do ciclo terapeutico ",
            referencia_clinica=" Protocolo HCPA 2023 ",
            responsavel_clinico_id=" med-001 ",
        )
    )
    assert result["desligamento_clinico_registrado"] is True


@pytest.mark.parametrize(
    "decision",
    ["desligar_clinico", "Desligar_Clinico", "DESLIGAR_CLINICO_X", "XDESLIGAR_CLINICO", "ENROLL "],
)
def test_discharge_non_exact_decisao_literal_still_refuses(decision: str) -> None:
    """Exact-match discipline survives normalization: case variants/substrings never satisfy
    the L0-hard clinical guard."""
    with pytest.raises(ProgramaError) as excinfo:
        register_program_discharge(_discharge_baseline(decisao_programa=decision))
    assert excinfo.value.code == ERR_PROGRAM_DISCHARGE_NOT_HUMAN


# ---------------------------------------------------------------
# stop_processing
# ---------------------------------------------------------------


def test_stop_processing() -> None:
    result = stop_processing(
        {
            "beneficiario_pseudo_id": "b-001",
        }
    )
    assert result["processamento_parado"] is True
    assert result["motivo"] == "revogacao_consentimento"


# ---------------------------------------------------------------
# proactive_contact — item-9 wave-5 item B: ST_ProactiveContact (new worker)
# ---------------------------------------------------------------


def test_proactive_contact_declara_a_lacuna_de_canal_em_vez_de_afirmar_contato() -> None:
    """NEW-A2-2: `contato_realizado=True` era fato fabricado — NENHUM canal e contatado.

    `ST_ProactiveContact` E uma task real do BPMN (topico declarado), mas nada neste repo fala com
    o beneficiario a partir dela: o handler raw publica uma notificacao de OBSERVABILIDADE em
    `operadora.notifications.internal` (`programa.proactive_contact`), que nao chega a pessoa
    alguma; e no caminho `kafka is None` a funcao afirmava o contato sem ter feito nada. Os
    remetentes reais da classe de acao `comunicacao_beneficiario`
    (`spec/policies/autonomy/action-approvals.yaml`) sao as superficies WhatsApp dos agentes,
    nunca este worker.

    Portanto ha lacuna REAL a declarar (ao contrario de `fraude.intake`, cujo registro acontece de
    fato noutro lugar e por isso devolve `{}`): o retorno passa a ser exatamente
    `{"contato_gap": <token de classe>}`, mesma forma e mesma disciplina de
    `evidencia_gap`/`dossie_gap`/`referral_gap` (BEA-09/FAB-REFER-TO-LEGAL) e de
    `enrollment_gap` (ENROLL-BENEFICIARIO-SEM-EFEITO-REAL) neste mesmo modulo.
    """
    token = getattr(programa_module, "GAP_CONTATO_BENEFICIARIO_NAO_LIGADO", None)
    assert token == "contato_beneficiario_nao_ligado", (
        "o token de classe da lacuna de canal precisa existir no modulo (vocabulario FECHADO, "
        "sem PHI, declarado no contrato)"
    )

    result = proactive_contact(_consented_variables())

    assert result == {"contato_gap": token}
    assert "contato_realizado" not in result


def test_proactive_contact_never_sets_desfecho() -> None:
    """BPMN's own `ST_ProactiveContact` outputParameter literal (~157) stamps
    `desfecho=enrollment_realizado` — the worker itself must never set it."""
    result = proactive_contact(_consented_variables())
    assert "desfecho" not in result


def test_proactive_contact_refuses_without_active_consent() -> None:
    """Defense-in-depth guard mirrors stratify_risk's EXACT pattern (same code, same invariant)."""
    with pytest.raises(ProgramaError) as excinfo:
        proactive_contact(_consented_variables(consentimento_ativo=False))
    assert excinfo.value.code == ERR_PROGRAMA_NO_CONSENT


def test_proactive_contact_refuses_without_consent_checked() -> None:
    with pytest.raises(ProgramaError) as excinfo:
        proactive_contact(_consented_variables(consent_checked=False))
    assert excinfo.value.code == ERR_PROGRAMA_NO_CONSENT


@pytest.mark.parametrize("vars_extra", _CONSENT_NON_TRUE_VECTORS)
def test_proactive_contact_fail_closed_rejects_non_true_signal(vars_extra: dict[str, object]) -> None:
    with pytest.raises(ProgramaError) as excinfo:
        proactive_contact(_consented_variables(**vars_extra))
    assert excinfo.value.code == ERR_PROGRAMA_NO_CONSENT


def test_proactive_contact_guard_error_is_not_workerbpmnerror_subclass() -> None:
    """ADR-0030: ST_ProactiveContact carries NO error boundary (re-verified: no
    `errorEventDefinition` attached to it in the BPMN) — a WorkerBpmnError here would silently
    end the process scope on CIB Seven 2.1.0. The guard MUST raise a plain ProgramaError, never
    a WorkerBpmnError (nor any subclass relationship between the two)."""
    assert not issubclass(ProgramaError, WorkerBpmnError)
    with pytest.raises(ProgramaError) as excinfo:
        proactive_contact(_consented_variables(consentimento_ativo=False))
    assert not isinstance(excinfo.value, WorkerBpmnError)


# ---------------------------------------------------------------
# notify_sla_risk — item-9 wave-5 item C: ST_NotifySlaRisk (new worker)
# ---------------------------------------------------------------


def test_notify_sla_risk_nao_afirma_notificacao() -> None:
    """FAB-SLA-RISK-NOTIFIED-SLICE4: a funcao pura retorna `{}` — NAO afirma nada.

    `sla_risk_notified` era a UNICA chave que esta funcao escrevia no escopo do processo. O
    `== {}` e' deliberado: so a igualdade exata pega uma fabricacao remontada chave-a-chave num
    local, que a cerca AST documenta nao alcancar.
    """
    assert notify_sla_risk({"beneficiario_pseudo_id": "b-001", "programa_id": "cronicos"}) == {}


@pytest.mark.parametrize(
    "variables",
    [
        {},
        {"programa_id": "cronicos"},
        {"beneficiario_pseudo_id": "b-001", "decisao_clinica": "ENCERRAR"},
    ],
)
def test_notify_sla_risk_nenhuma_entrada_produz_afirmacao(variables: dict) -> None:
    """Alerta informativo: entrada em branco completa sem excecao e nada e' afirmado."""
    assert notify_sla_risk(variables) == {}


# ---------------------------------------------------------------
# Raw-handler Kafka seam (item A root fix + items B/C new workers)
# ---------------------------------------------------------------


async def test_make_stratify_risk_handler_publishes_notification() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_stratify_risk_handler(kafka)
    task = _task(
        topic="operadora.programa.stratify_risk",
        variables={
            "tenant_id": "amh",
            "programa_id": "cronicos",
            "beneficiario_pseudo_id": "b-001",
            "consentimento_ativo": True,
            "consent_checked": True,
            "risco_estratificado": "moderado",
        },
    )
    result = await handler(task)
    assert result["risco_estratificado"] == "moderado"
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload["type"] == "programa.stratify_risk"
    assert payload["tenant_id"] == "amh"
    assert payload["programa_id"] == "cronicos"
    assert payload["beneficiario_pseudo_id"] == "b-001"
    assert payload["risco_estratificado"] == "moderado"
    assert key == task.business_key
    # NON-HOLLOW (t2-notify-integrity item 1): forced propagate-on-failure — no BPMN boundary is
    # declared on ST_StratifyRisk.
    assert kafka.best_effort_calls == [False]


async def test_make_stratify_risk_handler_no_producer_still_completes() -> None:
    """kafka=None: completes anyway (the task must not block the flow), never fabricates a
    publish."""
    handler = make_stratify_risk_handler(None)
    result = await handler(
        _task(variables={"consentimento_ativo": True, "consent_checked": True, "risco_estratificado": "alto"})
    )
    assert result["risco_estratificado"] == "alto"


async def test_make_stratify_risk_handler_no_consent_reclassifies_to_value_error() -> None:
    """The raw handler must NOT let ProgramaError propagate raw — `FunctionWorker.execute`
    already reclassified this exact guard to ValueError before this wave; moving to a raw
    handler (item A) must preserve that classification (`_call_guarded`) so the harness still
    routes it to `failure(retries=0)` (a never-retried, human-visible incident) instead of the
    engine-computed-retry path a bare, unclassified exception would take."""
    kafka = FakeKafkaPublisher()
    handler = make_stratify_risk_handler(kafka)
    task = _task(
        topic="operadora.programa.stratify_risk",
        variables={"consentimento_ativo": False, "consent_checked": True, "beneficiario_pseudo_id": "b-001"},
    )
    with pytest.raises(ValueError, match=ERR_PROGRAMA_NO_CONSENT) as excinfo:
        await handler(task)
    assert not isinstance(excinfo.value, WorkerBpmnError)
    assert not isinstance(excinfo.value, ProgramaError)
    assert kafka.published == [], "guard refusal must never publish a notification"


async def test_make_stop_processing_handler_publishes_notification() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_stop_processing_handler(kafka)
    task = _task(
        topic="operadora.programa.stop_processing",
        variables={
            "tenant_id": "amh",
            "programa_id": "cronicos",
            "beneficiario_pseudo_id": "b-001",
        },
    )
    result = await handler(task)
    assert result["processamento_parado"] is True
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload["type"] == "programa.stop_processing"
    assert payload["tenant_id"] == "amh"
    assert payload["programa_id"] == "cronicos"
    assert payload["beneficiario_pseudo_id"] == "b-001"
    assert key == task.business_key
    assert kafka.best_effort_calls == [False]


async def test_make_stop_processing_handler_no_producer_still_completes() -> None:
    handler = make_stop_processing_handler(None)
    result = await handler(_task(variables={"beneficiario_pseudo_id": "b-001"}))
    assert result["processamento_parado"] is True


async def test_make_proactive_contact_handler_publishes_notification() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_proactive_contact_handler(kafka)
    task = _task(
        topic="operadora.programa.proactive_contact",
        variables={
            "tenant_id": "amh",
            "programa_id": "cronicos",
            "beneficiario_pseudo_id": "b-001",
            "consentimento_ativo": True,
            "consent_checked": True,
        },
    )
    result = await handler(task)
    assert result == {"contato_gap": "contato_beneficiario_nao_ligado"}
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload["type"] == "programa.proactive_contact"
    assert payload["tenant_id"] == "amh"
    assert payload["programa_id"] == "cronicos"
    assert payload["beneficiario_pseudo_id"] == "b-001"
    assert key == task.business_key
    assert kafka.best_effort_calls == [False]


async def test_make_proactive_contact_handler_no_producer_still_completes() -> None:
    handler = make_proactive_contact_handler(None)
    result = await handler(_task(variables={"consentimento_ativo": True, "consent_checked": True}))
    # `kafka is None` era o caminho mais desonesto dos dois: afirmava o contato sem sequer a
    # notificacao de observabilidade ter saido.
    assert result == {"contato_gap": "contato_beneficiario_nao_ligado"}


async def test_make_proactive_contact_handler_no_consent_reclassifies_to_value_error() -> None:
    """Same reclassification guarantee as stratify_risk's raw handler (item B new worker)."""
    kafka = FakeKafkaPublisher()
    handler = make_proactive_contact_handler(kafka)
    task = _task(
        topic="operadora.programa.proactive_contact",
        variables={"consentimento_ativo": True, "consent_checked": False, "beneficiario_pseudo_id": "b-001"},
    )
    with pytest.raises(ValueError, match=ERR_PROGRAMA_NO_CONSENT) as excinfo:
        await handler(task)
    assert not isinstance(excinfo.value, WorkerBpmnError)
    assert not isinstance(excinfo.value, ProgramaError)
    assert kafka.published == [], "guard refusal must never publish a notification"


async def test_make_notify_sla_risk_handler_publishes_notification() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_notify_sla_risk_handler(kafka)
    task = _task(
        topic="operadora.programa.notify_sla_risk",
        variables={
            "tenant_id": "amh",
            "programa_id": "cronicos",
            "beneficiario_pseudo_id": "b-001",
        },
    )
    result = await handler(task)
    # FAB-SLA-RISK-NOTIFIED-SLICE4: mesmo COM publish, o retorno nao afirma notificacao — o
    # registro interno e' um PEDIDO de alerta, nunca a prova de que a equipe foi avisada.
    # O publish em si (topico, payload, chave, best_effort) segue pinado byte-a-byte abaixo.
    assert result == {}
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload["type"] == "programa.notify_sla_risk"
    assert payload["tenant_id"] == "amh"
    assert payload["programa_id"] == "cronicos"
    assert payload["beneficiario_pseudo_id"] == "b-001"
    assert key == task.business_key
    assert kafka.best_effort_calls == [False]


async def test_make_notify_sla_risk_handler_no_producer_still_completes() -> None:
    """FAB-SLA-RISK-NOTIFIED-SLICE4 — era exatamente AQUI que a fabricacao doia: o wrapper
    devolvia `{"sla_risk_notified": True}` no caminho SEM produtor, afirmando a notificacao
    justamente quando nada tinha sido publicado. Agora `{}`, e a task segue completando."""
    handler = make_notify_sla_risk_handler(None)
    result = await handler(_task(variables={"beneficiario_pseudo_id": "b-001"}))
    assert result == {}


# ---------------------------------------------------------------
# register_programa_workers — 8 operadora.programa.* topics (item A/B/C deltas)
# ---------------------------------------------------------------


def test_register_programa_workers_registers_all_7_topics() -> None:
    """PERSP-C5-MONITOR-PROGRAMA: `operadora.programa.monitor_programa` foi REMOVIDO — era um
    topico registrado que nenhum serviceTask do SP-OP-PROGRAMA-001 declara (orfao
    inalcancavel) e cujo unico output era um `monitoramento_atualizado: True` fabricado.
    Este teste passa a provar que a contagem caiu para 7 e que o topico NAO volta."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_programa_workers(harness, FakeKafkaPublisher())
    topics = set(harness.registered_topics)
    for topic in (
        "operadora.programa.check_consent",
        "operadora.programa.build_care_plan",
        "operadora.programa.register_program_discharge",
        "operadora.programa.stratify_risk",
        "operadora.programa.stop_processing",
        "operadora.programa.proactive_contact",
        "operadora.programa.notify_sla_risk",
    ):
        assert topic in topics, f"{topic} not registered by register_programa_workers"
    assert len(topics) == 7
    assert "operadora.programa.monitor_programa" not in topics, (
        "PERSP-C5-MONITOR-PROGRAMA: topico orfao nao deve voltar a ser registrado"
    )


def test_register_programa_workers_raw_handlers_outside_worker_registry() -> None:
    """item A/B/C's 4 raw handlers populate `_handlers` (dispatch-reachable) but NOT the
    `WorkerRegistry` — registered via `harness.register()`, not `harness.register_worker()`."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_programa_workers(harness, FakeKafkaPublisher())
    for topic in (
        "operadora.programa.stratify_risk",
        "operadora.programa.stop_processing",
        "operadora.programa.proactive_contact",
        "operadora.programa.notify_sla_risk",
    ):
        assert harness.registry.get(topic) is None


def test_register_programa_workers_accepts_kafka_none() -> None:
    """kafka=None (no producer wired) must not raise at registration time — only the raw
    handlers themselves log a warning when actually invoked."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_programa_workers(harness)
    assert len(harness.registered_topics) == 7
