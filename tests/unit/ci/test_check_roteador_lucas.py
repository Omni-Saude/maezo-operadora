"""Regressao da cerca do roteador Helena -> Lucas (`scripts/ci/check_roteador_lucas.py`, ADR-0062).

Cada teste monta uma arvore sintetica em `tmp_path` (o ambiente `dev-sa-east-1` REAL + os cinco
arquivos de codigo REAIS: settings, service, roteamento, grafo da Helena e despachante), aplica
UMA evasao e exige que a cerca — e a cerca ESPECIFICA do item —
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
    checar_handoff_so_na_helena,
    checar_ligacao_fora_de_dev,
    checar_lucas_depois_da_helena,
    executar,
)

_RAIZ_REAL = Path(__file__).resolve().parents[3]
_ENV_DEV = Path("deploy/aws-ecs/envs/dev-sa-east-1")
_ENV_PROD = Path("deploy/aws-ecs/envs/prod-sa-east-1")
_RECEPTOR_TF = "service-webhook-receiver.tf"
_SETTINGS = Path("src/maezo/platform/webhooks/whatsapp/settings.py")
_SERVICE = Path("src/maezo/platform/webhooks/service.py")
_ROTEAMENTO = Path("src/maezo/platform/webhooks/whatsapp/roteamento.py")
_GRAFO = Path("src/maezo/agents/helena/graph.py")
_DESPACHANTE = Path("src/maezo/platform/webhooks/whatsapp/dispatch.py")
_ARQUIVOS_DE_CODIGO = (_SETTINGS, _SERVICE, _ROTEAMENTO, _GRAFO, _DESPACHANTE)

#: Ancoras extraidas dos arquivos REAIS. Se o PR mudar uma delas, `_trocar` falha com a ancora
#: ausente em vez de o teste passar a testar outra coisa.
_ANCORA_ENV = '      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },'
#: As tres entradas REAIS do receptor em dev (onda g). A fonte so' existe em dev: um clone
#: "prod sem Lucas" tem de tira-la, e o clone verbatim tem de reprovar por causa dela.
_ENTRADA_ROTEADOR = '      { name = "MAEZO_ROTEADOR_LUCAS", value = tostring(var.roteador_lucas_enabled) },\n'
_ENTRADA_JANELA = (
    '      { name = "MAEZO_LUCAS_INATIVIDADE_MINUTOS", value = tostring(var.lucas_inatividade_minutos) },\n'
)
_ENTRADA_FONTE = '      { name = "MAEZO_LUCAS_FONTE_COBRANCA", value = var.lucas_fonte_cobranca },\n'
_TFVARS_LUCAS = "lucas.auto.tfvars"
_LINHA_TFVARS = "roteador_lucas_enabled = false\n"
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
    # Linha de base INDEPENDENTE do valor real de dev: as testemunhas abaixo trocam esta linha, e o
    # estado ligado/desligado do ambiente nao pode mudar o que elas provam.
    (raiz / _ENV_DEV / _TFVARS_LUCAS).write_bytes(_LINHA_TFVARS.encode("utf-8"))
    for relativo in _ARQUIVOS_DE_CODIGO:
        (raiz / relativo).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_RAIZ_REAL / relativo, raiz / relativo)
    return raiz


def _clonar_prod(raiz: Path, *, com_fonte: bool = False) -> Path:
    """Copia dev para prod. Sem `com_fonte`, tira a entrada da fonte simulada: e' o "prod sem
    Lucas" legitimo, onde o roteador desligado tem de passar."""
    shutil.copytree(raiz / _ENV_DEV, raiz / _ENV_PROD)
    tf = raiz / _ENV_PROD / _RECEPTOR_TF
    if not com_fonte:
        _reescrever(tf, lambda t: _trocar(t, _ENTRADA_FONTE, ""))
    return tf


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


