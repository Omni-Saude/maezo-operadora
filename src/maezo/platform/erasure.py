"""Erasure Manager — cascata de eliminacao LGPD por titular (ADR-0002 §4), DARK (R-013, F-008).

Origem normativa, uma linha cada:

* ADR-0002 item 4 — eliminacao por `fhir_patient_id` cascateia pelas camadas de estado do
  agente (trabalho / episodica / semantica) e e verificada depois.
* ADR-0047 — a camada semantica esta SUSPENSA (sem relacao desde `0009_drop_pgvector`,
  DU-01-b): nada a eliminar ali, e o relatorio diz isso em vez de afirmar "apagado".
* ADR-0019 §5 — o que o Maezo apaga em cascata do lado analitico e o SURROGATE de ligacao
  `mpi_id <-> fhir_patient_id`; agregados anonimos sobrevivem (fronteira DRAFT/DPO).
* ADR-0020 item 6 — eliminacao que colide com legal-hold ativo e resolvida A FAVOR do hold e
  diferida; o surrogate so cai sem hold ativo; o codigo nao decide sob colisao — roteia a humano.
* `spec/policies/retention/erasure-plan.template.yaml` (Chave A) — a decisao por relacao
  (`ELIMINAR` / `ANONIMIZAR` / `RETER_COM_BASE_LEGAL`) e do DPO, carregada por
  `maezo.platform.lifecycle.erasure_plan.load_erasure_plan`. Este modulo e a Chave B (o
  mecanismo): ele CARREGA a decisao registrada ate um executor; nunca a inventa.

O QUE ESTE MODULO E:

* O ORQUESTRADOR da cascata, atras da flag DARK `MAEZO_ERASURE_CASCADE` (OFF por padrao; so o
  literal `true` liga; virar e ato da onda ROLL com revisor nomeado; nenhum `deploy/**` a
  declara). Flag OFF = `ErasureCascadeDisabledError` ANTES de qualquer efeito: nenhum
  predicado, carregador, ponte ou executor e sequer tocado.
* Com a flag ON, a ordem e carregada e cada passo e fail-closed, sem efeito antes do ultimo
  portao:
    1. insumos (`tenant_id`, `fhir_patient_id`, `titular_pseudo_id`) nao-vazios;
    2. predicado de legal-hold (R-044, `evaluate_legal_hold`, chave `titular_pseudo_id`):
       qualquer desfecho que nao seja `LIVRE` (hold ativo OU duvida) => eliminacao DIFERIDA
       (`ErasureLegalHoldDeferredError`), decisao humana;
    3. plano do DPO ratificado (Chave A) — sem ele, `plan_unavailable`;
    4. ponte de identidade A (`titular_pseudo_id -> fhir_patient_id`, DARK, flag propria): o
       titular checado no legal-hold e o paciente eliminado tem de ser O MESMO; recusa da
       ponte ou divergencia => recusa;
    5. pre-voo: TODA relacao que o plano manda `ELIMINAR`/`ANONIMIZAR` precisa de um executor
       vinculado, e o surrogate `mpi_id<->fhir_patient_id` precisa de um store vinculado — sem
       eles, `ErasureNotImplementedError` (o mecanismo daquela relacao nao existe neste
       deployment). Nada roda se UMA relacao estiver sem executor;
    6. execucao por `ordem` (relacao a relacao), surrogate por ultimo; falha para a cascata e
       e reportada (`partial`/`failed`), nunca engolida;
    7. verificacao: cada relacao acionada e o surrogate precisam reportar zero residuo; so
       entao `status="completed"`.

O QUE ESTE MODULO NAO E:

* NAO tem SQL. O plano de eliminacao diz que as sentencas destrutivas vivem no pacote de
  revisao como DOCUMENTO (`docs/reviews/adr-0029-erasure-packet.md`); aqui so existem PORTAS
  (`ErasureLayerExecutor`, `SurrogateLinkStore`) que o CHAMADOR provisiona. Nenhuma
  implementacao de producao existe, e nenhum caminho de runtime chama `ErasureManager` — o
  par e pinado por teste (`tests/unit/platform/test_erasure_cascade.py`).
* NAO decide. Nenhuma decisao, base legal ou prazo de retencao nasce aqui: `RETER_COM_BASE_LEGAL`
  e respeitado como veio, e o vocabulario e o do carregador.
* NAO e a camada de trabalho por paciente: o checkpointer LangGraph (`checkpoints`,
  `checkpoint_blobs`, `checkpoint_writes`, provisionados por `PostgresSaver.setup()`, nao por
  Alembic — `0006_retire_dead_checkpoint_tables.py`) e chaveado por `thread_id`. Achar as
  threads de um paciente e trabalho do EXECUTOR daquela relacao (ponte B,
  `identity_bridges.resolve_thread_id`, tambem DARK).

PHI (ADR-0006): `fhir_patient_id` e identificador direto do titular e `titular_pseudo_id` e
pseudonimo — NENHUM dos dois entra em log, mensagem de erro, `detail` de recusa ou `repr` do
resultado. Logs carregam tenant, motivo tipado, nomes de relacao e contagens; uma excecao de
executor vira o seu TIPO, nunca a mensagem (pode ecoar bind values).

Uso (somente testes, ate a ROLL):
    manager = ErasureManager(executors={...}, surrogate_store=..., titular_bridge_source=...)
    result = manager.erase("tenant-amh", "patient-123", titular_pseudo_id="pseudo-1")
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

import structlog

# Imports de `identity_bridges` e `lifecycle.*` sao TARDIOS (dentro das funcoes): `maezo.platform`
# reexporta `ErasureManager`, e importar o pacote (ex.: `maezo.platform.observability`) nao pode
# arrastar o carregador do plano, o registro de legal-hold e as pontes
# (`tests/unit/platform/test_alert_metrics_fence.py`). So os tipos sobem no topo, sob TYPE_CHECKING.
if TYPE_CHECKING:
    from maezo.platform.identity_bridges import ErasureBridgeSource
    from maezo.platform.lifecycle.erasure_plan import ErasurePlan, PersistenceLayer
    from maezo.platform.lifecycle.legal_hold import LegalHoldDecision

logger = structlog.get_logger(__name__)

#: Flag DARK da cascata. OFF por padrao; so o literal `true` (case-insensitive) liga.
CASCADE_FLAG_ENV: Final = "MAEZO_ERASURE_CASCADE"

#: Decisoes do plano que exigem efeito (e portanto executor). `RETER_COM_BASE_LEGAL` nao exige.
_ACTIONABLE_DECISIONS: Final[frozenset[str]] = frozenset({"ELIMINAR", "ANONIMIZAR"})

#: Motivos de recusa tipados — a informacao que o operador precisa, nunca um valor de titular.
REASON_CASCADE_DISABLED: Final = "cascade_disabled"
REASON_TENANT_BLANK: Final = "tenant_blank"
REASON_SUBJECT_BLANK: Final = "subject_blank"
REASON_LEGAL_HOLD_DEFERRED: Final = "legal_hold_deferred"
REASON_PLAN_UNAVAILABLE: Final = "plan_unavailable"
REASON_IDENTITY_UNCONFIRMED: Final = "identity_unconfirmed"
REASON_IDENTITY_DIVERGENCE: Final = "identity_divergence"
REASON_DECISION_MISSING: Final = "decision_missing"
REASON_UNKNOWN_DECISION: Final = "unknown_decision"
REASON_EXECUTOR_UNKNOWN_RELATION: Final = "executor_unknown_relation"
REASON_EXECUTOR_UNBOUND: Final = "executor_unbound"
REASON_SURROGATE_UNBOUND: Final = "surrogate_unbound"

#: Status do resultado. Nao ha valor "assumir zero": ausencia de prova nunca vira "completed".
STATUS_COMPLETED: Final = "completed"
STATUS_PARTIAL: Final = "partial"
STATUS_FAILED: Final = "failed"
STATUS_CLEAN: Final = "clean"
STATUS_DIRTY: Final = "dirty"


class ErasureRefusedError(RuntimeError):
    """Recusa fail-closed ANTES de qualquer efeito. `reason` e um dos `REASON_*`.

    `detail` e texto de operador e NUNCA embarca `fhir_patient_id` nem `titular_pseudo_id`.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"erasure refused ({reason}): {detail}")


