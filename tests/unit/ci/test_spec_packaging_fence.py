"""Cerca de EMPACOTAMENTO da arvore `spec/` (IMPL-3 — mesma CLASSE do item 8 de `check_roteador_lucas`).

Todo artefato que o runtime carrega via `resolve_spec_dir()` tem de chegar ao wheel E a imagem.
O carregador (src/maezo/agents/__init__.py:187-248) resolve, nesta ordem, o checkout do repo e
DEPOIS a arvore `maezo/spec/` ADJACENTE AO PACOTE — e so chega la o que estiver citado no
`[tool.hatch.build.targets.wheel.force-include]` do pyproject.toml E na COPY correspondente do
deploy/Dockerfile. O padrao ja quebrou CINCO vezes (autonomia, amh, agents/processes, phi,
roteamento/lexico do Lucas) — cada vez achado piecemeal em producao. Esta cerca fecha a CLASSE:

1. um grep enumera os subtrees que o runtime cita (`resolve_spec_dir() / "a" / "b"` e
   `resolve_spec_agents_dir()`);
2. cada subtree citado tem de estar no force-include E na COPY, salvo dispensa explicita e
   justificada na allowlist deste arquivo;
3. subtree NOVO citado e nao classificado REPROVA o PR — ninguem redescobre isto num boot de
   producao (o gap A foi o phi-business-key-remediation, que falava para OFF em silencio:
   `load_phi_key_policy` nunca levanta, entao a ausencia do arquivo NAO da sinal nenhum).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

_RAIZ = Path(__file__).resolve().parents[3]
_SRC = _RAIZ / "src" / "maezo"
_PYPROJECT = _RAIZ / "pyproject.toml"
_DOCKERFILE = _RAIZ / "deploy" / "Dockerfile"

#: `resolve_spec_dir() / "a" / "b" / ...` — cadeia de segmentos LITERAIS (constantes de modulo
#: nao citadas no join entram como subtree pai, que e' o que interessa para empacotamento).
_SEG = r"\"([A-Za-z0-9_.\-]+)\"|'([A-Za-z0-9_.\-]+)'"
_PADRAO_JOIN = re.compile(r"resolve_spec_dir\(\)(?:\s*/\s*(?:" + _SEG + r"))+")
_SEG_EXTRAIDO = re.compile(_SEG)
#: `resolve_spec_agents_dir()` resolve `spec/agents` por construcao (agents/__init__.py:262).
_PADRAO_AGENTS = re.compile(r"resolve_spec_agents_dir\(\)")

#: Entradas de empacotamento, nos DOIS lugares obrigatorios.
_PADRAO_FORCE_INCLUDE = re.compile(r'^"(spec/[^"]+)"\s*=\s*"maezo/\1"\s*$', re.MULTILINE)
_PADRAO_COPY = re.compile(r"^COPY\s+(spec/\S+)\s+\./\S+\s*$", re.MULTILINE)


class Citacao(NamedTuple):
    """Um join literal que o runtime faz a partir de `resolve_spec_dir()`."""

    subtree: str
    arquivo: str  # relativo a raiz do repo
    linha: int


def _subtree_de(segmentos: list[str]) -> str | None:
    """`["policies", "autonomy", "L0-core.yaml"]` -> `spec/policies/autonomy`.

    Pega no maximo DOIS segmentos (o nivel de diretorio que se empacota) e derruba segmento
    final que parece nome de arquivo. Sem segmentos mapeaveis (join nao literal), retorna None.
    """
    pegos = list(segmentos[:2])
    while pegos and "." in pegos[-1]:
        pegos.pop()
    if not pegos:
        return None
    return "spec/" + "/".join(pegos)


def _citacoes() -> list[Citacao]:
    """Enumera os joins literais ao spec/ em todo `src/maezo/**.py`, com arquivo:linha."""
    achados: list[Citacao] = []
    for caminho in sorted(_SRC.rglob("*.py")):
        relativo = caminho.relative_to(_RAIZ).as_posix()
        texto = caminho.read_text(encoding="utf-8")
        for numero, linha in enumerate(texto.splitlines(), start=1):
            for casamento in _PADRAO_JOIN.finditer(linha):
                segmentos = [
                    grupo_1 or grupo_2
                    for grupo_1, grupo_2 in (
                        casamento_seg.groups() for casamento_seg in _SEG_EXTRAIDO.finditer(casamento.group(0))
                    )
                ]
                subtree = _subtree_de(segmentos)
                if subtree is not None:
                    achados.append(Citacao(subtree, relativo, numero))
            if _PADRAO_AGENTS.search(linha):
                achados.append(Citacao("spec/agents", relativo, numero))
    return achados


def _force_includes() -> set[str]:
    return set(_PADRAO_FORCE_INCLUDE.findall(_PYPROJECT.read_text(encoding="utf-8")))


def _copys() -> set[str]:
    return set(_PADRAO_COPY.findall(_DOCKERFILE.read_text(encoding="utf-8")))


def _coberto_por(entradas: set[str], subtree: str) -> bool:
    """`spec/processes` empacota `spec/processes/dmn` — subtree coberto pela PROPRIA entrada ou
    por qualquer ancestral dela listado."""
    return any(subtree == entrada or subtree.startswith(entrada + "/") for entrada in entradas)


# ---------------------------------------------------------------------------------------
# ALLOWLIST deliberada. Entrada AQUI dispensa o empacotamento de um subtree citado pelo
# runtime; cada entrada PRECISA de motivo. Chaves que o runtime nem cita (ex.: retention,
# que monta o caminho fora do `resolve_spec_dir`) tambem vivem aqui, para a dispensa ficar
# AUDITAVEL num lugar so. Trocar `empacotar` para True SEM adicionar o force-include + a COPY
# reprova no `test_todo_subtree_citado_esta_empacotado` — a cerca nao acredita em intencao.
# ---------------------------------------------------------------------------------------
_DISPENSADOS_DE_EMPACOTAMENTO: dict[str, dict[str, object]] = {
    # `resolve_spec_dir() / "schemas" / "tiss"` (src/maezo/tools/workers/tiss_schema.py:99):
    # os XSD do TISS sao dependencia EXTERNA (baixada/instalada fora do repo) — a arvore
    # `spec/schemas/` nem existe no checkout, entao nao ha o que empacotar.
    "spec/schemas/tiss": {
        "empacotar": False,
        "pendente_ratificacao": False,
        "motivo": "dependencia externa (XSD ANS); spec/schemas/ nem existe no checkout",
    },
    # `spec/policies/retention` NAO passa pelo `resolve_spec_dir()`: erasure_plan.py monta o
    # caminho direto (repo-relative + package-adjacent). DECISAO ABERTA documentada no modulo
    # (src/maezo/platform/lifecycle/erasure_plan.py:747-750): "kept so the module works if
    # `spec/policies/retention` is ever force-included; see the packet's open decision on
    # packaging, which is deliberately NOT taken here". Enquanto a decisao nao cai, a dispensa
    # fica registrada AQUI em vez de o subtree sumir do radar da cerca.
    "spec/policies/retention": {
        "empacotar": False,
        "pendente_ratificacao": True,
        "motivo": "decisao aberta de empacotamento (erasure_plan.py:747-750); artefato DRAFT",
    },
}

# ---------------------------------------------------------------------------------------
# Artefatos DRAFT que o runtime JA carrega e que, mesmo assim, VIAJAM empacotados. Status de
# ratificacao NAO e' dispensa: `pendente_ratificacao: true` documenta o estado, e a cerca
# CONTINUA exigindo force-include + COPY (o tiss-schema-pin DRAFT vai a bordo de proposito —
# quando a ANS ratificar, o loader em src/maezo/tools/workers/tiss_schema_pin.py:293 tem de
# achar o artefato e falhar com o reason code CERTO, nao com um FileNotFoundError de arquivo
# que nunca chegou ao container).
# ---------------------------------------------------------------------------------------
_PENDENTES_DE_RATIFICACAO: dict[str, str] = {
    "spec/policies/ans": (
        "tiss-schema-pin.yaml ainda DRAFT (tiss_schema_pin.py:293); a cerca EXIGE o "
        "empacotamento mesmo com o artefato pendente"
    ),
}

#: Ancora de nao-vacuidade: o que o grep TEM de achar hoje. Se o runtime parou de citar um
#: destes (ou passou a citar por outro mecanismo), atualiza as citacoes/allowlist — nao
#: apagues a entrada para o teste passar.
_SUBTREES_ESPERADOS = {
    "spec/policies/autonomy",
    "spec/policies/privacy",
    "spec/policies/ans",
    "spec/processes/dmn",
    "spec/schemas/tiss",
    "spec/agents",
}


# ---------------------------------------------------------------------------------------
# Nao-vacuidade.
# ---------------------------------------------------------------------------------------
def test_o_grep_encontra_os_subtrees_que_o_runtime_cita_hoje() -> None:
    citados = {citacao.subtree for citacao in _citacoes()}
    faltando = sorted(_SUBTREES_ESPERADOS - citados)
    assert not faltando, (
        "o grep da cerca parou de achar subtree(s) que ANTES eram citados pelo runtime: "
        f"{faltando}. Ou o join literal mudou de forma (atualiza `_PADRAO_JOIN`/"
        "`_SUBTREES_ESPERADOS`), ou o carregador saiu do ar — investiga antes de ajustar."
    )
    extras = sorted(citados - _SUBTREES_ESPERADOS - set(_DISPENSADOS_DE_EMPACOTAMENTO))
    assert not extras, (
        "o runtime passou a citar subtree(s) NOVO(S) do spec/ que a cerca nao classifica: "
        f"{extras}. Adiciona o force-include + a COPY correspondentes (padrao das 5 ocorrencias "
        "anteriores) OU justifica a dispensa em `_DISPENSADOS_DE_EMPACOTAMENTO`."
    )


def test_os_arquivos_citados_existem_no_repo() -> None:
    """O subtree exigido tem de existir no checkout — renomear o diretorio sem renomear o join
    e' exatamente o bricking que a cerca existe para pegar."""
    dispensados = set(_DISPENSADOS_DE_EMPACOTAMENTO)
    ausentes = sorted(
        subtree
        for subtree in {citacao.subtree for citacao in _citacoes()}
        if subtree not in dispensados and not (_RAIZ / subtree).is_dir()
    )
    assert not ausentes, (
        f"subtree(s) citado(s) pelo runtime NAO existem no checkout: {ausentes} — o loader "
        "vai resolver pelo checkout em dev e FALHAR (ou degradar em silencio) empacotado."
    )