def test_o_receptor_real_declara_as_tres_entradas_e_o_tfvars_e_literal() -> None:
    """Ancora das testemunhas abaixo: se a forma real mudar, elas nao testam outra coisa."""
    receptor = (_RAIZ_REAL / _ENV_DEV / _RECEPTOR_TF).read_bytes().decode("utf-8")
    for entrada in (_ENTRADA_ROTEADOR, _ENTRADA_JANELA, _ENTRADA_FONTE):
        assert receptor.count(entrada) == 1, entrada
    # O VALOR de dev e' decisao de cada momento (ligado em 01/10/2026); o que esta ancora fixa e' a
    # FORMA: uma atribuicao literal `true`/`false`, que e' o que a cerca consegue resolver.
    tfvars = (_RAIZ_REAL / _ENV_DEV / _TFVARS_LUCAS).read_bytes().decode("utf-8")
    atribuicoes = [linha.strip() for linha in tfvars.splitlines() if linha.startswith("roteador_lucas_enabled")]
    assert atribuicoes in (["roteador_lucas_enabled = false"], ["roteador_lucas_enabled = true"]), atribuicoes


def test_ligar_em_dev_pelo_tfvars_passa(tmp_path: Path) -> None:
    """O caminho previsto pelo plano (§4): o PR troca `lucas.auto.tfvars` para `true` em dev."""
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _ENV_DEV / _TFVARS_LUCAS,
        lambda t: _trocar(t, _LINHA_TFVARS, "roteador_lucas_enabled = true\n"),
    )
    assert executar(raiz) == []


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


def test_item1_clone_verbatim_de_dev_reprova_pela_fonte(tmp_path: Path) -> None:
    """Copiar o receptor de dev para outro ambiente como esta' leva a fonte simulada junto."""
    raiz = _montar(tmp_path)
    _clonar_prod(raiz, com_fonte=True)
    achados = checar_ligacao_fora_de_dev(raiz)
    assert achados and all("prod-sa-east-1" in a for a in achados), achados
    assert any("MAEZO_LUCAS_FONTE_COBRANCA" in a for a in achados), achados


@pytest.mark.parametrize(
    "arquivo",
    ["lucas.auto.tfvars", "terraform.tfvars", "zz-outro.auto.tfvars"],
)
def test_item1_ligado_pelo_tfvars_fora_de_dev_reprova(tmp_path: Path, arquivo: str) -> None:
    """O default continua `false`; quem liga e' o tfvars que o Terraform carrega sozinho."""
    raiz = _montar(tmp_path)
    _clonar_prod(raiz)
    (raiz / _ENV_PROD / arquivo).write_bytes(b"roteador_lucas_enabled = true\n")
    if arquivo == "terraform.tfvars":
        # `*.auto.tfvars` carrega DEPOIS de `terraform.tfvars`: o `false` copiado de dev venceria.
        (raiz / _ENV_PROD / _TFVARS_LUCAS).unlink()
    achados = checar_ligacao_fora_de_dev(raiz)
    assert any("MAEZO_ROTEADOR_LUCAS='true'" in a and "prod-sa-east-1" in a for a in achados), achados


def test_item1_ligado_pelo_tfvars_json_fora_de_dev_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _clonar_prod(raiz)
    (raiz / _ENV_PROD / "zz.auto.tfvars.json").write_bytes(b'{"roteador_lucas_enabled": true}')
    achados = checar_ligacao_fora_de_dev(raiz)
    assert any("MAEZO_ROTEADOR_LUCAS='true'" in a and "prod-sa-east-1" in a for a in achados), achados


def test_item1_default_da_variavel_ligado_fora_de_dev_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _clonar_prod(raiz)
    (raiz / _ENV_PROD / _TFVARS_LUCAS).unlink()
    _reescrever(
        raiz / _ENV_PROD / "variables.tf",
        lambda t: _trocar(
            t,
            '  default     = false\n  nullable    = false\n}\n\nvariable "lucas_inatividade_minutos"',
            '  default     = true\n  nullable    = false\n}\n\nvariable "lucas_inatividade_minutos"',
        ),
    )
    achados = checar_ligacao_fora_de_dev(raiz)
    assert any("MAEZO_ROTEADOR_LUCAS='true'" in a and "prod-sa-east-1" in a for a in achados), achados


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
        '      { name = "MAEZO_ROTEADOR_LUCAS", value = tostring(var.nao_existe) },',
        '      { name = "MAEZO_ROTEADOR_LUCAS", value = tostring(data.aws_ssm_parameter.x.value) },',
        '      { name = "MAEZO_ROTEADOR_LUCAS", value = var.roteador_lucas_enabled ? "true" : "false" },',
    ],
)
def test_item2_segredo_ou_indirecao_reprova_ate_em_dev(tmp_path: Path, entrada: str) -> None:
    raiz = _montar(tmp_path)
    _acrescentar_env(raiz / _ENV_DEV / _RECEPTOR_TF, entrada)
    assert checar_ligacao_fora_de_dev(raiz)