class ErasureLegalHoldDeferredError(ErasureRefusedError):
    """Eliminacao DIFERIDA pelo predicado de legal-hold (hold ativo ou duvida) — ADR-0020 item 6."""

    def __init__(self, decision: LegalHoldDecision) -> None:
        self.decision = decision
        super().__init__(
            REASON_LEGAL_HOLD_DEFERRED,
            f"desfecho={decision.outcome.value}, motivo={decision.motivo}, "
            f"holds_ativos={len(decision.hold_ids)}, registro_sha256={decision.registro_sha256}. "
            "A colisao eliminacao x legal-hold e resolvida a favor do hold e reconciliada ao fim "
            "dele; na duvida nada e eliminado. Decisao humana (juridico/DPO) — NAO retentar.",
        )


class ErasureNotImplementedError(ErasureRefusedError, NotImplementedError):
    """O mecanismo de eliminacao NAO esta disponivel neste deployment — recusa sem efeito.

    Duas formas: (a) `ErasureCascadeDisabledError` — a flag dark esta desligada (o padrao de
    todo deployment); (b) pre-voo — o plano do DPO manda `ELIMINAR`/`ANONIMIZAR` uma relacao sem
    executor vinculado, ou o store do surrogate `mpi_id<->fhir_patient_id` nao esta vinculado.
    Nenhuma relacao e tocada: a cascata e tudo-ou-nada no pre-voo. Mantem o nome e a ancestralidade
    `NotImplementedError` do sentinela T3.4-F3 de antes de R-013, para que todo `except` escrito
    contra a recusa antiga continue tratando o estado dark como recusa (nunca como sucesso).
    """


