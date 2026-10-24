"""As DUAS pontes de identidade do erasure — substrato DARK (W5 CLNT 5.4).

O plano de eliminacao por camada (`spec/policies/retention/erasure-plan.template.yaml`,
"Chave B") nomeia exatamente DUAS pontes de identidade que nao existem em lugar nenhum da
arvore, e o carregador do plano repete as duas como as primeiras lacunas de identidade
(`maezo.platform.lifecycle.erasure_plan`, docstring "MISSING IDENTITY BRIDGES"):

1. **`titular_pseudo_id` -> `fhir_patient_id`** — o processo DSR (SP-OP-LGPD-DSR-001) carrega
   um pseudonimo; as duas colunas de titular da cadeia Alembica sao `fhir_patient_id`
   (`agent_memory` 0001:69 e `erasure_log` 0004:67). Sem a travessia, a referencia do titular
   NAO chega a nenhuma delas (`IdentityResolution.PONTE_AUSENTE`).
2. **`thread_id` -> `fhir_patient_id`** — a camada de trabalho (checkpointer LangGraph) e
   chaveada por `thread_id`/`checkpoint_ns`, nao por paciente (`erasure.py`, docstring de
   `_erase_working`). Sem a travessia, nenhuma relacao da camada de trabalho e alcancavel
   por titular.

O QUE ESTE MODULO E — e o que ele NAO E:

* E o SUBSTRATO de travessia de identidade, DARK: cada ponte atras da SUA flag, OFF por
  padrao (`MAEZO_ERASURE_BRIDGE_TITULAR_ENABLED` / `MAEZO_ERASURE_BRIDGE_THREAD_ENABLED`).
  Com a flag OFF a funcao recusa ANTES de qualquer coisa — zero travessia, zero chamada de
  fonte, zero efeito: o comportamento do sistema e EXATAMENTE o status quo de antes deste
  modulo existir. Virar uma flag e ato da onda ROLL, com janela de evidencia e revisor
  nomeados. Nenhum `deploy/**` pode liga-las neste programa.
* NAO decide nada. A travessia so acontece mediante a DECISAO do DPO registrada no formato
  fechado existente — o plano de eliminacao ratificado (`load_erasure_plan`, que recusa
  placeholder, vocabulario fechado de `decisao_dpo` e exige cobertura EXATA das relacoes
  conhecidas). As 16+ decisoes por relacao (`decisao_dpo`/`base_legal`/`retencao`) permanecem
  HUMANAS e NENHUMA existe neste PR — o artefato segue DRAFT com tudo `PENDENTE`. As decisoes
  que CADA ponte espera consumir:
    - Ponte A (`titular_pseudo_id` -> `fhir_patient_id`): as decisoes das relacoes chaveadas
      por `fhir_patient_id` que a travessia desbloqueia — `agent_memory` e `erasure_log`.
    - Ponte B (`thread_id` -> `fhir_patient_id`): as decisoes das relacoes da camada de
      trabalho chaveadas por thread — `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`.
* NAO elimina dado nenhum. Esta ponte e uma travessia de LEITURA de identidade; o unico
  efeito destrutivo do erasure segue atras de `ErasureManager.erase()`/`verify()`, que
  levantam `ErasureNotImplementedError` SEMPRE (T3.4-F3) e nao foram tocados. Compor
  travessia + cascata e trabalho do futuro mecanismo de execucao, que tambem esbarrara no
  `assert_dry_run_only` (`execution_mechanism_absent`) — ratificacao nunca e gatilho.
* NAO tem fonte propria. A origem do mapeamento e um `ErasureBridgeSource` que o CHAMADOR
  provisiona; sem fonte vinculada a ponte recusa (`source_unbound`) e NUNCA inventa
  mapeamento. Nenhuma tabela, DSN ou query vive aqui — este modulo nao executa SQL.
* NAO tem chamador de producao. Nenhum caminho de runtime chama `resolve_*` — isso e
  pinado por teste (o molde `test_retention_query_has_no_production_caller`). A ROLL que
  ligar a flag tambem assume criar a amarracao, com revisor proprio.

FAIL-CLOSED em ausencia e divergencia, cada recusa com motivo proprio e tipado:

* `flag_off` — a flag da ponte nao esta EXATAMENTE em `true` (so o literal habilita;
  `1`/`yes`/`on`/vazio/typo NUNCA habilitam — um typo nao pode meia-habilitar uma ponte de
  LGPD).
* `tenant_blank` / `reference_blank` — insumo vazio e recusa, nao `None` disfarçado.
* `source_unbound` — fabrica de fonte sem amarracao (molde 503 unbound).
* `ErasurePlanUnavailableError` (do carregador) — ausencia de decisao DPO, com os motivos
  precisos dele (`not_ratified`, `placeholder_value`, `unknown_decision`, ...).
* `relation_coverage_divergence` — o conjunto de relacoes que ESTA ponte desbloqueia nao
  esta coberto pelo plano registrado (defesa em profundidade alem da igualdade global do
  carregador).
* `source_failed` — a fonte levantou; a mensagem carrega so o TIPO da excecao, nunca a
  mensagem (pode ecoar bind values — o mesmo idioma de `_probe` no dry-run).
* `no_mapping` — a fonte NAO conhece a travessia. Ausencia de mapeamento NUNCA e sucesso
  vazio: e recusa, porque "resolveu para nada" seria o mesmo falso-sucesso que o
  `ErasureManager` foi feito a recusar.
* `invalid_resolution` — a fonte devolveu algo fora do DTO fechado (`BridgeResolution`) ou
  com identidade em branco (listas/mappings de REST nunca sao resolucao).
* `tenant_divergence` — a resolucao veio com tenant diferente do escopo pedido.
* `resolution_divergence` — com `confirm_with` (segunda fonte independente, opcional), as
  duas fontes divergem na identidade resolvida.

PHI (ADR-0006): o `titular_pseudo_id` e pseudonimo e a identidade RESOLVIDA
(`fhir_patient_id`) e identificador direto do titular — NENHUM dos dois entra em log,
mensagem de erro ou detalhe de recusa. Logs e erros carregam no maximo: a ponte (nome da
travessia), o motivo tipado, o tenant e booleanos.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

import structlog

from maezo.platform.lifecycle.erasure_plan import ErasurePlan, load_erasure_plan

logger = structlog.get_logger(__name__)

#: Flag DARK da Ponte A (`titular_pseudo_id` -> `fhir_patient_id`). OFF por padrao; virar e
#: ato da onda ROLL. Valor que nao seja o literal `true` (case-insensitive) e OFF.
TITULAR_BRIDGE_FLAG_ENV: Final = "MAEZO_ERASURE_BRIDGE_TITULAR_ENABLED"

#: Flag DARK da Ponte B (`thread_id` -> `fhir_patient_id`). Mesma postura da Ponte A.
THREAD_BRIDGE_FLAG_ENV: Final = "MAEZO_ERASURE_BRIDGE_THREAD_ENABLED"

#: Motivos de recusa tipados. Precisos de proposito — o motivo e a informacao que o
#: operador precisa, e nada mais (nunca um valor de referencia).
REASON_FLAG_OFF: Final = "flag_off"
REASON_TENANT_BLANK: Final = "tenant_blank"
REASON_REFERENCE_BLANK: Final = "reference_blank"
REASON_SOURCE_UNBOUND: Final = "source_unbound"
REASON_RELATION_COVERAGE_DIVERGENCE: Final = "relation_coverage_divergence"
REASON_SOURCE_FAILED: Final = "source_failed"
REASON_NO_MAPPING: Final = "no_mapping"
REASON_INVALID_RESOLUTION: Final = "invalid_resolution"
REASON_TENANT_DIVERGENCE: Final = "tenant_divergence"
REASON_RESOLUTION_DIVERGENCE: Final = "resolution_divergence"
REASON_UNKNOWN_BRIDGE: Final = "unknown_bridge"

#: O conjunto FECHADO de pontes. Nao existe terceira ponte: qualquer outra travessia de
#: identidade e outro pacote, com decisao propria.
KNOWN_BRIDGE_KINDS: Final[tuple[str, ...]] = (
    "titular_pseudo_id->fhir_patient_id",
    "thread_id->fhir_patient_id",
)


class ErasureBridgeRefusedError(RuntimeError):
    """Recusa fail-closed da travessia de identidade. Nao ha fallback, parcial ou default.

    `reason` e um dos `REASON_*`; `detail` e texto para operador e NUNCA embarca a
    referencia pedida nem a identidade resolvida.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"erasure identity bridge refused ({reason}): {detail}")


