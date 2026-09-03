"""Unit tests for maezo.tools.workers.ans_cron (SP-OP-ANS-CRON-001).

TDD London School: tests verify the per-report_type dispatch resolution the scheduler's FIRST
service task (`ST_ResolverCompetencia*`) performs. The engine-side halves of the same contract —
that the BPMN really binds this topic, that the timeCycle agrees with the taxonomy, and that the
fact really carries the computed competencia — live in
`tests/integration/processes/test_sp_op_ans_cron_001.py`; the Python<->DMN taxonomy agreement
lives in `tests/integration/dmn/test_dmn_golden_parity.py`.
"""

from datetime import datetime, timedelta
from typing import Any

import pytest

from maezo.tools.workers import ans_cron
from maezo.tools.workers.ans_cron import (
    _BUSINESS_TZ,
    _REPORT_PERIODICIDADE,
    COMPETENCIA_PENDENTE,
    PERIODICIDADE_INDETERMINADA,
    _compute_competencia,
    _now_business,
    register_ans_cron_workers,
    trigger_submissions,
)
from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness

# ---------------------------------------------------------------
# Taxonomia — PERSP-B5-ANSCRON-TOPICS
# ---------------------------------------------------------------

#: A taxonomia canonica: os MESMOS literais do BPMN, do contrato e das rows de ans_calendar.dmn.
_TAXONOMIA_CANONICA: dict[str, tuple[str, str]] = {
    "RN_124_SIP": ("mensal", "P1M"),
    "RN_209_UTILIZACAO": ("mensal", "P1M"),
    "RN_388_QUALIDADE": ("anual", "P12M"),
    "RN_424_TISS_MONITORAMENTO": ("mensal", "P1M"),
    "DIOPS_TRIMESTRAL": ("trimestral", "P3M"),
}


def test_taxonomia_report_type_e_a_do_bpmn_contrato_e_dmn() -> None:
    """PERSP-B5-ANSCRON-TOPICS: a taxonomia paralela anterior (`MAPEAMENTO_REDE`/`DIOPS`/`SIP`/
    `RPC`/`ANS_TISS`/`QUALIFICACAO`) tinha intersecao VAZIA com BPMN/contrato/DMN. Pin de
    regressao: nenhum daqueles literais volta, e os 5 canonicos estao todos presentes."""
    assert _REPORT_PERIODICIDADE == _TAXONOMIA_CANONICA
    orfaos = {"MAPEAMENTO_REDE", "DIOPS", "SIP", "RPC", "ANS_TISS", "QUALIFICACAO"}
    assert not (orfaos & set(_REPORT_PERIODICIDADE)), (
        "taxonomia paralela (sem intersecao com BPMN/contrato/DMN) reintroduzida"
    )


# ---------------------------------------------------------------
# trigger_submissions
# ---------------------------------------------------------------


@pytest.mark.parametrize(("report_type", "esperado"), sorted(_TAXONOMIA_CANONICA.items()))
def test_trigger_submissions_resolve_periodicidade_e_competencia_por_tipo(
    report_type: str, esperado: tuple[str, str]
) -> None:
    """Cada report_type canonico resolve a sua periodicidade pt-BR e uma competencia REAL
    (nunca a sentinela) a partir da ancora do tick."""
    periodicidade, periodicidade_iso = esperado
    result = trigger_submissions({"ans_cron_report_type": report_type})

    assert result["report_type"] == report_type
    assert result["periodicidade"] == periodicidade
    assert result["competencia"] != COMPETENCIA_PENDENTE
    assert result["competencia"] == _compute_competencia(
        result["competencia_referencia_iso"], periodicidade_iso
    )


def test_trigger_submissions_nao_devolve_fato_fabricado() -> None:
    """ANS-CRON-DEAD-CODE / classe GAP-FAB-NOTIF: esta funcao NAO publica nada (quem publica e o
    `ST_PublishCronDue*` seguinte), entao nao pode devolver `fato_publicado`/`event_type`."""
    result = trigger_submissions({"ans_cron_report_type": "RN_124_SIP"})
    assert "fato_publicado" not in result
    assert "event_type" not in result
    assert set(result) == {
        "report_type",
        "periodicidade",
        "competencia",
        "competencia_referencia_iso",
        "tenant_id",
    }


