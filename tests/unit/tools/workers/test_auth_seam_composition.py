"""COMPOSICAO dos seams de `register_auth_workers` (WP-J1-03 x WP-J1-06 x WP-J1-09).

POR QUE ESTE ARQUIVO EXISTE. Em 18/09/2026 tres WPs mexeram no MESMO bootstrap e a resolucao do
conflito foi feita por agente: J1-03 tira `RequestDocumentsWorker` da lista quando a ponte PHI
esta' instalada, J1-06 tirava `SendDenialNoticeWorker` quando o dono nativo do portal existia
(R-004 / F-032 removeu esse segundo dono — decisao do dono DEC-01, issue #1081 — e o eixo virou
`denial_written_notice`, a bandeira dark da entrega por escrito), e J1-09 converte
`notify_sla_risk` de classe para handler CRU com o seam `kafka`. Cada um foi
testado no seu proprio arquivo, SOZINHO. O que ninguem testou foi a COMBINACAO — e e' na
combinacao que uma aritmetica de indice, um `append` na ordem errada ou um seam consumido duas
vezes aparece.

A INVARIANTE que este arquivo persegue, em todas as 16 combinacoes (`denial_written_notice` x
`document_request_host_installed` x `kafka` x chamada dupla): **cada topico tem EXATAMENTE um
handler, e o handler certo**. Um topico com dois consumidores nao da' erro: os dois fazem
fetch-and-lock da mesma tarefa externa e quem ganha a corrida decide se o pedido de documento foi
ENTREGUE ou apenas registrado em log — falha que so' aparece quando o prazo P5D expira.

O QUE ESTE ARQUIVO NAO PROVA: nada sobre o COMPORTAMENTO dos workers (isso e'
`test_auth_denial_guard.py`, `test_auth_notify_sla_risk.py`, `test_auth_auto_criteria.py`), nem
sobre a corrida real de fetch-and-lock contra um engine de verdade — aqui o transporte e' falso e
o que se mede e' a TABELA de registro, que e' onde a exclusividade e' decidida.
"""

from __future__ import annotations

import itertools

import pytest

from maezo.runtime.worker_runtime.denial_notices import TOPIC as TOPICO_NEGATIVA
from maezo.runtime.worker_runtime.denial_notices import TopicOwnershipError
from maezo.tools.workers.auth import (
    _NOTIFY_SLA_RISK_TOPIC,
    NotifySlaRiskWorker,
    SendDenialNoticeWorker,
    register_auth_workers,
)
from maezo.tools.workers.base import WorkerBase
from maezo.tools.workers.bootstrap import register_all_workers
from maezo.tools.workers.harness import (
    FakeKafkaPublisher,
    FakeWorkerTransport,
    TopicSealError,
    WorkerHarness,
)

_TOPICO_DOCUMENTOS = "operadora.auth.request_documents"
_TOPICO_CRITERIOS = "operadora.auth.validate_auto_criteria"

#: Os topicos que o modulo auth serve SEMPRE, seja qual for a combinacao de seams. Escrito a mao
#: de proposito: derivar do proprio modulo faria o teste concordar com qualquer regressao.
_TOPICOS_INCONDICIONAIS = frozenset(
    {
        "operadora.auth.analyze_request",
        "operadora.auth.issue_authorization",
        "operadora.auth.convene_junta",
        _TOPICO_CRITERIOS,
        _NOTIFY_SLA_RISK_TOPIC,
        TOPICO_NEGATIVA,
    }
)


def _harness() -> WorkerHarness:
    return WorkerHarness(FakeWorkerTransport(), worker_id="composicao-de-seams")


