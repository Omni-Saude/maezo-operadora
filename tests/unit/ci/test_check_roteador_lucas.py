"""Regressao da cerca do roteador Helena -> Lucas (`scripts/ci/check_roteador_lucas.py`, ADR-0062).

Cada teste monta uma arvore sintetica em `tmp_path` (o ambiente `dev-sa-east-1` REAL + os tres
arquivos de codigo REAIS), aplica UMA evasao e exige que a cerca — e a cerca ESPECIFICA do item —
reprove. As testemunhas de nao-vacuidade vem primeiro: a arvore real passa, a sintetica sem
evasao passa, e ligar o roteador EM DEV passa (a cerca nao pode reprovar o que o plano permite).
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from scripts.ci.check_roteador_lucas import (
    checar_construcao_condicional,
    checar_default_desligado,
    checar_ligacao_fora_de_dev,
    executar,
)

_RAIZ_REAL = Path(__file__).resolve().parents[3]
_ENV_DEV = Path("deploy/aws-ecs/envs/dev-sa-east-1")
_ENV_PROD = Path("deploy/aws-ecs/envs/prod-sa-east-1")
_RECEPTOR_TF = "service-webhook-receiver.tf"
_SETTINGS = Path("src/maezo/platform/webhooks/whatsapp/settings.py")
_SERVICE = Path("src/maezo/platform/webhooks/service.py")
_ROTEAMENTO = Path("src/maezo/platform/webhooks/whatsapp/roteamento.py")
_ARQUIVOS_DE_CODIGO = (_SETTINGS, _SERVICE, _ROTEAMENTO)

#: Ancoras extraidas dos arquivos REAIS. Se o PR mudar uma delas, `_trocar` falha com a ancora
#: ausente em vez de o teste passar a testar outra coisa.
_ANCORA_ENV = '      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },'
_DEFAULT = "    roteador_lucas_enabled: bool = Field(\n        default=False,\n"
_IF_DO_SERVICE = "    roteador = None\n    if settings.roteador_lucas_enabled:\n"
_CONSTRUCAO = "        roteador = ConversaRouter(\n"
_IMPORT = (
    "        from maezo.platform.webhooks.whatsapp.roteamento import ConversaRouter, "
    "PostgresAgenteAtivoStore\n"
)


def _trocar(texto: str, alvo: str, novo: str) -> str:
    assert alvo in texto, f"ancora ausente no arquivo do PR: {alvo!r}"
    return texto.replace(alvo, novo, 1)


def _montar(tmp_path: Path) -> Path:
    raiz = tmp_path / "arvore"
    shutil.copytree(_RAIZ_REAL / _ENV_DEV, raiz / _ENV_DEV)
    for relativo in _ARQUIVOS_DE_CODIGO:
        (raiz / relativo).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_RAIZ_REAL / relativo, raiz / relativo)
    return raiz


def _clonar_prod(raiz: Path) -> Path:
    shutil.copytree(raiz / _ENV_DEV, raiz / _ENV_PROD)
    return raiz / _ENV_PROD / _RECEPTOR_TF


def _reescrever(caminho: Path, transformar: Callable[[str], str]) -> None:
    caminho.write_bytes(transformar(caminho.read_bytes().decode("utf-8")).encode("utf-8"))


def _acrescentar_env(caminho: Path, entrada: str) -> None:
    _reescrever(caminho, lambda t: _trocar(t, _ANCORA_ENV, _ANCORA_ENV + "\n" + entrada))


# ---------------------------------------------------------------------------------------
# Nao-vacuidade.
# ---------------------------------------------------------------------------------------
def test_a_arvore_real_do_repositorio_passa() -> None:
    assert executar(_RAIZ_REAL) == []


def test_a_arvore_sintetica_sem_evasao_passa(tmp_path: Path) -> None:
    assert executar(_montar(tmp_path)) == []


@pytest.mark.parametrize(
    "entrada",
    [
        '      { name = "MAEZO_ROTEADOR_LUCAS", value = "true" },',
        '      { name = "MAEZO_LUCAS_FONTE_COBRANCA", value = "simulada" },',
        '      { name = "MAEZO_LUCAS_INATIVIDADE_MINUTOS", value = "60" },',
    ],
)
def test_ligar_em_dev_passa(tmp_path: Path, entrada: str) -> None:
    raiz = _montar(tmp_path)
    _acrescentar_env(raiz / _ENV_DEV / _RECEPTOR_TF, entrada)
    assert executar(raiz) == []


@pytest.mark.parametrize(
    "entrada",
    [
        '      { name = "MAEZO_ROTEADOR_LUCAS", value = "0" },',
        '      { name = "MAEZO_ROTEADOR_LUCAS", value = "false" },',
        '      { name = "MAEZO_LUCAS_INATIVIDADE_MINUTOS", value = "30" },',
    ],
)
def test_desligado_ou_inerte_fora_de_dev_passa(tmp_path: Path, entrada: str) -> None:
    raiz = _montar(tmp_path)
    _acrescentar_env(_clonar_prod(raiz), entrada)
    assert checar_ligacao_fora_de_dev(raiz) == []


# ---------------------------------------------------------------------------------------
# Item 1 — ligado fora de dev.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "entrada",
    [
        '      { name = "MAEZO_ROTEADOR_LUCAS", value = "true" },',
        '      { name = "MAEZO_ROTEADOR_LUCAS", value = "1" },',
        '      { name = "MAEZO_ROTEADOR_LUCAS", value = "on" },',
        '      { name = "MAEZO_LUCAS_FONTE_COBRANCA", value = "simulada" },',
    ],
)
def test_item1_ligado_fora_de_dev_reprova(tmp_path: Path, entrada: str) -> None:
    raiz = _montar(tmp_path)
    _acrescentar_env(_clonar_prod(raiz), entrada)
    achados = checar_ligacao_fora_de_dev(raiz)
    assert achados and all("prod-sa-east-1" in a for a in achados), achados


def test_item1_ligado_por_local_resolvivel_fora_de_dev_reprova(tmp_path: Path) -> None:
    """A evasao RV-371: trocar o literal por `local.x`. O analisador resolve como o Terraform."""
    raiz = _montar(tmp_path)
    tf = _clonar_prod(raiz)
    _acrescentar_env(tf, '      { name = "MAEZO_ROTEADOR_LUCAS", value = local.ligar_lucas },')
    tf.write_bytes(tf.read_bytes() + b'\nlocals {\n  ligar_lucas = "true"\n}\n')
    assert checar_ligacao_fora_de_dev(raiz)


# ---------------------------------------------------------------------------------------
# Item 2 — segredo ou nao resolvivel, em qualquer ambiente (inclusive dev).
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "entrada",
    [
        '      { name = "MAEZO_ROTEADOR_LUCAS", valueFrom = "arn:aws:secretsmanager:x:y:secret:z" },',
        '      { name = "MAEZO_LUCAS_INATIVIDADE_MINUTOS", valueFrom = "arn:aws:ssm:x:y:parameter/z" },',
        '      { name = "MAEZO_ROTEADOR_LUCAS", value = data.aws_ssm_parameter.lucas.value },',
        '      { name = "MAEZO_LUCAS_FONTE_COBRANCA", value = local.nao_existe },',
    ],
)
def test_item2_segredo_ou_indirecao_reprova_ate_em_dev(tmp_path: Path, entrada: str) -> None:
    raiz = _montar(tmp_path)
    _acrescentar_env(raiz / _ENV_DEV / _RECEPTOR_TF, entrada)
    assert checar_ligacao_fora_de_dev(raiz)


# ---------------------------------------------------------------------------------------
# Item 3 — fora do Terraform analisado, ou em forma que nao vira entrada de `environment`.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("relativo", "conteudo"),
    [
        ("deploy/helm/maezo/values.yaml", "webhookReceiver:\n  env:\n    MAEZO_ROTEADOR_LUCAS: 'true'\n"),
        (
            "deploy/c1-local/docker-compose.lucas.yml",
            "environment:\n  - MAEZO_LUCAS_FONTE_COBRANCA=simulada\n",
        ),
    ],
)
def test_item3_fora_do_terraform_reprova(tmp_path: Path, relativo: str, conteudo: str) -> None:
    raiz = _montar(tmp_path)
    (raiz / relativo).parent.mkdir(parents=True, exist_ok=True)
    (raiz / relativo).write_bytes(conteudo.encode())
    achados = checar_ligacao_fora_de_dev(raiz)
    assert any(Path(relativo).name in a and "fora do Terraform" in a for a in achados), achados


def test_item3_mencao_que_nao_vira_entrada_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    tf = raiz / _ENV_DEV / _RECEPTOR_TF
    tf.write_bytes(tf.read_bytes() + b'\nlocals {\n  nomes_lucas = ["MAEZO_ROTEADOR_LUCAS"]\n}\n')
    achados = checar_ligacao_fora_de_dev(raiz)
    assert achados and "nao consegue avaliar" in achados[0], achados


# ---------------------------------------------------------------------------------------
# Item 4 — o default no codigo.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "novo",
    [
        "    roteador_lucas_enabled: bool = Field(\n        default=True,\n",
        "    roteador_lucas_enabled: bool = Field(\n        default=bool(0),\n",
        "    roteador_lucas_enabled: bool = Field(\n        default=0,\n",
    ],
)
def test_item4_default_que_nao_e_false_literal_reprova(tmp_path: Path, novo: str) -> None:
    raiz = _montar(tmp_path)
    _reescrever(raiz / _SETTINGS, lambda t: _trocar(t, _DEFAULT, novo))
    assert checar_default_desligado(raiz)


def test_item4_default_por_atribuicao_direta_true_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _SETTINGS,
        lambda t: _trocar(
            t, "    @model_validator(", "    roteador_lucas_enabled: bool = True\n\n    @model_validator("
        ),
    )
    assert checar_default_desligado(raiz), "duas declaracoes do campo tambem tem de reprovar"


def test_item4_campo_removido_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(raiz / _SETTINGS, lambda t: t.replace("roteador_lucas_enabled: bool", "roteador_outro: bool"))
    assert checar_default_desligado(raiz)


# ---------------------------------------------------------------------------------------
# Item 5 — construcao so' no corpo do `if settings.roteador_lucas_enabled`.
# ---------------------------------------------------------------------------------------
def test_item5_construcao_fora_do_if_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _SERVICE,
        lambda t: _trocar(t, _IF_DO_SERVICE, "    roteador = None\n    if True:\n"),
    )
    achados = checar_construcao_condicional(raiz)
    assert achados and "service.py" in achados[0], achados


def test_item5_if_negado_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _SERVICE,
        lambda t: _trocar(
            t, _IF_DO_SERVICE, "    roteador = None\n    if not settings.roteador_lucas_enabled:\n"
        ),
    )
    assert checar_construcao_condicional(raiz)


def test_item5_construcao_no_else_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _SERVICE,
        lambda t: _trocar(
            t,
            _IF_DO_SERVICE,
            "    roteador = None\n    if settings.roteador_lucas_enabled:\n        pass\n    else:\n",
        ),
    )
    assert checar_construcao_condicional(raiz)


def test_item5_alias_de_import_fora_do_if_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    modulo = raiz / "src/maezo/platform/webhooks/atalho.py"
    modulo.write_bytes(
        b"from maezo.platform.webhooks.whatsapp.roteamento import ConversaRouter as Roteador\n\n"
        b"def montar(store, lexicos, inatividade):\n"
        b"    return Roteador(tenant_id='amh', store=store, lexicos=lexicos, inatividade=inatividade)\n"
    )
    achados = checar_construcao_condicional(raiz)
    assert achados and "atalho.py" in achados[0], achados


def test_item5_lucas_turno_por_atributo_de_modulo_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    modulo = raiz / "src/maezo/platform/webhooks/outro.py"
    modulo.write_bytes(
        b"from maezo.platform.webhooks.whatsapp import lucas_turno\n\n"
        b"def montar(settings):\n"
        b"    if settings.memoria_clinica_enabled:\n"
        b"        return lucas_turno.LucasTurno()\n"
    )
    achados = checar_construcao_condicional(raiz)
    assert achados and "outro.py" in achados[0], achados


def test_item5_renomear_a_classe_deixa_a_cerca_cega_e_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _ROTEAMENTO, lambda t: _trocar(t, "class ConversaRouter:", "class RoteadorDeConversa:")
    )
    achados = checar_construcao_condicional(raiz)
    assert achados and "cerca cega" in achados[0], achados


def test_item5_ancora_de_import_existe() -> None:
    """Testemunha: o `import` do roteador mora DENTRO do `if` no service real."""
    texto = (_RAIZ_REAL / _SERVICE).read_text(encoding="utf-8")
    assert texto.index(_IF_DO_SERVICE) < texto.index(_IMPORT) < texto.index(_CONSTRUCAO)
