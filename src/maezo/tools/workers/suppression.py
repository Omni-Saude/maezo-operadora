"""Worker: `vendor.suppression.{verify_subject,route}` — wiring GP11 (VW4, OP20).

Dois handlers externos de SP-OP-SUPP-001 (`spec/processes/bpmn/SP-OP-SUPP-001_
Direitos_de_Nao_Contato.bpmn`), agora no estado de WIRING materializado sobre os insumos DPO
ACEITOS PELO DONO (2026-10-07 — VW0-DECISION-REGISTER §"INCORPORACAO VW4-ANSWERS", sha
`ab262f7b…`; conteúdo `VW4-RATIFICATION-ANSWERS-V1.md` §GP11, tabela de ripple §8):

- `verify_subject` resolve o sujeito da supressao no STORE (seam `suppression_store` —
  `gateway/vendor_suppression.py#SuppressionStore`, migration 0021). A referencia e OPACA:
  o handler cunha o MESMO digest canonico do plano de aplicacao
  (`suppression_ref_of(subject_ref, contact_channel)`) e consulta; devolve `subject_resolved
  =True` + categoria declarada + o RELÓGIO do registro (`t_lembrete/t_escalonado/t_deadline`
  ISO — computados no nascimento pelo calendário own-code de dias úteis), que os timers
  `timeDate` de escalonamento 50/80/100% do processo leem. Store AUSENTE/ILEGÍVEL/registro
  inexistente MANTÉM `ERR_SUPP_SUBJECT_UNRESOLVED` — UNKNOWN, nunca zero (molde
  `erasure_plan.py#IdentityResolution` L193); a forma de recusa de admissao foi preservada
  byte-a-byte na transicao de estado.

- `route` avalia a DMN `suppression_routing` ENGINE-SIDE via o seam `dmn=` (ADR-0028 —
  `CibSevenDmnTransport`, thread pelo runtime a TODOS os workers; evaluator local PROIBIDO,
  `dmn_transport.py:1-8`), com inputs de CLASSE da taxonomia C1–C6 ratificada
  (`classe_campo`, `celula_tamanho` vs `k_piso` = constante ratificada `K_ANON_FLOOR` —
  `k` é input/param, a tabela nunca carrega a cifra). Transport NÃO servido MANTÉM
  `ERR_SUPP_ROUTING_UNAVAILABLE`. `DmnEvaluationError` (engine inacessível) PROPAGA — é
  TRANSIENT (ADR-0028 §3: engine retry); `DmnNoResultError`/linha fora do vocabulário fechado
  de rota é drift de contrato ⇒ recusa modelada (no-match) ou incidente tipado
  `CONTRACT_MISMATCH` (`failure(retries=0)` — humano visível, nunca rota fabricada).

GUARDA DE SEAMS (molde `vendor_admin.py`): registro SEMPRE sucede; seam ausente ou com forma
errada ⇒ recusa tipada em todo tick — o tópico fica visível e a recusa é LOUD. Um seam
placeholder (`object()`) NÃO autoriza resposta: a guarda exige a operação da fonte.

NENHUM handler fabrica desfecho: `REGISTRO_HONRADO` só existe EXPLÍCITO da DMN (GW_Rota do
processo: ausente/branco/desconhecido vai SEMPRE ao humano — nunca honra por omissão). Os
dois códigos modelados continuam consumption-covered ADR-0030 (boundaries de SP-OP-SUPP-001,
`SUPPRESSION_BPMN_ERROR_ALLOWLIST` unida a producao).

WHY RAW `harness.register()` (espelha `vendor_admin.py`/`events.py`): os handlers leem
variaveis de processo por contrato de topico DECLARADO (`variables=(...)` — fetch scope
explicito, nunca o `None` legado) e devolvem/levantam; o acoplamento a DMN engine-side
do `route` e o motivo de nao serem dict-first `FunctionWorker`.

`kafka` accepted-but-unused (`del kafka`, precedente `ans_cron.py`/`vendor_admin.py`):
egress de eventos de dominio e SEMPRE via `operadora.events.publish` (topico generico
consumido), nunca via broker direto deste modulo.

Idempotente como todo bootstrap: `WorkerHarness.register` substitui no re-registro, mesmo
topico. Prefixo `vendor.suppression.` — plano vendor (raiz `vendor.`), nao SP-OP-*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from maezo.gateway.vendor_suppression import K_ANON_FLOOR, suppression_ref_of
from maezo.tools.workers.dmn_transport import DmnNoResultError, first_row
from maezo.tools.workers.harness import WorkerBpmnError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from maezo.tools.workers.harness import ExternalTask, KafkaPublisher, TaskHandler, WorkerHarness

#: Resolucao de sujeito da supressao (boundary `ST_VerificarSujeito`).
SUPPRESSION_VERIFY_TOPIC = "vendor.suppression.verify_subject"
#: Roteamento engine-side da supressao (boundary `ST_AvaliarRoteamento`).
SUPPRESSION_ROUTE_TOPIC = "vendor.suppression.route"

#: Erros modelados (consumption-covered ADR-0030 — boundaries de SP-OP-SUPP-001).
ERR_SUPP_SUBJECT_UNRESOLVED = "ERR_SUPP_SUBJECT_UNRESOLVED"
ERR_SUPP_ROUTING_UNAVAILABLE = "ERR_SUPP_ROUTING_UNAVAILABLE"

#: Gate-proven boundary codes deste modulo (ADR-0030) — unidos em
#: `worker_runtime/service.py#_GATE_PROVEN_BPMN_ERROR_CODES`; ambos NAO-`*_NOT_HUMAN`
#: (fail-safes tecnicos de admissao) => Tier-0 direto.
SUPPRESSION_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset(
    {ERR_SUPP_SUBJECT_UNRESOLVED, ERR_SUPP_ROUTING_UNAVAILABLE}
)

#: Chave da DMN engine-side consumida por `route` (spec/processes/dmn/suppression_routing.dmn).
SUPPRESSION_ROUTING_DMN: Final = "suppression_routing"

#: Vocabulário FECHADO de `rota` (contrato OP20/GP11): só `REGISTRO_HONRADO` EXPLÍCITO honra
#: (GW_Rota); todo o resto vai ao humano — saída fora deste conjunto é drift de contrato.
_ROUTE_VOCABULARY: Final[frozenset[str]] = frozenset({"REGISTRO_HONRADO", "RECLAMACAO_ENCARREGADO"})


class SuppressionContractMismatchError(ValueError):
    """Drift de contrato na linha devolvida pela DMN — incidente `failure(retries=0)`.

    Recusa da CAMADA DE APLICACAO (contrato SP-OP-SUPP-001: `CONTRACT_MISMATCH` nunca é erro
    de processo engine): o handler NUNCA fabrica rota, NUNCA honra por omissão — uma DMN que
    devolva `rota` fora do vocabulário fechado (ou sem `grupo_decisao`) vira incidente
    humano-visível, codificado `CONTRACT_MISMATCH` (convenção do pacote — `DmnNoResultError`).
    """

    def __init__(self, detail: str) -> None:
        self.code = "CONTRACT_MISMATCH"
        self.message = f"suppression.route: {detail}"
        super().__init__(self.message)


def _usable(store: Any, *operations: str) -> bool:
    """Guarda de seam: presente E com as operações esperadas callable (placeholder recusa)."""
    if store is None:
        return False
    return all(callable(getattr(store, operation, None)) for operation in operations)


def make_verify_subject_handler(store: Any | None) -> TaskHandler:
    """Create the `vendor.suppression.verify_subject` handler — resolução REAL no store 0021.

    Guarda de seam (molde vendor_admin): store ausente ou sem a operação `resolve` ⇒ recusa
    modelada ANTES de qualquer leitura — UNKNOWN, nunca zero. Com a fonte servida, o handler
    cunha o digest canônico do par opaco (o MESMO do plano de aplicação) e consulta; registro
    inexistente e fonte ilegível devolvem a MESMA recusa (a forma não muda com a causa).
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any] | None:
        if not _usable(store, "resolve"):
            raise WorkerBpmnError(
                ERR_SUPP_SUBJECT_UNRESOLVED,
                "verify_subject: store de supressao ausente/sem forma de fonte (seam "
                "`suppression_store`) — UNKNOWN, nunca zero.",
            )
        variables = task.variables
        tenant = variables.get("tenant_id")
        subject_ref = variables.get("subject_ref")
        contact_channel = variables.get("contact_channel")
        if not (isinstance(tenant, str) and tenant and isinstance(subject_ref, str) and subject_ref
                and isinstance(contact_channel, str) and contact_channel):
            # Identificador ausente/vazio = sujeito irresolvível — nunca um "zero supressões".
            raise WorkerBpmnError(
                ERR_SUPP_SUBJECT_UNRESOLVED,
                "verify_subject: identificador opaco ausente (tenant_id/subject_ref/"
                "contact_channel) — UNKNOWN, nunca zero.",
            )
        assert store is not None  # narrowed pelo guarda de seam
        suppression_ref = suppression_ref_of(subject_ref, contact_channel)
        try:
            record = await store.resolve(tenant, suppression_ref)
        except Exception as exc:
            raise WorkerBpmnError(
                ERR_SUPP_SUBJECT_UNRESOLVED,
                "verify_subject: fonte ilegivel (store levantou "
                f"{type(exc).__name__}) — UNKNOWN, nunca zero.",
            ) from None
        if record is None:
            raise WorkerBpmnError(
                ERR_SUPP_SUBJECT_UNRESOLVED,
                "verify_subject: registro de supressao inexistente para o par opaco — UNKNOWN, "
                "nunca zero.",
            )
        # Relógio visível (§4c): as datas computadas NO NASCIMENTO alimentam os timers
        # timeDate de escalonamento do processo (idioma CONTAS GAP-4 — âncora = o fato
        # gerador, nunca o attach da User Task). Datas e categoria declarada não são PHI
        # (art. 10 §1o: identificador + data + canal é o próprio conteúdo minimizado).
        return {
            "subject_resolved": True,
            "categoria_sujeito": record.categoria_sujeito,
            "canal": record.canal,
            "t_lembrete_iso": record.t_lembrete.isoformat(),
            "t_escalonado_iso": record.t_escalonado.isoformat(),
            "t_deadline_iso": record.t_deadline.isoformat(),
        }

    return handler


