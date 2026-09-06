"""Unit tests for SP-OP-LGPD-DSR-001 workers — TDD London School.

Tests LGPD DSR workers: validate_identity and execute_request (the single execution worker).

NOTE (R-181, gap `SP-OP-LGPD-DSR-001` / `LGPD-WORKER-DRIFT`): the three `Execute*Worker` classes
this file used to cover (`execute_export`/`execute_rectification`/`execute_erasure` — ORPHAN CODE
topics carried by no BPMN service task) were COLLAPSED into `ExecuteRequestWorker` on the modelled
topic `operadora.lgpd.execute_request`. The old file name is kept so the evidence-ledger hash path
stays stable. The full old->new assertion map, including the TWO deliberate polarity flips and
their justification, is the `MAPA DE MIGRACAO` comment above the `execute_request` section.

CRITICAL: Workers must NEVER make adverse decisions (accusation of fraud, denial).

NOTE (#55 R-E, T2.8): `assess_request` (`AssessRequestWorker`) was RETIRED — routing
(`BRT_RotearDsr`) is a native engine-side DMN decision (`lgpd_dsr_routing`), never an external
task; the worker was unreachable by construction. Its tests were removed with it (see
`docs/compliance/lgpd-topic-reconciliation.md` R-E). `send_response`/`notify_sla_risk` (#55 R-F/
R-G raw handlers) are covered in `test_lgpd_send_response.py`/`test_lgpd_notify_sla_risk.py`.

NOTE (R-H, gap `LGPD-PUBLISH-COMPLETED-ORPHAN-TOPIC`): `publish_completed`
(`PublishCompletedWorker`) was RETIRED for the same reason, and THREE tests went with it —
`test_publish_completed_topic`, `test_publish_completed_publishes_event` and
`test_publish_completed_includes_desfecho`. They were not neutral coverage, but only ONE of the
three asserted the fabricated fact: `test_publish_completed_publishes_event` asserted
`status == "published"` / `event == "agents.events.lgpd_dsr.completed"` out of a SYNCHRONOUS
`WorkerBase.execute` with no publisher seam at all. The other two pinned the worker's topic
(`test_publish_completed_topic`) and its `desfecho` echo (`test_publish_completed_includes_desfecho`)
— a real reason to delete them WITH the worker, but not the same false-fact claim. Under the
REMOVAL framing actually applied (the class deleted, the import gone), all three broke — the two
survivors named a class that no longer exists. Under a return-`{}` framing (had the worker been
kept but rewritten to no-op instead of removed), only `test_publish_completed_publishes_event` and
`test_publish_completed_includes_desfecho` would have broken (`KeyError` on `"status"`/`"desfecho"`
from an empty dict); `test_publish_completed_topic` would still PASS, because `topic` is set in
`WorkerBase.__init__`, independent of what `execute` returns. The DSR's completion
is published by the BPMN's shared generic publisher (`ST_PublishCompleted` ->
`operadora.events.publish`, `bpmn:286-297`); that path is exercised by
`tests/unit/tools/workers/test_events.py`, not here. Deleting the tests WITH the worker is the
point — keeping them green against a rewritten worker would have preserved the fabrication.
"""

from __future__ import annotations

import inspect
import pathlib
import re
import xml.etree.ElementTree as ET

import pytest

from maezo.tools.workers import lgpd as lgpd_module
from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN, ERR_FRAUD_ACCUSATION_NOT_HUMAN
from maezo.tools.workers.harness import (
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerBpmnError,
    WorkerFailureError,
    WorkerHarness,
)
from maezo.tools.workers.lgpd import (
    ExecuteRequestWorker,
    ValidateIdentityWorker,
    register_lgpd_workers,
)

# T3.1 (mirrors this file's own ADR-0031 `identidade_verificada` fail-closed matrix above):
# `human_approved` is now pinned to the explicit boolean `True` — NOT bare truthiness — in
# `ExecuteRequestWorker` (R-181; era execute_export/rectification/erasure). Absent / False / None / junk
# (string incl. whitespace-only, int, list, dict) must NEVER be read as human approval. Shared
# vectors reused across each worker's own parametrized fail-closed test below.
_HUMAN_APPROVED_NON_TRUE_VECTORS: list[dict[str, object]] = [
    {},  # human_approved absent
    {"human_approved": False},  # explicit False
    {"human_approved": None},  # explicit None
    {"human_approved": "true"},  # garbage: truthy string, not the bool True
    {"human_approved": " "},  # garbage: whitespace-only truthy string
    {"human_approved": 1},  # garbage: truthy int, not the bool True
    {"human_approved": [1]},  # garbage: truthy list, not the bool True
    {"human_approved": {"ok": True}},  # garbage: truthy dict, not the bool True
]

# ---------------------------------------------------------------------------
# validate_identity
# ---------------------------------------------------------------------------


def test_validate_identity_topic() -> None:
    """validate_identity worker must have topic 'operadora.lgpd.verify_identity'."""
    worker = ValidateIdentityWorker()
    assert worker.topic == "operadora.lgpd.verify_identity"