class ErasureCascadeDisabledError(ErasureNotImplementedError):
    """A flag `MAEZO_ERASURE_CASCADE` nao esta ligada neste deploy (o padrao)."""


@runtime_checkable
class ErasureLayerExecutor(Protocol):
    """Executor de UMA relacao do plano — provisionado pelo chamador, nunca por este modulo.

    `apply` aplica a decisao do DPO (`ELIMINAR` ou `ANONIMIZAR`) as linhas do titular e devolve
    quantas linhas foram afetadas (`int >= 0`). `remaining` devolve quantas linhas do titular
    ainda NAO estao no estado decidido (`int >= 0`; zero = limpo). Qualquer outra forma de
    retorno e tratada como falha; uma excecao vira o TIPO, nunca a mensagem.
    """

    def apply(self, tenant_id: str, fhir_patient_id: str, decisao_dpo: str) -> int: ...

    def remaining(self, tenant_id: str, fhir_patient_id: str, decisao_dpo: str) -> int: ...


@runtime_checkable
class SurrogateLinkStore(Protocol):
    """O surrogate de ligacao `mpi_id <-> fhir_patient_id` (ADR-0019 §5) — provisionado pelo chamador.

    `drop` derruba os vinculos do titular e devolve quantos caíram; `remaining` devolve quantos
    restam. Nenhuma implementacao existe neste pacote: o Maezo ainda nao persiste o surrogate,
    entao a cascata recusa (`surrogate_unbound`) em vez de afirmar que a ponte caiu.
    """

    def drop(self, tenant_id: str, fhir_patient_id: str) -> int: ...

    def remaining(self, tenant_id: str, fhir_patient_id: str) -> int: ...


@dataclass(frozen=True)
class LayerErasureOutcome:
    """O que a cascata fez com UMA relacao do plano. Sem valor de titular, por construcao.

    `disposicao`: `retido` (plano manda reter), `retirada` (relacao nao existe mais),
    `aplicado`, `falhou`, `nao_executado` (cascata parou antes). `residual` vem da verificacao.
    """

    tabela: str
    camada: str
    decisao_dpo: str
    disposicao: str
    rows_affected: int | None = None
    residual: int | None = None
    error_type: str | None = None