def test_item2_tfvars_json_ilegivel_reprova_ate_em_dev(tmp_path: Path) -> None:
    """Nao se sabe o que um tfvars ilegivel sobrescreve: a entrada por `var.` nao se resolve."""
    raiz = _montar(tmp_path)
    (raiz / _ENV_DEV / "zz.auto.tfvars.json").write_bytes(b"{nao e' json")
    achados = checar_ligacao_fora_de_dev(raiz)
    assert any("nao e' um literal" in a for a in achados), achados


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


# ---------------------------------------------------------------------------------------
# Item 6 — o handoff so' nasce no no' `handoff_cobranca` da Helena (onda f).
# ---------------------------------------------------------------------------------------
_CONSTRUCAO_DO_HANDOFF = "        handoff: HandoffCobranca = {\n"
_NO_DO_HANDOFF = "    async def handoff_cobranca(self, state: HelenaState) -> dict[str, Any]:\n"


def _modulo(raiz: Path, nome: str, codigo: str) -> Path:
    caminho = raiz / "src/maezo/platform/webhooks" / nome
    caminho.write_bytes(codigo.encode("utf-8"))
    return caminho


def test_item6_ancoras_existem() -> None:
    texto = (_RAIZ_REAL / _GRAFO).read_text(encoding="utf-8")
    assert texto.count(_CONSTRUCAO_DO_HANDOFF) == 1
    assert texto.index(_NO_DO_HANDOFF) < texto.index(_CONSTRUCAO_DO_HANDOFF)


_KW = "para='lucas', cobranca_subtipo='outro', competencia=None, message_ref=ref"


@pytest.mark.parametrize(
    "codigo",
    [
        # chamada direta do TypedDict
        f"from maezo.agents.helena.graph import HandoffCobranca\n\n"
        f"def forjar(ref):\n    return HandoffCobranca({_KW})\n",
        # alias de import
        f"from maezo.agents.helena.graph import HandoffCobranca as H\n\n"
        f"def forjar(ref):\n    return H({_KW})\n",
        # atributo de modulo
        f"from maezo.agents.helena import graph\n\n"
        f"def forjar(ref):\n    return graph.HandoffCobranca({_KW})\n",
        # anotacao com valor
        "from maezo.agents.helena.graph import HandoffCobranca\n\n"
        "def forjar(ref):\n    h: HandoffCobranca = {}\n    return h\n",
        # anotacao em string
        "def forjar(ref):\n    h: 'HandoffCobranca' = {}\n    return h\n",
        # cast
        "from typing import cast\nfrom maezo.agents.helena.graph import HandoffCobranca\n\n"
        "def forjar(bruto):\n    return cast(HandoffCobranca, bruto)\n",
        # dict literal SEM o tipo, com as chaves de um handoff
        "def forjar(ref):\n"
        "    return {'para': 'lucas', 'cobranca_subtipo': 'outro', "
        "'competencia': None, 'message_ref': ref}\n",
        # dict(...) por keywords
        f"def forjar(ref):\n    return dict({_KW})\n",
    ],
    ids=["chamada", "alias", "atributo", "anotacao", "anotacao-string", "cast", "dict-literal", "dict-kw"],
)
def test_item6_handoff_construido_fora_da_helena_reprova(tmp_path: Path, codigo: str) -> None:
    raiz = _montar(tmp_path)
    _modulo(raiz, "forja.py", codigo)
    achados = checar_handoff_so_na_helena(raiz)
    assert achados and "forja.py" in achados[0], achados