def test_validate_identity_fail_closed_requires_explicit_verified_signal() -> None:
    """FAIL-CLOSED (T2.8): identity is confirmed ONLY on an explicit `identidade_verificada is
    True`. Pseudo_id present but no verified signal -> NOT confirmed (routes to the challenge)."""
    worker = ValidateIdentityWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc123",
            "canal": "portal",
            "identidade_verificada": True,
        }
    )

    assert result["identidade_confirmada"] is True
    assert result["status"] == "verified"


@pytest.mark.parametrize(
    "vars_extra",
    [
        {},  # identidade_verificada absent
        {"identidade_verificada": False},  # explicit False
        {"identidade_verificada": "true"},  # garbage (truthy string, not the bool True)
        {"identidade_verificada": 1},  # garbage (truthy int, not the bool True)
    ],
)
def test_validate_identity_fail_closed_rejects_non_true_signal(vars_extra: dict[str, object]) -> None:
    """The mere presence of the (obligatory) titular_pseudo_id NEVER confirms identity, and only
    the bool `True` confirms — absent/False/garbage -> NOT confirmed (fail-closed, anti eng.
    social). This is the fail-OPEN defect this change closes."""
    worker = ValidateIdentityWorker()

    result = worker.run(
        {"tenant_id": "amh", "titular_pseudo_id": "pseudo-abc123", "canal": "portal", **vars_extra}
    )

    assert result["identidade_confirmada"] is False
    assert result["status"] == "pending_proof"


def test_validate_identity_rejects_empty_pseudo_id() -> None:
    """validate_identity RAISES a modeled BPMN error when pseudo_id is empty/missing.

    Per GAP-LGPD-6: absent/empty titular_pseudo_id RAISES WorkerBpmnError(ERR_DSR_IDENTITY_
    UNVERIFIED) so the BE_IdentidadeInverificavel boundary can fire -> End_IdentidadeInverificavel.
    This is a TECHNICAL guard (impossibilidade mecanica), NEVER an accusation.
    """
    worker = ValidateIdentityWorker()

    with pytest.raises(WorkerBpmnError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "",
                "canal": "whatsapp",
            }
        )

    assert exc_info.value.error_code == "ERR_DSR_IDENTITY_UNVERIFIED"


def test_validate_identity_never_accuses_fraud() -> None:
    """validate_identity must NEVER automatically accuse fraud.

    L0 hard: fraud_accusation is intocavel. Even with missing data, the worker only reports
    inability to verify MECHANICALLY (a technical BPMN error), never a fraud accusation.
    """
    worker = ValidateIdentityWorker()

    with pytest.raises(WorkerBpmnError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": None,  # missing
            }
        )

    exc = exc_info.value
    # Must be a technical, fail-safe outcome — never fraud-accusation language/code.
    assert exc.error_code == "ERR_DSR_IDENTITY_UNVERIFIED"
    assert exc.error_code != ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "fraud" not in str(exc).lower()
    assert "acusacao" not in str(exc).lower()


# ---------------------------------------------------------------------------
# execute_request (R-181) — o UNICO worker de execucao, no topico modelado
# ---------------------------------------------------------------------------
#
# MAPA DE MIGRACAO O2-O4 -> execute_request (R-181). Nenhuma asercao comportamental foi perdida;
# duas mudaram de POLARIDADE de proposito e estao marcadas FLIP com a justificativa:
#
#   [antigo]                                              -> [novo]
#   test_execute_erasure_topic                            -> test_execute_request_topic_e_o_do_bpmn
#   test_execute_export_topic                             -> idem
#   test_execute_rectification_topic                      -> idem
#   test_execute_erasure_blocks_without_human_approval    -> test_execute_request_recusa_sem_aprovacao_humana
#   test_execute_export_guard_blocks_without_human        -> idem (parametrizado por tipo_requisicao)
#   test_execute_rectification_guard_blocks_without_human -> idem
#   test_execute_{erasure,export,rectification}_fail_closed_rejects_non_true_human_approved
#                                                         -> test_execute_request_fail_closed_rejeita_
#                                                            human_approved_nao_true (os MESMOS 8 vetores)
#   test_execute_erasure_approved_fails_closed_not_success
#                                                         -> test_execute_request_eliminacao_aprovada_
#                                                            falha_fechada_nao_sucesso
#   test_execute_erasure_never_erases_without_decision    -> test_execute_request_nunca_executa_sem_decisao
#   test_execute_erasure_respects_negativa_fundamentada   -> test_execute_request_respeita_negativa_
#                                                            fundamentada
#   test_execute_export_compiles_data             -> FLIP -> test_execute_request_exportacao_nao_
#                                                            fabrica_pacote
#   test_execute_rectification_applies_correction -> FLIP -> test_execute_request_retificacao_nao_
#                                                            afirma_correcao
#
# AS DUAS INVERSOES (as unicas): os dois testes antigos PINAVAM UM SUCESSO FABRICADO —
# `status="export_compiled"` com `package_ref` cunhado por `uuid4()`, e
# `status="rectification_completed"` — devolvidos por um `WorkerBase.execute` SINCRONO sem seam de
# exportacao nem de retificacao, sem executar UMA LINHA de SQL. Enquanto os topicos eram ORFAOS
# (nenhuma service task os carregava) a fabricacao era LATENTE; no topico MODELADO ela ficaria
# VIVA, e `Flow_Executar_Enviar` nao tem `conditionExpression`, entao o `complete` avancaria o
# token direto para "Enviar resposta ao titular" -> `event_desfecho=atendida` ->
# `End_RequisicaoConcluida`. Manter esses dois testes verdes contra o worker colapsado seria
# PRESERVAR a fabricacao no exato momento em que ela passa a alcancar o titular — a mesma licao
# que a aposentadoria de `PublishCompletedWorker` (R-H) registra no cabecalho deste modulo. O
# irmao `ExecuteErasureWorker` ja recusava (T3.4-F3); a inversao alinha os outros dois direitos a
# essa postura auditada. O INVARIANTE que os dois testes protegiam de verdade — "o caminho
# aprovado do direito X e exercitado e nao passa em branco" — continua provado, agora pela
# recusa tipada por direito.


