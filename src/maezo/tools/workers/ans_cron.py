"""Worker: ans_cron (SP-OP-ANS-CRON-001).

Agendador per-report_type dos Envios Periodicos ANS.
Pure scheduler — NO adverse effects, all terminals NEUTRAL.

TOPOLOGIA (pos GAP-ANS-1 / ANS-CRON-DEAD-CODE): cada uma das 5 process definitions de
`spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn` executa DOIS serviceTasks em
sequencia a partir do seu proprio TimerStartEvent:

    TimerStartEvent (timeCycle proprio)
      -> ST_ResolverCompetencia*  topic `operadora.ans_cron.trigger_submissions`  (ESTE modulo)
      -> ST_PublishCronDue*       topic `operadora.events.publish`                (events.py)
      -> End_Cron*

O PRIMEIRO task e servido por `trigger_submissions` aqui: le o literal `ans_cron_report_type`
(inputParameter LOCAL do proprio task — o unico lugar do BPMN onde o report_type e declarado) e
DEVOLVE `report_type`/`periodicidade`/`competencia`/`competencia_referencia_iso` como VARIAVEIS DE
PROCESSO. O SEGUNDO task (publicador generico) le essas variaveis pelo `event_payload_vars` e
emite o fato tipado `ans.cron_due` em `operadora.notifications.internal`; o `notification_bridge`
mapeia o fato para o start de SP-OP-ANS-SUBMIT-001 com business key deterministica
`ANSSUB-{tenant}-{report_type}-{competencia}`.

POR QUE o literal do BPMN se chama `ans_cron_report_type` e NAO `report_type`: `camunda:
inputParameter` cria uma variavel LOCAL do activity scope, e `complete(variables=...)` do external
task usa `setVariable`, que ATUALIZA a variavel no escopo mais proximo onde ela ja existe. Se o
literal se chamasse `report_type`, o `report_type` devolvido por este worker seria escrito de volta
no escopo LOCAL do proprio task e descartado no fim da activity — nunca chegaria ao
`ST_PublishCronDue*`. O nome distinto garante que os 4 nomes devolvidos aqui nao existem
localmente e portanto sobem para o escopo da instancia de processo.

FAIL-CLOSED: um `report_type` fora da taxonomia (`_REPORT_PERIODICIDADE`) resolve
`periodicidade="indeterminada"` — o MESMO literal da row catch-all de
`spec/processes/dmn/ans_calendar.dmn` — e `competencia=COMPETENCIA_PENDENTE` (sentinela). Nada e
inventado: sem periodicidade regulatoria conhecida nao ha competencia computavel, e a resolucao
volta a ser humana em `UT_CorrigirPendenciaEnvio` (SP-OP-ANS-SUBMIT-001).

TAXONOMIA UNICA (PERSP-B5-ANSCRON-TOPICS): as chaves de `_REPORT_PERIODICIDADE` sao EXATAMENTE os
literais `report_type` do BPMN, do contrato (`docs/processes/contracts/SP-OP-ANS-CRON-001.md`) e
das rows de `ans_calendar.dmn`. A taxonomia paralela anterior (`MAPEAMENTO_REDE`/`DIOPS`/`SIP`/
`RPC`/`ANS_TISS`/`QUALIFICACAO`) tinha intersecao VAZIA com o resto do repo e foi eliminada; o
acordo Python<->DMN e provado engine-side em
`tests/integration/dmn/test_dmn_golden_parity.py::test_ans_calendar_periodicidade_paridade_com_
taxonomia_do_worker`, e o acordo Python<->BPMN (timeCycle) em
`test_ans_cron_timecycle_do_bpmn_bate_com_a_taxonomia_do_worker` (fence estatico, sem engine).

`check_calendar` FOI REMOVIDA (nao renomeada, nao desativada): era uma re-implementacao Python da
`ans_calendar.dmn` que a propria docstring declarava bloqueada pela divergencia de taxonomia. Com a
taxonomia reconciliada, o gate do ADR-0028 §7 ("only after 100% parity in CI: delete the Python
re-implementation") passa a valer, e a tabela e avaliada ENGINE-SIDE por
`BRT_Calendario`/`camunda:decisionRef="ans_calendar"` em
`spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn` — o processo a jusante deste
agendador. O output `deve_enviar` que ela produzia nao tinha consumidor algum (nenhum gateway,
nenhum worker, nenhum agente).
"""

