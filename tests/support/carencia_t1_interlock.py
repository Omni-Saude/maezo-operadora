"""INTERLOCK T1 (PROC-01 / EP-PROC-01a) — the CPT block of `carencia_check.dmn` masks the urgency ladder.

THE DEFECT, DERIVED FROM THE ARTEFACT (never asserted in prose). `spec/processes/dmn/carencia_check.dmn`
is `hitPolicy="FIRST"` (:59) and opens with `r01`/`r02` (:89-108), which carry the wildcard `-` on
`tipo_procedimento` and gate only on `dias_desde_adesao` + `cpt_declarada = true`. Every other rule,
including the 24h urgency ladder `r03`/`r04` (:115-134), requires `cpt_declarada = false`. So with a CPT
declared, `urgencia_emergencia / 30 / true` lands on `r01` -> `false / 700` instead of `r04` -> `true / 0`.

WHAT IS MITIGATED TODAY, AND WHY THAT IS NOT ENOUGH. The table is not evaluated in production because
(a) none of its three inputs exists in SP-OP-AUTH-001's start contract, and (b) `carencia_check` is
`ratificado: false` in `auth-criteria-ratification.yaml`. Both are acts OUTSIDE this window — a producer
of the inputs, or a pure DATA flip of the manifest — and either one turns the masked verdict live. This
module turns that accidental mitigation into an intentional, tested one: the guards in
`tests/unit/spec/test_carencia_check_t1_interlock.py` refuse both acts while the masking stands.

WHAT LIFTS THE INTERLOCK — EXACTLY TWO CONDITIONS, NOTHING ELSE (PROC-01, "Condicao EXATA"):

  (i)  the live table no longer reproduces the masking (`mascaramento_t1_ativo` is False) BECAUSE the
       candidate was applied and ratified: a copy of `carencia-check-shadow-candidate.yaml` archived
       OUTSIDE `spec/processes/`, carrying `ratificado: true` + non-placeholder `revisor`/`ratificado_em`
       and `tabela_viva.sha256` equal to the pre-fix digest `SHA256_TABELA_PRE_FIX`;
  (ii) `carencia_check` was RETIRED: the `.dmn` left `spec/processes/dmn/`, the consumer
       `_criterio_regulatorio` evaluates an `rco_`-prefixed table (ADR-0075), and
       `config/integrations/rco/rules.lock.json` is in `"estado": "fixado"`.

There is no engineering valve: no flag, no environment variable, no allowlist, no `xfail`/`skip`. A live
table edited WITHOUT the ratified candidate (predicate false, condition (i) unproven) keeps the interlock
shut — `interlock_t1_vigente` is the single place that decides, and it fails closed.

WHAT THIS MODULE IS NOT. It holds no business rule: every verdict comes from FIRST-hit evaluation of the
REAL artefacts through `tests.support.dmn_first_hit` (a parser of the XML, not an engine), and every
ratification answer comes from the REAL loader `maezo.tools.workers.auth_criteria`. The only facts it
pins are the ones PROC-01 freezes (the boundary-day grid, the pre-fix digest, the producer ratchet).
"""

from __future__ import annotations

import ast
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from maezo.tools.workers.auth_criteria import load_criteria_sources
from tests.support.dmn_first_hit import (
    REPO_ROOT,
    DecisionTable,
    UnreadableEntryError,
    evaluate,
    load_candidate_ratification,
    load_manifest,
    read_candidate_table,
    read_live_table,
)

# -------------------------------------------------------------------------------------------------
# Stable failure tokens — a CI log line that carries one of these names the interlock that refused.
# -------------------------------------------------------------------------------------------------

TOKEN_RATIFICACAO = "T1-INTERLOCK-RATIFICACAO"
TOKEN_BLOQUEIO = "T1-INTERLOCK-BLOQUEIO"
TOKEN_ENTRADAS = "T1-INTERLOCK-ENTRADAS"
TOKEN_TRILHA = "T1-INTERLOCK-TRILHA"
TOKEN_L0 = "T1-INTERLOCK-L0"
TOKEN_ADMISSIBILIDADE = "T1-INTERLOCK-ADMISSIBILIDADE"


class InterlockT1ViolationError(AssertionError):
    """An interlock check refused. The message always starts with one of the `TOKEN_*` above."""


# -------------------------------------------------------------------------------------------------
# Frozen facts (PROC-01 / GUARD-TEST-SPECS §2)
# -------------------------------------------------------------------------------------------------