def test_item6_no_grafo_mas_fora_do_no_handoff_reprova(tmp_path: Path) -> None:
    """Mesmo dentro de `graph.py`, so' o no' `handoff_cobranca` (o que vem depois de
    `_handoff_recusado`) pode construir."""
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _GRAFO,
        lambda t: (
            t
            + "\n\ndef _atalho(ref: str) -> HandoffCobranca:\n"
            + "    h: HandoffCobranca = {'para': 'lucas', 'cobranca_subtipo': 'outro', "
            + "'competencia': None, 'message_ref': ref}\n"
            + "    return h\n"
        ),
    )
    achados = checar_handoff_so_na_helena(raiz)
    assert achados and "graph.py" in achados[0], achados


def test_item6_funcao_aninhada_no_no_handoff_nao_herda_a_permissao(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _GRAFO,
        lambda t: _trocar(
            t,
            _CONSTRUCAO_DO_HANDOFF,
            "        def _de_novo(ref: str) -> Any:\n"
            "            return {'para': 'lucas', 'cobranca_subtipo': 'outro', 'message_ref': ref}\n\n"
            + _CONSTRUCAO_DO_HANDOFF,
        ),
    )
    assert checar_handoff_so_na_helena(raiz)


def test_item6_renomear_a_classe_deixa_a_cerca_cega_e_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _GRAFO, lambda t: _trocar(t, "class HandoffCobranca(TypedDict):", "class Passagem(TypedDict):")
    )
    achados = checar_handoff_so_na_helena(raiz)
    assert achados and "cerca cega" in achados[0], achados


def test_item6_renomear_o_no_deixa_a_cerca_cega_e_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _GRAFO,
        lambda t: _trocar(t, _NO_DO_HANDOFF, _NO_DO_HANDOFF.replace("handoff_cobranca", "passar_ao_lucas")),
    )
    assert any("cerca cega" in a for a in checar_handoff_so_na_helena(raiz))


# ---------------------------------------------------------------------------------------
# Item 7 — o turno do Lucas so' depois dos lexicos e do `ainvoke` da Helena (onda f).
# ---------------------------------------------------------------------------------------
_PRE_ROTEAR = (
    "        sinais = self._pre_rotear(message.text, conversation_id)"
    " if self.roteador is not None else None\n"
)
_AINVOKE = "            result = await compiled.ainvoke(initial_state, thread_config)\n"
_IF_DO_HANDOFF = '            if self._roteamento_completo and isinstance(result.get("handoff"), Mapping):\n'
_HANDOFF_DO_RESULTADO = '                        handoff=result["handoff"],\n'
_SINAL_LEXICO = "                sinal_saude_lexico=sinais is None or sinais.sinal_saude,\n"
_DEF_TURNO = "    async def _turno_do_lucas(\n"
_DEF_RESUME = "    async def resume(\n"
_DOCSTRING_RESUME = (
    '        """Run ONE Helena RESUME turn (GAP-XHITL-4) under the conversation' + "'s checkpoint thread.\n"
)


def test_item7_ancoras_existem() -> None:
    texto = (_RAIZ_REAL / _DESPACHANTE).read_text(encoding="utf-8")
    for ancora in (
        _PRE_ROTEAR,
        _IF_DO_HANDOFF,
        _HANDOFF_DO_RESULTADO,
        _SINAL_LEXICO,
        _DEF_TURNO,
        _DEF_RESUME,
        _DOCSTRING_RESUME,
    ):
        assert texto.count(ancora) == 1, ancora
    assert texto.count(_AINVOKE) == 2  # `resume` e `dispatch`; a cerca so' le' o do `dispatch`
    assert texto.index(_PRE_ROTEAR) < texto.rindex(_AINVOKE) < texto.index(_IF_DO_HANDOFF)