from __future__ import annotations

import functools
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import structlog

from maezo.tools.workers.base import FunctionWorker

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------
# Taxonomia de report_type -> (periodicidade pt-BR, periodo ISO 8601)
#
# DRAFT/verify regulatorio: RN 124/2006 (SIP), RN 209/2009 (utilizacao), RN 388/2015
# (qualidade), RN 424/2017 (TISS/monitoramento) e DIOPS trimestral seguem marcados DRAFT em
# `docs/review-queue.md` e no contrato — nem a periodicidade nem qualquer prazo aqui foram
# confirmados com o regulatorio.
#
# A metade pt-BR de cada par e o MESMO vocabulario que `ans_calendar.dmn` devolve em
# `periodicidade` (mensal/anual/trimestral); a metade ISO e o que `_compute_competencia` consome.
# ---------------------------------------------------------------

#: Fuso civil de negocio da operadora — a ancora temporal de que a competencia e derivada.
#:
#: POR QUE NAO UTC: `_compute_competencia` promete nomear o periodo JA FECHADO na ancora. Com
#: `datetime.now(UTC)`, um tick entre 00:00 e 02:59 UTC do dia 1 (= 21:00-23:59 do ULTIMO dia do
#: mes anterior no horario civil brasileiro, UTC-3) leria o mes NOVO enquanto o Brasil ainda esta
#: no ANTIGO — e o "mes imediatamente anterior" por UTC seria entao um mes que AINDA NAO FECHOU
#: para o regulador. A mesma janela de ~3h existe em cada virada de trimestre e de ano.
#:
#: DRAFT/verify regulatorio: a ESCOLHA do fuso e um default de ENGENHARIA (a operadora e
#: brasileira e os prazos ANS sao publicados em horario civil brasileiro), NAO confirmada com o
#: regulatorio — pergunta 2b de SP-OP-ANS-CRON-001 em `docs/sme-dispatch/regulatorio/PACKAGE.md`.
#:
#: Nao existe helper canonico de fuso de negocio no repo (`grep -rn "Sao_Paulo|BUSINESS_TZ|
#: zoneinfo" src/maezo` -> 0 antes desta mudanca); esta e a UNICA definicao e deve ser reusada
#: (nao reescrita) por qualquer outro worker que precise do mesmo conceito.
_BUSINESS_TZ = ZoneInfo("America/Sao_Paulo")


def _now_business() -> datetime:
    """Instante corrente no fuso civil de negocio (seam unica de relogio deste modulo).

    Existe como funcao — e nao como chamada inline — para que os testes de fronteira de periodo
    possam fixar o instante sem monkeypatch em `datetime` (ver
    `tests/unit/tools/workers/test_ans_cron.py`, casos de virada de mes/trimestre/ano).
    """
    return datetime.now(_BUSINESS_TZ)