def test_trigger_submissions_report_type_desconhecido_e_fail_closed() -> None:
    """Fora da taxonomia: periodicidade `indeterminada` (o literal da row catch-all da DMN) e
    competencia SENTINELA — nunca um periodo mensal inventado (o default P1M anterior escrevia um
    mes plausivel na business key ANSSUB de um tipo que ninguem reconhece)."""
    result = trigger_submissions({"ans_cron_report_type": "UNKNOWN_REPORT"})
    assert result["periodicidade"] == PERIODICIDADE_INDETERMINADA
    assert result["competencia"] == COMPETENCIA_PENDENTE


def test_trigger_submissions_sem_report_type_e_fail_closed() -> None:
    """Variavel ausente/em branco tem o mesmo tratamento de um tipo desconhecido."""
    for variables in ({}, {"ans_cron_report_type": ""}, {"ans_cron_report_type": "   "}):
        result = trigger_submissions(dict(variables))
        assert result["report_type"] == ""
        assert result["periodicidade"] == PERIODICIDADE_INDETERMINADA
        assert result["competencia"] == COMPETENCIA_PENDENTE


def test_trigger_submissions_ignora_report_type_do_escopo_de_processo() -> None:
    """O tipo vem SO do literal local `ans_cron_report_type` (ver o racional de escopo Camunda na
    docstring do modulo): um `report_type` que ja exista no escopo de processo nunca e a fonte."""
    result = trigger_submissions({"ans_cron_report_type": "DIOPS_TRIMESTRAL", "report_type": "RN_124_SIP"})
    assert result["report_type"] == "DIOPS_TRIMESTRAL"
    assert result["periodicidade"] == "trimestral"


def test_trigger_submissions_defaults_tenant_id_to_blank() -> None:
    """T2.6-EB3 part 3: tenant_id defaults to "" fail-closed for any caller that predates the
    keyword-only parameter — no behavior change for existing positional-only callers."""
    result = trigger_submissions({"ans_cron_report_type": "DIOPS_TRIMESTRAL"})
    assert result["tenant_id"] == ""


def test_trigger_submissions_carries_tenant_id_when_passed() -> None:
    """T2.6-EB3 part 3 — worker-signature seam: trigger_submissions accepts and echoes a
    tenant_id (source: the deployment's own identity, threaded via register_ans_cron_workers)."""
    result = trigger_submissions({"ans_cron_report_type": "DIOPS_TRIMESTRAL"}, tenant_id="amh")
    assert result["tenant_id"] == "amh"


