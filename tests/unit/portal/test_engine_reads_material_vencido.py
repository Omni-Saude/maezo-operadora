"""Material vencido no portal: a fronteira EXATA de validade, com relogio injetado.

`engine_reads.py` recusa o catalogo (`EngineReadBundle.catalog`) e a continuidade nativa
(`check_continuity`) quando `valid_until <= agora`; a conclusao (`EngineHumanTaskTransport.
complete_task`) passa por `bundle.guard()`, que exige o material de leitura (admissao e assinatura,
`read_credentials.py`) vigente: `agora < valid_until`. Os testes existentes
(`test_engine_queue_adapters.py`) usam o mesmo instante de validade para catalogo, tarefa e
continuidade e nunca posicionam o relogio NA fronteira — trocar `<=` por `<` em qualquer uma das
duas linhas passava despercebido. Aqui cada fronteira e' isolada (as outras validades ficam folgadas)
e provada nos dois lados: recusa com motivo FECHADO em `valid_until == agora`, aceite em
`agora == valid_until - 1s`.

Respostas nativas sinteticas (o mesmo `bundle_fixture` da suite irma); nenhum motor e' simulado aqui:
o que se prova e' a custodia de validade do adaptador.
"""

from datetime import timedelta
from typing import Any

import pytest
from tests.unit.portal.test_direct_completion_transport import NOTES, Capability
from tests.unit.portal.test_engine_queue_adapters import bundle_fixture

from maezo.gateway.human.engine_reads import EngineHumanTaskTransport, EngineReadBundle, EngineTaskAuthority
from maezo.gateway.human.queue import ReadRefusalError
from maezo.gateway.human.read_profile import digest
from maezo.gateway.human.read_transport import ReadResult

pytestmark = pytest.mark.asyncio

#: O motivo fechado de toda recusa de leitura por dependencia (`read_credentials.unavailable`).
_MOTIVO = "read_dependency_unavailable"
#: Folga das validades que NAO estao sob teste: a fronteira observada e' sempre a de uma so' linha.
_FOLGA = timedelta(seconds=5)


class _ClienteComValidade:
    """O cliente do `bundle_fixture`, com UMA validade encurtada na resposta nativa."""

    def __init__(
        self, base: Any, *, catalogo_ate: Any = None, continuidade_autoridade_ate: Any = None
    ) -> None:
        self._base = base
        self.partition = base.partition
        self.read_context_id = base.read_context_id
        self._catalogo_ate = catalogo_ate
        self._autoridade_ate = continuidade_autoridade_ate

    def _catalogo(self, valor: Any) -> Any:
        if self._catalogo_ate is None:
            return valor
        expectativa = valor.catalog.expectation.model_copy(update={"valid_until": self._catalogo_ate})
        return valor.model_copy(
            update={"catalog": valor.catalog.model_copy(update={"expectation": expectativa})}
        )

    async def task(self, anchor: Any, task_id: str) -> ReadResult:
        r = await self._base.task(anchor, task_id)
        return ReadResult(self._catalogo(r.value), r.source_observed_at, r.valid_until, r.request_digest)

    async def authority(self, *args: Any) -> ReadResult:
        r = await self._base.authority(*args)
        valor = self._catalogo(r.value)
        if self._autoridade_ate is not None:
            # Uma autoridade de validade mais curta, coerente de ponta a ponta: a propria autoridade, a
            # continuidade que a atesta (`valid_until` iguais, `:371`) e o digest dela (`:370`).
            autoridade = valor.authority.model_copy(update={"valid_until": self._autoridade_ate})
            ca = valor.authority_continuity
            claims = ca.claims.model_copy(
                update={"valid_until": self._autoridade_ate, "authority_digest": digest(autoridade)}
            )
            valor = valor.model_copy(
                update={
                    "authority": autoridade,
                    "authority_continuity": ca.model_copy(update={"claims": claims}),
                }
            )
        return ReadResult(valor, r.source_observed_at, r.valid_until, r.request_digest)


async def _bundle(**validades: Any) -> tuple[EngineReadBundle, Any, Any]:
    base, principal = await bundle_fixture()
    cliente = _ClienteComValidade(base.client, **validades)
    return (
        EngineReadBundle(client=cliente, anchor=base.anchor, cursor=base.cursor),
        principal,
        base.client.partition,
    )


