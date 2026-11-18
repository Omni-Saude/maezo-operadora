"""DL-0186 (unidade 2b): ponta a ponta sintetico — composicao registrada na tarefa -> harness completa ->
linhas no outbox -> relay publica (produtor falso) -> o inbox grava UMA linha por grupo da fila, do pin.

Sem engine, Postgres nem broker. O pin e o sintetico 2.3.0 (`rco_k1_sintetico`) com `grupo_principal`
declarado no K6; o grupo vem do pin de bordo (monkeypatch de `rco_pin.rco_rules_pin`), nunca do teste. Prova
tambem a idempotencia: re-entrega da tarefa e re-publicacao do relay convergem em uma linha por fila; uma
fila sem grupo para no relay (`fila_sem_grupo`, antes de publicar) e, se o pin do consumidor diferir, termina
na fila morta com codigo fechado, sem linha em grupo nenhum.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from maezo.platform import rco_ativacao as at
from maezo.platform import rco_avisos_relay
from maezo.platform.integrations.notifications_bridge import (
    REASON_RCO_FILA_SEM_GRUPO,
    BridgeDlqShunt,
    BridgeMessage,
    FakeBridgeKafkaConsumer,
)
from maezo.platform.integrations.notifications_inbox import run_inbox_loop
from maezo.tools.workers import rco_pin
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.platform import rco_k1_sintetico as sint
from tests.unit.platform.integrations.test_notifications_inbox import _SpyInbox
from tests.unit.platform.integrations.test_notifications_inbox_rco import InboxRcoEmMemoria, _Pub
from tests.unit.platform.test_rco_avisos import OutboxEmMemoria, ProdutorFalso
from tests.unit.tools.workers.test_harness_rco_avisos import _montar, _proibido, _task

TOPICO_NOTIFICACOES = "operadora.notifications.internal"


def _pin_com_grupos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **grupos: str) -> rco_pin.RcoRulesPin:
    filas: dict[str, Any] = {nome: dict(item) for nome, item in sint.K6_FILAS.items()}
    for fila, grupo in grupos.items():
        filas[fila]["grupo_principal"] = grupo
    sint.com_formato_2_3(tmp_path, sint.montar(tmp_path), k6=sint.k6_2_3(filas=filas))
    carregado = rco_pin.load_rco_rules_pin(tmp_path / sint.LOCK_REL, rules_dir=tmp_path / sint.RULES_DIR)
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: carregado)
    monkeypatch.delenv(at.CHAVE_EFEITO_K1, raising=False)
    monkeypatch.setattr(at, "efeito_k1_habilitado", _proibido)
    monkeypatch.setattr(rco_pin, "efeito_k1_habilitado", _proibido)
    at.limpar_cache_estados()
    return carregado


def _mensagens(produtor: ProdutorFalso) -> list[BridgeMessage]:
    assert {p[0] for p in produtor.publicadas} == {TOPICO_NOTIFICACOES}
    return [
        BridgeMessage.from_value(p[1], topic=TOPICO_NOTIFICACOES, offset=i)
        for i, p in enumerate(produtor.publicadas)
    ]


async def test_ponta_a_ponta_ate_a_linha_do_grupo_com_reentrega(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = _pin_com_grupos(tmp_path, monkeypatch, auditoria_medica="grupo-sint-a", cadastro="grupo-sint-b")
    outbox = OutboxEmMemoria()
    harness, transport, sink = _montar(pin, outbox=outbox)
    await harness._handle(_task())
    await harness._handle(_task())  # re-entrega da tarefa: mesma chave, nada duplica no outbox
    assert len(transport.completed) == 2 and len(outbox.linhas) == 2

    produtor = ProdutorFalso()
    rel = await rco_avisos_relay.drain_once(outbox, produtor, reivindicada_por="relay-1")
    assert rel.entregues == 2

    inbox = InboxRcoEmMemoria()
    mensagens = _mensagens(produtor)
    # at-least-once: o relay (ou o broker) entrega as mesmas mensagens de novo
    consumer = FakeBridgeKafkaConsumer([*mensagens, *mensagens])
    await run_inbox_loop(consumer, _SpyInbox(), avisos_rco=inbox)  # grupos: pin de bordo (padrao)
    assert consumer.commits == 4
    linhas = sorted((a.linha.fila, a.grupo, a.linha.task_id) for a in inbox.linhas.values())
    assert linhas == [("auditoria_medica", "grupo-sint-a", "task-1"), ("cadastro", "grupo-sint-b", "task-1")]
    chaves_outbox = {r["linha"].chave_idempotencia for r in outbox.linhas.values()}
    assert {a.linha.chave_idempotencia for a in inbox.linhas.values()} == chaves_outbox
    assert "bk-1" not in repr(list(inbox.linhas.values()))  # sem chave de negocio
    assert sink.emitted[0][0].dmn_versions["rco_sint_d"]["sinaisAuditoria"] == [sint.SINAL]


async def test_ponta_a_ponta_fila_sem_grupo_para_no_relay_antes_de_publicar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DL-0186: o relay le o MESMO pin de bordo e marca `fila_sem_grupo` antes de publicar; nada da fila sem
    grupo chega ao inbox nem a fila morta."""
    pin = _pin_com_grupos(tmp_path, monkeypatch, auditoria_medica="grupo-sint-a")  # `cadastro` sem grupo
    outbox = OutboxEmMemoria()
    harness, transport, _ = _montar(pin, outbox=outbox)
    await harness._handle(_task())
    assert len(transport.completed) == 1  # a decisao nao depende do grupo
    produtor = ProdutorFalso()
    rel = await rco_avisos_relay.drain_once(outbox, produtor, reivindicada_por="relay-1")
    assert (rel.entregues, rel.sem_grupo) == (1, 1)
    estados = sorted((r["linha"].fila, r["status"], r["ultimo_erro"]) for r in outbox.linhas.values())
    assert estados == [("auditoria_medica", "entregue", None), ("cadastro", "falhou", "fila_sem_grupo")]

    inbox = InboxRcoEmMemoria()
    dlq = BridgeDlqShunt(publisher=_Pub(), audit_sink=FakeStartAuditSink(), tenant_id="amh")  # type: ignore[arg-type]
    consumer = FakeBridgeKafkaConsumer(_mensagens(produtor))
    await run_inbox_loop(consumer, _SpyInbox(), dlq=dlq, avisos_rco=inbox)
    assert [(a.linha.fila, a.grupo) for a in inbox.linhas.values()] == [("auditoria_medica", "grupo-sint-a")]
    assert dlq.shunted == []
    assert consumer.commits == 1


