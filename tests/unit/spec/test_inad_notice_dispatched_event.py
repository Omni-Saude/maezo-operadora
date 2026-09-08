"""Cerca focal da re-semantizacao R-093 do evento de notificacao de inadimplencia.

O evento registra somente que a etapa de disparo foi publicada. A publicacao continua
incondicional entre ``ST_CheckPriorNotice`` e ``GW_CureWindow`` e nao comprova entrega,
recebimento ou o requisito regulatorio ainda marcado como DRAFT/verify.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from maezo.platform.topic_registry import TopicRegistry

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn"
_CONTRACT = _REPO / "docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md"

_BPMN_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_CAMUNDA_NS = "{http://camunda.org/schema/1.0/bpmn}"
_TASK_ID = "ST_PublishInadimplenciaNotified"
_TOPIC = "agents.events.inadimplencia.notice_dispatched"


def _root() -> ET.Element:
    return ET.parse(_BPMN).getroot()


def _one(element_name: str, element_id: str) -> ET.Element:
    matches = [
        element for element in _root().iter(f"{_BPMN_NS}{element_name}") if element.get("id") == element_id
    ]
    assert len(matches) == 1, f"esperado um {element_name} {element_id}; encontrados {len(matches)}"
    return matches[0]


def test_publicador_emite_notice_dispatched_sem_mudar_delivery_semantics() -> None:
    """Pina o topico honesto e o payload, sem transformar o evento em prova de entrega."""
    task = _one("serviceTask", _TASK_ID)
    assert task.get(f"{_CAMUNDA_NS}type") == "external"
    assert task.get(f"{_CAMUNDA_NS}topic") == "operadora.events.publish"

    params = {
        str(param.get("name")): (param.text or "").strip()
        for param in task.iter(f"{_CAMUNDA_NS}inputParameter")
    }
    assert params == {
        "event_topic": _TOPIC,
        "event_payload_vars": "tenant_id,numero_contrato",
    }
    assert "comprovacao_notificacao_previa" not in ET.tostring(task, encoding="unicode")
    assert TopicRegistry.validate(_TOPIC) is True


def test_publicacao_permanece_incondicional_no_mesmo_fluxo() -> None:
    """R-093 troca a semantica do nome; nao cria gate nem altera o caminho/timer RN 593."""
    expected = {
        "Flow_RequestNotif_PubPended": ("ST_CheckPriorNotice", _TASK_ID),
        "Flow_PubPended_CureGW": (_TASK_ID, "GW_CureWindow"),
    }
    for flow_id, endpoints in expected.items():
        flow = _one("sequenceFlow", flow_id)
        assert (flow.get("sourceRef"), flow.get("targetRef")) == endpoints
        assert flow.find(f"{_BPMN_NS}conditionExpression") is None

    prior_notice_task = _one("serviceTask", "ST_CheckPriorNotice")
    documentation = " ".join("".join(prior_notice_task.itertext()).split())
    assert "DRAFT/verify" in documentation


def test_contrato_cataloga_dispatch_sem_afirmar_entrega_ou_recebimento() -> None:
    """O catalogo contratual usa o novo nome e conserva a ancora regulatoria pendente."""
    contract = _CONTRACT.read_text(encoding="utf-8")
    assert f"`{_TOPIC}`" in contract
    assert "agents.events.inadimplencia.notified" not in contract
    assert "`notice_dispatched`" in contract
    assert "RN 593 (DRAFT/verify" in contract
    assert "**DRAFT/verify**" in contract
