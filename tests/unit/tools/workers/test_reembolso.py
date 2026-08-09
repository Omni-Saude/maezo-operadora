"""Unit tests for maezo.tools.workers.reembolso — SP-OP-REEMBOLSO-001.

TDD London School: tests exercise the external task contracts.
"""

import dataclasses
from pathlib import Path

import pytest

from maezo.tools.workers.ceilings import CeilingResolver
from maezo.tools.workers.harness import (
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerHarness,
)
from maezo.tools.workers.reembolso import (
    _BASE_VALUES_CENTS,
    ReembolsoDenialInput,
    ReembolsoDenialNotHumanError,
    ReembolsoInput,
    ReembolsoProtocoloInvalidoError,
    ReembolsoValorPagamentoInvalidoError,
    analyze_request,
    analyze_request_entry,
    calculate_amount_entry,
    calculate_value,
    check_coverage,
    check_coverage_entry,
    check_prazo_entry,
    issue_payment_entry,
    notify_sla_risk,
    notify_sla_risk_entry,
    process_payment,
    publish_completed,
    publish_completed_entry,
    register_reembolso_workers,
    request_documents,
    request_documents_entry,
    send_reembolso_denial,
    send_reembolso_denial_entry,
    validate_reembolso,
)

# tests/unit/tools/workers/<file> -> parents[4] == repo root (same idiom as
# test_auth_denial_guard.py / test_auth_auto_criteria.py).
_REPO_ROOT = Path(__file__).resolve().parents[4]

# Synthetic-core helper (mirrors test_ceilings) — pins the reembolso ceiling in isolation so
# these tests do not depend on the D-07 value in the real spec matrix.
_HARD_BLOCK = """\
  clinical_decision:      { level: L0, hard: true }
  authorization_denial:   { level: L0, hard: true }
  nip_manter_negativa:    { level: L0, hard: true }
  fraud_accusation:       { level: L0, hard: true }
  contract_termination:   { level: L0, hard: true }
"""


def _pin_resolver(tmp_path: Path, max_value_brl: int) -> CeilingResolver:
    """Return a CeilingResolver pinned to a synthetic core with a given reembolso ceiling.

    ``tmp_path`` may be a not-yet-created SUBDIRECTORY of the fixture: the matrix load is
    ``lru_cache``d by resolved path, so a test that needs two different ceilings must pin them
    to two different paths.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    core = tmp_path / "L0-core.yaml"
    core.write_text(
        "version: 1\nactions:\n"
        f"{_HARD_BLOCK}"
        f"  reembolso_auto_approval: {{ level: L2, params: {{ max_value_brl: {max_value_brl} }} }}\n",
        encoding="utf-8",
    )
    return CeilingResolver(core_path=core)


# ---------------------------------------------------------------------------
# validate_reembolso
# ---------------------------------------------------------------------------


def test_validate_reembolso_valid() -> None:
    """validate_reembolso accepts a valid reembolso."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-001",
        codigo_procedimento_tuss="10101012",
        valor_solicitado_cents=35000,
        cobertura_prevista=True,
        documentacao_completa=True,
        dentro_prazo=True,
        beneficiario_ativo=True,
        carencia_cumprida=True,
    )
    result = validate_reembolso(inp)
    assert result.valid is True
    assert result.errors == []


def test_validate_reembolso_missing_protocolo() -> None:
    """validate_reembolso raises ERR_REEMBOLSO_INVALID_PROTOCOLO on missing protocolo."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="",
        codigo_procedimento_tuss="10101012",
    )
    with pytest.raises(ReembolsoProtocoloInvalidoError):
        validate_reembolso(inp)


def test_validate_reembolso_missing_procedimento() -> None:
    """validate_reembolso flags missing codigo_procedimento_tuss."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-002",
        codigo_procedimento_tuss="",
        valor_solicitado_cents=10000,
    )
    result = validate_reembolso(inp)
    assert any("procedimento" in e.lower() or "tuss" in e.lower() for e in result.errors)


def test_validate_reembolso_zero_value() -> None:
    """validate_reembolso flags valor_solicitado_cents <= 0."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-003",
        codigo_procedimento_tuss="10101012",
        valor_solicitado_cents=0,
    )
    result = validate_reembolso(inp)
    assert any("valor" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# check_coverage
# ---------------------------------------------------------------------------


def test_check_coverage_prevista() -> None:
    """check_coverage returns cobertura_prevista flag."""
    inp = ReembolsoInput(
        tenant_id="amh",
        codigo_procedimento_tuss="10101012",
        tipo_reembolso="livre_escolha",
        cobertura_prevista=True,
    )
    result = check_coverage(inp)
    assert result["cobertura_prevista"] is True


def test_check_coverage_nao_prevista() -> None:
    """check_coverage returns False for uncovered procedures."""
    inp = ReembolsoInput(
        tenant_id="amh",
        codigo_procedimento_tuss="99999999",
        tipo_reembolso="livre_escolha",
        cobertura_prevista=False,
    )
    result = check_coverage(inp)
    assert result["cobertura_prevista"] is False


# ---------------------------------------------------------------------------
# calculate_value
# ---------------------------------------------------------------------------


def test_calculate_value_consulta() -> None:
    """calculate_value computes table value for consulta."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-004",
        codigo_procedimento_tuss="10101012",
        categoria_procedimento="consulta",
        tipo_reembolso="livre_escolha",
        valor_solicitado_cents=35000,
    )
    result = calculate_value(inp)
    assert result.valor_calculado_tabela_cents > 0
    # Consulta table value should be around R$350
    assert result.valor_calculado_tabela_cents == 35000


def test_calculate_value_dentro_tabela() -> None:
    """Valor dentro da tabela -> dentro_tabela=True."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-005",
        codigo_procedimento_tuss="10101012",
        categoria_procedimento="consulta",
        tipo_reembolso="livre_escolha",
        valor_solicitado_cents=30000,  # Below table value
    )
    result = calculate_value(inp)
    assert result.dentro_tabela is True


def test_calculate_value_fora_tabela() -> None:
    """Valor acima da tabela -> dentro_tabela=False."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-006",
        codigo_procedimento_tuss="10101012",
        categoria_procedimento="consulta",
        tipo_reembolso="livre_escolha",
        valor_solicitado_cents=50000,  # Above table value
    )
    result = calculate_value(inp)
    assert result.dentro_tabela is False