@dataclass(frozen=True, slots=True)
class BridgeResolution:
    """DTO fechado que Toda fonte de ponte devolve.

    A travessia e tenant-scoped por construcao: a fonte declara O tenant da identidade
    devolvida, e a ponte recusa divergencia em vez de confiar que a fonte escopou certo.
    """

    tenant_id: str
    fhir_patient_id: str


@runtime_checkable
class ErasureBridgeSource(Protocol):
    """A fonte do mapeamento — provisionada pelo CHAMADOR, nunca por este modulo.

    Nenhuma implementacao de producao existe neste PR (dark, sem amarracao). Uma fonte real
    futura (ex.: a tabela de metadados do checkpointer, ou o vinculo beneficiary do portal)
    sera outro pacote, com a propria decisao de habilitacao.

    Contrato: devolve `BridgeResolution` quando conhece a travessia, `None` quando NAO
    conhece (nunca uma resolucao vazia), e pode levantar (o erro e convertido em
    `source_failed` com o tipo apenas).
    """

    def resolve(self, tenant_id: str, reference: str) -> BridgeResolution | None: ...


class IdentityBridgeKind(StrEnum):
    """As DUAS travessias nomeadas pelo plano de eliminacao (Chave B). Fechado."""

    TITULAR_PSEUDO_ID_TO_FHIR_PATIENT_ID = "titular_pseudo_id->fhir_patient_id"
    THREAD_ID_TO_FHIR_PATIENT_ID = "thread_id->fhir_patient_id"