DMN_REL = "spec/processes/dmn"
TABELA_REL = f"{DMN_REL}/carencia_check.dmn"
MANIFESTO_RATIFICACAO_REL = f"{DMN_REL}/auth-criteria-ratification.yaml"
ADMISSIBILIDADE_REL = f"{DMN_REL}/auth_admissibility.dmn"
CANDIDATO_NOME = "carencia-check-shadow-candidate.yaml"
CANDIDATO_REL = f"{DMN_REL}/{CANDIDATO_NOME}"
AUTH_WORKER_REL = "src/maezo/tools/workers/auth.py"
RCO_LOCK_REL = "config/integrations/rco/rules.lock.json"
ENGINE_SCHEMAS_REL = "src/maezo/gateway/engine_schemas.py"
ENGINE_SCHEMAS_JAVA_REL = "src/maezo/portal/engine/java/src/main/resources/engine-schemas-v1.json"
AUTH_BPMN_REL = "spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn"
AUTH_CONTRATO_REL = "docs/processes/contracts/SP-OP-AUTH-001.md"

#: The decision id whose ratification the interlock governs, and the A1b data entry that blocks it.
FONTE = "carencia_check"
ENTRADA_BLOQUEIO = "t1_mascaramento_cpt_urgencia"

#: Byte digest of `carencia_check.dmn` as PROC-01 verified it (== the candidate's `tabela_viva.sha256`).
#: Condition (i) binds the archived, ratified candidate to THIS content: a ratification of the
#: correction is a ratification of a diff against these bytes, never against an arbitrary later table.
SHA256_TABELA_PRE_FIX = "2fb3aafe2a5953ffea95b57e1a9aaf04bf4e48899896be790d0dcd921cc85a21"

#: Every day boundary the live table declares (1, 180, 300, 730) plus both neighbours, and 0.
GRADE_DIAS: tuple[int, ...] = (0, 1, 2, 179, 180, 181, 299, 300, 301, 729, 730, 731)
TIPO_URGENCIA = "urgencia_emergencia"
#: Types OUTSIDE the declared domain — the half-space where the L0 catch-all must answer.
TIPOS_FORA_DO_DOMINIO: tuple[str, ...] = ("", "internacao")

ENTRADAS: tuple[str, ...] = ("tipo_procedimento", "dias_desde_adesao", "cpt_declarada")
SAIDAS: frozenset[str] = frozenset({"carencia_cumprida", "prazo_restante_dias", "fonte"})
#: The L0 instruction every `false` verdict carries to the auditor (`carencia_check.dmn:13-15`).
TOKEN_FONTE_L0 = "Nunca negativa automatica"

#: `auth_admissibility` outcomes that never decide against the beneficiary (the table has no denial
#: output, `auth_admissibility.dmn:11-13`).
RESULTADOS_ADMISSIVEIS: frozenset[str] = frozenset({"NAO_REQUER", "SEGUE_ANALISE", "PENDENTE_DOCUMENTACAO"})

#: A2a ratchet (4), frozen at the state PROC-01 verified. Files in `src/`/`spec/` that NAME
#: `cpt_declarada` or `dias_desde_adesao`: the consumer, the evidence-sweep fixture, the table itself
#: and its candidate. A new producer is a new file here — and that is exactly what must stay red.
RATCHET_CPT_DIAS: frozenset[str] = frozenset(
    {
        AUTH_WORKER_REL,
        "src/maezo/platform/evidence/dmn_sweep.py",
        TABELA_REL,
        CANDIDATO_REL,
    }
)
#: `tipo_procedimento` is a generic name, so its ratchet is scoped to where a PRODUCER for
#: SP-OP-AUTH-001 would live: the gateway, the agents, the AUTH workers, the runtime, the AUTH BPMN.
RATCHET_TIPO: frozenset[str] = frozenset({AUTH_WORKER_REL})

#: Where a producer of `cpt_declarada`/`dias_desde_adesao` could appear: code, spec, and the
#: deployment/config/tooling trees (a deploy that injects the inputs is a producer too). Missing
#: directories are simply empty.
BASES_RATCHET_CPT_DIAS: tuple[str, ...] = ("src", "spec", "deploy", "config", "tools")
#: The env override of the ratification-manifest path (`auth_criteria.MANIFEST_PATH_ENV`). PROC-01's
#: residual risk: if a deploy sets it, A1 checks the wrong file. No file under `deploy/` may name it.
MANIFEST_OVERRIDE_ENV = "MAEZO_AUTH_CRITERIA_MANIFEST_PATH"

