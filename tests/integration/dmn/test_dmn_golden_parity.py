"""Golden-parity suite for T1.5 (ADR-0028) — engine-side DMN evaluation cutover.

Runs the REAL `CibSevenDmnTransport` against a REAL CIB Seven engine (`docker compose
--profile core up`, ADR-0011 — never a mock in this file) for every decision key migrated off a
Python re-implementation. This is the "keep the corpus as an engine-only regression test"
artifact ADR-0028 §7 mandates: a repeatable, engine-marked test proving each deployed table
evaluates exactly as the golden-parity run (performed before cutover, see the T1.5 PR body /
evidence ledger) found — and a guard against future silent drift in `spec/processes/dmn/*.dmn`.

Every expected value below was captured from a REAL run against the compose engine BEFORE the
corresponding Python re-implementation was deleted (T1.5) — never fabricated (constraint 3).
Where the deployed table's answer differs from what the OLD (now-deleted) Python would have
produced, the docstring/comment says so explicitly (`DIVERGENCE:` — DMN wins, per ADR-0028 §7;
the DMN content itself is never patched, per constraint 5).

If the engine is unreachable, every test in this module SKIPS via the session-scoped
`_skip_if_engine_unreachable` autouse fixture (`tests/integration/conftest.py`) with an explicit,
loud reason — never a silent pass, never a fabricated result (constraint 3).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from maezo.tools.workers.credenciamento import assess_admissibility as cred_assess_admissibility
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport, DmnVersion
from maezo.tools.workers.pagto import route_aprovacao

pytestmark = pytest.mark.integration


@pytest.fixture
async def dmn(engine_base_url: str) -> AsyncIterator[CibSevenDmnTransport]:
    # timeout=30.0 (not the 10s default): the compose engine can be slow for a few requests
    # right after this package's session-start full-tree deploy (77 artifacts) and while the
    # sibling spine tests hold long-poll fetchAndLock connections — observed transient
    # ReadTimeouts at 10s under full-suite load. Production's daemon constructs the transport
    # with `client_timeout_s` (40s, worker_runtime/service.py) for the same reason.
    transport = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    try:
        yield transport
    finally:
        await transport.close()


async def _eval(dmn_transport: CibSevenDmnTransport, key: str, variables: dict[str, Any]) -> dict[str, Any]:
    rows, version = await dmn_transport.evaluate(key, variables)
    assert isinstance(version, DmnVersion)
    assert version.key == key
    assert rows, f"empty result for `{key}` with {variables!r} — ADR-0028 §3 requires a catch-all row"
    return rows[0]


# ---------------------------------------------------------------------------
# ans_retry_policy — ans_submit.retry_submission (clean cutover, 0 divergence)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("retry_attempt", "expected_backoff", "expected_continue"),
    [
        (1, "PT5M", True),
        (2, "PT30M", True),
        (3, "PT2H", True),
        (4, "", False),  # catch-all: retry exhausted
    ],
)
async def test_ans_retry_policy_parity(
    dmn: CibSevenDmnTransport, retry_attempt: int, expected_backoff: str, expected_continue: bool
) -> None:
    row = await _eval(dmn, "ans_retry_policy", {"retry_attempt": retry_attempt})
    assert row["backoff"] == expected_backoff
    assert row["continue_retry"] == expected_continue


# ---------------------------------------------------------------------------
# pagto_admissibility — pagto.assess_admissibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dados_validos", "lastro", "duplicidade", "expected_roteamento"),
    [
        (True, True, False, "SEGUE_ROTEAMENTO"),
        (True, True, True, "ANALISE_HUMANA"),
        (False, True, False, "PENDENTE_DADOS"),
        (True, False, False, "ANALISE_HUMANA"),
        # DIVERGENCE: old Python checked dados_validos FIRST -> would say PENDENTE_DADOS.
        # DMN checks duplicidade_suspeita FIRST (FIRST hit policy) -> ANALISE_HUMANA.
        (False, True, True, "ANALISE_HUMANA"),
    ],
)
async def test_pagto_admissibility_parity(
    dmn: CibSevenDmnTransport,
    dados_validos: bool,
    lastro: bool,
    duplicidade: bool,
    expected_roteamento: str,
) -> None:
    row = await _eval(
        dmn,
        "pagto_admissibility",
        {
            "dados_pagamento_validos": dados_validos,
            "lastro_confirmado": lastro,
            "duplicidade_suspeita": duplicidade,
        },
    )
    assert row["roteamento"] == expected_roteamento


# ---------------------------------------------------------------------------
# pagto_alcada — pagto.route_aprovacao (the ADR's flagship forcing-argument table)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor_cents", "dentro_teto", "expected_faixa", "expected_grupo"),
    [
        (5_000_000, True, "DENTRO_TETO_L2", "clerical-pagamentos"),
        (25_000_000, False, "ALCADA_L1", "aprovacao-financeira-l1"),
        (100_000_000, False, "ALCADA_L2", "aprovacao-financeira-l2"),
        (500_000_000, False, "ALCADA_L3", "aprovacao-financeira-l3"),
        (2_000_000_000, False, "ANALISE_HUMANA", "comite-financeiro"),
        # DIVERGENCE: old Python trusted dentro_teto_l2 ALONE. The deployed table's
        # DENTRO_TETO_L2 row ALSO requires valor_pagamento_cents <= 10_000_000 (hardcoded
        # ~R$100k) — a value above that gate falls through to ALCADA_L1 even with
        # dentro_teto_l2=True (e.g. a future tenant ceiling override above R$100k).
        (15_000_000, True, "ALCADA_L1", "aprovacao-financeira-l1"),
    ],
)
async def test_pagto_alcada_parity(
    dmn: CibSevenDmnTransport,
    valor_cents: int,
    dentro_teto: bool,
    expected_faixa: str,
    expected_grupo: str,
) -> None:
    row = await _eval(
        dmn,
        "pagto_alcada",
        {"valor_pagamento_cents": valor_cents, "dentro_teto_l2": dentro_teto, "tipo_pagamento": "pix"},
    )
    assert row["faixa_valor"] == expected_faixa
    assert row["grupo_aprovador"] == expected_grupo


async def test_pagto_alcada_long_typing_above_int32(dmn: CibSevenDmnTransport) -> None:
    """Load-bearing (ADR-0028 §2/ADR-0018): a payment above int32-max cents MUST be typed
    `Long`, or the engine rejects the evaluate call. R$50MM = 5_000_000_000 cents."""
    row = await _eval(
        dmn,
        "pagto_alcada",
        {"valor_pagamento_cents": 5_000_000_000, "dentro_teto_l2": False, "tipo_pagamento": ""},
    )
    assert row["faixa_valor"] == "ANALISE_HUMANA"  # catch-all — above every tier


async def test_route_aprovacao_end_to_end_against_live_engine(dmn: CibSevenDmnTransport) -> None:
    """End-to-end proof: the MIGRATED worker function itself (not just raw evaluate()) against
    the real engine — resolver stub simulates the T1.9 ceiling fact (untouched by T1.5).

    `route_aprovacao` is sync-by-design (`evaluate_sync` bridges via `asyncio.run`, T1.1 design
    §7) — in production the harness always dispatches it via `asyncio.to_thread` (never
    directly on the event loop, `FunctionWorker`/`WorkerHarness.register_worker`), so this test
    does the same rather than calling it inline from this `async def` test (which would hit
    "asyncio.run() cannot be called from a running event loop" — the same fail-closed
    protection that keeps this bridge from ever double-nesting event loops in production).
    """

    class _RealCeilingLikeResolver:
        def within_l2_ceiling(self, **_kwargs: Any) -> bool:
            return True

    result = await asyncio.to_thread(
        route_aprovacao,
        {"valor_pagamento_cents": 5_000_000},
        _RealCeilingLikeResolver(),  # type: ignore[arg-type]
        dmn=dmn,
    )
    assert result["faixa_valor"] == "DENTRO_TETO_L2"
    assert result["tier_minimo"] == 0


# ---------------------------------------------------------------------------
# inadimplencia_status — inadimplencia.assess_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tipo_plano", "dentro_min", "notificacao", "dentro_purga", "expected_roteamento"),
    [
        ("coletivo_empresarial", True, True, False, "ANALISE_HUMANA"),
        ("individual", True, False, False, "PENDENTE_NOTIFICACAO"),
        ("individual", True, True, True, "AGUARDA_PURGA"),
        ("individual", True, True, False, "SEGUE_ANALISE"),
        ("individual", False, True, False, "ANALISE_HUMANA"),
        # DIVERGENCE: old Python checked notificacao_previa_feita BEFORE dentro_janela_purga ->
        # would say PENDENTE_NOTIFICACAO. DMN checks dentro_janela_purga FIRST -> AGUARDA_PURGA.
        ("individual", True, False, True, "AGUARDA_PURGA"),
    ],
)
async def test_inadimplencia_status_parity(
    dmn: CibSevenDmnTransport,
    tipo_plano: str,
    dentro_min: bool,
    notificacao: bool,
    dentro_purga: bool,
    expected_roteamento: str,
) -> None:
    row = await _eval(
        dmn,
        "inadimplencia_status",
        {
            "meses_inadimplencia": 3,
            "dentro_periodo_minimo": dentro_min,
            "notificacao_previa_feita": notificacao,
            "dentro_janela_purga": dentro_purga,
            "tipo_plano": tipo_plano,
        },
    )
    assert row["roteamento"] == expected_roteamento


# ---------------------------------------------------------------------------
# glosa_reason_normalization — contas.analyze_reason
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason_code", "expected_categoria"),
    [
        ("GUIA_INCOMPLETA", "administrativa"),
        ("PROCEDIMENTO_NAO_INDICADO", "tecnica"),
        ("VALOR_ACIMA_TABELA", "valor"),
        ("SEM_AUTORIZACAO", "documental"),
        # DIVERGENCE: old Python's substring-match dict had NO entry for CARENCIA (fell
        # through to "desconhecida"). DMN maps it to "clinica".
        ("CARENCIA", "clinica"),
        ("XYZ-999", "desconhecida"),
    ],
)
async def test_glosa_reason_normalization_parity(
    dmn: CibSevenDmnTransport, reason_code: str, expected_categoria: str
) -> None:
    row = await _eval(dmn, "glosa_reason_normalization", {"reason_code_tiss": reason_code})
    assert row["categoria_normalizada"] == expected_categoria


# ---------------------------------------------------------------------------
# glosa_triage — contas.prepare_triage_dossier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tipo_item", "categoria", "item_conforme", "divergencia_valor", "documentacao_anexa", "expected"),
    [
        ("consulta", "administrativa", True, False, True, "SEM_GLOSA"),
        # DIVERGENCE (MAJOR): old Python said "no glosas -> SEM_GLOSA" checking only
        # has_glosas/divergencia_valor. The DMN ALSO requires item_conforme_tabela=true AND
        # documentacao_anexa=true for SEM_GLOSA — with both False (old GlosaInput defaults)
        # it now correctly escalates instead.
        ("", "administrativa", False, False, False, "ANALISE_HUMANA"),
        ("consulta", "tecnica", True, False, True, "ANALISE_HUMANA"),
        ("consulta", "documental", True, False, False, "ANALISE_HUMANA"),
        # DIVERGENCE (MAJOR): RECORRER was structurally unreachable dead code in the old
        # Python (its own docstring: "For now, always route to ANALISE_HUMANA"). Reachable now.
        ("consulta", "valor", True, True, True, "RECORRER"),
        ("consulta", "clinica", False, True, False, "ANALISE_HUMANA"),
    ],
)
async def test_glosa_triage_parity(
    dmn: CibSevenDmnTransport,
    tipo_item: str,
    categoria: str,
    item_conforme: bool,
    divergencia_valor: bool,
    documentacao_anexa: bool,
    expected: str,
) -> None:
    row = await _eval(
        dmn,
        "glosa_triage",
        {
            "tipo_item": tipo_item,
            "categoria_normalizada": categoria,
            "item_conforme_tabela": item_conforme,
            "divergencia_valor": divergencia_valor,
            "documentacao_anexa": documentacao_anexa,
        },
    )
    assert row["roteamento"] == expected


# ---------------------------------------------------------------------------
# recurso_admissibility + recurso_eligibility — recurso.assess_eligibility (2-DMN chain)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("glosa_existe", "dentro_prazo", "doc_completa", "expected_roteamento"),
    [
        (False, True, True, "ANALISE_HUMANA"),
        (True, False, True, "ANALISE_HUMANA"),
        (True, True, False, "PENDENTE_DOCUMENTACAO"),
        (True, True, True, "SEGUE_ANALISE"),
    ],
)
async def test_recurso_admissibility_parity(
    dmn: CibSevenDmnTransport,
    glosa_existe: bool,
    dentro_prazo: bool,
    doc_completa: bool,
    expected_roteamento: str,
) -> None:
    row = await _eval(
        dmn,
        "recurso_admissibility",
        {
            "glosa_existe": glosa_existe,
            "dentro_prazo_recurso": dentro_prazo,
            "documentacao_recurso_completa": doc_completa,
        },
    )
    assert row["roteamento"] == expected_roteamento


@pytest.mark.parametrize(
    ("glosa_type", "expected_roteamento", "expected_grupo"),
    [
        ("clinica", "RECORRIVEL", "medico-auditor"),
        ("administrativa", "RECORRIVEL", "analista-recurso-glosa"),
        # DIVERGENCE: old Python's `recorivel` was a tautology (roteamento == "SEGUE_ANALISE"),
        # never consulting this table's real RECORRIVEL/ANALISE_HUMANA output.
        ("outra", "ANALISE_HUMANA", "analista-recurso-glosa"),
    ],
)
async def test_recurso_eligibility_parity(
    dmn: CibSevenDmnTransport, glosa_type: str, expected_roteamento: str, expected_grupo: str
) -> None:
    row = await _eval(
        dmn,
        "recurso_eligibility",
        {"glosa_type": glosa_type, "glosa_reason_code": "", "valor_glosado_brl": 500.0},
    )
    assert row["roteamento"] == expected_roteamento
    assert row["grupo_revisor"] == expected_grupo


# ---------------------------------------------------------------------------
# cred_admissibility + cred_route — credenciamento.assess_admissibility (2-DMN chain)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("direcao", "doc", "licenca", "criterios", "indicio", "expected_roteamento"),
    [
        ("credenciamento", False, True, True, False, "PENDENTE_DOCUMENTACAO"),
        ("credenciamento", True, False, True, False, "ANALISE_HUMANA"),
        ("descredenciamento", True, True, True, False, "SEGUE_ANALISE"),
        ("credenciamento", True, True, True, True, "SEGUE_ANALISE"),
        ("invalida", True, True, True, False, "ANALISE_HUMANA"),
        ("credenciamento", True, True, True, False, "CLERICAL_CREDENCIAR"),
    ],
)
async def test_cred_admissibility_parity(
    dmn: CibSevenDmnTransport,
    direcao: str,
    doc: bool,
    licenca: bool,
    criterios: bool,
    indicio: bool,
    expected_roteamento: str,
) -> None:
    row = await _eval(
        dmn,
        "cred_admissibility",
        {
            "direcao": direcao,
            "tipo_prestador": "hospital",
            "documentacao_completa": doc,
            "licenca_valida": licenca,
            "dentro_criterios_rede": criterios,
            "indicio_irregularidade_sinalizado": indicio,
        },
    )
    assert row["roteamento"] == expected_roteamento


@pytest.mark.parametrize(
    ("direcao", "indicio", "expected_roteamento"),
    [
        ("descredenciamento", False, "ANALISE_DESCREDENCIAMENTO"),
        ("credenciamento", True, "ANALISE_DESCREDENCIAMENTO"),
        ("credenciamento", False, "ANALISE_CREDENCIAMENTO"),
    ],
)
async def test_cred_route_parity(
    dmn: CibSevenDmnTransport, direcao: str, indicio: bool, expected_roteamento: str
) -> None:
    row = await _eval(
        dmn,
        "cred_route",
        {
            "direcao": direcao,
            "tipo_prestador": "hospital",
            "origem_solicitacao": "portal",
            "indicio_irregularidade_sinalizado": indicio,
        },
    )
    assert row["roteamento"] == expected_roteamento


async def test_assess_admissibility_end_to_end_against_live_engine(dmn: CibSevenDmnTransport) -> None:
    """End-to-end proof: the MIGRATED credenciamento worker function (chains cred_admissibility
    -> cred_route) against the real engine. Dispatched via `asyncio.to_thread`, matching how the
    harness actually invokes sync worker functions in production (see
    `test_route_aprovacao_end_to_end_against_live_engine`'s docstring)."""
    result = await asyncio.to_thread(
        cred_assess_admissibility,
        {
            "direcao": "descredenciamento",
            "documentacao_completa": True,
            "licenca_valida": True,
            "dentro_criterios_rede": True,
            "indicio_irregularidade_sinalizado": False,
        },
        dmn=dmn,
    )
    assert result["roteamento"] == "ANALISE_DESCREDENCIAMENTO"


# ---------------------------------------------------------------------------
# adequacao_gap + adequacao_remediation_routing — adequacao.route_remediation (2-DMN chain)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tipo_carater", "tempo", "distancia", "prestadores", "cobertura", "expected_gap"),
    [
        # DIVERGENCE: old Python's tighter CONFORME gate (tempo<=30, distancia<=20.0) called
        # this CONFORME; the deployed table's GAP_LEVE row (tempo<=60, distancia<=50.0)
        # precedes CONFORME in FIRST-hit-policy order and wins here.
        ("eletivo", 15, 5.0, 5, True, "GAP_LEVE"),
        ("eletivo", 45, 10.0, 2, True, "GAP_LEVE"),
        ("urgencia_emergencia", 45, 10.0, 2, True, "GAP_CRITICO"),
        ("eletivo", 75, 30.0, 1, False, "GAP_MODERADO"),
        ("eletivo", 120, 50.0, 0, False, "GAP_CRITICO"),
        # CONFORME is still reachable when tempo exceeds GAP_LEVE's own gate (DMN-content
        # quirk, not patched: CONFORME's row has no tempo/distancia gate of its own).
        ("eletivo", 70, 10.0, 2, True, "CONFORME"),
    ],
)
async def test_adequacao_gap_parity(
    dmn: CibSevenDmnTransport,
    tipo_carater: str,
    tempo: int,
    distancia: float,
    prestadores: int,
    cobertura: bool,
    expected_gap: str,
) -> None:
    row = await _eval(
        dmn,
        "adequacao_gap",
        {
            "tipo_carater": tipo_carater,
            "tempo_acesso_apurado_min": tempo,
            "distancia_apurada_km": distancia,
            "prestadores_disponiveis": prestadores,
            "cobertura_geo_suficiente": cobertura,
        },
    )
    assert row["gap_adequacao"] == expected_gap