@dataclass
class ErasureResult:
    """Resultado de uma eliminacao em cascata.

    Attributes:
        fhir_patient_id: O titular (fora do `repr`, ADR-0006).
        tenant_id: Escopo de tenant.
        working_erased: Toda relacao acionada da camada de trabalho aplicada E verificada limpa.
        episodic_erased: Idem para a camada episodica.
        semantic_erased: Camada semantica SUSPENSA (ADR-0047): sem relacao, fica False.
        surrogate_dropped: Surrogate `mpi_id<->fhir_patient_id` derrubado E verificado (ADR-0019 §5).
        status: `completed` (tudo aplicado e verificado), `partial` (algo aplicado),
            `failed` (nada aplicado), `pending` (nao executado).
        errors: Mensagens sem PHI (relacao + tipo de excecao / residuo).
        layers: Desfecho por relacao, na ordem do plano.
        legal_hold: `LegalHoldDecision.registro()` consultado (snapshot do registro, ADR-0029 §2).
        plan_evidence_ref: `evidence_ref` do plano do DPO que autorizou a cascata.
        timestamp: Quando a cascata rodou.
    """

    fhir_patient_id: str = field(repr=False)
    tenant_id: str
    working_erased: bool = False
    episodic_erased: bool = False
    semantic_erased: bool = False
    surrogate_dropped: bool = False
    status: str = "pending"
    errors: list[str] = field(default_factory=list)
    layers: tuple[LayerErasureOutcome, ...] = ()
    legal_hold: dict[str, Any] | None = None
    plan_evidence_ref: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


def cascade_flag_enabled() -> bool:
    """Le a flag DARK da cascata. SO o literal `true` (case-insensitive) habilita.

    Ausente/vazio/`1`/`yes`/`on`/qualquer outra grafia = OFF: nenhum typo meia-habilita LGPD.
    """
    raw = os.environ.get(CASCADE_FLAG_ENV)
    if raw is None:
        return False
    return raw.strip().lower() == "true"


