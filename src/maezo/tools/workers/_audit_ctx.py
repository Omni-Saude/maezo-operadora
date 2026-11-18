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

from collections.abc import Callable, Iterator, Mapping
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

#: DL-0185/DL-0187: modos de uma composicao registrada. `sombra` = a avaliacao nao muda o caso; `efeito` = a
#: decisao pode ter efeito. DL-0187 (emenda ao DL-0185): SO `efeito` enfileira o aviso; `sombra` grava a
#: auditoria (`sinaisAuditoria` e `composicaoK1`) e conta
#: `maezo_rco_aviso_total{desfecho="suprimido_sombra"}`.
#: O modo vem de quem registra (a avaliacao), nunca do indicador de efeito do K1. Tambem e o rotulo `modo` de
#: `rco_composicao.compor` e de `maezo_rco_composicao_revisao_humana_total`.
MODOS_COMPOSICAO: Final[frozenset[str]] = frozenset({"sombra", "efeito"})
MODO_SOMBRA: Final[str] = "sombra"
MODO_EFEITO: Final[str] = "efeito"
#: DL-0185: a chave acrescentada a entrada de `dmn_versions` de cada decisao avaliada do grupo.
CHAVE_SINAIS_AUDITORIA: Final[str] = "sinaisAuditoria"
#: DL-0187 (PR-4): a chave aditiva com o resumo da composicao na entrada de cada decisao avaliada do grupo:
#: `{nome, modo, desfecho, decisaoVencedora, motivo, peloPadrao}`, so tokens fechados, ids e booleano. E por
#: ela que a reconciliacao sabe o modo pelo registro DURAVEL (so `efeito` espera linha no outbox).
CHAVE_COMPOSICAO_K1: Final[str] = "composicaoK1"
#: Desfechos e motivos de `rco_composicao.ResultadoComposicao` (paridade em teste; literais aqui porque
#: `rco_composicao` importa `dmn_transport`, que importa este modulo).
DESFECHOS_COMPOSICAO: Final[frozenset[str]] = frozenset({"escolhido", "revisao_humana"})
MOTIVOS_COMPOSICAO: Final[frozenset[str]] = frozenset(
    {"violacao_prova_rco", "avaliador_falhou", "resultado_invalido"}
)


@dataclass(frozen=True, slots=True)
class ComposicaoRegistrada:
    """O que o harness precisa de uma composicao K1 da tarefa para enfileirar os avisos (DL-0185/DL-0187).

    So ids e codigos: o nome da composicao do manifesto, a uniao dos `sinalAuditoria` nao vazios (na ordem
    do grupo), o modo, as decisoes avaliadas (para reaplicar as chaves na auditoria,
    `reaplicar_sinais_auditoria`) e o resumo do desfecho (`composicaoK1`). Nenhuma linha da DMN, nenhuma
    entrada do caso.
    """

    composicao: str
    sinais: tuple[str, ...]
    modo: str
    avaliadas: tuple[str, ...] = ()
    desfecho: str = "escolhido"
    decisao_vencedora: str | None = None
    motivo: str | None = None
    pelo_padrao: bool = False

    def composicao_k1(self) -> dict[str, Any]:
        """A chave `composicaoK1` (DL-0187): so tokens, ids e booleano."""
        return {
            "nome": self.composicao,
            "modo": self.modo,
            "desfecho": self.desfecho,
            "decisaoVencedora": self.decisao_vencedora,
            "motivo": self.motivo,
            "peloPadrao": self.pelo_padrao,
        }


# Module-level collector. `None` means "not inside an audited dispatch" — see module docstring.
# The value is a MUTABLE dict deliberately (the container survives the to_thread context copy).
_DMN_AUDIT_COLLECTOR: ContextVar[dict[str, Any] | None] = ContextVar("dmn_audit_collector", default=None)
# MZO-5c-2 (ADR-0077 §4): as avaliacoes de regra K1 da mesma tarefa, por decision key (ultima vence, como
# `dmn_versions`). Mesmo desenho: container mutavel que sobrevive a copia de contexto do `to_thread`.
_RCO_AVALIACOES: ContextVar[dict[str, AvaliacaoRegra] | None] = ContextVar("rco_avaliacoes", default=None)
# DL-0185: as composicoes K1 da mesma tarefa, por nome da composicao (ultima vence). Mesmo desenho.
_RCO_COMPOSICOES: ContextVar[dict[str, ComposicaoRegistrada] | None] = ContextVar(
    "rco_composicoes", default=None
)