def test_calculate_value_urgencia_multiplier() -> None:
    """Urgencia/emergencia applies 1.5x multiplier."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-007",
        codigo_procedimento_tuss="10101012",
        categoria_procedimento="consulta",
        tipo_reembolso="urgencia_emergencia",
        valor_solicitado_cents=50000,
    )
    result = calculate_value(inp)
    # Table base 35000 * 1.5 = 52500
    assert result.valor_calculado_tabela_cents == 52500


# ---------------------------------------------------------------------------
# calculate_value — T1.9 ceiling enforcement (defect B3)
# ---------------------------------------------------------------------------


def test_within_table_max_value_zero_routes_analise_humana(tmp_path: Path) -> None:
    """THE T1.9 property: a within-table request with max_value_brl=0 computes dentro_teto_l2=False.

    consulta @ 30000 cents is within the 35000 reference table (dentro_tabela=True), but the
    reembolso_auto_approval ceiling is 0 (D-07) -> dentro_teto_l2=False. The auto-approval
    routing itself is the NATIVE DMN `reembolso_auto_approval` (BRT_AutoApproval,
    spec/processes/dmn/reembolso_coverage.dmn — its ONLY AUTO_APROVAR rule, `r_auto`, requires
    dentro_teto_l2=true; hitPolicy FIRST with fail-safe catch-all ANALISE_HUMANA), so the False
    fact this worker computes is exactly what forces ANALISE_HUMANA engine-side. This is the
    exact acceptance criterion.
    """
    resolver = _pin_resolver(tmp_path, max_value_brl=0)
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-T19-1",
        codigo_procedimento_tuss="10101012",
        categoria_procedimento="consulta",
        tipo_reembolso="livre_escolha",
        valor_solicitado_cents=30000,
    )

    calculo = calculate_value(inp, resolver=resolver)
    assert calculo.dentro_tabela is True
    assert calculo.dentro_teto_l2 is False


def test_calculate_value_ignores_inbound_dentro_teto_l2(tmp_path: Path) -> None:
    """A seeded inbound `dentro_teto_l2=True` is IGNORED — the resolver truth (ceiling 0) wins.

    Proves the echo-through at old reembolso.py:227 is dead.
    """
    resolver = _pin_resolver(tmp_path, max_value_brl=0)
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-T19-2",
        codigo_procedimento_tuss="10101012",
        categoria_procedimento="consulta",
        tipo_reembolso="livre_escolha",
        valor_solicitado_cents=30000,
        dentro_teto_l2=True,  # attacker/upstream seed — must be ignored
    )

    calculo = calculate_value(inp, resolver=resolver)
    assert calculo.dentro_teto_l2 is False


def test_calculate_value_within_ceiling_when_configured(tmp_path: Path) -> None:
    """A real positive ceiling computes dentro_teto_l2=True, config-driven, at the inclusive boundary.

    The ceiling is compared against the REFERENCE-TABLE value, not the requested amount, so the
    boundary is walked by moving the ceiling around a fixed table value: `consulta` = 35000
    cents, ceiling R$350 -> 35000 <= 35000 within (True); ceiling R$349 -> 35000 > 34900 above
    (False). (The AUTO_APROVAR routing on a True fact is the native DMN
    `reembolso_auto_approval`, BRT_AutoApproval — engine-side, not this worker.)

    M-2 NOTE — this test previously drove the boundary with `categoria_procedimento=""`, whose
    docstring read "an unknown category makes valor_calculado == valor_solicitado". That was the
    M-2 defect itself, load-bearing in the test suite: the unknown-categoria fallback to the
    claimant's own figure. It also asserted `dentro_tabela is True` for a categoria that is in no
    table. Both are now impossible; the ceiling property under test is unchanged and is exercised
    against a real table row.
    """
    at_boundary = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-T19-3a",
        codigo_procedimento_tuss="10101012",
        categoria_procedimento="consulta",  # real table row: 35000 cents
        tipo_reembolso="livre_escolha",
        valor_solicitado_cents=30000,
    )
    calculo_ok = calculate_value(at_boundary, resolver=_pin_resolver(tmp_path / "at", max_value_brl=350))
    assert calculo_ok.valor_calculado_tabela_cents == 35000
    assert calculo_ok.dentro_tabela is True
    assert calculo_ok.dentro_teto_l2 is True

    above_resolver = _pin_resolver(tmp_path / "above", max_value_brl=349)
    assert calculate_value(at_boundary, resolver=above_resolver).dentro_teto_l2 is False


# ---------------------------------------------------------------------------
# calculate_value — M-2: money + audit-trail honesty
#
# The three facts calculate_value writes into the ADR-0007 audit chain
# (`dentro_tabela`, `multiplo_tabela_aplicado`, `fonte_tabela`) must describe what the
# lookup ACTUALLY did. The defect: an unknown/misspelled categoria fell back to the
# claimant's own `valor_solicitado_cents`, making the "reference table value" equal the
# request, so `valor_solicitado <= valor_calculado` was unconditionally True — a fabricated
# corroboration presented to the human reviewer as a verified fact — while
# `multiplo_tabela_aplicado` was hardcoded 1.0 (hiding the 1.5 uplift) and `fonte_tabela`
# was hardcoded "TUSS-REFERENCIA" (labelling an invented value as a TUSS table reading).
# ---------------------------------------------------------------------------

#: `(categoria, tipo_reembolso, valor_solicitado_cents)` ->
#: `(valor_calculado, dentro_tabela, multiplo, fonte)`.
_CALCULO_CASES: list[tuple[str, str, str, int, int, bool, float, str]] = [
    # -- known categoria, no multiplier: plain table hit -------------------------------
    ("known/at-table", "consulta", "livre_escolha", 35000, 35000, True, 1.0, "TABELA_REFERENCIA"),
    ("known/below-table", "consulta", "livre_escolha", 30000, 35000, True, 1.0, "TABELA_REFERENCIA"),
    ("known/above-table", "consulta", "livre_escolha", 50000, 35000, False, 1.0, "TABELA_REFERENCIA"),
    ("known/exame_simples", "exame_simples", "livre_escolha", 8000, 8000, True, 1.0, "TABELA_REFERENCIA"),
    ("known/opme", "opme", "livre_escolha", 300000, 300000, True, 1.0, "TABELA_REFERENCIA"),
    # case-insensitivity is pre-existing behaviour and stays a table hit
    ("known/uppercase", "CONSULTA", "livre_escolha", 30000, 35000, True, 1.0, "TABELA_REFERENCIA"),
    # -- known categoria + 1.5 multiplier: adjusted, and SAID to be adjusted -----------
    (
        "mult/urgencia",
        "consulta",
        "urgencia_emergencia",
        50000,
        52500,
        True,
        1.5,
        "TABELA_REFERENCIA_MULTIPLICADA",
    ),
    ("mult/fora_rede", "consulta", "fora_rede", 50000, 52500, True, 1.5, "TABELA_REFERENCIA_MULTIPLICADA"),
    # the uplift raises the reference value but does NOT rubber-stamp any amount
    (
        "mult/above-even-uplifted",
        "consulta",
        "urgencia_emergencia",
        52501,
        52500,
        False,
        1.5,
        "TABELA_REFERENCIA_MULTIPLICADA",
    ),
    (
        "mult/internacao",
        "internacao",
        "fora_rede",
        750000,
        750000,
        True,
        1.5,
        "TABELA_REFERENCIA_MULTIPLICADA",
    ),
    # -- unknown categoria: fail closed, no fallback to the claimant's own figure ------
    ("unknown/typo", "consuta", "livre_escolha", 999999, 0, False, 0.0, "SEM_TABELA"),
    ("unknown/absent", "quimioterapia", "livre_escolha", 500000, 0, False, 0.0, "SEM_TABELA"),
    # a padded categoria misses the table (`.lower()` only, no strip) and fails CLOSED
    ("unknown/padded", " consulta ", "livre_escolha", 30000, 0, False, 0.0, "SEM_TABELA"),
    # the multiplier never resurrects a categoria that has no table row
    ("unknown/with-multiplier", "consuta", "urgencia_emergencia", 999999, 0, False, 0.0, "SEM_TABELA"),
    # -- empty categoria: same refusal --------------------------------------------------
    ("empty/blank", "", "livre_escolha", 50000, 0, False, 0.0, "SEM_TABELA"),
    # THE ZERO TRAP: `0 <= 0` would read True on arithmetic alone. dentro_tabela is a claim
    # about the reference table, and there is no table row here, so it must be False.
    ("empty/zero-request", "", "livre_escolha", 0, 0, False, 0.0, "SEM_TABELA"),
    ("unknown/zero-request", "consuta", "urgencia_emergencia", 0, 0, False, 0.0, "SEM_TABELA"),
]


@pytest.mark.parametrize(
    ("label", "categoria", "tipo", "solicitado", "valor", "tabela", "multiplo", "fonte"),
    _CALCULO_CASES,
    ids=[case[0] for case in _CALCULO_CASES],
)
def test_calculate_value_table_driven_facts(
    label: str,
    categoria: str,
    tipo: str,
    solicitado: int,
    valor: int,
    tabela: bool,
    multiplo: float,
    fonte: str,
) -> None:
    """Every (categoria, tipo) case writes exactly the facts its computation supports."""
    result = calculate_value(
        ReembolsoInput(
            tenant_id="amh",
            protocolo_reembolso=f"REEMB-M2-{label}",
            codigo_procedimento_tuss="10101012",
            categoria_procedimento=categoria,
            tipo_reembolso=tipo,
            valor_solicitado_cents=solicitado,
        )
    )
    assert result.valor_calculado_tabela_cents == valor, label
    assert result.dentro_tabela is tabela, label
    assert result.multiplo_tabela_aplicado == multiplo, label
    assert result.fonte_tabela == fonte, label
    # money stays integer centavos end-to-end (ADR-0018): never a float, never a bool
    assert type(result.valor_calculado_tabela_cents) is int, label
    assert result.valor_solicitado_cents == solicitado, label


@pytest.mark.parametrize(
    ("label", "categoria", "tipo", "solicitado", "valor", "tabela", "multiplo", "fonte"),
    _CALCULO_CASES,
    ids=[case[0] for case in _CALCULO_CASES],
)
def test_calculate_value_audit_facts_match_the_computation(
    label: str,
    categoria: str,
    tipo: str,
    solicitado: int,
    valor: int,
    tabela: bool,
    multiplo: float,
    fonte: str,
) -> None:
    """AUDIT CHAIN (ADR-0007): the written facts must be internally consistent, not just correct.

    Asserted as INVARIANTS over whatever was computed, so they keep biting if the table values
    or the multiplier are ever re-tuned — the M-2 failure mode was precisely a fact that
    contradicted the arithmetic that produced it.
    """
    del valor, tabela, multiplo, fonte  # this test re-derives them; the case row is the input
    result = calculate_value(
        ReembolsoInput(
            tenant_id="amh",
            protocolo_reembolso=f"REEMB-M2-AUDIT-{label}",
            codigo_procedimento_tuss="10101012",
            categoria_procedimento=categoria,
            tipo_reembolso=tipo,
            valor_solicitado_cents=solicitado,
        )
    )

    if result.fonte_tabela == "SEM_TABELA":
        # A refusal claims nothing: no value, no multiplier, and NEVER a within-table verdict.
        assert result.valor_calculado_tabela_cents == 0, label
        assert result.multiplo_tabela_aplicado == 0.0, label
        assert result.dentro_tabela is False, label
        # the defect's signature: the reference value must NOT be the claimant's own figure
        assert result.valor_calculado_tabela_cents != result.valor_solicitado_cents or solicitado == 0, (
            f"{label}: SEM_TABELA echoed valor_solicitado_cents back as the reference value"
        )
    else:
        # A table hit: the recorded value must equal base * the recorded multiplier, and the
        # recorded provenance must match whether a multiplier was really applied.
        base = _BASE_VALUES_CENTS[categoria.lower()]
        assert result.valor_calculado_tabela_cents == int(base * result.multiplo_tabela_aplicado), label
        multiplicado = result.multiplo_tabela_aplicado != 1.0
        assert result.fonte_tabela == (
            "TABELA_REFERENCIA_MULTIPLICADA" if multiplicado else "TABELA_REFERENCIA"
        ), label
        # dentro_tabela is the honest comparison against that same recorded value
        assert result.dentro_tabela is (solicitado <= result.valor_calculado_tabela_cents), label

    # `fonte_tabela` is never blank and never the retired label that lied about provenance
    assert result.fonte_tabela, label
    assert result.fonte_tabela != "TUSS-REFERENCIA", label


def test_calculate_value_unknown_categoria_never_corroborates_the_claim() -> None:
    """THE M-2 REGRESSION: an unknown categoria must not make the claim self-verifying.

    Pre-fix, `base_values.get(categoria.lower(), valor_solicitado_cents)` set the reference
    value TO the requested amount, so `dentro_tabela` was True for ANY amount whatsoever — a
    R$ 1.000.000 request under a misspelled categoria was reported to the human reviewer as
    within the reference table. Swept across magnitudes to prove there is no amount at which
    the fallback survives.
    """
    for amount in (1, 5000, 999_999, 100_000_000):
        result = calculate_value(
            ReembolsoInput(
                tenant_id="amh",
                protocolo_reembolso="REEMB-M2-NOFALLBACK",
                codigo_procedimento_tuss="10101012",
                categoria_procedimento="categoria_que_nao_existe",
                tipo_reembolso="livre_escolha",
                valor_solicitado_cents=amount,
            )
        )
        assert result.dentro_tabela is False, amount
        assert result.valor_calculado_tabela_cents == 0, amount
        assert result.valor_calculado_tabela_cents != amount, amount
        assert result.fonte_tabela == "SEM_TABELA", amount


def test_calculate_value_sem_tabela_token_matches_the_dmn_catch_all() -> None:
    """The refusal token is the DMN's, not this module's invention.

    `spec/processes/dmn/reembolso_calculo.dmn` rule `r_catchall` emits
    `valor_calculado_tabela_cents=0`, `multiplo_tabela_aplicado=0.0`, `fonte_tabela="SEM_TABELA"`,
    and the contract + test-spec pin that same triple ("Catch-all (procedimento sem tabela) ->
    valor_calculado_tabela_cents = 0 + fonte_tabela = 'SEM_TABELA', o que forca dentro_tabela=false
    -> ANALISE_HUMANA"). The worker must speak the SAME token for the SAME situation, so the two
    producers of this fact cannot disagree in the audit chain.
    """
    dmn = _REPO_ROOT / "spec" / "processes" / "dmn" / "reembolso_calculo.dmn"
    catch_all = dmn.read_text(encoding="utf-8")
    assert '"SEM_TABELA"' in catch_all, "DMN catch-all token moved — the worker must follow it"

    result = calculate_value(
        ReembolsoInput(
            tenant_id="amh",
            protocolo_reembolso="REEMB-M2-TOKEN",
            categoria_procedimento="sem_row_na_tabela",
            tipo_reembolso="livre_escolha",
            valor_solicitado_cents=12345,
        )
    )
    assert result.fonte_tabela == "SEM_TABELA"
    assert result.valor_calculado_tabela_cents == 0
    assert result.multiplo_tabela_aplicado == 0.0


def test_calculate_value_known_categoria_behaviour_is_unchanged() -> None:
    """REGRESSION PIN: the valid, known-categoria path computes exactly what it did pre-fix.

    The M-2 fix must be invisible to a well-formed request. These are the pre-fix values of the
    three original tests (`test_calculate_value_consulta`, `..._dentro_tabela`, `..._fora_tabela`,
    `..._urgencia_multiplier`), asserted here together so a future change to the lookup cannot
    quietly move money on the legitimate path.
    """
    base = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-M2-REGRESSION",
        codigo_procedimento_tuss="10101012",
        categoria_procedimento="consulta",
        tipo_reembolso="livre_escolha",
    )

    # consulta reference value: R$ 350,00 — unchanged
    at_table = calculate_value(dataclasses.replace(base, valor_solicitado_cents=35000))
    assert at_table.valor_calculado_tabela_cents == 35000
    # below table -> within; above table -> not within
    assert calculate_value(dataclasses.replace(base, valor_solicitado_cents=30000)).dentro_tabela is True
    assert calculate_value(dataclasses.replace(base, valor_solicitado_cents=50000)).dentro_tabela is False
    # urgencia/emergencia still applies 1.5x to the same base: 35000 * 1.5 = 52500
    urgencia = calculate_value(
        dataclasses.replace(base, tipo_reembolso="urgencia_emergencia", valor_solicitado_cents=50000)
    )
    assert urgencia.valor_calculado_tabela_cents == 52500
    # ...and now SAYS so, where it used to report 1.0 / "TUSS-REFERENCIA"
    assert urgencia.multiplo_tabela_aplicado == 1.5
    assert urgencia.fonte_tabela == "TABELA_REFERENCIA_MULTIPLICADA"


def test_calculate_amount_entry_marshals_the_honest_facts() -> None:
    """The engine-boundary entry carries all three facts out to the process scope.

    `calculate_amount_entry` is what `ST_CalculateAmount` actually invokes; the facts only
    reach `BRT_AutoApproval` (which gates AUTO_APROVAR on `dentro_tabela`) and the human dossie
    if they survive `dataclasses.asdict`.
    """
    out = calculate_amount_entry(
        {
            "tenant_id": "amh",
            "protocolo_reembolso": "REEMB-M2-ENTRY",
            "codigo_procedimento_tuss": "10101012",
            "categoria_procedimento": "categoria_desconhecida",
            "tipo_reembolso": "urgencia_emergencia",
            "valor_solicitado_cents": 777000,
            # a seeded within-table claim must not survive either
            "dentro_tabela": True,
        }
    )
    assert out["dentro_tabela"] is False
    assert out["valor_calculado_tabela_cents"] == 0
    assert out["multiplo_tabela_aplicado"] == 0.0
    assert out["fonte_tabela"] == "SEM_TABELA"
    assert out["valor_solicitado_cents"] == 777000


# ---------------------------------------------------------------------------
# request_documents (T2.5-P2B — BPMN ST_SolicitarDocumentos, previously missing)
# ---------------------------------------------------------------------------


def test_request_documents_happy_path() -> None:
    """request_documents opens the documentation pendency to the beneficiario."""
    result = request_documents("BEN-PSEUDO-001", "REEMB-010")
    assert result["notified"] is True
    assert result["status"] == "pended"
    assert result["beneficiario_pseudo_id"] == "BEN-PSEUDO-001"
    assert result["protocolo_reembolso"] == "REEMB-010"
    assert result["message_type"] == "pendencia_documentacao"


def test_request_documents_never_carries_a_decision() -> None:
    """Invariant (L0): request_documents never fabricates or carries an adverse decision.

    The BPMN's own ST_SolicitarDocumentos documentation: expiry of the pendency NEVER
    auto-denies — a human decides in UT_DecidirPendenciaExpirada.
    """
    result = request_documents("", "")
    assert "decisao_reembolso" not in result
    assert "decisao_pendencia" not in result
    assert set(result.keys()) == {
        "notified",
        "beneficiario_pseudo_id",
        "protocolo_reembolso",
        "status",
        "message_type",
    }


def test_request_documents_entry_round_trips_default_pendencia() -> None:
    """Default message_type is 'pendencia_documentacao' (the pendency-open semantics of
    ST_SolicitarDocumentos; mirrors recurso's request_documents idiom)."""
    variables = {"beneficiario_pseudo_id": "B-1", "protocolo_reembolso": "R-1"}
    assert request_documents_entry(variables) == request_documents("B-1", "R-1", "pendencia_documentacao")


def test_request_documents_entry_missing_inputs_fail_safe() -> None:
    """Missing inputs degrade to empty identifiers — never an exception, never a decision."""
    result = request_documents_entry({})
    assert result["notified"] is True
    assert result["beneficiario_pseudo_id"] == ""
    assert result["protocolo_reembolso"] == ""


def test_request_documents_entry_ignores_kafka_seam() -> None:
    """Entry accepts the kafka seam (donor contract) but never publishes (documented gap)."""
    kafka = FakeKafkaPublisher()
    result = request_documents_entry({"protocolo_reembolso": "R-2"}, kafka=kafka)
    assert result["notified"] is True
    assert kafka.published == []


# ---------------------------------------------------------------------------
# analyze_request (T2.5-P2B — BPMN ST_PrepararDossie, previously missing)
# ---------------------------------------------------------------------------


def test_analyze_request_never_decides() -> None:
    """analyze_request assembles the dossie; it NEVER substitutes the human decision (ADR-0005)."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-011",
        tipo_reembolso="livre_escolha",
        categoria_procedimento="consulta",
        codigo_procedimento_tuss="10101012",
        valor_solicitado_cents=35000,
    )
    result = analyze_request(inp)
    assert result["dossie"] == "dossie_instruido"
    assert result["recomendacao_sugerida"] == "ANALISE_HUMANA"
    assert result["protocolo_reembolso"] == "REEMB-011"
    assert "decisao_reembolso" not in result


def test_analyze_request_carries_calculo_evidence_opportunistically() -> None:
    """dentro_tabela/requer_avaliacao_clinica ride as evidence when present — facts, not verdicts."""
    inp = ReembolsoInput(
        protocolo_reembolso="REEMB-012",
        dentro_tabela=True,
        requer_avaliacao_clinica=True,
        valor_solicitado_cents=99000,
    )
    result = analyze_request(inp)
    assert result["dentro_tabela"] is True
    assert result["requer_avaliacao_clinica"] is True
    assert result["valor_solicitado_cents"] == 99000
    assert result["recomendacao_sugerida"] == "ANALISE_HUMANA"


def test_analyze_request_missing_inputs_fail_safe() -> None:
    """Empty input still yields a human-routing dossie — never an exception, never a verdict."""
    result = analyze_request(ReembolsoInput())
    assert result["recomendacao_sugerida"] == "ANALISE_HUMANA"
    assert "decisao_reembolso" not in result


def test_analyze_request_entry_round_trips() -> None:
    variables = {
        "protocolo_reembolso": "REEMB-013",
        "tipo_reembolso": "urgencia_emergencia",
        "valor_solicitado_cents": 12000,
    }
    assert analyze_request_entry(variables) == analyze_request(ReembolsoInput(**variables))


def test_analyze_request_entry_ignores_kafka_seam() -> None:
    kafka = FakeKafkaPublisher()
    result = analyze_request_entry({"protocolo_reembolso": "REEMB-014"}, kafka=kafka)
    assert result["recomendacao_sugerida"] == "ANALISE_HUMANA"
    assert kafka.published == []


# ---------------------------------------------------------------------------
# notify_sla_risk (T2.5-P2B — BPMN ST_NotificarRiscoSla, previously missing;
# mirrors cancel.notify_sla_risk's proven pattern)
# ---------------------------------------------------------------------------


def test_notify_sla_risk_happy_path() -> None:
    """notify_sla_risk is informational-only: no guard, no decision, UT stays open."""
    inp = ReembolsoInput(protocolo_reembolso="REEMB-SLA-001", tipo_reembolso="livre_escolha")
    result = notify_sla_risk(inp)
    assert result["sla_risk_notified"] is True
    assert result["protocolo_reembolso"] == "REEMB-SLA-001"


def test_notify_sla_risk_never_alters_a_decision() -> None:
    """Invariant: notify_sla_risk's output never carries a decision/adverse marker."""
    result = notify_sla_risk(ReembolsoInput(protocolo_reembolso="REEMB-SLA-002"))
    assert "decisao_reembolso" not in result
    assert set(result.keys()) == {"sla_risk_notified", "protocolo_reembolso"}


def test_notify_sla_risk_missing_inputs_fail_safe() -> None:
    """Empty input still notifies (fail-safe, non-adverse) — never an exception."""
    result = notify_sla_risk(ReembolsoInput())
    assert result["sla_risk_notified"] is True
    assert result["protocolo_reembolso"] == ""


def test_notify_sla_risk_entry_round_trips() -> None:
    variables = {"protocolo_reembolso": "REEMB-SLA-003", "tipo_reembolso": "urgencia_emergencia"}
    input_data = ReembolsoInput(
        **{k: v for k, v in variables.items() if k in ReembolsoInput.__dataclass_fields__}
    )
    assert notify_sla_risk_entry(variables) == notify_sla_risk(input_data)


def test_notify_sla_risk_entry_ignores_kafka_seam() -> None:
    kafka = FakeKafkaPublisher()
    result = notify_sla_risk_entry({"protocolo_reembolso": "REEMB-SLA-004"}, kafka=kafka)
    assert result["sla_risk_notified"] is True
    assert kafka.published == []


# ---------------------------------------------------------------------------
# register_reembolso_workers — registry/drift coverage (T2.5-P2B reconciliation)
# ---------------------------------------------------------------------------

# The 8 `operadora.reembolso.*` topics SP-OP-REEMBOLSO-001 declares as camunda:topic (grep
# spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn), excl. the shared
# `operadora.events.publish` (registered separately by events.register_events_workers).
_BPMN_REEMBOLSO_TOPICS = frozenset(
    {
        "operadora.reembolso.check_coverage",
        "operadora.reembolso.check_prazo",
        "operadora.reembolso.calculate_amount",
        "operadora.reembolso.request_documents",
        "operadora.reembolso.analyze_request",
        "operadora.reembolso.notify_sla_risk",
        "operadora.reembolso.issue_payment",
        "operadora.reembolso.send_reembolso_denial",
    }
)

# Function-derived topic KEPT by documented registry-completeness convention (shared with
# recurso.py; the generic events.publish task serves the BPMN's ST_Publish* nodes).
_CONVENTION_TOPICS = frozenset({"operadora.reembolso.publish_completed"})


def test_register_reembolso_workers_matches_bpmn_topics_plus_convention() -> None:
    """Registry coverage (ADR-0026 test strategy): the registered `operadora.reembolso.*` set is
    EXACTLY the 8 BPMN-declared topics + the documented publish_completed convention — no gap
    (previously-missing request_documents/analyze_request/notify_sla_risk now registered) and no
    orphan (auto_approve_or_route/notify_beneficiario deleted: BRT_AutoApproval is a NATIVE
    businessRuleTask with camunda:decisionRef=reembolso_auto_approval, and no BPMN topic ever
    referenced notify_beneficiario)."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_reembolso_workers(harness, FakeKafkaPublisher())
    reembolso_topics = {t for t in harness.registered_topics if t.startswith("operadora.reembolso.")}
    assert reembolso_topics == _BPMN_REEMBOLSO_TOPICS | _CONVENTION_TOPICS


def test_register_reembolso_workers_orphans_absent() -> None:
    """The two T2.5-P2B-deleted orphan registrations must never come back."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_reembolso_workers(harness, FakeKafkaPublisher())
    assert "operadora.reembolso.auto_approve_or_route" not in harness.registered_topics
    assert "operadora.reembolso.notify_beneficiario" not in harness.registered_topics


# ---------------------------------------------------------------------------
# process_payment
# ---------------------------------------------------------------------------


def test_process_payment() -> None:
    """process_payment issues payment and returns comprovante."""
    result = process_payment("REEMB-009", 35000, "BEN-PSEUDO-002")
    assert result["payment_issued"] is True
    assert result["comprovante_pagamento_ref"].startswith("PAY-")
    assert result["valor_cents"] == 35000


def test_process_payment_echoes_contract_output_variable() -> None:
    """The paid amount is written back under its CONTRACT name (SP-OP-REEMBOLSO-001
    §"Variaveis de saida": `valor_reembolso_aprovado_cents`), so the value that actually moved
    is observable in engine history/`reembolso.completed` — not only under the internal
    `valor_cents` key."""
    result = process_payment("REEMB-009", 8000, "BEN-PSEUDO-002")
    assert result["valor_reembolso_aprovado_cents"] == 8000
    assert result["valor_cents"] == 8000


# --- process_payment: fail-closed money guard (ADR-0018 integer-centavos) -------------------


def test_process_payment_refuses_missing_amount() -> None:
    """No amount => NO payment. Fail-closed: never a fabricated R$0,00 comprovante."""
    with pytest.raises(ReembolsoValorPagamentoInvalidoError) as exc:
        process_payment("REEMB-009", None, "BEN-PSEUDO-002")  # type: ignore[arg-type]
    assert str(exc.value).startswith("ERR_REEMBOLSO_VALOR_PAGAMENTO_INVALIDO")


def test_process_payment_refuses_zero_amount() -> None:
    """`0` is the exact shape of the pre-fix defect (`variables.get("valor_cents", 0)`): a
    payment issued for money that never moved. Refused."""
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        process_payment("REEMB-009", 0, "BEN-PSEUDO-002")


def test_process_payment_refuses_negative_amount() -> None:
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        process_payment("REEMB-009", -8000, "BEN-PSEUDO-002")


@pytest.mark.parametrize("valor", [8000.0, 80.5])
def test_process_payment_refuses_float_amount(valor: float) -> None:
    """Money is integer centavos ONLY (ADR-0018) — a float NEVER round-trips to a payment,
    not even an integral one (8000.0). No rounding, no coercion: refuse."""
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        process_payment("REEMB-009", valor, "BEN-PSEUDO-002")  # type: ignore[arg-type]


def test_process_payment_refuses_bool_amount() -> None:
    """`True` is an `int` in Python — the guard must reject it explicitly."""
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        process_payment("REEMB-009", True, "BEN-PSEUDO-002")  # type: ignore[arg-type]


def test_process_payment_refuses_string_amount() -> None:
    """A String-typed money variable is itself an ADR-0018 typing defect (`_to_camunda_var`
    types every int as Integer/Long) — refuse, never parse."""
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        process_payment("REEMB-009", "8000", "BEN-PSEUDO-002")  # type: ignore[arg-type]


def test_valor_pagamento_invalido_is_value_error() -> None:
    """`ValueError` family => harness reports `failure(retries=0)` => engine incident, never a
    silent retry and never a bpmnError (`ST_IssuePayment*` has NO error boundary event)."""
    assert issubclass(ReembolsoValorPagamentoInvalidoError, ValueError)


# ---------------------------------------------------------------------------
# send_reembolso_denial — GUARD tests
# ---------------------------------------------------------------------------


def test_send_denial_guard_not_adverse() -> None:
    """send_reembolso_denial raises if decisao not in {NEGAR, APROVAR_PARCIAL}."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="APROVAR",
        justificativa="test",
        fundamentacao_contratual="clausula",
        analista_id="analista-1",
    )
    with pytest.raises(ReembolsoDenialNotHumanError) as exc:
        send_reembolso_denial(inp)
    assert "decisao_reembolso" in str(exc.value)


def test_send_denial_guard_missing_justificativa() -> None:
    """send_reembolso_denial raises if justificativa is empty."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="NEGAR",
        justificativa="",
        fundamentacao_contratual="clausula",
        analista_id="analista-1",
    )
    with pytest.raises(ReembolsoDenialNotHumanError) as exc:
        send_reembolso_denial(inp)
    assert "justificativa" in str(exc.value)


def test_send_denial_guard_missing_fundamentacao() -> None:
    """send_reembolso_denial raises if fundamentacao_contratual is empty."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="NEGAR",
        justificativa="Motivo",
        fundamentacao_contratual="",
        analista_id="analista-1",
    )
    with pytest.raises(ReembolsoDenialNotHumanError) as exc:
        send_reembolso_denial(inp)
    assert "fundamentacao_contratual" in str(exc.value)


def test_send_denial_guard_missing_human() -> None:
    """send_reembolso_denial raises if no human identifier."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="NEGAR",
        justificativa="Motivo",
        fundamentacao_contratual="clausula",
        analista_id="",
        auditor_id="",
    )
    with pytest.raises(ReembolsoDenialNotHumanError) as exc:
        send_reembolso_denial(inp)
    assert "analista_id" in str(exc.value) or "auditor_id" in str(exc.value)


