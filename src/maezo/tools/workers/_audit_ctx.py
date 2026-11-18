"""Per-task DMN-version provenance collector (T-B, T1.10; ADR-0007 / ADR-0028).

The problem this closes (design `docs/design/audit-emit-path-wiring.md` §3.1–3.2): a worker
evaluates a DMN table via `dmn_transport.evaluate_sync(...)` and gets back a `DmnVersion`, but
that version is **discarded** inside the worker function — it never reaches the harness, which
only sees the returned output dict. The harness needs those versions to populate
`AuditRecord.dmn_versions` (the ADR-0007 non-repudiation tuple: *which DMN version drove this
money/routing decision*).

Threading the version up through every `fn(variables) -> dict` return boundary would touch ~11
functions across 9 modules (several on the money path) and would leak audit metadata into BPMN
process variables — rejected (design §3.2). Instead this module holds a **module-level
`ContextVar` bound to a mutable dict** used as a per-task collector:

  - `WorkerHarness._handle` opens a fresh collector via `collect_dmn_versions()` around the
    handler dispatch (one collector per task — reset-per-task; each `_handle` runs as its own
    `asyncio.create_task`, so each gets an isolated `contextvars` copy and cannot race another
    task's collector).
  - `evaluate_sync` (the ONE sync funnel every worker DMN eval goes through) calls
    `record_dmn_version(version)` on each successful evaluation, which writes
    `collector[version.key] = version.to_audit_dict()`.

**Why a mutable container survives the sync/async thread bridge.** The harness dispatches sync
workers via `asyncio.to_thread`, which runs them under `contextvars.copy_context()`. A context
copy shares the *same dict object* the var is bound to (only the var→object binding is copied,
not the object), so in-thread `collector[key] = ...` mutations are visible to `_handle` after the
handler returns. A ContextVar holding an *immutable* value would NOT work here; the mutable-dict
container is deliberate (design §3.2).

**Fail-open by construction — best-effort ENRICHMENT, not the fail-closed audit itself.** When
no collector is set (`default=None`: unit tests calling a worker directly, tooling, any code path
outside an audited dispatch), `record_dmn_version` is a silent no-op — a DMN eval that never
reaches an audited dispatch simply yields empty `dmn_versions`. This is acceptable: `dmn_versions`
is provenance *enrichment* of the audit row, distinct from the fail-CLOSED emit-before-complete
guarantee the harness enforces (design §5, review point #5). The audit row is still written; it
just may carry `{}` for `dmn_versions` if the effect consulted no DMN table (many worker topics
consult none).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import structlog

if TYPE_CHECKING:
    from maezo.platform.rco_composicao import ResultadoComposicao
    from maezo.platform.rco_registro import AvaliacaoRegra
    from maezo.tools.workers.dmn_transport import DmnVersion

_logger = structlog.get_logger(__name__)

#: DL-0184: modos de uma composicao registrada. `sombra` = a avaliacao nao muda o caso; `efeito` = a
#: decisao pode ter efeito. Os dois enfileiram o aviso: o modo so vai para o log.
MODOS_COMPOSICAO: Final[frozenset[str]] = frozenset({"sombra", "efeito"})
#: DL-0184: a chave acrescentada a entrada de `dmn_versions` de cada decisao avaliada do grupo.
CHAVE_SINAIS_AUDITORIA: Final[str] = "sinaisAuditoria"


@dataclass(frozen=True, slots=True)
class ComposicaoRegistrada:
    """O que o harness precisa de uma composicao K1 da tarefa para enfileirar os avisos (DL-0184).

    So ids e codigos: o nome da composicao do manifesto, a uniao dos `sinalAuditoria` nao vazios (na ordem
    do grupo) e o modo. Nenhuma linha da DMN, nenhuma entrada do caso.
    """

    composicao: str
    sinais: tuple[str, ...]
    modo: str


# Module-level collector. `None` means "not inside an audited dispatch" — see module docstring.
# The value is a MUTABLE dict deliberately (the container survives the to_thread context copy).
_DMN_AUDIT_COLLECTOR: ContextVar[dict[str, Any] | None] = ContextVar("dmn_audit_collector", default=None)
# MZO-5c-2 (ADR-0077 §4): as avaliacoes de regra K1 da mesma tarefa, por decision key (ultima vence, como
# `dmn_versions`). Mesmo desenho: container mutavel que sobrevive a copia de contexto do `to_thread`.
_RCO_AVALIACOES: ContextVar[dict[str, AvaliacaoRegra] | None] = ContextVar("rco_avaliacoes", default=None)
# DL-0184: as composicoes K1 da mesma tarefa, por nome da composicao (ultima vence). Mesmo desenho.
_RCO_COMPOSICOES: ContextVar[dict[str, ComposicaoRegistrada] | None] = ContextVar(
    "rco_composicoes", default=None
)


def record_dmn_version(
    version: DmnVersion,
    *,
    row: Mapping[str, Any] | None = None,
    entradas_rco: Mapping[str, Any] | None = None,
    sombra: bool = False,
) -> None:
    """Record one consulted DMN version into the current task's collector, if one is set.

    Keyed on `version.key` (the decision-definition key) so evaluating the same table twice in
    one task collapses to a single provenance entry (last write wins — versions are stable within
    a dispatch since `CibSevenDmnTransport` caches the resolved version). No-op when no collector
    is bound (default `None`) — see the module docstring's fail-open note.

    `row` is the matched result row, if exactly one: for an rco K1 table it supplies `idRegra`
    (ADR-0075 §(f), `DmnVersion.to_audit_dict`). The fail-open note above covers only the ABSENT
    collector; an `rco_` table the pin cannot attest still raises `RcoRulesPinError` here.

    `entradas_rco` (MZO-3): the audit of the K1 input builder for this evaluation
    (`rco_sombra.auditoria_das_entradas`: `derivacoes` with `diasBrutos`/`toleranciaAplicada`/
    `valorEntregue`/`motivoTolerancia`, and `entradasNulas` with the null reason per input — integers
    and codes only, never case data). Merged into the entry of an `rco_` table only; ignored for a
    Maezo table, so a Maezo entry keeps exactly `{version, id, deploymentId}`. `sombra` (MZO-3): the
    evaluation is the montador's shadow (`DmnVersion.audit_pair(sombra=True)`): `modo=sombra` always and
    no sampling ordinal in the K1 decision record.
    """
    collector = _DMN_AUDIT_COLLECTOR.get()
    if collector is not None:
        entry, avaliacao = version.audit_pair(row, sombra=sombra)
        if entradas_rco and "shaDmn" in entry:
            entry.update(entradas_rco)
        collector[version.key] = entry
        avaliacoes = _RCO_AVALIACOES.get()
        if avaliacoes is not None:
            # Ultima vence; sem regra casada agora, uma avaliacao anterior da mesma tabela sai (a
            # auditoria tambem so guarda a ultima entrada).
            if avaliacao is None:
                avaliacoes.pop(version.key, None)
            else:
                avaliacoes[version.key] = avaliacao


@contextmanager
def collect_dmn_versions() -> Iterator[dict[str, Any]]:
    """Bind a fresh per-task DMN-version collector for the duration of the block.

    Yields the mutable dict that `record_dmn_version` writes into. Resets the `ContextVar` to its
    previous binding on exit (reset-per-task, design §3.2 / MUST-FIX 1) — so a collector never
    leaks across tasks even on the same OS thread / event loop.
    """
    collector: dict[str, Any] = {}
    token = _DMN_AUDIT_COLLECTOR.set(collector)
    try:
        yield collector
    finally:
        _DMN_AUDIT_COLLECTOR.reset(token)


@contextmanager
def collect_rco_avaliacoes() -> Iterator[dict[str, AvaliacaoRegra]]:
    """Liga um coletor novo das avaliacoes de regra K1 da tarefa (5c-2); o harness o le depois da
    auditoria para gravar o registro de decisoes. Reset por tarefa, como `collect_dmn_versions`."""
    avaliacoes: dict[str, AvaliacaoRegra] = {}
    token = _RCO_AVALIACOES.set(avaliacoes)
    try:
        yield avaliacoes
    finally:
        _RCO_AVALIACOES.reset(token)


def registrar_composicao(resultado: ResultadoComposicao, *, modo: str) -> None:
    """Registra uma composicao K1 avaliada nesta tarefa (DL-0184). NUNCA levanta.

    Quem compoe (`rco_composicao.compor`/`compor_async`) chama isto com o resultado e o modo
    (`MODOS_COMPOSICAO`): `sombra` quando a avaliacao nao muda o caso, `efeito` quando pode mudar. Faz duas
    coisas, ambas sem efeito fora do coletor:

    1. guarda `ComposicaoRegistrada` no coletor de `collect_rco_composicoes` (o harness enfileira os avisos
       DEPOIS de completar a tarefa);
    2. com sinal nao vazio, acrescenta `sinaisAuditoria` (a uniao, na ordem do grupo) a entrada de
       `dmn_versions` de cada decisao avaliada do grupo que ja esta no coletor de `collect_dmn_versions`.
       Essa entrada vai na auditoria duravel, gravada ANTES de completar: um aviso que nao chegou ao outbox
       pode ser reconciliado pela auditoria. Chave aditiva; sem sinal, a entrada fica byte a byte igual.

    Fora de um despacho auditado (sem coletor) e' no-op. Modo fora do vocabulario ou resultado sem a forma
    esperada: log e metrica `maezo_rco_aviso_total{desfecho="invalido"}`, nada registrado. Nunca levanta,
    porque uma falha do caminho do aviso nao pode mudar a decisao da tarefa.
    """
    try:
        if modo not in MODOS_COMPOSICAO:
            raise ValueError("modo_invalido")
        sinais = tuple(dict.fromkeys(str(s) for s in resultado.sinais if isinstance(s, str) and s.strip()))
        if len(sinais) != len(tuple(resultado.sinais)):
            raise ValueError("sinal_invalido")
        registrada = ComposicaoRegistrada(composicao=str(resultado.composicao), sinais=sinais, modo=modo)
        composicoes = _RCO_COMPOSICOES.get()
        if composicoes is not None:
            composicoes[registrada.composicao] = registrada
        collector = _DMN_AUDIT_COLLECTOR.get()
        if collector is not None and sinais:
            for decisao in resultado.avaliadas:
                entrada = collector.get(decisao)
                if isinstance(entrada, dict):
                    entrada[CHAVE_SINAIS_AUDITORIA] = list(sinais)
    except Exception as exc:  # fail-open: o aviso nunca muda a decisao.
        _logger.error("rco_aviso_composicao_nao_registrada", erro=type(exc).__name__)
        try:
            from maezo.platform.observability import record_rco_aviso

            record_rco_aviso("invalido")
        except Exception:  # pragma: no cover - a telemetria nunca derruba o chamador
            _logger.debug("rco_aviso_metric_emit_failed", exc_info=True)


@contextmanager
def collect_rco_composicoes() -> Iterator[dict[str, ComposicaoRegistrada]]:
    """Liga um coletor novo das composicoes K1 da tarefa (DL-0184); o harness o le depois de completar a
    tarefa para enfileirar os avisos. Reset por tarefa, como `collect_dmn_versions`."""
    composicoes: dict[str, ComposicaoRegistrada] = {}
    token = _RCO_COMPOSICOES.set(composicoes)
    try:
        yield composicoes
    finally:
        _RCO_COMPOSICOES.reset(token)