@dataclass(frozen=True, slots=True)
class IdentityBridge:
    """Uma ponte: QUE travessia, SOB qual flag, QUE relacoes ela desbloqueia.

    `source_relations` e o subconjunto do plano de eliminacao cujas linhas ficam alcancaveis
    pela travessia — e portanto as decisoes do DPO que a ponte consome. E checado contra o
    plano registrado a cada resolucao (`relation_coverage_divergence`).
    """

    kind: IdentityBridgeKind
    flag_env: str
    source_relations: tuple[str, ...]


#: Ponte A — a referencia que o processo DSR carrega (pseudonimo) ate a coluna de titular
#: das relacoes episodicas e do registro de eliminacao (0001:69 / 0004:67).
TITULAR_IDENTITY_BRIDGE: Final = IdentityBridge(
    kind=IdentityBridgeKind.TITULAR_PSEUDO_ID_TO_FHIR_PATIENT_ID,
    flag_env=TITULAR_BRIDGE_FLAG_ENV,
    source_relations=("agent_memory", "erasure_log"),
)

#: Ponte B — a chave da camada de trabalho (checkpointer LangGraph, provisionada por
#: PostgresSaver.setup()/.asetup() e NAO por Alembic) ate a mesma coluna de titular.
THREAD_IDENTITY_BRIDGE: Final = IdentityBridge(
    kind=IdentityBridgeKind.THREAD_ID_TO_FHIR_PATIENT_ID,
    flag_env=THREAD_BRIDGE_FLAG_ENV,
    source_relations=("checkpoints", "checkpoint_blobs", "checkpoint_writes"),
)

_BRIDGES_BY_KIND: Final[Mapping[str, IdentityBridge]] = {
    TITULAR_IDENTITY_BRIDGE.kind.value: TITULAR_IDENTITY_BRIDGE,
    THREAD_IDENTITY_BRIDGE.kind.value: THREAD_IDENTITY_BRIDGE,
}


