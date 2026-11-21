"""E-SF-09r: estado fechado entre os textos do cancelamento PF (Lucas) e o prazo regulatorio.

O que este arquivo PINA (somente leitura sobre `maezo.agents.lucas.cancelamento_pf`; nenhum arquivo
do Lucas e' tocado):

(a) Todo texto `TEXTO_*` de nivel de modulo em `cancelamento_pf.py` passa no `lint_copy` da politica
    de saida a pedido com os dois portoes FECHADOS (`promessa_ratificada=False`,
    `prazo_cancelamento_ratificado=False`): nenhuma promessa numerica e nenhum prazo regulatorio
    chega ao cliente. O conjunto de `TEXTO_*` achado nao pode ser vazio (o teste nao pode checar nada
    em silencio) e o achado por AST tem de bater com o achado em tempo de execucao.
(b) Os placeholders de formato desses textos ficam dentro de `{protocolo, data}`
    (`string.Formatter().parse`), e nenhum nome de SAIDA da DMN `cancel_sla`
    (`spec/processes/dmn/cancel_sla.dmn`: `sla_analise`, `sla_alerta`, `prazo_notificacao_previa`,
    `fonte_regulatoria`) aparece como placeholder ou como literal nos textos.
(c) Nenhum modulo do pacote `maezo.platform.saida_a_pedido` importa ou le `cancel_sla`: varredura de
    import por AST (resolvendo import relativo) e busca textual simples por "cancel_sla".

Cada cerca tem controle positivo (o detector pega um caso plantado) e caso sem falso positivo (um caso
inocuo passa). Os detectores aceitam texto/caminho/nome de pacote como argumento: a mutacao e' feita
apontando para copias, nunca editando `src/` no lugar.

LIMITES DECLARADOS:
  * O lint e' o da politica (`politica.py`), tal como esta. A lacuna conhecida de acento da regex de
    prazo ("dias uteis" sem acento escapa) e' achado registrado para outro dono: este arquivo NAO a
    corrige e NAO afirma nada que dependa dela, em nenhum sentido.
  * Os textos sao checados como TEMPLATE (antes do `format`); o valor de `{data}`/`{protocolo}` em
    tempo de execucao nao e' coberto aqui.
  * (c) cobre o codigo-fonte do pacote; leitura indireta via outro pacote nao e' coberta.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import string
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

from maezo.platform.saida_a_pedido.politica import lint_copy

RAIZ: Final[Path] = Path(__file__).resolve().parents[3]
DMN_CANCEL_SLA: Final[Path] = RAIZ / "spec" / "processes" / "dmn" / "cancel_sla.dmn"
MODULO_TEXTOS: Final[str] = "maezo.agents.lucas.cancelamento_pf"
PACOTE_SAIDA: Final[str] = "maezo.platform.saida_a_pedido"
PLACEHOLDERS_PERMITIDOS: Final[frozenset[str]] = frozenset({"protocolo", "data"})
SAIDAS_ESPERADAS_DMN: Final[frozenset[str]] = frozenset(
    {"sla_analise", "sla_alerta", "prazo_notificacao_previa", "fonte_regulatoria"}
)
_NS_DMN: Final[str] = "{https://www.omg.org/spec/DMN/20191111/MODEL/}"


# ---------------------------------------------------------------------------------------------------
# Helpers (parametrizados por texto/caminho/pacote, para controle positivo e mutacao por copia)
# ---------------------------------------------------------------------------------------------------


def _modulo_textos() -> ModuleType:
    return importlib.import_module(MODULO_TEXTOS)


def _textos_em_execucao(modulo: ModuleType) -> dict[str, str]:
    """Constantes `TEXTO_*` str de nivel de modulo, como o interpretador as ve."""
    return {
        nome: valor
        for nome, valor in vars(modulo).items()
        if nome.startswith("TEXTO_") and isinstance(valor, str)
    }


def _nomes_texto_no_fonte(fonte: str) -> set[str]:
    """Nomes `TEXTO_*` atribuidos no nivel de modulo do fonte (AST)."""
    nomes: set[str] = set()
    for no in ast.parse(fonte).body:
        alvos: list[ast.expr] = []
        if isinstance(no, ast.Assign):
            alvos = list(no.targets)
        elif isinstance(no, ast.AnnAssign):
            alvos = [no.target]
        for alvo in alvos:
            if isinstance(alvo, ast.Name) and alvo.id.startswith("TEXTO_"):
                nomes.add(alvo.id)
    return nomes


def _violacoes_lint(textos: dict[str, str]) -> dict[str, tuple[str, ...]]:
    violacoes: dict[str, tuple[str, ...]] = {}
    for nome, texto in textos.items():
        codigos = lint_copy(texto, promessa_ratificada=False, prazo_cancelamento_ratificado=False)
        if codigos:
            violacoes[nome] = codigos
    return violacoes


def _placeholders(texto: str) -> set[str]:
    """Nomes-base dos campos de formato (`{a.b}`/`{a[0]}` -> `a`); `{}` posicional vira ''."""
    nomes: set[str] = set()
    for _literal, campo, _spec, _conv in string.Formatter().parse(texto):
        if campo is None:
            continue
        nomes.add(campo.split(".", 1)[0].split("[", 1)[0])
    return nomes


def _saidas_dmn(caminho: Path) -> set[str]:
    arvore = ET.parse(caminho)
    return {
        nome
        for saida in arvore.iter(f"{_NS_DMN}output")
        for nome in (saida.get("name"), saida.get("label"))
        if nome
    }


def _violacoes_formato(textos: dict[str, str], saidas: set[str]) -> list[str]:
    violacoes: list[str] = []
    for nome, texto in textos.items():
        campos = _placeholders(texto)
        for fora in sorted(campos - PLACEHOLDERS_PERMITIDOS):
            violacoes.append(f"{nome}: placeholder fora do permitido {{{fora}}}")
        baixo = texto.lower()
        for saida in sorted(saidas):
            if saida in campos:
                violacoes.append(f"{nome}: placeholder de saida da DMN {{{saida}}}")
            elif saida.lower() in baixo:
                violacoes.append(f"{nome}: literal de saida da DMN {saida!r}")
    return violacoes


def _resolver_import(no: ast.ImportFrom, modulo_atual: str) -> str:
    """Nome absoluto do `from ... import`, resolvendo o relativo a partir do modulo atual."""
    if no.level == 0:
        return no.module or ""
    partes = modulo_atual.split(".")
    base = partes[: len(partes) - no.level]
    if no.module:
        base.append(no.module)
    return ".".join(base)


def _violacoes_cancel_sla(fonte: str, modulo_atual: str) -> list[str]:
    """Imports (AST, relativos resolvidos) ou leitura textual de `cancel_sla` num fonte."""
    violacoes: list[str] = []
    for no in ast.walk(ast.parse(fonte)):
        if isinstance(no, ast.Import):
            for alias in no.names:
                if "cancel_sla" in alias.name:
                    violacoes.append(f"import {alias.name}")
        elif isinstance(no, ast.ImportFrom):
            origem = _resolver_import(no, modulo_atual)
            for alias in no.names:
                completo = f"{origem}.{alias.name}"
                if "cancel_sla" in completo:
                    violacoes.append(f"from {origem} import {alias.name}")
    if "cancel_sla" in fonte:
        violacoes.append("texto 'cancel_sla' no fonte")
    return violacoes


def _fontes_do_pacote(nome_pacote: str) -> dict[str, Path]:
    """Modulo (nome absoluto) -> arquivo, para todo `*.py` do pacote, sem importar o pacote."""
    spec = importlib.util.find_spec(nome_pacote)
    assert spec is not None and spec.submodule_search_locations, nome_pacote
    pasta = Path(next(iter(spec.submodule_search_locations)))
    fontes: dict[str, Path] = {}
    for arquivo in sorted(pasta.rglob("*.py")):
        partes = arquivo.relative_to(pasta).with_suffix("").parts
        partes = partes[:-1] if partes[-1] == "__init__" else partes
        fontes[".".join((nome_pacote, *partes))] = arquivo
    return fontes


def _modulo_de_referencia(nome_modulo: str, arquivo: Path) -> str:
    """Para resolver import relativo: um `__init__` resolve como se fosse modulo dentro do pacote."""
    return f"{nome_modulo}.__init__" if arquivo.stem == "__init__" else nome_modulo


# ---------------------------------------------------------------------------------------------------
# (a) Textos passam no lint com os portoes fechados
# ---------------------------------------------------------------------------------------------------


def test_a_conjunto_de_textos_nao_e_vazio_e_bate_com_o_fonte() -> None:
    modulo = _modulo_textos()
    textos = _textos_em_execucao(modulo)
    assert textos, "nenhum TEXTO_* achado: o teste estaria checando nada"
    arquivo = Path(str(modulo.__file__))
    assert _nomes_texto_no_fonte(arquivo.read_text(encoding="utf-8")) == set(textos)


def test_a_todo_texto_passa_no_lint_com_portoes_fechados() -> None:
    textos = _textos_em_execucao(_modulo_textos())
    assert _violacoes_lint(textos) == {}


def test_a_controle_positivo_prazo_plantado_falha_no_lint() -> None:
    plantado = {"TEXTO_PLANTADO": "Concluimos em até 10 dias úteis. Protocolo: {protocolo}."}
    violacoes = _violacoes_lint(plantado)
    assert "TEXTO_PLANTADO" in violacoes
    assert "REGULATORY_DEADLINE_BEFORE_REG27" in violacoes["TEXTO_PLANTADO"]


def test_a_sem_falso_positivo_texto_inocuo_passa() -> None:
    inocuo = {"TEXTO_INOCUO": "Recebemos o seu pedido. Protocolo: {protocolo}."}
    assert _violacoes_lint(inocuo) == {}


def test_a_detector_de_nomes_no_fonte_acha_atribuicao_anotada_e_simples() -> None:
    fonte = 'TEXTO_A: str = "x"\nTEXTO_B = "y"\nOUTRO = "z"\ndef f():\n    TEXTO_C = "w"\n'
    assert _nomes_texto_no_fonte(fonte) == {"TEXTO_A", "TEXTO_B"}


# ---------------------------------------------------------------------------------------------------
# (b) Placeholders so' {protocolo, data}; nenhuma saida da DMN cancel_sla nos textos
# ---------------------------------------------------------------------------------------------------


def test_b_saidas_da_dmn_lidas_do_arquivo_real() -> None:
    saidas = _saidas_dmn(DMN_CANCEL_SLA)
    assert saidas >= SAIDAS_ESPERADAS_DMN, saidas


def test_b_textos_so_usam_placeholders_permitidos_e_nenhuma_saida_da_dmn() -> None:
    textos = _textos_em_execucao(_modulo_textos())
    assert textos
    assert _violacoes_formato(textos, _saidas_dmn(DMN_CANCEL_SLA)) == []


@pytest.mark.parametrize(
    ("texto", "trecho"),
    [
        ("Prazo: {prazo}. Protocolo {protocolo}.", "{prazo}"),
        ("Prazo: {sla_analise}.", "{sla_analise}"),
        ("Fonte: {fonte_regulatoria.texto}.", "{fonte_regulatoria}"),
        ("Valor {0} e {}.", "{0}"),
        ("Aviso conforme prazo_notificacao_previa.", "'prazo_notificacao_previa'"),
    ],
)
def test_b_controle_positivo_placeholder_ou_literal_plantado_falha(texto: str, trecho: str) -> None:
    violacoes = _violacoes_formato({"TEXTO_PLANTADO": texto}, set(SAIDAS_ESPERADAS_DMN))
    assert violacoes
    assert any(trecho in v for v in violacoes), violacoes


def test_b_sem_falso_positivo_placeholders_permitidos_e_chaves_escapadas() -> None:
    inocuo = {"TEXTO_INOCUO": "Cancelado a partir de {data} (protocolo {protocolo}). {{literal}}"}
    assert _violacoes_formato(inocuo, set(SAIDAS_ESPERADAS_DMN)) == []


# ---------------------------------------------------------------------------------------------------
# (c) O pacote saida_a_pedido nao importa nem le cancel_sla
# ---------------------------------------------------------------------------------------------------


def test_c_pacote_nao_importa_nem_le_cancel_sla() -> None:
    fontes = _fontes_do_pacote(PACOTE_SAIDA)
    assert fontes, "pacote sem modulos: o teste estaria checando nada"
    achados = {
        nome: v
        for nome, arquivo in fontes.items()
        if (
            v := _violacoes_cancel_sla(
                arquivo.read_text(encoding="utf-8"), _modulo_de_referencia(nome, arquivo)
            )
        )
    }
    assert achados == {}


@pytest.mark.parametrize(
    "fonte",
    [
        "import maezo.processes.cancel_sla\n",
        "from maezo.dmn import cancel_sla\n",
        "from .cancel_sla import regra\n",
        "from .. import cancel_sla\n",
        'CAMINHO = "spec/processes/dmn/cancel_sla.dmn"\n',
    ],
)
def test_c_controle_positivo_import_ou_leitura_plantada_falha(fonte: str) -> None:
    assert _violacoes_cancel_sla(fonte, f"{PACOTE_SAIDA}.plantado")


def test_c_import_relativo_e_resolvido_para_o_nome_absoluto() -> None:
    no = ast.parse("from ..dmn import x\n").body[0]
    assert isinstance(no, ast.ImportFrom)
    assert _resolver_import(no, f"{PACOTE_SAIDA}.plantado") == "maezo.platform.dmn"


def test_c_sem_falso_positivo_imports_inocuos_passam() -> None:
    fonte = (
        "import re\n"
        "from .politica import lint_copy\n"
        "from maezo.platform.saida_a_pedido.janela import JanelaAvaliada\n"
        'NOME = "pedido-cancelamento.yaml"\n'
    )
    assert _violacoes_cancel_sla(fonte, f"{PACOTE_SAIDA}.plantado") == []