def _is_count(value: object) -> bool:
    """Contagem valida: `int` (nunca `bool`) e nao-negativa."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _blank(value: object) -> bool:
    return not isinstance(value, str) or not value.strip() or value != value.strip()


class ErasureManager:
    """Orquestrador DARK da cascata de eliminacao (ver docstring do modulo para a ordem).

    Todas as dependencias sao injetadas pelo chamador; sem elas, a cascata recusa em vez de
    inventar. `enabled` (testes) sobrepoe a flag: so o booleano `True` liga.
    """

    def __init__(
        self,
        *,
        executors: Mapping[str, ErasureLayerExecutor] | None = None,
        surrogate_store: SurrogateLinkStore | None = None,
        titular_bridge_source: ErasureBridgeSource | None = None,
        titular_bridge_confirm_with: ErasureBridgeSource | None = None,
        titular_bridge_enabled: bool | None = None,
        plan: ErasurePlan | None = None,
        plan_loader: Callable[[], ErasurePlan] | None = None,
        legal_hold_evaluator: Callable[..., LegalHoldDecision] | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._executors: dict[str, ErasureLayerExecutor] = dict(executors or {})
        self._surrogate = surrogate_store
        self._bridge_source = titular_bridge_source
        self._bridge_confirm = titular_bridge_confirm_with
        self._bridge_enabled = titular_bridge_enabled
        self._plan = plan
        self._plan_loader = plan_loader
        self._legal_hold_evaluator = legal_hold_evaluator
        self._enabled = enabled
        logger.info("erasure_manager_initialized", executors_bound=len(self._executors))

    # ------------------------------------------------------------------ gates

    def _flag_on(self) -> bool:
        return (cascade_flag_enabled() if self._enabled is None else self._enabled) is True

    def _require_enabled(self, op: str) -> None:
        if not self._flag_on():
            logger.error("erasure_refused", op=op, reason=REASON_CASCADE_DISABLED)
            raise ErasureCascadeDisabledError(
                REASON_CASCADE_DISABLED,
                f"ErasureManager.{op}: a flag {CASCADE_FLAG_ENV} nao esta habilitada neste "
                "deploy (OFF por padrao; virar e ato da onda ROLL). Recusando antes de qualquer "
                "efeito — nenhum predicado, plano, ponte ou executor foi tocado.",
            )

    @staticmethod
    def _require_inputs(op: str, tenant_id: object, fhir_patient_id: object) -> None:
        if _blank(tenant_id):
            logger.error("erasure_refused", op=op, reason=REASON_TENANT_BLANK)
            raise ErasureRefusedError(REASON_TENANT_BLANK, f"{op}: tenant_id ausente/em branco.")
        if _blank(fhir_patient_id):
            logger.error("erasure_refused", op=op, tenant_id=tenant_id, reason=REASON_SUBJECT_BLANK)
            raise ErasureRefusedError(
                REASON_SUBJECT_BLANK,
                f"{op}: fhir_patient_id ausente/em branco — recusa, nunca cascata contra referencia vazia.",
            )

    def _load_plan(self, op: str, tenant_id: str) -> ErasurePlan:
        from maezo.platform.lifecycle.erasure_plan import ErasurePlanUnavailableError, load_erasure_plan

        loader = self._plan_loader if self._plan_loader is not None else load_erasure_plan
        try:
            return self._plan if self._plan is not None else loader()
        except ErasurePlanUnavailableError as exc:
            logger.error(
                "erasure_refused",
                op=op,
                tenant_id=tenant_id,
                reason=REASON_PLAN_UNAVAILABLE,
                plan_reason=exc.reason,
            )
            raise ErasureRefusedError(
                REASON_PLAN_UNAVAILABLE,
                f"{op}: nao ha plano de eliminacao ratificado pelo DPO ({exc.reason}) — sem a "
                "decisao registrada por relacao (Chave A) nada e eliminado.",
            ) from exc

    def _confirm_identity(
        self, tenant_id: str, fhir_patient_id: str, titular_pseudo_id: str, plan: ErasurePlan
    ) -> None:
        from maezo.platform.identity_bridges import ErasureBridgeRefusedError, resolve_titular_pseudo_id

        try:
            resolved = resolve_titular_pseudo_id(
                tenant_id=tenant_id,
                titular_pseudo_id=titular_pseudo_id,
                source=self._bridge_source,
                plan=plan,
                confirm_with=self._bridge_confirm,
                enabled=self._bridge_enabled,
            )
        except ErasureBridgeRefusedError as exc:
            logger.error(
                "erasure_refused",
                op="erase",
                tenant_id=tenant_id,
                reason=REASON_IDENTITY_UNCONFIRMED,
                bridge_reason=exc.reason,
            )
            raise ErasureRefusedError(
                REASON_IDENTITY_UNCONFIRMED,
                f"erase: a ponte titular_pseudo_id->fhir_patient_id recusou ({exc.reason}) — sem "
                "confirmar que o titular checado no legal-hold e o paciente eliminado sao o "
                "mesmo, nada e eliminado.",
            ) from exc
        if resolved != fhir_patient_id:
            logger.error(
                "erasure_refused", op="erase", tenant_id=tenant_id, reason=REASON_IDENTITY_DIVERGENCE
            )
            raise ErasureRefusedError(
                REASON_IDENTITY_DIVERGENCE,
                "erase: a ponte resolveu o titular para um paciente DIFERENTE do pedido — "
                "divergencia de identidade e recusa, nunca escolha.",
            )

    def _preflight(self, op: str, tenant_id: str, plan: ErasurePlan) -> list[tuple[PersistenceLayer, str]]:
        """Relacoes acionaveis (decisao + executor) — ou recusa, sem efeito nenhum."""
        from maezo.platform.lifecycle.erasure_plan import (
            DECISION_VOCABULARY,
            KNOWN_TABLES,
            PERSISTENCE_LAYERS,
            IdentityResolution,
        )

        unknown = sorted(set(self._executors) - KNOWN_TABLES)
        if unknown:
            logger.error(
                "erasure_refused", op=op, tenant_id=tenant_id, reason=REASON_EXECUTOR_UNKNOWN_RELATION
            )
            raise ErasureRefusedError(
                REASON_EXECUTOR_UNKNOWN_RELATION,
                f"{op}: executor vinculado a relacao fora da enumeracao do plano: {unknown}.",
            )
        actionable: list[tuple[PersistenceLayer, str]] = []
        unbound: list[str] = []
        for layer in sorted(PERSISTENCE_LAYERS, key=lambda item: item.ordem):
            decision = plan.get(layer.tabela)
            if decision is None:
                logger.error("erasure_refused", op=op, tenant_id=tenant_id, reason=REASON_DECISION_MISSING)
                raise ErasureRefusedError(
                    REASON_DECISION_MISSING,
                    f"{op}: o plano registrado nao traz decisao para {layer.tabela!r}.",
                )
            if decision.decisao_dpo not in DECISION_VOCABULARY:
                logger.error("erasure_refused", op=op, tenant_id=tenant_id, reason=REASON_UNKNOWN_DECISION)
                raise ErasureRefusedError(
                    REASON_UNKNOWN_DECISION,
                    f"{op}: decisao fora do vocabulario fechado para {layer.tabela!r}.",
                )
            if decision.decisao_dpo not in _ACTIONABLE_DECISIONS:
                continue
            if layer.resolucao is IdentityResolution.RETIRADA:
                continue
            if layer.tabela not in self._executors:
                unbound.append(layer.tabela)
                continue
            actionable.append((layer, decision.decisao_dpo))
        if unbound:
            logger.error(
                "erasure_refused",
                op=op,
                tenant_id=tenant_id,
                reason=REASON_EXECUTOR_UNBOUND,
                relations=unbound,
            )
            raise ErasureNotImplementedError(
                REASON_EXECUTOR_UNBOUND,
                f"{op}: o plano do DPO manda eliminar/anonimizar {unbound}, mas nao ha executor "
                "vinculado para essas relacoes neste deployment — nenhuma relacao foi tocada "
                "(LGPD art. 18, VI: nunca reportar eliminacao sem efeito real).",
            )
        if self._surrogate is None:
            logger.error("erasure_refused", op=op, tenant_id=tenant_id, reason=REASON_SURROGATE_UNBOUND)
            raise ErasureNotImplementedError(
                REASON_SURROGATE_UNBOUND,
                f"{op}: o store do surrogate mpi_id<->fhir_patient_id (ADR-0019 §5) nao esta "
                "vinculado — sem derrubar a ponte re-identificadora a cascata nao se afirma.",
            )
        return actionable

    # ------------------------------------------------------------------ public API

    def erase(
        self,
        tenant_id: str,
        fhir_patient_id: str,
        *,
        titular_pseudo_id: str | None = None,
    ) -> ErasureResult:
        """Executa a cascata de eliminacao do titular (ordem e portoes: docstring do modulo).

        Returns:
            `ErasureResult`; so `status == "completed"` significa eliminacao aplicada E verificada.

        Raises:
            ErasureCascadeDisabledError: flag OFF (padrao) — antes de qualquer efeito.
            ErasureLegalHoldDeferredError: legal-hold ativo ou indeterminado — antes de qualquer efeito.
            ErasureNotImplementedError: relacao exigida sem executor / surrogate sem store.
            ErasureRefusedError: demais recusas pre-efeito (insumo, plano, identidade, plano incoerente).
        """
        self._require_enabled("erase")
        self._require_inputs("erase", tenant_id, fhir_patient_id)

        from maezo.platform.lifecycle.erasure_plan import PERSISTENCE_LAYERS
        from maezo.platform.lifecycle.legal_hold import LegalHoldDecision, evaluate_legal_hold

        evaluator = (
            self._legal_hold_evaluator if self._legal_hold_evaluator is not None else evaluate_legal_hold
        )
        decision = evaluator(tenant_id=tenant_id, titular_pseudo_id=titular_pseudo_id)
        if not isinstance(decision, LegalHoldDecision) or decision.bloqueia_eliminacao:
            if not isinstance(decision, LegalHoldDecision):
                logger.error(
                    "erasure_refused", op="erase", tenant_id=tenant_id, reason=REASON_LEGAL_HOLD_DEFERRED
                )
                raise ErasureRefusedError(
                    REASON_LEGAL_HOLD_DEFERRED,
                    "erase: o predicado de legal-hold devolveu forma invalida — duvida nao vira apagamento.",
                )
            logger.error(
                "erasure_refused",
                op="erase",
                tenant_id=tenant_id,
                reason=REASON_LEGAL_HOLD_DEFERRED,
                outcome=decision.outcome.value,
                motivo=decision.motivo,
                hold_count=len(decision.hold_ids),
            )
            raise ErasureLegalHoldDeferredError(decision)

        # Defesa em profundidade: o predicado padrao ja devolve INDETERMINADO sem titular, mas um
        # avaliador injetado nao pode transformar titular ausente em "livre".
        if _blank(titular_pseudo_id) or not isinstance(titular_pseudo_id, str):
            logger.error("erasure_refused", op="erase", tenant_id=tenant_id, reason=REASON_SUBJECT_BLANK)
            raise ErasureRefusedError(
                REASON_SUBJECT_BLANK,
                "erase: titular_pseudo_id ausente/em branco — o legal-hold e a ponte de identidade "
                "exigem o pseudonimo do titular.",
            )
        plan = self._load_plan("erase", tenant_id)
        self._confirm_identity(tenant_id, fhir_patient_id, titular_pseudo_id, plan)
        actionable = self._preflight("erase", tenant_id, plan)
        surrogate = self._bound_surrogate()

        result = ErasureResult(
            fhir_patient_id=fhir_patient_id,
            tenant_id=tenant_id,
            legal_hold=decision.registro(),
            plan_evidence_ref=plan.evidence_ref,
        )
        outcomes = self._static_outcomes(plan)
        applied: dict[str, LayerErasureOutcome] = {}
        failed = False
        for layer, decisao in actionable:
            if failed:
                applied[layer.tabela] = LayerErasureOutcome(
                    layer.tabela, layer.camada, decisao, "nao_executado"
                )
                continue
            executor = self._executors[layer.tabela]
            try:
                rows = executor.apply(tenant_id, fhir_patient_id, decisao)
            except Exception as exc:  # executor e do chamador; o raise-set e dele
                failed = True
                result.errors.append(f"{layer.tabela}: {type(exc).__name__}")
                applied[layer.tabela] = LayerErasureOutcome(
                    layer.tabela, layer.camada, decisao, "falhou", error_type=type(exc).__name__
                )
                continue
            if not _is_count(rows):
                failed = True
                result.errors.append(f"{layer.tabela}: invalid_executor_result")
                applied[layer.tabela] = LayerErasureOutcome(
                    layer.tabela, layer.camada, decisao, "falhou", error_type="invalid_executor_result"
                )
                continue
            applied[layer.tabela] = LayerErasureOutcome(
                layer.tabela, layer.camada, decisao, "aplicado", rows_affected=rows
            )

        surrogate_applied = False
        if not failed:
            try:
                dropped = surrogate.drop(tenant_id, fhir_patient_id)
            except Exception as exc:  # store e do chamador
                failed = True
                result.errors.append(f"surrogate_mpi_id: {type(exc).__name__}")
            else:
                if _is_count(dropped):
                    surrogate_applied = True
                else:
                    failed = True
                    result.errors.append("surrogate_mpi_id: invalid_store_result")

        # Verificacao: residuo zero por relacao aplicada e no surrogate.
        verified: dict[str, LayerErasureOutcome] = {}
        for tabela, outcome in applied.items():
            if outcome.disposicao != "aplicado":
                verified[tabela] = outcome
                continue
            residual = self._residual(
                self._executors[tabela], tenant_id, fhir_patient_id, outcome.decisao_dpo
            )
            if residual != 0:
                result.errors.append(
                    f"{tabela}: residual" if residual is not None else f"{tabela}: verify_failed"
                )
            verified[tabela] = LayerErasureOutcome(
                outcome.tabela,
                outcome.camada,
                outcome.decisao_dpo,
                outcome.disposicao,
                rows_affected=outcome.rows_affected,
                residual=residual,
            )
        surrogate_clean = False
        if surrogate_applied:
            residual_links = self._surrogate_residual(surrogate, tenant_id, fhir_patient_id)
            surrogate_clean = residual_links == 0
            if not surrogate_clean:
                result.errors.append(
                    "surrogate_mpi_id: residual"
                    if residual_links is not None
                    else "surrogate_mpi_id: verify_failed"
                )

        outcomes.update(verified)
        result.layers = tuple(
            outcomes[layer.tabela] for layer in sorted(PERSISTENCE_LAYERS, key=lambda item: item.ordem)
        )
        result.surrogate_dropped = surrogate_clean
        result.working_erased = self._camada_clean(verified, "trabalho")
        result.episodic_erased = self._camada_clean(verified, "episodica")
        result.semantic_erased = False  # ADR-0047: camada suspensa, sem relacao a eliminar
        any_applied = any(o.disposicao == "aplicado" for o in verified.values()) or surrogate_applied
        all_clean = (
            not failed
            and surrogate_clean
            and all(o.disposicao == "aplicado" and o.residual == 0 for o in verified.values())
        )
        result.status = STATUS_COMPLETED if all_clean else (STATUS_PARTIAL if any_applied else STATUS_FAILED)
        logger.info(
            "erasure_cascade_finished",
            tenant_id=tenant_id,
            status=result.status,
            relations_applied=sum(1 for o in verified.values() if o.disposicao == "aplicado"),
            relations_actionable=len(actionable),
            surrogate_dropped=surrogate_clean,
            error_count=len(result.errors),
        )
        return result

    def verify(self, tenant_id: str, fhir_patient_id: str) -> dict[str, Any]:
        """Verifica (somente leitura) que toda relacao acionavel do plano e o surrogate estao limpos.

        Returns:
            Dict com `working_clean`, `episodic_clean`, `semantic_clean` (sempre False: camada
            suspensa), `surrogate_clean`, `residual` (relacoes com residuo/falha) e `status`
            (`clean` | `dirty`).

        Raises:
            ErasureCascadeDisabledError / ErasureRefusedError / ErasureNotImplementedError: como `erase`
            (sem o legal-hold: verificar nao apaga).
        """
        self._require_enabled("verify")
        self._require_inputs("verify", tenant_id, fhir_patient_id)
        plan = self._load_plan("verify", tenant_id)
        actionable = self._preflight("verify", tenant_id, plan)
        surrogate = self._bound_surrogate()
        clean_by_camada: dict[str, bool] = {}
        residual_relations: list[str] = []
        for layer, decisao in actionable:
            residual = self._residual(self._executors[layer.tabela], tenant_id, fhir_patient_id, decisao)
            clean = residual == 0
            if not clean:
                residual_relations.append(layer.tabela)
            clean_by_camada[layer.camada] = clean_by_camada.get(layer.camada, True) and clean
        surrogate_clean = self._surrogate_residual(surrogate, tenant_id, fhir_patient_id) == 0
        status = STATUS_CLEAN if not residual_relations and surrogate_clean else STATUS_DIRTY
        logger.info(
            "erasure_verify_finished",
            tenant_id=tenant_id,
            status=status,
            residual_relations=residual_relations,
            surrogate_clean=surrogate_clean,
        )
        return {
            "working_clean": clean_by_camada.get("trabalho", False),
            "episodic_clean": clean_by_camada.get("episodica", False),
            "semantic_clean": False,
            "surrogate_clean": surrogate_clean,
            "residual": residual_relations,
            "status": status,
        }

    # ------------------------------------------------------------------ helpers

    def _bound_surrogate(self) -> SurrogateLinkStore:
        """O store do surrogate, ja exigido pelo pre-voo (recusa de novo se sumiu)."""
        if self._surrogate is None:
            raise ErasureNotImplementedError(REASON_SURROGATE_UNBOUND, "store do surrogate nao vinculado.")
        return self._surrogate

    @staticmethod
    def _static_outcomes(plan: ErasurePlan) -> dict[str, LayerErasureOutcome]:
        from maezo.platform.lifecycle.erasure_plan import PERSISTENCE_LAYERS, IdentityResolution

        outcomes: dict[str, LayerErasureOutcome] = {}
        for layer in PERSISTENCE_LAYERS:
            decision = plan.get(layer.tabela)
            decisao = decision.decisao_dpo if decision is not None else ""
            if layer.resolucao is IdentityResolution.RETIRADA:
                disposicao = "retirada"
            else:
                disposicao = "retido" if decisao not in _ACTIONABLE_DECISIONS else "nao_executado"
            outcomes[layer.tabela] = LayerErasureOutcome(layer.tabela, layer.camada, decisao, disposicao)
        return outcomes

    @staticmethod
    def _residual(
        executor: ErasureLayerExecutor, tenant_id: str, fhir_patient_id: str, decisao: str
    ) -> int | None:
        try:
            value = executor.remaining(tenant_id, fhir_patient_id, decisao)
        except Exception as exc:  # executor e do chamador
            logger.error("erasure_verify_executor_failed", tenant_id=tenant_id, error_type=type(exc).__name__)
            return None
        return value if _is_count(value) else None

    @staticmethod
    def _surrogate_residual(store: SurrogateLinkStore, tenant_id: str, fhir_patient_id: str) -> int | None:
        try:
            value = store.remaining(tenant_id, fhir_patient_id)
        except Exception as exc:  # store e do chamador
            logger.error(
                "erasure_verify_surrogate_failed", tenant_id=tenant_id, error_type=type(exc).__name__
            )
            return None
        return value if _is_count(value) else None

    @staticmethod
    def _camada_clean(verified: Mapping[str, LayerErasureOutcome], camada: str) -> bool:
        relevant = [o for o in verified.values() if o.camada == camada]
        return bool(relevant) and all(o.disposicao == "aplicado" and o.residual == 0 for o in relevant)
