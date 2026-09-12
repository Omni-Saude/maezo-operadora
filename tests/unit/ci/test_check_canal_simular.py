"""Regressão permanente das evasões que a revisão adversarial RV-371 encontrou na cerca.

A cerca `scripts/ci/check_canal_simular.py` existe para mecanizar um perímetro: a rota
`/receptor/simular` produz uma assinatura **indistinguível da da Meta**, e quem alcança o canal
fabrica uma mensagem de beneficiário. A primeira versão da cerca casava expressão regular
contra o texto dos `.tf`, e a revisão mostrou que **6 de 7 evasões passavam com `rc=0`** — a
pior delas (F) deixando um canal COM capacidade de assinar limpo em produção.

Cada teste aqui monta uma árvore sintética em `tmp_path` (cópia real de
`deploy/aws-ecs/envs/dev-sa-east-1/` + o `server.py` real), aplica UMA evasão, e exige que a
cerca — e a cerca **específica** responsável — reprove. Os identificadores A/D/E/F/G/H/I são
os da tabela do relatório RV-371, preservados de propósito: quem reabrir o relatório encontra
aqui a mesma linha.

Duas testemunhas de não-vacuidade acompanham as evasões: a árvore sintética **sem** evasão tem
de passar, e a árvore **real** do repositório tem de passar. Sem elas, um teste que reprova
tudo pareceria verde.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from scripts.ci.check_canal_simular import (
    checar_cercas_no_codigo,
    checar_ligacao_fora_de_dev,
    checar_segredo_no_canal,
    executar,
)

# tests/unit/ci/<arquivo> -> parents[3] == raiz do repositório.
_RAIZ_REAL = Path(__file__).resolve().parents[3]
_ENV_DEV = Path("deploy/aws-ecs/envs/dev-sa-east-1")
_ENV_PROD = Path("deploy/aws-ecs/envs/prod-sa-east-1")
_CANAL_TF = "service-canal-teste.tf"
_SERVIDOR = Path("src/maezo/platform/testchannel/server.py")

# Âncoras extraídas dos arquivos REAIS do PR. Se o PR mudar qualquer uma delas, os testes
# falham em `_trocar` com a âncora ausente em vez de passarem a testar outra coisa.
_PORTAO_LIGADO = '{ name = "CANAL_SIMULAR_RECEPTOR", value = "1" },'
_BLOCO_SEGREDO = (
    "    secrets = [\n"
    '      { name = "WHATSAPP_APP_SECRET", '
    'valueFrom = "${aws_secretsmanager_secret.whatsapp_meta.arn}:app_secret::" },\n'
    "    ]\n"
)
_FAIXA = 'FAIXA_TESTE = re.compile(r"^55119000000\\d{2}$")'
_GATE_NA_ROTA = "        if not SIMULAR_LIGADO:"


def _trocar(texto: str, alvo: str, novo: str, *, contagem: int = 1) -> str:
    """Substitui uma âncora exigindo que ela exista — uma evasão que não se aplicou não é
    uma evasão testada."""
    assert texto.count(alvo) >= contagem, f"âncora ausente no arquivo do PR: {alvo!r}"
    return texto.replace(alvo, novo, contagem)


def _montar(tmp_path: Path) -> Path:
    """Árvore sintética: o ambiente `dev-sa-east-1` real + o `server.py` real."""
    raiz = tmp_path / "arvore"
    shutil.copytree(_RAIZ_REAL / _ENV_DEV, raiz / _ENV_DEV)
    (raiz / _SERVIDOR).parent.mkdir(parents=True)
    shutil.copy2(_RAIZ_REAL / _SERVIDOR, raiz / _SERVIDOR)
    return raiz


def _clonar_prod(raiz: Path, *, locais: str = "") -> Path:
    """Clona o ambiente de dev como `prod-sa-east-1` — é o que a evasão precisa: a mesma
    configuração, o ambiente errado."""
    shutil.copytree(raiz / _ENV_DEV, raiz / _ENV_PROD)
    if locais:
        with (raiz / _ENV_PROD / "main.tf").open("a", encoding="utf-8") as saida:
            saida.write(f"\nlocals {{\n{locais}}}\n")
    return raiz / _ENV_PROD / _CANAL_TF


def _reescrever(caminho: Path, transformar) -> None:  # type: ignore[no-untyped-def]
    caminho.write_text(transformar(caminho.read_text(encoding="utf-8")), encoding="utf-8")


# ---------------------------------------------------------------------------------------
# As sete evasões de RV-371.
# ---------------------------------------------------------------------------------------
def _sonda_a(raiz: Path) -> None:
    """A — portão ligado em prod, em HCL multi-linha comum (sem a vírgula que a regex exigia)."""
    canal = _clonar_prod(raiz)
    _reescrever(
        canal,
        lambda t: _trocar(
            _trocar(t, _BLOCO_SEGREDO, ""),
            _PORTAO_LIGADO,
            '{\n        name  = "CANAL_SIMULAR_RECEPTOR"\n        value = "1"\n      },',
        ),
    )


def _sonda_d(raiz: Path) -> None:
    """D — o mesmo portão, com `value = local.ligar` no lugar do literal."""
    canal = _clonar_prod(raiz, locais='  ligar = "1"\n')
    _reescrever(
        canal,
        lambda t: _trocar(
            _trocar(t, _BLOCO_SEGREDO, ""),
            _PORTAO_LIGADO,
            '{ name = "CANAL_SIMULAR_RECEPTOR", value = local.ligar },',
        ),
    )


def _sonda_e(raiz: Path) -> None:
    """E — portão E segredo em prod, com o container ainda chamado `"canal-teste"`.

    A única das sete que a cerca antiga pegava. Continua tendo de reprovar.
    """
    _clonar_prod(raiz)


def _sonda_f(raiz: Path) -> None:
    """F — a material: um canal PLENAMENTE capaz de assinar, em produção, limpando as três
    cercas da versão antiga.

    Portão e segredo presentes; o portão escrito como em A (multi-linha, sem a vírgula que a
    regex exigia) e o nome do container vindo de um `local`, de modo que o literal
    `"canal-teste"` não aparece no arquivo — era o que fazia a cerca antiga pular o arquivo
    inteiro e sair com `rc=0`.
    """
    canal = _clonar_prod(raiz, locais='  nome_canal = "canal-teste"\n')

    def transformar(t: str) -> str:
        t = _trocar(
            t,
            _PORTAO_LIGADO,
            '{\n        name  = "CANAL_SIMULAR_RECEPTOR"\n        value = "1"\n      },',
        )
        return _trocar(t, '"canal-teste"', "local.nome_canal", contagem=3)

    _reescrever(canal, transformar)


def _sonda_f_endurecida(raiz: Path) -> None:
    """F endurecida (além de RV-371) — F mais o rótulo do recurso e a imagem renomeados.

    Prova que a identificação do canal não depende de um único sinal: o que sobra é o módulo
    que o container executa, e a regra fail-closed do nome não resolvível com o segredo junto.
    """
    canal = _clonar_prod(raiz, locais='  nome_canal = "chat-interno"\n')

    def transformar(t: str) -> str:
        t = _trocar(
            t,
            _PORTAO_LIGADO,
            '{\n        name  = "CANAL_SIMULAR_RECEPTOR"\n        value = "1"\n      },',
        )
        t = _trocar(t, '"canal-teste"', "local.nome_canal", contagem=3)
        t = t.replace('"canal_teste"', '"chat_interno"')
        t = t.replace("var.canal_teste_image_tag", "var.chat_interno_image_tag")
        return t

    _reescrever(canal, transformar)


def _sonda_g(raiz: Path) -> None:
    """G — `FAIXA_TESTE` reescrita com aspas simples e sem as âncoras."""
    _reescrever(
        raiz / _SERVIDOR,
        lambda t: _trocar(t, _FAIXA, "FAIXA_TESTE = re.compile(r'55119000000\\d{2}')"),
    )


def _sonda_h(raiz: Path) -> None:
    """H — âncoras e aspas mantidas, faixa alargada para `^\\d+$`: QUALQUER telefone, inclusive
    o de um beneficiário real."""
    _reescrever(raiz / _SERVIDOR, lambda t: _trocar(t, _FAIXA, 'FAIXA_TESTE = re.compile(r"^\\d+$")'))


def _sonda_i(raiz: Path) -> None:
    """I — `_simular_whatsapp` renomeada E o portão neutralizado (`if False:`)."""

    def transformar(t: str) -> str:
        t = _trocar(t, "_simular_whatsapp", "_simular_wa", contagem=3)
        return _trocar(t, _GATE_NA_ROTA, "        if False:")

    _reescrever(raiz / _SERVIDOR, transformar)


_SONDAS = {
    "A": (_sonda_a, checar_ligacao_fora_de_dev),
    "D": (_sonda_d, checar_ligacao_fora_de_dev),
    "E": (_sonda_e, checar_segredo_no_canal),
    "F": (_sonda_f, checar_segredo_no_canal),
    "F-endurecida": (_sonda_f_endurecida, checar_segredo_no_canal),
    "G": (_sonda_g, checar_cercas_no_codigo),
    "H": (_sonda_h, checar_cercas_no_codigo),
    "I": (_sonda_i, checar_cercas_no_codigo),
}


@pytest.mark.parametrize("sonda", sorted(_SONDAS))
def test_evasao_de_rv371_e_reprovada(tmp_path: Path, sonda: str) -> None:
    """Cada evasão do relatório reprova — e reprova pela cerca de que ela era o alvo."""
    aplicar, cerca_responsavel = _SONDAS[sonda]
    raiz = _montar(tmp_path)
    aplicar(raiz)

    achados_da_cerca = cerca_responsavel(raiz)
    assert achados_da_cerca, (
        f"evasão {sonda}: {cerca_responsavel.__name__} não reprovou — é exatamente o "
        f"fail-open que RV-371 mediu."
    )
    assert executar(raiz), f"evasão {sonda}: a cerca inteira saiu com rc=0."


def test_arvore_sintetica_sem_evasao_passa(tmp_path: Path) -> None:
    """Não-vacuidade: sem evasão, a mesma árvore passa. Sem isto, um teste que reprova tudo
    pareceria verde."""
    assert executar(_montar(tmp_path)) == []


def test_arvore_real_passa() -> None:
    """O repositório como está hoje passa — o PR liga a rota em `dev-sa-east-1`, e é lá que
    ela pode estar ligada."""
    assert executar(_RAIZ_REAL) == []


def test_faixa_sintetica_e_o_criterio_e_nao_apenas_a_ancora(tmp_path: Path) -> None:
    """O que a cerca cobra é a FAIXA, não o par `^`/`$`.

    RV-371/H passava porque a cerca antiga só verificava a presença das âncoras. `^\\d+$` tem
    as duas e aceita o telefone de qualquer beneficiário.
    """
    raiz = _montar(tmp_path)
    _sonda_h(raiz)
    achados = checar_cercas_no_codigo(raiz)
    assert any("faixa sintetica" in a for a in achados), achados


def test_indirecao_nao_resolvivel_no_portao_reprova(tmp_path: Path) -> None:
    """A regra que fecha as evasões que ninguém escreveu ainda: o que a cerca não consegue
    resolver, ela reprova — mesmo em dev, porque um portão invisível não é um portão."""
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _ENV_DEV / _CANAL_TF,
        lambda t: _trocar(t, _PORTAO_LIGADO, '{ name = "CANAL_SIMULAR_RECEPTOR", value = var.ligar_canal },'),
    )
    achados = checar_ligacao_fora_de_dev(raiz)
    assert any("nao resolvivel" in a for a in achados), achados


def test_destino_diferente_do_webhook_reprova(tmp_path: Path) -> None:
    """O envelope assinado só pode sair para o webhook do receptor — trocar o destino por
    qualquer outro (o motor, por exemplo) é perder a garantia de que a rota não alcança o
    engine, que é o que ADR-0049 D7 cobra."""
    raiz = _montar(tmp_path)
    _reescrever(
        raiz / _SERVIDOR,
        lambda t: _trocar(t, 'RECEPTOR + "/webhook"', 'ENGINE + "/engine/process-definition"'),
    )
    achados = checar_cercas_no_codigo(raiz)
    assert any("destino" in a or "envia o envelope assinado" in a for a in achados), achados
