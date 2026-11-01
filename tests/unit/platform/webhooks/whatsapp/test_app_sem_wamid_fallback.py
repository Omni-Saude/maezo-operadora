"""Mensagem SEM `wamid` — identidade de fallback no registro DURAVEL (F-147 / R-060).

Decisao do dono de 2026-10-31 (#1084, Rodada 2 "Marina / WhatsApp / chaves"): a mensagem sem
`wamid` continua sendo despachada, mas passa a deixar uma identidade de fallback no registro
duravel de dedup (`driver_idempotency`); ate 2 entregas iguais ao mesmo beneficiario passam, da 3a
em diante a entrega e' SUPRIMIDA em silencio (nenhum aviso ao beneficiario), e um contador de
rotulos fechados mede o caminho. Tudo atras de `WHATSAPP_WEBHOOK_SEM_WAMID_FALLBACK` (padrao
desligado). O DPO confirma a classificacao do registro (nota no plano de erasure, campo em branco).

O que cada bloco fixa:

* flag desligada  -> comportamento de antes byte a byte (despacha, anuncia, nada e' reivindicado),
  so' que agora contado;
* flag ligada     -> 1a e 2a entregas despachadas, 3a em diante suprimidas sem aviso;
* reinicio        -> o limite vive no registro, nao no processo: uma app nova continua suprimindo;
* turno que falha -> devolve o seu slot (nao converte falha transitoria em perda silenciosa);
* registro fora   -> continua despachando ("manter despacho"), anunciado e contado;
* PHI             -> nenhum telefone, texto ou wamid cru em chave, log ou rotulo de metrica.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import structlog
from fastapi import FastAPI

from maezo.gateway.pseudonymizer import KEYED_PSEUDONYM_PREFIX, Pseudonymizer
from maezo.platform.observability import (
    MENSAGEM_SEM_WAMID_DESFECHOS,
    get_metrics_collector,
    record_mensagem_sem_wamid,
)
from maezo.platform.webhooks.whatsapp.app import create_app
from maezo.platform.webhooks.whatsapp.dedup import FALLBACK_MAX_ENTREGAS, WhatsAppDedupGuard
from maezo.platform.webhooks.whatsapp.dispatch import (
    InboundMessage,
    InboundNonTextMessage,
    extract_inbound_messages,
)
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
from tests.support.dedup_fakes import FakeDedupRegistry

from .test_app_dedup import (
    TENANT,
    _envelope,
    _FakeDispatcher,
    _guard,
    _post,
    _settings,
)

_PHONE = "5511987654321"
#: Corpo sintetico com cara de PHI (CPF ficticio) — nao pode aparecer em lugar nenhum.
_BODY = "meu cpf e 12345678909"
_TS = "1730419200"
_FLAG_ENV = "WHATSAPP_WEBHOOK_SEM_WAMID_FALLBACK"
_METRIC = "maezo_webhook_mensagem_sem_wamid_total"


def _sem_wamid(*, body: str = _BODY, timestamp: str | None = _TS, phone: str = _PHONE) -> dict[str, Any]:
    msg: dict[str, Any] = {"from": phone, "type": "text", "text": {"body": body}}
    if timestamp is not None:
        msg["timestamp"] = timestamp
    return msg


def _sem_wamid_nao_texto(*, timestamp: str = _TS) -> dict[str, Any]:
    return {"from": _PHONE, "type": "audio", "timestamp": timestamp}


def _metric(desfecho: str) -> float:
    value = get_metrics_collector().registry.get_sample_value(
        _METRIC, {"tenant": TENANT, "desfecho": desfecho}
    )
    return float(value or 0.0)


def _app(registry: FakeDedupRegistry, dispatcher: _FakeDispatcher, *, flag: bool = True) -> FastAPI:
    return create_app(  # type: ignore[arg-type]
        _settings(sem_wamid_identidade_fallback=flag), dispatcher=dispatcher, dedup=_guard(registry)
    )


# ---------------------------------------------------------------------------
# Flag: padrao desligado, alias de ambiente, desligada = comportamento de antes
# ---------------------------------------------------------------------------


def test_flag_is_off_by_default() -> None:
    assert _settings().sem_wamid_identidade_fallback is False


def test_flag_binds_from_its_env_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FLAG_ENV, "true")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "s")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "v")
    assert WhatsAppWebhookSettings().sem_wamid_identidade_fallback is True  # type: ignore[call-arg]


async def test_env_alias_reaches_the_receiver_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fiacao inteira: env -> settings -> `create_app` (o mesmo objeto que `service.py` passa)."""
    monkeypatch.setenv(_FLAG_ENV, "true")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-app-secret")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "test-verify-token")
    monkeypatch.setenv("TENANT_ID", TENANT)
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    app = create_app(  # type: ignore[arg-type]
        WhatsAppWebhookSettings(),
        dispatcher=dispatcher,
        dedup=_guard(registry),  # type: ignore[call-arg]
    )

    for _ in range(3):
        await _post(app, _envelope(_sem_wamid()))

    assert len(dispatcher.dispatched) == FALLBACK_MAX_ENTREGAS