# DL-0187: trabalho de SOMBRA que o worker adia para DEPOIS de completar a tarefa (lista de chamaveis sem
# argumento, na ordem). Mesmo desenho dos coletores acima: container mutavel que sobrevive a copia de
# contexto.
_POS_CONCLUSAO: ContextVar[list[Callable[[], None]] | None] = ContextVar("pos_conclusao", default=None)


def adiar_pos_conclusao(trabalho: Callable[[], None]) -> bool:
    """Adia `trabalho` (sincrono, sem argumentos) para DEPOIS de completar a tarefa (DL-0187). NUNCA levanta.

    So para trabalho de SOMBRA, que nao pode custar nada ao caso: o harness o roda depois de `complete`, fora
    do bloqueio da tarefa, com coletores proprios, e grava o que ele auditar num fato separado da cadeia
    (`WorkerHarness._executar_pos_conclusao`). Devolve `False` (e nada roda) fora de um despacho auditado;
    o mesmo fail-open de `record_dmn_version`. Numa tarefa que nao completa, o trabalho adiado e descartado.
    """
    adiados = _POS_CONCLUSAO.get()
    if adiados is None:
        return False
    adiados.append(trabalho)
    return True


@contextmanager
def collect_pos_conclusao() -> Iterator[list[Callable[[], None]]]:
    """Liga a lista do trabalho adiado da tarefa (DL-0187). Reset por tarefa, como `collect_dmn_versions`."""
    adiados: list[Callable[[], None]] = []
    token = _POS_CONCLUSAO.set(adiados)
    try:
        yield adiados
    finally:
        _POS_CONCLUSAO.reset(token)


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
    """Registra uma composicao K1 avaliada nesta tarefa (DL-0185/DL-0187). NUNCA levanta.

    CONTRATO DE LIGACAO: `rco_composicao.compor`/`compor_async` sao PUROS e NAO chamam isto (nem devem:
    `test_compor_e_puro_e_nao_registra_nada`). Quem compoe chama `registrar_composicao(resultado, modo=...)`
    logo depois de compor, com o modo (`MODOS_COMPOSICAO`): `sombra` quando a avaliacao nao muda o caso,
    `efeito` quando pode mudar. A composicao em sombra (`rco_sombra.avaliar_composicao`, ligada ao AUTH por
    `auth_rco`) registra com `modo=sombra` (DL-0187). Faz tres coisas, todas sem efeito fora do coletor:

    1. guarda `ComposicaoRegistrada` no coletor de `collect_rco_composicoes` (o harness enfileira os avisos
       DEPOIS de completar a tarefa, SO os de `modo=efeito`; os de `sombra` sao contados como
       `suprimido_sombra`, DL-0187);
    2. acrescenta `composicaoK1` (DL-0187: nome, modo, desfecho, decisao vencedora, motivo, pelo padrao) a
       entrada de `dmn_versions` de cada decisao avaliada do grupo que ja esta no coletor de
       `collect_dmn_versions`: o modo fica no registro DURAVEL, que a reconciliacao le;
    3. com sinal nao vazio, acrescenta `sinaisAuditoria` (a uniao, na ordem do grupo) as mesmas entradas.
       Essas entradas vao na auditoria duravel, gravada ANTES de completar: um aviso de `efeito` que nao
       chegou ao outbox pode ser reconciliado pela auditoria. Chaves aditivas.

    Fora de um despacho auditado (sem coletor) e' no-op. Modo, desfecho ou motivo fora do vocabulario ou
    resultado sem a forma esperada: log e metrica `maezo_rco_aviso_total{desfecho="invalido"}`, nada
    registrado. Nunca levanta, porque uma falha do caminho do aviso nao pode mudar a decisao da tarefa.
    """
    try:
        if modo not in MODOS_COMPOSICAO:
            raise ValueError("modo_invalido")
        sinais = tuple(dict.fromkeys(str(s) for s in resultado.sinais if isinstance(s, str) and s.strip()))
        if len(sinais) != len(tuple(resultado.sinais)):
            raise ValueError("sinal_invalido")
        if resultado.desfecho not in DESFECHOS_COMPOSICAO:
            raise ValueError("desfecho_invalido")
        if resultado.motivo is not None and resultado.motivo not in MOTIVOS_COMPOSICAO:
            raise ValueError("motivo_invalido")
        avaliadas = tuple(str(d) for d in resultado.avaliadas)
        vencedora = resultado.decisao_vencedora
        if vencedora is not None and vencedora not in avaliadas:
            raise ValueError("vencedora_invalida")
        if not isinstance(resultado.pelo_padrao, bool):
            raise ValueError("pelo_padrao_invalido")
        registrada = ComposicaoRegistrada(
            composicao=str(resultado.composicao),
            sinais=sinais,
            modo=modo,
            avaliadas=avaliadas,
            desfecho=resultado.desfecho,
            decisao_vencedora=vencedora,
            motivo=resultado.motivo,
            pelo_padrao=resultado.pelo_padrao,
        )
        composicoes = _RCO_COMPOSICOES.get()
        if composicoes is not None:
            composicoes[registrada.composicao] = registrada
        collector = _DMN_AUDIT_COLLECTOR.get()
        if collector is not None:
            _aplicar(collector, registrada)
    except Exception as exc:  # fail-open: o aviso nunca muda a decisao.
        _logger.error("rco_aviso_composicao_nao_registrada", erro=type(exc).__name__)
        try:
            from maezo.platform.observability import record_rco_aviso

            record_rco_aviso("invalido")
        except Exception:  # pragma: no cover - a telemetria nunca derruba o chamador
            _logger.debug("rco_aviso_metric_emit_failed", exc_info=True)


