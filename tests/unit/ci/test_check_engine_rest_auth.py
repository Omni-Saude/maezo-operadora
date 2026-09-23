"""Regressão da cerca N5 (T1.9 do plano portal-autoridade-nativa-dev).

Decisão do dono em 23/09/2026 (plano §6, N5): o `engine-rest` sem autenticação é aceito SÓ em dev.
`scripts/ci/check_engine_rest_auth.py` mecaniza isso de forma declarativa, porque pelo digest não
dá para saber se a imagem é aberta ou `secured*`: todo ambiente que não é dev e que sobe um engine
tem de declarar `engine_rest_authentication = "client-certificate"`, e a imagem oficial aberta
(`cibseven/cibseven`) não pode ser a do engine in-cluster do Helm fora de dev.

Cada teste monta uma árvore sintética em `tmp_path` com o ambiente `dev-sa-east-1` REAL e aplica
UM caso. Testemunhas de não-vacuidade: a árvore real passa, a árvore sintética mínima passa, e a
cerca reprova quando não encontra ambiente nenhum (uma cerca cega não é uma cerca).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from scripts.ci.check_engine_rest_auth import (
    ATRIBUTO,
    VALOR_EXIGIDO,
    checar_helm,
    checar_terraform,
    executar,
)

_RAIZ_REAL = Path(__file__).resolve().parents[3]
_ENV_DEV = Path("deploy/aws-ecs/envs/dev-sa-east-1")
_CHART = Path("deploy/helm/maezo-tenant")
_DECLARACAO = f'locals {{\n  {ATRIBUTO} = "{VALOR_EXIGIDO}"\n}}\n'


def _montar(tmp_path: Path, *, com_helm: bool = False) -> Path:
    raiz = tmp_path / "arvore"
    shutil.copytree(_RAIZ_REAL / _ENV_DEV, raiz / _ENV_DEV)
    if com_helm:
        (raiz / _CHART).mkdir(parents=True)
        for nome in ("values.yaml", "values-staging.yaml", "values-amh.yaml"):
            shutil.copyfile(_RAIZ_REAL / _CHART / nome, raiz / _CHART / nome)
    return raiz


def _copiar_dev_como(raiz: Path, nome: str, base: str = "deploy/aws-ecs/envs") -> Path:
    destino = raiz / base / nome
    shutil.copytree(raiz / _ENV_DEV, destino)
    return destino


# ---------------------------------------------------------------------------------------------
# Não-vacuidade.
# ---------------------------------------------------------------------------------------------
def test_arvore_real_do_repositorio_passa() -> None:
    assert executar(_RAIZ_REAL) == []


def test_arvore_minima_com_so_o_dev_real_passa(tmp_path: Path) -> None:
    assert executar(_montar(tmp_path, com_helm=True)) == []


def test_cerca_cega_reprova_sem_ambiente_nenhum(tmp_path: Path) -> None:
    raiz = tmp_path / "vazia"
    (raiz / "deploy").mkdir(parents=True)
    achados = checar_terraform(raiz)
    assert len(achados) == 1
    assert "nenhum ambiente" in achados[0]


def test_cerca_cega_reprova_sem_o_ambiente_dev_conhecido(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _copiar_dev_como(raiz, "staging-x")
    (raiz / "deploy/aws-ecs/envs/staging-x/zz.tf").write_text(_DECLARACAO)
    shutil.rmtree(raiz / _ENV_DEV)
    assert any("dev-sa-east-1" in a for a in checar_terraform(raiz))


# ---------------------------------------------------------------------------------------------
# O controle negativo do plano: copiar o dev para outro ambiente reprova; declarar passa.
# ---------------------------------------------------------------------------------------------
def test_copia_do_dev_como_staging_sem_declaracao_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _copiar_dev_como(raiz, "staging-x")
    achados = checar_terraform(raiz)
    assert len(achados) == 1
    assert "staging-x" in achados[0]
    assert ATRIBUTO in achados[0]


def test_mesma_copia_com_a_declaracao_passa(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    env = _copiar_dev_como(raiz, "staging-x")
    (env / "engine-rest.tf").write_text(_DECLARACAO)
    assert checar_terraform(raiz) == []


@pytest.mark.parametrize(
    "nome",
    [
        "prod-sa-east-1",
        "staging",
        "dr-us-east-1",
        "devx-sa-east-1",
        "prod-dev-1",
        # Allowlist exata (revisao de seguranca do #482): nome com cara de dev nao isenta.
        "dev-prod",
        "dev-us-east-1",
        "dev",
        "dev-sa-east-2",
        "dev-sa-east-1-copy",
    ],
)
def test_qualquer_nome_fora_de_dev_exige_a_declaracao(tmp_path: Path, nome: str) -> None:
    raiz = _montar(tmp_path)
    _copiar_dev_como(raiz, nome)
    assert any(nome in a for a in checar_terraform(raiz))


def test_so_os_caminhos_da_allowlist_podem_omitir(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    borda = raiz / "deploy/cloudflare/envs/dev"
    borda.mkdir(parents=True)
    shutil.copyfile(_RAIZ_REAL / "deploy/cloudflare/envs/dev/variables.tf", borda / "variables.tf")
    assert checar_terraform(raiz) == []
    # O mesmo conteudo num caminho fora da allowlist reprova, mesmo chamado `dev`.
    outra = raiz / "deploy/terraform/envs/dev"
    shutil.copytree(borda, outra)
    assert any("deploy/terraform/envs/dev" in a for a in checar_terraform(raiz))


def test_allowlist_dev_e_exata_e_justificada() -> None:
    from scripts.ci.check_engine_rest_auth import AMBIENTES_DEV, OVERLAYS_HELM_DEV, e_dev

    assert set(AMBIENTES_DEV) == {"deploy/aws-ecs/envs/dev-sa-east-1", "deploy/cloudflare/envs/dev"}
    assert all(motivo.strip() for motivo in AMBIENTES_DEV.values())
    assert not OVERLAYS_HELM_DEV
    assert not e_dev("deploy/aws-ecs/envs/dev-prod")
    assert not e_dev("deploy/aws-ecs/envs/dev-sa-east-1/")
    assert not e_dev("dev-sa-east-1")


# ---------------------------------------------------------------------------------------------
# Formas de declarar: o que conta e o que é indireção não resolvível.
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "conteudo",
    [
        f'locals {{\n  {ATRIBUTO} = "none"\n}}\n',
        f'locals {{\n  {ATRIBUTO} = "client-certificates"\n}}\n',
        f'locals {{\n  {ATRIBUTO} = ""\n}}\n',
        f'locals {{\n  {ATRIBUTO} = var.modo\n}}\nvariable "modo" {{\n  type = string\n}}\n',
        f'locals {{\n  {ATRIBUTO} = lower("CLIENT-CERTIFICATE")\n}}\n',
        f'locals {{\n  {ATRIBUTO} = "${{var.modo}}"\n}}\nvariable "modo" {{\n  type = string\n}}\n',
        f'variable "{ATRIBUTO}" {{\n  type = string\n}}\n',
        f'# {ATRIBUTO} = "{VALOR_EXIGIDO}"\n',
        f'/* {ATRIBUTO} = "{VALOR_EXIGIDO}" */\n',
        f'locals {{\n  nota = "{ATRIBUTO} = {VALOR_EXIGIDO}"\n}}\n',
    ],
    ids=[
        "outro-valor",
        "quase-igual",
        "vazio",
        "var-sem-default",
        "funcao",
        "interpolacao-sem-default",
        "variable-sem-default-nem-tfvars",
        "so-em-comentario-hash",
        "so-em-comentario-bloco",
        "so-dentro-de-string",
    ],
)
def test_declaracao_que_nao_prova_client_certificate_reprova(tmp_path: Path, conteudo: str) -> None:
    raiz = _montar(tmp_path)
    env = _copiar_dev_como(raiz, "staging-x")
    (env / "engine-rest.tf").write_text(conteudo)
    assert any("staging-x" in a for a in checar_terraform(raiz))


@pytest.mark.parametrize(
    ("arquivo", "conteudo"),
    [
        ("engine-rest.tf", f'locals {{\n  modo = "{VALOR_EXIGIDO}"\n  {ATRIBUTO} = local.modo\n}}\n'),
        (
            "engine-rest.tf",
            f'variable "{ATRIBUTO}" {{\n  type    = string\n  default = "{VALOR_EXIGIDO}"\n}}\n',
        ),
        ("engine-rest.tf", f'module "engine" {{\n  source = "./m"\n  {ATRIBUTO} = "{VALOR_EXIGIDO}"\n}}\n'),
        ("terraform.tfvars", f'{ATRIBUTO} = "{VALOR_EXIGIDO}"\n'),
        ("engine.auto.tfvars", f'{ATRIBUTO} = "{VALOR_EXIGIDO}"\n'),
    ],
    ids=["local-indireto", "variable-default", "argumento-de-modulo", "terraform-tfvars", "auto-tfvars"],
)
def test_formas_aceitas_de_declarar(tmp_path: Path, arquivo: str, conteudo: str) -> None:
    raiz = _montar(tmp_path)
    env = _copiar_dev_como(raiz, "staging-x")
    (env / arquivo).write_text(conteudo)
    if arquivo.endswith(".tfvars"):
        # Um valor em tfvars pede a variable que o recebe; sem default, o tfvars e' a declaracao.
        (env / "engine-rest.tf").write_text(f'variable "{ATRIBUTO}" {{\n  type = string\n}}\n')
    assert checar_terraform(raiz) == []


def test_tfvars_que_o_terraform_nao_carrega_nao_conta(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    env = _copiar_dev_como(raiz, "staging-x")
    (env / "engine-rest.tf").write_text(f'variable "{ATRIBUTO}" {{\n  type = string\n}}\n')
    (env / "terraform.tfvars.example").write_text(f'{ATRIBUTO} = "{VALOR_EXIGIDO}"\n')
    assert any("staging-x" in a for a in checar_terraform(raiz))


def test_toda_declaracao_conta_nao_so_a_ultima(tmp_path: Path) -> None:
    """Nome repetido nao e' erro para o Terraform/ECS e a ultima vence: nenhuma pode divergir."""
    raiz = _montar(tmp_path)
    env = _copiar_dev_como(raiz, "staging-x")
    (env / "engine-rest.tf").write_text(
        f'variable "{ATRIBUTO}" {{\n  type    = string\n  default = "none"\n}}\n'
    )
    (env / "terraform.tfvars").write_text(f'{ATRIBUTO} = "{VALOR_EXIGIDO}"\n')
    achados = checar_terraform(raiz)
    assert any("none" in a for a in achados)