def parse_competencia_referencia_iso(value: object) -> datetime:
    """Parse a `competencia_referencia_iso` anchor. UNICO leitor autorizado dessa forma.

    Esta funcao e o DONO do formato do campo: quem produz (`trigger_submissions`) e quem le
    (`_compute_competencia`, os testes de unidade, a suite de integracao contra o engine) passam
    todos por aqui, entao a forma esta definida em UM lugar so e uma mudanca de forma futura
    atualiza uma funcao — nao N call sites espalhados.

    Existe porque a forma MUDOU e um leitor ficou para tras: ate ANS-CRON-DEAD-CODE/REP-ANS-CRON o
    campo era a data nua `YYYY-MM-DD` (ancora UTC), e havia call sites usando
    `date.fromisoformat`, que aceita a data nua mas REJEITA um instante com offset
    (`date.fromisoformat("2026-09-03T06:08:06-03:00")` -> `ValueError: Invalid isoformat string`).
    Com a ancora migrada para o fuso civil de negocio, o campo passou a ser um instante COM
    OFFSET e esses leitores quebraram contra o engine real.

    Aceita, portanto, as DUAS formas:
      - canonica: instante ISO-8601 COM offset, p.ex. `2026-02-28T21:30:00-03:00` (o que
        `trigger_submissions` emite hoje) -> devolve um `datetime` AWARE;
      - legado/tolerada: data nua `YYYY-MM-DD` -> devolve um `datetime` NAIVE a meia-noite.
        Mantida porque fatos `ans.cron_due` ja em transito (ou fixtures antigas) ainda podem
        carregar a forma antiga, e porque o campo IRMAO `ans_cron_reference_date_iso`
        (`events.py`, carimbo do publicador) segue sendo data nua UTC de proposito.

    `_compute_competencia` so le `.year`/`.month`, entao a diferenca aware/naive nao muda a
    competencia derivada — o que importa e que o offset, quando presente, seja RESPEITADO e nao
    cause excecao.

    LEVANTA `ValueError`/`TypeError` em qualquer outra coisa: o tratamento fail-closed (sentinela)
    e decisao de quem chama, nao desta funcao (ver `_compute_competencia`), para que um leitor
    novo nao herde silenciosamente um fallback que nao pediu.
    """
    if not isinstance(value, str):
        raise TypeError(f"competencia_referencia_iso deve ser str, veio {type(value).__name__}")
    return datetime.fromisoformat(value.strip())


#: Sentinela de competencia nao resolvida (o humano resolve em UT_CorrigirPendenciaEnvio).
COMPETENCIA_PENDENTE = "COMPETENCIA_PENDENTE"

#: Periodicidade de um report_type fora da taxonomia — mesmo literal da row catch-all da DMN.
PERIODICIDADE_INDETERMINADA = "indeterminada"

_REPORT_PERIODICIDADE: dict[str, tuple[str, str]] = {
    "RN_124_SIP": ("mensal", "P1M"),
    "RN_209_UTILIZACAO": ("mensal", "P1M"),
    "RN_388_QUALIDADE": ("anual", "P12M"),
    "RN_424_TISS_MONITORAMENTO": ("mensal", "P1M"),
    "DIOPS_TRIMESTRAL": ("trimestral", "P3M"),
}


