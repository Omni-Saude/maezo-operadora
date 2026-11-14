"""OP21 PR 4/5 — a postura ENFORCING do `legitimacy.screen` (desenho sha256 `5639f41d…`).

Provas 1, 2 e 8 do desenho §7 DECISÃO 6 (shard `gateway-platform`) e a extensão da prova 4
ao sucessor — sobre os doubles de unidade do caminho comercial (`tests.unit.test_commercial_gateway`;
doubles do substrato e do coletor importados do módulo do PR 3). A classe é exercitada DIRETO
(desenho §4 DECISÃO 3: "O teste negativo de bloqueio exercita a classe
`LegitimacyScreenEnforcing` diretamente em CI") — NENHUM bind de produção existe, nenhum é
criado aqui, e a porta da flag recusa a classe em boot.

- **Prova 1** — PJ ilegítima não atravessa: CADA comando do bound set com a PJ contratante
  sem recibo `valid` vigente é recusado ANTES de qualquer passo de fonte/efeito, com a
  recusa declarada da classe (`denied`/`legitimacy_unproven`) e bytes produzidos PELO CODEC
  quando o token é codificável;
- **Prova 2** — PENDENTE ≠ APROVADO por omissão (inércia honesta): recibo `pending`,
  `invalid`, reconfirmação vencida, PJ sem recibo e FONTE que não responde (store mudo,
  lento além do teto ou hostil) são TODOS bloqueio — o enforcing é fail-closed até sobre
  os próprios defeitos, e o par que anda no wire é FIXO (a fonte não o escolhe);
- **Prova 8** — firewall PHI camada 2 (admissão hostil): a triagem é INDEPENDENTE do corpo
  (a mesma PJ sem recibo produz a mesma recusa para os sete comandos), os modelos fechados
  da camada não têm slot onde dado clínico caiba, e o vocabulário `phi_in_commercial_input`
  está pinado FORA do enum congelado do codec C6-4 (a ida a wire é schema sucessor);
- **Prova 4 (extensão)** — a enforcing NUNCA é alcançável por ambiente: a porta da flag
  (`collective_legitimacy_shadow`) a recusa em boot, nenhuma árvore do portal a nomeia e o
  wrap passa no `isinstance` do router congelado como qualquer outro gateway comercial.

SONDA DO CODEC (conduta obrigatória do PR, resultado experimental pinado): o codec REAL do
C6-4 (`maezo.adapters.amh.commercial.CommercialWire` sobre os bytes pinados do contrato)
LEVANTA `ValueError("commercial_result_invalid")` para `legitimacy_unproven` e para
`phi_in_commercial_input` — o enum fechado de `reason_code` contém `source_unavailable` e
não contém os tokens D4 novos. Por isso o byte-exato do token NO WIRE, through the router,
exige vocabulário no codec = sucessor do freeze C6-4 (questão E-2 do desenho; decisão do
DONO) — e a prova do bloqueio aqui é a superfície da classe (exceção+status+reason pinados)
mais os bytes que o codec produz: os do token quando o teste usa codec que o aceita, os da
indisponibilidade honesta quando o codec é o congelado.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from maezo.adapters.amh.commercial import CommercialWire
from maezo.gateway.commercial import CommercialGateway
from maezo.gateway.commercial_legitimacy_guard import (
    ART39_SIGNAL_ANCHOR,
    ART39_TWIN_ANCHOR,
    COLLECTIVE_BOUND_SET,
    GUARD_ACTOR,
    LEGITIMACY_UNPROVEN,
    PHI_IN_COMMERCIAL_INPUT,
    SOURCE_UNAVAILABLE,
    LegitimacyScreenEnforcing,
    LegitimacyScreenEvent,
    LegitimacyScreenShadow,
    collective_legitimacy_shadow,
)
from maezo.gateway.commercial_legitimacy_screen import (
    ART39_ANCHOR,
    LegitimacyCriterion,
    LegitimacyScreenStoreError,
)
from maezo.ports.commercial import CommercialCommand, CommercialRefusalError, SalesTransport
from tests.unit.gateway.test_commercial_legitimacy_guard import (
    _flag_off,
    _receipt,
    _Sink,
    _Store,
)
from tests.unit.test_commercial_gateway import NOW, UnitAdmission, UnitCodec, UnitSource, scope

_GUARD_MODULE = (
    Path(__file__).resolve().parents[3] / "src" / "maezo" / "gateway" / "commercial_legitimacy_guard.py"
)


# ----------------------------------------------------------------------------------
# O codec do bound set: o molde compartilhado aceita 6 comandos de outro arco; este
# aceita também os outros 6 do set coletivo (comandos sem seleção — o arco PJ→oferta).
# ----------------------------------------------------------------------------------


class _ColetivoCodec(UnitCodec):
    def parse_command(self, body: bytes) -> CommercialCommand:
        name = body.decode()
        if name in COLLECTIVE_BOUND_SET and name != "acceptance.record":
            return CommercialCommand(
                command=name,  # type: ignore[arg-type]
                command_ref="command",
                expected_revision=1,
                wire_bytes=body,  # type: ignore[arg-type]
            )
        return super().parse_command(body)


def _coletivo_scope() -> Any:
    """O escopo admitido com o bound set INTEIRO nas operações permitidas."""
    return replace(scope(), allowed_operations=frozenset(COLLECTIVE_BOUND_SET))


def _wire_codec() -> CommercialWire:
    """O codec REAL do C6-4, montado sobre os bytes pinados do contrato congelado."""
    return CommercialWire(Path("tests/candidate_artifacts/commercial/contract-c6-4.json").read_bytes())


def _enforcing(
    store: _Store,
    *,
    events: _Sink | None = None,
    timeout_seconds: float = 5.0,
    codec: Any | None = None,
) -> tuple[LegitimacyScreenEnforcing, UnitAdmission, UnitSource]:
    value = _coletivo_scope()
    admission = UnitAdmission(value)
    source = UnitSource(value)
    gateway = LegitimacyScreenEnforcing(
        store=store,
        tenant="tenant-a",
        events=events,
        codec=codec if codec is not None else _ColetivoCodec(),
        admission=admission,
        source=source,
        clock=lambda: NOW,
        timeout_seconds=timeout_seconds,
    )
    return gateway, admission, source


async def _execute(gateway: Any, command: bytes = b"acceptance.record") -> Any:
    value = await gateway.admit(
        SalesTransport(connection_ref="server-connection", method="POST", path="fixed")
    )
    return await gateway.execute(_ColetivoCodec().parse_command(command), value)


# ----------------------------------------------------------------------------------
# Prova 1 — PJ ilegítima não atravessa (recusa ANTES de fonte/efeito, bytes do codec).
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize("comando", sorted(COLLECTIVE_BOUND_SET))
@pytest.mark.asyncio
async def test_prova1_pj_ilegítima_não_atravessa(comando: str) -> None:
    store = _Store(_receipt(status="invalid"))
    sink = _Sink()
    enforcing, _, source = _enforcing(store, events=sink)

    result = await _execute(enforcing, comando.encode())

    # A recusa sai ANTES de qualquer passo de fonte/efeito: do source só a publicação do
    # `admit` — nenhum `prepare`, nenhum efeito.
    assert source.calls == ["publication"], comando
    # A recusa declarada da classe, renderizada PELO CODEC para o par exato.
    assert result.status == "denied"
    assert result.reason_code == LEGITIMACY_UNPROVEN
    assert result.wire_bytes == UnitCodec().refusal("denied", LEGITIMACY_UNPROVEN).wire_bytes
    # A perna REGISTRAR registrou o bloqueio (nova revisão da MESMA instância) e a
    # SINALIZAR levou o resultado + o par estrutural do art. 39 — as três pernas juntas.
    head = store.head("tenant-a", "entity-a")
    assert head is not None
    assert head.screen_revision == 2 and head.would_block is True
    assert head.art39_signaled is True and head.art39_anchor == ART39_ANCHOR
    assert head.created_by == GUARD_ACTOR
    assert [event.kind for event in sink.events] == [
        "legitimacy.blocked",
        "legitimacy.art39_signaled",
    ]


@pytest.mark.asyncio
async def test_prova1_a_superfície_da_classe_é_a_recusa_declarada() -> None:
    """O par (status, reason_code) é propriedade da CLASSE — pino direto na superfície
    dela, com o codec REAL congelado montado no gateway (sem bind de produção algum)."""
    enforcing, _, _ = _enforcing(_Store(_receipt(status="invalid")), codec=_wire_codec())
    with pytest.raises(CommercialRefusalError) as exc:
        await enforcing._posture(UnitCodec().parse_command(b"acceptance.record"), _coletivo_scope())
    assert exc.value.status == "denied"
    assert exc.value.reason_code == LEGITIMACY_UNPROVEN == "legitimacy_unproven"


@pytest.mark.asyncio
async def test_prova1_no_codec_congelado_a_inércia_no_wire_é_honesta() -> None:
    """SONDA pinada: o codec REAL do C6-4 RECUSA renderizar o token novo (enum fechado,
    `commercial_result_invalid`) — e o quadro de postura da base renderiza, no lugar, a
    ÚNICA forma de bloqueio que o wire congelado aceita: a indisponibilidade honesta. O
    byte-exato de `legitimacy_unproven` NO WIRE, through the router, é vocabulário no
    codec = sucessor do freeze C6-4 (questão E-2 do desenho, decisão do DONO)."""
    codec = _wire_codec()
    with pytest.raises(ValueError, match="commercial_result_invalid"):
        codec.refusal("denied", LEGITIMACY_UNPROVEN)
    with pytest.raises(ValueError, match="commercial_result_invalid"):
        codec.refusal("denied", PHI_IN_COMMERCIAL_INPUT)
    fallback = codec.refusal("unavailable", SOURCE_UNAVAILABLE)

    enforcing, _, source = _enforcing(_Store(_receipt(status="invalid")), codec=codec)
    result = await _execute(enforcing)

    assert result.wire_bytes == fallback.wire_bytes
    assert result.status == "unavailable"
    assert json.loads(result.wire_bytes)["reason_code"] == SOURCE_UNAVAILABLE
    # Bloqueado ANTES da fonte mesmo com o codec congelado.
    assert source.calls == ["publication"]


# ----------------------------------------------------------------------------------
# Prova 2 — PENDENTE ≠ APROVADO por omissão (inércia honesta, fail-closed).
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "resultado_kind"),
    [("pending", "legitimacy.blocked"), ("invalid", "legitimacy.blocked")],
)
@pytest.mark.asyncio
async def test_prova2_recibo_sem_vigência_é_bloqueio(status: str, resultado_kind: str) -> None:
    store = _Store(_receipt(status=status))  # type: ignore[arg-type]
    sink = _Sink()
    enforcing, _, source = _enforcing(store, events=sink)

    result = await _execute(enforcing)

    assert (result.status, result.reason_code) == ("denied", LEGITIMACY_UNPROVEN)
    assert source.calls == ["publication"]
    assert [event.kind for event in sink.events] == [resultado_kind, "legitimacy.art39_signaled"]


@pytest.mark.asyncio
async def test_prova2_reconfirmação_vencida_é_bloqueio() -> None:
    store = _Store(_receipt(status="valid", reconfirmation_due_at=NOW - timedelta(minutes=1)))
    sink = _Sink()
    enforcing, _, _ = _enforcing(store, events=sink)

    result = await _execute(enforcing)

    assert (result.status, result.reason_code) == ("denied", LEGITIMACY_UNPROVEN)
    assert [event.kind for event in sink.events] == [
        "legitimacy.expired",
        "legitimacy.art39_signaled",
    ]
    head = store.head("tenant-a", "entity-a")
    assert head is not None and head.screen_revision == 2 and head.would_block is True


@pytest.mark.asyncio
async def test_prova2_pj_sem_recibo_nenhum_é_bloqueio_sem_linha_fabricada() -> None:
    store = _Store()
    sink = _Sink()
    enforcing, _, _ = _enforcing(store, events=sink)

    result = await _execute(enforcing)

    assert (result.status, result.reason_code) == ("denied", LEGITIMACY_UNPROVEN)
    # O guard NUNCA fabrica instância (o modelo é opportunity-bound): sem recibo não há
    # linha — o sinal vai pelo evento, com a âncora canônica do art. 39.
    assert store.save_calls == 0
    assert [event.kind for event in sink.events] == [
        "legitimacy.blocked",
        "legitimacy.art39_signaled",
    ]
    assert sink.events[1].art39 is not None
    assert sink.events[1].art39.anchor == ART39_SIGNAL_ANCHOR == "RN 557/2022, art. 39"


@pytest.mark.asyncio
async def test_prova2_fonte_muda_é_bloqueio_declarado_nunca_passagem() -> None:
    """O contraste com a shadow: LÁ o defeito da fonte é engolido (resposta byte-idêntica);
    AQUI UNKNOWN vira BLOQUEIO declarado — PENDENTE/ausente nunca é APROVADO por omissão."""
    store = _Store()
    store.fail_reads = LegitimacyScreenStoreError("source_unavailable")
    enforcing, _, source = _enforcing(store)

    result = await _execute(enforcing)

    assert (result.status, result.reason_code) == ("unavailable", SOURCE_UNAVAILABLE)
    assert source.calls == ["publication"]


@pytest.mark.asyncio
async def test_prova2_fonte_lenta_bloqueia_sem_pendurar_o_caminho() -> None:
    store = _Store(_receipt(status="invalid"))
    store.delay = 30.0
    enforcing, _, _ = _enforcing(store, timeout_seconds=0.2)

    result = await asyncio.wait_for(_execute(enforcing), timeout=5.0)

    assert (result.status, result.reason_code) == ("unavailable", SOURCE_UNAVAILABLE)


@pytest.mark.asyncio
async def test_prova2_store_hostil_não_escolhe_o_token_da_recusa() -> None:
    """A tradução do defeito é FIXA: uma fonte hostil que levante `CommercialRefusalError`
    na leitura não escolhe o par que anda no wire — o par declarado é sempre o de
    indisponibilidade, nunca o da fonte."""
    store = _Store()
    store.fail_reads = CommercialRefusalError("denied", "actor_denied")
    enforcing, _, _ = _enforcing(store)

    result = await _execute(enforcing)

    assert (result.status, result.reason_code) == ("unavailable", SOURCE_UNAVAILABLE)
    assert result.reason_code != "actor_denied"


@pytest.mark.asyncio
async def test_prova2_defeito_da_sinalização_não_vira_passagem_e_a_linha_sai() -> None:
    store = _Store(_receipt(status="invalid"))
    sink = _Sink()
    sink.fail = True
    enforcing, _, source = _enforcing(store, events=sink)

    result = await _execute(enforcing)

    # O BLOQUEIO persiste (fail-closed) e a observação foi registrada ANTES da falha do
    # coletor — nenhum defeito da perna SINALIZAR compra passagem.
    assert (result.status, result.reason_code) == ("unavailable", SOURCE_UNAVAILABLE)
    assert store.save_calls == 1
    assert sink.events == []
    assert source.calls == ["publication"]


@pytest.mark.asyncio
async def test_prova2_recibo_vigente_é_pré_condição_e_não_negação_universal() -> None:
    store = _Store(_receipt(status="valid"))
    sink = _Sink()
    enforcing, _, source = _enforcing(store, events=sink)

    result = await _execute(enforcing)

    assert result.status == "succeeded"
    assert "prepare" in source.calls and source.calls.count("execute") == 1
    assert [event.kind for event in sink.events] == ["legitimacy.approved"]
    assert store.save_calls == 0  # a cabeça JÁ é a verificação de registro da PJ


@pytest.mark.asyncio
async def test_enforcing_registra_a_observação_do_bloqueio_uma_única_vez() -> None:
    store = _Store(_receipt(status="invalid"))
    sink = _Sink()
    enforcing, _, _ = _enforcing(store, events=sink)

    primeiro = await _execute(enforcing)
    segundo = await _execute(enforcing)

    assert (primeiro.status, segundo.status) == ("denied", "denied")
    assert segundo.wire_bytes == primeiro.wire_bytes
    assert store.save_calls == 1  # a cabeça já carrega o sinal; reenvio não escreve de novo
    head = store.head("tenant-a", "entity-a")
    assert head is not None
    assert head.screen_revision == 2 and head.would_block is True
    assert head.created_by == GUARD_ACTOR
    assert head.art39_anchor == ART39_ANCHOR == "RN 557/2022, art. 39"
    assert len(sink.events) == 4  # os eventos seguem saindo; a linha, não


@pytest.mark.asyncio
async def test_enforcing_gêmeo_do_empresário_individual_sinaliza_com_âncora_própria() -> None:
    store = _Store(_receipt(contract_kind="empresario_individual", status="invalid"))
    sink = _Sink()
    enforcing, _, _ = _enforcing(store, events=sink)

    result = await _execute(enforcing)

    assert (result.status, result.reason_code) == ("denied", LEGITIMACY_UNPROVEN)
    art39 = sink.events[1]
    assert art39.art39 is not None
    assert art39.art39.anchor == ART39_TWIN_ANCHOR == "RN 557/2022, art. 10, §§ 2º e 3º"
    # A LINHA persistida carrega sempre a âncora canônica do art. 39 (Literal do substrato).
    head = store.head("tenant-a", "entity-a")
    assert head is not None and head.art39_anchor == "RN 557/2022, art. 39"


# ----------------------------------------------------------------------------------
# Prova 4 (extensão) — a enforcing NUNCA é alcançável por ambiente.
# ----------------------------------------------------------------------------------


def test_prova4_a_porta_da_flag_recusa_a_postura_enforcing() -> None:
    gateway, _, _ = _flag_off()

    def fábrica(inner: Any) -> LegitimacyScreenEnforcing:
        return LegitimacyScreenEnforcing(
            store=_Store(),
            tenant="tenant-a",
            codec=inner.codec,
            admission=inner.admission,
            source=inner.source,
            clock=lambda: NOW,
            timeout_seconds=inner.timeout_seconds,
        )

    with pytest.raises(ValueError, match="legitimacy_screen_posture_invalid"):
        collective_legitimacy_shadow(gateway, enabled=True, guard_factory=fábrica)


def test_prova4_nenhum_caminho_de_composição_default_monta_a_classe() -> None:
    """Desenho §4: "a classe existe, está provada, e não há caminho de composição default
    que a monte" — a árvore inteira do portal não a nomeia (a cerca do PR 3 pina
    app.py/production.py; esta extensão pina o resto da árvore)."""
    raiz = Path(__file__).resolve().parents[3] / "src" / "maezo" / "portal"
    encontrados = [
        str(path)
        for path in sorted(raiz.rglob("*.py"))
        if "LegitimacyScreenEnforcing" in path.read_text(encoding="utf-8")
    ]
    assert encontrados == []


@pytest.mark.asyncio
async def test_prova4_o_wrap_enforcing_passa_no_isinstance_do_router_congelado() -> None:
    enforcing, _, _ = _enforcing(_Store())
    assert isinstance(enforcing, CommercialGateway)  # portal/api/commercial.py:51 — sem editá-lo
    assert type(enforcing) is LegitimacyScreenEnforcing
    assert type(enforcing) is not LegitimacyScreenShadow


# ----------------------------------------------------------------------------------
# Prova 8 — firewall PHI camada 2 (admissão hostil).
# ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prova8_a_triagem_é_independente_do_corpo() -> None:
    """A camada 2 avalia DOIS fatos contratacionais — a PJ e o recibo — e NUNCA abre o
    corpo do comando: a mesma PJ sem recibo produz A MESMA recusa para os sete comandos,
    qualquer corpo que viaje (um corpo hostil não compra passagem nem muda o veredito)."""
    bytes_vistos: set[bytes] = set()
    for comando in sorted(COLLECTIVE_BOUND_SET):
        enforcing, _, source = _enforcing(_Store(_receipt(status="invalid")))
        resultado = await _execute(enforcing, comando.encode())
        bytes_vistos.add(resultado.wire_bytes)
        assert source.calls == ["publication"], comando
    assert len(bytes_vistos) == 1


def test_prova8_modelos_fechados_sem_slot_de_dado_clínico() -> None:
    with pytest.raises(ValidationError):
        LegitimacyScreenEvent(
            kind="legitimacy.blocked",
            tenant="tenant-a",
            contract_kind=None,
            would_block=True,
            diagnostico="j45",  # type: ignore[call-arg] — nenhum slot de dado clínico existe
        )
    with pytest.raises(ValidationError):
        LegitimacyScreenEvent(
            kind="legitimacy.blocked",
            tenant="tenant-a",
            contract_kind=None,
            would_block=True,
            cid10="C61",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        LegitimacyCriterion(
            criterion_id="vinculo_beneficiario_contratante",
            result="proven",
            norm_anchor="RN 557/2022, art. 5º",
            historico_clinico="...",  # type: ignore[call-arg]
        )


def test_prova8_vocabulário_da_camada_2_pino_e_fora_do_enum_congelado() -> None:
    texto = _GUARD_MODULE.read_text(encoding="utf-8")
    assert "wire_bytes" not in texto  # o corpo do comando NUNCA é aberto pela camada 2
    assert PHI_IN_COMMERCIAL_INPUT == "phi_in_commercial_input"
    contrato = json.loads(
        Path("tests/candidate_artifacts/commercial/contract-c6-4.json").read_text(encoding="utf-8")
    )
    enum = next(
        braço["properties"]["reason_code"]["enum"]
        for braço in contrato["$defs"]["OperationalResult"]["oneOf"]
        if "enum" in braço["properties"]["reason_code"]
    )
    assert SOURCE_UNAVAILABLE in enum
    assert LEGITIMACY_UNPROVEN not in enum
    assert PHI_IN_COMMERCIAL_INPUT not in enum