@pytest.mark.parametrize(
    ("entrega_por_escrito", "ponte_de_documentos", "com_kafka", "duas_vezes"),
    list(itertools.product([False, True], repeat=4)),
)
def test_toda_combinacao_de_seams_deixa_um_dono_por_topico(
    entrega_por_escrito: bool, ponte_de_documentos: bool, com_kafka: bool, duas_vezes: bool
) -> None:
    """As 16 combinacoes, com a chamada dupla dentro da propria matriz.

    A IDEMPOTENCIA entra como eixo e nao como teste separado porque o defeito que ela esconde e'
    combinatorio: `RequestDocumentsWorker` volta por `insert(1, ...)` e `SendDenialNoticeWorker`
    por `append`, e uma segunda passagem sobre uma lista construida com aritmetica de indice e'
    exatamente onde a ordem de registro se desfaz.
    """
    harness = _harness()
    kafka = FakeKafkaPublisher() if com_kafka else None
    seams = {
        "denial_written_notice": entrega_por_escrito,
        "document_request_host_installed": ponte_de_documentos,
    }

    register_auth_workers(harness, kafka=kafka, **seams)
    if duas_vezes:
        register_auth_workers(harness, kafka=kafka, **seams)

    topicos = harness.registered_topics
    assert len(topicos) == len(set(topicos)), "topico duplicado na tabela de handlers"
    assert set(topicos) >= _TOPICOS_INCONDICIONAIS
    # A ponte PHI instalada RETIRA o topico do harness generico — nao o duplica.
    assert (_TOPICO_DOCUMENTOS in topicos) is not ponte_de_documentos
    esperados = len(_TOPICOS_INCONDICIONAIS) + (0 if ponte_de_documentos else 1)
    assert len(topicos) == esperados, f"esperado {esperados} topicos auth, obtido {sorted(topicos)}"


@pytest.mark.parametrize("entrega_por_escrito", [False, True])
@pytest.mark.parametrize("ponte_de_documentos", [False, True])
@pytest.mark.parametrize("com_kafka", [False, True])
@pytest.mark.parametrize("duas_vezes", [False, True])
def test_o_dono_da_negativa_e_sempre_o_worker_do_contrato(
    entrega_por_escrito: bool, ponte_de_documentos: bool, com_kafka: bool, duas_vezes: bool
) -> None:
    """`send_denial_notice` tem um dono SO' — e o teste olha QUEM, nao quantos (R-004 / F-032)."""
    harness = _harness()
    seams = {
        "denial_written_notice": entrega_por_escrito,
        "document_request_host_installed": ponte_de_documentos,
    }
    kafka = FakeKafkaPublisher() if com_kafka else None
    register_auth_workers(harness, kafka=kafka, **seams)
    if duas_vezes:
        register_auth_workers(harness, kafka=kafka, **seams)
    assert type(harness.registry.get(TOPICO_NEGATIVA)) is SendDenialNoticeWorker


@pytest.mark.parametrize("entrega_por_escrito", [False, True])
@pytest.mark.parametrize("com_kafka", [False, True])
def test_o_alerta_de_sla_nunca_volta_a_ser_um_workerbase_na_lista(
    entrega_por_escrito: bool, com_kafka: bool
) -> None:
    """WP-J1-09: `notify_sla_risk` e' handler CRU, e `NotifySlaRiskWorker` NAO esta' na lista.

    O `WorkerRegistry` so' recebe entradas de `register_worker`. Se alguem devolvesse a classe a'
    lista base "para nao quebrar o registro", o topico continuaria aparecendo em
    `registered_topics` — verde em qualquer teste de contagem — mas o alerta voltaria a ser
    publicado por um caminho SEM produtor Kafka, que e' o defeito que aquele WP fechou. A unica
    pergunta que distingue os dois mundos e' esta: o topico esta' no registry de `WorkerBase`?
    """
    harness = _harness()
    register_auth_workers(
        harness,
        kafka=FakeKafkaPublisher() if com_kafka else None,
        denial_written_notice=entrega_por_escrito,
    )
    assert _NOTIFY_SLA_RISK_TOPIC in harness.registered_topics
    assert harness.registry.get(_NOTIFY_SLA_RISK_TOPIC) is None, "SLA voltou a ser WorkerBase na lista"
    # E a classe nao esta' registrada em NENHUM outro topico: o WP a manteve viva como passo puro
    # CHAMADO pelo handler, nao como worker. `list_topics` e' a visao completa do registry.
    donos = {t: type(harness.registry.get(t)) for t in harness.registry.list_topics()}
    assert NotifySlaRiskWorker not in donos.values(), f"NotifySlaRiskWorker registrado em {donos}"
    assert donos, "registry vazio — a assercao acima seria vacua"


