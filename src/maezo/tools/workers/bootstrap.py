"""Composition root — `register_all_workers` (T1.2/ADR-0026 Decisao §4).

Composes all 16 `register_<domain>_workers(harness, kafka=None, **seams)` bootstraps (the
donor's `_register_all_workers` shape, T1.1 design §16) into a single call the worker-runtime
daemon makes at boot (`worker_runtime/service.py` STEP B). This is the ONLY place that imports
every domain worker module — the modules themselves stay independent of each other.

3 `WorkerBase` class modules (their classes/topics are unchanged, only newly bootstrap-wrapped):
    auth, escalation, lgpd
7 dict-first `FunctionWorker`-wrapped modules:
    adequacao, ans_cron, credenciamento, fraude, inadimplencia, pagto, programa
6 typed-I/O modules with new dict-boundary entry functions (ADR-0026 §2b), `FunctionWorker`-wrapped:
    ans_submit, cancel, contas, nip, recurso, reembolso
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maezo.tools.workers.adequacao import register_adequacao_workers
from maezo.tools.workers.ans_cron import register_ans_cron_workers
from maezo.tools.workers.ans_submit import register_ans_submit_workers
from maezo.tools.workers.auth import register_auth_workers
from maezo.tools.workers.cancel import register_cancel_workers
from maezo.tools.workers.contas import register_contas_workers
from maezo.tools.workers.credenciamento import register_credenciamento_workers
from maezo.tools.workers.escalation import register_escalation_workers
from maezo.tools.workers.fraude import register_fraude_workers
from maezo.tools.workers.inadimplencia import register_inadimplencia_workers
from maezo.tools.workers.lgpd import register_lgpd_workers
from maezo.tools.workers.nip import register_nip_workers
from maezo.tools.workers.pagto import register_pagto_workers
from maezo.tools.workers.programa import register_programa_workers
from maezo.tools.workers.recurso import register_recurso_workers
from maezo.tools.workers.reembolso import register_reembolso_workers

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

#: All 16 per-module bootstraps, in a stable (alphabetical-by-module) order. Exposed so tests can
#: assert 16/16 without re-deriving the list from imports.
ALL_WORKER_BOOTSTRAPS: tuple[Any, ...] = (
    register_adequacao_workers,
    register_ans_cron_workers,
    register_ans_submit_workers,
    register_auth_workers,
    register_cancel_workers,
    register_contas_workers,
    register_credenciamento_workers,
    register_escalation_workers,
    register_fraude_workers,
    register_inadimplencia_workers,
    register_lgpd_workers,
    register_nip_workers,
    register_pagto_workers,
    register_programa_workers,
    register_recurso_workers,
    register_reembolso_workers,
)


def register_all_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register all 16 SP-OP-* worker modules on `harness` (donor's `_register_all_workers`).

    Idempotent (`WorkerHarness.register_worker`/`.register` replace on re-registration, same
    topic) — safe to call more than once against the same harness.
    """
    for bootstrap in ALL_WORKER_BOOTSTRAPS:
        bootstrap(harness, kafka, **seams)