async def test_flag_off_keeps_dispatching_every_delivery_and_claims_nothing() -> None:
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    app = _app(registry, dispatcher, flag=False)
    before = _metric("nao_registrada")

    with structlog.testing.capture_logs() as logs:
        for _ in range(3):
            response = await _post(app, _envelope(_sem_wamid()))
            assert response.status_code == 200

    assert len(dispatcher.dispatched) == 3
    assert registry.calls == [], "flag desligada: nada pode ser reivindicado"
    assert sum(1 for e in logs if e["event"] == "whatsapp_webhook_message_without_wamid") == 3
    assert _metric("nao_registrada") == before + 3


# ---------------------------------------------------------------------------
# Flag ligada: ate 2, suprime a 3a em diante, silenciosa
# ---------------------------------------------------------------------------


async def test_third_identical_delivery_onward_is_suppressed_silently() -> None:
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    app = _app(registry, dispatcher)
    registrada, suprimida = _metric("registrada"), _metric("suprimida")
    payload = _envelope(_sem_wamid())

    responses = [await _post(app, payload) for _ in range(4)]

    assert [r.status_code for r in responses] == [200, 200, 200, 200]
    assert len(dispatcher.dispatched) == 2, "ate 2 entregas passam"
    assert dispatcher.acknowledged == [], "supressao silenciosa: nenhum aviso ao beneficiario"
    assert responses[0].json() == {"status": "ok", "dispatched": 1, "failed": 0}
    assert responses[1].json() == {"status": "ok", "dispatched": 1, "failed": 0}
    assert responses[2].json() == {"status": "ok", "dispatched": 0, "failed": 0, "duplicates": 1}
    assert responses[3].json() == {"status": "ok", "dispatched": 0, "failed": 0, "duplicates": 1}
    assert _metric("registrada") == registrada + 2
    assert _metric("suprimida") == suprimida + 2
    # Os dois slots foram selados (o efeito aconteceu) — nao ficam `pending` ate o lease.
    assert sorted(registry.rows.values()) == ["processed", "processed"]


async def test_max_entregas_is_the_owner_number() -> None:
    """O "ate 2" e' decisao do dono (#1084, 2026-10-31), nao um ajuste de engenharia."""
    assert FALLBACK_MAX_ENTREGAS == 2


async def test_suppressed_non_text_is_not_acknowledged_a_third_time() -> None:
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    app = _app(registry, dispatcher)

    for _ in range(3):
        await _post(app, _envelope(_sem_wamid_nao_texto()))

    assert len(dispatcher.acknowledged) == 2
    assert dispatcher.dispatched == []


