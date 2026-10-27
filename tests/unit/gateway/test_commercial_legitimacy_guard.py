"""OP21 PR 3/5 — o guard shadow do caminho coletivo (desenho sha256 `5639f41d…`).

Provas 3 e 4 do desenho §7 DECISÃO 6 (shard `gateway-platform`), sobre os doubles de
unidade do caminho comercial (`tests.unit.test_commercial_gateway` — nenhum engine, live
admission ou efeito financeiro):

- **Prova 3** — a shadow registra sem quebrar o caminho: o MESMO comando ilegítimo (PJ
  contratante sem recibo vigente) com o guard montado responde BYTE-IDÊNTICO ao de flag
  OFF, grava a linha com `would_block`/`art39_signaled` (nova revisão da MESMA instância,
  idempotente) e emite `legitimacy.art39_signaled` com o PAR estrutural verbatim do
  art. 39 — e o gêmeo do empresário individual com a âncora própria (art. 10 §§ 2º-3º);
- **Prova 4** — a shadow NUNCA bloqueia, estruturalmente: a composition default não
  referencia a postura enforcing (cerca grep com prova de mutação), a porta da flag aceita
  EXCLUSIVAMENTE a classe `LegitimacyScreenShadow` exata (nenhuma subclasse passa), flag
  OFF devolve o MESMO objeto, e flag ON sem fábrica é recusa de boot (dois portões);
- o bound set é FECHADO nas 7 operações coletivas e tudo fora passa intocado;
- o vocabulário de recusa é fixado uma vez e o enum de `reason_code` do codec C6-4 é
  pinado com a verdade (`source_unavailable` dentro; os tokens D4 novos, fora);
- a cerca de citação do parecer §1/§4: zero forma imprecisa ("RN 659/2025, art. 23-A",
  "RN 557/659") no módulo do guard; zero vocabulário da decisão A8 (a cerca de
  `test_commercial_admission_a8` pina a árvore inteira — este módulo não entra nela);
- PHI (camada 2): o guard nunca abre o corpo do comando e nenhum modelo fechado dele tem
  campo onde dado clínico caiba.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from maezo.gateway.commercial import CommercialGateway
from maezo.gateway.commercial_legitimacy_guard import (
    ART39_SIGNAL_ANCHOR,
    ART39_SIGNAL_CONSEQUENCE,
    ART39_TWIN_ANCHOR,
    COLLECTIVE_BOUND_SET,
    GUARD_ACTOR,
    LEGITIMACY_UNPROVEN,
    PHI_IN_COMMERCIAL_INPUT,
    SOURCE_UNAVAILABLE,
    LegitimacyScreenEvent,
    LegitimacyScreenGateway,
    LegitimacyScreenShadow,
    collective_legitimacy_shadow,
)
from maezo.gateway.commercial_legitimacy_screen import (
    ART39_ANCHOR,
    LegitimacyCriterion,
    LegitimacyScreenRecord,
    LegitimacyScreenStoreError,
)
from maezo.ports.commercial import CommercialOperation, CommercialRefusalError, SalesTransport
from tests.unit.test_commercial_gateway import NOW, UnitAdmission, UnitCodec, UnitSource, scope

_SHA = "b" * 64
_GUARD_MODULE = (
    Path(__file__).resolve().parents[3] / "src" / "maezo" / "gateway" / "commercial_legitimacy_guard.py"
)
#: As DUAS formas de citação imprecisas (parecer §1/§4) — nenhuma existe no módulo do guard.
PROHIBIDAS_CITACAO = ("RN 659/2025, art. 23-A", "RN 557/659")
_COMPOSITION_FILES = (
    Path(__file__).resolve().parents[3] / "src" / "maezo" / "portal" / "api" / "app.py",
    Path(__file__).resolve().parents[3] / "src" / "maezo" / "portal" / "api" / "production.py",
)
#: A postura enforcing (sucessora, PR 4) nunca é NOMEADA pela composição default — cerca
#: da prova 4 (desenho §4: enforcing nunca é alcançável por ambiente).
ENFORCING_NAME = "LegitimacyScreenEnforcing"


# ----------------------------------------------------------------------------------
# Doubles do port do substrato (#1022) e do coletor de eventos.
# ----------------------------------------------------------------------------------


class _Store:
    """Port double com a MESMA semântica do store: save idempotente na chave de instância
    e recusa `contract_mismatch` para mesma oportunidade+revisão sob outro `screen_ref`."""

    def __init__(self, *receipts: LegitimacyScreenRecord) -> None:
        self.rows: dict[tuple[str, str, int], LegitimacyScreenRecord] = {}
        self.opportunities: dict[tuple[str, str, int], str] = {}
        self.save_calls = 0
        self.read_calls = 0
        self.fail_reads: Exception | None = None
        self.delay = 0.0
        for receipt in receipts:
            self._insert(receipt)

    def _insert(self, record: LegitimacyScreenRecord) -> LegitimacyScreenRecord:
        key = (record.tenant, record.screen_ref, record.screen_revision)
        binding = (record.tenant, record.opportunity_ref, record.screen_revision)
        owner = self.opportunities.get(binding)
        if owner is not None and owner != record.screen_ref:
            raise LegitimacyScreenStoreError("contract_mismatch")
        existing = self.rows.get(key)
        if existing is not None:
            return existing
        self.rows[key] = record
        self.opportunities[binding] = record.screen_ref
        return record

    async def save(self, record: LegitimacyScreenRecord) -> LegitimacyScreenRecord:
        self.save_calls += 1
        return self._insert(record)

    async def read(self, tenant: str, screen_ref: str, screen_revision: int) -> LegitimacyScreenRecord | None:
        self.read_calls += 1
        return self.rows.get((tenant, screen_ref, screen_revision))

    async def latest_for_opportunity(
        self, tenant: str, opportunity_ref: str
    ) -> LegitimacyScreenRecord | None:
        return self._latest(tenant, lambda record: record.opportunity_ref == opportunity_ref)

    async def latest_for_legal_entity(
        self, tenant: str, legal_entity_ref: str
    ) -> LegitimacyScreenRecord | None:
        if self.fail_reads is not None:
            raise self.fail_reads
        if self.delay:
            await asyncio.sleep(self.delay)
        return self._latest(tenant, lambda record: record.legal_entity_ref == legal_entity_ref)

    def _latest(self, tenant: str, predicate: Any) -> LegitimacyScreenRecord | None:
        self.read_calls += 1
        vivos = [
            record
            for (inquilino, _, _), record in self.rows.items()
            if inquilino == tenant and predicate(record)
        ]
        if not vivos:
            return None
        return max(vivos, key=lambda record: (record.screen_revision, record.screen_ref))

    def head(self, tenant: str, legal_entity_ref: str) -> LegitimacyScreenRecord | None:
        return self._latest(tenant, lambda record: record.legal_entity_ref == legal_entity_ref)


class _Sink:
    def __init__(self) -> None:
        self.events: list[LegitimacyScreenEvent] = []
        self.fail = False

    def emit(self, event: LegitimacyScreenEvent) -> None:
        if self.fail:
            raise RuntimeError("sink_down")
        self.events.append(event)


# ----------------------------------------------------------------------------------
# Moldes de recibo e de composição.
# ----------------------------------------------------------------------------------


def _criterion(**overrides: object) -> LegitimacyCriterion:
    fields: dict[str, object] = {
        "criterion_id": "vinculo_beneficiario_contratante",
        "result": "not_proven",
        "norm_anchor": "RN 557/2022, art. 5º",
    }
    fields.update(overrides)
    return LegitimacyCriterion(**fields)  # type: ignore[arg-type]


def _receipt(**overrides: object) -> LegitimacyScreenRecord:
    fields: dict[str, object] = {
        "tenant": "tenant-a",
        "screen_ref": "screen-" + "a" * 16,
        "screen_revision": 1,
        "legal_entity_ref": "entity-a",
        "opportunity_ref": "opportunity-1",
        "contract_kind": "empresarial",
        "contracting_form": "direta",
        "criteria": (_criterion(),),
        "status": "invalid",
        "would_block": False,
        "art39_signaled": False,
        "created_by": "staff-op21",
        "created_at": NOW,
        "updated_at": NOW,
    }
    fields.update(overrides)
    return LegitimacyScreenRecord(**fields)  # type: ignore[arg-type]


def _gateway(
    postura: type[LegitimacyScreenGateway],
    store: _Store,
    *,
    events: _Sink | None = None,
    timeout_seconds: float = 5.0,
) -> tuple[Any, UnitAdmission, UnitSource]:
    value = scope()
    admission = UnitAdmission(value)
    source = UnitSource(value)
    kwargs: dict[str, Any] = {
        "codec": UnitCodec(),
        "admission": admission,
        "source": source,
        "clock": lambda: NOW,
        "timeout_seconds": timeout_seconds,
    }
    if postura is LegitimacyScreenShadow:
        gateway = LegitimacyScreenShadow(store=store, tenant="tenant-a", events=events, **kwargs)
    else:
        gateway = postura(store=store, tenant="tenant-a", events=events, **kwargs)
    return gateway, admission, source


def _flag_off() -> tuple[Any, UnitAdmission, UnitSource]:
    """O gateway de flag OFF: a classe base, sem guard — o stub byte a byte de hoje."""
    value = scope()
    admission = UnitAdmission(value)
    source = UnitSource(value)
    gateway = CommercialGateway(codec=UnitCodec(), admission=admission, source=source, clock=lambda: NOW)
    return gateway, admission, source


async def _execute(gateway: Any, command: bytes = b"acceptance.record") -> Any:
    value = await gateway.admit(
        SalesTransport(connection_ref="server-connection", method="POST", path="fixed")
    )
    return await gateway.execute(UnitCodec().parse_command(command), value)


# ----------------------------------------------------------------------------------
# Prova 3 — shadow registra sem quebrar o caminho (resposta byte-idêntica).
# ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prova3_comando_ilegítimo_responde_byte_idêntico_e_registra_o_bloqueio() -> None:
    store = _Store(_receipt(status="invalid"))
    sink = _Sink()
    shadow, _, shadow_source = _gateway(LegitimacyScreenShadow, store, events=sink)
    off, _, off_source = _flag_off()

    on_bytes = (await _execute(shadow)).wire_bytes
    off_bytes = (await _execute(off)).wire_bytes

    # A resposta comercial é BYTE-IDÊNTICA à de flag OFF — comando "ilegítimo" é a PJ, não
    # a resposta: a shadow nunca altera o resultado (desenho §4).
    assert on_bytes == off_bytes
    assert json.loads(on_bytes)["status"] == "succeeded"
    assert shadow_source.calls.count("execute") == off_source.calls.count("execute") == 1

    # A perna REGISTRAR: nova revisão da MESMA instância, com a observação postural.
    head = store.head("tenant-a", "entity-a")
    assert head is not None
    assert head.screen_ref == "screen-" + "a" * 16
    assert head.screen_revision == 2
    assert head.would_block is True
    assert head.art39_signaled is True
    assert head.art39_anchor == ART39_ANCHOR == "RN 557/2022, art. 39"
    assert head.created_by == GUARD_ACTOR
    assert head.criteria[0].criterion_id == "vinculo_beneficiario_contratante"
    assert store.save_calls == 1

    # A perna SINALIZAR: resultado + par estrutural verbatim do art. 39.
    assert [event.kind for event in sink.events] == [
        "legitimacy.blocked",
        "legitimacy.art39_signaled",
    ]
    art39 = sink.events[1]
    assert art39.art39 is not None
    assert art39.art39.anchor == ART39_SIGNAL_ANCHOR == "RN 557/2022, art. 39"
    assert art39.art39.consequence == ART39_SIGNAL_CONSEQUENCE
    assert art39.would_block is True
    assert art39.contract_kind == "empresarial"


@pytest.mark.asyncio
async def test_prova3_comando_fora_do_bound_set_passa_intocado() -> None:
    store = _Store(_receipt(status="invalid"))
    sink = _Sink()
    shadow, _, _ = _gateway(LegitimacyScreenShadow, store, events=sink)
    off, _, _ = _flag_off()

    on_bytes = (await _execute(shadow, b"payment.reconcile")).wire_bytes
    off_bytes = (await _execute(off, b"payment.reconcile")).wire_bytes

    assert on_bytes == off_bytes
    assert store.read_calls == 0
    assert store.save_calls == 0
    assert sink.events == []


@pytest.mark.asyncio
async def test_prova3_recibo_vigente_aprova_sem_nova_linha() -> None:
    store = _Store(_receipt(status="valid"))
    sink = _Sink()
    shadow, _, _ = _gateway(LegitimacyScreenShadow, store, events=sink)
    off, _, _ = _flag_off()

    on_bytes = (await _execute(shadow)).wire_bytes
    assert on_bytes == (await _execute(off)).wire_bytes
    assert store.save_calls == 0  # a cabeça JÁ é a verificação de registro da PJ
    assert [event.kind for event in sink.events] == ["legitimacy.approved"]
    assert sink.events[0].would_block is False
    assert sink.events[0].art39 is None


@pytest.mark.asyncio
async def test_prova3_reconfirmação_vencida_expira_e_registra() -> None:
    store = _Store(_receipt(status="valid", reconfirmation_due_at=NOW - timedelta(minutes=1)))
    sink = _Sink()
    shadow, _, _ = _gateway(LegitimacyScreenShadow, store, events=sink)

    on_bytes = (await _execute(shadow)).wire_bytes
    off, _, _ = _flag_off()
    assert on_bytes == (await _execute(off)).wire_bytes
    head = store.head("tenant-a", "entity-a")
    assert head is not None and head.screen_revision == 2 and head.would_block is True
    assert [event.kind for event in sink.events] == [
        "legitimacy.expired",
        "legitimacy.art39_signaled",
    ]


@pytest.mark.asyncio
async def test_prova3_pendente_não_é_aprovado_por_omissão() -> None:
    store = _Store(_receipt(status="pending"))
    sink = _Sink()
    shadow, _, _ = _gateway(LegitimacyScreenShadow, store, events=sink)

    await _execute(shadow)
    head = store.head("tenant-a", "entity-a")
    assert head is not None and head.would_block is True
    assert [event.kind for event in sink.events][0] == "legitimacy.blocked"


@pytest.mark.asyncio
async def test_prova3_sem_tela_nenhuma_nada_a_persistir_sinal_vai_pelo_evento() -> None:
    store = _Store()
    sink = _Sink()
    shadow, _, _ = _gateway(LegitimacyScreenShadow, store, events=sink)
    off, _, _ = _flag_off()

    assert (await _execute(shadow)).wire_bytes == (await _execute(off)).wire_bytes
    # O guard NUNCA fabrica instância (o modelo é opportunity-bound; a tela staff registra
    # a verificação): sem recibo não há linha — o sinal vai pelo evento.
    assert store.save_calls == 0
    assert [event.kind for event in sink.events] == [
        "legitimacy.blocked",
        "legitimacy.art39_signaled",
    ]
    assert sink.events[1].art39 is not None
    # Regime desconhecido: a âncora é a canônica do art. 39 (a que o §2º do art. 10 invoca).
    assert sink.events[1].art39.anchor == ART39_SIGNAL_ANCHOR


@pytest.mark.asyncio
async def test_prova3_gêmeo_empresário_individual_sinaliza_com_âncora_própria() -> None:
    store = _Store(_receipt(contract_kind="empresario_individual", status="invalid"))
    sink = _Sink()
    shadow, _, _ = _gateway(LegitimacyScreenShadow, store, events=sink)

    await _execute(shadow)
    art39 = sink.events[1]
    assert art39.art39 is not None
    assert art39.art39.anchor == ART39_TWIN_ANCHOR == "RN 557/2022, art. 10, §§ 2º e 3º"
    assert art39.art39.consequence == ART39_SIGNAL_CONSEQUENCE
    # A LINHA persistida carrega sempre a âncora canônica do art. 39 (Literal do substrato).
    head = store.head("tenant-a", "entity-a")
    assert head is not None and head.art39_anchor == "RN 557/2022, art. 39"


@pytest.mark.asyncio
async def test_prova3_idempotência_cabeça_que_já_registra_não_escreve_de_novo() -> None:
    store = _Store(
        _receipt(status="invalid", would_block=True, art39_signaled=True, art39_anchor="RN 557/2022, art. 39")
    )
    sink = _Sink()
    shadow, _, _ = _gateway(LegitimacyScreenShadow, store, events=sink)

    await _execute(shadow)
    await _execute(shadow)
    assert store.save_calls == 0
    head = store.head("tenant-a", "entity-a")
    assert head is not None and head.screen_revision == 1
    assert len(sink.events) == 4  # os eventos seguem saindo; a linha, não


@pytest.mark.asyncio
async def test_prova3_store_indisponível_não_altera_a_resposta() -> None:
    store = _Store()
    store.fail_reads = LegitimacyScreenStoreError("source_unavailable")
    shadow, _, _ = _gateway(LegitimacyScreenShadow, store)
    off, _, _ = _flag_off()

    assert (await _execute(shadow)).wire_bytes == (await _execute(off)).wire_bytes


@pytest.mark.asyncio
async def test_prova3_sink_falhando_não_altera_a_resposta_e_a_linha_sai() -> None:
    store = _Store(_receipt(status="invalid"))
    sink = _Sink()
    sink.fail = True
    shadow, _, _ = _gateway(LegitimacyScreenShadow, store, events=sink)
    off, _, _ = _flag_off()

    assert (await _execute(shadow)).wire_bytes == (await _execute(off)).wire_bytes
    assert store.save_calls == 1
    assert sink.events == []


@pytest.mark.asyncio
async def test_prova3_store_lento_não_pende_o_caminho() -> None:
    store = _Store(_receipt(status="invalid"))
    store.delay = 30.0
    shadow, _, _ = _gateway(LegitimacyScreenShadow, store, timeout_seconds=0.2)

    result = await asyncio.wait_for(_execute(shadow), timeout=5.0)
    assert result.status == "succeeded"


@pytest.mark.asyncio
async def test_prova3_recusa_do_caminho_segue_byte_idêntica_e_observada() -> None:
    store = _Store(_receipt(status="invalid"))
    sink = _Sink()
    shadow, shadow_admission, _ = _gateway(LegitimacyScreenShadow, store, events=sink)
    off, off_admission, _ = _flag_off()
    shadow_admission.revoke_at = "before_source"
    off_admission.revoke_at = "before_source"

    on_result = await _execute(shadow)
    off_result = await _execute(off)

    # `operation_ref` de recusa pré-fonte é volátil ("commercial-"+uuid): normaliza SÓ ele.
    def _normalized(result: Any) -> Any:
        value = json.loads(result.wire_bytes)
        value["operation_ref"] = "<volátil>"
        return value

    assert on_result.status == off_result.status == "denied"
    assert _normalized(on_result) == _normalized(off_result)
    # A avaliação correu ANTES de super().execute: a observação foi registrada mesmo assim.
    head = store.head("tenant-a", "entity-a")
    assert head is not None and head.would_block is True
    assert [event.kind for event in sink.events] == [
        "legitimacy.blocked",
        "legitimacy.art39_signaled",
    ]


# ----------------------------------------------------------------------------------
# Prova 4 — a shadow NUNCA bloqueia, estruturalmente.
# ----------------------------------------------------------------------------------


def test_prova4_a_composição_default_não_nomeia_a_postura_enforcing() -> None:
    for path in _COMPOSITION_FILES:
        assert ENFORCING_NAME not in path.read_text(encoding="utf-8"), path


def test_prova4_cerca_de_enforcing_mutada_fica_vermelha(tmp_path: Path) -> None:
    alvo = tmp_path / "app.py"
    alvo.write_text((_COMPOSITION_FILES[0]).read_text(encoding="utf-8"), encoding="utf-8")
    assert [str(path) for path, _ in _fence(tmp_path)] == []

    mutado = alvo.read_text(encoding="utf-8").replace("collective_legitimacy_shadow(", f"{ENFORCING_NAME}(")
    assert mutado != alvo.read_text(encoding="utf-8"), "mutação não aplicada — fixture inválida"
    alvo.write_text(mutado, encoding="utf-8")
    achados = _fence(tmp_path)
    assert len(achados) == 1 and ENFORCING_NAME in achados[0][1]


def _fence(root: Path) -> list[tuple[Path, str]]:
    achados: list[tuple[Path, str]] = []
    for name in ("app.py", "production.py"):
        path = root / name
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if ENFORCING_NAME in line:
                achados.append((path, f"{name}:{number}: {ENFORCING_NAME} na composição"))
    return achados


def test_prova4_a_porta_da_flag_aceita_somente_a_shadow_exata() -> None:
    gateway, _, _ = _flag_off()
    assert (
        collective_legitimacy_shadow(
            gateway,
            enabled=True,
            guard_factory=lambda inner: LegitimacyScreenShadow(
                store=_Store(),
                tenant="tenant-a",
                codec=inner.codec,
                admission=inner.admission,
                source=inner.source,
                clock=lambda: NOW,
                timeout_seconds=inner.timeout_seconds,
            ),
        )
        is not None
    )

    class _MutanteBloqueante(LegitimacyScreenGateway):
        async def _posture(self, command: Any, scope: Any) -> None:  # pragma: no cover - porta
            raise CommercialRefusalError("denied", LEGITIMACY_UNPROVEN)

    with pytest.raises(ValueError, match="legitimacy_screen_posture_invalid"):
        collective_legitimacy_shadow(
            gateway,
            enabled=True,
            guard_factory=lambda inner: _MutanteBloqueante(  # type: ignore[return-value]
                store=_Store(),
                tenant="tenant-a",
                codec=inner.codec,
                admission=inner.admission,
                source=inner.source,
                clock=lambda: NOW,
            ),
        )
    with pytest.raises(ValueError, match="legitimacy_screen_posture_invalid"):
        collective_legitimacy_shadow(gateway, enabled=True, guard_factory=lambda inner: inner)


def test_prova4_flag_off_devolve_o_mesmo_objeto_e_none_permanece_none() -> None:
    gateway, _, _ = _flag_off()
    assert collective_legitimacy_shadow(gateway, enabled=False) is gateway
    assert collective_legitimacy_shadow(None, enabled=True, guard_factory=None) is None


def test_prova4_flag_on_sem_fábrica_é_recusa_de_boot() -> None:
    gateway, _, _ = _flag_off()
    with pytest.raises(ValueError, match="legitimacy_screen_guard_unbound"):
        collective_legitimacy_shadow(gateway, enabled=True, guard_factory=None)


@pytest.mark.asyncio
async def test_prova4_porta_de_recusa_de_postura_renderiza_pelo_codec_e_defeito_engolido() -> None:
    class _RecusaDeclarada(LegitimacyScreenGateway):
        async def _posture(self, command: Any, scope: Any) -> None:
            raise CommercialRefusalError("denied", LEGITIMACY_UNPROVEN)

    class _DefeitoMudo(LegitimacyScreenGateway):
        async def _posture(self, command: Any, scope: Any) -> None:
            raise RuntimeError("bug_da_postura")

    recusante, _, _ = _gateway(_RecusaDeclarada, _Store())  # type: ignore[arg-type]
    result = await _execute(recusante)
    assert result.status == "denied"
    assert json.loads(result.wire_bytes)["reason_code"] == LEGITIMACY_UNPROVEN

    bugado, _, _ = _gateway(_DefeitoMudo, _Store())  # type: ignore[arg-type]
    off, _, _ = _flag_off()
    assert (await _execute(bugado)).wire_bytes == (await _execute(off)).wire_bytes


@pytest.mark.asyncio
async def test_prova4_a_base_sem_postura_é_recusa_loud_nunca_fail_open() -> None:
    base, _, _ = _gateway(LegitimacyScreenGateway, _Store())  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await _execute(base)


@pytest.mark.asyncio
async def test_prova4_o_wrap_passa_no_isinstance_do_router_congelado() -> None:
    shadow, _, _ = _gateway(LegitimacyScreenShadow, _Store())
    assert isinstance(shadow, CommercialGateway)  # portal/api/commercial.py:51 — sem editá-lo
    assert type(shadow) is LegitimacyScreenShadow


# ----------------------------------------------------------------------------------
# Bound set fechado, vocabulário, citação e PHI (camada 2).
# ----------------------------------------------------------------------------------


def test_bound_set_fechado_é_exatamente_o_arco_coletivo() -> None:
    assert (
        frozenset(
            {
                "party.prepare",
                "party.submit",
                "party.link",
                "offer.quote",
                "offer.terms",
                "acceptance.record",
                "contract.issue",
            }
        )
        == COLLECTIVE_BOUND_SET
    )
    assert set(get_args(CommercialOperation)) >= COLLECTIVE_BOUND_SET
    for fora in (
        "work.list",
        "work.claim",
        "catalog.get",
        "catalog.publish",
        "payment.create",
        "payment.reconcile",
        "mandate.create",
        "mandate.revoke",
        "support.handoff",
        "operator.get",
        "journey.get",
        "receivable.ensure",
        "operation.get",
        "contract.get",
    ):
        assert fora not in COLLECTIVE_BOUND_SET, fora


def test_vocabulário_de_recusa_fixado_uma_vez_e_o_enum_do_codec_pina_a_verdade() -> None:
    assert LEGITIMACY_UNPROVEN == "legitimacy_unproven"
    assert SOURCE_UNAVAILABLE == "source_unavailable"
    assert PHI_IN_COMMERCIAL_INPUT == "phi_in_commercial_input"
    contrato = json.loads(
        (
            Path(__file__).resolve().parents[2] / "candidate_artifacts" / "commercial" / "contract-c6-4.json"
        ).read_text(encoding="utf-8")
    )
    enum = next(
        braço["properties"]["reason_code"]["enum"]
        for braço in contrato["$defs"]["OperationalResult"]["oneOf"]
        if "enum" in braço["properties"]["reason_code"]
    )
    assert SOURCE_UNAVAILABLE in enum  # este token já anda no wire C6-4
    assert LEGITIMACY_UNPROVEN not in enum  # a ida a wire é negociação de schema sucessor
    assert PHI_IN_COMMERCIAL_INPUT not in enum


def test_cerca_de_citação_e_de_canal_no_módulo_do_guard() -> None:
    texto = _GUARD_MODULE.read_text(encoding="utf-8")
    for proibida in PROHIBIDAS_CITACAO:
        assert proibida not in texto, proibida
    assert ART39_SIGNAL_ANCHOR == ART39_ANCHOR == "RN 557/2022, art. 39"
    assert ART39_SIGNAL_CONSEQUENCE == (
        "vinculo_direto_e_individual_com_a_operadora_equiparado_ao_plano_individual_ou_familiar"
    )
    # A cerca de vocabulário da decisão A8 (`test_commercial_admission_a8`) pina a árvore
    # `src/` inteira — este módulo não entra nela (o reparo F-1 do #1029 é o precedente:
    # citar o molde D-36 pelo ATO/#987, nunca pelo nome de arquivo).
    assert "commission" not in texto.lower()
    assert "comissão" not in texto.lower()


def test_phi_camada_2_o_guard_nunca_abre_o_corpo_do_comando() -> None:
    texto = _GUARD_MODULE.read_text(encoding="utf-8")
    assert "wire_bytes" not in texto  # o parse é do codec, dentro de super().execute
    assert "diagnóstico" not in texto.lower()


def test_evento_fechado_par_do_art39_exclusivo_da_espécie_de_sinalização() -> None:
    event = LegitimacyScreenEvent(
        kind="legitimacy.blocked", tenant="tenant-a", contract_kind=None, would_block=True
    )
    assert event.art39 is None
    with pytest.raises(ValidationError):
        LegitimacyScreenEvent(
            kind="legitimacy.blocked",
            tenant="tenant-a",
            contract_kind=None,
            would_block=True,
            art39={"anchor": ART39_SIGNAL_ANCHOR, "consequence": ART39_SIGNAL_CONSEQUENCE},
        )
    with pytest.raises(ValidationError):
        LegitimacyScreenEvent(
            kind="legitimacy.art39_signaled", tenant="tenant-a", contract_kind=None, would_block=True
        )
    with pytest.raises(ValidationError):
        LegitimacyScreenEvent(
            kind="legitimacy.blocked",
            tenant="tenant-a",
            contract_kind=None,
            would_block=True,
            diagnostico="j45",  # type: ignore[call-arg] — nenhum slot de dado clínico existe
        )