def bridge_flag_enabled(flag_env: str) -> bool:
    """Le a flag DARK da ponte. SO o literal `true` (case-insensitive) habilita.

    Ausente/vazio/`1`/`yes`/`on`/qualquer outra grafia = OFF. A direcao do erro e sempre
    para a recusa: nenhum typo, forma alternativa ou valor herdado de outro subsistema pode
    meia-habilitar uma travessia de identidade de LGPD.
    """
    raw = os.environ.get(flag_env)
    if raw is None:
        return False
    return raw.strip().lower() == "true"


def _checked_crossing(
    source: ErasureBridgeSource,
    *,
    label: str,
    tenant_id: str,
    reference: str,
) -> BridgeResolution:
    """UMA travessia na fonte, com toda higiene de forma e de erro ao redor.

    Toda saida que nao seja um `BridgeResolution` utilizavel e recusa tipada; uma excecao da
    fonte vira `source_failed` com o TIPO apenas (a mensagem pode ecoar bind values).
    """
    try:
        resolution = source.resolve(tenant_id, reference)
    except Exception as exc:  # fonte e um callable do chamador; o raise-set e dele
        logger.error(
            "erasure_bridge_source_failed",
            bridge=label,
            tenant_id=tenant_id,
            reason=REASON_SOURCE_FAILED,
            source_error_type=type(exc).__name__,
        )
        raise ErasureBridgeRefusedError(
            REASON_SOURCE_FAILED,
            f"{label}: a fonte do mapeamento levantou {type(exc).__name__} (mensagem retida: "
            "pode ecoar valores de bind) — nenhuma travessia e afirmada.",
        ) from exc

    if resolution is None:
        logger.error(
            "erasure_bridge_no_mapping",
            bridge=label,
            tenant_id=tenant_id,
            reason=REASON_NO_MAPPING,
        )
        raise ErasureBridgeRefusedError(
            REASON_NO_MAPPING,
            f"{label}: a fonte vinculada NAO conhece travessia para a referencia pedida. "
            "Ausencia de mapeamento e recusa, nunca uma resolucao vazia — 'resolveu para "
            "nada' seria o falso-sucesso que o ErasureManager recusa (LGPD art. 18, VI).",
        )

    if not isinstance(resolution, BridgeResolution):
        logger.error(
            "erasure_bridge_invalid_resolution_shape",
            bridge=label,
            tenant_id=tenant_id,
            reason=REASON_INVALID_RESOLUTION,
        )
        raise ErasureBridgeRefusedError(
            REASON_INVALID_RESOLUTION,
            f"{label}: a fonte devolveu {type(resolution).__name__}, que nao e o DTO fechado "
            "BridgeResolution — forma invalida e recusa, nunca interpretacao.",
        )

    if not isinstance(resolution.fhir_patient_id, str) or not resolution.fhir_patient_id.strip():
        logger.error(
            "erasure_bridge_invalid_resolution_blank",
            bridge=label,
            tenant_id=tenant_id,
            reason=REASON_INVALID_RESOLUTION,
        )
        raise ErasureBridgeRefusedError(
            REASON_INVALID_RESOLUTION,
            f"{label}: o DTO devolvido traz identidade em branco — travessia sem identidade "
            "e recusa, nao sucesso vazio.",
        )
    return resolution