_PALAVRA_CPT_DIAS = re.compile(r"\b(?:cpt_declarada|dias_desde_adesao)\b")
_PALAVRA_TIPO = re.compile(r"\btipo_procedimento\b")
_QUALQUER_ENTRADA = re.compile(r"\b(?:tipo_procedimento|dias_desde_adesao|cpt_declarada)\b")
_DIRETORIOS_IGNORADOS = frozenset({"__pycache__", "node_modules", ".mypy_cache", ".ruff_cache"})


# =================================================================================================
# The predicate
# =================================================================================================


def _entradas(tabela: DecisionTable, tipo: str, dias: int, cpt: bool) -> dict[str, Any]:
    if set(tabela.input_names) != set(ENTRADAS) or len(tabela.input_names) != len(ENTRADAS):
        raise UnreadableEntryError(
            f"{tabela.source.name}: inputs are {tabela.input_names}, expected exactly {ENTRADAS} — the "
            "T1 predicate refuses to guess what a re-shaped table means"
        )
    por_nome = {"tipo_procedimento": tipo, "dias_desde_adesao": dias, "cpt_declarada": cpt}
    return {name: por_nome[name] for name in tabela.input_names}


def _decisao(tabela: DecisionTable, tipo: str, dias: int, cpt: bool) -> tuple[Any, Any]:
    saidas = evaluate(tabela, _entradas(tabela, tipo, dias, cpt)).saidas
    return saidas["carencia_cumprida"], saidas["prazo_restante_dias"]


def mascaramento_t1_ativo(tabela: DecisionTable) -> bool:
    """True iff, on some boundary day, urgency WITH a CPT does not read the same ladder as WITHOUT one.

    Compares the two decision-bearing outputs (`carencia_cumprida`, `prazo_restante_dias`) of
    `(urgencia_emergencia, d, cpt=true)` and `(urgencia_emergencia, d, cpt=false)` for every `d` in
    `GRADE_DIAS`. Independent of rule ids and rule order, so a renamed or reordered table is judged by
    what it DECIDES, not by how it is written.
    """
    return any(
        _decisao(tabela, TIPO_URGENCIA, dias, True) != _decisao(tabela, TIPO_URGENCIA, dias, False)
        for dias in GRADE_DIAS
    )


# =================================================================================================
# The two lifting conditions — and the single place that decides
# =================================================================================================


def _candidatos_arquivados(raiz: Path) -> Iterator[Path]:
    """Copies of the candidate manifest archived OUTSIDE `spec/processes/` (EP-PROC-01c B5).

    Searched under `docs/` and `spec/` — where the owner's archive destination is to be defined
    (GUARD-TEST-SPECS §3.2 B5, e.g. `docs/decisions/...`). `docs/audits/` is excluded: it is
    gitignored, so a file there exists on one workstation and never in CI.
    """
    excluidos = (raiz / "spec" / "processes", raiz / "docs" / "audits")
    for base in (raiz / "docs", raiz / "spec"):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob(CANDIDATO_NOME)):
            if path.is_file() and not any(path.is_relative_to(e) for e in excluidos):
                yield path


def _candidato_ratificado_contra_a_tabela_pre_fix(path: Path) -> bool:
    if not load_candidate_ratification(path).ratificado:
        return False
    try:
        bloco = load_manifest(path).get("tabela_viva")
    except Exception:  # fail-closed: an unreadable archive proves nothing
        return False
    return isinstance(bloco, dict) and bloco.get("sha256") == SHA256_TABELA_PRE_FIX


def _viva_decide_como_o_candidato(viva: DecisionTable, candidato: Path) -> bool:
    """The live table decides EXACTLY like the ratified candidate — `carencia_cumprida` and
    `prazo_restante_dias` — on every cell of the grid. Without this, any non-masking table (an edit
    nobody ratified) plus an archived ratified candidate would satisfy (i). Fails closed."""
    try:
        regras_candidatas = read_candidate_table(candidato, viva)
        for tipo in _tipos_da_grade(viva):
            for dias in GRADE_DIAS:
                for cpt in (True, False):
                    if _decisao(viva, tipo, dias, cpt) != _decisao(regras_candidatas, tipo, dias, cpt):
                        return False
    except Exception:  # fail-closed: a candidate that cannot be read against the table proves nothing
        return False
    return True