async def test_a_real_repeat_with_a_new_meta_timestamp_is_not_suppressed() -> None:
    """O beneficiario que de fato repete "sim" gera outro `timestamp` da Meta -> outra identidade."""
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    app = _app(registry, dispatcher)

    for i in range(4):
        await _post(app, _envelope(_sem_wamid(body="sim", timestamp=str(1730419200 + i))))

    assert len(dispatcher.dispatched) == 4


async def test_wamid_traffic_is_untouched_by_the_flag() -> None:
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    app = _app(registry, dispatcher)
    msg = {"from": _PHONE, "id": "wamid.ABC", "type": "text", "text": {"body": "oi"}, "timestamp": _TS}

    await _post(app, _envelope(msg))
    await _post(app, _envelope(msg))

    assert len(dispatcher.dispatched) == 1
    assert all(key.startswith(f"wa:inbound:{TENANT}:") for _, key in registry.calls)


# ---------------------------------------------------------------------------
# Durabilidade: o limite sobrevive a reinicio (vive no registro, nao no processo)
# ---------------------------------------------------------------------------


async def test_the_limit_survives_a_receiver_restart() -> None:
    registry = FakeDedupRegistry()  # o registro DURAVEL — sobrevive ao processo
    payload = _envelope(_sem_wamid())

    before_restart = _FakeDispatcher()
    app = _app(registry, before_restart)
    await _post(app, payload)
    await _post(app, payload)

    # "Reinicio": app nova, guard novo, pseudonimizador reconstruido com a MESMA chave.
    after_restart = _FakeDispatcher()
    restarted = _app(registry, after_restart)
    response = await _post(restarted, payload)

    assert len(before_restart.dispatched) == 2
    assert after_restart.dispatched == [], "a 3a entrega continua suprimida depois do reinicio"
    assert response.json()["duplicates"] == 1


# ---------------------------------------------------------------------------
# Turno que falha devolve o slot; registro indisponivel mantem o despacho
# ---------------------------------------------------------------------------


async def test_a_failed_turn_releases_its_slot() -> None:
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher(fail_times=1)
    app = _app(registry, dispatcher)
    payload = _envelope(_sem_wamid())

    first = await _post(app, payload)
    assert first.status_code == 500  # nada no lote deu certo -> a Meta tenta de novo
    assert registry.rows == {}, "o slot do turno que falhou foi devolvido"

    for _ in range(3):
        await _post(app, payload)

    assert len(dispatcher.dispatched) == 2, "o slot devolvido foi reaproveitado; a 4a entrega e' a suprimida"


async def test_an_unreachable_registry_keeps_dispatching_and_says_so() -> None:
    registry = FakeDedupRegistry(unavailable=True)
    dispatcher = _FakeDispatcher()
    app = _app(registry, dispatcher)
    before = _metric("registro_indisponivel")

    with structlog.testing.capture_logs() as logs:
        response = await _post(app, _envelope(_sem_wamid()))

    assert response.status_code == 200
    assert len(dispatcher.dispatched) == 1, "decisao do dono: manter despacho"
    assert _metric("registro_indisponivel") == before + 1
    assert any(e["event"] == "whatsapp_webhook_sem_wamid_registro_indisponivel" for e in logs)


# ---------------------------------------------------------------------------
# PHI: nada cru em chave, log ou rotulo
# ---------------------------------------------------------------------------


