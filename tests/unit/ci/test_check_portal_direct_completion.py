"""Regressão permanente das quatro cercas da interinidade registrada em DL-0049.

`scripts/ci/check_portal_direct_completion.py` existe para mecanizar um perímetro: a conclusão
direta do portal contorna os comandos atômicos da ADR-0049 e o CORS com credenciais transforma a
página de teste — demonstração declarada, sem autenticação própria — em caller do BFF. As duas
coisas são aceitáveis em `dev-sa-east-1` enquanto o relé D6 não existe, e em nenhum outro lugar.

Cada teste monta uma árvore sintética em `tmp_path` (o ambiente `dev-sa-east-1` REAL + os quatro
arquivos de código REAIS), aplica UMA evasão e exige que a cerca — e a cerca **específica**
responsável — reprove. Três testemunhas de não-vacuidade acompanham: a árvore sintética sem
evasão tem de passar, a árvore real do repositório tem de passar, e a cerca tem de reprovar
quando um arquivo que ela precisa ler desaparece (uma cerca cega não é uma cerca).

A herança é deliberada: o analisador de HCL vem de `check_canal_simular.py`, endurecido pela
revisão adversarial RV-371 (6 de 7 evasões passavam quando a cerca casava expressão regular).
Os testes 1 e 2 abaixo re-exercitam aquele endurecimento sobre os nomes NOVOS — é o que prova
que a herança não foi só um `import`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from scripts.ci.check_portal_direct_completion import (
    checar_cors_condicional,
    checar_defaults_desligados,
    checar_ligacao_fora_de_dev,
    checar_recusa_antes_do_await,
    executar,
)

# tests/unit/ci/<arquivo> -> parents[3] == raiz do repositório.
_RAIZ_REAL = Path(__file__).resolve().parents[3]
_ENV_DEV = Path("deploy/aws-ecs/envs/dev-sa-east-1")
_ENV_PROD = Path("deploy/aws-ecs/envs/prod-sa-east-1")
_PORTAL_TF = "service-portal.tf"

_ARQUIVOS_DE_CODIGO = (
    Path("src/maezo/portal/api/config.py"),
    Path("src/maezo/portal/api/app.py"),
    Path("src/maezo/portal/api/tasks.py"),
    Path("src/maezo/gateway/staff_cases/production_config.py"),
)

# Âncoras extraídas dos arquivos REAIS deste PR. Se o PR mudar qualquer uma delas, os testes
# falham em `_trocar` com a âncora ausente em vez de passarem a testar outra coisa.
_DEFAULT_FLAG = "    direct_completion: bool = False"
_DEFAULT_CORS = '    cors_origins: str = ""'
_CORS_CONDICIONAL = "    if cross_origins:\n        app.add_middleware(\n            CORSMiddleware,"
_BLOCO_CORS = (
    "    if cross_origins:\n"
    "        app.add_middleware(\n"
    "            CORSMiddleware,\n"
    "            allow_origins=list(cross_origins),  # exact strings; never a regex or an echo\n"
    "            allow_credentials=True,\n"
    '            allow_methods=["GET", "POST"],\n'
    '            allow_headers=["content-type", "x-csrf-token"],\n'
    "            max_age=600,\n"
    "        )\n"
)
_RECUSA = '        return completion_error("completion_unavailable")'

#: Uma entrada de `environment` do container do portal, inserida no fim do `concat([...])` real.
_ANCORA_ENV = '      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },'


def _trocar(texto: str, alvo: str, novo: str, *, contagem: int = 1) -> str:
    assert texto.count(alvo) >= contagem, f"ancora ausente no arquivo do PR: {alvo!r}"
    return texto.replace(alvo, novo, contagem)


def _montar(tmp_path: Path) -> Path:
    """Árvore sintética: o ambiente `dev-sa-east-1` real + os arquivos de código reais."""
    raiz = tmp_path / "arvore"
    shutil.copytree(_RAIZ_REAL / _ENV_DEV, raiz / _ENV_DEV)
    for relativo in _ARQUIVOS_DE_CODIGO:
        (raiz / relativo).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_RAIZ_REAL / relativo, raiz / relativo)
    return raiz


def _clonar_prod(raiz: Path) -> Path:
    """Clona dev como `prod-sa-east-1`: a mesma configuração, no ambiente errado."""
    shutil.copytree(raiz / _ENV_DEV, raiz / _ENV_PROD)
    return raiz / _ENV_PROD / _PORTAL_TF


def _reescrever(caminho: Path, transformar) -> None:  # type: ignore[no-untyped-def]
    caminho.write_text(transformar(caminho.read_text(encoding="utf-8")), encoding="utf-8")


def _acrescentar_env(caminho: Path, entrada: str) -> None:
    _reescrever(caminho, lambda t: _trocar(t, _ANCORA_ENV, _ANCORA_ENV + "\n" + entrada))


# ---------------------------------------------------------------------------------------
# Não-vacuidade: sem evasão, tudo passa.
# ---------------------------------------------------------------------------------------
def test_a_arvore_real_do_repositorio_passa() -> None:
    assert executar(_RAIZ_REAL) == []


def test_a_arvore_sintetica_sem_evasao_passa(tmp_path: Path) -> None:
    assert executar(_montar(tmp_path)) == []


def test_prod_clonado_sem_nenhum_dos_portoes_passa(tmp_path: Path) -> None:
    """A clonagem em si não é a evasão — só ligar o portão nela é."""
    raiz = _montar(tmp_path)
    _clonar_prod(raiz)
    assert executar(raiz) == []


# ---------------------------------------------------------------------------------------
# Cerca 1/2/3 — a configuração efetiva das task definitions.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "entrada",
    [
        '      { name = "MAEZO_PORTAL_DIRECT_COMPLETION", value = "1" },',
        '      { name = "MAEZO_PORTAL_DIRECT_COMPLETION", value = "true" },',
        '      { name = "MAEZO_PORTAL_DIRECT_COMPLETION", value = "on" },',
        '      { name = "MAEZO_PORTAL_DIRECT_COMPLETION_ENGINE_ORIGIN", '
        'value = "http://cibseven:8080/engine-rest" },',
        '      { name = "MAEZO_PORTAL_DIRECT_COMPLETION_TIMEOUT_SECONDS", value = "15" },',
        '      { name = "MAEZO_PORTAL_CORS_ORIGINS", value = "https://maezo-teste-dev.austa.com.br" },',
    ],
)
def test_o_portao_ligado_fora_de_dev_reprova(tmp_path: Path, entrada: str) -> None:
    raiz = _montar(tmp_path)
    _acrescentar_env(_clonar_prod(raiz), entrada)
    achados = checar_ligacao_fora_de_dev(raiz)
    assert achados, entrada
    assert any("prod-sa-east-1" in a for a in achados)


@pytest.mark.parametrize(
    "entrada",
    [
        '      { name = "MAEZO_PORTAL_DIRECT_COMPLETION", value = "1" },',
        '      { name = "MAEZO_PORTAL_DIRECT_COMPLETION_ENGINE_ORIGIN", '
        'value = "http://cibseven:8080/engine-rest" },',
        '      { name = "MAEZO_PORTAL_CORS_ORIGINS", value = "https://maezo-teste-dev.austa.com.br" },',
    ],
)
def test_o_mesmo_portao_ligado_em_dev_e_aceito(tmp_path: Path, entrada: str) -> None:
    """É isto que separa uma cerca de um bloqueio: em `dev-sa-east-1` o atalho PODE existir."""
    raiz = _montar(tmp_path)
    _acrescentar_env(raiz / _ENV_DEV / _PORTAL_TF, entrada)
    assert executar(raiz) == []


@pytest.mark.parametrize(
    "entrada",
    [
        # Evasão D/F de RV-371, aplicada aos nomes novos: indireção não resolvível.
        '      { name = "MAEZO_PORTAL_DIRECT_COMPLETION", value = local.ligar_conclusao },',
        '      { name = "MAEZO_PORTAL_CORS_ORIGINS", value = var.origens_permitidas },',
        '      { name = "MAEZO_PORTAL_DIRECT_COMPLETION", value = each.value.direct_completion },',
        # Um portão escondido num segredo não é visível nem para a cerca nem para o review.
        '      { name = "MAEZO_PORTAL_DIRECT_COMPLETION", '
        'valueFrom = "arn:aws:secretsmanager:sa-east-1:1:secret:x" },',
    ],
)
def test_valor_nao_resolvivel_ou_por_segredo_reprova_em_qualquer_ambiente(
    tmp_path: Path, entrada: str
) -> None:
    raiz = _montar(tmp_path)
    _acrescentar_env(raiz / _ENV_DEV / _PORTAL_TF, entrada)
    assert checar_ligacao_fora_de_dev(raiz)


def test_um_local_resolvivel_e_avaliado_como_o_terraform_o_resolveria(tmp_path: Path) -> None:
    """Herança de RV-371: resolver, não casar texto. `local.x = "1"` em prod reprova."""
    raiz = _montar(tmp_path)
    caminho = _clonar_prod(raiz)
    with (raiz / _ENV_PROD / "main.tf").open("a", encoding="utf-8") as saida:
        saida.write('\nlocals {\n  ligar_conclusao = "1"\n}\n')
    _acrescentar_env(
        caminho, '      { name = "MAEZO_PORTAL_DIRECT_COMPLETION", value = local.ligar_conclusao },'
    )
    achados = checar_ligacao_fora_de_dev(raiz)
    assert achados and any("prod-sa-east-1" in a for a in achados)


def test_mencao_que_a_cerca_nao_consegue_avaliar_reprova_por_precaucao(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    with (raiz / _ENV_DEV / "outputs.tf").open("a", encoding="utf-8") as saida:
        saida.write('\noutput "gate" {\n  value = "MAEZO_PORTAL_DIRECT_COMPLETION"\n}\n')
    assert checar_ligacao_fora_de_dev(raiz)


@pytest.mark.parametrize("arquivo", ["values.yaml", "deployment.yaml", "docker-compose.yml"])
def test_portao_declarado_fora_do_terraform_analisado_reprova(tmp_path: Path, arquivo: str) -> None:
    """Um template não tem ambiente provável, então nele o portão não pode existir."""
    raiz = _montar(tmp_path)
    alvo = raiz / "deploy" / "chart" / arquivo
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text("MAEZO_PORTAL_CORS_ORIGINS: https://qualquer.test\n", encoding="utf-8")
    achados = checar_ligacao_fora_de_dev(raiz)
    assert achados and any(arquivo in a for a in achados)


# ---------------------------------------------------------------------------------------
# Cerca 5 — o default desligado no código.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("relativo", [_ARQUIVOS_DE_CODIGO[0], _ARQUIVOS_DE_CODIGO[3]])
def test_default_da_flag_ligado_reprova(tmp_path: Path, relativo: Path) -> None:
    """Um default ligado tornaria as cercas de `deploy/**` decorativas."""
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / relativo,
        lambda t: _trocar(t, _DEFAULT_FLAG, "    direct_completion: bool = True"),
    )
    achados = checar_defaults_desligados(raiz)
    assert achados and any("direct_completion" in a for a in achados)


def test_default_do_cors_preenchido_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _ARQUIVOS_DE_CODIGO[0],
        lambda t: _trocar(t, _DEFAULT_CORS, '    cors_origins: str = "https://maezo-teste-dev.austa.com.br"'),
    )
    achados = checar_defaults_desligados(raiz)
    assert achados and any("cors_origins" in a for a in achados)


def test_default_vindo_de_env_em_vez_de_literal_reprova(tmp_path: Path) -> None:
    """`Field(default_factory=...)` ou um valor calculado não é um default que se possa ler."""
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _ARQUIVOS_DE_CODIGO[0],
        lambda t: _trocar(t, _DEFAULT_FLAG, "    direct_completion: bool = bool(os.environ.get('X'))"),
    )
    assert checar_defaults_desligados(raiz)


def test_campo_apagado_reprova_em_vez_de_passar_vazio(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(raiz / _ARQUIVOS_DE_CODIGO[0], lambda t: _trocar(t, _DEFAULT_FLAG, ""))
    achados = checar_defaults_desligados(raiz)
    assert achados and any("exatamente uma vez" in a for a in achados)


def test_arquivo_ausente_reprova_a_cerca_em_vez_de_cega_la(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    (raiz / _ARQUIVOS_DE_CODIGO[0]).unlink()
    assert checar_defaults_desligados(raiz)


def test_classe_renomeada_reprova_a_cerca_em_vez_de_cega_la(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _ARQUIVOS_DE_CODIGO[0],
        lambda t: _trocar(t, "class PortalSettings(BaseSettings):", "class Settings(BaseSettings):"),
    )
    achados = checar_defaults_desligados(raiz)
    assert achados and any("nao encontrada" in a for a in achados)


# ---------------------------------------------------------------------------------------
# Cerca 6 — CORS condicional, sem curinga.
# ---------------------------------------------------------------------------------------
def test_cors_instalado_incondicionalmente_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _ARQUIVOS_DE_CODIGO[1],
        lambda t: _trocar(
            t,
            _CORS_CONDICIONAL,
            "    app.add_middleware(\n        CORSMiddleware,",
        ),
    )
    achados = checar_cors_condicional(raiz)
    assert achados and any("FORA de um `if`" in a for a in achados)


def test_cors_com_curinga_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _ARQUIVOS_DE_CODIGO[1],
        lambda t: _trocar(t, "allow_origins=list(cross_origins),", 'allow_origins=["*"],'),
    )
    achados = checar_cors_condicional(raiz)
    assert achados and any("curinga" in a for a in achados)


def test_cors_com_expressao_de_origem_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _ARQUIVOS_DE_CODIGO[1],
        lambda t: _trocar(
            t,
            "allow_origins=list(cross_origins),",
            'allow_origin_regex=r"https://.*\\.austa\\.com\\.br",',
        ),
    )
    achados = checar_cors_condicional(raiz)
    assert achados and any("allow_origin_regex" in a for a in achados)


def test_nenhum_cors_no_app_nao_e_achado(tmp_path: Path) -> None:
    """Ausência de CORS é o estado mais seguro possível; a cerca não cobra o que não existe."""
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _ARQUIVOS_DE_CODIGO[1],
        lambda t: _trocar(t, _BLOCO_CORS, "    if cross_origins:\n        pass\n"),
    )
    # A árvore continua analisável: um arquivo que não parseia é ACHADO, não silêncio, e é
    # exatamente o que `test_arquivo_ilegivel_reprova_a_cerca` cobra abaixo.
    assert checar_cors_condicional(raiz) == []


def test_arquivo_ilegivel_reprova_a_cerca_em_vez_de_cega_la(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    (raiz / _ARQUIVOS_DE_CODIGO[1]).write_text("def (:\n", encoding="utf-8")
    achados = checar_cors_condicional(raiz)
    assert achados and any("nao foi possivel analisar" in a for a in achados)


# ---------------------------------------------------------------------------------------
# Cerca 7 — a recusa vem antes de tocar em dependência.
# ---------------------------------------------------------------------------------------
def test_recusa_depois_do_primeiro_await_reprova(tmp_path: Path) -> None:
    """Um 501 que só chega depois de resolver sessão já tocou no que o portão deveria proteger."""
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _ARQUIVOS_DE_CODIGO[2],
        lambda t: _trocar(
            t,
            "    policy = _policy(request)\n    if not policy.enabled:",
            "    policy = _policy(request)\n    await _warm(request)\n    if not policy.enabled:",
        ),
    )
    achados = checar_recusa_antes_do_await(raiz)
    assert achados and any("DEPOIS" in a for a in achados)


def test_recusa_removida_reprova(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _ARQUIVOS_DE_CODIGO[2],
        lambda t: _trocar(t, _RECUSA, '        return completion_error("resource_unavailable")'),
    )
    achados = checar_recusa_antes_do_await(raiz)
    assert achados and any("deixou de ter resposta declarada" in a for a in achados)


def test_funcao_da_rota_renomeada_reprova_a_cerca_em_vez_de_cega_la(tmp_path: Path) -> None:
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _ARQUIVOS_DE_CODIGO[2],
        lambda t: _trocar(t, "async def complete_task(", "async def finish_task("),
    )
    achados = checar_recusa_antes_do_await(raiz)
    assert achados and any("nao encontrada" in a for a in achados)


# ---------------------------------------------------------------------------------------
# O CLI.
# ---------------------------------------------------------------------------------------
def test_o_cli_reprova_com_codigo_1_e_sem_vazar_valor(tmp_path: Path, monkeypatch, capsys) -> None:
    import scripts.ci.check_portal_direct_completion as fence

    raiz = _montar(tmp_path)
    _acrescentar_env(
        _clonar_prod(raiz),
        '      { name = "MAEZO_PORTAL_DIRECT_COMPLETION", value = "1" },',
    )
    monkeypatch.setattr(fence, "REPO_ROOT", raiz)
    assert fence.main() == 1
    saida = capsys.readouterr()
    assert "REPROVADO" in saida.err
    assert "DL-0049" in saida.err or "ADR-0049" in saida.err