def condicao_i_satisfeita(raiz: Path = REPO_ROOT) -> bool:
    """(i) live table no longer masks BECAUSE the ratified candidate was applied: the candidate is
    archived, ratified, bound to the pre-fix bytes, AND the live table decides exactly like it."""
    tabela = raiz / TABELA_REL
    if not tabela.is_file():
        return False
    viva = read_live_table(tabela)
    if mascaramento_t1_ativo(viva):
        return False
    return any(
        _candidato_ratificado_contra_a_tabela_pre_fix(p) and _viva_decide_como_o_candidato(viva, p)
        for p in _candidatos_arquivados(raiz)
    )


def _string_de_modulo(arvore: ast.Module, nome: str) -> str | None:
    for no in arvore.body:
        if isinstance(no, ast.Assign):
            alvos = [alvo.id for alvo in no.targets if isinstance(alvo, ast.Name)]
        elif isinstance(no, ast.AnnAssign) and isinstance(no.target, ast.Name):
            alvos = [no.target.id]
        else:
            continue
        valor = no.value
        if nome in alvos and isinstance(valor, ast.Constant) and isinstance(valor.value, str):
            return valor.value
    return None


def tabela_do_criterio_regulatorio(raiz: Path = REPO_ROOT) -> str | None:
    """The decision key `_criterio_regulatorio` evaluates, read from the worker's SOURCE (AST).

    Resolves the first argument of every `self._evaluate_dmn(...)` call inside `_criterio_regulatorio`
    (a string literal, or a module-level string constant). Returns None — "not proven" — unless there
    is exactly one such key: an ambiguous or unreadable consumer never satisfies condition (ii).
    """
    fonte = raiz / AUTH_WORKER_REL
    if not fonte.is_file():
        return None
    arvore = ast.parse(fonte.read_text(encoding="utf-8"))
    chaves: set[str] = set()
    funcoes = [
        no
        for no in ast.walk(arvore)
        if isinstance(no, ast.FunctionDef | ast.AsyncFunctionDef) and no.name == "_criterio_regulatorio"
    ]
    if len(funcoes) != 1:
        return None
    for no in ast.walk(funcoes[0]):
        if not (
            isinstance(no, ast.Call)
            and isinstance(no.func, ast.Attribute)
            and no.func.attr == "_evaluate_dmn"
            and no.args
        ):
            continue
        primeiro = no.args[0]
        if isinstance(primeiro, ast.Constant) and isinstance(primeiro.value, str):
            chaves.add(primeiro.value)
        elif isinstance(primeiro, ast.Name):
            valor = _string_de_modulo(arvore, primeiro.id)
            if valor is None:
                return None
            chaves.add(valor)
        else:
            return None
    return next(iter(chaves)) if len(chaves) == 1 else None


def _chave_de_string(arvore: ast.Module, no: ast.expr) -> str | None:
    if isinstance(no, ast.Constant) and isinstance(no.value, str):
        return no.value
    if isinstance(no, ast.Name):
        return _string_de_modulo(arvore, no.id)
    return None


def fontes_consultadas_pelo_criterio_regulatorio(raiz: Path = REPO_ROOT) -> frozenset[str] | None:
    """Every ratification key `_criterio_regulatorio` hands to `_gate_on_ratification(consulted=...)`,
    read from the worker's SOURCE (AST). This is the set of manifest `fontes` whose ratification
    actually opens the REGULATORIO criterion — so it, not a literal `"carencia_check"`, is what the
    ratification interlock must watch. Returns None — "not proven" — if the function, the call or any
    element of `consulted=` cannot be resolved to a string."""
    fonte = raiz / AUTH_WORKER_REL
    if not fonte.is_file():
        return None
    arvore = ast.parse(fonte.read_text(encoding="utf-8"))
    funcoes = [
        no
        for no in ast.walk(arvore)
        if isinstance(no, ast.FunctionDef | ast.AsyncFunctionDef) and no.name == "_criterio_regulatorio"
    ]
    if len(funcoes) != 1:
        return None
    chaves: set[str] = set()
    chamadas = 0
    for no in ast.walk(funcoes[0]):
        if not (
            isinstance(no, ast.Call)
            and isinstance(no.func, ast.Name)
            and no.func.id == "_gate_on_ratification"
        ):
            continue
        chamadas += 1
        consulted = next((kw.value for kw in no.keywords if kw.arg == "consulted"), None)
        if not isinstance(consulted, ast.List | ast.Tuple) or not consulted.elts:
            return None
        for elemento in consulted.elts:
            chave = _chave_de_string(arvore, elemento)
            if chave is None:
                return None
            chaves.add(chave)
    return frozenset(chaves) if chamadas else None