def _resolve(
    bridge: IdentityBridge,
    *,
    tenant_id: str,
    reference: str,
    source: ErasureBridgeSource | None,
    plan: ErasurePlan | None,
    confirm_with: ErasureBridgeSource | None,
    enabled: bool | None,
) -> str:
    """A cadeia fail-closed COMPLETA, compartilhada pelas duas pontes.

    Ordem carregada: a flag OFF recusa antes de TUDO (status quo); a travessia na fonte so
    acontece DEPOIS da decisao DPO registrada estar validada — nenhuma chamada de fonte sem
    decisao. Devolve o `fhir_patient_id` resolvido; qualquer desvio e recusa tipada.
    """
    if bridge.kind.value not in KNOWN_BRIDGE_KINDS or _BRIDGES_BY_KIND.get(bridge.kind.value) is not bridge:
        raise ErasureBridgeRefusedError(
            REASON_UNKNOWN_BRIDGE,
            "a ponte pedida nao e uma das duas declaradas pelo plano de eliminacao — o "
            "conjunto de pontes e fechado.",
        )

    flag_on = (bridge_flag_enabled(bridge.flag_env) if enabled is None else enabled) is True
    if not flag_on:
        logger.error(
            "erasure_bridge_flag_off",
            bridge=bridge.kind.value,
            reason=REASON_FLAG_OFF,
        )
        raise ErasureBridgeRefusedError(
            REASON_FLAG_OFF,
            f"{bridge.kind.value}: a flag {bridge.flag_env} nao esta habilitada NESTE deploy "
            "(OFF por padrao; virar e ato da onda ROLL). Recusando antes de qualquer efeito: "
            "a travessia nao existe para este deployment.",
        )

    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ErasureBridgeRefusedError(
            REASON_TENANT_BLANK,
            f"{bridge.kind.value}: tenant_id ausente/vazio — uma travessia sem escopo de "
            "tenant nao pode ser checada contra divergencia e e recusa.",
        )

    if not isinstance(reference, str) or not reference.strip():
        raise ErasureBridgeRefusedError(
            REASON_REFERENCE_BLANK,
            f"{bridge.kind.value}: a referencia do titular esta ausente/em branco — recusa, "
            "nunca uma resolucao contra referencia vazia (o mesmo idioma do dry-run, que "
            "recusa subject_ref em branco para nao confundir input ausente com zero).",
        )

    if source is None:
        logger.error(
            "erasure_bridge_source_unbound",
            bridge=bridge.kind.value,
            tenant_id=tenant_id,
            reason=REASON_SOURCE_UNBOUND,
        )
        raise ErasureBridgeRefusedError(
            REASON_SOURCE_UNBOUND,
            f"{bridge.kind.value}: nenhuma fonte de mapeamento vinculada — este modulo NUNCA "
            "inventa mapeamento. A amarracao da fonte e trabalho do pacote de habilitacao "
            "(ROLL), com decisao e revisor proprios.",
        )

    # ---- DECISAO DPO REGISTRADA (formato fechado) — ANTES de qualquer travessia. ----
    # A travessia de fonte so acontece depois deste gate: sem decisao, nem a fonte e tocada.
    decision_plan = load_erasure_plan() if plan is None else plan
    missing = [r for r in bridge.source_relations if r not in decision_plan.decisions]
    if missing:
        logger.error(
            "erasure_bridge_relation_coverage_divergence",
            bridge=bridge.kind.value,
            tenant_id=tenant_id,
            reason=REASON_RELATION_COVERAGE_DIVERGENCE,
        )
        raise ErasureBridgeRefusedError(
            REASON_RELATION_COVERAGE_DIVERGENCE,
            f"{bridge.kind.value}: o plano de eliminacao registrado NAO cobre as relacoes que "
            f"esta travessia desbloqueia ({sorted(missing)}) — divergencia entre ponte e "
            "decisao registrada e recusa; a cobertura global o carregador ja exige, esta "
            "checagem e a defesa local da ponte.",
        )

    resolution = _checked_crossing(
        source,
        label=bridge.kind.value,
        tenant_id=tenant_id,
        reference=reference,
    )

    if resolution.tenant_id != tenant_id:
        logger.error(
            "erasure_bridge_tenant_divergence",
            bridge=bridge.kind.value,
            tenant_id=tenant_id,
            reason=REASON_TENANT_DIVERGENCE,
        )
        raise ErasureBridgeRefusedError(
            REASON_TENANT_DIVERGENCE,
            f"{bridge.kind.value}: a resolucao veio de um tenant DIFERENTE do escopo pedido — "
            "divergencia de escopo e recusa, nunca reconciliacao silenciosa.",
        )

    if confirm_with is not None:
        confirmation = _checked_crossing(
            confirm_with,
            label=bridge.kind.value,
            tenant_id=tenant_id,
            reference=reference,
        )
        if confirmation.tenant_id != tenant_id:
            raise ErasureBridgeRefusedError(
                REASON_TENANT_DIVERGENCE,
                f"{bridge.kind.value}: a FONTE DE CONFIRMACAO veio de tenant diferente do "
                "escopo pedido — divergencia de escopo e recusa.",
            )
        if confirmation.fhir_patient_id != resolution.fhir_patient_id:
            logger.error(
                "erasure_bridge_resolution_divergence",
                bridge=bridge.kind.value,
                tenant_id=tenant_id,
                reason=REASON_RESOLUTION_DIVERGENCE,
            )
            raise ErasureBridgeRefusedError(
                REASON_RESOLUTION_DIVERGENCE,
                f"{bridge.kind.value}: as duas fontes independentes DIVERGEM na identidade "
                "resolvida — a travessia exige concordancia quando uma segunda fonte e "
                "provisionada; divergencia e recusa, nunca escolha arbitrária.",
            )

    logger.info(
        "erasure_bridge_resolved",
        bridge=bridge.kind.value,
        tenant_id=tenant_id,
        relations_covered=len(bridge.source_relations),
    )
    return resolution.fhir_patient_id


