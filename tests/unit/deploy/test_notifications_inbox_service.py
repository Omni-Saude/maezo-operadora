"""O servico ECS do inbox de avisos de escalonamento nasce DESLIGADO e nao disputa a ponte.

O que esta cerca tranca, lendo a FONTE Terraform (sem `plan`: o repo nao tem credencial AWS):

  1. Desligado por padrao: `notifications_inbox_desired_count` default 0, e TODO recurso do
     arquivo condicionado a `count` derivado dela — com 0 o plan do ambiente nao muda.
  2. Teto 1 replica (validacao da variavel) enquanto a ponte for singleton declarado.
  3. O comando e' o entrypoint do modulo (`python -m maezo.platform.integrations.notifications_inbox`)
     e o modulo existe.
  4. Consumer group distinto do da ponte: nenhum dos dois `.tf` sobrescreve o grupo por ambiente,
     e os DEFAULTS do codigo (importados, nao digitados) sao diferentes.
  5. Log group proprio `/ecs/<name>/notifications-inbox`.
  6. Mesmo segredo de banco da aplicacao e das migracoes (`db_secrets_maezo`): e' o que torna a
     role dona da tabela `escalation_team_notice` (sem GRANT novo).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Final

from maezo.platform.integrations.notifications_bridge import DEFAULT_CONSUMER_GROUP_ID
from maezo.platform.integrations.notifications_inbox import DEFAULT_INBOX_CONSUMER_GROUP_ID
from tests.unit.deploy._hcl_probe import attr_raw, block_body, direct_attrs, strip_comments

_ENV_DIR: Final[Path] = Path(__file__).resolve().parents[3] / "deploy" / "aws-ecs" / "envs" / "dev-sa-east-1"
_SERVICE_TF: Final[Path] = _ENV_DIR / "service-notifications-inbox.tf"
_MODULO: Final[str] = "maezo.platform.integrations.notifications_inbox"


def _src(path: Path) -> str:
    assert path.is_file(), f"arquivo Terraform ausente: {path}"
    return strip_comments(path.read_text(encoding="utf-8"))


def _variavel() -> str:
    return block_body(_src(_ENV_DIR / "variables.tf"), r'variable\s+"notifications_inbox_desired_count"\s*\{')


def test_desligado_por_padrao() -> None:
    assert attr_raw(direct_attrs(_variavel()), "default") == "0"


def test_teto_de_uma_replica_na_validacao() -> None:
    validacao = block_body(_variavel(), r"validation\s*\{")
    condicao = attr_raw(validacao, "condition")
    assert "var.notifications_inbox_desired_count >= 0" in condicao
    assert "var.notifications_inbox_desired_count <= 1" in condicao


def test_todo_recurso_do_arquivo_existe_so_com_o_servico_ligado() -> None:
    src = _src(_SERVICE_TF)
    local = block_body(src, r"locals\s*\{")
    assert attr_raw(local, "notifications_inbox_ativo") == "var.notifications_inbox_desired_count > 0 ? 1 : 0"
    recursos = re.findall(r'(?m)^resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', src)
    assert {tipo for tipo, _ in recursos} == {
        "aws_cloudwatch_log_group",
        "aws_ecs_task_definition",
        "aws_ecs_service",
    }
    for tipo, nome in recursos:
        corpo = direct_attrs(block_body(src, rf'resource\s+"{tipo}"\s+"{nome}"\s*\{{'))
        assert attr_raw(corpo, "count") == "local.notifications_inbox_ativo", f"{tipo}.{nome} sem count"


def test_servico_usa_a_variavel_de_replicas() -> None:
    servico = direct_attrs(
        block_body(_src(_SERVICE_TF), r'resource\s+"aws_ecs_service"\s+"notifications_inbox"\s*\{')
    )
    assert attr_raw(servico, "desired_count") == "var.notifications_inbox_desired_count"
    assert attr_raw(servico, "name") == '"notifications-inbox"'


def test_comando_e_o_entrypoint_do_modulo_e_o_modulo_existe() -> None:
    src = _src(_SERVICE_TF)
    assert f"exec python -m {_MODULO}" in src
    assert importlib.util.find_spec(_MODULO) is not None


def test_log_group_proprio() -> None:
    grupo = direct_attrs(
        block_body(_src(_SERVICE_TF), r'resource\s+"aws_cloudwatch_log_group"\s+"notifications_inbox"\s*\{')
    )
    assert attr_raw(grupo, "name") == '"/ecs/${local.name}/notifications-inbox"'


def test_consumer_group_distinto_do_da_ponte() -> None:
    assert DEFAULT_INBOX_CONSUMER_GROUP_ID != DEFAULT_CONSUMER_GROUP_ID
    # Nenhum dos dois servicos sobrescreve o grupo por ambiente: valem os defaults do codigo.
    for arquivo in (_SERVICE_TF, _ENV_DIR / "service-bridge.tf"):
        src = _src(arquivo)
        assert "KAFKA_GROUP_ID" not in src, f"{arquivo.name} sobrescreve o consumer group"


def test_mesmo_segredo_de_banco_da_aplicacao_e_das_migracoes() -> None:
    inbox = _src(_SERVICE_TF)
    migracoes = _src(_ENV_DIR / "task-migrations.tf")
    assert re.search(r"(?m)^\s*secrets\s*=\s*local\.db_secrets_maezo\s*$", inbox)
    assert re.search(r"(?m)^\s*secrets\s*=\s*local\.db_secrets_maezo\s*$", migracoes)