@pytest.mark.parametrize(
    ("gap_adequacao", "dados_completos", "expected_roteamento"),
    [
        ("CONFORME", True, "MONITORAR"),
        ("GAP_LEVE", True, "MONITORAR"),
        ("GAP_MODERADO", True, "ENCAMINHAR_CREDENCIAMENTO"),
        ("GAP_CRITICO", True, "ANALISE_HUMANA"),
        ("GAP_LEVE", False, "ANALISE_HUMANA"),
    ],
)
async def test_adequacao_remediation_routing_parity(
    dmn: CibSevenDmnTransport, gap_adequacao: str, dados_completos: bool, expected_roteamento: str
) -> None:
    row = await _eval(
        dmn,
        "adequacao_remediation_routing",
        {"gap_adequacao": gap_adequacao, "dados_geo_completos": dados_completos},
    )
    assert row["roteamento_remediacao"] == expected_roteamento


# ---------------------------------------------------------------------------
# lgpd_dsr_routing — BRT_RotearDsr (native businessRuleTask; the former `lgpd.AssessRequestWorker`
# duplicate was RETIRED, #55 R-E, T2.8 — routing is engine-side DMN evaluation only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tipo_requisicao", "envolve_dados_saude", "expected_fluxo", "expected_grupo"),
    [
        ("confirmacao_acesso", True, "EXPORTACAO", "juridico-privacidade"),
        ("confirmacao_acesso", False, "EXPORTACAO", "dpo"),
        ("eliminacao", True, "ELIMINACAO_AVALIACAO", "juridico-privacidade"),
        # DIVERGENCE: old Python's grupo_revisor logic would have routed this to "dpo"
        # (envolve_dados_saude=False and tipo IS mapped). The DMN's eliminacao rule (r5) ALWAYS
        # routes to juridico-privacidade regardless of envolve_dados_saude.
        ("eliminacao", False, "ELIMINACAO_AVALIACAO", "juridico-privacidade"),
        ("correcao", False, "RETIFICACAO", "dpo"),
        ("desconhecido", False, "INFORMATIVO", "juridico-privacidade"),
    ],
)
async def test_lgpd_dsr_routing_parity(
    dmn: CibSevenDmnTransport,
    tipo_requisicao: str,
    envolve_dados_saude: bool,
    expected_fluxo: str,
    expected_grupo: str,
) -> None:
    row = await _eval(
        dmn,
        "lgpd_dsr_routing",
        {"tipo_requisicao": tipo_requisicao, "envolve_dados_saude": envolve_dados_saude},
    )
    assert row["fluxo"] == expected_fluxo
    assert row["grupo_revisor"] == expected_grupo
    # GAP-LGPD-5: sla_resposta is IDENTICAL (P15D) across every rule by design.
    assert row["sla_resposta"] == "P15D"