def test_item7_lexicos_depois_do_ainvoke_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)

    def mover(t: str) -> str:
        t = _trocar(t, _PRE_ROTEAR, "        sinais = None\n")
        tardio = "            sinais = self._pre_rotear(message.text, conversation_id)\n"
        return _trocar(t, _IF_DO_HANDOFF, tardio + _IF_DO_HANDOFF)

    _reescrever(raiz / _DESPACHANTE, mover)
    achados = checar_lucas_depois_da_helena(raiz)
    assert any("DEPOIS do `ainvoke`" in a for a in achados), achados


def test_item7_sem_lexicos_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(raiz / _DESPACHANTE, lambda t: _trocar(t, _PRE_ROTEAR, "        sinais = None\n"))
    assert any("_pre_rotear" in a for a in checar_lucas_depois_da_helena(raiz))


def test_item7_sinal_de_saude_que_nao_vem_dos_lexicos_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _DESPACHANTE,
        lambda t: _trocar(t, _SINAL_LEXICO, "                sinal_saude_lexico=False,\n"),
    )
    assert any("sinal_saude_lexico" in a for a in checar_lucas_depois_da_helena(raiz))


def test_item7_turno_do_lucas_sem_o_if_do_handoff_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _DESPACHANTE,
        lambda t: _trocar(t, _IF_DO_HANDOFF, "            if self._roteamento_completo:\n"),
    )
    assert any("fora do `if`" in a for a in checar_lucas_depois_da_helena(raiz))


def test_item7_handoff_que_nao_e_o_do_resultado_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _DESPACHANTE,
        lambda t: _trocar(
            t, _HANDOFF_DO_RESULTADO, "                        handoff=self._ultimo_handoff,\n"
        ),
    )
    assert any("so' a saida do `ainvoke`" in a for a in checar_lucas_depois_da_helena(raiz))


def test_item7_turno_do_lucas_chamado_do_resume_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _DESPACHANTE,
        lambda t: _trocar(
            t,
            _DOCSTRING_RESUME,
            "        await self._turno_do_lucas(None, phone_hash='', conversation_id=conversation_id, "
            "beneficiario_pseudo_id='', handoff={})\n" + _DOCSTRING_RESUME,
        ),
    )
    achados = checar_lucas_depois_da_helena(raiz)
    assert any("referenciado fora" in a for a in achados), achados


def test_item7_executar_por_alias_no_despachante_reprova(tmp_path: Path) -> None:
    """Um alias (`lt = self.lucas_turno`) nao escapa: no despachante, QUALQUER `.executar(`
    fora de `_turno_do_lucas` reprova."""
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _DESPACHANTE,
        lambda t: _trocar(
            t,
            _DOCSTRING_RESUME,
            "        lt = self.lucas_turno\n        await lt.executar({}, None, None)\n" + _DOCSTRING_RESUME,
        ),
    )
    achados = checar_lucas_depois_da_helena(raiz)
    assert any("lt.executar" in a for a in achados), achados


def test_item7_executar_do_lucas_em_outro_modulo_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _modulo(
        raiz,
        "atalho_lucas.py",
        "async def atender(dispatcher, handoff, conversa, remetente):\n"
        "    return await dispatcher.lucas_turno.executar(handoff, conversa, remetente)\n",
    )
    achados = checar_lucas_depois_da_helena(raiz)
    assert achados and "atalho_lucas.py" in achados[0], achados


def test_item7_conversa_do_turno_construida_fora_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _modulo(
        raiz,
        "atalho_conversa.py",
        "from maezo.platform.webhooks.whatsapp.lucas_turno import ConversaDoTurno as C\n\n"
        "def montar():\n"
        "    return C(conversation_id='', beneficiario_pseudo_id='', to_hash='', message_id='')\n",
    )
    achados = checar_lucas_depois_da_helena(raiz)
    assert achados and "atalho_conversa.py" in achados[0], achados


def test_item7_renomear_o_metodo_deixa_a_cerca_cega_e_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _DESPACHANTE,
        lambda t: _trocar(t, _DEF_TURNO, "    async def _atender_cobranca(\n"),
    )
    achados = checar_lucas_depois_da_helena(raiz)
    assert achados and "cerca cega" in achados[0], achados
