"""Worker: adequacao (SP-OP-ADEQUACAO-001).

Adequacao Geografica de Rede — mostly L3 monitoring with one human-gated adverse effect.
Guard: ERR_FALLBACK_COMMITMENT_NOT_HUMAN (ADR-0018).
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

import structlog

from maezo.platform.integrations.partition_key import partition_key_for_task
from maezo.tools.workers.adequacao_shadow import record_shadow_divergence
from maezo.tools.workers.base import FunctionWorker, non_blank
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn

if TYPE_CHECKING:
    from collections.abc import Mapping

    from maezo.a2a import DelegationDispatcher
    from maezo.tools.workers.harness import (
        ExternalTask,
        KafkaPublisher,
        TaskHandler,
        WorkerHarness,
    )

logger = structlog.get_logger(__name__)

# Internal-notification channel (mirrors programa.py's/recurso.py's/lgpd.py's own
# `_NOTIFICATIONS_TOPIC` — a `type`-discriminated envelope on `operadora.notifications.internal`,
# NOT a BPMN-declared domain-event topic). Used by the `update_monitoring_plan` raw handler below
# (the item-9 notify-wiring gap: `register_adequacao_workers` used to `del kafka  # unused`)
# whose Kafka publish needs the async seam a `FunctionWorker`'s sync boundary cannot reach
# (`WorkerBase`'s own docstring: "Async I/O is handled by the engine/message layer, not by the
# worker logic").
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

# ---------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------

ERR_FALLBACK_COMMITMENT_NOT_HUMAN = "ERR_FALLBACK_COMMITMENT_NOT_HUMAN"
ERR_ADEQUACAO_CELULA_INVALIDA = "ERR_ADEQUACAO_CELULA_INVALIDA"

# Decision values
DECISAO_COMPROMISSO_FALLBACK = "COMPROMISSO_FALLBACK"


# ---------------------------------------------------------------
# measure_gap — geo-analysis (FACT, no decision)
# ---------------------------------------------------------------

# FABRICATED-MEASUREMENT PLACEHOLDERS. `measure_gap` has no geo/network DB behind it yet, so when no
# correctly-typed fact is seeded it FABRICATES these two numbers (the pre-existing behaviour — see
# `measure_gap`'s own "Placeholder fallback" note). Promoted from function locals to module
# constants for ONE reason: `route_remediation`'s fail-safe has to be able to recognise a
# measurement that is INDISTINGUISHABLE from what this module fabricates, and the two sites must not
# be able to drift apart. Changing a value here changes both sites at once, by construction.
#
# WHY THE PAIR MATTERS (this is the M-1 blind spot): 45min/15.5km sits INSIDE the elective ceiling
# the table declares (60min/50.0km, `_ELETIVO_LEVE_*` below), so a fabricated measurement can never
# trip the owner-ratified ceiling branch — it lands in exactly the band that reads CONFORME and
# terminates at `End_AdequacaoConforme` with NO monitoring plan and NO alert.
_TEMPO_PLACEHOLDER_MIN = 45  # minutes
_DISTANCIA_PLACEHOLDER_KM = 15.5  # km


def measure_gap(variables: dict[str, Any]) -> dict[str, Any]:
    """Measure geographic coverage gap (FACT only — NEVER decides commitment).

    Computes: tempo_acesso_apurado_min, distancia_apurada_km,
    prestadores_disponiveis, cobertura_geo_suficiente, dados_geo_completos.

    FACT PRESERVATION (mirrors `credenciamento.validate_cred`'s FACT PRESERVATION fix — the
    IDENTICAL defect class): the pre-fix worker unconditionally overwrote
    `tempo_acesso_apurado_min`/`distancia_apurada_km` with hardcoded placeholders and
    RE-DERIVED `cobertura_geo_suficiente`/`dados_geo_completos` from OTHER variables — clobbering
    any already-resolved fact on the process BEFORE `BRT_AdequacaoGap`
    (`operadora.adequacao.calculate_gap`, the task that runs immediately after this one) could
    evaluate it. The BPMN's own task documentation says this worker "apura/ECOA" these facts
    (echo, not overwrite). An explicit, correctly-typed resolved value already on `variables` is
    now respected (echoed through unchanged); anything else (absent, wrong type) falls back to
    the SAME placeholder computation as before (real implementation: query geo-location / network
    DB) — `prestadores_disponiveis` was already preserved this way pre-fix and is unchanged here.

    Type-appropriate isinstance guards (engine variables arrive untyped): `tempo_acesso_apurado_min`
    is contract-typed `integer` (SP-OP-ADEQUACAO-001.md:108, ADR-0018 parte 2 "numeros nunca como
    number") — only a Python `int` counts, and `bool` is explicitly EXCLUDED even though `bool` is
    a subclass of `int` in Python (`isinstance(True, int) is True`) — a boolean must NEVER be
    accepted as a numeric measurement. `distancia_apurada_km` is contract-typed `double`
    (SP-OP-ADEQUACAO-001.md:109) — only a Python `float` counts (an `int` distance is a WRONG type
    here, not a resolved fact — falls back to the placeholder, same as absent).
    `cobertura_geo_suficiente`/`dados_geo_completos` are booleans — only `bool` counts, mirroring
    `credenciamento.validate_cred`'s `isinstance(seeded_licenca, bool)` exactly.

    FABRICATION IS NO LONGER SILENT (M-1). The placeholder fallback is KEPT — removing it would
    change what this task completes with, and the geo/network query that replaces it does not exist
    yet — but whenever either number is fabricated, a `adequacao_medidas_fabricadas` WARNING is
    emitted naming which of the two it was. This is the origin-side half of the M-1 fix; the
    action-side half is `route_remediation`'s placeholder-contradiction branch, which refuses to act
    on a `CONFORME` resting on a measurement indistinguishable from these placeholders. NO new
    engine variable is minted (the contract's variable tables are human-gated) — see the log call
    site for the full mechanism note and the `pagto.py` precedent.
    """
    regiao = variables.get("regiao_saude", "")
    especialidade = variables.get("especialidade", "")

    seeded_tempo = variables.get("tempo_acesso_apurado_min")
    seeded_distancia = variables.get("distancia_apurada_km")
    seeded_cobertura = variables.get("cobertura_geo_suficiente")
    seeded_dados_completos = variables.get("dados_geo_completos")

    # Placeholder fallback — real implementation queries geo-location / network DB; only used
    # when no resolved fact of the CORRECT type exists on `variables` (FACT PRESERVATION above).
    prestadores = variables.get("prestadores_disponiveis", 3)

    tempo_fabricado = not (isinstance(seeded_tempo, int) and not isinstance(seeded_tempo, bool))
    distancia_fabricada = not isinstance(seeded_distancia, float)

    tempo_acesso = _TEMPO_PLACEHOLDER_MIN if tempo_fabricado else seeded_tempo
    distancia = _DISTANCIA_PLACEHOLDER_KM if distancia_fabricada else seeded_distancia
    cobertura_suficiente = seeded_cobertura if isinstance(seeded_cobertura, bool) else prestadores >= 2
    dados_completos = (
        seeded_dados_completos if isinstance(seeded_dados_completos, bool) else bool(regiao and especialidade)
    )

    if tempo_fabricado or distancia_fabricada:
        # VISIBILITY LEG 1 of 2 (M-1): the fabrication is announced AT ITS ORIGIN, loudly, instead
        # of being silently indistinguishable from a real geo/network reading. Deliberately a
        # `warning`, not `info`: a measurement nobody measured is an operational defect, not
        # routine progress. LEG 2 is `route_remediation`'s own placeholder-contradiction branch,
        # which refuses to ACT on a CONFORME resting on these numbers.
        #
        # NOT an engine variable, and that is the deliberate choice (M-1 mechanism note). Minting
        # `medidas_fabricadas` would add an UNDECLARED variable to a contracted process, whose
        # variable tables are human-gated — SP-OP-ADEQUACAO-001.md:100-113 (entrada) and :123-133
        # (saida) declare neither it nor any equivalent flag. Same call, same reasoning, already
        # taken and disclosed in `pagto.calculate_facts` (pagto.py:318-326: "Minting one would add
        # an undeclared variable to a contracted process (the contract's variable table is
        # human-gated)"). The operator log carries the evidence; the fail-safe carries the refusal.
        # Declaring the variable is the contract owner's act — deferred, not denied.
        logger.warning(
            "adequacao_medidas_fabricadas",
            regiao_saude=regiao,
            especialidade=especialidade,
            tempo_fabricado=tempo_fabricado,
            distancia_fabricada=distancia_fabricada,
            tempo_acesso_min=tempo_acesso,
            distancia_km=distancia,
        )

    logger.info(
        "adequacao_measure_gap",
        regiao_saude=regiao,
        especialidade=especialidade,
        tempo_acesso_min=tempo_acesso,
        distancia_km=distancia,
        prestadores=prestadores,
    )

    return {
        "tempo_acesso_apurado_min": tempo_acesso,
        "distancia_apurada_km": distancia,
        "prestadores_disponiveis": prestadores,
        "cobertura_geo_suficiente": cobertura_suficiente,
        "dados_geo_completos": dados_completos,
    }


# ---------------------------------------------------------------
# route_remediation — classify gap and route (no commitment decision)
# ---------------------------------------------------------------


# GAP-ADEQ-INVERSAO (decisao do dono, 2026-08-06): tetos LIDOS da propria tabela deployada
# `adequacao_gap.dmn`, regra `r_eletivo_leve` (`tempo <= 60`, `distancia <= 50.0`). NAO sao valores
# novos nem inventados — sao o unico teto de acesso eletivo que a tabela ja declara. Servem apenas
# para RECUSAR agir sobre um CONFORME que a propria tabela produziu por queda no `r_conforme` sem
# teto (que precede nenhuma regra `*_critico` eletiva por tempo/distancia).
_ELETIVO_LEVE_TEMPO_MAX_MIN = 60
_ELETIVO_LEVE_DISTANCIA_MAX_KM = 50.0
_MOTIVO_CONFORME_RECUSADO = "CONFORME_RECUSADO_ACESSO_ACIMA_DO_TETO_LEVE"

# M-1 (blind spot of the ratified fail-safe above). VOCABULARIO FECHADO de `tipo_carater`, LIDO da
# tabela deployada `adequacao_gap.dmn` — nao inventado. A coluna de entrada `in_tipo_carater`
# (adequacao_gap.dmn:33-35) e literalizada em exatamente 4 das 8 rows, com apenas DOIS literais
# distintos:
#     "urgencia_emergencia" -> adequacao_gap.dmn:52 (r_urgencia_critico),
#                              :62 (r_urgencia_distancia_critico),
#                              :102 (r_urgencia_leve)
#     "eletivo"             -> adequacao_gap.dmn:92 (r_eletivo_leve)
# As outras 4 rows curinga a coluna (`-`): :72 (r_zero_prestadores_critico),
# :82 (r_cobertura_insuficiente), :112 (r_conforme), :122 (r_catchall) — curinga NAO declara
# literal. Logo o vocabulario que a tabela declara e, exaustivamente, {eletivo,
# urgencia_emergencia}. Corroborado (nao derivado dai) pelo dominio do proprio contrato,
# docs/processes/contracts/SP-OP-ADEQUACAO-001.md:113, e pela transcricao do shadow
# (adequacao_shadow.TIPO_ELETIVO/TIPO_URGENCIA, adequacao_shadow.py:99-100).
_TIPO_CARATER_VOCABULARIO: frozenset[str] = frozenset({"eletivo", "urgencia_emergencia"})
_MOTIVO_CONFORME_CARATER_DESCONHECIDO = "CONFORME_RECUSADO_CARATER_FORA_DO_VOCABULARIO"
_MOTIVO_CONFORME_MEDIDAS_FABRICADAS = "CONFORME_RECUSADO_MEDIDAS_INDISTINGUIVEIS_DE_PLACEHOLDER"


def _record_gap_shadow(
    *,
    gap_adequacao: str,
    tempo: int,
    distancia: float,
    prestadores: int,
    cobertura: bool,
    tipo_carater: str,
) -> None:
    """SHADOW SEAM — observation only, structurally incapable of changing anything here.

    Records what the CANDIDATE rule set (`spec/processes/dmn/adequacao-gap-shadow-candidate.yaml`,
    the correction the REGULATORY OWNER must ratify and apply — PLANS.md:150-159) WOULD have
    verdicted, when that diverges from the live DMN verdict. Same idea as
    `auth.ValidateAutoApprovalCriteria`'s `auto_criteria_shadow`: the reviewer ratifies against real
    outcome data instead of reviewing rules in the abstract.

    THREE INDEPENDENT REASONS THIS CANNOT INFLUENCE THE VERDICT OR THE ROUTE, so that a regression
    has to defeat all three:
      1. it returns `None`, and the caller never binds its result to anything;
      2. it writes no engine variable — `route_remediation`'s returned dict is assembled without
         reference to it;
      3. EVERY exception is swallowed here, so even a broken evaluator degrades to a log line.
    Pinned by `test_adequacao_shadow.py` (neutralised/raising/absent evaluator -> identical
    outcomes across a behaviour matrix).

    NEVER a correction of the table: per ADR-0028 (docs/adr/0028-dmn-evaluation-engine-side.md:
    214-216) the DMN wins and engineering never patches it back.
    """
    try:
        record_shadow_divergence(
            gap_adequacao_dmn=gap_adequacao,
            tipo_carater=tipo_carater,
            tempo_acesso_apurado_min=tempo,
            distancia_apurada_km=distancia,
            prestadores_disponiveis=prestadores,
            cobertura_geo_suficiente=cobertura,
        )
    except Exception as exc:  # noqa: BLE001 — reason 3 above: shadow never disturbs the worker.
        logger.warning("adequacao_gap_shadow_falhou", error=str(exc))


def route_remediation(variables: dict[str, Any], *, dmn: DmnTransport | None = None) -> dict[str, Any]:
    """Classify gap severity and route remediation — NEVER commits to fallback.

    Evaluates TWO chained deployed decision tables (ADR-0028/T1.5): `adequacao_gap` (tipo_carater,
    tempo_acesso_apurado_min, distancia_apurada_km, prestadores_disponiveis,
    cobertura_geo_suficiente) -> `gap_adequacao` in {CONFORME, GAP_LEVE, GAP_MODERADO,
    GAP_CRITICO}; then, UNCONDITIONALLY, `adequacao_remediation_routing` (gap_adequacao,
    dados_geo_completos) -> `roteamento_remediacao`.

    `tipo_carater` is a NEW input this function did not previously read at all (additive —
    sourced directly from `variables`, defaulting to `""` when absent).

    golden-parity divergence found + DMN wins (documented, not patched — ADR-0028 §7): the
    deployed `adequacao_gap` table's rule ORDER makes `GAP_LEVE` win over `CONFORME` for
    `tipo_carater="eletivo"` whenever `tempo_acesso_apurado_min<=60` and
    `distancia_apurada_km<=50.0` (its own row precedes `CONFORME`'s in FIRST-hit-policy order) —
    live-verified: `tempo=15, distancia=5.0, prestadores=5, cobertura=True` -> `GAP_LEVE`, not
    `CONFORME` (the old Python's own tighter `tempo<=30`/`distancia<=20.0` gate for CONFORME).
    `GAP_MODERADO` is also computed differently (DMN keys off `cobertura_geo_suficiente=false`;
    old Python keyed off `prestadores>=1 and tempo<=90`). Values stay within the same neutral
    closed set with no adverse output on either path — both `CONFORME` and `GAP_LEVE` route to
    the SAME `MONITORAR` remediation (live-verified), so this divergence has zero effect on the
    downstream remediation action. **CORRIGIDO 2026-08-06 (GK-adequacao finding 2): essa ultima
    frase e FALSA no nivel do BPMN** — `GW_Roteamento` distingue os dois
    (`MONITORAR && gap_adequacao == 'CONFORME'` vai a `ST_PublishConforme`/`End_AdequacaoConforme`,
    SEM plano de monitoramento e SEM alerta; `MONITORAR && != 'CONFORME'` vai a
    `ST_UpdateMonitoringPlanL3`). Mesmo valor de roteamento, ramos e efeitos colaterais
    DIFERENTES. Ver o fail-safe abaixo.

    TRES CONTRADICOES, UMA SO FORMA (M-1). O fail-safe abaixo tem hoje tres ramos, todos gateados
    em `gap_adequacao == "CONFORME"`, todos preservando o veredito da DMN e recusando apenas AGIR
    sobre ele, e todos com o mesmo destino (`ANALISE_HUMANA`) sob `motivo` DISTINTOS:
      1. `_MOTIVO_CONFORME_RECUSADO` — acesso acima do teto eletivo que a tabela declara (ramo
         RATIFICADO PELO DONO em 2026-08-06; inalterado por M-1);
      2. `_MOTIVO_CONFORME_CARATER_DESCONHECIDO` — `tipo_carater` fora do vocabulario fechado que a
         tabela declara ({eletivo, urgencia_emergencia}), inclusive a string vazia que este proprio
         `variables.get("tipo_carater", "")` produz quando a variavel esta ausente;
      3. `_MOTIVO_CONFORME_MEDIDAS_FABRICADAS` — medidas indistinguiveis dos placeholders que
         `measure_gap` fabrica quando nao ha fato tipado semeado.
    Os ramos 2 e 3 sao `elif` do ramo 1: alcancam SO o que o ramo ratificado ja deixava passar.
    NENHUM alcanca um veredito != CONFORME. A tabela NAO e tocada em nenhum dos tres (ADR-0028): a
    correcao definitiva de ordem/tetos/vocabulario continua sendo do portao REGULATORIO — o
    conjunto candidato ja a descreve em spec/processes/dmn/adequacao-gap-shadow-candidate.yaml
    (ACHADO-1 e a perna de TABELA do mesmo defeito que o ramo 2 fecha em runtime).
    """
    tempo = variables.get("tempo_acesso_apurado_min", 0)
    distancia = variables.get("distancia_apurada_km", 0.0)
    prestadores = variables.get("prestadores_disponiveis", 0)
    cobertura = variables.get("cobertura_geo_suficiente", False)
    dados_completos = variables.get("dados_geo_completos", True)
    tipo_carater = variables.get("tipo_carater", "")

    dmn_transport = require_dmn(dmn, "operadora.adequacao.calculate_gap")

    gap_rows, gap_version = evaluate_sync(
        dmn_transport,
        "adequacao_gap",
        {
            "tipo_carater": tipo_carater,
            "tempo_acesso_apurado_min": int(tempo),
            "distancia_apurada_km": float(distancia),
            "prestadores_disponiveis": int(prestadores),
            "cobertura_geo_suficiente": bool(cobertura),
        },
    )
    gap_row = first_row(gap_rows, "adequacao_gap", variables)
    gap_adequacao = str(gap_row.get("gap_adequacao", "GAP_CRITICO"))
    motivo = str(gap_row.get("motivo", ""))

    route_rows, route_version = evaluate_sync(
        dmn_transport,
        "adequacao_remediation_routing",
        {"gap_adequacao": gap_adequacao, "dados_geo_completos": bool(dados_completos)},
    )
    route_row = first_row(route_rows, "adequacao_remediation_routing", variables)
    roteamento = str(route_row.get("roteamento_remediacao", "ANALISE_HUMANA"))
    route_motivo = str(route_row.get("motivo", motivo))

    # SHADOW (observation only — see `_record_gap_shadow`): what the CANDIDATE rule set
    # (spec/processes/dmn/adequacao-gap-shadow-candidate.yaml) would have verdicted. Returns None,
    # binds nothing, writes no engine variable, swallows every exception. Placed HERE so it observes
    # the DMN's own verdict, before the fail-safe below touches the routing.
    _record_gap_shadow(
        gap_adequacao=gap_adequacao,
        tempo=int(tempo),
        distancia=float(distancia),
        prestadores=int(prestadores),
        cobertura=bool(cobertura),
        tipo_carater=tipo_carater,
    )

    # FAIL-SAFE (GAP-ADEQ-INVERSAO): a tabela deployada tem `r_conforme` SEM teto de
    # tempo/distancia, colocado DEPOIS de `r_eletivo_leve` sob hitPolicy=FIRST — logo um acesso
    # eletivo ARBITRARIAMENTE ruim (ex.: 200min/80km) NAO casa o `r_eletivo_leve` e cai em
    # CONFORME, terminando em End_AdequacaoConforme SEM plano de monitoramento e SEM alerta a
    # gestao-rede. Antes da preservacao de fatos isso ficava mascarado (o worker sobrescrevia
    # 45min/15.5km e o caso virava GAP_LEVE, monitorado). Aqui NAO reescrevemos o veredito da DMN
    # (`gap_adequacao` continua o que ela disse — auditavel); recusamos AGIR sobre ele: o
    # roteamento vai para ANALISE_HUMANA. Mesma forma da aplicacao do teto na emissao de
    # autorizacao: nao se corrige a tabela, torna-se o limite que ela ja declara efetivo.
    # A correcao definitiva (ordem/tetos das regras) e do portao REGULATORIO — RN 259.
    conforme = gap_adequacao == "CONFORME"
    conforme_contradito = conforme and (
        int(tempo) > _ELETIVO_LEVE_TEMPO_MAX_MIN or float(distancia) > _ELETIVO_LEVE_DISTANCIA_MAX_KM
    )
    # F-1 (GK REVISE): EXACT-match coercion, not `_norm_str`'s strip(). The DMN compares FEEL
    # literals exactly, so a padded token (" eletivo ") is ALREADY out-of-vocabulary at the table
    # (misses every literal row, falls through the `-` wildcards straight to `r_conforme`/
    # `r_catchall`) — stripping it here before the vocabulary check would silently ACCEPT what the
    # table itself refuses, reopening the exact M-1 hole this fail-safe closes. Non-strings coerce
    # to "" (a bare `in` check against a non-hashable member, e.g. a `list`, raises `TypeError:
    # unhashable type` on a `frozenset`) — same fail-closed outcome as `_norm_str`, without
    # trimming whitespace that the table would never trim either.
    carater_literal = tipo_carater if isinstance(tipo_carater, str) else ""
    if conforme_contradito:
        logger.warning(
            "adequacao_conforme_recusado_por_acesso",
            gap_adequacao_dmn=gap_adequacao,
            tempo_acesso_apurado_min=int(tempo),
            distancia_apurada_km=float(distancia),
            roteamento_dmn=roteamento,
            motivo=_MOTIVO_CONFORME_RECUSADO,
        )
        roteamento = "ANALISE_HUMANA"
        route_motivo = _MOTIVO_CONFORME_RECUSADO

    # M-1 — DUAS CEGUEIRAS do fail-safe ratificado acima, fechadas com a MESMA forma (preserva o
    # veredito da DMN, recusa AGIR sobre ele) e SEM tocar na tabela.
    #
    # ORDEM DELIBERADA: os dois ramos abaixo sao `elif` do ramo ratificado, entao so alcancam
    # entradas em que ele JA ESTAVA SILENCIOSO. Nenhuma entrada que ele hoje recusa muda de
    # roteamento nem de `motivo` — o diff e estritamente aditivo sobre o artefato ratificado.
    # Nenhum dos dois alcanca um veredito != CONFORME (todos gateados por `conforme`), entao as
    # rotas de urgencia/GAP_* seguem byte-identicas.
    #
    # (a) CARATER FORA DO VOCABULARIO. O ramo ratificado aplica o teto ELETIVO (60min/50km) a
    #     QUALQUER carater. Quando o carater nao e um dos dois literais que a tabela declara
    #     (`_TIPO_CARATER_VOCABULARIO`), nao existe teto aplicavel: escolher o eletivo seria
    #     assumir que o caso e eletivo, e escolher o de urgencia (30min/30km) seria inventar o
    #     enquadramento pelo lado oposto. Entao 45min/40km com `tipo_carater=""` — precisamente o
    #     que o processo envia quando a variavel esta ausente (`variables.get(..., "")` acima) —
    #     lia CONFORME e terminava em `End_AdequacaoConforme` SEM plano de monitoramento e SEM
    #     alerta, enquanto as MESMAS medidas sob `urgencia_emergencia` sao GAP_CRITICO. A propria
    #     `<description>` da tabela promete GAP_CRITICO para "combinacao nao coberta"
    #     (adequacao_gap.dmn:22-23, :121), mas `r_conforme` (:110-119) precede o catch-all com
    #     curinga em tipo_carater/tempo/distancia e torna essa promessa inalcancavel. Ja RECORDADO
    #     como ACHADO-1 no candidato W3 (spec/processes/dmn/adequacao-gap-shadow-candidate.yaml);
    #     esta e a perna de RUNTIME do mesmo achado. Fail-closed tambem para carater NAO-string
    #     (variaveis do engine chegam sem tipo): `carater_literal` vira "" e "" nao esta no
    #     vocabulario. NAO alteramos o que foi enviado a DMN — a normalizacao vive so aqui.
    #
    # (b) MEDIDAS INDISTINGUIVEIS DO PLACEHOLDER. `measure_gap` fabrica o PAR 45min/15.5km quando
    #     nao ha nenhum fato tipado semeado (`_TEMPO_PLACEHOLDER_MIN`/`_DISTANCIA_PLACEHOLDER_KM`),
    #     e esse par cai DENTRO do teto eletivo — logo o ramo ratificado nunca o alcanca. Um
    #     CONFORME apoiado em numero que ninguem mediu nao e conformidade apurada. A afirmacao e
    #     literalmente "indistinguivel", nao "fabricado": quando esta tarefa roda, `measure_gap` ja
    #     escreveu o fato de volta ao processo, entao o worker NAO consegue mais separar uma medida
    #     real de 45min+15.5km de uma fabricada. Recusar e o lado conservador da assimetria (falso
    #     positivo = uma celula CONFORME revista por humano; falso negativo = uma celula sem
    #     monitoramento).
    #
    #     SUB-INCLUSAO DECLARADA: `and`, nao `or` — exige o PAR exato. A fabricacao dos dois campos
    #     e independente (um pode vir semeado e o outro nao), entao o meio-termo (so o tempo, ou so
    #     a distancia, fabricado) NAO cai aqui. `or` foi rejeitado por custo/beneficio explicito:
    #     na TABELA VIVA os dois casos meio-fabricados sao inalcancaveis como CONFORME
    #     (eletivo dentro do teto casa `r_eletivo_leve` primeiro, adequacao_gap.dmn:90-99; urgencia
    #     nunca chega a CONFORME, ACHADO-2 do candidato), sobrando so a rota de carater
    #     branco/desconhecido — que o ramo (a) ja fecha. `or` compraria zero cobertura real hoje e
    #     recusaria toda medida GENUINA de 45min. Se o dono adotar a DECISAO-A opcao (a) do
    #     candidato (uma regra CONFORME com teto), reavaliar este `and`.
    #
    #     A visibilidade na ORIGEM e o warning `adequacao_medidas_fabricadas` em `measure_gap`.
    elif conforme and carater_literal not in _TIPO_CARATER_VOCABULARIO:
        logger.warning(
            "adequacao_conforme_recusado_por_carater_desconhecido",
            gap_adequacao_dmn=gap_adequacao,
            tipo_carater=repr(tipo_carater),
            vocabulario_declarado=sorted(_TIPO_CARATER_VOCABULARIO),
            tempo_acesso_apurado_min=int(tempo),
            distancia_apurada_km=float(distancia),
            roteamento_dmn=roteamento,
            motivo=_MOTIVO_CONFORME_CARATER_DESCONHECIDO,
        )
        roteamento = "ANALISE_HUMANA"
        route_motivo = _MOTIVO_CONFORME_CARATER_DESCONHECIDO

    elif conforme and (
        int(tempo) == _TEMPO_PLACEHOLDER_MIN and float(distancia) == _DISTANCIA_PLACEHOLDER_KM
    ):
        logger.warning(
            "adequacao_conforme_recusado_por_medidas_fabricadas",
            gap_adequacao_dmn=gap_adequacao,
            tipo_carater=_norm_str(tipo_carater),
            tempo_acesso_apurado_min=int(tempo),
            distancia_apurada_km=float(distancia),
            tempo_placeholder_min=_TEMPO_PLACEHOLDER_MIN,
            distancia_placeholder_km=_DISTANCIA_PLACEHOLDER_KM,
            roteamento_dmn=roteamento,
            motivo=_MOTIVO_CONFORME_MEDIDAS_FABRICADAS,
        )
        roteamento = "ANALISE_HUMANA"
        route_motivo = _MOTIVO_CONFORME_MEDIDAS_FABRICADAS

    logger.info(
        "adequacao_route_remediation",
        gap_adequacao=gap_adequacao,
        roteamento=roteamento,
        dmn_gap_version=gap_version.version,
        dmn_route_version=route_version.version,
    )

    return {
        "gap_adequacao": gap_adequacao,
        "roteamento_remediacao": roteamento,
        "motivo": route_motivo,
    }


# ---------------------------------------------------------------
# notify_coordenacao — neutral notification
# ---------------------------------------------------------------


def notify_coordenacao(variables: dict[str, Any]) -> dict[str, Any]:
    """Log the network-coordination alert step for a gap under monitoring (NEUTRAL).

    GAP-FAB-NOTIF fix: this handler previously returned a constant `{"notificacao_enviada":
    True}` regardless of input — a fabricated fact (no channel, no publisher) that had ZERO
    consumers anywhere in BPMN/DMN (`grep -rn 'notificacao_enviada' src/ spec/` found only this
    line producing it) and is not part of the contract's documented output-variable set
    (`docs/processes/contracts/SP-OP-ADEQUACAO-001.md` §Variaveis de saida). BPMN `ST_NotifyRedeL3`
    ("Notificar a area de rede (L3)") only documents this as an informational alert to the
    internal `gestao-rede` group — it never claimed delivery/receipt, so there was nothing this
    handler needed to invent. The real domain event for this branch
    (`agents.events.adequacao.completed`, `desfecho=monitoramento_atualizado`) is published by the
    NEXT BPMN task, `ST_PublishMonitoramentoAtualizado`, via the generic `operadora.events.publish`
    worker — not here. This function only logs; it returns nothing beyond what is true.
    """
    regiao = variables.get("regiao_saude", "")
    gap = variables.get("gap_adequacao", "")

    logger.info(
        "adequacao_notify_coordenacao",
        regiao_saude=regiao,
        gap=gap,
        grupo_alertado="gestao-rede",
    )

    return {}


# ---------------------------------------------------------------
# execute_remediation — handoff to CRED-001 (NEUTRAL)
# ---------------------------------------------------------------


def execute_remediation(variables: dict[str, Any]) -> dict[str, Any]:
    """Initiate credentialing to close the gap (handoff to CRED-001 — NEUTRAL).

    This is L3 — buscar prestador nao e adverso.
    """
    logger.info(
        "adequacao_execute_remediation",
        regiao_saude=variables.get("regiao_saude"),
        especialidade=variables.get("especialidade"),
    )

    return {
        "handoff_credenciamento": True,
        "processo_destino": "SP-OP-CRED-001",
    }


# ---------------------------------------------------------------
# update_monitoring_plan — L3 monitoring, NEUTRAL (no adverse effect)
# ---------------------------------------------------------------


def update_monitoring_plan(variables: dict[str, Any]) -> dict[str, Any]:
    """Open/update the cell's (regiao x especialidade) monitoring plan.

    External task: `operadora.adequacao.update_monitoring_plan` (`ST_UpdateMonitoringPlanL3`,
    SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn:197-203). First step of the GAP_LEVE/MONITORAR L3
    branch (reached from the initial routing gateway `GW_Monitorar` OR from
    `GWDec_MonitorarOk` — the human choosing `decisao_remediacao=MONITORAR_OK` at
    `UT_DecisaoFallback` instead of committing a fallback).

    NEUTRAL, per the BPMN's own task documentation (bpmn:documentation, line 199):
    "Monitoramento/alerta — NAO compromete caixa nem nega atendimento" (monitoring/alert only —
    does NOT commit cash-flow nor deny care) — mirrors the contract's own framing (GAP-ADEQ-6,
    docs/processes/contracts/SP-OP-ADEQUACAO-001.md:149). TASY write DROP (same doc line). No
    human gate: unlike `register_fallback_commitment`, this is a clerical/monitoring action, not
    an adverse effect.
    """
    regiao = variables.get("regiao_saude", "")
    especialidade = variables.get("especialidade", "")
    gap = variables.get("gap_adequacao", "")

    logger.info(
        "adequacao_update_monitoring_plan",
        regiao_saude=regiao,
        especialidade=especialidade,
        gap_adequacao=gap,
    )

    return {
        "plano_monitoramento_atualizado": True,
        "celula": f"{regiao}:{especialidade}",
        "gap_adequacao": gap,
    }


_UPDATE_MONITORING_PLAN_NOTIFICATION_TYPE = "adequacao.update_monitoring_plan"


# GK-adequacao finding 3 (divulgacao): mover `update_monitoring_plan` de `FunctionWorker`
# para raw handler contorna `WorkerBase.run`, entao as metricas M11 POR WORKER
# (`record_worker_execution`/`record_worker_error`) deixam de ser emitidas para este topico.
# O `_emit_worker_task_outcome` do harness continua disparando. Mesmo trade-off ja aceito e
# divulgado em programa.py para os seus 4 raw handlers.
def make_update_monitoring_plan_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Raw-handler factory for `operadora.adequacao.update_monitoring_plan` (serves
    `ST_UpdateMonitoringPlanL3`).

    `register_adequacao_workers` previously `del kafka  # unused` — no adequacao.py worker ever
    declared a Kafka dependency, so `update_monitoring_plan` (the ONLY topic in this family whose
    BPMN documentation implies a downstream-observable side effect, GAP-ADEQ-6) silently never
    published anything. This closes that gap — mirrors `programa.make_notify_sla_risk_handler`/
    `recurso.make_notify_sla_risk_handler`'s exact idiom (a raw `harness.register()` handler is
    needed for the async Kafka seam a sync `FunctionWorker.execute` boundary cannot reach;
    `update_monitoring_plan` itself, the pure function above, is UNCHANGED).

    NEUTRAL, non-PHI payload — `tenant_id`/`regiao_saude`/`especialidade`/`gap_adequacao` (the
    cell identity + the SAME 3 input fields the pure function already reads off `variables`, plus
    the pure function's own `gap_adequacao` passthrough); this process carries no beneficiary
    identifier at all (geography is region/municipio granularity only — ADR-0006).
    `best_effort=False`: no BPMN error boundary is declared on `ST_UpdateMonitoringPlanL3` -> RAW
    propagate a broker failure to the harness retry/incident ladder (ADR-0030) instead of silently
    swallowing it, mirroring every sibling `_NOTIFICATIONS_TOPIC` publisher in this codebase.
    `kafka=None` (no producer wired) logs a warning and still completes the task — the L3
    monitoring-plan update itself is NEVER blocked by a missing producer.
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        result = update_monitoring_plan(task.variables)
        if kafka is None:
            logger.warning("adequacao_update_monitoring_plan_no_producer", business_key=task.business_key)
            return result
        notification = {
            "type": _UPDATE_MONITORING_PLAN_NOTIFICATION_TYPE,
            "tenant_id": task.variables.get("tenant_id", ""),
            "regiao_saude": task.variables.get("regiao_saude", ""),
            "especialidade": task.variables.get("especialidade", ""),
            "gap_adequacao": result.get("gap_adequacao", ""),
        }
        # best_effort=False — see factory docstring (no boundary declared -> raw propagate).
        # GAP-SC-04-a: the partition key comes from the ONE shared chain (task business key ->
        # payload anchors -> `{tenant}|{process_instance_id}`), never from `task.business_key or
        # None` — that idiom degraded a blank business key into an UNKEYED publish, i.e.
        # round-robin across the topic's 3 default partitions and no per-entity ordering. Hoisted
        # above the publish so a `PseudonymizerKeyMissingError` (ratified `scrub_only` with no
        # provisioned `PHI_HMAC_KEY`) stays a configuration fault, never a broker diagnosis.
        message_key = partition_key_for_task(task, _NOTIFICATIONS_TOPIC, notification)
        await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=message_key, best_effort=False)
        return result

    return handler


# ---------------------------------------------------------------
# notify_sla_risk — informational SLA alert (non-interruptive timer)
# ---------------------------------------------------------------


def notify_sla_risk(variables: dict[str, Any]) -> dict[str, Any]:
    """Alert coordenacao-rede of SLA risk (non-interruptive timer BT_AlertaSlaAdequacao).

    External task: `operadora.adequacao.notify_sla_risk` (`ST_NotificarRiscoSla`,
    SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn:287-291 — task name "Notificar risco de SLA
    (coordenacao-rede)"). Fires at `${sla.sla_alerta}` (60-70% of `adequacao_sla`'s
    `sla_alerta`, per the contract, DRAFT/verify — docs/processes/contracts/
    SP-OP-ADEQUACAO-001.md:153,225) on the non-interruptive boundary event
    (cancelActivity="false") attached to `UT_DecisaoFallback`.

    Informational only (mirrors `inadimplencia.notify_sla_risk`/`cancel.notify_sla_risk`/
    `fraude.notify_sla_risk`): UT_DecisaoFallback stays open, no decision is made or altered,
    NO adverse outcome (fallback commitment or otherwise) is ever produced by this alert.
    The fallback commitment NEVER arises from a timer -- only the human decision at
    UT_DecisaoFallback (`register_fallback_commitment`'s own guard, unchanged) does.
    """
    regiao = variables.get("regiao_saude", "")
    especialidade = variables.get("especialidade", "")

    logger.info(
        "adequacao_notify_sla_risk",
        regiao_saude=regiao,
        especialidade=especialidade,
        grupo_alertado="coordenacao-rede",
    )

    return {
        "sla_risk_notified": True,
        "grupo_alertado": "coordenacao-rede",
        "regiao_saude": regiao,
        "especialidade": especialidade,
    }


# ---------------------------------------------------------------
# register_fallback_commitment — GATED adverse effect
# ---------------------------------------------------------------


def _norm_str(value: Any) -> str:
    """Normalize an engine variable to a stripped string for guard checks — FAIL-CLOSED.

    Mirrors `pagto._norm_str`/`pagto.register_payment_refusal`'s fix (t2.5-p2b-round2, then
    t3.1-guard-input-hardening for `release_high_value_payment`): the pre-fix bare
    `if not tipo_fallback` / `if not justificativa` / ... checks let WHITESPACE-ONLY decision +
    accountability fields pass the guard — defeating ADR-0007/RN 259 (a fallback commitment
    recorded with a non-identifying approver or a blank justification/regulatory reference).
    Closes that class:
    - a `str` normalizes to `value.strip()` — whitespace-only ("   ", "\\t", "\\n", ...)
      becomes "" and is treated EXACTLY like an absent field (refusal, never registration);
    - a NON-string (None, int, bool, list, dict — engine variables arrive untyped) normalizes
      to "" (fail-closed refusal), never a truthy pass-through and never an AttributeError
      incident from calling `.strip()` on a non-string.
    """
    if isinstance(value, str):
        return value.strip()
    return ""


def register_fallback_commitment(variables: dict[str, Any]) -> dict[str, Any]:
    """Register a financial fallback commitment (livre escolha/reembolso).

    GUARDED: ERR_FALLBACK_COMMITMENT_NOT_HUMAN.
    This is the ONLY adverse effect in this L3-majority process.

    NORMALIZATION (t3.1-guard-input-hardening, closing the bare-truthiness gap noted in the
    #133 audit — adequacao.py:289-295 pre-fix): ALL decision + human-accountability fields
    (`decisao_remediacao`, `tipo_fallback`, `justificativa_fallback`, `referencia_regulatoria`,
    `responsavel_id`) are normalized via `_norm_str` (strip; non-string -> "") BEFORE any guard
    check, mirroring `pagto.register_payment_refusal`'s fix. Consequences, all fail-closed:
    - whitespace-only `tipo_fallback`/`justificativa_fallback`/`referencia_regulatoria`/
      `responsavel_id` REFUSES exactly like an absent field;
    - a whitespace-PADDED but otherwise exact `decisao_remediacao` literal
      ("COMPROMISSO_FALLBACK ") normalizes to the literal and still passes Guard 1 (still
      subject to the accountability-field checks) — case variants/substrings still refuse
      (exact `!=` match, no folding);
    - a non-string in ANY of these fields normalizes to "" (refusal), never a truthy
      pass-through and never an AttributeError incident.
    """
    decisao = _norm_str(variables.get("decisao_remediacao", ""))
    tipo_fallback = _norm_str(variables.get("tipo_fallback", ""))
    justificativa = _norm_str(variables.get("justificativa_fallback", ""))
    ref_regulatoria = _norm_str(variables.get("referencia_regulatoria", ""))
    responsavel_id = _norm_str(variables.get("responsavel_id", ""))

    errors: list[str] = []

    if decisao != DECISAO_COMPROMISSO_FALLBACK:
        errors.append(f"decisao_remediacao != {DECISAO_COMPROMISSO_FALLBACK} (got: {decisao!r})")
    if not tipo_fallback:
        errors.append("tipo_fallback ausente")
    if not justificativa:
        errors.append("justificativa_fallback ausente")
    if not ref_regulatoria:
        errors.append("referencia_regulatoria ausente (RN 259)")
    if not responsavel_id:
        errors.append("responsavel_id ausente (ADR-0007)")

    if errors:
        logger.error(
            "adequacao_fallback_guard_rejected",
            errors=errors,
        )
        raise AdequacaoError(ERR_FALLBACK_COMMITMENT_NOT_HUMAN, "; ".join(errors))

    logger.info(
        "adequacao_fallback_commitment_registered",
        tipo_fallback=tipo_fallback,
        responsavel_id=responsavel_id,
    )

    return {
        "compromisso_fallback_registrado": True,
        "estimativa_custo_cents": variables.get("estimativa_custo_cents", 0),
    }


# ---------------------------------------------------------------
# prepare_remediation_dossier — REAL Andre A2A delegation (raw async handler; DL-0033 closed,
# DL-0037)
# ---------------------------------------------------------------


def make_prepare_remediation_dossier_handler(
    dispatcher: DelegationDispatcher | None,
) -> TaskHandler:
    """Create the handler for `operadora.adequacao.prepare_remediation_dossier` — the REAL Andre
    A2A delegation.

    Serves `ST_PrepareRemediationDossier` (ANALISE_HUMANA branch; also reached on the GAP-ADEQ-3
    `seguir_analise` re-entry). Replaces the DL-0033 local stub with
    `delegate_adequacao_dossier` -> `DelegationDispatcher.delegate` -> Andre's REAL graph
    (SHARED task_type `analytics.population`, disambiguated into his `adequacao_dossier` flow by
    the `adequacao-worker` origin — `agents/andre/delegation.py`).

    RAW ASYNC HANDLER (DL-0034 precedent): `dispatcher.delegate` is async; the raw
    `harness.register()` form runs on the harness's own loop. Populates `_handlers` but NOT the
    `WorkerRegistry` (see `test_bootstrap_registration.py`'s `raw_handler_topics`).

    FAIL-NEUTRAL-WITH-DISCLOSED-GAP (DL-0037): the dossier INSTRUCTS `UT_DecisaoFallback`
    ("instrui, nao decide" — SP-OP-ADEQUACAO-001). A missing dispatcher, missing cell identity,
    a structured rejection or ANY delegation failure returns
    `{"dossier_prepared": False, "dossier_gap": <bounded reason token>}` + a LOUD log and
    COMPLETES the task — the human UT MUST still open; this handler NEVER raises. The gap token
    is a bounded class token (engine-variable hygiene) — raw error text stays in the log.

    Idempotency note (disclosed): the delegation `task_id` is the cell key
    `ADEQ-{tenant}-{regiao}-{especialidade}[-{ciclo}]` — the `seguir_analise` re-entry for the
    SAME cell/ciclo receives the idempotent REPLAY of the same dossier; a genuinely new
    evaluation cycle carries a new `ciclo_avaliacao` (a new task_id, a fresh dossier).
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        v = task.variables
        tenant_id = str(v.get("tenant_id", "") or "")
        regiao_saude = str(v.get("regiao_saude", "") or "")
        especialidade = str(v.get("especialidade", "") or "")
        ciclo_avaliacao = str(v.get("ciclo_avaliacao", "") or "").strip() or None

        if dispatcher is None:
            # Degraded runtime (DL-0037): dispatcher absent at composition (no signing key /
            # no DATABASE_URL — worker_runtime readiness reports dossier_delegation_ready=false).
            logger.warning(
                "adequacao_prepare_remediation_dossier_dispatcher_unavailable",
                tenant_id=tenant_id,
                regiao_saude=regiao_saude,
                especialidade=especialidade,
                business_key=task.business_key,
            )
            return {"dossier_prepared": False, "dossier_gap": "dispatcher_unavailable"}

        if not (non_blank(tenant_id) and non_blank(regiao_saude) and non_blank(especialidade)):
            # No well-formed ADEQ cell key can be derived (EB-4 R1 `non_blank` discipline) —
            # never delegate with a degenerate task_id; the UT still opens with the gap disclosed.
            logger.error(
                "adequacao_prepare_remediation_dossier_missing_cell_identity",
                tenant_id=tenant_id,
                regiao_saude=regiao_saude,
                especialidade=especialidade,
                business_key=task.business_key,
            )
            return {"dossier_prepared": False, "dossier_gap": "missing_business_identifiers"}

        from maezo.agents.andre.delegation import delegate_adequacao_dossier

        try:
            result = await delegate_adequacao_dossier(
                dispatcher,
                tenant=tenant_id.strip(),
                regiao_saude=regiao_saude.strip(),
                especialidade=especialidade.strip(),
                case_meta=dict(v),
                ciclo_avaliacao=ciclo_avaliacao,
            )
        except Exception as exc:  # noqa: BLE001 — DL-0037: the UT must open; never raise here.
            logger.error(
                "adequacao_prepare_remediation_dossier_delegation_failed",
                tenant_id=tenant_id,
                regiao_saude=regiao_saude,
                especialidade=especialidade,
                business_key=task.business_key,
                error=str(exc),
            )
            return {"dossier_prepared": False, "dossier_gap": "delegation_failed"}

        if not result.success:
            reason = str(result.rejection_reason or "unknown")
            logger.error(
                "adequacao_prepare_remediation_dossier_delegation_rejected",
                tenant_id=tenant_id,
                regiao_saude=regiao_saude,
                especialidade=especialidade,
                business_key=task.business_key,
                reason=reason,
                detail=result.detail,
            )
            return {"dossier_prepared": False, "dossier_gap": f"delegation_rejected:{reason}"}

        logger.info(
            "adequacao_prepare_remediation_dossier_delegated",
            tenant_id=tenant_id,
            regiao_saude=regiao_saude,
            especialidade=especialidade,
            business_key=task.business_key,
            dossier_ref=result.output_ref,
            idempotent_replay=result.idempotent_replay,
        )
        return {
            "dossier_prepared": True,
            "dossier_ref": result.output_ref or "",
            # UT-FORM SEAM (SME/PO sign-off PENDING): the dossier CONTENT field schema for
            # UT_DecisaoFallback is uncontracted — no dossier field appears in the contract's
            # variable table. `dossier_summary` carries Andre's agent-produced bounded summary
            # tokens AS-IS (route/desfecho/motivo/grupo — never the narrative, never a decision);
            # the full dossier is reachable via `dossier_ref`. Do NOT invent/extend this schema
            # here — it is the human-gated injection point.
            "dossier_summary": dict(result.meta),
        }

    return handler


# ---------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------


class AdequacaoError(Exception):
    """Worker guard error for adequacao adverse effects."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------
# Bootstrap — FunctionWorker adapter (T1.2/ADR-0026 Decisao §2a: dict-first
# functions wrap directly).
#
# Topic mapping vs spec/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn
# (excl. the shared/out-of-scope `operadora.events.publish` — see ADR-0026 §2b
# note on cross-cutting event-publish topics):
#   measure_gap    -> operadora.adequacao.measure_coverage      (spec match: geo-analysis facts)
#   route_remediation -> operadora.adequacao.calculate_gap      (spec match: gap classification)
#   notify_coordenacao -> operadora.adequacao.notify_rede       (spec match: network coordination)
#   execute_remediation -> operadora.adequacao.start_credenciamento (spec match: CRED-001 handoff)
#   register_fallback_commitment -> operadora.adequacao.register_fallback_commitment
#     (exact spec match, GUARDED)
#   make_update_monitoring_plan_handler -> operadora.adequacao.update_monitoring_plan (spec
#     match, NEUTRAL — t2.5-p2b-round2 closed the registry-drift gap; RAW async handler now
#     publishes a `_NOTIFICATIONS_TOPIC` notification too — closes the item-9 `del kafka # unused`
#     notify-wiring gap, GAP-ADEQ-6)
#   notify_sla_risk -> operadora.adequacao.notify_sla_risk (spec match, informational —
#     t2.5-p2b-round2 closed this gap)
#   make_prepare_remediation_dossier_handler -> operadora.adequacao.prepare_remediation_dossier
#     (exact spec match; RAW async handler — the REAL Andre A2A delegation (analytics.population,
#     origin-disambiguated to his adequacao_dossier flow) DL-0033 deferred, now wired. NEUTRAL —
#     instructs UT_DecisaoFallback, never decides; dispatcher absent/failed -> disclosed-gap
#     marker, the UT still opens (DL-0037).)
# ---------------------------------------------------------------


def register_adequacao_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-ADEQUACAO-001 function workers on `harness`.

    `dmn` (ADR-0028 §1 seam) is threaded into `route_remediation` (`adequacao_gap` +
    `adequacao_remediation_routing`, T1.5 cutover) via `functools.partial`. `kafka` is threaded
    into the `update_monitoring_plan` RAW handler (item-9 notify-wiring fix — this used to be
    `del kafka  # unused`; `update_monitoring_plan` is the ONLY adequacao.py worker with a Kafka
    dependency, `notify_sla_risk` stays plain `FunctionWorker`-wrapped, no publish).

    `dossier_dispatcher` (dossier-A2A seam, DL-0033 real wiring) is threaded into the
    `prepare_remediation_dossier` RAW async handler — a `DelegationDispatcher` assembled by the
    worker-runtime composition root (`build_dossier_delegation_dispatcher`). Absent (`None`, the
    topic-probe default and the degraded-runtime posture) the topic still registers and the
    handler fail-neutrals with a disclosed gap (DL-0037) — the human UT always still opens.
    """
    dmn = seams.get("dmn")
    dossier_dispatcher: DelegationDispatcher | None = seams.get("dossier_dispatcher")
    harness.register_worker(FunctionWorker("operadora.adequacao.measure_coverage", measure_gap))
    harness.register_worker(
        FunctionWorker("operadora.adequacao.calculate_gap", functools.partial(route_remediation, dmn=dmn))
    )
    harness.register_worker(FunctionWorker("operadora.adequacao.notify_rede", notify_coordenacao))
    harness.register_worker(FunctionWorker("operadora.adequacao.start_credenciamento", execute_remediation))
    harness.register_worker(
        FunctionWorker("operadora.adequacao.register_fallback_commitment", register_fallback_commitment)
    )
    # RAW handler (NOT register_worker) — needs the async Kafka seam (module topic-map note).
    harness.register(
        "operadora.adequacao.update_monitoring_plan",
        make_update_monitoring_plan_handler(kafka),
    )
    harness.register_worker(FunctionWorker("operadora.adequacao.notify_sla_risk", notify_sla_risk))
    # RAW handler (NOT register_worker) — needs the async dispatcher seam (module topic-map note).
    harness.register(
        "operadora.adequacao.prepare_remediation_dossier",
        make_prepare_remediation_dossier_handler(dossier_dispatcher),
    )