# ---------------------------------------------------------------------------
# ans_calendar — BLOCKED (NOT cut over, ans_cron.check_calendar left as pure Python)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "report_type", ["MAPEAMENTO_REDE", "DIOPS", "SIP", "RPC", "ANS_TISS", "QUALIFICACAO"]
)
async def test_ans_calendar_domain_mismatch_evidence(dmn: CibSevenDmnTransport, report_type: str) -> None:
    """Evidence-of-block test (T1.5, NOT a parity assertion): proves every `report_type` value
    `ans_cron.py` actually produces/consumes hits the DMN's catch-all
    (`fonte_regulatoria="REVISAO_HUMANA"`), because the deployed table's literals are RN-citation
    style (`RN_124_SIP`, `DIOPS_TRIMESTRAL`, ...) — zero overlap. This is WHY `check_calendar`
    was left as pure Python (ADR-0028 §7 gate: 100% parity required before deletion; it does not
    hold here) rather than a functional regression masquerading as a "safe" cutover. If this test
    ever starts failing (a real rule match appears), the `report_type` taxonomies have been
    reconciled upstream in `spec/` and `check_calendar` should be re-evaluated for cutover.
    """
    row = await _eval(dmn, "ans_calendar", {"report_type": report_type, "competencia": "2026-06"})
    assert row["fonte_regulatoria"].startswith("REVISAO_HUMANA"), (
        f"report_type={report_type!r} unexpectedly matched a non-catch-all ans_calendar rule — "
        "the taxonomy mismatch may have been reconciled; re-evaluate check_calendar for cutover"
    )