class _RecordingLogger:
    """Logger de gravacao: captura TODA chamada de log do worker para a varredura de PHI.

    `WorkerBase.__init__` guarda o structlog em `self.logger` (atributo de INSTANCIA), entao
    troca-lo por este duble e suficiente e nao mexe em estado global.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def debug(self, event: str, **kwargs: object) -> None:
        self.calls.append(("debug", event, kwargs))

    def info(self, event: str, **kwargs: object) -> None:
        self.calls.append(("info", event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:
        self.calls.append(("warning", event, kwargs))

    def error(self, event: str, **kwargs: object) -> None:
        self.calls.append(("error", event, kwargs))


def _execute_request_worker() -> ExecuteRequestWorker:
    return ExecuteRequestWorker()


#: Os SEIS valores de `tipo_requisicao` que o modelo declara — lidos do dominio do proprio
#: worker, nao redigitados aqui, para que um valor novo no modelo nao passe despercebido.
_TIPOS_DECLARADOS: tuple[str, ...] = tuple(sorted(lgpd_module._TIPO_REQUISICAO_PARA_DIREITO))

#: Os tres tipos cujo `fluxo` teria execucao a fazer (EXPORTACAO / RETIFICACAO / ELIMINACAO).
_TIPOS_EXECUTAVEIS: tuple[str, ...] = tuple(
    sorted(
        tipo
        for tipo, direito in lgpd_module._TIPO_REQUISICAO_PARA_DIREITO.items()
        if direito != lgpd_module._DIREITO_INFORMATIVO
    )
)


def test_execute_request_topic_e_o_do_bpmn() -> None:
    """R-181: o worker unico vive no topico que `ST_ExecutarRequisicao` declara — nao num inventado.

    O nome NAO e redigitado a partir da memoria: e lido do proprio BPMN.
    """
    assert _execute_request_worker().topic == "operadora.lgpd.execute_request"
    assert "operadora.lgpd.execute_request" in _bpmn_lgpd_topics()


_BPMN_DSR = (
    pathlib.Path(__file__).resolve().parents[4]
    / "spec"
    / "processes"
    / "bpmn"
    / "SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn"
)


_CONTRATO_DSR = (
    pathlib.Path(__file__).resolve().parents[4] / "docs" / "processes" / "contracts" / "SP-OP-LGPD-DSR-001.md"
)


def _tipos_declarados_pelo_contrato() -> set[str]:
    """O dominio de `tipo_requisicao` LIDO da tabela "Variaveis de entrada" do contrato.

    Derivado do artefato, nunca redigitado: a linha e localizada pelo nome da variavel e os
    valores sao os tokens entre crases da linha, menos o proprio nome da variavel. (Nao se divide
    a linha por `|`: dentro da celula os pipes vem ESCAPADOS como `\\|`, e um split ingenuo
    despedaca a celula — foi exatamente assim que a primeira versao desta cerca leu um valor so.)
    """
    for linha in _CONTRATO_DSR.read_text(encoding="utf-8").splitlines():
        if linha.startswith("| `tipo_requisicao` |"):
            return set(re.findall(r"`([a-z_]+)`", linha)) - {"tipo_requisicao"}
    raise AssertionError("linha de `tipo_requisicao` nao encontrada na tabela do contrato")


def test_o_dominio_de_dispatch_e_o_declarado_pelo_contrato_nao_um_inventado() -> None:
    """R-181 manda despachar pela variavel de tipo que o modelo carrega — e so por ela.

    O worker unificado escolhe o caminho por `tipo_requisicao`, cujo dominio de SEIS valores e
    declarado em DOIS artefatos: a tabela "Variaveis de entrada" do contrato e a `bpmn:
    documentation` do processo ("VARIAVEIS DE ENTRADA"). Esta cerca deriva o conjunto do
    CONTRATO e exige paridade EXATA com o mapa de dispatch do worker, para que um valor novo no
    modelo (ou um valor inventado no codigo) fique vermelho em vez de cair no ramo de
    "tipo desconhecido" em silencio.
    """
    declarados = _tipos_declarados_pelo_contrato()
    assert declarados, "a cerca ficou inerte: nenhum valor extraido da celula do contrato"
    assert set(lgpd_module._TIPO_REQUISICAO_PARA_DIREITO) == declarados, (
        "o dominio de dispatch do worker divergiu do contrato: "
        f"worker={sorted(lgpd_module._TIPO_REQUISICAO_PARA_DIREITO)} contrato={sorted(declarados)}"
    )
    # O BPMN declara os MESMOS seis na sua documentation — a terceira fonte, para que a paridade
    # nao possa ser satisfeita editando so o contrato.
    doc_bpmn = _BPMN_DSR.read_text(encoding="utf-8")
    for tipo in sorted(declarados):
        assert tipo in doc_bpmn, f"`{tipo}` esta no contrato mas nao aparece no BPMN"


def test_execute_request_max_retries_1_nao_mascara_o_incidente() -> None:
    """Recusa DETERMINISTICA, nunca fault transitorio: o retry in-process do WorkerBase (que
    re-tenta TODA Exception com `time.sleep`) nao pode atrasar nem mascarar o incidente."""
    assert _execute_request_worker().max_retries == 1


@pytest.mark.parametrize("tipo_requisicao", _TIPOS_EXECUTAVEIS)
def test_execute_request_recusa_sem_aprovacao_humana(tipo_requisicao: str) -> None:
    """L0 hard: nenhuma execucao sem aprovacao humana (DPO/juridico), em direito algum.

    Substitui os tres `*_guard_blocks_without_human`. A diferenca com os antigos: em vez de
    DEVOLVER `{"status": "blocked_by_guard"}` — que no topico MODELADO completaria a tarefa e
    avancaria o token por `Flow_Executar_Enviar` (sem `conditionExpression`) ate "Enviar resposta
    ao titular" — o worker levanta um incidente que SEGURA o token.
    """
    worker = _execute_request_worker()

    with pytest.raises(WorkerFailureError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "pseudo-abc",
                "tipo_requisicao": tipo_requisicao,
                "decisao_dsr": "EXECUTAR_E_ENVIAR",
                # sem human_approved
            }
        )

    assert exc_info.value.retries_left == 0
    assert ERR_DENIAL_NOT_HUMAN in str(exc_info.value)


@pytest.mark.parametrize("vars_extra", _HUMAN_APPROVED_NON_TRUE_VECTORS)
def test_execute_request_fail_closed_rejeita_human_approved_nao_true(
    vars_extra: dict[str, object],
) -> None:
    """FAIL-CLOSED (T3.1, matriz preservada byte-a-byte dos tres testes antigos): o guard so
    aceita o literal `human_approved is True`.

    Ausente / False / None / lixo truthy (string incl. so-espaco, int, list, dict) NUNCA e lido
    como aprovacao humana.
    """
    worker = _execute_request_worker()

    with pytest.raises(WorkerFailureError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "pseudo-abc",
                "tipo_requisicao": "eliminacao",
                "decisao_dsr": "EXECUTAR_E_ENVIAR",
                **vars_extra,
            }
        )

    assert exc_info.value.retries_left == 0
    assert ERR_DENIAL_NOT_HUMAN in str(exc_info.value)


def test_execute_request_eliminacao_aprovada_falha_fechada_nao_sucesso() -> None:
    """FAIL-CLOSED (T3.4-F3, herdado de `ExecuteErasureWorker` sem perda): mesmo com AMBOS os
    guards satisfeitos (human_approved is True + decisao_dsr == EXECUTAR_E_ENVIAR), a eliminacao
    NAO reporta sucesso — a delecao real nao esta implementada, entao levanta um incidente
    NAO-RETENTADO (WorkerFailureError, retries_left=0) em vez do antigo `status="erasure_completed"`.

    CERCA DE FLIP CONSCIENTE: quem for implementar a delecao real TEM de reescrever este teste —
    ele nao pode voltar a devolver `erasure_completed` em silencio. Reportar eliminacao concluida
    sem executar SQL e violacao silenciosa do art. 18, VI (dizer ao titular que os dados sumiram
    enquanto eles seguem la).
    """
    worker = _execute_request_worker()

    with pytest.raises(WorkerFailureError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "pseudo-abc",
                "tipo_requisicao": "eliminacao",
                "decisao_dsr": "EXECUTAR_E_ENVIAR",
                "human_approved": True,
                "fundamentacao_legal": "Art. 18, LGPD",
            }
        )

    # retries_left=0 => incidente IMEDIATO no engine, nunca retry transitorio. Re-tentar uma
    # delecao nao implementada nao conquista nada; um humano precisa ser chamado.
    assert exc_info.value.retries_left == 0
    assert "NAO esta implementada" in str(exc_info.value)
    assert lgpd_module._DIREITO_ELIMINACAO in str(exc_info.value)


def test_execute_request_exportacao_nao_fabrica_pacote() -> None:
    """FLIP CONSCIENTE de `test_execute_export_compiles_data` (ver o MAPA DE MIGRACAO acima).

    O worker aposentado devolvia `status="export_compiled"` + `package_ref=uuid4()` sem compilar
    pacote algum. No topico MODELADO essa afirmacao alcancaria o titular. Agora recusa — e nenhum
    `package_ref` e cunhado em lugar nenhum do modulo.
    """
    worker = _execute_request_worker()

    with pytest.raises(WorkerFailureError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "pseudo-abc",
                "tipo_requisicao": "portabilidade",
                "decisao_dsr": "EXECUTAR_E_ENVIAR",
                "human_approved": True,
            }
        )

    assert exc_info.value.retries_left == 0
    assert lgpd_module._DIREITO_EXPORTACAO in str(exc_info.value)
    assert "package_ref" not in str(exc_info.value)
    # Cerca de CODIGO (nao de prosa): o modulo nao pode sequer IMPORTAR `uuid` — era so isso que
    # `ExecuteExportWorker` usava, para cunhar o `package_ref` do pacote que nunca compilou.
    assert not hasattr(lgpd_module, "uuid"), (
        "`uuid` voltou ao modulo lgpd — era a fonte do `package_ref` fabricado por `ExecuteExportWorker`"
    )


def test_execute_request_retificacao_nao_afirma_correcao() -> None:
    """FLIP CONSCIENTE de `test_execute_rectification_applies_correction` (ver o MAPA acima).

    O worker aposentado devolvia `status="rectification_completed"` / `data_type="rectification"`
    sem retificar nada. Agora recusa, tipado pelo direito pedido.
    """
    worker = _execute_request_worker()

    with pytest.raises(WorkerFailureError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "pseudo-abc",
                "tipo_requisicao": "correcao",
                "decisao_dsr": "EXECUTAR_E_ENVIAR",
                "human_approved": True,
            }
        )

    assert exc_info.value.retries_left == 0
    assert lgpd_module._DIREITO_RETIFICACAO in str(exc_info.value)
    assert "rectification_completed" not in str(exc_info.value)


def test_execute_request_nunca_executa_sem_decisao() -> None:
    """FAIL-CLOSED (GAP-LGPD-4): `decisao_dsr` ausente/em branco NUNCA libera execucao."""
    worker = _execute_request_worker()

    with pytest.raises(WorkerFailureError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "pseudo-abc",
                "tipo_requisicao": "eliminacao",
                # sem decisao_dsr
                "human_approved": True,
            }
        )

    assert exc_info.value.retries_left == 0
    assert "EXECUTAR_E_ENVIAR" in str(exc_info.value)


def test_execute_request_respeita_negativa_fundamentada() -> None:
    """`NEGAR_FUNDAMENTADO` NUNCA executa — a negativa fundamentada e decisao HUMANA de nao executar.

    Requisito (c) do colapso R-D em `docs/compliance/lgpd-topic-reconciliation.md`.
    """
    worker = _execute_request_worker()

    with pytest.raises(WorkerFailureError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "pseudo-abc",
                "tipo_requisicao": "eliminacao",
                "decisao_dsr": "NEGAR_FUNDAMENTADO",
                "fundamentacao_legal": "Retencao legal obrigatoria — Lei 13.787/2018",
                "human_approved": True,
            }
        )

    assert exc_info.value.retries_left == 0
    assert "NEGAR_FUNDAMENTADO" in str(exc_info.value)


def test_execute_request_tipo_desconhecido_falha_fechada() -> None:
    """Tipo fora do dominio declarado -> recusa, espelhando o catch-all fail-safe da DMN (r7)."""
    worker = _execute_request_worker()

    with pytest.raises(WorkerFailureError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "pseudo-abc",
                "tipo_requisicao": "tipo_que_o_modelo_nao_declara",
                "decisao_dsr": "EXECUTAR_E_ENVIAR",
                "human_approved": True,
            }
        )

    assert exc_info.value.retries_left == 0
    assert "fora do dominio" in str(exc_info.value)


@pytest.mark.parametrize(
    "tipo_requisicao",
    sorted(set(_TIPOS_DECLARADOS) - set(_TIPOS_EXECUTAVEIS)),
)
def test_execute_request_fluxo_informativo_recusa(tipo_requisicao: str) -> None:
    """Os tipos do `fluxo` INFORMATIVO nao tem execucao a fazer — e mesmo assim nao completam.

    A resposta informativa sai por `APROVAR_ENVIO` direto para `ST_EnviarResposta`, sem passar
    por `ST_ExecutarRequisicao`; chegar aqui com um deles ja e desvio do modelo.
    """
    worker = _execute_request_worker()

    with pytest.raises(WorkerFailureError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "pseudo-abc",
                "tipo_requisicao": tipo_requisicao,
                "decisao_dsr": "EXECUTAR_E_ENVIAR",
                "human_approved": True,
            }
        )

    assert exc_info.value.retries_left == 0
    assert "INFORMATIVO" in str(exc_info.value)


def test_execute_request_nenhuma_entrada_completa_a_tarefa() -> None:
    """A PROVA CENTRAL de R-181: `nenhuma execucao real habilitada` nao e prosa, e uma propriedade.

    Varre o produto cartesiano de TODAS as entradas que o modelo pode produzir (os seis
    `tipo_requisicao` declarados + ausente + um desconhecido) x (as tres `decisao_dsr` declaradas
    + ausente) x (os oito vetores nao-True de `human_approved` + o True explicito). NENHUMA delas
    devolve um dict: toda uma delas levanta `WorkerFailureError(retries_left=0)`.

    Enquanto esta propriedade valer, `Flow_Executar_Enviar` NUNCA e atravessado — logo nenhuma
    instancia pode alcancar `ST_EnviarResposta`/`event_desfecho=atendida` por conta de uma
    execucao que nao aconteceu.
    """
    tipos = (*_TIPOS_DECLARADOS, "", "tipo_que_o_modelo_nao_declara")
    decisoes = ("EXECUTAR_E_ENVIAR", "NEGAR_FUNDAMENTADO", "APROVAR_ENVIO", "")
    humanos: list[dict[str, object]] = [*_HUMAN_APPROVED_NON_TRUE_VECTORS, {"human_approved": True}]

    exercidos = 0
    for tipo in tipos:
        for decisao in decisoes:
            for humano in humanos:
                worker = _execute_request_worker()
                process_vars: dict[str, object] = {
                    "tenant_id": "amh",
                    "titular_pseudo_id": "pseudo-abc",
                    "tipo_requisicao": tipo,
                    "decisao_dsr": decisao,
                    **humano,
                }
                with pytest.raises(WorkerFailureError) as exc_info:
                    worker.run(process_vars)
                assert exc_info.value.retries_left == 0, (
                    f"retry transitorio em vez de incidente: tipo={tipo!r} decisao={decisao!r} "
                    f"human={humano!r}"
                )
                assert "NAO esta implementada" in str(exc_info.value), (
                    f"a recusa nao lidera com o fato invariante: tipo={tipo!r} "
                    f"decisao={decisao!r} human={humano!r}"
                )
                exercidos += 1

    assert exercidos == len(tipos) * len(decisoes) * len(humanos)


def test_execute_request_nunca_emite_texto_livre_phi() -> None:
    """Gap `LGPD-EXECUTE-ERASURE-RAW-FUNDAMENTACAO`: nem `fundamentacao_legal` nem
    `detalhes_requisicao` saem deste worker — nem em variavel de processo, nem em log, nem na
    mensagem do incidente.

    O worker aposentado devolvia `{"fundamentacao_legal": fundamentacao}` CRU e o escrevia numa
    linha structlog (`fundamentacao=fundamentacao`). Os dois nomes nao estao em
    `PHI_PROCESS_VARS` nem em `PHI_FREE_TEXT_VARS` — e `tests/unit/docs/test_dpo_drafts_citations.py`
    PINA os dois FORA dos dois conjuntos (o §2.4 do runbook do DPO depende disso), entao a
    correcao NAO pode ser lista-los. A correcao e estrutural e mais forte: o texto cru nunca e
    vinculado a um local; do worker sai no maximo a PRESENCA (`tem_fundamentacao: bool`), o mesmo
    idioma que `make_send_response_handler` ja aplica a este nome nesta mesma BPMN.
    """
    sentinela_fundamentacao = "SENTINELA-FUNDAMENTACAO-paciente-relatou-quadro-clinico-detalhado"
    sentinela_detalhes = "SENTINELA-DETALHES-quero-apagar-meu-prontuario-de-oncologia"

    for decisao in ("NEGAR_FUNDAMENTADO", "EXECUTAR_E_ENVIAR"):
        for tipo in _TIPOS_EXECUTAVEIS:
            worker = _execute_request_worker()
            recorder = _RecordingLogger()
            worker.logger = recorder  # type: ignore[assignment]

            with pytest.raises(WorkerFailureError) as exc_info:
                worker.run(
                    {
                        "tenant_id": "amh",
                        "titular_pseudo_id": "pseudo-abc",
                        "tipo_requisicao": tipo,
                        "decisao_dsr": decisao,
                        "human_approved": True,
                        "fundamentacao_legal": sentinela_fundamentacao,
                        "detalhes_requisicao": sentinela_detalhes,
                    }
                )

            mensagem = str(exc_info.value)
            for sentinela in (sentinela_fundamentacao, sentinela_detalhes):
                assert sentinela not in mensagem, (
                    f"texto livre PHI vazou na mensagem do incidente ({decisao}/{tipo})"
                )
            assert recorder.calls, "o worker nao registrou log algum — a varredura ficaria inerte"
            for _nivel, evento, kwargs in recorder.calls:
                blob = f"{evento} {kwargs!r}"
                for sentinela in (sentinela_fundamentacao, sentinela_detalhes):
                    assert sentinela not in blob, (
                        f"texto livre PHI vazou numa linha de log ({decisao}/{tipo}): {evento}"
                    )
            # A PRESENCA pode sair; o texto nao.
            presencas = [k for _n, _e, kw in recorder.calls for k in kw if k == "tem_fundamentacao"]
            if decisao == "NEGAR_FUNDAMENTADO":
                assert presencas, "a presenca booleana deveria ser observavel no ramo da negativa"


def test_execute_request_nao_le_o_texto_de_fundamentacao_em_lugar_nenhum() -> None:
    """Cerca de FONTE: o modulo so pode tocar `fundamentacao_legal` dentro de um `bool(...)`.

    Complementa a cerca de comportamento acima fechando o caminho pelo qual ela poderia voltar:
    qualquer `fundamentacao = process_vars.get("fundamentacao_legal")` (o idioma do worker
    aposentado) reintroduz o local cru que vazava.
    """
    fonte = inspect.getsource(lgpd_module)
    ocorrencias = [linha.strip() for linha in fonte.splitlines() if 'get("fundamentacao_legal")' in linha]
    assert ocorrencias, "a cerca ficou inerte: o modulo nao le mais o campo de forma alguma"
    for linha in ocorrencias:
        assert "bool(" in linha, (
            f"`fundamentacao_legal` lido fora de um `bool(...)` — o texto cru voltou a ser "
            f"vinculado a um local: {linha!r}"
        )


# ---------------------------------------------------------------------------
# Cercas de REGISTRO — aposentadorias (R-H, R-181) e drift topico<->BPMN
# ---------------------------------------------------------------------------
#
# Espelha `test_cancel.py::test_register_cancel_workers_does_not_register_orphan_topics`, a mesma
# especie de defeito (topico de codigo orfao registrado sem service task de BPMN). Sem estas
# provas, apagar um worker seria uma mudanca que nada impede de voltar; com elas, o retorno do
# `PublishCompletedWorker` (R-H) ou de qualquer dos tres `Execute*Worker` (R-181) fica VERMELHO.

#: Os topicos `operadora.lgpd.*` que `register_lgpd_workers` DEVE registrar hoje — exatamente.
#: Desde R-181 TODOS eles sao `camunda:topic` que a BPMN declara: os tres orfaos O2-O4
#: (`execute_export`/`execute_rectification`/`execute_erasure`) foram COLAPSADOS no modelado
#: `execute_request` (linha T4 de `docs/compliance/lgpd-topic-reconciliation.md`). Declara-los
#: explicitamente e o que impede esta cerca de virar uma afirmacao vaga; a cerca DERIVADA da
#: arvore (`test_nenhum_topico_lgpd_registrado_falta_no_bpmn`) e a que impede a lista de rodar.
_LGPD_TOPICOS_REGISTRADOS = frozenset(
    {
        "operadora.lgpd.verify_identity",  # T1 — ST_VerificarIdentidade
        "operadora.lgpd.request_additional_proof",  # T2 — #55 R-B, handler cru
        "operadora.lgpd.execute_request",  # T4 — R-181, ST_ExecutarRequisicao
        "operadora.lgpd.send_response",  # T5 — #55 R-F, handler cru
        "operadora.lgpd.notify_sla_risk",  # T6 — #55 R-G, handler cru
    }
)

#: Os topicos de codigo que R-181 APOSENTOU. Nenhum deles pode voltar a ser registrado.
_LGPD_TOPICOS_APOSENTADOS_R181 = frozenset(
    {
        "operadora.lgpd.execute_export",
        "operadora.lgpd.execute_rectification",
        "operadora.lgpd.execute_erasure",
    }
)


def _bpmn_lgpd_topics() -> set[str]:
    """Os `camunda:topic` `operadora.lgpd.*` que a BPMN realmente declara — DERIVADOS da arvore.

    Nada aqui e redigitado: o conjunto vem de um parse XML do proprio arquivo de spec, entao uma
    service task adicionada/removida move esta cerca sozinha.
    """
    ns = "{http://camunda.org/schema/1.0/bpmn}topic"
    root = ET.parse(_BPMN_DSR).getroot()
    return {
        topic
        for element in root.iter()
        for topic in (element.get(ns),)
        if topic is not None and topic.startswith("operadora.lgpd.")
    }


def _lgpd_harness() -> WorkerHarness:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_lgpd_workers(harness, FakeKafkaPublisher())
    return harness


def _lgpd_registered_topics() -> set[str]:
    return {t for t in _lgpd_harness().registered_topics if t.startswith("operadora.lgpd.")}


def test_register_lgpd_workers_nao_registra_o_topico_orfao_publish_completed() -> None:
    """R-H: `operadora.lgpd.publish_completed` NAO corresponde a `camunda:topic` algum do BPMN.

    `grep -rn "operadora.lgpd.publish_completed" spec/` -> 0 ocorrencias. Quem publica a conclusao
    do DSR e `ST_PublishCompleted` pelo topico generico `operadora.events.publish`.
    """
    assert "operadora.lgpd.publish_completed" not in _lgpd_harness().registered_topics


def test_o_modulo_lgpd_nao_expoe_mais_a_classe_publishcompletedworker() -> None:
    """A classe foi APAGADA, nao neutralizada: reintroduzi-la (mesmo sem registrar) e vermelho.

    Um worker sincrono sem seam de publisher que devolve `{"status": "published"}` afirma um fato
    falso — em terreno LGPD/DSR sujeito a auditoria — mesmo que o motor nunca o chame.
    """
    assert not hasattr(lgpd_module, "PublishCompletedWorker")


@pytest.mark.parametrize(
    "classe_aposentada",
    ["ExecuteExportWorker", "ExecuteRectificationWorker", "ExecuteErasureWorker"],
)
def test_o_modulo_lgpd_nao_expoe_mais_as_classes_execute_aposentadas(classe_aposentada: str) -> None:
    """R-181: as tres classes foram APAGADAS, nao neutralizadas.

    Duas delas (`ExecuteExportWorker`/`ExecuteRectificationWorker`) devolviam sucesso FABRICADO
    (`export_compiled` com `package_ref` de `uuid4()`; `rectification_completed`) de um `execute`
    sincrono sem seam algum. Reintroduzi-las, mesmo sem registrar, e vermelho aqui.
    """
    assert not hasattr(lgpd_module, classe_aposentada)


@pytest.mark.parametrize("topico_aposentado", sorted(_LGPD_TOPICOS_APOSENTADOS_R181))
def test_os_tres_topicos_execute_aposentados_nao_sao_registrados(topico_aposentado: str) -> None:
    """R-181: zero registros nos tres topicos ORFAOS que o colapso retirou."""
    assert topico_aposentado not in _lgpd_registered_topics()
    assert topico_aposentado not in _bpmn_lgpd_topics(), (
        "o topico aposentado apareceu na BPMN — o colapso R-181 precisa ser relido"
    )


def test_execute_request_tem_exatamente_um_registro() -> None:
    """R-181: UM worker no topico modelado — nem zero (a lacuna T4), nem dois (sombra silenciosa)."""
    topicos = _lgpd_harness().registered_topics
    assert topicos.count("operadora.lgpd.execute_request") == 1


def test_o_conjunto_de_topicos_lgpd_registrados_e_exatamente_o_declarado() -> None:
    """Paridade EXATA: nem topico a menos (lacuna) nem a mais (novo orfao entrando de fininho)."""
    assert _lgpd_registered_topics() == _LGPD_TOPICOS_REGISTRADOS


def test_nenhum_topico_lgpd_registrado_falta_no_bpmn() -> None:
    """Gap `LGPD-WORKER-DRIFT` FECHADO — e provado pela ARVORE, nao por uma lista redigitada.

    Antes de R-181 o registro expunha QUATRO topicos sem `serviceTask` correspondente
    (`execute_export`/`execute_rectification`/`execute_erasure`/`publish_completed`); `R-H`
    fechou o quarto e R-181 os tres primeiros. Esta cerca deriva os dois lados (topicos
    registrados por introspeccao do harness; `camunda:topic` por parse do BPMN) para que um
    registro novo em topico nao-modelado fique vermelho SOZINHO.
    """
    orfaos = _lgpd_registered_topics() - _bpmn_lgpd_topics()
    assert orfaos == set(), (
        f"topico(s) LGPD registrado(s) sem service task no BPMN: {sorted(orfaos)} — mesma especie "
        "de drift que R-H e R-181 fecharam"
    )


def test_topicos_lgpd_do_bpmn_sem_worker_sao_exatamente_compile_data_package() -> None:
    """O outro lado do drift: qual `camunda:topic` LGPD ainda nao tem worker.

    Exatamente UM — `operadora.lgpd.compile_data_package` (#55 R-C). Ele NAO e construivel por
    agente: `ST_CompilarPacote` precisa embarcar o "mapa de bases legais e obrigacoes de retencao
    aplicaveis" (`bpmn:183`), que e a matriz que o DPO + juridico ainda tem de definir
    (`docs/compliance/lgpd-topic-reconciliation.md` R-C, DPO/SME-sign-off-gated). Ele fica nesta
    cerca como LACUNA DECLARADA, nunca como surpresa.
    """
    sem_worker = _bpmn_lgpd_topics() - _lgpd_registered_topics()
    assert sem_worker == {"operadora.lgpd.compile_data_package"}, (
        f"o conjunto de topicos LGPD sem worker mudou: {sorted(sem_worker)} — se um novo topico "
        "entrou, ele precisa de worker ou de uma lacuna declarada; se compile_data_package saiu, "
        "R-C deixou de ser DPO-gated e a reconciliacao precisa ser relida"
    )