def condicao_ii_satisfeita(raiz: Path = REPO_ROOT) -> bool:
    """(ii) `carencia_check` retired: table gone, consumer on an `rco_` table, K1 lock `fixado`."""
    if (raiz / TABELA_REL).exists():
        return False
    chave = tabela_do_criterio_regulatorio(raiz)
    if chave is None or not chave.startswith("rco_"):
        return False
    lock = raiz / RCO_LOCK_REL
    try:
        estado = json.loads(lock.read_text(encoding="utf-8")).get("estado")
    except Exception:  # fail-closed: an unreadable lock proves nothing
        return False
    return estado == "fixado"


def interlock_t1_vigente(raiz: Path = REPO_ROOT) -> bool:
    """THE single decision: the interlock stands unless condition (i) or condition (ii) holds."""
    return not (condicao_i_satisfeita(raiz) or condicao_ii_satisfeita(raiz))


# =================================================================================================
# A1 — ratification interlock (CI layer A1a, data layer A1b)
# =================================================================================================


def verificar_interlock_de_ratificacao(manifesto: Path, raiz: Path = REPO_ROOT) -> None:
    """A1a. Refuses (raises) when `carencia_check` reads as ratified while the interlock stands.

    `is_ratified` is asked of the REAL loader, so every runtime suppression (including A1b's entry)
    is honoured — this check is the CI-side backstop that holds even if that entry is deleted.
    """
    if not interlock_t1_vigente(raiz):
        return
    consultadas = fontes_consultadas_pelo_criterio_regulatorio(raiz)
    if consultadas is None:
        raise InterlockT1ViolationError(
            f"{TOKEN_RATIFICACAO}: cannot resolve the `consulted=` keys of `_criterio_regulatorio` in "
            f"{AUTH_WORKER_REL} — the sources whose ratification opens REGULATORIO are unknown, so none "
            "may be trusted while the interlock stands"
        )
    sources = load_criteria_sources(manifesto)
    ratificadas = sorted(chave for chave in consultadas | {FONTE} if sources.is_ratified(chave))
    if ratificadas:
        raise InterlockT1ViolationError(
            f"{TOKEN_RATIFICACAO}: {ratificadas} read as ratified in {manifesto.name} (REGULATORIO "
            f"consults {sorted(consultadas)}) while {TABELA_REL} still masks the urgency ladder under "
            "CPT (urgencia_emergencia/30/true -> r01). Only PROC-01 condition (i) — candidate applied "
            "AND ratified — or (ii) — table retired for the rco K1 package — may lift this."
        )


def verificar_bloqueio_declarado(manifesto: Path, raiz: Path = REPO_ROOT) -> None:
    """A1b. While the interlock stands, the manifest must DECLARE the block and the loader must HONOUR it."""
    if not interlock_t1_vigente(raiz):
        return
    data = yaml.safe_load(manifesto.read_text(encoding="utf-8"))
    secao = data.get("criterios_nao_cobertos") if isinstance(data, dict) else None
    entrada = secao.get(ENTRADA_BLOQUEIO) if isinstance(secao, dict) else None
    if not isinstance(entrada, dict):
        raise InterlockT1ViolationError(
            f"{TOKEN_BLOQUEIO}: {manifesto.name} has no `criterios_nao_cobertos.{ENTRADA_BLOQUEIO}` "
            f"entry while {TABELA_REL} still masks the urgency ladder under CPT"
        )
    alvos = entrada.get("bloqueia_ratificacao_de")
    alvos = [alvos] if isinstance(alvos, str) else alvos
    if not isinstance(alvos, list) or FONTE not in alvos or entrada.get("pertence_a") != FONTE:
        raise InterlockT1ViolationError(
            f"{TOKEN_BLOQUEIO}: `{ENTRADA_BLOQUEIO}` must declare `pertence_a: {FONTE}` and "
            f"`bloqueia_ratificacao_de: {FONTE}` (got {entrada!r})"
        )
    if not str(entrada.get("revisor_necessario") or "").strip():
        raise InterlockT1ViolationError(
            f"{TOKEN_BLOQUEIO}: `{ENTRADA_BLOQUEIO}` names no `revisor_necessario`"
        )
    if ENTRADA_BLOQUEIO not in load_criteria_sources(manifesto).blocked_by_uncovered.get(FONTE, ()):
        raise InterlockT1ViolationError(
            f"{TOKEN_BLOQUEIO}: the loader does not honour `{ENTRADA_BLOQUEIO}` — the declaration is "
            "inert text (the M-3 defect class) or the manifest was refused wholesale"
        )