async def test_ponta_a_ponta_pin_do_consumidor_sem_grupo_termina_na_fila_morta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Segunda barreira: o relay publicou com um pin que tinha os dois grupos, o consumidor le um pin em que
    `cadastro` nao tem grupo -> fila morta com codigo fechado, sem linha em grupo nenhum."""
    pin = _pin_com_grupos(tmp_path, monkeypatch, auditoria_medica="grupo-sint-a")
    outbox = OutboxEmMemoria()
    harness, _, _ = _montar(pin, outbox=outbox)
    await harness._handle(_task())
    produtor = ProdutorFalso()
    relay_grupos = {"auditoria_medica": "grupo-sint-a", "cadastro": "grupo-sint-b"}
    await rco_avisos_relay.drain_once(
        outbox, produtor, reivindicada_por="relay-1", grupos_do_pin=lambda: relay_grupos
    )

    inbox = InboxRcoEmMemoria()
    dlq = BridgeDlqShunt(publisher=_Pub(), audit_sink=FakeStartAuditSink(), tenant_id="amh")  # type: ignore[arg-type]
    consumer = FakeBridgeKafkaConsumer(_mensagens(produtor))
    await run_inbox_loop(consumer, _SpyInbox(), dlq=dlq, avisos_rco=inbox)  # grupos: pin de bordo
    assert [(a.linha.fila, a.grupo) for a in inbox.linhas.values()] == [("auditoria_medica", "grupo-sint-a")]
    assert dlq.shunted == [(f"{TOPICO_NOTIFICACOES}.dlq", REASON_RCO_FILA_SEM_GRUPO)]
    assert consumer.commits == 2