def _aplicar(collector: Mapping[str, Any], registrada: ComposicaoRegistrada) -> None:
    for decisao in registrada.avaliadas:
        entrada = collector.get(decisao)
        if isinstance(entrada, dict):
            entrada[CHAVE_COMPOSICAO_K1] = registrada.composicao_k1()
            if registrada.sinais:
                entrada[CHAVE_SINAIS_AUDITORIA] = list(registrada.sinais)


def reaplicar_sinais_auditoria(
    collector: Mapping[str, Any], composicoes: Mapping[str, ComposicaoRegistrada]
) -> None:
    """Reaplica `composicaoK1` e `sinaisAuditoria` as entradas do grupo logo antes de montar a auditoria
    (DL-0185/DL-0187). NUNCA levanta.

    `record_dmn_version` e "ultima vence": se uma decisao do grupo for reavaliada DEPOIS de
    `registrar_composicao`, a entrada nova perderia as chaves. O harness chama isto com o coletor e as
    composicoes da tarefa antes de `_build_audit_record`, entao a auditoria duravel sempre leva o modo e os
    sinais.
    """
    try:
        for registrada in composicoes.values():
            _aplicar(collector, registrada)
    except Exception as exc:  # fail-open: o aviso nunca muda a decisao.
        _logger.error("rco_aviso_sinais_nao_reaplicados", erro=type(exc).__name__)


@contextmanager
def collect_rco_composicoes() -> Iterator[dict[str, ComposicaoRegistrada]]:
    """Liga um coletor novo das composicoes K1 da tarefa (DL-0185); o harness o le depois de completar a
    tarefa para enfileirar os avisos. Reset por tarefa, como `collect_dmn_versions`."""
    composicoes: dict[str, ComposicaoRegistrada] = {}
    token = _RCO_COMPOSICOES.set(composicoes)
    try:
        yield composicoes
    finally:
        _RCO_COMPOSICOES.reset(token)