def test_send_denial_parcial_guard_invalid_reduction() -> None:
    """APROVAR_PARCIAL with approved >= solicited raises."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="APROVAR_PARCIAL",
        justificativa="Reducao indevida",
        fundamentacao_contratual="clausula",
        valor_reembolso_aprovado_cents=10000,
        valor_solicitado_cents=5000,  # Approved > solicited
        analista_id="analista-1",
    )
    with pytest.raises(ReembolsoDenialNotHumanError) as exc:
        send_reembolso_denial(inp)
    assert "reducao" in str(exc.value).lower() or "aprovado" in str(exc.value).lower()


def test_send_denial_success_negar() -> None:
    """send_reembolso_denial succeeds for NEGAR with all fields."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="NEGAR",
        justificativa="Procedimento nao coberto pela segmentacao",
        fundamentacao_contratual="Clausula 5.2",
        valor_solicitado_cents=50000,
        analista_id="analista-789",
    )
    result = send_reembolso_denial(inp)
    assert result["denial_sent"] is True
    assert result["decisao_reembolso"] == "NEGAR"


def test_send_denial_success_aprovado_parcial() -> None:
    """send_reembolso_denial succeeds for APROVAR_PARCIAL with auditor."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="APROVAR_PARCIAL",
        justificativa="Valor reduzido conforme tabela",
        fundamentacao_contratual="Clausula 8.1",
        valor_reembolso_aprovado_cents=30000,
        valor_solicitado_cents=50000,
        auditor_id="auditor-001",
        parecer_auditor="Parecer tecnico favoravel a reducao",
    )
    result = send_reembolso_denial(inp)
    assert result["denial_sent"] is True


# ---------------------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------------------


def test_publish_completed_reembolso() -> None:
    """publish_completed emits domain event."""
    result = publish_completed(desfecho="aprovado_automatico")
    assert result["published"] is True
    assert result["payload"]["desfecho"] == "aprovado_automatico"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_reembolso_denial_not_human_is_permission_error() -> None:
    """ReembolsoDenialNotHumanError must be a subclass of PermissionError."""
    assert issubclass(ReembolsoDenialNotHumanError, PermissionError)


def test_reembolso_protocolo_invalido_is_value_error() -> None:
    """ReembolsoProtocoloInvalidoError must be a subclass of ValueError."""
    assert issubclass(ReembolsoProtocoloInvalidoError, ValueError)


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — round-trip vs calling the
# typed function directly; fail-closed marshalling on invalid/missing input.
# T1.9: calculate_amount_entry only marshals into `calculate_value` — it never
# re-derives `dentro_teto_l2` (see calculate_amount_entry's docstring).
# ---------------------------------------------------------------------------


def test_check_coverage_entry_round_trips_check_coverage() -> None:
    variables = {"codigo_procedimento_tuss": "10101012", "cobertura_prevista": True}
    assert check_coverage_entry(variables) == check_coverage(ReembolsoInput(**variables))


def test_check_prazo_entry_raises_on_missing_protocolo() -> None:
    """Fail-closed (unchanged guard): validate_reembolso raises ReembolsoProtocoloInvalidoError
    when protocolo_reembolso is blank."""
    with pytest.raises(ReembolsoProtocoloInvalidoError):
        check_prazo_entry({"protocolo_reembolso": ""})


def test_check_prazo_entry_happy_path_round_trips_validate_reembolso() -> None:
    variables = {
        "protocolo_reembolso": "REEMB-1",
        "codigo_procedimento_tuss": "10101012",
        "valor_solicitado_cents": 35000,
        "dentro_prazo": True,
    }
    direct = validate_reembolso(ReembolsoInput(**variables))
    result = check_prazo_entry(variables)
    assert result["valid"] == direct.valid
    assert result["dentro_prazo"] == direct.dentro_prazo


def test_calculate_amount_entry_round_trips_calculate_value() -> None:
    variables = {
        "codigo_procedimento_tuss": "10101012",
        "categoria_procedimento": "consulta",
        "tipo_reembolso": "eletivo",
        "valor_solicitado_cents": 30000,
        "dentro_teto_l2": True,
    }
    direct = calculate_value(ReembolsoInput(**variables))
    result = calculate_amount_entry(variables)
    assert result["dentro_teto_l2"] == direct.dentro_teto_l2
    assert result["valor_calculado_tabela_cents"] == direct.valor_calculado_tabela_cents


def test_issue_payment_entry_round_trips_process_payment() -> None:
    variables = {
        "protocolo_reembolso": "REEMB-1",
        "valor_reembolso_aprovado_cents": 30000,
        "beneficiario_pseudo_id": "B-1",
    }
    direct = process_payment("REEMB-1", 30000, "B-1")
    result = issue_payment_entry(variables)
    assert result["payment_issued"] == direct["payment_issued"]
    assert result["valor_cents"] == direct["valor_cents"]


# --- issue_payment_entry: the amount each of the THREE payment paths must pay ---------------
# One topic (`operadora.reembolso.issue_payment`) serves three BPMN service tasks. Contract
# SP-OP-REEMBOLSO-001 §"Variaveis de saida": `valor_reembolso_aprovado_cents` = "Valor
# efetivamente aprovado (humano em APROVAR/APROVAR_PARCIAL; = valor_calculado_tabela_cents no
# caminho automatico)" — ONE variable, three producers.


def test_issue_payment_entry_auto_path_pays_calculated_amount() -> None:
    """ST_IssuePaymentAuto (AUTO_APROVAR, L2 integral): the BPMN inputParameter
    `valor_reembolso_aprovado_cents = ${calculo.valor_calculado_tabela_cents}` has already
    resolved when the task is fetched, so the worker sees the calculated amount."""
    variables = {
        "protocolo_reembolso": "REEMB-AUTO",
        "beneficiario_pseudo_id": "B-1",
        # As delivered by the BPMN input mapping on ST_IssuePaymentAuto.
        "valor_reembolso_aprovado_cents": 12000,
        "valor_solicitado_cents": 12000,
    }
    result = issue_payment_entry(variables)
    assert result["payment_issued"] is True
    assert result["valor_cents"] == 12000
    assert result["valor_reembolso_aprovado_cents"] == 12000


def test_issue_payment_entry_analista_path_pays_human_approved_amount() -> None:
    """ST_IssuePaymentAnalista (human APROVAR): pays the value the human set in the User Task."""
    variables = {
        "protocolo_reembolso": "REEMB-ANALISTA",
        "beneficiario_pseudo_id": "B-1",
        "decisao_reembolso": "APROVAR",
        "valor_reembolso_aprovado_cents": 12000,
        "analista_id": "analista-sintetico-001",
    }
    result = issue_payment_entry(variables)
    assert result["valor_cents"] == 12000


def test_issue_payment_entry_parcial_path_pays_reduced_human_amount() -> None:
    """ST_IssuePaymentParcial (human APROVAR_PARCIAL): pays the REDUCED value the human set —
    8000 of a 12000 request (the exact shape the live parcial suite asserts). Pre-fix this path
    issued 0."""
    variables = {
        "protocolo_reembolso": "REEMB-PARCIAL",
        "beneficiario_pseudo_id": "B-1",
        "decisao_reembolso": "APROVAR_PARCIAL",
        "valor_solicitado_cents": 12000,
        "valor_reembolso_aprovado_cents": 8000,
        "analista_id": "analista-sintetico-001",
    }
    result = issue_payment_entry(variables)
    assert result["valor_cents"] == 8000
    assert result["valor_reembolso_aprovado_cents"] == 8000


def test_issue_payment_entry_approved_overrides_calculated() -> None:
    """PRECEDENCE: the human-approved value wins over the table-calculated one. A parcial
    reduction (8000) must NEVER be paid at the calculated 12000."""
    variables = {
        "protocolo_reembolso": "REEMB-PARCIAL",
        "beneficiario_pseudo_id": "B-1",
        "valor_calculado_tabela_cents": 12000,
        "valor_reembolso_aprovado_cents": 8000,
    }
    assert issue_payment_entry(variables)["valor_cents"] == 8000


def test_issue_payment_entry_never_falls_back_to_calculated_amount() -> None:
    """NO fallback to `valor_calculado_tabela_cents`: paying a machine-calculated amount no
    human approved would originate a reduction/overpayment in the worker — the L0-hard
    violation this process exists to prevent (contract §"Invariante L0 hard")."""
    variables = {
        "protocolo_reembolso": "REEMB-ANALISTA",
        "beneficiario_pseudo_id": "B-1",
        "decisao_reembolso": "APROVAR",
        "valor_calculado_tabela_cents": 12000,
    }
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        issue_payment_entry(variables)


def test_issue_payment_entry_refuses_orphan_valor_cents_variable() -> None:
    """Regression fence for the fixed defect: `valor_cents` is produced by NOTHING in this
    process (not by calculate_amount, not by any BPMN mapping, not by the start seeds). It must
    never be the amount source again."""
    variables = {
        "protocolo_reembolso": "REEMB-1",
        "beneficiario_pseudo_id": "B-1",
        "valor_cents": 30000,
    }
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        issue_payment_entry(variables)


def test_issue_payment_entry_refuses_when_amount_absent() -> None:
    """Nothing resolvable => incident, never a R$0,00 payment (the pre-fix behaviour)."""
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        issue_payment_entry({"protocolo_reembolso": "REEMB-1", "beneficiario_pseudo_id": "B-1"})


def test_send_reembolso_denial_entry_guards_missing_human_decision() -> None:
    """send_reembolso_denial_entry raises the UNCHANGED ReembolsoDenialNotHumanError guard."""
    with pytest.raises(ReembolsoDenialNotHumanError):
        send_reembolso_denial_entry({"decisao_reembolso": ""})


def test_send_reembolso_denial_entry_happy_path() -> None:
    variables = {
        "decisao_reembolso": "NEGAR",
        "justificativa": "fora de cobertura",
        "fundamentacao_contratual": "clausula 5",
        "analista_id": "analista-1",
    }
    direct = send_reembolso_denial(ReembolsoDenialInput(**variables))
    assert send_reembolso_denial_entry(variables) == direct


def test_publish_completed_entry_round_trips_publish_completed() -> None:
    variables = {"event_type": "reembolso.completed", "desfecho": "aprovado_automatico"}
    assert publish_completed_entry(variables) == publish_completed(
        event_type="reembolso.completed", payload={}, desfecho="aprovado_automatico"
    )