# ---------------------------------------------------------------------------------------
# A cerca de classe.
# ---------------------------------------------------------------------------------------
def test_todo_subtree_citado_esta_empacotado() -> None:
    """Cada subtree que o runtime cita tem de estar no force-include E na COPY, salvo dispensa
    justificada. Falha nomeando o(s) lado(s) que faltam e onde o runtime cita."""
    force = _force_includes()
    copy = _copys()
    dispensados = set(_DISPENSADOS_DE_EMPACOTAMENTO)
    citacoes_por_subtree: dict[str, list[Citacao]] = {}
    for citacao in _citacoes():
        citacoes_por_subtree.setdefault(citacao.subtree, []).append(citacao)

    problemas: list[str] = []
    for subtree in sorted(citacoes_por_subtree):
        if subtree in dispensados:
            continue
        onde = ", ".join(f"{c.arquivo}:{c.linha}" for c in citacoes_por_subtree[subtree][:4])
        if not _coberto_por(force, subtree):
            problemas.append(
                f"{subtree} citado em {onde} MAS ausente do "
                f"`[tool.hatch.build.targets.wheel.force-include]` de pyproject.toml "
                f'(faltou `"{subtree}" = "maezo/{subtree}"`)'
            )
        if not _coberto_por(copy, subtree):
            problemas.append(
                f"{subtree} citado em {onde} MAS ausente do deploy/Dockerfile "
                f"(faltou `COPY {subtree} ./{subtree}`)"
            )
    assert not problemas, (
        "cerca de empacotamento do spec/ (classe das 5 ocorrencias anteriores) REPROVOU:\n- "
        + "\n- ".join(problemas)
    )