def test_register_ans_cron_workers_threads_tenant_id_seam() -> None:
    """T2.6-EB3 part 3: register_ans_cron_workers(harness, tenant_id=...) binds it into
    trigger_submissions via functools.partial — proven end-to-end through the registry, not just
    by calling trigger_submissions directly."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="ans-cron-tenant-probe")
    register_ans_cron_workers(harness, tenant_id="amh")

    worker = harness.registry.get("operadora.ans_cron.trigger_submissions")
    assert worker is not None
    result: dict[str, Any] = worker.run({"ans_cron_report_type": "DIOPS_TRIMESTRAL"})
    assert result["tenant_id"] == "amh"


def test_register_ans_cron_workers_defaults_tenant_id_to_blank_when_not_passed() -> None:
    """No seam passed -> "" fail-closed, never fabricated (unchanged behavior for any existing
    caller of register_ans_cron_workers that doesn't know about this seam yet)."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="ans-cron-tenant-probe-2")
    register_ans_cron_workers(harness)

    worker = harness.registry.get("operadora.ans_cron.trigger_submissions")
    assert worker is not None
    result: dict[str, Any] = worker.run({"ans_cron_report_type": "DIOPS_TRIMESTRAL"})
    assert result["tenant_id"] == ""


def test_register_ans_cron_workers_registra_apenas_o_topico_alcancavel() -> None:
    """PERSP-B5-ANSCRON-TOPICS: `operadora.ans_cron.check_calendar` foi REMOVIDO (era uma
    re-implementacao Python da DMN `ans_calendar`, hoje avaliada engine-side por `BRT_Calendario`
    em SP-OP-ANS-SUBMIT-001, e o seu output `deve_enviar` nao tinha consumidor algum). Resta o
    unico topico que o BPMN de fato declara."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="ans-cron-topics-probe")
    register_ans_cron_workers(harness)

    ans_cron_topics = {t for t in harness.registered_topics if t.startswith("operadora.ans_cron.")}
    assert ans_cron_topics == {"operadora.ans_cron.trigger_submissions"}


# ---------------------------------------------------------------
# _compute_competencia — table-driven, all 12 months x {P1M, P3M, P12M} (item 3)
#
# The competencia is the calendar period IMMEDIATELY BEFORE — already CLOSED as of — the
# reference date; the period CONTAINING the reference date is never itself a valid answer.
# Reference year 2026 throughout (year-wrap vectors are the Jan/P1M and Jan-Mar/P3M rows, which
# roll back into 2025).
# ---------------------------------------------------------------

#: (reference month in 2026, expected P1M competencia, expected P3M competencia)
#: P3M expectation encodes the FIX: all 3 months of a quarter resolve to the SAME prior, CLOSED
#: quarter — pre-fix, only the quarter's first month (Jan/Apr/Jul/Oct) did; the other 8 months
#: wrongly returned the CURRENT, still-open quarter's own start month instead.
_COMPETENCIA_TABLE: list[tuple[int, str, str]] = [
    (1, "2025-12", "2025-10"),  # Q1 month 1 -> still-open Q1 -> prior CLOSED quarter is Q4/2025
    (2, "2026-01", "2025-10"),  # Q1 month 2 (mid-quarter) -> same prior quarter, Q4/2025
    (3, "2026-02", "2025-10"),  # Q1 month 3 (quarter-end) -> same prior quarter, Q4/2025
    (4, "2026-03", "2026-01"),  # Q2 month 1 -> prior CLOSED quarter is Q1/2026
    (5, "2026-04", "2026-01"),  # Q2 month 2 (mid-quarter) -> same prior quarter, Q1/2026
    (6, "2026-05", "2026-01"),  # Q2 month 3 (quarter-end) -> same prior quarter, Q1/2026
    (7, "2026-06", "2026-04"),  # Q3 month 1 -> prior CLOSED quarter is Q2/2026
    (8, "2026-07", "2026-04"),  # Q3 month 2 (mid-quarter) -> same prior quarter, Q2/2026
    (9, "2026-08", "2026-04"),  # Q3 month 3 (quarter-end) -> same prior quarter, Q2/2026
    (10, "2026-09", "2026-07"),  # Q4 month 1 -> prior CLOSED quarter is Q3/2026
    (11, "2026-10", "2026-07"),  # Q4 month 2 (mid-quarter) -> same prior quarter, Q3/2026
    (12, "2026-11", "2026-07"),  # Q4 month 3 (quarter-end) -> same prior quarter, Q3/2026
]


@pytest.mark.parametrize(("month", "expected_p1m", "expected_p3m"), _COMPETENCIA_TABLE)
def test_compute_competencia_p1m_table(month: int, expected_p1m: str, expected_p3m: str) -> None:
    """P1M (monthly): always the previous month, every month of the year, incl. the Jan->Dec/y-1 wrap."""
    del expected_p3m
    assert _compute_competencia(f"2026-{month:02d}-15", "P1M") == expected_p1m


@pytest.mark.parametrize(("month", "expected_p1m", "expected_p3m"), _COMPETENCIA_TABLE)
def test_compute_competencia_p3m_table(month: int, expected_p1m: str, expected_p3m: str) -> None:
    """P3M (quarterly) REGRESSION PIN: every month of a quarter — first, mid, and last — resolves
    to the SAME most-recently-CLOSED quarter. Pre-fix, only Jan/Apr/Jul/Oct did; Feb/Mar,
    May/Jun, Aug/Sep and Nov/Dec each wrongly returned their own CURRENT, still-open quarter."""
    del expected_p1m
    assert _compute_competencia(f"2026-{month:02d}-15", "P3M") == expected_p3m


@pytest.mark.parametrize("month", [m for m, _, _ in _COMPETENCIA_TABLE])
def test_compute_competencia_p12m_table_is_always_the_prior_calendar_year(month: int) -> None:
    """P12M (annual) REGRESSION PIN: every month of the year resolves to the previous calendar
    YEAR, represented "YYYY-01" — shape-consistent with the "YYYY-MM" of P1M/P3M, never a bare
    "YYYY" (see ans_cron.py's `_compute_competencia` P12M branch for the shape-consistency
    rationale) — and never a month WITHIN that year."""
    assert _compute_competencia(f"2026-{month:02d}-15", "P12M") == "2025-01"


def test_compute_competencia_p12m_year_boundary() -> None:
    """The P12M year rollback is unconditional on month — pinned explicitly at both ends of the
    year (January and December of the same reference year still both resolve to year-1)."""
    assert _compute_competencia("2026-01-01", "P12M") == "2025-01"
    assert _compute_competencia("2026-12-31", "P12M") == "2025-01"


def test_compute_competencia_periodicidade_desconhecida_e_fail_closed() -> None:
    """ANS-CRON-DEAD-CODE: uma periodicidade fora de {P1M, P3M, P12M} devolve a SENTINELA, nao o
    mes anterior. Antes deste fix a funcao tratava qualquer valor desconhecido (inclusive o vazio
    de um report_type nao mapeado) como P1M e inventava uma competencia mensal plausivel, que
    entrava na business key ANSSUB sem nunca ter sido derivada de uma cadencia regulatoria."""
    for periodicidade in ("", "P6M", "indeterminada", "mensal"):
        assert _compute_competencia("2026-08-15", periodicidade) == COMPETENCIA_PENDENTE


def test_compute_competencia_invalid_reference_date_is_pending_not_a_crash() -> None:
    """An unparseable reference_date_iso fails closed to the sentinel, for every periodicidade —
    never raises, never silently defaults to a real-looking competencia."""
    for periodicidade in ("P1M", "P3M", "P12M"):
        assert _compute_competencia("not-a-date", periodicidade) == COMPETENCIA_PENDENTE


# ---------------------------------------------------------------
# Ancora temporal — fuso civil de negocio (nao UTC)
#
# O bug corrigido aqui NAO esta no mapeamento periodo->competencia (esse ja era correto), e sim
# na ANCORA: com `datetime.now(UTC)` havia uma janela de ~3h em CADA virada de mes/trimestre/ano
# em que o UTC ja estava no periodo novo enquanto o Brasil ainda estava no antigo — e o "periodo
# imediatamente anterior" segundo o UTC era um periodo que AINDA NAO havia fechado para o
# regulador, violando o proprio invariante documentado de `_compute_competencia`.
#
# Os casos abaixo fixam o instante do relogio pela seam `_now_business` e comparam o resultado
# com o que o UTC teria produzido no MESMO instante.
# ---------------------------------------------------------------


def _freeze_business_clock(monkeypatch: pytest.MonkeyPatch, utc_instant: str) -> None:
    """Congela `ans_cron._now_business` no instante UTC dado, convertido para `_BUSINESS_TZ`."""
    momento = datetime.fromisoformat(utc_instant).astimezone(_BUSINESS_TZ)
    monkeypatch.setattr(ans_cron, "_now_business", lambda: momento)


def test_business_tz_e_o_fuso_civil_brasileiro() -> None:
    """A constante e America/Sao_Paulo e produz offset UTC-3 fora do horario de verao (extinto no
    Brasil desde 2019). DRAFT/verify regulatorio: a ESCOLHA do fuso e um default de engenharia —
    ver `docs/sme-dispatch/regulatorio/PACKAGE.md` (SP-OP-ANS-CRON-001, pergunta 2b)."""
    assert str(_BUSINESS_TZ) == "America/Sao_Paulo"
    assert datetime(2026, 2, 28, 21, 30, tzinfo=_BUSINESS_TZ).utcoffset() == timedelta(hours=-3)


def test_ancora_carrega_offset_explicito_e_nao_e_data_nua(monkeypatch: pytest.MonkeyPatch) -> None:
    """`competencia_referencia_iso` viaja em ISO-8601 COM OFFSET: sem o offset o consumidor do
    fato nao consegue dizer em que calendario civil o periodo foi fechado."""
    _freeze_business_clock(monkeypatch, "2026-02-28T23:30:00+00:00")
    ancora = trigger_submissions({"ans_cron_report_type": "RN_124_SIP"})["competencia_referencia_iso"]

    assert ancora == "2026-02-28T20:30:00-03:00"
    momento = datetime.fromisoformat(ancora)
    assert momento.utcoffset() == timedelta(hours=-3), "a ancora precisa carregar o offset"


def test_ancora_mensal_na_virada_do_mes_usa_o_calendario_brasileiro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """00:30 UTC do dia 1 = 21:30 BRT do ULTIMO dia do mes anterior. Em horario civil brasileiro
    fevereiro AINDA ESTA ABERTO, logo a ultima competencia FECHADA e janeiro. Com a ancora UTC
    (o defeito) o resultado seria `2026-02` — um mes ainda aberto para o regulador."""
    _freeze_business_clock(monkeypatch, "2026-03-01T00:30:00+00:00")
    result = trigger_submissions({"ans_cron_report_type": "RN_124_SIP"})

    assert result["competencia_referencia_iso"] == "2026-02-28T21:30:00-03:00"
    assert result["competencia"] == "2026-01"
    # O que a ancora UTC teria produzido no MESMO instante — o periodo ainda aberto no Brasil.
    assert _compute_competencia("2026-03-01T00:30:00+00:00", "P1M") == "2026-02"


def test_ancora_mensal_depois_da_virada_no_brasil_fecha_o_mes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """03:00 UTC do dia 1 = 00:00 BRT do dia 1: marco COMECOU tambem no Brasil, entao fevereiro
    fechou e passa a ser a competencia. O outro lado da mesma fronteira."""
    _freeze_business_clock(monkeypatch, "2026-03-01T03:00:00+00:00")
    result = trigger_submissions({"ans_cron_report_type": "RN_124_SIP"})

    assert result["competencia_referencia_iso"] == "2026-03-01T00:00:00-03:00"
    assert result["competencia"] == "2026-02"


def test_ancora_mensal_no_fim_do_mes_nunca_nomeia_o_mes_aberto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """23:30 UTC do ULTIMO dia do mes = 20:30 BRT do MESMO dia: UTC e BRT concordam no mes, e o
    mes corrente (ainda aberto) nunca e a resposta."""
    _freeze_business_clock(monkeypatch, "2026-03-31T23:30:00+00:00")
    result = trigger_submissions({"ans_cron_report_type": "RN_124_SIP"})

    assert result["competencia_referencia_iso"] == "2026-03-31T20:30:00-03:00"
    assert result["competencia"] == "2026-02"


def test_ancora_trimestral_na_virada_do_trimestre_usa_o_calendario_brasileiro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mesma janela na virada de TRIMESTRE: 00:30 UTC de 01/abr = 21:30 BRT de 31/mar, com o Q1
    ainda aberto no Brasil — a ultima competencia trimestral FECHADA e o Q4/2025 (`2025-10`).
    Pela ancora UTC seria `2026-01` (o proprio Q1, ainda aberto)."""
    _freeze_business_clock(monkeypatch, "2026-04-01T00:30:00+00:00")
    result = trigger_submissions({"ans_cron_report_type": "DIOPS_TRIMESTRAL"})

    assert result["competencia"] == "2025-10"
    assert _compute_competencia("2026-04-01T00:30:00+00:00", "P3M") == "2026-01"


def test_ancora_anual_na_virada_do_ano_usa_o_calendario_brasileiro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mesma janela na virada de ANO: 01:00 UTC de 01/jan/2026 = 22:00 BRT de 31/dez/2025, com
    2025 ainda aberto no Brasil — o ultimo ano FECHADO e 2024 (`2024-01`). Pela ancora UTC seria
    `2025-01` (o proprio 2025, ainda aberto)."""
    _freeze_business_clock(monkeypatch, "2026-01-01T01:00:00+00:00")
    result = trigger_submissions({"ans_cron_report_type": "RN_388_QUALIDADE"})

    assert result["competencia"] == "2024-01"
    assert _compute_competencia("2026-01-01T01:00:00+00:00", "P12M") == "2025-01"


def test_now_business_devolve_instante_aware_no_fuso_de_negocio() -> None:
    """A seam real (nao congelada) devolve um datetime AWARE no fuso de negocio — nunca naive
    (um naive faria `isoformat()` perder o offset e a ancora voltaria a ser ambigua)."""
    agora = _now_business()
    assert agora.tzinfo is not None
    assert agora.utcoffset() == timedelta(hours=-3)