def test_o_seam_de_kafka_e_consumido_e_nao_descartado() -> None:
    """A nota de merge diz que "o descarte do `kafka` que a main fazia no topo SAI". Um `del
    kafka` reintroduzido por um rebase nao quebraria teste nenhum de contagem de topico — o
    handler continuaria registrado, so' que mudo. Este teste olha o handler CONSTRUIDO: com e sem
    produtor ele tem de ser um objeto DIFERENTE, porque o produtor e' fechado dentro dele."""
    com = _harness()
    sem = _harness()
    register_auth_workers(com, kafka=FakeKafkaPublisher())
    register_auth_workers(sem, kafka=None)
    handler_com = com._handlers[_NOTIFY_SLA_RISK_TOPIC]
    handler_sem = sem._handlers[_NOTIFY_SLA_RISK_TOPIC]
    assert handler_com is not handler_sem
    fechados = {c.cell_contents for c in (handler_com.__closure__ or ()) if not callable(c.cell_contents)}
    assert any(isinstance(v, FakeKafkaPublisher) for v in fechados), "o produtor nao chegou ao handler"


# =============================================================================================
# SEGUNDO DONO e SELO — o topico da negativa nunca fica com dois donos nem sem dono
# =============================================================================================


def test_o_seam_do_segundo_dono_e_recusado_antes_de_registrar_qualquer_coisa() -> None:
    """R-004 / F-032: o seam `denial_notice_host` nao instala mais nada — e e' RECUSADO, nao ignorado.

    Recusar ANTES do laco e' o ponto: um harness que chegou a registrar metade do modulo para so'
    entao recusar ficaria com o topico da negativa sem dono.
    """
    harness = _harness()
    with pytest.raises(TopicOwnershipError):
        register_auth_workers(harness, denial_notice_host=object())
    assert not harness.registered_topics


class _DonoEstranho(WorkerBase):
    """Um dono qualquer, fora do contrato, selado no topico da negativa."""

    def __init__(self) -> None:
        super().__init__(topic=TOPICO_NEGATIVA, max_retries=1)

    def execute(self, process_vars):  # type: ignore[no-untyped-def]
        return {}


def _selado_por_estranho() -> WorkerHarness:
    harness = _harness()
    harness.register_worker(_DonoEstranho())
    harness.seal_topic(TOPICO_NEGATIVA, _DonoEstranho)
    return harness


def test_a_recusa_do_selo_nao_deveria_deixar_o_modulo_auth_pela_metade() -> None:
    harness = _selado_por_estranho()

    with pytest.raises(TopicSealError):
        register_auth_workers(harness)

    servidos = set(harness.registered_topics)
    assert _TOPICO_CRITERIOS in servidos, "autorizacao inteira travada: criterio automatico sem worker"
    assert _NOTIFY_SLA_RISK_TOPIC in servidos, "alerta de risco de SLA sem handler"


def test_a_recusa_do_selo_nao_deveria_derrubar_os_outros_dezesseis_modulos() -> None:
    harness = _selado_por_estranho()

    with pytest.raises(TopicSealError):
        register_all_workers(harness)

    dominios = {t.split(".")[1] for t in harness.registered_topics if t.count(".") >= 2}
    assert len(dominios) >= 15, f"apenas {len(dominios)} dominios servidos: {sorted(dominios)}"
