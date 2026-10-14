"""Testes do classificador de deriva do dev, com planos `terraform show -json` sinteticos.

As perguntas que importam: o ruido conhecido passa? uma mudanca REAL escondida dentro de
uma task definition recriada reprova? um delete que nao e' de task definition reprova? e o
resumo nunca imprime valor nenhum (nem nome de atributo)?
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.ci.classificar_deriva import (
    PlanoInvalido,
    classificar,
    endereco_base,
    main,
    resumo_markdown,
)

SEGREDO = "SENHA-SUPERSECRETA-123"


def _container(**extra: object) -> dict:
    base = {
        "name": "app",
        "image": "123.dkr.ecr.sa-east-1.amazonaws.com/amh/maezo-operadora@sha256:" + "a" * 64,
        "command": ["sh", "-c", "echo oi\necho tchau\n"],
        "portMappings": [{"containerPort": 8080, "protocol": "tcp"}],
        "environment": [{"name": "TOKEN", "value": SEGREDO}],
    }
    base.update(extra)
    return base


def _task_def(
    antes: dict, depois: dict, *, acoes=("delete", "create"), endereco="aws_ecs_task_definition.worker"
) -> dict:
    """Mudanca de task definition como o Terraform a emite numa substituicao."""
    base = {
        "family": "maezo-operadora-dev-worker",
        "cpu": "256",
        "memory": "512",
        "arn": "arn:x:1",
        "revision": 1,
    }
    return {
        "address": endereco,
        "type": "aws_ecs_task_definition",
        "change": {
            "actions": list(acoes),
            "before": {**base, "container_definitions": json.dumps([antes])},
            "after": {
                "family": base["family"],
                "cpu": "256",
                "memory": "512",
                "container_definitions": json.dumps([depois]),
            },
            "after_unknown": {"arn": True, "id": True, "revision": True, "arn_without_revision": True},
        },
    }


def _plano(*mudancas: dict) -> dict:
    return {"format_version": "1.2", "resource_changes": list(mudancas)}


def _simples(endereco: str, acoes: list[str], antes=None, depois=None, desconhecido=None) -> dict:
    return {
        "address": endereco,
        "type": endereco_base(endereco).split(".")[0],
        "change": {
            "actions": acoes,
            "before": antes or {},
            "after": depois or {},
            "after_unknown": desconhecido or {},
        },
    }


# ---------------------------------------------------------------- ruido conhecido -----


def test_plano_vazio_e_limpo():
    assert classificar({"format_version": "1.2"}).limpo
    assert classificar(_plano(_simples("aws_x.y", ["no-op"]))).limpo


def test_ruido_hostport_passa():
    antes = _container(portMappings=[{"containerPort": 8080, "hostPort": 8080, "protocol": "tcp"}])
    depois = _container()
    r = classificar(_plano(_task_def(antes, depois)))
    assert r.limpo
    assert [m.endereco for m in r.ruido] == ["aws_ecs_task_definition.worker"]


def test_ruido_capabilities_add_vazio_e_systemcontrols_passam():
    antes = _container(linuxParameters={"capabilities": {"add": [], "drop": ["ALL"]}}, systemControls=[])
    depois = _container(linuxParameters={"capabilities": {"drop": ["ALL"]}})
    assert classificar(_plano(_task_def(antes, depois))).limpo


def test_ruido_capabilities_add_vazio_unico_campo_passa():
    antes = _container(linuxParameters={"capabilities": {"add": []}})
    depois = _container()
    assert classificar(_plano(_task_def(antes, depois))).limpo


def test_ruido_crlf_no_comando_passa():
    antes = _container(command=["sh", "-c", "echo oi\r\necho tchau\r\n"])
    depois = _container()
    assert classificar(_plano(_task_def(antes, depois))).limpo


def test_ruido_todos_juntos_e_ordem_de_acoes_invertida():
    antes = _container(
        portMappings=[{"containerPort": 8080, "hostPort": 8080, "protocol": "tcp"}],
        linuxParameters={"capabilities": {"add": []}},
        systemControls=[],
        command=["sh", "-c", "echo oi\r\necho tchau\r\n"],
    )
    r = classificar(_plano(_task_def(antes, _container(), acoes=("create", "delete"))))
    assert r.limpo


def test_assinatura_sns_pendente_e_ruido_mesmo_com_indice():
    r = classificar(
        _plano(
            _simples("aws_sns_topic_subscription.staff_alerts_email", ["create"]),
            _simples('module.a.aws_sns_topic_subscription.staff_alerts_email["x"]', ["create"]),
        )
    )
    assert r.limpo
    assert len(r.ruido) == 2


def _task_def_ruidosa() -> dict:
    return _task_def(
        _container(portMappings=[{"containerPort": 8080, "hostPort": 8080, "protocol": "tcp"}]), _container()
    )


def test_servico_que_so_segue_a_task_definition_e_ruido():
    antes = {
        "name": "worker",
        "desired_count": 1,
        "task_definition": "arn:x:1",
        "network_configuration": [{"a": 1}],
    }
    depois = {"name": "worker", "desired_count": 1, "network_configuration": [{"a": 1}]}
    desconhecido = {"task_definition": True, "network_configuration": [{}]}
    srv = _simples("aws_ecs_service.worker", ["update"], antes, depois, desconhecido)
    r = classificar(_plano(_task_def_ruidosa(), srv))
    assert r.limpo
    assert len(r.ruido) == 2


def test_seguidores_do_staff_job_sao_ruido():
    schedule = _simples(
        'aws_scheduler_schedule.staff_job["this"]',
        ["update"],
        {
            "state": "ENABLED",
            "target": [{"arn": "c", "ecs_parameters": [{"task_definition_arn": "arn:x:1"}]}],
        },
        {"state": "ENABLED", "target": [{"arn": "c", "ecs_parameters": [{}]}]},
        {"target": [{"ecs_parameters": [{"task_definition_arn": True}]}]},
    )
    politica = _simples(
        'aws_iam_role_policy.staff_job_scheduler["this"]',
        ["update"],
        {"name": "p", "policy": "{...}"},
        {"name": "p"},
        {"policy": True},
    )
    assert classificar(_plano(_task_def_ruidosa(), schedule, politica)).limpo


def test_seguidor_sem_task_definition_recriada_no_plano_e_deriva():
    antes = {"name": "worker", "task_definition": "arn:x:1"}
    depois = {"name": "worker"}
    srv = _simples("aws_ecs_service.worker", ["update"], antes, depois, {"task_definition": True})
    assert not classificar(_plano(srv)).limpo


def test_seguidor_com_atributo_desconhecido_a_mais_e_deriva():
    politica = _simples(
        'aws_iam_role_policy.staff_job_scheduler["this"]',
        ["update"],
        {"name": "p", "policy": "{...}"},
        {"name": "q"},
        {"policy": True},
    )
    assert not classificar(_plano(_task_def_ruidosa(), politica)).limpo


def test_ruido_de_valores_padrao_do_ecs_passa():
    """O que o dev real mostrou: listas vazias, "" vs nulo, ordem do ambiente, campos calculados."""
    antes = _container(
        mountPoints=[],
        portMappings=[],
        volumesFrom=[],
        environment=[{"name": "A", "value": "1"}, {"name": "B", "value": "2"}],
    )
    depois = _container(environment=[{"name": "B", "value": "2"}, {"name": "A", "value": "1"}])
    mud = _task_def(antes, depois)
    mud["change"]["before"].update(
        {
            "ipc_mode": "",
            "pid_mode": "",
            "task_role_arn": "",
            "enable_fault_injection": False,
            "volume": [{"name": "v", "host_path": "", "configure_at_launch": False}],
        }
    )
    mud["change"]["after"].update(
        {
            "ipc_mode": None,
            "pid_mode": None,
            "task_role_arn": None,
            "enable_fault_injection": None,
            "volume": [{"name": "v", "host_path": ""}],
        }
    )
    mud["change"]["after_unknown"].update(
        {
            "enable_fault_injection": True,
            "volume": [{"configure_at_launch": True}],
            "runtime_platform": [{}],
            "requires_compatibilities": [False],
        }
    )
    mud["change"]["before"].update(
        {"runtime_platform": [{"x": "y"}], "requires_compatibilities": ["FARGATE"]}
    )
    mud["change"]["after"].update({"runtime_platform": [{"x": "y"}], "requires_compatibilities": ["FARGATE"]})
    assert classificar(_plano(mud)).limpo


# ---------------------------------------------------------------- deriva real ---------


def test_task_definition_com_mudanca_real_escondida_e_deriva():
    """Ruido + uma variavel de ambiente diferente: a normalizacao NAO pode engolir isso."""
    antes = _container(portMappings=[{"containerPort": 8080, "hostPort": 8080, "protocol": "tcp"}])
    depois = _container(environment=[{"name": "TOKEN", "value": "outro"}])
    r = classificar(_plano(_task_def(antes, depois)))
    assert not r.limpo
    assert r.deriva[0].endereco == "aws_ecs_task_definition.worker"


def test_task_definition_com_imagem_diferente_e_deriva():
    antes = _container()
    depois = _container(image="x/amh/maezo-operadora@sha256:" + "b" * 64)
    assert not classificar(_plano(_task_def(antes, depois))).limpo


def test_capabilities_add_nao_vazio_e_deriva():
    antes = _container()
    depois = _container(linuxParameters={"capabilities": {"add": ["SYS_ADMIN"]}})
    assert not classificar(_plano(_task_def(antes, depois))).limpo


def test_atributo_desconhecido_nao_calculado_e_deriva():
    mud = _task_def(_container(), _container())
    mud["change"]["after_unknown"]["execution_role_arn"] = True
    assert not classificar(_plano(mud)).limpo


def test_task_definition_so_criada_ou_so_apagada_e_deriva():
    for acoes in (("create",), ("delete",), ("update",)):
        assert not classificar(_plano(_task_def(_container(), _container(), acoes=acoes))).limpo


def test_task_role_nova_na_task_definition_e_deriva():
    mud = _task_def(_container(), _container())
    mud["change"]["before"]["task_role_arn"] = ""
    mud["change"]["after"]["task_role_arn"] = "arn:aws:iam::1:role/nova"
    assert not classificar(_plano(mud)).limpo


def test_volume_novo_ou_renomeado_e_deriva():
    mud = _task_def(_container(), _container())
    mud["change"]["before"]["volume"] = [{"name": "v", "configure_at_launch": False}]
    mud["change"]["after"]["volume"] = [{"name": "outro"}]
    mud["change"]["after_unknown"]["volume"] = [{"configure_at_launch": True}]
    assert not classificar(_plano(mud)).limpo


def test_ambiente_com_variavel_a_mais_e_deriva():
    antes = _container(environment=[{"name": "A", "value": "1"}])
    depois = _container(environment=[{"name": "A", "value": "1"}, {"name": "B", "value": "2"}])
    assert not classificar(_plano(_task_def(antes, depois))).limpo


def test_update_real_e_deriva():
    mud = _simples(
        "aws_cloudwatch_log_group.x", ["update"], {"retention_in_days": 7}, {"retention_in_days": 30}
    )
    r = classificar(_plano(mud))
    assert [(m.endereco, m.acao) for m in r.deriva] == [("aws_cloudwatch_log_group.x", "update")]


def test_delete_de_recurso_que_nao_e_task_definition_e_deriva():
    r = classificar(_plano(_simples("aws_lb.portal", ["delete"])))
    assert [(m.endereco, m.acao) for m in r.deriva] == [("aws_lb.portal", "delete")]


def test_create_de_outra_assinatura_sns_e_deriva_e_update_da_pendente_tambem():
    r = classificar(
        _plano(
            _simples("aws_sns_topic_subscription.outra", ["create"]),
            _simples("aws_sns_topic_subscription.staff_alerts_email", ["update"]),
        )
    )
    assert len(r.deriva) == 2


def test_servico_com_outro_atributo_mudando_e_deriva():
    antes = {"name": "worker", "desired_count": 1, "task_definition": "arn:x:1"}
    depois = {"name": "worker", "desired_count": 3}
    srv = _simples("aws_ecs_service.worker", ["update"], antes, depois, {"task_definition": True})
    assert not classificar(_plano(srv)).limpo


def test_servico_update_sem_task_definition_desconhecida_e_deriva():
    antes = {"name": "worker", "task_definition": "arn:x:1"}
    depois = {"name": "worker", "task_definition": "arn:x:2"}
    assert not classificar(_plano(_simples("aws_ecs_service.worker", ["update"], antes, depois))).limpo


def test_servico_substituido_e_deriva():
    assert not classificar(_plano(_simples("aws_ecs_service.worker", ["delete", "create"]))).limpo


# ---------------------------------------------------------------- saida ---------------


def test_resumo_nunca_imprime_valores_nem_nomes_de_atributo():
    antes = _container(portMappings=[{"containerPort": 8080, "hostPort": 8080, "protocol": "tcp"}])
    depois = _container(environment=[{"name": "TOKEN", "value": "outro-valor-secreto"}])
    plano = _plano(
        _task_def(antes, depois),
        _simples(
            "aws_secretsmanager_secret.s",
            ["update"],
            {"description": SEGREDO},
            {"description": "novo-" + SEGREDO},
        ),
        _simples("aws_lb.portal", ["delete"], {"dns_name": "interno.example"}),
    )
    md = resumo_markdown(classificar(plano))
    for proibido in (
        SEGREDO,
        "outro-valor-secreto",
        "TOKEN",
        "interno.example",
        "description",
        "environment",
    ):
        assert proibido not in md
    assert "`aws_lb.portal`" in md
    assert "delete" in md


def test_resumo_limpo_diz_que_nao_ha_deriva():
    md = resumo_markdown(
        classificar(_plano(_simples("aws_sns_topic_subscription.staff_alerts_email", ["create"])))
    )
    assert "nenhuma" in md
    assert "Ruído conhecido ignorado (1)" in md


def test_endereco_base_ignora_indices_e_modulos():
    assert endereco_base('module.m["a.b"].aws_lb.portal[0]') == "aws_lb.portal"
    assert endereco_base("aws_lb.portal") == "aws_lb.portal"


@pytest.mark.parametrize("entrada", [{"outra": 1}, {"format_version": "1", "resource_changes": 3}])
def test_formato_inesperado_levanta(entrada):
    with pytest.raises(PlanoInvalido):
        classificar(entrada)


# ---------------------------------------------------------------- CLI -----------------


def _gravar(tmp_path: Path, plano: dict) -> Path:
    p = tmp_path / "plano.json"
    p.write_text(json.dumps(plano), encoding="utf-8")
    return p


def test_cli_codigos_de_saida(tmp_path, capsys):
    md = tmp_path / "r.md"
    limpo = _gravar(tmp_path, _plano(_simples("aws_sns_topic_subscription.staff_alerts_email", ["create"])))
    assert main([str(limpo), "--markdown", str(md)]) == 0
    assert "nenhuma" in md.read_text(encoding="utf-8")

    sujo = _gravar(tmp_path, _plano(_simples("aws_lb.portal", ["delete"])))
    assert main([str(sujo), "--markdown", str(md)]) == 1
    assert "aws_lb.portal" in md.read_text(encoding="utf-8")
    assert "\r" not in md.read_text(encoding="utf-8")

    capsys.readouterr()


def test_cli_plano_ilegivel_nao_e_limpo(tmp_path):
    ruim = tmp_path / "ruim.json"
    ruim.write_text("{nao e json", encoding="utf-8")
    assert main([str(ruim)]) == 2
    assert main([str(tmp_path / "inexistente.json")]) == 2
    lista = tmp_path / "lista.json"
    lista.write_text("[]", encoding="utf-8")
    assert main([str(lista)]) == 2
