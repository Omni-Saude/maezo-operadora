"""E-SF-20r (EP-REG-08b + GTM-E-36) — a postura que recusa, dormente, e a corrida `revision_taken`.

Variante decidida: a postura enforcing existe e é exercitada direto; nenhuma composição a
monta e a porta da flag a recusa. O token `legitimacy_unproven` não está no enum congelado
do C6-4: fica interno até o contrato sucessor ser publicado e re-pinado.

- REG-08b: recibo `valid` vigente segue byte-idêntico; pendente, inválido, vencido ou
  ausente recusa `denied/legitimacy_unproven`; fonte muda ou lenta é
  `unavailable/source_unavailable`, nunca passagem; âncora do art. 39 só na forma literal.
- Corrida (`revision_taken`): o guard e o gatilho de aniversário relêem a cabeça UMA vez e
  decidem sobre a revisão vigente; colisão dupla na postura que recusa é indisponibilidade;
  em sombra o sinal sai uma vez e a linha não duplica; reenvio idêntico segue idempotente.
- GTM-E-36: o canal vendor só liga com a postura enforcing montada, e todo comando do C6-4
  que o canal alcança está no conjunto coletivo.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from maezo.gateway.capabilities.journeys.contracts import JourneyContractError
from maezo.gateway.capabilities.vendor_journey import (
    VENDOR_OPERATIONS,
    vendor_journey,
    vendor_journey_binding,
)
from maezo.gateway.commercial import CommercialGateway
from maezo.gateway.commercial_legitimacy_anniversary import (
    ANNIVERSARY_ACTOR,
    LegitimacyAnniversaryTrigger,
    anniversary_revision,
)
from maezo.gateway.commercial_legitimacy_guard import (
    COLLECTIVE_BOUND_SET,
    GUARD_ACTOR,
    LEGITIMACY_UNPROVEN,
    SOURCE_UNAVAILABLE,
    LegitimacyArt39Signal,
    LegitimacyScreenEnforcing,
    LegitimacyScreenShadow,
    collective_legitimacy_shadow,
)
from maezo.gateway.commercial_legitimacy_screen import (
    PROHIBITED_ANCHOR_FRAGMENTS,
    LegitimacyCriterion,
    LegitimacyScreenRecord,
    LegitimacyScreenStoreError,
)
from tests.unit.gateway.capabilities.journeys.helpers import (
    MEMBERS,
    Step,
    UnitIngress,
    UnitJournal,
    UnitSourceOracle,
)
from tests.unit.gateway.capabilities.vendor_journey.vendor_helpers import enforcing_posture, vendor_config
from tests.unit.gateway.test_commercial_legitimacy_enforcing import (
    _coletivo_scope,
    _ColetivoCodec,
    _enforcing,
)
from tests.unit.gateway.test_commercial_legitimacy_enforcing import _execute as _execute_coletivo
from tests.unit.gateway.test_commercial_legitimacy_guard import (
    _execute,
    _flag_off,
    _gateway,
    _receipt,
    _Sink,
    _Store,
)
from tests.unit.test_commercial_gateway import NOW, UnitAdmission, UnitSource

_CONTRACT = Path("tests/candidate_artifacts/commercial/contract-c6-4.json")


class _RacingStore(_Store):
    """Outro escritor grava a mesma revisão logo antes de cada `save` do guard/gatilho.

    `rivals` entra um por chamada de `save`, na ordem; `fail_reread` faz a leitura que vem
    depois da primeira colisão falhar (fonte muda na releitura)."""

    def __init__(
        self,
        *receipts: LegitimacyScreenRecord,
        rivals: tuple[LegitimacyScreenRecord, ...] = (),
        fail_reread: bool = False,
    ) -> None:
        super().__init__(*receipts)
        self.rivals = list(rivals)
        self.fail_reread = fail_reread

    async def save(self, record: LegitimacyScreenRecord) -> LegitimacyScreenRecord:
        if self.rivals:
            self._insert(self.rivals.pop(0))
        try:
            return await super().save(record)
        except LegitimacyScreenStoreError:
            if self.fail_reread:
                self.fail_reads = LegitimacyScreenStoreError("source_unavailable")
            raise


def _staff(revision: int, **overrides: object) -> LegitimacyScreenRecord:
    """Uma revisão da equipe na MESMA instância (revalidação concorrente)."""
    return _receipt(screen_revision=revision, created_by="staff-op21", **overrides)


def _guard_rows(store: _Store) -> list[LegitimacyScreenRecord]:
    return [row for row in store.rows.values() if row.created_by == GUARD_ACTOR]


def _plain_gateway() -> CommercialGateway:
    """O caminho de flag OFF com o mesmo codec e escopo coletivo da postura enforcing."""
    value = _coletivo_scope()
    return CommercialGateway(
        codec=_ColetivoCodec(),
        admission=UnitAdmission(value),
        source=UnitSource(value),
        clock=lambda: NOW,
    )


# ----------------------------------------------------------------------------------
# REG-08b — a postura que recusa, exercitada direto (nenhuma composição a monta).
# ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enforcing_valid_receipt_is_byte_identical_to_flag_off() -> None:
    enforcing, _, source = _enforcing(_Store(_receipt(status="valid")))
    result = await _execute_coletivo(enforcing)
    plain = await _execute_coletivo(_plain_gateway())
    assert result.status == "succeeded"
    assert result.wire_bytes == plain.wire_bytes
    assert source.calls.count("execute") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "head",
    [
        _receipt(status="pending"),
        _receipt(status="invalid"),
        _receipt(status="valid", reconfirmation_due_at=NOW - timedelta(days=1)),
        None,
    ],
    ids=["pending", "invalid", "vencido", "ausente"],
)
async def test_enforcing_refuses_pending_as_unproven(head: LegitimacyScreenRecord | None) -> None:
    store = _Store() if head is None else _Store(head)
    enforcing, _, source = _enforcing(store)
    result = await _execute_coletivo(enforcing)
    assert (result.status, result.reason_code) == ("denied", LEGITIMACY_UNPROVEN)
    assert source.calls == ["publication"]


@pytest.mark.asyncio
async def test_enforcing_store_timeout_is_unavailable_not_pass() -> None:
    store = _Store(_receipt(status="valid"))
    store.delay = 30.0
    enforcing, _, source = _enforcing(store, timeout_seconds=0.2)
    result = await asyncio.wait_for(_execute_coletivo(enforcing), timeout=5.0)
    assert (result.status, result.reason_code) == ("unavailable", SOURCE_UNAVAILABLE)
    assert "execute" not in source.calls


def test_flag_port_rejects_enforcing_class() -> None:
    gateway, _, _ = _flag_off()
    with pytest.raises(ValueError, match="legitimacy_screen_posture_invalid"):
        collective_legitimacy_shadow(gateway, enabled=True, guard_factory=lambda inner: enforcing_posture())
    # Desligada (o estado entregue), a porta devolve o gateway recebido, sem postura.
    assert collective_legitimacy_shadow(gateway, enabled=False) is gateway


def test_the_unproven_token_stays_internal_until_the_successor_contract() -> None:
    contract = json.loads(_CONTRACT.read_bytes())
    assert LEGITIMACY_UNPROVEN not in json.dumps(contract)


@pytest.mark.parametrize(
    "anchor",
    ["RN 557/2022 art. 39", "RN 557, art. 39", "art. 39", "RN 557/2022, artigo 39", "RN 557/659"],
)
def test_anchor_rejects_imprecise_forms(anchor: str) -> None:
    with pytest.raises(ValidationError):
        LegitimacyArt39Signal(
            anchor=anchor,  # type: ignore[arg-type]
            consequence="vinculo_direto_e_individual_com_a_operadora_equiparado_ao_plano_individual_ou_familiar",
        )
    with pytest.raises(ValidationError):
        _receipt(art39_signaled=True, art39_anchor=anchor)


@pytest.mark.parametrize("fragment", PROHIBITED_ANCHOR_FRAGMENTS)
def test_criterion_anchor_rejects_imprecise_citation(fragment: str) -> None:
    with pytest.raises(ValidationError):
        LegitimacyCriterion(
            criterion_id="documentos_art9",
            result="proven",
            norm_anchor=f"{fragment} texto",
            evidence=None,
        )


# ----------------------------------------------------------------------------------
# Corrida `revision_taken` na postura que recusa (CD-73 itens 1, 2 e 4).
# ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrida_na_postura_que_recusa_nao_vira_passagem() -> None:
    # A equipe gravou a revisão 2 (ainda pendente) entre a leitura e o save do guard.
    store = _RacingStore(_receipt(status="invalid"), rivals=(_staff(2, status="pending"),))
    sink = _Sink()
    enforcing, _, source = _enforcing(store, events=sink)
    result = await _execute_coletivo(enforcing)
    assert (result.status, result.reason_code) == ("denied", LEGITIMACY_UNPROVEN)
    assert source.calls == ["publication"]
    # Decidiu sobre a cabeça relida: a linha do guard é a revisão 3, uma só.
    assert [row.screen_revision for row in _guard_rows(store)] == [3]
    assert [event.kind for event in sink.events] == ["legitimacy.blocked", "legitimacy.art39_signaled"]


@pytest.mark.asyncio
async def test_corrida_na_postura_que_recusa_segue_com_recibo_vigente_relido() -> None:
    # A revisão concorrente é uma revalidação `valid` vigente: a postura decide sobre ela.
    store = _RacingStore(_receipt(status="invalid"), rivals=(_staff(2, status="valid"),))
    sink = _Sink()
    enforcing, _, source = _enforcing(store, events=sink)
    result = await _execute_coletivo(enforcing)
    assert result.status == "succeeded"
    assert source.calls.count("execute") == 1
    assert _guard_rows(store) == []
    assert [event.kind for event in sink.events] == ["legitimacy.approved"]


@pytest.mark.asyncio
async def test_corrida_dupla_na_postura_que_recusa_e_indisponivel() -> None:
    store = _RacingStore(
        _receipt(status="invalid"),
        rivals=(_staff(2, status="pending"), _staff(3, status="pending")),
    )
    enforcing, _, source = _enforcing(store)
    result = await _execute_coletivo(enforcing)
    assert (result.status, result.reason_code) == ("unavailable", SOURCE_UNAVAILABLE)
    assert source.calls == ["publication"]
    assert store.save_calls == 2  # uma releitura, nunca laço
    assert _guard_rows(store) == []


@pytest.mark.asyncio
async def test_releitura_que_falha_na_postura_que_recusa_e_indisponivel() -> None:
    store = _RacingStore(_receipt(status="invalid"), rivals=(_staff(2, status="valid"),), fail_reread=True)
    enforcing, _, source = _enforcing(store)
    result = await _execute_coletivo(enforcing)
    assert (result.status, result.reason_code) == ("unavailable", SOURCE_UNAVAILABLE)
    assert "execute" not in source.calls


# ----------------------------------------------------------------------------------
# Corrida em sombra: o sinal não se perde nem duplica (CD-73 item 3, CD-82 item 2).
# ----------------------------------------------------------------------------------


async def _shadow_run(store: _Store) -> tuple[_Sink, bytes, bytes]:
    sink = _Sink()
    shadow, _, _ = _gateway(LegitimacyScreenShadow, store, events=sink)
    off, _, _ = _flag_off()
    return sink, (await _execute(shadow)).wire_bytes, (await _execute(off)).wire_bytes


@pytest.mark.asyncio
async def test_corrida_em_sombra_nao_perde_nem_duplica_o_sinal() -> None:
    store = _RacingStore(_receipt(status="invalid"), rivals=(_staff(2, status="pending"),))
    sink, shadow_bytes, off_bytes = await _shadow_run(store)
    assert shadow_bytes == off_bytes
    assert [row.screen_revision for row in _guard_rows(store)] == [3]
    assert [event.kind for event in sink.events] == ["legitimacy.blocked", "legitimacy.art39_signaled"]


@pytest.mark.asyncio
async def test_corrida_em_sombra_com_o_par_ja_registrado_nao_grava_de_novo() -> None:
    # O outro escritor já registrou o par `would_block`+`art39_signaled`: nada a gravar, e o
    # sinal desta tentativa sai uma vez.
    rival = _receipt(
        screen_revision=2,
        status="pending",
        would_block=True,
        art39_signaled=True,
        art39_anchor="RN 557/2022, art. 39",
        created_by=GUARD_ACTOR,
    )
    store = _RacingStore(_receipt(status="invalid"), rivals=(rival,))
    sink, shadow_bytes, off_bytes = await _shadow_run(store)
    assert shadow_bytes == off_bytes
    assert store.save_calls == 1
    assert _guard_rows(store) == [rival]
    assert [event.kind for event in sink.events] == ["legitimacy.blocked", "legitimacy.art39_signaled"]


@pytest.mark.asyncio
async def test_corrida_dupla_em_sombra_ainda_sinaliza_uma_vez() -> None:
    store = _RacingStore(
        _receipt(status="invalid"),
        rivals=(_staff(2, status="pending"), _staff(3, status="pending")),
    )
    sink, shadow_bytes, off_bytes = await _shadow_run(store)
    assert shadow_bytes == off_bytes
    assert store.save_calls == 2
    assert _guard_rows(store) == []
    assert [event.kind for event in sink.events] == ["legitimacy.blocked", "legitimacy.art39_signaled"]


@pytest.mark.asyncio
async def test_reenvio_identico_continua_idempotente() -> None:
    # Mesmo conteúdo e autor, só timestamps diferentes: o store devolve a linha existente.
    store = _Store(_receipt(status="invalid"))
    sink = _Sink()
    shadow, _, _ = _gateway(LegitimacyScreenShadow, store, events=sink)
    await _execute(shadow)
    first = _guard_rows(store)
    rows_before = dict(store.rows)
    await _execute(shadow)
    assert len(first) == 1 and store.rows == rows_before
    assert store.save_calls == 1
    direct = first[0].model_copy(update={"created_at": NOW + timedelta(seconds=5)})
    assert await store.save(direct) == first[0]


# ----------------------------------------------------------------------------------
# Aniversário sob `revision_taken`: não perde nem duplica revisão (CD-73 itens 1 e 4).
# ----------------------------------------------------------------------------------

_DUE = NOW - timedelta(days=1)


def _overdue(**overrides: object) -> LegitimacyScreenRecord:
    return _receipt(status="valid", reconfirmation_due_at=_DUE, **overrides)


def _trigger(store: _Store) -> LegitimacyAnniversaryTrigger:
    return LegitimacyAnniversaryTrigger(store, tenant="tenant-a", clock=lambda: NOW)


def _anniversary_rows(store: _Store) -> list[LegitimacyScreenRecord]:
    return [row for row in store.rows.values() if row.created_by == ANNIVERSARY_ACTOR]


@pytest.mark.asyncio
async def test_aniversario_sob_revision_taken_nao_perde_nem_duplica_revisao() -> None:
    store = _RacingStore(_overdue(), rivals=(_staff(2, status="valid", reconfirmation_due_at=_DUE),))
    written = await _trigger(store).on_anniversary("entity-a")
    assert written is not None and written.screen_revision == 3
    assert _anniversary_rows(store) == [written]
    # Segundo disparo: a cabeça já registra o aniversário; nada novo.
    assert await _trigger(store).on_anniversary("entity-a") is None
    assert _anniversary_rows(store) == [written]


@pytest.mark.asyncio
async def test_aniversario_sob_revision_taken_com_bloqueio_ja_visivel_nao_escreve() -> None:
    rival = _overdue(
        screen_revision=2,
        would_block=True,
        art39_signaled=True,
        art39_anchor="RN 557/2022, art. 39",
        created_by=GUARD_ACTOR,
    )
    store = _RacingStore(_overdue(), rivals=(rival,))
    assert await _trigger(store).on_anniversary("entity-a") is None
    assert store.save_calls == 1
    assert _anniversary_rows(store) == []


@pytest.mark.asyncio
async def test_aniversario_com_colisao_dupla_propaga_sem_laco() -> None:
    store = _RacingStore(
        _overdue(),
        rivals=(
            _staff(2, status="valid", reconfirmation_due_at=_DUE),
            _staff(3, status="valid", reconfirmation_due_at=_DUE),
        ),
    )
    with pytest.raises(LegitimacyScreenStoreError) as exc:
        await _trigger(store).on_anniversary("entity-a")
    assert exc.value.code == "revision_taken"
    assert store.save_calls == 2
    assert _anniversary_rows(store) == []


def test_revisao_do_aniversario_copia_a_cabeca_relida() -> None:
    head = _staff(2, status="valid", reconfirmation_due_at=_DUE)
    row = anniversary_revision(head, now=NOW)
    assert row is not None and row.screen_revision == 3 and row.criteria == head.criteria


# ----------------------------------------------------------------------------------
# GTM-E-36 — o canal vendor só liga com a postura enforcing montada.
# ----------------------------------------------------------------------------------


def _vendor_ports() -> dict[str, Any]:
    b = vendor_journey_binding(vendor_config())
    journal, ingress = UnitJournal(b), UnitIngress(b)
    oracle = UnitSourceOracle(b, journal, [Step("C1_need", "access.resolve")])
    return {
        "journal": journal,
        "preparation": oracle,
        "transitions": oracle,
        "currentness": ingress,
        "memberships": MEMBERS,
    }


def test_vendor_channel_enable_requires_enforcing_legitimacy_posture() -> None:
    config = vendor_config()
    shadow, _, _ = _gateway(LegitimacyScreenShadow, _Store())
    plain, _, _ = _flag_off()
    for posture in (None, shadow, plain):
        with pytest.raises(JourneyContractError):
            vendor_journey(config, **_vendor_ports(), legitimacy_posture=posture)
    assert vendor_journey(config, **_vendor_ports(), legitimacy_posture=enforcing_posture()) is not None
    # Desligado (o estado entregue), nada é construído e nenhuma postura é exigida.
    assert vendor_journey(vendor_config(enabled=False), **_vendor_ports()) is None


def test_vendor_channel_enforcing_subclass_is_not_the_posture() -> None:
    class _Lookalike(LegitimacyScreenEnforcing):  # type: ignore[misc]
        pass

    real = enforcing_posture()
    lookalike = object.__new__(_Lookalike)
    lookalike.__dict__.update(real.__dict__)
    with pytest.raises(JourneyContractError):
        vendor_journey(vendor_config(), **_vendor_ports(), legitimacy_posture=lookalike)


def test_collective_bound_set_covers_every_command_the_vendor_channel_can_emit() -> None:
    contract = json.loads(_CONTRACT.read_bytes())
    commands = set(contract["x-commands"])
    assert commands, "the pinned C6-4 contract lists its commands"
    reachable = VENDOR_OPERATIONS & commands
    assert reachable <= COLLECTIVE_BOUND_SET
    # Hoje o canal não alcança comando comercial nenhum do C6-4 (J24: coletivo fora do canal).
    assert reachable == set()