def _compute_competencia(reference_date_iso: str, periodicidade: str) -> str:
    """Compute the competencia from the reference date, per the ISO period of `report_type`.

    The competencia is the calendar period IMMEDIATELY BEFORE — i.e. already CLOSED as of — the
    reference date. The still-OPEN period containing the reference date is never itself a valid
    answer (it has not finished yet). "Closed" is judged in the CIVIL calendar of the anchor it is
    given: the caller (`trigger_submissions`) anchors on `_BUSINESS_TZ`, not UTC, precisely so that
    this invariant holds in Brazilian civil time — see `_BUSINESS_TZ`'s note. This function itself
    is pure and timezone-agnostic: it reads the year/month of whatever ISO instant it is handed
    (with or without offset) and never consults a clock.
      - P1M (monthly):   the previous month, "YYYY-MM".
      - P3M (quarterly): the most recently CLOSED quarter's start month, "YYYY-MM" — e.g. a
        reference date anywhere in Apr/May/Jun (all of Q2, still open) resolves to the SAME
        "YYYY-01" (Q1, the quarter that already ended), never "YYYY-04" (Q2 itself).
      - P12M (annual):   the previous calendar year in full, represented "YYYY-01" — shape-
        consistent with the "YYYY-MM" of the branches above (never bare "YYYY"; see the branch
        below for why).
      - anything else:   FAIL-CLOSED to `COMPETENCIA_PENDENTE`. An unrecognized (or empty)
        periodicidade means the report_type is outside the ratified taxonomy — there is no
        regulatory cadence to derive a period from, so the honest answer is the sentinel that
        routes the resolution to a human, NOT a monthly guess. (Pre-ANS-CRON-DEAD-CODE this
        branch silently defaulted to P1M and invented a previous-month competencia for an
        unknown report type; that value then travelled into the ANSSUB business key.)

    DRAFT/verify regulatorio: o mapeamento periodo->competencia (mes/trimestre/ano ANTERIOR ao da
    ancora) NAO foi confirmado com o regulatorio — ver `docs/review-queue.md`.
    """
    try:
        ref_date = parse_competencia_referencia_iso(reference_date_iso)
    except (ValueError, TypeError):
        return COMPETENCIA_PENDENTE

    if periodicidade == "P12M":
        # Annual: the previous calendar year, in full.
        #
        # Representation: "YYYY-01" (4-digit year + literal "-01"), NOT bare "YYYY". Chosen for
        # SHAPE-CONSISTENCY with the P1M/P3M "YYYY-MM" above — this value flows unvalidated into
        # the ANSSUB-{tenant}-{report_type}-{competencia} business key (ans_submit.py's
        # `_ans_business_key`, notification_bridge.py's `_ans_cron_business_key`) alongside every
        # other report_type's "YYYY-MM" competencia; a bare 4-char value would be the one outlier
        # shape in that key, with no format-normalizing consumer between here and the key string. No
        # in-repo convention was found expecting bare-year (grepped competencia format usages and
        # ANSSUB key tests). Still DRAFT/verify regulatorio like the mapping itself — see
        # docs/review-queue.md and docs/sme-dispatch/regulatorio/PACKAGE.md (annual/P12M case).
        return f"{ref_date.year - 1:04d}-01"

    if periodicidade == "P3M":
        # Quarterly: the most recently CLOSED quarter — computed the SAME way regardless of
        # which of the 3 months of the current quarter the reference date falls in.
        quarter_start = ((ref_date.month - 1) // 3) * 3 + 1  # start month of the CURRENT quarter
        prev_quarter_start = quarter_start - 3
        if prev_quarter_start < 1:
            prev_quarter_start += 12
            year = ref_date.year - 1
        else:
            year = ref_date.year
        return f"{year:04d}-{prev_quarter_start:02d}"

    if periodicidade == "P1M":
        if ref_date.month == 1:
            return f"{ref_date.year - 1:04d}-12"
        return f"{ref_date.year:04d}-{ref_date.month - 1:02d}"

    # Unmapped periodicidade => fail-closed sentinel (never a fabricated period).
    return COMPETENCIA_PENDENTE


# ---------------------------------------------------------------
# trigger_submissions — resolve os identificadores do despacho do tick
# ---------------------------------------------------------------


def trigger_submissions(variables: dict[str, Any], *, tenant_id: str = "") -> dict[str, Any]:
    """Resolve os identificadores do despacho de UM tick do agendador (`ST_ResolverCompetencia*`).

    Le o literal `ans_cron_report_type` do proprio task (inputParameter local do BPMN) e devolve,
    como VARIAVEIS DE PROCESSO, o que o `ST_PublishCronDue*` seguinte copia para o fato
    `ans.cron_due` via `event_payload_vars`:

      report_type                 — ecoado sob o nome canonico (o literal local nao viaja).
      periodicidade               — pt-BR (mensal/trimestral/anual), mesmo vocabulario da DMN
                                    `ans_calendar`; `indeterminada` fora da taxonomia.
      competencia                 — periodo FECHADO imediatamente anterior a ancora, ou
                                    `COMPETENCIA_PENDENTE` fail-closed.
      competencia_referencia_iso  — a ancora de que a competencia foi derivada, em ISO-8601 COM
                                    OFFSET no fuso civil de negocio (`_BUSINESS_TZ`,
                                    America/Sao_Paulo) — p.ex. `2026-02-28T21:30:00-03:00`. O
                                    offset viaja junto de proposito: sem ele o leitor nao sabe em
                                    que calendario civil o periodo foi fechado. Viaja no fato para
                                    que a derivacao seja auditavel e reproduzivel (distinta de
                                    `ans_cron_reference_date_iso`, que `events.py` carimba como
                                    data UTC do INSTANTE DA PUBLICACAO).

    NAO publica nada (o publicador e o task seguinte) e NAO devolve nenhum `*_publicado`: o antigo
    `fato_publicado: True` deste worker era um fato FABRICADO — afirmava uma publicacao que esta
    funcao nunca executou (mesma classe corrigida em GAP-FAB-NOTIF).

    NO adverse effects — pure scheduling dispatch.

    `tenant_id` (T2.6-EB3 part 3 — worker-signature seam, keyword-only, threaded via
    `register_ans_cron_workers(harness, tenant_id=...)`): SP-OP-ANS-CRON-001 e disparado por
    TimerStartEvent e nao tem contexto de caso por instancia, entao nao ha variavel de processo de
    onde ler um tenant real — a fonte e a identidade do proprio DEPLOYMENT
    (`WorkerRuntimeSettings.tenant_id`/`TENANT_ID`). O tenant do FATO, porem, e carimbado pelo
    publicador generico (`events.py::make_publish_event_handler`, seam `deployment_tenant_id`,
    `setdefault`) — e por isso `tenant_id` NAO esta em `event_payload_vars` no BPMN: se estivesse,
    o "" devolvido por uma composicao sem seam viajaria no payload e DESARMARIA o carimbo do
    publicador (a regra `ans.cron_due` da ponte exige tenant nao-vazio). Ha fence estatico para
    isso em `test_ans_cron_payload_vars_nao_carregam_tenant_id`.
    """
    report_type = str(variables.get("ans_cron_report_type", "")).strip()
    periodicidade, periodicidade_iso = _REPORT_PERIODICIDADE.get(
        report_type, (PERIODICIDADE_INDETERMINADA, "")
    )
    reference_date = _now_business().isoformat(timespec="seconds")

    competencia = _compute_competencia(reference_date, periodicidade_iso)

    logger.info(
        "ans_cron_trigger_submission",
        report_type=report_type,
        periodicidade=periodicidade,
        periodicidade_iso=periodicidade_iso,
        competencia=competencia,
        reference_date=reference_date,
        tenant_id=tenant_id,
    )

    return {
        "report_type": report_type,
        "periodicidade": periodicidade,
        "competencia": competencia,
        "competencia_referencia_iso": reference_date,
        "tenant_id": tenant_id,
    }


# ---------------------------------------------------------------
# Bootstrap — FunctionWorker adapter (T1.2/ADR-0026 Decisao §2a). No custom
# error class in this module (pure scheduler, all terminals NEUTRAL).
#
# Topic mapping: `operadora.ans_cron.trigger_submissions` e declarado por
# `ST_ResolverCompetencia*` nas 5 process definitions de
# spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn — topico
# REGISTRADO e ALCANCAVEL (PERSP-B5-ANSCRON-TOPICS). O outro topico do BPMN,
# `operadora.events.publish`, e o publicador generico compartilhado pelos 16
# BPMNs e vive em events.py.
# ---------------------------------------------------------------


def register_ans_cron_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-ANS-CRON-001 function workers on `harness`.

    `tenant_id` (T2.6-EB3 part 3 seam, `**seams` catch-all — mirrors `ans_gateway`/
    `tiss_validator` in `ans_submit.py`): threaded into `trigger_submissions` (see its docstring
    for why this scheduler needs a deployment-scoped seam rather than a per-instance process
    variable). Defaults to `""` fail-closed when the composition root does not pass one.
    """
    del kafka  # unused — no ans_cron.py worker declares a Kafka dependency
    tenant_id = str(seams.get("tenant_id", ""))
    harness.register_worker(
        FunctionWorker(
            "operadora.ans_cron.trigger_submissions",
            functools.partial(trigger_submissions, tenant_id=tenant_id),
        )
    )