def test_force_include_e_copy_andam_em_par() -> None:
    """Todo force-include de spec/ tem COPY correspondente, e vice-versa — vale para TODAS as
    entradas, nao so para as que o runtime cita hoje (e' o padrao que ja quebrou 5 vezes)."""
    force = _force_includes()
    copy = _copys()
    so_force = sorted(force - copy)
    so_copy = sorted(copy - force)
    assert not so_force, (
        f"force-include sem COPY no deploy/Dockerfile: {so_force} — o `uv sync --no-editable` "
        "falha fechado com `FileNotFoundError: Forced include not found`"
    )
    assert not so_copy, (
        f"COPY no deploy/Dockerfile sem force-include no pyproject.toml: {so_copy} — a camada "
        "copia arvore que o wheel nao embarca (e `resolve_spec_dir()` prefere a adjacente ao pacote)"
    )


# ---------------------------------------------------------------------------------------
# Sanidade da allowlist.
# ---------------------------------------------------------------------------------------
def test_a_allowlist_so_tem_entradas_justificadas() -> None:
    for subtree, entrada in sorted(_DISPENSADOS_DE_EMPACOTAMENTO.items()):
        motivo = entrada.get("motivo")
        assert isinstance(motivo, str) and motivo.strip(), (
            f"dispensa de `{subtree}` sem motivo — a allowlist exige justificativa explicita"
        )
        existe = (_RAIZ / subtree).is_dir() or subtree in {c.subtree for c in _citacoes()}
        assert existe, (
            f"dispensa de `{subtree}` nao corresponde a subtree nem citado nem presente no "
            "repo — apaga a entrada em vez de manter allowlist morta"
        )


def test_pendente_ratificacao_nao_dispensa_empacotamento() -> None:
    """`pendente_ratificacao: true` documenta estado DRAFT; o empacotamento CONTINUA obrigatorio
    (Gap B vai a bordo de proposito, para o brick pós-ratificação ter o reason code certo)."""
    force = _force_includes()
    copy = _copys()
    for subtree, motivo in sorted(_PENDENTES_DE_RATIFICACAO.items()):
        assert subtree not in _DISPENSADOS_DE_EMPACOTAMENTO, (
            f"`{subtree}` nao pode estar simultaneamente pendente de ratificacao e dispensado: {motivo}"
        )
        assert subtree in force, (
            f"`{subtree}` pendente de ratificacao TEM de continuar no force-include ({motivo})"
        )
        assert subtree in copy, (
            f"`{subtree}` pendente de ratificacao TEM de continuar na COPY do Dockerfile ({motivo})"
        )
