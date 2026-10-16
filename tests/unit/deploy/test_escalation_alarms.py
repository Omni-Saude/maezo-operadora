"""Cerca dos alarmes de escalonamento/retomada (`escalation-alarms.tf`).

O modo de falha que esta cerca impede e o do achado `phantom-metrics-escalation-latency-evalscore`
(predeploy-audit-report): alarme montado sobre um evento que o codigo nao emite, ou emite com outra
grafia, e que por isso nunca dispara. Por isso:

  * os nomes de evento NAO sao digitados aqui: vem das constantes do codigo;
  * a linha de log usada para conferir cada padrao e produzida pelo CODIGO REAL (o evento e
    capturado do caminho de producao e renderizado pelo `ConsoleRenderer` sem cor, que e o que um
    container sem TTY escreve) e cada termo do padrao do filtro tem de aparecer nela;
  * o padrao do supervisor NAO pode casar com o aviso de fallback (outro motivo).

Fonte Terraform lida com `_hcl_probe` (o repo nao tem credencial para `plan`).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import pytest
import structlog
from structlog.testing import capture_logs

from maezo.platform.integrations import agent_resume as ar
from maezo.platform.integrations.notifications_bridge import (
    BridgeDlqShunt,
    BridgeMessage,
    FakeBridgeKafkaConsumer,
)
from maezo.tools.workers import escalation as esc
from maezo.tools.workers.harness import ExternalTask, FakeKafkaPublisher, WorkerBpmnError
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.deploy._hcl_probe import attr_raw, block_body, direct_attrs, strip_comments

_REPO: Final[Path] = Path(__file__).resolve().parents[3]
_TF: Final[Path] = _REPO / "deploy" / "aws-ecs" / "envs" / "dev-sa-east-1" / "escalation-alarms.tf"

#: filtro -> (alarme, log group esperado, local de count)
_PARES: Final[dict[str, tuple[str, str, str]]] = {
    "agent_resume_falha": ("agent_resume_falha", "aws_cloudwatch_log_group.agent_resume[0].name", "resume"),
    "agent_resume_fila_morta": (
        "agent_resume_fila_morta",
        "aws_cloudwatch_log_group.agent_resume[0].name",
        "resume",
    ),
    "escalation_aviso_falhou": (
        "escalation_aviso_falhou",
        "aws_cloudwatch_log_group.worker.name",
        "escalation",
    ),
    "escalation_supervisor_alertado": (
        "escalation_supervisor_alertado",
        "aws_cloudwatch_log_group.worker.name",
        "escalation",
    ),
}


def _src() -> str:
    assert _TF.is_file(), f"arquivo ausente: {_TF}"
    return strip_comments(_TF.read_text(encoding="utf-8"))


def _recurso(tipo: str, nome: str) -> str:
    return block_body(_src(), rf'resource\s+"{tipo}"\s+"{nome}"\s*\{{')


def _termos_do_padrao(filtro: str) -> list[str]:
    """Os termos entre aspas do `pattern` do filtro (CloudWatch: todos tem de aparecer na linha)."""
    bruto = attr_raw(direct_attrs(_recurso("aws_cloudwatch_log_metric_filter", filtro)), "pattern")
    assert bruto.startswith('"') and bruto.endswith('"'), bruto
    interno = bruto[1:-1]
    termos = re.findall(r'\\"([^"\\]+)\\"', interno)
    assert termos, f"padrao sem termo entre aspas: {bruto}"
    assert interno == " ".join(f'\\"{t}\\"' for t in termos), f"padrao com sintaxe fora do esperado: {bruto}"
    return termos


def _renderizar(evento: dict[str, Any]) -> str:
    """A linha que um container sem TTY escreve para este evento (renderer de console sem cor)."""
    dados = dict(evento)
    dados["level"] = dados.pop("log_level")
    dados["timestamp"] = "2026-10-16T00:00:00Z"
    return str(structlog.dev.ConsoleRenderer(colors=False)(None, dados["level"], dados))


def _casa(filtro: str, linha: str) -> bool:
    return all(termo in linha for termo in _termos_do_padrao(filtro))


# --- eventos produzidos pelo codigo real ------------------------------------------------------


def _msg(value: Any) -> BridgeMessage:
    return BridgeMessage.from_value(value, topic=ar.PROCESS_COMPLETED_TOPIC, offset=0)


class _HandlerQueFalha:
    async def handle(self, value: Any) -> Any:
        raise RuntimeError("qualquer")


class _DlqPublisher:
    async def publish_dlq(self, topic: str, *, raw: bytes, key: bytes | None, headers: Any) -> None:
        return None


async def _evento_resume_falha() -> dict[str, Any]:
    consumer = FakeBridgeKafkaConsumer([_msg({"x": 1})])
    with capture_logs() as logs, pytest.raises(RuntimeError):
        await ar.run_resume_loop(consumer, _HandlerQueFalha())  # type: ignore[arg-type]
    (evento,) = [e for e in logs if e["event"] == ar.EVENTO_RESUME_FALHA]
    return evento


async def _evento_resume_fila_morta() -> dict[str, Any]:
    dlq = BridgeDlqShunt(publisher=_DlqPublisher(), audit_sink=FakeStartAuditSink(), tenant_id="amh")  # type: ignore[arg-type]
    handler = ar.ResumeHandler(
        tenant_id="amh",
        instructions=None,  # type: ignore[arg-type]
        recipients=None,  # type: ignore[arg-type]
        resumers={},
        alerter=None,  # type: ignore[arg-type]
    )
    consumer = FakeBridgeKafkaConsumer([_msg({"sem": "campos"})])
    with capture_logs() as logs:
        await ar.run_resume_loop(consumer, handler, dlq=dlq)
    (evento,) = [e for e in logs if e["event"] == ar.EVENTO_RESUME_FILA_MORTA]
    return evento


def _vars_supervisor(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "severidade": "grave",
        "grupo_atendimento": "plantao-clinico",
        "prioridade": "P1",
    }
    base.update(extra)
    return base


def _task(variables: dict[str, Any]) -> ExternalTask:
    return ExternalTask(
        task_id="t-1",
        topic="operadora.escalation.notify_supervisor",
        process_instance_id="p-1",
        business_key="ESC-amh-conv-1",
        worker_id="w-1",
        variables=variables,
    )


async def _evento_supervisor(**extra: Any) -> dict[str, Any]:
    with capture_logs() as logs:
        await esc.make_notify_supervisor_handler(FakeKafkaPublisher())(_task(_vars_supervisor(**extra)))
    (evento,) = [e for e in logs if e["event"] == esc.EVENTO_SUPERVISOR_ALERTADO]
    return evento


class _KafkaFora:
    async def publish(
        self, topic: str, value: dict[str, Any], *, key: str | None = None, best_effort: bool | None = None
    ) -> bool:
        raise RuntimeError("broker fora")


async def _evento_aviso_falhou() -> dict[str, Any]:
    with capture_logs() as logs, pytest.raises(WorkerBpmnError):
        await esc.make_notify_supervisor_handler(_KafkaFora())(
            _task(_vars_supervisor(motivo="sla_ack_breached"))
        )  # type: ignore[arg-type]
    (evento,) = [e for e in logs if e["event"] == esc.EVENTO_AVISO_FALHOU]
    return evento


# --- os padroes casam com o que o codigo escreve ----------------------------------------------


async def test_padrao_da_falha_da_retomada_casa_com_o_evento_real() -> None:
    linha = _renderizar(await _evento_resume_falha())
    assert _termos_do_padrao("agent_resume_falha") == [ar.EVENTO_RESUME_FALHA]
    assert _casa("agent_resume_falha", linha), linha


async def test_padrao_da_fila_morta_casa_com_o_evento_real() -> None:
    linha = _renderizar(await _evento_resume_fila_morta())
    assert _termos_do_padrao("agent_resume_fila_morta") == [ar.EVENTO_RESUME_FILA_MORTA]
    assert _casa("agent_resume_fila_morta", linha), linha
    assert not _casa("agent_resume_falha", linha)


async def test_padrao_do_aviso_falhou_casa_com_o_evento_real() -> None:
    linha = _renderizar(await _evento_aviso_falhou())
    assert _termos_do_padrao("escalation_aviso_falhou") == [esc.EVENTO_AVISO_FALHOU]
    assert _casa("escalation_aviso_falhou", linha), linha


async def test_padrao_do_prazo_estourado_casa_so_com_o_motivo_de_prazo() -> None:
    assert _termos_do_padrao("escalation_supervisor_alertado") == [
        esc.EVENTO_SUPERVISOR_ALERTADO,
        f"motivo_alerta={esc.MOTIVO_ALERTA_SLA_ACK}",
    ]
    prazo = _renderizar(await _evento_supervisor(motivo="sla_ack_breached"))
    fallback = _renderizar(await _evento_supervisor(motivo_fallback="notificacao_primaria_falhou"))
    assert _casa("escalation_supervisor_alertado", prazo), prazo
    assert not _casa("escalation_supervisor_alertado", fallback), fallback


# --- forma dos recursos -----------------------------------------------------------------------


def test_desligado_por_padrao_e_todo_recurso_condicionado() -> None:
    src = _src()
    variavel = direct_attrs(block_body(src, r'variable\s+"escalation_alarms_enabled"\s*\{'))
    assert attr_raw(variavel, "default") == "false"
    assert attr_raw(variavel, "type") == "bool"
    locais = block_body(src, r"locals\s*\{")
    assert attr_raw(locais, "escalation_alarms_ativo") == "var.escalation_alarms_enabled ? 1 : 0"
    assert attr_raw(locais, "resume_alarms_ativo") == (
        "var.escalation_alarms_enabled && local.agent_resume_ativo == 1 ? 1 : 0"
    )
    recursos = re.findall(r'(?m)^\s*resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', src)
    assert len(recursos) == 2 * len(_PARES)
    for tipo, nome in recursos:
        contagem = attr_raw(direct_attrs(_recurso(tipo, nome)), "count")
        assert contagem in {"local.escalation_alarms_ativo", "local.resume_alarms_ativo"}, (tipo, nome)


@pytest.mark.parametrize("filtro", sorted(_PARES))
def test_filtro_e_alarme_formam_par_no_log_group_certo(filtro: str) -> None:
    alarme, log_group, familia = _PARES[filtro]
    corpo_filtro = _recurso("aws_cloudwatch_log_metric_filter", filtro)
    corpo_alarme = direct_attrs(_recurso("aws_cloudwatch_metric_alarm", alarme))
    contagem = f"local.{familia}_alarms_ativo"
    assert attr_raw(direct_attrs(corpo_filtro), "count") == contagem
    assert attr_raw(corpo_alarme, "count") == contagem
    assert attr_raw(direct_attrs(corpo_filtro), "log_group_name") == log_group
    transformacao = block_body(corpo_filtro, r"metric_transformation\s*\{")
    assert attr_raw(transformacao, "name") == attr_raw(corpo_alarme, "metric_name")
    assert attr_raw(transformacao, "namespace") == attr_raw(corpo_alarme, "namespace")
    assert attr_raw(corpo_alarme, "treat_missing_data") == '"notBreaching"'
    assert attr_raw(corpo_alarme, "comparison_operator") == '"GreaterThanOrEqualToThreshold"'
    assert attr_raw(corpo_alarme, "threshold") == "1"
    assert attr_raw(corpo_alarme, "alarm_actions") == "local.escalation_alarm_sns"


def test_destino_e_o_topico_de_alertas_do_staff() -> None:
    locais = block_body(_src(), r"locals\s*\{")
    assert attr_raw(locais, "escalation_alarm_sns") == "[for topic in aws_sns_topic.staff_alerts : topic.arn]"


def test_prazo_estourado_e_por_hora_com_ok_actions() -> None:
    alarme = direct_attrs(_recurso("aws_cloudwatch_metric_alarm", "escalation_supervisor_alertado"))
    assert attr_raw(alarme, "period") == "3600"
    assert attr_raw(alarme, "ok_actions") == "local.escalation_alarm_sns"


@pytest.mark.parametrize("alarme", sorted(a for a, _, _ in _PARES.values()))
def test_cada_alarme_aponta_runbook_especifico_que_existe_e_cita_o_evento(alarme: str) -> None:
    descricao = attr_raw(direct_attrs(_recurso("aws_cloudwatch_metric_alarm", alarme)), "alarm_description")
    achado = re.search(r"Runbook: (docs/runbooks/alerts/[A-Za-z0-9_-]+\.md)", descricao)
    assert achado is not None, descricao
    caminho = _REPO / achado.group(1)
    assert caminho.is_file(), caminho
    assert caminho.name.lower() not in {"readme.md", "index.md"}
    evento = {
        "agent_resume_falha": ar.EVENTO_RESUME_FALHA,
        "agent_resume_fila_morta": ar.EVENTO_RESUME_FILA_MORTA,
        "escalation_aviso_falhou": esc.EVENTO_AVISO_FALHOU,
        "escalation_supervisor_alertado": esc.EVENTO_SUPERVISOR_ALERTADO,
    }[alarme]
    assert evento in caminho.read_text(encoding="utf-8")
