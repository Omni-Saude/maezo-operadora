"""DL-0186 (unidade 2b): o inbox consome `rco.aviso_auditoria` e grava UMA linha do grupo da fila, do pin.

Sem Postgres nem broker: `FakeBridgeKafkaConsumer` e um inbox em memoria com a semantica do contrato
(`InboxAvisosRco.registrar`: `RECORDED` na primeira, `DUPLICATE` na re-entrega). Os grupos vem do recorte REAL
do K1 2.3.0 (`auditoria_medica` -> `medico-auditor`, `cadastro` -> `cadastro-beneficiarios`). Provas: o offset
so avanca depois do recibo; re-entrega = uma linha; fila sem `grupo_principal` termina como falha com codigo
fechado (fila morta), nunca em outro grupo; sem inbox ligado o tipo segue ignorado (contado); metrica fechada.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.platform import observability, rco_avisos
from maezo.platform import rco_manifesto as k1
from maezo.platform.integrations import notifications_inbox as inbox_mod
from maezo.platform.integrations.notifications_bridge import (
    BRIDGE_DLQ_REASONS,
    REASON_RCO_AVISO_INVALIDO,
    REASON_RCO_FILA_SEM_GRUPO,
    BridgeDlqShunt,
    BridgeMessage,
    FakeBridgeKafkaConsumer,
)
from maezo.platform.integrations.notifications_inbox import (
    RCO_AVISO_CONSUMO,
    AvisoRcoGrupo,
    InboxAvisosRco,
    MalformedRcoAvisoError,
    MalformedTeamNoticeError,
    NoticeRecordOutcome,
    TeamNoticeReceipt,
    handle_inbox_message,
    parse_aviso_rco,
    resolver_grupo,
    run_inbox_loop,
)
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.platform import rco_k1_2_3_recorte as rec
from tests.unit.platform.integrations.test_notifications_inbox import _notification, _SpyInbox

_AGORA = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
SINAL = "aviso_urgencia_carencia"


def grupos_do_recorte() -> Mapping[str, str]:
    return k1.carregar_grupos_k6(rec.manifesto(), k1_dir=rec.RECORTE)


def _linhas(task_id: str = "task-1") -> tuple[rco_avisos.LinhaAviso, ...]:
    return rco_avisos.linhas_de_aviso(
        tenant="amh",
        task_id=task_id,
        composicoes=[(rec.COMPOSICAO, (SINAL,))],
        avisos_do_pin=k1.carregar_avisos_k6(rec.manifesto(), rec.vocabulario(), k1_dir=rec.RECORTE),
    )


def _mensagem(destino: str = "auditoria_medica", **trocas: Any) -> dict[str, Any]:
    linha = next(x for x in _linhas() if x.fila == destino)
    payload: dict[str, Any] = rco_avisos.montar_payload(linha)
    payload.update(trocas)
    return payload


@dataclass
class InboxRcoEmMemoria:
    """O contrato de `InboxAvisosRco`: unico por (tenant, notice_ref); a re-entrega e `DUPLICATE`."""

    linhas: dict[tuple[str, str], AvisoRcoGrupo] = field(default_factory=dict)
    falhar: BaseException | None = None

    async def registrar(self, aviso: AvisoRcoGrupo) -> TeamNoticeReceipt:
        if self.falhar is not None:
            raise self.falhar
        chave = (aviso.tenant_id, aviso.notice_ref)
        outcome = NoticeRecordOutcome.DUPLICATE if chave in self.linhas else NoticeRecordOutcome.RECORDED
        self.linhas.setdefault(chave, aviso)
        return TeamNoticeReceipt(notice_ref=aviso.notice_ref, outcome=outcome, recorded_at=_AGORA)


@pytest.fixture
def contados(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    registro: list[str] = []
    monkeypatch.setattr(observability, "record_rco_aviso_consumo", registro.append)
    return registro


class _Pub:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, bytes]]] = []

    async def publish_dlq(self, topic: str, *, raw: bytes, key: bytes | None, headers: Any) -> None:
        self.sent.append((topic, dict(headers)))


def _msg(payload: Any, offset: int = 0) -> BridgeMessage:
    return BridgeMessage.from_value(payload, topic="operadora.notifications.internal", offset=offset)


# --- forma e grupo -----------------------------------------------------------------------------------


def test_mensagem_do_relay_vira_aviso_do_grupo_do_pin() -> None:
    linha = parse_aviso_rco(_mensagem())
    assert linha is not None
    aviso = resolver_grupo(linha, grupos_do_recorte())
    assert (aviso.grupo, aviso.audience, aviso.linha.fila) == ("medico-auditor", "staff", "auditoria_medica")
    outra = resolver_grupo(parse_aviso_rco(_mensagem("cadastro")) or linha, grupos_do_recorte())
    assert outra.grupo == "cadastro-beneficiarios"


def test_outro_tipo_nao_e_aviso_rco() -> None:
    assert parse_aviso_rco(_notification()) is None
    assert parse_aviso_rco("nao e objeto") is None


@pytest.mark.parametrize(
    ("trocas", "remover"),
    [
        ({"extra": "x"}, None),
        ({}, "prazo_parametro"),
        ({"fila": "fila com espaco"}, None),
        ({"task_id": ""}, None),
        ({"chave_idempotencia": "0" * 64}, None),  # nao bate com o conteudo
        ({"sinal": None}, None),
    ],
)
def test_mensagem_fora_da_forma_e_recusa_deterministica(trocas: dict[str, Any], remover: str | None) -> None:
    payload = _mensagem(**trocas)
    if remover:
        del payload[remover]
    with pytest.raises(MalformedRcoAvisoError) as exc:
        parse_aviso_rco(payload)
    assert exc.value.code == REASON_RCO_AVISO_INVALIDO
    assert isinstance(exc.value, MalformedTeamNoticeError)  # o laco o leva a fila morta


def test_fila_sem_grupo_no_pin_falha_com_codigo_fechado_e_nunca_vai_a_outro_grupo() -> None:
    linha = parse_aviso_rco(_mensagem("cadastro"))
    assert linha is not None
    with pytest.raises(MalformedRcoAvisoError) as exc:
        resolver_grupo(linha, {"auditoria_medica": "medico-auditor"})
    assert exc.value.code == REASON_RCO_FILA_SEM_GRUPO
    assert "cadastro" not in exc.value.reason and "medico" not in exc.value.reason


def test_identidade_e_a_chave_do_outbox_e_o_grupo() -> None:
    linha = parse_aviso_rco(_mensagem())
    assert linha is not None
    a = AvisoRcoGrupo(linha=linha, grupo="medico-auditor")
    assert a.notice_ref == AvisoRcoGrupo(linha=linha, grupo="medico-auditor").notice_ref
    assert a.notice_ref != AvisoRcoGrupo(linha=linha, grupo="outro-grupo").notice_ref
    outra = parse_aviso_rco(_mensagem("cadastro"))
    assert outra is not None and AvisoRcoGrupo(linha=outra, grupo="medico-auditor").notice_ref != a.notice_ref


# --- laco: recibo antes do commit, re-entrega, falhas --------------------------------------------------


async def test_o_offset_so_avanca_depois_da_linha_do_grupo(contados: list[str]) -> None:
    rco = InboxRcoEmMemoria()
    consumer = FakeBridgeKafkaConsumer([_mensagem(), _mensagem("cadastro")])
    await run_inbox_loop(consumer, _SpyInbox(), avisos_rco=rco, grupos_do_pin=grupos_do_recorte)
    assert sorted(a.grupo for a in rco.linhas.values()) == ["cadastro-beneficiarios", "medico-auditor"]
    assert consumer.commits == 2
    assert contados == ["registrado", "registrado"]


async def test_reentrega_e_uma_linha(contados: list[str]) -> None:
    rco = InboxRcoEmMemoria()
    consumer = FakeBridgeKafkaConsumer([_mensagem(), _mensagem(), _mensagem()])
    await run_inbox_loop(consumer, _SpyInbox(), avisos_rco=rco, grupos_do_pin=grupos_do_recorte)
    assert len(rco.linhas) == 1 and consumer.commits == 3
    assert contados == ["registrado", "repetido", "repetido"]


async def test_falha_ao_gravar_nao_commita(contados: list[str]) -> None:
    rco = InboxRcoEmMemoria(falhar=RuntimeError("banco fora"))
    consumer = FakeBridgeKafkaConsumer([_mensagem()])
    with pytest.raises(RuntimeError):
        await run_inbox_loop(consumer, _SpyInbox(), avisos_rco=rco, grupos_do_pin=grupos_do_recorte)
    assert consumer.commits == 0 and contados == []


async def test_pin_ilegivel_nao_commita_nem_vai_a_fila_morta(contados: list[str]) -> None:
    def pin_fora() -> Mapping[str, str]:
        raise OSError("pin ilegivel")

    pub = _Pub()
    dlq = BridgeDlqShunt(publisher=pub, audit_sink=FakeStartAuditSink(), tenant_id="amh")  # type: ignore[arg-type]
    consumer = FakeBridgeKafkaConsumer([_mensagem()])
    with pytest.raises(OSError):
        await run_inbox_loop(
            consumer, _SpyInbox(), dlq=dlq, avisos_rco=InboxRcoEmMemoria(), grupos_do_pin=pin_fora
        )
    assert consumer.commits == 0 and pub.sent == []
    assert contados == ["pin_indisponivel"]


async def test_fila_sem_grupo_vai_a_fila_morta_com_fato_duravel_e_confirma(contados: list[str]) -> None:
    rco = InboxRcoEmMemoria()
    pub = _Pub()
    auditoria = FakeStartAuditSink()
    dlq = BridgeDlqShunt(publisher=pub, audit_sink=auditoria, tenant_id="amh")  # type: ignore[arg-type]
    consumer = FakeBridgeKafkaConsumer([_msg(_mensagem("cadastro"))])
    await run_inbox_loop(
        consumer,
        _SpyInbox(),
        dlq=dlq,
        avisos_rco=rco,
        grupos_do_pin=lambda: {"auditoria_medica": "medico-auditor"},
    )
    assert rco.linhas == {}  # nenhuma linha em grupo nenhum
    assert dlq.shunted == [("operadora.notifications.internal.dlq", REASON_RCO_FILA_SEM_GRUPO)]
    assert pub.sent[0][1]["maezo_dlq_reason"] == REASON_RCO_FILA_SEM_GRUPO.encode()
    assert consumer.commits == 1
    assert contados == ["fila_sem_grupo"]


async def test_fila_sem_grupo_sem_dlq_nao_commita(contados: list[str]) -> None:
    consumer = FakeBridgeKafkaConsumer([_mensagem("cadastro")])
    with pytest.raises(MalformedRcoAvisoError):
        await run_inbox_loop(consumer, _SpyInbox(), avisos_rco=InboxRcoEmMemoria(), grupos_do_pin=lambda: {})
    assert consumer.commits == 0


async def test_mensagem_invalida_vai_a_fila_morta(contados: list[str]) -> None:
    dlq = BridgeDlqShunt(publisher=_Pub(), audit_sink=FakeStartAuditSink(), tenant_id="amh")  # type: ignore[arg-type]
    consumer = FakeBridgeKafkaConsumer([_msg(_mensagem(extra="texto"))])
    await run_inbox_loop(consumer, _SpyInbox(), dlq=dlq, avisos_rco=InboxRcoEmMemoria(), grupos_do_pin=dict)
    assert dlq.shunted == [("operadora.notifications.internal.dlq", REASON_RCO_AVISO_INVALIDO)]
    assert contados == ["invalido"]


async def test_sem_inbox_ligado_o_tipo_segue_ignorado_e_e_contado(contados: list[str]) -> None:
    spy = _SpyInbox()
    consumer = FakeBridgeKafkaConsumer([_mensagem(), _notification()])
    await run_inbox_loop(consumer, spy)
    assert len(spy.recorded) == 1 and consumer.commits == 2
    assert contados == ["nao_configurado"]


async def test_aviso_de_equipe_continua_igual_com_o_inbox_rco_ligado(contados: list[str]) -> None:
    spy = _SpyInbox()
    rco = InboxRcoEmMemoria()
    receipt = await handle_inbox_message(
        spy, _notification(), avisos_rco=rco, grupos_do_pin=grupos_do_recorte
    )
    assert receipt is not None and len(spy.recorded) == 1 and rco.linhas == {} and contados == []


async def test_inbox_rco_que_nao_sabe_registrar_e_recusado_antes_da_primeira_mensagem() -> None:
    class _Nao:
        pass

    consumer = FakeBridgeKafkaConsumer([_mensagem()])
    with pytest.raises(TypeError, match="InboxAvisosRco"):
        await run_inbox_loop(consumer, _SpyInbox(), avisos_rco=_Nao())  # type: ignore[arg-type]
    assert consumer.commits == 0
    assert isinstance(InboxRcoEmMemoria(), InboxAvisosRco)


async def test_o_grupo_vem_do_pin_de_bordo_por_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from maezo.tools.workers import rco_pin

    monkeypatch.setattr(
        rco_pin, "rco_rules_pin", lambda: SimpleNamespace(grupos_principais={"auditoria_medica": "g-pin"})
    )
    rco = InboxRcoEmMemoria()
    await handle_inbox_message(_SpyInbox(), _mensagem(), avisos_rco=rco)
    assert [a.grupo for a in rco.linhas.values()] == ["g-pin"]


# --- vocabulario fechado ----------------------------------------------------------------------------


def test_paridade_dos_desfechos_e_codigos() -> None:
    assert RCO_AVISO_CONSUMO == observability.RCO_AVISO_CONSUMO_DESFECHOS
    assert {REASON_RCO_AVISO_INVALIDO, REASON_RCO_FILA_SEM_GRUPO} <= BRIDGE_DLQ_REASONS
    assert inbox_mod.RCO_AVISO_TYPE == rco_avisos.TIPO_NOTIFICACAO


def test_metrica_com_rotulo_fechado() -> None:
    from maezo.platform.observability import _get_metrics_collector, record_rco_aviso_consumo

    contador = _get_metrics_collector().rco_aviso_consumo_total
    antes = contador.labels(desfecho="outro")._value.get()
    record_rco_aviso_consumo("medico-auditor")  # fora do vocabulario: nunca vira rotulo
    record_rco_aviso_consumo("registrado")
    assert contador.labels(desfecho="outro")._value.get() == antes + 1
    rotulos = {s.labels.get("desfecho") for m in contador.collect() for s in m.samples}
    assert rotulos <= RCO_AVISO_CONSUMO | {"outro", None}


# --- o destino Postgres (`rco_aviso_grupo`, 0037) e a composicao do daemon ------------------------------


class _ConnFalsa:
    """Uma conexao asyncpg falsa: guarda as linhas por (tenant, notice_ref) como o PK da 0037."""

    def __init__(self, linhas: dict[tuple[str, str], dict[str, Any]], *, sumir: bool = False) -> None:
        self.linhas = linhas
        self.sumir = sumir
        self.sql: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self) -> Any:
        conn = self

        class _Tx:
            async def __aenter__(self) -> _ConnFalsa:
                return conn

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Tx()

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.sql.append((sql, args))
        if sql.startswith("INSERT"):
            chave = (args[0], args[1])
            if chave in self.linhas:
                return None
            self.linhas[chave] = {"args": args, "registrado_em": _AGORA}
            return {"registrado_em": _AGORA}
        if self.sumir:
            return None
        linha = self.linhas.get((args[0], args[1]))
        return None if linha is None else {"registrado_em": linha["registrado_em"]}


class _PoolFalso:
    def __init__(self, conn: _ConnFalsa) -> None:
        self.conn = conn

    def acquire(self) -> Any:
        conn = self.conn

        class _Acq:
            async def __aenter__(self) -> _ConnFalsa:
                return conn

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Acq()


def _aviso(destino: str = "auditoria_medica") -> AvisoRcoGrupo:
    linha = next(x for x in _linhas() if x.fila == destino)
    return resolver_grupo(linha, grupos_do_recorte())


async def test_o_destino_postgres_grava_uma_linha_e_a_reentrega_e_duplicate() -> None:
    conn = _ConnFalsa({})
    sink = inbox_mod.PostgresInboxAvisosRco(
        dsn="postgresql://x@h/db",
        tenant="amh",
        pool=_PoolFalso(conn),  # type: ignore[arg-type]
    )
    assert isinstance(sink, InboxAvisosRco)
    assert not hasattr(sink, "record")  # nao e' um inbox de escalonamento
    aviso = _aviso()
    primeiro = await sink.registrar(aviso)
    assert (primeiro.outcome, primeiro.notice_ref, primeiro.recorded_at) == (
        NoticeRecordOutcome.RECORDED,
        aviso.notice_ref,
        _AGORA,
    )
    segundo = await sink.registrar(aviso)
    assert (segundo.outcome, segundo.recorded_at) == (NoticeRecordOutcome.DUPLICATE, _AGORA)
    assert len(conn.linhas) == 1
    sql, args = conn.sql[0]
    assert "INSERT INTO rco_aviso_grupo" in sql and "ON CONFLICT (tenant, notice_ref) DO NOTHING" in sql
    linha = aviso.linha
    assert args == (
        "amh",
        aviso.notice_ref,
        linha.chave_idempotencia,
        linha.task_id,
        linha.composicao,
        linha.sinal,
        linha.fila,
        linha.prazo_parametro,
        "medico-auditor",
        "staff",
    )


async def test_o_destino_postgres_recusa_outro_tenant_e_releitura_vazia() -> None:
    conn = _ConnFalsa({})
    sink = inbox_mod.PostgresInboxAvisosRco(
        dsn="postgresql://x@h/db",
        tenant="outro",
        pool=_PoolFalso(conn),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="wrong tenant schema"):
        await sink.registrar(_aviso())
    assert conn.sql == []

    sumiu = _ConnFalsa({}, sumir=True)
    sink2 = inbox_mod.PostgresInboxAvisosRco(
        dsn="postgresql://x@h/db",
        tenant="amh",
        pool=_PoolFalso(sumiu),  # type: ignore[arg-type]
    )
    await sink2.registrar(_aviso())
    with pytest.raises(RuntimeError, match="could not be read back"):
        await sink2.registrar(_aviso())


def test_o_destino_postgres_recusa_tenant_que_nao_e_schema() -> None:
    with pytest.raises(Exception, match="(?i)tenant|schema"):
        inbox_mod.PostgresInboxAvisosRco(dsn="postgresql://x@h/db", tenant='amh"; DROP SCHEMA public; --')


def test_o_destino_postgres_sem_database_url_recusa_subir() -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        inbox_mod.build_avisos_rco(inbox_mod.NotificationsInboxSettings(DATABASE_URL=None))
    sink = inbox_mod.build_avisos_rco(
        inbox_mod.NotificationsInboxSettings(DATABASE_URL="postgresql://x@h/db", TENANT_ID="amh")
    )
    assert isinstance(sink, inbox_mod.PostgresInboxAvisosRco)


async def test_o_daemon_liga_os_avisos_rco_no_laco(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main()` compoe o inbox de escalonamento E o destino dos avisos rco (mesmo DSN/tenant) e os passa ao
    laco; sem o destino ligado, `rco.aviso_auditoria` seria so `nao_configurado`."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@h/db")
    monkeypatch.setenv("TENANT_ID", "amh")
    chamadas: list[dict[str, Any]] = []

    class _Consumidor:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    async def _laco(consumer: Any, inbox: Any, **kw: Any) -> None:
        chamadas.append({"consumer": consumer, "inbox": inbox, **kw})

    monkeypatch.setattr(inbox_mod, "AioKafkaBridgeConsumer", _Consumidor)
    monkeypatch.setattr(inbox_mod, "run_inbox_loop", _laco)
    await inbox_mod.main()
    assert len(chamadas) == 1
    chamada = chamadas[0]
    assert isinstance(chamada["inbox"], inbox_mod.PostgresEscalationTeamNoticeInbox)
    rco = chamada["avisos_rco"]
    assert isinstance(rco, inbox_mod.PostgresInboxAvisosRco) and isinstance(rco, InboxAvisosRco)
    assert rco._tenant == chamada["inbox"]._tenant == "amh"
