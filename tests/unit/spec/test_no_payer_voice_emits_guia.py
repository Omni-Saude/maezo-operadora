"""Fence PERSP-AUTH-VOICE: nenhum artefato atribui ao lado pagador a emissao da guia TISS.

GAP-REGISTER `PERSP-AUTH-VOICE`: em SP-OP-AUTH-001 a operadora AUTORIZA (emite a autorizacao /
o numero de autorizacao); quem emite a guia TISS e' o prestador. O padrao de voz de prestador
"a operadora emite a guia" (ou uma variante equivalente — "o PROCESSO emite a guia",
"SP-OP-AUTH-001 emite a guia") ja apareceu em 3 artefatos reais desta cadeia: o rotulo BPMN de
`ST_EmitirAutorizacaoAuto`, o comentario de `spec/agents/rafael/agent.yaml` e o SYSTEM_PROMPT de
`src/maezo/agents/rafael/prompts.py` — o ultimo e o elemento de MAIOR risco porque um LLM le a
frase (nota do GAP-REGISTER).

Este modulo e' deliberadamente uma cerca lexical LEVE, distinta e sem relacao de escopo com
`maezo.platform.validation.perspective` (a cerca de PR-1/ADR-0040 D7, restrita a cadeia
CONTAS/RECURSO): aqui a classe de defeito e' "quem emite a GUIA TISS", nao "quem decide/aguarda
resposta do recurso". Varre `spec/`, `docs/`, `src/` e `tests/` por qualquer conjugacao de
"emitir a guia" cujo ator mais proximo (numa janela de 60 caracteres a esquerda) NAO seja o
prestador — "o prestador emite a guia" e a voz correta e nunca deve acusar; "a operadora emite a
guia" / "o processo emite a guia" / "SP-OP-AUTH-001 emite a guia" (ausencia de "prestador" na
janela) sempre acusam.

EXCECAO DECLARADA (nao lexical, por caminho): `docs/evidence-ledger.md` e `docs/review-queue.md`
sao registros append-only cuja narrativa CITA o padrao rejeitado para dizer o que foi corrigido
— exatamente a mesma convencao que `maezo.platform.validation.perspective` declara para a sua
propria cerca ("Where historical references go": a narrativa de uma remocao pertence ao ledger/
review-queue, nunca a um artefato de producao). Sem esta excecao, toda linha deste ledger que
DESCREVE o defeito corrigido se tornaria, ela mesma, uma nova ocorrencia do defeito — um pino que
nunca poderia ficar verde e documentado ao mesmo tempo.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_ROOTS = ("spec", "docs", "src", "tests")
_TEXT_SUFFIXES = {".bpmn", ".dmn", ".yaml", ".yml", ".md", ".py", ".json"}
_EXCLUDE_PARTS = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".ruff_cache"}
_SELF = Path(__file__).resolve()
# Registros append-only cuja narrativa cita o padrao rejeitado por design (ver docstring do
# modulo, "EXCECAO DECLARADA"). NAO e' um allowlist de excecoes de conteudo de producao — os
# dois caminhos sao literais e fixos, o mesmo par que `maezo.platform.validation.perspective`
# nomeia para a mesma finalidade.
_AUDIT_LOG_EXEMPT = {"docs/evidence-ledger.md", "docs/review-queue.md"}

# "emite"/"emitir"/"emitindo" ... " a guia" — qualquer conjugacao de "emitir a guia".
_ANTI_PATTERN = re.compile(r"emit\w*\s+a\s+guia", re.IGNORECASE)
# Atores candidatos na janela imediatamente antes do match; o ator mais PROXIMO decide.
_ACTOR = re.compile(r"prestador|operadora|\bprocesso\b|sp-op-[a-z]+-\d{3}", re.IGNORECASE)
_WINDOW = 60


def _iter_files() -> list[Path]:
    files: list[Path] = []
    for root in _ROOTS:
        base = _REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.resolve() == _SELF:
                continue
            if path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            if _EXCLUDE_PARTS & set(path.parts):
                continue
            if path.relative_to(_REPO).as_posix() in _AUDIT_LOG_EXEMPT:
                continue
            files.append(path)
    return files


def _hits(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    achados: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in _ANTI_PATTERN.finditer(line):
            janela = line[max(0, m.start() - _WINDOW) : m.start()]
            atores = list(_ACTOR.finditer(janela))
            # Sem ator identificavel na janela: fail-closed, acusa (ambiguidade nao e' aprovacao).
            if atores and atores[-1].group().lower() == "prestador":
                continue
            try:
                relativo = path.relative_to(_REPO)
            except ValueError:
                relativo = path
            achados.append(f"{relativo}:{lineno}: {line.strip()}")
    return achados


def test_nenhum_artefato_atribui_ao_pagador_a_emissao_da_guia() -> None:
    achados: list[str] = []
    for path in _iter_files():
        achados.extend(_hits(path))
    assert achados == [], (
        "GAP PERSP-AUTH-VOICE: padrao de voz de prestador ('X emite a guia' sem o prestador "
        "como ator) reintroduzido. Apenas o PRESTADOR emite a guia TISS; a operadora emite a "
        "AUTORIZACAO (numero de autorizacao). Achados:\n" + "\n".join(achados)
    )


def test_a_cerca_acusa_quando_o_padrao_e_reintroduzido(tmp_path: Path) -> None:
    """Nao-vacuidade: prova que a cerca de fato acusa as 2 formulacoes reais do defeito original."""
    plantado = tmp_path / "plantado.py"
    plantado.write_text(
        "# SP-OP-AUTH-001 emite a guia; Rafael apenas inicia o processo.\n"
        '"""...nesse caso o PROCESSO (nao voce) emite a guia."""\n',
        encoding="utf-8",
    )
    achados = _hits(plantado)
    assert len(achados) == 2, f"a cerca tem de acusar as 2 linhas plantadas, achou: {achados}"


def test_a_cerca_nao_acusa_a_voz_correta_do_prestador(tmp_path: Path) -> None:
    """A voz correta ('prestador emite a guia') nunca deve acusar, mesmo citando a operadora perto."""
    correto = tmp_path / "correto.py"
    correto.write_text(
        "# a operadora emite a autorizacao; o prestador emite a guia TISS.\n"
        "# o numero de autorizacao consta na guia TISS emitida pelo prestador.\n",
        encoding="utf-8",
    )
    achados = _hits(correto)
    assert achados == [], f"a cerca nao deveria acusar voz correta do prestador: {achados}"


def test_a_excecao_de_auditoria_e_restrita_aos_dois_arquivos_do_ledger() -> None:
    """PIN: a excecao NAO e' um allowlist generico de `docs/` — so' os 2 registros append-only.

    Sem este pino, um PR futuro poderia trocar `_AUDIT_LOG_EXEMPT` por algo amplo (ex.: um
    prefixo `docs/`) e esvaziar a cerca inteira em silencio; este teste faz essa mudanca
    quebrar a build citando o conjunto pinado.
    """
    assert {"docs/evidence-ledger.md", "docs/review-queue.md"} == _AUDIT_LOG_EXEMPT