# =================================================================================================
# A2a — no producer of the three inputs while the interlock stands
# =================================================================================================


@dataclass(frozen=True, slots=True)
class Acusacao:
    """One producer finding: which check fired, on which repo-relative file, and what it saw."""

    verificacao: str
    arquivo: str
    detalhe: str


def _strings(no: ast.AST) -> Iterator[str]:
    for filho in ast.walk(no):
        if isinstance(filho, ast.Constant) and isinstance(filho.value, str):
            yield filho.value


def _acusar_contrato_de_start_python(raiz: Path) -> list[Acusacao]:
    """(1a) The typed start contract of `Autorizacao_Previa` (`RAFAEL_START` today), read by AST."""
    path = raiz / ENGINE_SCHEMAS_REL
    if not path.is_file():
        return [Acusacao("start-python", ENGINE_SCHEMAS_REL, "file absent — the anchor cannot be checked")]
    arvore = ast.parse(path.read_text(encoding="utf-8"))
    atribuicoes = [
        no
        for no in arvore.body
        if isinstance(no, ast.Assign | ast.AnnAssign)
        and no.value is not None
        and "Autorizacao_Previa" in set(_strings(no.value))
    ]
    if not atribuicoes:
        return [Acusacao("start-python", ENGINE_SCHEMAS_REL, "no `Autorizacao_Previa` start contract found")]
    nomes = {palavra for no in atribuicoes for s in _strings(no) for palavra in s.split()}
    return [
        Acusacao("start-python", ENGINE_SCHEMAS_REL, f"`{nome}` is in the Autorizacao_Previa start contract")
        for nome in sorted(nomes & set(ENTRADAS))
    ]


def _acusar_contrato_de_start_java(raiz: Path) -> list[Acusacao]:
    """(1b) The Java mirror of the same contracts — every SP-OP-AUTH-001 schema."""
    path = raiz / ENGINE_SCHEMAS_JAVA_REL
    if not path.is_file():
        return [Acusacao("start-java", ENGINE_SCHEMAS_JAVA_REL, "file absent — the anchor cannot be checked")]
    schemas = [
        s
        for s in json.loads(path.read_text(encoding="utf-8")).get("schemas", [])
        if isinstance(s, dict) and s.get("process_key") == "SP-OP-AUTH-001"
    ]
    if not schemas:
        return [Acusacao("start-java", ENGINE_SCHEMAS_JAVA_REL, "no SP-OP-AUTH-001 schema found")]
    return [
        Acusacao("start-java", ENGINE_SCHEMAS_JAVA_REL, f"`{f['name']}` in schema {s.get('schema_id')!r}")
        for s in schemas
        for f in s.get("fields", [])
        if isinstance(f, dict) and f.get("name") in ENTRADAS
    ]


_ELEMENTOS_DE_DADOS_BPMN = frozenset({"inputParameter", "outputParameter", "formField", "in", "out"})


def _acusar_bpmn(raiz: Path) -> list[Acusacao]:
    """(2) `camunda:inputParameter`/`outputParameter`/`formField`/`in`/`out` of the AUTH BPMN."""
    path = raiz / AUTH_BPMN_REL
    if not path.is_file():
        return [Acusacao("bpmn", AUTH_BPMN_REL, "file absent — the anchor cannot be checked")]
    acusacoes: list[Acusacao] = []
    for elemento in ET.parse(path).getroot().iter():
        local = elemento.tag.rpartition("}")[2]
        if local not in _ELEMENTOS_DE_DADOS_BPMN:
            continue
        textos = [*elemento.attrib.values(), "".join(elemento.itertext())]
        for nome in sorted({m for t in textos for m in _QUALQUER_ENTRADA.findall(t)}):
            acusacoes.append(Acusacao("bpmn", AUTH_BPMN_REL, f"`{nome}` in <{local} {elemento.attrib}>"))
    return acusacoes