async def test_no_raw_phi_in_keys_logs_or_metric_labels() -> None:
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    app = _app(registry, dispatcher)
    payload = _envelope(_sem_wamid())

    with structlog.testing.capture_logs() as logs:
        for _ in range(3):
            await _post(app, payload)

    keys = [key for _, key in registry.calls]
    assert keys, "a identidade de fallback tem que ter sido gravada no registro"
    for key in keys:
        assert key.startswith(f"wa:inbound-sem-wamid:{TENANT}:{KEYED_PSEUDONYM_PREFIX}")
        assert _PHONE not in key and _PHONE[2:] not in key
        assert "12345678909" not in key and _BODY not in key

    rendered = json.dumps(logs, default=str)
    assert _PHONE not in rendered and _PHONE[2:] not in rendered
    assert "12345678909" not in rendered

    suppressed = [e for e in logs if e["event"] == "whatsapp_webhook_sem_wamid_suprimida"]
    assert len(suppressed) == 1
    # Correlacionavel pelos pseudonimos KEYED, nunca pelo dado cru.
    assert suppressed[0]["conversation_id"].startswith(f"wa:{TENANT}:{KEYED_PSEUDONYM_PREFIX}")
    assert suppressed[0]["identidade_fallback"].startswith(KEYED_PSEUDONYM_PREFIX)

    for metric in get_metrics_collector().registry.collect():
        if metric.name != _METRIC.removesuffix("_total"):
            continue
        for sample in metric.samples:
            assert set(sample.labels) <= {"tenant", "desfecho"}
            for value in sample.labels.values():
                assert _PHONE not in value and "12345678909" not in value


def test_desfecho_label_is_a_closed_vocabulary() -> None:
    assert (
        frozenset({"nao_registrada", "registrada", "suprimida", "registro_indisponivel", "desconhecido"})
        == MENSAGEM_SEM_WAMID_DESFECHOS
    )
    before = _metric("desconhecido")

    record_mensagem_sem_wamid(tenant=TENANT, desfecho=_PHONE)  # valor fora do vocabulario

    assert _metric("desconhecido") == before + 1
    assert _metric(_PHONE) == 0.0, "um valor livre nunca vira serie temporal"


# ---------------------------------------------------------------------------
# A identidade: deterministica, keyed, sensivel a cada campo
# ---------------------------------------------------------------------------


def _ident(guard: WhatsAppDedupGuard, **over: Any) -> str:
    fields: dict[str, Any] = {"phone": _PHONE, "body": _BODY, "timestamp": _TS, "type": "text"}
    fields.update(over)
    if fields["type"] == "text":
        message: InboundMessage | InboundNonTextMessage = InboundMessage(
            from_number=fields["phone"], text=fields["body"], message_id="", timestamp=fields["timestamp"]
        )
    else:
        message = InboundNonTextMessage(
            from_number=fields["phone"],
            message_type=fields["type"],
            message_id="",
            timestamp=fields["timestamp"],
        )
    return guard.fallback_identity(message)


def test_fallback_identity_is_deterministic_keyed_and_field_sensitive() -> None:
    guard = _guard(FakeDedupRegistry())
    base = _ident(guard)

    assert base == _ident(guard), "a mesma reentrega da Meta tem que gerar a mesma identidade"
    assert base.startswith(KEYED_PSEUDONYM_PREFIX)
    assert _ident(guard, body=f"  {_BODY}  ") == base, "normalizacao: espacas das bordas nao contam"
    for variant in (
        {"phone": "5511900000000"},
        {"body": "outro texto"},
        {"timestamp": "1730419201"},
        {"type": "image"},
    ):
        assert _ident(guard, **variant) != base, variant

    other_key = WhatsAppDedupGuard(
        registry=FakeDedupRegistry(),
        pseudonymizer=Pseudonymizer.from_settings(
            phi_hmac_key="outra-chave", production=False, tenant_id=TENANT
        ),
        tenant=TENANT,
    )
    assert _ident(other_key) != base, "sem a chave do vault nao se reproduz a identidade"


def test_the_meta_timestamp_is_parsed_without_changing_the_wamid_path() -> None:
    [message] = extract_inbound_messages(json.loads(_envelope(_sem_wamid())))
    assert isinstance(message, InboundMessage)
    assert message.timestamp == _TS
    assert message.message_id == ""
    assert _TS not in repr(message) and _PHONE not in repr(message)
