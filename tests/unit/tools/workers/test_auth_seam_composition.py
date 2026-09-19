"""COMPOSICAO dos seams de `register_auth_workers` (WP-J1-03 x WP-J1-06 x WP-J1-09).

POR QUE ESTE ARQUIVO EXISTE. Em 18/09/2026 tres WPs mexeram no MESMO bootstrap e a resolucao do
conflito foi feita por agente: J1-03 tira `RequestDocumentsWorker` da lista quando a ponte PHI
esta' instalada, J1-06 tira `SendDenialNoticeWorker` quando o dono nativo do portal existe, e
J1-09 converte `notify_sla_risk` de classe para handler CRU com o seam `kafka`. Cada um foi
testado no seu proprio arquivo, SOZINHO. O que ninguem testou foi a COMBINACAO — e e' na
combinacao que uma aritmetica de indice, um `append` na ordem errada ou um seam consumido duas
vezes aparece.

A INVARIANTE que este arquivo persegue, em todas as 16 combinacoes (`denial_notice_host` x
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
from maezo.runtime.worker_runtime.denial_notices import (
    NativeDenialNoticeWorker,
    TopicOwnershipError,
    install_denial_notice_host,
)
from maezo.tools.workers.auth import (
    _NOTIFY_SLA_RISK_TOPIC,
    NotifySlaRiskWorker,
    SendDenialNoticeWorker,
    register_auth_workers,
)
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
    ("com_dono_de_negativa", "ponte_de_documentos", "com_kafka", "duas_vezes"),
    list(itertools.product([False, True], repeat=4)),
)
def test_toda_combinacao_de_seams_deixa_um_dono_por_topico(
    com_dono_de_negativa: bool, ponte_de_documentos: bool, com_kafka: bool, duas_vezes: bool
) -> None:
    """As 16 combinacoes, com a chamada dupla dentro da propria matriz.

    A IDEMPOTENCIA entra como eixo e nao como teste separado porque o defeito que ela esconde e'
    combinatorio: `RequestDocumentsWorker` volta por `insert(1, ...)` e `SendDenialNoticeWorker`
    por `append`, e uma segunda passagem sobre uma lista construida com aritmetica de indice e'
    exatamente onde a ordem de registro se desfaz.
    """
    harness = _harness()
    host = install_denial_notice_host() if com_dono_de_negativa else None
    kafka = FakeKafkaPublisher() if com_kafka else None
    seams = {"denial_notice_host": host, "document_request_host_installed": ponte_de_documentos}

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


@pytest.mark.parametrize("ponte_de_documentos", [False, True])
@pytest.mark.parametrize("com_kafka", [False, True])
@pytest.mark.parametrize("duas_vezes", [False, True])
def test_o_dono_da_negativa_e_o_nativo_quando_o_seam_existe_e_o_generico_quando_nao(
    ponte_de_documentos: bool, com_kafka: bool, duas_vezes: bool
) -> None:
    """`send_denial_notice` tem um dono SO' — e o teste olha QUEM, nao quantos.

    Contar handlers nao bastaria: o registro do generico DEPOIS do nativo tambem daria contagem 1
    (o `WorkerRegistry` substitui com um simples warning), e o beneficiario receberia a negativa
    redigida a partir das variaveis de processo em vez da fundamentacao humana em custodia PHI —
    silenciosamente.
    """
    for com_dono, esperado in ((True, NativeDenialNoticeWorker), (False, SendDenialNoticeWorker)):
        harness = _harness()
        seams = {
            "denial_notice_host": install_denial_notice_host() if com_dono else None,
            "document_request_host_installed": ponte_de_documentos,
        }
        kafka = FakeKafkaPublisher() if com_kafka else None
        register_auth_workers(harness, kafka=kafka, **seams)
        if duas_vezes:
            register_auth_workers(harness, kafka=kafka, **seams)
        assert type(harness.registry.get(TOPICO_NEGATIVA)) is esperado


@pytest.mark.parametrize("com_dono_de_negativa", [False, True])
@pytest.mark.parametrize("com_kafka", [False, True])
def test_o_alerta_de_sla_nunca_volta_a_ser_um_workerbase_na_lista(
    com_dono_de_negativa: bool, com_kafka: bool
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
        denial_notice_host=install_denial_notice_host() if com_dono_de_negativa else None,
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
# SEQUENCIAS — o seam MUDANDO entre duas chamadas no mesmo harness
# =============================================================================================


def test_ligar_o_dono_nativo_depois_do_generico_e_recusado_em_vez_de_substituir() -> None:
    """Sequencia generico -> nativo (o env var ligado num processo que ja' registrou).

    `assert_exclusive` e' re-executado DEPOIS do laco justamente para enxergar o que foi
    registrado nesta mesma passagem. Substituir em silencio seria pior que recusar: o dono nativo
    assumiria um topico que o generico ja' tinha travado, e o guard do perdedor nunca correria.
    """
    harness = _harness()
    register_auth_workers(harness)
    with pytest.raises(TopicOwnershipError):
        register_auth_workers(harness, denial_notice_host=install_denial_notice_host())
    # A recusa acontece DEPOIS do laco e do handler cru: nenhum topico auth ficou de fora.
    assert set(harness.registered_topics) >= _TOPICOS_INCONDICIONAIS


def test_o_selo_impede_que_o_generico_retome_o_topico_do_dono_nativo() -> None:
    """Sequencia nativo -> generico. O SELO (`seal_topic`, V14 MINOR-5) tem de segurar.

    Esta e' a garantia central do WP-J1-06: sem o selo, um `register_all_workers(harness)` sem o
    seam devolvia o topico ao worker generico com um mero warning de substituicao.
    """
    harness = _harness()
    register_auth_workers(harness, denial_notice_host=install_denial_notice_host())
    with pytest.raises(TopicSealError):
        register_auth_workers(harness)
    assert type(harness.registry.get(TOPICO_NEGATIVA)) is NativeDenialNoticeWorker


def test_reinstalar_o_mesmo_dono_nativo_continua_idempotente() -> None:
    """Contraprova do selo: uma instancia NOVA do mesmo host (o que um bring-up repetido produz)
    nao pode ser confundida com um invasor. Sem este teste, "selar" poderia significar
    "congelar", e a idempotencia documentada de `register_all_workers` morreria com ela."""
    harness = _harness()
    register_auth_workers(harness, denial_notice_host=install_denial_notice_host())
    register_auth_workers(harness, denial_notice_host=install_denial_notice_host())
    assert type(harness.registry.get(TOPICO_NEGATIVA)) is NativeDenialNoticeWorker


def test_a_recusa_do_selo_nao_deveria_deixar_o_modulo_auth_pela_metade() -> None:
    harness = _harness()
    host = install_denial_notice_host(generic_topics=harness.registered_topics)
    harness.register_worker(host.worker())
    harness.seal_topic(TOPICO_NEGATIVA, type(host.worker()))

    with pytest.raises(TopicSealError):
        register_auth_workers(harness)

    servidos = set(harness.registered_topics)
    assert _TOPICO_CRITERIOS in servidos, "autorizacao inteira travada: criterio automatico sem worker"
    assert _NOTIFY_SLA_RISK_TOPIC in servidos, "alerta de risco de SLA sem handler"


def test_a_recusa_do_selo_nao_deveria_derrubar_os_outros_dezesseis_modulos() -> None:
    harness = _harness()
    host = install_denial_notice_host(generic_topics=harness.registered_topics)
    harness.register_worker(host.worker())
    harness.seal_topic(TOPICO_NEGATIVA, type(host.worker()))

    with pytest.raises(TopicSealError):
        register_all_workers(harness)

    dominios = {t.split(".")[1] for t in harness.registered_topics if t.count(".") >= 2}
    assert len(dominios) >= 15, f"apenas {len(dominios)} dominios servidos: {sorted(dominios)}"