def make_route_handler(transport: Any | None) -> TaskHandler:
    """Create the `vendor.suppression.route` handler — DMN engine-side via o seam `dmn=`.

    Guarda de seam: transport ausente ou sem `evaluate` ⇒ recusa modelada (roteamento
    automático sem fonte nunca existe). Com o transport servido, avalia `suppression_routing`
    com inputs de CLASSE (`classe_campo`/`celula_tamanho` declarados no pedido; `k_piso` = a
    constante RATIFICADA — a cifra vive no código aceito, nunca na tabela). `DmnEvaluationError`
    propaga (transient — engine retry); no-match e drift de vocabulário fail-closed (recusa
    modelada / incidente `CONTRACT_MISMATCH`), nunca rota fabricada.
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any] | None:
        if not _usable(transport, "evaluate"):
            raise WorkerBpmnError(
                ERR_SUPP_ROUTING_UNAVAILABLE,
                "route: CibSevenDmnTransport nao servido a vendor.suppression.route (seam "
                "`dmn=`/`suppression_dmn_transport`) — rota automatica sem fonte nunca existe.",
            )
        variables = task.variables
        celula = variables.get("celula_tamanho")
        inputs: dict[str, Any] = {
            "canal": str(variables.get("canal") or ""),
            "categoria_sujeito": str(variables.get("categoria_sujeito") or ""),
            "classe_campo": str(variables.get("classe_campo") or ""),
            "celula_tamanho": celula if type(celula) is int else None,
            "k_piso": K_ANON_FLOOR,
        }
        assert transport is not None  # narrowed pelo guarda de seam
        try:
            rows, version = await transport.evaluate(SUPPRESSION_ROUTING_DMN, inputs)
            row = first_row(rows, SUPPRESSION_ROUTING_DMN, inputs)
        except DmnNoResultError:
            # Determinístico (nunca transient): a tabela que embarca com catch-all não pode
            # deixar de casar sem drift — recusa modelada, humano assume.
            raise WorkerBpmnError(
                ERR_SUPP_ROUTING_UNAVAILABLE,
                "route: DMN suppression_routing sem match (drift de catch-all) — rota "
                "indisponivel, humano assume.",
            ) from None
        rota = row.get("rota")
        grupo = row.get("grupo_decisao")
        rota_valida = isinstance(rota, str) and rota in _ROUTE_VOCABULARY
        grupo_valido = isinstance(grupo, str) and bool(grupo)
        if not (rota_valida and grupo_valido):
            raise SuppressionContractMismatchError(
                "linha da DMN fora do vocabulario fechado "
                f"(rota={rota!r}, grupo_decisao={'presente' if grupo else 'ausente'}) — nunca "
                "honra por omissao, nunca rota fabricada."
            )
        return {
            "rota": rota,
            "grupo_decisao": grupo,
            "dmn_decision_version": version.version,
        }

    return handler


def register_suppression_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register both SP-OP-SUPP-001 external-task handlers on `harness`.

    Raw-handler registration (`harness.register`) com fetch scope EXPLICITO por topico —
    contrato de topico = minimizacao art. 10 §1o (identificador + canal, nada mais;
    `subject_ref` opaco). A DATA do registro nao e variavel de processo e portanto nao e
    fetchada — o timestamp nasce NO STORE (calendario own-code do wiring) e VOLTA como
    output do verify (`t_*_iso`), onde os timers timeDate do processo o leem. `route`
    fetcha TAMBEM os fatos de CLASSE do contexto lista/egresso (`classe_campo` token
    declarado, `celula_tamanho` cardinalidade agregada — nunca conteudo) e recebe `k_piso`
    da constante ratificada (nunca de variavel de processo — fonte única).

    Seams: `suppression_store` (fonte de resolução de sujeito — migration 0021) e o transport
    DMN (o `suppression_dmn_transport` reservado pelo envelope, com fallback ao seam
    plataforma `dmn=` que o runtime thread a todos os workers). Seam ausente/sem forma ⇒
    recusa tipada em todo tick (molde vendor_admin) — o tópico fica visível e a recusa é loud.
    """
    del kafka  # unused — egress de eventos e via operadora.events.publish (docstring)
    harness.register(
        SUPPRESSION_VERIFY_TOPIC,
        make_verify_subject_handler(seams.get("suppression_store")),
        variables=("tenant_id", "subject_ref", "contact_channel"),
    )
    harness.register(
        SUPPRESSION_ROUTE_TOPIC,
        make_route_handler(seams.get("suppression_dmn_transport") or seams.get("dmn")),
        variables=("tenant_id", "canal", "categoria_sujeito", "classe_campo", "celula_tamanho"),
    )
