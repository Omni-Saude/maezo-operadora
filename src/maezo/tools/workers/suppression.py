"""Worker: `vendor.suppression.{verify_subject,route}` — envelope GP11 (VW4, OP20).

Dois handlers externos de SP-OP-SUPP-001 (`spec/processes/bpmn/SP-OP-SUPP-001_
Direitos_de_Nao_Contato.bpmn`), no estado de ADMISSAO fail-closed registrado pelo dono
(2026-10-07 — VW0-DECISION-REGISTER §"Decisoes de Fechamento", item 1):

- `verify_subject` resolve o sujeito da supressao (referencia OPACA; minimizacao art. 10
  §1o). O store de supressao AINDA NAO EXISTE (migração/tabela vivem no pacote de wiring
  seguinte) e a fonte, enquanto ausente, e UNKNOWN — nunca zero (molde
  `erasure_plan.py#IdentityResolution` L193: recusar != reportar zero). O handler levanta
  SEMPRE `ERR_SUPP_SUBJECT_UNRESOLVED` (erro modelado, boundary em `ST_VerificarSujeito`)
  via a fonte recusante — quando o store pousar, a MESMA superficie passa a resolver e o
  handler nao muda de forma.

- `route` avaliaria a DMN `suppression_routing` ENGINE-SIDE via `CibSevenDmnTransport`
  (evaluator local PROIBIDO — `dmn_transport.py:1-8`). O wiring do transport a este topico
  chega com os insumos DPO (taxonomia VW0-D11 / SLA VW0-D20); sem ele, levanta SEMPRE
  `ERR_SUPP_ROUTING_UNAVAILABLE` (boundary em `ST_AvaliarRoteamento`) — roteamento
  automatico sem insumos ratificados nunca existe, nem por omissao.

NENHUM dos dois handlers fabrica desfecho: a recusa tipada E o estado de admissao
("o desenho opera com recusas tipadas ate os insumos chegarem" — registro do dono). O
harness ladder reporta `WorkerBpmnError` como bpmnError real (codigos consumption-covered
ADR-0030 desde o nascimento — zero dead models) e o processo termina nos terminais neutros
visiveis a DPO.

WHY RAW `harness.register()` (espelha `vendor_admin.py`/`events.py`): os handlers leem
variaveis de processo por contrato de topico DECLARADO (`variables=(...)` — fetch scope
explicito, nunca o `None` legado) e devolvem/levantam; o acoplamento a DMN engine-side
do `route` e o motivo de nao serem dict-first `FunctionWorker` (a assinatura engine-side
do transport e async contra seams do deployment).

`kafka` accepted-but-unused (`del kafka`, precedente `ans_cron.py`/`vendor_admin.py`):
egress de eventos de dominio e SEMPRE via `operadora.events.publish` (topico generico
consumido), nunca via broker direto deste modulo.

Idempotente como todo bootstrap: `WorkerHarness.register` substitui no re-registro, mesmo
topico. Prefixo `vendor.suppression.` — plano vendor (raiz `vendor.`), nao SP-OP-*.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from maezo.tools.workers.harness import WorkerBpmnError

if TYPE_CHECKING:
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


def make_verify_subject_handler(store: Any | None) -> TaskHandler:
    """Create the `vendor.suppression.verify_subject` handler.

    `store=None` (estado de admissao: a fonte ainda nao existe) recusa com o erro modelado
    ANTES de qualquer leitura — UNKNOWN, nunca zero. Quando o store real pousar (PR-2), a
    fonte passa a resolver `subject_ref` opaco (minimizacao art. 10 §1o) e o handler passa
    a devolver `subject_resolved=True`; a forma de recusa permanece a mesma para
    store ausente/ilegivel.
    """
    del store  # estado de admissao: nenhuma fonte instalada — a recusa e o proprio contrato

    async def handler(task: ExternalTask) -> Mapping[str, Any] | None:
        raise WorkerBpmnError(
            ERR_SUPP_SUBJECT_UNRESOLVED,
            "verify_subject: store de supressao ausente (estado de admissao GP11) — UNKNOWN, "
            "nunca zero; insumos/wiring chegam no pacote de wiring (PR-2).",
        )

    return handler


def make_route_handler(transport: Any | None) -> TaskHandler:
    """Create the `vendor.suppression.route` handler.

    `transport=None` (o `CibSevenDmnTransport` ainda nao esta servido a este topico) recusa
    com o erro modelado — roteamento automatico sem insumos ratificados (taxonomia VW0-D11,
    SLA VW0-D20) nunca existe. Com o transport servido, o handler avalia
    `suppression_routing` engine-side e devolve `rota` (valor fechado) + `grupo_decisao`.
    """
    del transport  # estado de admissao: DMN engine-side nao servida — recusa e o contrato

    async def handler(task: ExternalTask) -> Mapping[str, Any] | None:
        raise WorkerBpmnError(
            ERR_SUPP_ROUTING_UNAVAILABLE,
            "route: CibSevenDmnTransport nao servido a vendor.suppression.route (insumos DPO "
            "pendentes — VW0-D11/VW0-D20); rota automatica sem insumos nunca existe.",
        )

    return handler


def register_suppression_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register both SP-OP-SUPP-001 external-task handlers on `harness`.

    Raw-handler registration (`harness.register`) com fetch scope EXPLICITO por topico —
    as variaveis declaradas sao EXATAMENTE as do contrato OP20 (minimizacao: identificador
    + data + canal, nada mais; `subject_ref` opaco). `suppression_ref` nao e fetchado:
    e variavel de negocio da instancia usada pela business key, nao input dos handlers
    neste estado.

    Seams reservados para o pacote de wiring (PR-2): `suppression_store` (fonte de
    resolucao de sujeito) e `suppression_dmn_transport` (`CibSevenDmnTransport`). Nenhum
    dos dois existe ainda — os handlers recusam com os erros modelados, que e o estado de
    admissao registrado (nunca um stub que responde).
    """
    del kafka  # unused — egress de eventos e via operadora.events.publish (docstring)
    harness.register(
        SUPPRESSION_VERIFY_TOPIC,
        make_verify_subject_handler(seams.get("suppression_store")),
        variables=("tenant_id", "subject_ref", "contact_channel"),
    )
    harness.register(
        SUPPRESSION_ROUTE_TOPIC,
        make_route_handler(seams.get("suppression_dmn_transport")),
        variables=("tenant_id", "canal", "categoria_sujeito"),
    )