def _acusar_contrato_documental(raiz: Path) -> list[Acusacao]:
    """(3) The SP-OP-AUTH-001 contract keeps "Sem fonte hoje" on each of the three input rows."""
    path = raiz / AUTH_CONTRATO_REL
    if not path.is_file():
        return [Acusacao("contrato", AUTH_CONTRATO_REL, "file absent — the anchor cannot be checked")]
    linhas = path.read_text(encoding="utf-8").splitlines()
    acusacoes: list[Acusacao] = []
    for nome in ENTRADAS:
        linhas_da_entrada = [
            (numero, linha)
            for numero, linha in enumerate(linhas, start=1)
            if linha.startswith(f"| `{nome}` |")
        ]
        if len(linhas_da_entrada) != 1:
            acusacoes.append(
                Acusacao(
                    "contrato",
                    AUTH_CONTRATO_REL,
                    f"`{nome}`: expected 1 input row, got {len(linhas_da_entrada)}",
                )
            )
        elif "Sem fonte hoje" not in linhas_da_entrada[0][1]:
            acusacoes.append(
                Acusacao(
                    "contrato",
                    f"{AUTH_CONTRATO_REL}:{linhas_da_entrada[0][0]}",
                    f"`{nome}` lost 'Sem fonte hoje' — a source for it is being declared",
                )
            )
    return acusacoes


def _arquivos_de_texto(raiz: Path, bases: Iterable[str]) -> Iterator[tuple[str, str]]:
    for base in bases:
        alvo = raiz / base
        candidatos = [alvo] if alvo.is_file() else sorted(alvo.rglob("*")) if alvo.is_dir() else []
        for path in candidatos:
            if not path.is_file() or _DIRETORIOS_IGNORADOS & set(path.relative_to(raiz).parts):
                continue
            try:
                texto = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # binary asset: cannot name a process variable
            yield path.relative_to(raiz).as_posix(), texto


def _escopo_tipo(raiz: Path) -> list[str]:
    workers = sorted(
        p.relative_to(raiz).as_posix() for p in (raiz / "src/maezo/tools/workers").glob("auth*.py")
    )
    return ["src/maezo/gateway", "src/maezo/agents", "src/maezo/runtime", AUTH_BPMN_REL, *workers]


def _acusar_ratchet(raiz: Path) -> list[Acusacao]:
    """(4) The set of files that name the inputs must be EXACTLY the frozen set — no more, no fewer."""
    cpt_dias = {
        rel
        for rel, texto in _arquivos_de_texto(raiz, BASES_RATCHET_CPT_DIAS)
        if _PALAVRA_CPT_DIAS.search(texto)
    }
    tipo = {rel for rel, texto in _arquivos_de_texto(raiz, _escopo_tipo(raiz)) if _PALAVRA_TIPO.search(texto)}
    acusacoes: list[Acusacao] = []
    for encontrados, congelado, rotulo in (
        (cpt_dias, RATCHET_CPT_DIAS, "cpt_declarada/dias_desde_adesao"),
        (tipo, RATCHET_TIPO, "tipo_procedimento"),
    ):
        for rel in sorted(encontrados - congelado):
            acusacoes.append(Acusacao("ratchet", rel, f"new file naming {rotulo} (a producer?)"))
        for rel in sorted(congelado - encontrados):
            acusacoes.append(
                Acusacao(
                    "ratchet", rel, f"frozen file no longer names {rotulo} — the ratchet must be re-reviewed"
                )
            )
    return acusacoes


def acusar_produtores_de_entradas(raiz: Path = REPO_ROOT) -> tuple[Acusacao, ...]:
    """Every finding of the four A2a checks against the tree rooted at `raiz`. Empty == clean."""
    return (
        *_acusar_contrato_de_start_python(raiz),
        *_acusar_contrato_de_start_java(raiz),
        *_acusar_bpmn(raiz),
        *_acusar_contrato_documental(raiz),
        *_acusar_ratchet(raiz),
    )


def acusar_override_de_manifesto_em_deploy(raiz: Path = REPO_ROOT) -> tuple[str, ...]:
    """Files under `deploy/` naming `MANIFEST_OVERRIDE_ENV` — each would point the runtime ratification
    gate at a manifest A1a/A1b never read."""
    return tuple(
        rel for rel, texto in _arquivos_de_texto(raiz, ("deploy",)) if MANIFEST_OVERRIDE_ENV in texto
    )


def verificar_interlock_de_entradas(raiz: Path = REPO_ROOT) -> None:
    """A2a. While the interlock stands, nothing may produce the three inputs for SP-OP-AUTH-001."""
    if not interlock_t1_vigente(raiz):
        return
    acusacoes = acusar_produtores_de_entradas(raiz)
    if acusacoes:
        detalhe = "\n".join(f"  - [{a.verificacao}] {a.arquivo}: {a.detalhe}" for a in acusacoes)
        raise InterlockT1ViolationError(
            f"{TOKEN_ENTRADAS}: {len(acusacoes)} finding(s) while {TABELA_REL} still masks the urgency "
            f"ladder under CPT. Producing these inputs has PROC-01 as a prerequisite (interlock merged "
            f"AND condition (i) or (ii)):\n{detalhe}"
        )