async def _validade_da_tarefa() -> Any:
    base, _ = await bundle_fixture()
    tarefa = await EngineHumanTaskTransport(base).read_task("task-1")
    return tarefa.valid_until


@pytest.mark.parametrize(("antes", "aceita"), [(timedelta(0), False), (timedelta(seconds=1), True)])
async def test_material_vencido_catalogo_recusa_a_leitura_no_instante_exato(
    antes: timedelta, aceita: bool
) -> None:
    """`engine_reads.py:117` — `catalogo.valid_until <= agora` recusa; um segundo antes, le."""
    vence = await _validade_da_tarefa() - _FOLGA  # so' o catalogo vence; a tarefa segue valida
    bundle, _, particao = await _bundle(catalogo_ate=vence)
    particao.clock.now = vence - antes
    transporte = EngineHumanTaskTransport(bundle)
    if aceita:
        tarefa = await transporte.read_task("task-1")
        assert tarefa.snapshot.task_id == "task-1" and not bundle._closed
        return
    with pytest.raises(ReadRefusalError) as recusa:
        await transporte.read_task("task-1")
    assert recusa.value.code == _MOTIVO
    assert bundle._closed and not bundle._tasks  # envenenado: nada da leitura recusada sobrevive


@pytest.mark.parametrize(("antes", "aceita"), [(timedelta(0), False), (timedelta(seconds=1), True)])
async def test_material_vencido_continuidade_recusa_a_autoridade_no_instante_exato(
    antes: timedelta, aceita: bool
) -> None:
    """`engine_reads.py:148` — continuidade nativa com `valid_until <= agora` recusa; um segundo antes, le."""
    vence = await _validade_da_tarefa() - _FOLGA  # so' a continuidade da autoridade vence
    bundle, principal, particao = await _bundle(continuidade_autoridade_ate=vence)
    tarefa = await EngineHumanTaskTransport(bundle).read_task("task-1")  # bem antes da fronteira
    particao.clock.now = vence - antes
    if aceita:
        autoridade = await EngineTaskAuthority(bundle).current_authority(principal, tarefa)
        assert autoridade.task_id == "task-1" and not bundle._closed
        return
    with pytest.raises(ReadRefusalError) as recusa:
        await EngineTaskAuthority(bundle).current_authority(principal, tarefa)
    assert recusa.value.code == _MOTIVO
    assert bundle._closed


@pytest.mark.parametrize(("antes", "aceita"), [(timedelta(0), False), (timedelta(seconds=1), True)])
async def test_material_vencido_recusa_a_conclusao_no_instante_exato(antes: timedelta, aceita: bool) -> None:
    """A conclusao exige o material de leitura vigente (`agora < valid_until` da admissao): recusa no
    instante exato do vencimento, com motivo fechado e SEM chamar o motor; um segundo antes, conclui.

    No fixture a admissao e a assinatura vencem no MESMO instante, entao esta prova cobre o guarda
    combinado (`ReadAdmissionLease` + `ReadSigningLease`), nao cada uma isolada."""
    bundle, _ = await bundle_fixture()
    capacidade = Capability(scope=bundle.scope)
    transporte = EngineHumanTaskTransport(bundle, completion=capacidade)
    tarefa = await transporte.read_task("task-1")
    particao = bundle.client.partition
    vence = particao.admission.record.valid_until  # a validade do material, lida do proprio material
    particao.clock.now = vence - antes
    if aceita:
        ack = await transporte.complete_task(
            "task-1",
            resultado="devolvido_agente",
            notes=NOTES,
            expected_task_revision=tarefa.snapshot.task_revision,
        )
        assert ack.task_id == "task-1" and len(capacidade.calls) == 1
        return
    with pytest.raises(ReadRefusalError) as recusa:
        await transporte.complete_task(
            "task-1",
            resultado="devolvido_agente",
            notes=NOTES,
            expected_task_revision=tarefa.snapshot.task_revision,
        )
    assert recusa.value.code == _MOTIVO
    assert capacidade.calls == []  # o motor nunca foi chamado
    assert bundle._closed