def test_mencao_em_forma_nao_avaliavel_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    env = _copiar_dev_como(raiz, "staging-x")
    (env / "engine-rest.tf").write_text(
        _DECLARACAO + f'locals {{\n  outro = lookup(var.mapa, "{ATRIBUTO}", "none")\n}}\n'
    )
    assert any("nao consegue avaliar" in a for a in checar_terraform(raiz))


# ---------------------------------------------------------------------------------------------
# deploy/terraform/envs: so' exige quem sobe um engine.
# ---------------------------------------------------------------------------------------------
def test_env_terraform_sem_engine_nao_exige(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    env = raiz / "deploy/terraform/envs/staging-sa-east-1"
    env.mkdir(parents=True)
    shutil.copyfile(_RAIZ_REAL / "deploy/terraform/envs/staging-sa-east-1/main.tf", env / "main.tf")
    assert checar_terraform(raiz) == []


def test_env_terraform_que_sobe_engine_exige(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    env = raiz / "deploy/terraform/envs/staging-sa-east-1"
    env.mkdir(parents=True)
    (env / "main.tf").write_text(
        'resource "helm_release" "engine" {\n  name  = "cibseven"\n  chart = "cibseven"\n}\n'
    )
    assert any("staging-sa-east-1" in a for a in checar_terraform(raiz))
    (env / "auth.tf").write_text(_DECLARACAO)
    assert checar_terraform(raiz) == []


# ---------------------------------------------------------------------------------------------
# Helm: o engine in-cluster fora de dev.
# ---------------------------------------------------------------------------------------------
def _overlay(raiz: Path, nome: str, conteudo: dict) -> None:
    (raiz / _CHART / nome).write_text(yaml.safe_dump(conteudo))


def _in_cluster(**campos: object) -> dict:
    return {"cibseven": {"inCluster": {"enabled": True, **campos}}}


def test_helm_staging_liga_engine_in_cluster_sem_declaracao_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path, com_helm=True)
    _overlay(raiz, "values-staging.yaml", _in_cluster())
    achados = checar_helm(raiz)
    assert any("values-staging.yaml" in a and "engineRestAuthentication" in a for a in achados)


def test_helm_imagem_oficial_aberta_reprova_mesmo_declarada(tmp_path: Path) -> None:
    raiz = _montar(tmp_path, com_helm=True)
    _overlay(raiz, "values-staging.yaml", _in_cluster(engineRestAuthentication=VALOR_EXIGIDO))
    achados = checar_helm(raiz)
    assert any("cibseven/cibseven" in a for a in achados)


def test_helm_declarado_com_imagem_propria_passa(tmp_path: Path) -> None:
    raiz = _montar(tmp_path, com_helm=True)
    _overlay(
        raiz,
        "values-staging.yaml",
        _in_cluster(
            engineRestAuthentication=VALOR_EXIGIDO,
            image={
                "repository": "123.dkr.ecr.sa-east-1.amazonaws.com/amh/cibseven-maezo-secured",
                "tag": "x",
            },
        ),
    )
    assert checar_helm(raiz) == []


def test_helm_valor_base_ligado_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path, com_helm=True)
    base = yaml.safe_load((raiz / _CHART / "values.yaml").read_text())
    base["cibseven"]["inCluster"]["enabled"] = True
    (raiz / _CHART / "values.yaml").write_text(yaml.safe_dump(base))
    assert any("values.yaml" in a for a in checar_helm(raiz))


@pytest.mark.parametrize("valor", ["true", "{{ .Values.x }}", 1])
def test_helm_enabled_que_nao_e_booleano_reprova(tmp_path: Path, valor: object) -> None:
    raiz = _montar(tmp_path, com_helm=True)
    _overlay(raiz, "values-amh.yaml", {"cibseven": {"inCluster": {"enabled": valor}}})
    assert any("values-amh.yaml" in a for a in checar_helm(raiz))


@pytest.mark.parametrize("nome", ["values-dev.yaml", "values-dev-prod.yaml"])
def test_helm_overlay_com_nome_de_dev_nao_e_isento(tmp_path: Path, nome: str) -> None:
    """Nao existe overlay dev na allowlist: o nome do arquivo nao abre excecao."""
    raiz = _montar(tmp_path, com_helm=True)
    _overlay(raiz, nome, _in_cluster())
    assert any(nome in a for a in checar_helm(raiz))