# =================================================================================================
# A2b — the A2a guard must ride every agent's CI trail
# =================================================================================================


def marcadores_de_trilha_ausentes(texto: str, agentes: Iterable[str]) -> list[str]:
    """The `agents/<id>/` markers (`scripts/ci/trilha.py::testes_do_agente`) missing from `texto`."""
    return [f"agents/{agente}/" for agente in sorted(agentes) if f"agents/{agente}/" not in texto]


# =================================================================================================
# A3 — L0: a `false` verdict is only ever "route to a human", in every reachable cell
# =================================================================================================


def _tipos_da_grade(tabela: DecisionTable) -> tuple[str, ...]:
    coluna = tabela.input_names.index("tipo_procedimento")
    declarados = {lit for regra in tabela.rules for lit in re.findall(r'"([^"]*)"', regra.inputs[coluna])}
    return (*sorted(declarados), *TIPOS_FORA_DO_DOMINIO)


def violacoes_l0_carencia(tabela: DecisionTable) -> list[str]:
    """A3a. Output set exactly the three; `carencia_cumprida` boolean; every `false` carries the L0 token."""
    violacoes: list[str] = []
    if set(tabela.output_names) != SAIDAS or len(tabela.output_names) != len(SAIDAS):
        violacoes.append(f"declared outputs {tabela.output_names} != {sorted(SAIDAS)}")
    for tipo in _tipos_da_grade(tabela):
        for dias in GRADE_DIAS:
            for cpt in (True, False):
                veredito = evaluate(tabela, _entradas(tabela, tipo, dias, cpt))
                saidas = veredito.saidas
                onde = f"{veredito.regra} @ ({tipo!r}, {dias}, {cpt})"
                if set(saidas) != SAIDAS:
                    violacoes.append(f"{onde}: outputs {sorted(saidas)}")
                cumprida = saidas.get("carencia_cumprida")
                if not isinstance(cumprida, bool):
                    violacoes.append(f"{onde}: carencia_cumprida={cumprida!r} is not a boolean")
                elif cumprida is False and TOKEN_FONTE_L0 not in str(saidas.get("fonte", "")):
                    violacoes.append(f"{onde}: false verdict without {TOKEN_FONTE_L0!r} in `fonte`")
    return violacoes


def valores_de_carencia_emitidos(tabela: DecisionTable) -> frozenset[Any]:
    """Every `carencia_cumprida` value `tabela` emits across the full grid."""
    return frozenset(
        evaluate(tabela, _entradas(tabela, tipo, dias, cpt)).saidas["carencia_cumprida"]
        for tipo in _tipos_da_grade(tabela)
        for dias in GRADE_DIAS
        for cpt in (True, False)
    )


def violacoes_composicao_admissibilidade(
    tabelas_carencia: Iterable[DecisionTable], admissibilidade: DecisionTable
) -> list[str]:
    """A3b. Any `carencia_cumprida` the carencia tables emit, composed with all 8 combinations of the
    other three admissibility inputs, yields a non-adverse result — and `SEGUE_ANALISE` exactly when
    `requer_autorizacao ∧ beneficiario_ativo ∧ ¬carencia_cumprida`."""
    violacoes: list[str] = []
    for tabela in tabelas_carencia:
        for carencia in sorted(valores_de_carencia_emitidos(tabela), key=repr):
            for requer in (True, False):
                for docs in (True, False):
                    for ativo in (True, False):
                        por_nome = {
                            "requer_autorizacao": requer,
                            "documentacao_completa": docs,
                            "beneficiario_ativo": ativo,
                            "carencia_cumprida": carencia,
                        }
                        valores = {nome: por_nome[nome] for nome in admissibilidade.input_names}
                        veredito = evaluate(admissibilidade, valores)
                        resultado = veredito.saidas.get("resultado")
                        onde = f"{tabela.source.name} carencia={carencia!r} -> {veredito.regra} {valores}"
                        if resultado not in RESULTADOS_ADMISSIVEIS:
                            violacoes.append(f"{onde}: adverse/unknown resultado {resultado!r}")
                        elif requer and ativo and carencia is False and resultado != "SEGUE_ANALISE":
                            violacoes.append(
                                f"{onde}: unmet carencia must route to SEGUE_ANALISE, got {resultado!r}"
                            )
    return violacoes