def resolve_titular_pseudo_id(
    *,
    tenant_id: str,
    titular_pseudo_id: str,
    source: ErasureBridgeSource | None,
    plan: ErasurePlan | None = None,
    confirm_with: ErasureBridgeSource | None = None,
    enabled: bool | None = None,
) -> str:
    """PONTE A — `titular_pseudo_id` -> `fhir_patient_id` (flag `TITULAR_BRIDGE_FLAG_ENV`).

    Consumindo as decisoes DPO das relacoes `agent_memory` e `erasure_log` (as unicas
    chaveadas por `fhir_patient_id` na cadeia Alembica). Args e cadeia de recusa: ver
    `_resolve`.

    Returns:
        O `fhir_patient_id` resolvido, SOMENTE com flag ON + decisao DPO registrada + fonte
        vinculada concordante.

    Raises:
        ErasureBridgeRefusedError: em toda recusa tipada (`REASON_*`).
        maezo.platform.lifecycle.erasure_plan.ErasurePlanUnavailableError: quando a decisao
            DPO registrada esta ausente/invalida (motivos precisos do carregador).
    """
    return _resolve(
        TITULAR_IDENTITY_BRIDGE,
        tenant_id=tenant_id,
        reference=titular_pseudo_id,
        source=source,
        plan=plan,
        confirm_with=confirm_with,
        enabled=enabled,
    )


def resolve_thread_id(
    *,
    tenant_id: str,
    thread_id: str,
    source: ErasureBridgeSource | None,
    plan: ErasurePlan | None = None,
    confirm_with: ErasureBridgeSource | None = None,
    enabled: bool | None = None,
) -> str:
    """PONTE B — `thread_id` -> `fhir_patient_id` (flag `THREAD_BRIDGE_FLAG_ENV`).

    Consumindo as decisoes DPO das relacoes da camada de trabalho `checkpoints`,
    `checkpoint_blobs` e `checkpoint_writes` (chaveadas por thread, sem coluna de titular).
    Args e cadeia de recusa: ver `_resolve`.

    Returns:
        O `fhir_patient_id` resolvido, SOMENTE com flag ON + decisao DPO registrada + fonte
        vinculada concordante.

    Raises:
        ErasureBridgeRefusedError: em toda recusa tipada (`REASON_*`).
        maezo.platform.lifecycle.erasure_plan.ErasurePlanUnavailableError: quando a decisao
            DPO registrada esta ausente/invalida (motivos precisos do carregador).
    """
    return _resolve(
        THREAD_IDENTITY_BRIDGE,
        tenant_id=tenant_id,
        reference=thread_id,
        source=source,
        plan=plan,
        confirm_with=confirm_with,
        enabled=enabled,
    )
