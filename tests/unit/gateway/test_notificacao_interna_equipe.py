"""DL-0150 — a classe `notificacao_interna_equipe`, declarada em SOMBRA e NÃO aprovada.

O que este PR muda, e só isso: os dois tópicos de aviso interno do SP-OP-ESCALATION-001
(`operadora.escalation.notify_team` e `operadora.escalation.notify_supervisor`) deixam de cair em
`ACAO_NAO_MAPEADA` na telemetria do `ActionExecutionGateway` e passam a nomear a classe nova. Nada é
liberado: o manifesto segue `status: DRAFT`, `modo: shadow`, a classe segue `enforcement: shadow` e
os três blocos de aprovação seguem `PENDENTE`.

Provas, todas contra o manifesto REAL (`spec/policies/autonomy/action-approvals.yaml`):

  * os dois tópicos resolvem para a classe nova, continuam WOULD_DENY e `enforced=False`;
  * o motivo é `MANIFESTO_NAO_RATIFICADO` no registro real e, numa cópia forjada só com o `status`
    trocado, `APROVACAO_PENDENTE` — declarar não é aprovar;
  * `operadora.events.publish` segue SEM classe (`ACAO_NAO_MAPEADA`): fica para uma classe própria;
  * as classes de efeito adverso (negativa, fraude, vínculo contratual, autorização) continuam negando
    mesmo numa cópia em que a classe nova foi forjada como totalmente aprovada;
  * DENTES: tirar as duas linhas do `mapeamento_topicos` devolve os tópicos a `ACAO_NAO_MAPEADA`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.gateway import action_execution, effect_classes
from maezo.gateway.action_execution import (
    MODE_ENFORCING,
    MODE_SHADOW,
    REASON_ACTION_UNMAPPED,
    REASON_APPROVAL_PENDING,
    REASON_APPROVED,
    REASON_MANIFEST_DRAFT,
    STATUS_RATIFIED,
    TOPIC_MAP_KEY,
    ActionExecutionGateway,
    evaluate_worker_task,
    load_action_approvals,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHIPPED = _REPO_ROOT / "spec" / "policies" / "autonomy" / "action-approvals.yaml"

_CLASSE = "notificacao_interna_equipe"
_TOPICOS = ("operadora.escalation.notify_team", "operadora.escalation.notify_supervisor")
_EVENTS_PUBLISH = "operadora.events.publish"

#: Classes de efeito adverso/hard (negativa, acusação de fraude, rescisão) e a emissão de autorização,
#: com um tópico real de cada uma. Nenhuma pode ser arrastada pela classe nova.
_ADVERSAS = {
    "negativa_notificacao": "operadora.auth.send_denial_notice",
    "acusacao_fraude_registro": "operadora.fraude.register_fraud_accusation",
    "vinculo_contratual_mudanca": "operadora.cancel.send_cancellation_notice",
    "autorizacao_emissao": "operadora.auth.issue_authorization",
}


@pytest.fixture(autouse=True)
def _cache_limpo(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv(action_execution.MANIFEST_PATH_ENV, raising=False)
    action_execution._load_cached.cache_clear()
    yield
    action_execution._load_cached.cache_clear()


def _real() -> dict[str, Any]:
    data = yaml.safe_load(_SHIPPED.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _gravar(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "action-approvals.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _aprovado() -> dict[str, Any]:
    return {
        "aprovado": True,
        "aprovador": "Fixture Approver, fixture role",
        "data": "2026-01-01",
        "evidencia_ref": "fixture://evidence",
    }


# -- O registro real ----------------------------------------------------------------------------


def test_a_classe_existe_no_catalogo_e_no_manifesto_sem_aprovacao() -> None:
    spec = effect_classes.lookup_class(_CLASSE)
    assert spec is not None
    assert spec.rung == effect_classes.RUNG_C1_NOTIFICACAO
    assert spec.denial_shape == effect_classes.SHAPE_INCIDENTE_FALHA_FECHADA

    entrada = _real()["acoes"][_CLASSE]
    assert entrada["enforcement"] == "shadow"
    assert set(entrada["dominios_exigidos"]) == {"medica", "ans", "seguranca"}
    for dominio, bloco in entrada["aprovacoes"].items():
        assert bloco["aprovado"] is False, dominio
        for campo in ("aprovador", "data", "evidencia_ref"):
            assert bloco[campo] == "PENDENTE", f"{dominio}.{campo}"


def test_o_registro_real_continua_draft_shadow_e_desvio_intacto() -> None:
    real = _real()
    assert real["status"] == "DRAFT"
    assert real["modo"] == MODE_SHADOW
    assert real["enforcement_padrao_nao_mapeado"] == "shadow"
    assert real["deviation"]["expires"] == "2027-02-09"
    assert _CLASSE not in set(real["mapeamento_acoes"].values())


@pytest.mark.parametrize("topico", _TOPICOS)
def test_os_dois_topicos_nomeiam_a_classe_e_seguem_would_deny_sem_enforcar(topico: str) -> None:
    decisao = evaluate_worker_task(topic=topico, tenant="t-teste", path=_SHIPPED)
    assert decisao.action_class == _CLASSE
    assert decisao.reason != REASON_ACTION_UNMAPPED
    assert decisao.allow is False
    assert decisao.telemetry_decision == "WOULD_DENY"
    assert decisao.reason == REASON_MANIFEST_DRAFT
    assert decisao.enforced is False

    gateway = ActionExecutionGateway(load_action_approvals(_SHIPPED))
    assert gateway.classify(topico) == _CLASSE
    direta = gateway.evaluate(gateway.classify(topico))
    assert (direta.action_class, direta.allow, direta.reason, direta.enforced) == (
        _CLASSE,
        False,
        REASON_MANIFEST_DRAFT,
        False,
    )


def test_events_publish_continua_nao_mapeado() -> None:
    assert _EVENTS_PUBLISH not in _real()[TOPIC_MAP_KEY]
    decisao = evaluate_worker_task(topic=_EVENTS_PUBLISH, tenant="t-teste", path=_SHIPPED)
    assert decisao.action_class is None
    assert decisao.reason == REASON_ACTION_UNMAPPED
    assert decisao.allow is False
    assert decisao.enforced is False


@pytest.mark.parametrize(("classe", "topico"), sorted(_ADVERSAS.items()))
def test_as_classes_adversas_continuam_negando_no_registro_real(classe: str, topico: str) -> None:
    decisao = evaluate_worker_task(topic=topico, tenant="t-teste", path=_SHIPPED)
    assert decisao.action_class == classe
    assert decisao.allow is False
    assert decisao.enforced is False


# -- Cópias forjadas (tmp), para provar o que a declaração NÃO faz --------------------------------


def test_so_ratificar_o_arquivo_nao_aprova_a_classe(tmp_path: Path) -> None:
    """Com `status: RATIFICADO` e `modo: enforcing`, os blocos PENDENTE ainda negam — e aí BLOQUEIAM."""
    data = _real()
    data["status"] = STATUS_RATIFIED
    data["modo"] = MODE_ENFORCING
    data["acoes"][_CLASSE]["enforcement"] = "enforcing"
    gateway = ActionExecutionGateway(load_action_approvals(_gravar(tmp_path, data)))
    for topico in _TOPICOS:
        decisao = gateway.evaluate(gateway.classify(topico))
        assert decisao.action_class == _CLASSE
        assert decisao.allow is False
        assert decisao.reason == REASON_APPROVAL_PENDING
        assert decisao.enforced is True


def test_aprovar_a_classe_nova_nao_arrasta_as_adversas(tmp_path: Path) -> None:
    data = _real()
    data["status"] = STATUS_RATIFIED
    data["modo"] = MODE_ENFORCING
    data["acoes"][_CLASSE]["aprovacoes"] = {d: _aprovado() for d in ("medica", "ans", "seguranca")}
    gateway = ActionExecutionGateway(load_action_approvals(_gravar(tmp_path, data)))
    for topico in _TOPICOS:
        decisao = gateway.evaluate(gateway.classify(topico))
        assert (decisao.allow, decisao.reason) == (True, REASON_APPROVED)
    for classe, topico in _ADVERSAS.items():
        decisao = gateway.evaluate(gateway.classify(topico))
        assert decisao.action_class == classe
        assert decisao.allow is False, classe
        assert decisao.reason == REASON_APPROVAL_PENDING, classe
    publish = gateway.evaluate(gateway.classify(_EVENTS_PUBLISH))
    assert (publish.action_class, publish.allow, publish.reason) == (None, False, REASON_ACTION_UNMAPPED)


def test_dentes_sem_o_mapeamento_os_topicos_voltam_a_acao_nao_mapeada(tmp_path: Path) -> None:
    data = _real()
    for topico in _TOPICOS:
        del data[TOPIC_MAP_KEY][topico]
    path = _gravar(tmp_path, data)
    for topico in _TOPICOS:
        decisao = evaluate_worker_task(topic=topico, tenant="t-teste", path=path)
        assert decisao.action_class is None
        assert decisao.reason == REASON_ACTION_UNMAPPED
