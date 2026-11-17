#!/usr/bin/env python3
"""Registro de sala limpa por EXTRACT (EP-CORE-09b, epico E-SF-33; registro CORE-09, caminho B).

Aceite de EP-CORE-09b: todo modulo marcado `derived-from: OMNI-V-<nn>` tem registro
`docs/governance/cleanroom/CLEANROOM-OMNI-V-<nn>.json`; o registro so cita fontes custodiadas pela
Maezo ou pela AMH (nunca o omni_sales_platform); autor da spec != implementador; o implementador atesta
que nao abriu arquivos do omni ao implementar.

Recusas tipadas (nomes fixados pela SPEC de EP-CORE-09b):

* `CleanroomRefusal.FORBIDDEN_SOURCE` -- uma fonte da spec nao e Maezo/AMH (p.ex. cita o omni).
* `CleanroomRefusal.ACTOR_NOT_SEPARATED` -- `spec_author == implementer`.
* `CleanroomRefusal.RECORD_MISSING` -- modulo marcado sem registro que o liste.

Recusa adicional (nao nomeada pela SPEC; declarada no relatorio de autoria):

* `CleanroomRefusal.RECORD_MALFORMED` -- registro que nao segue `cleanroom-record.schema.json`
  (campo ausente, atestacao falsa ou vazia). Sem ela, um registro incompleto passaria por valido.

Limite declarado: o registro e uma atestacao; nao prova sozinho que ninguem abriu o omni. O que e
mecanico: a fonte declarada, a separacao de atores e (via EP-CORE-09a) a ausencia de blob identico.
Por que em `scripts/docs/`: ver o cabecalho de `scripts/docs/omni_provenance.py`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CLEANROOM_REL = "docs/governance/cleanroom"
SCHEMA_REL = f"{CLEANROOM_REL}/cleanroom-record.schema.json"
REGISTRO_GLOB = "CLEANROOM-OMNI-V-*.json"

#: Repositorios cujas fontes uma spec de sala limpa pode citar (Maezo e AMH, CORE-09 opcao B).
FONTES_PERMITIDAS = frozenset({"Omni-Saude/maezo-operadora", "Omni-Saude/amh-data-platform"})
_OMNI_RE = re.compile(r"omni[_-]sales[_-]platform", re.IGNORECASE)
#: Marcador de modulo derivado. So conta no inicio de linha (com prefixo de comentario opcional).
_MARCADOR_RE = re.compile(
    r"^[ \t]*(?:#|//|/\*+|\*|<!--|--|;)?[ \t]*derived-from:[ \t]*OMNI-V-(\d{2})\b", re.MULTILINE
)
_EXTRACT_RE = re.compile(r"OMNI-V-\d{2}")
_CAMPOS = (
    "schema_version",
    "extract",
    "modules",
    "spec_sources",
    "spec_author",
    "implementer",
    "attestation",
)


class CleanroomRefusal(StrEnum):
    FORBIDDEN_SOURCE = "FORBIDDEN_SOURCE"
    ACTOR_NOT_SEPARATED = "ACTOR_NOT_SEPARATED"
    RECORD_MISSING = "RECORD_MISSING"
    RECORD_MALFORMED = "RECORD_MALFORMED"


@dataclass(frozen=True)
class Recusa:
    motivo: CleanroomRefusal
    alvo: str
    detalhe: str


def validar_registro(nome: str, registro: Any) -> list[Recusa]:
    """Valida UM registro (dict ja carregado do JSON)."""
    if not isinstance(registro, dict):
        return [Recusa(CleanroomRefusal.RECORD_MALFORMED, nome, "registro nao e objeto JSON")]
    faltando = [c for c in _CAMPOS if c not in registro]
    if faltando:
        return [Recusa(CleanroomRefusal.RECORD_MALFORMED, nome, f"campos ausentes: {faltando}")]
    recusas: list[Recusa] = []
    if not isinstance(registro["extract"], str) or not _EXTRACT_RE.fullmatch(registro["extract"]):
        recusas.append(Recusa(CleanroomRefusal.RECORD_MALFORMED, nome, "extract fora de OMNI-V-<nn>"))
    fontes = registro["spec_sources"]
    if not isinstance(fontes, list) or not fontes:
        recusas.append(Recusa(CleanroomRefusal.RECORD_MALFORMED, nome, "spec_sources vazio"))
        fontes = []
    for fonte in fontes:
        repo = fonte.get("repository") if isinstance(fonte, dict) else None
        caminho = fonte.get("path") if isinstance(fonte, dict) else None
        texto = json.dumps(fonte, ensure_ascii=False)
        if repo not in FONTES_PERMITIDAS or not isinstance(caminho, str) or _OMNI_RE.search(texto):
            recusas.append(Recusa(CleanroomRefusal.FORBIDDEN_SOURCE, nome, f"fonte nao custodiada: {texto}"))
    autor, impl = registro["spec_author"], registro["implementer"]
    if not (isinstance(autor, str) and autor.strip() and isinstance(impl, str) and impl.strip()):
        recusas.append(Recusa(CleanroomRefusal.RECORD_MALFORMED, nome, "spec_author/implementer vazio"))
    elif autor.strip().casefold() == impl.strip().casefold():
        recusas.append(
            Recusa(CleanroomRefusal.ACTOR_NOT_SEPARATED, nome, f"spec_author == implementer ({autor})")
        )
    att = registro["attestation"]
    if not (
        isinstance(att, dict)
        and att.get("implementer_did_not_open_omni_files") is True
        and isinstance(att.get("attested_by"), str)
        and att["attested_by"].strip().casefold() == str(impl).strip().casefold()
    ):
        recusas.append(
            Recusa(
                CleanroomRefusal.RECORD_MALFORMED,
                nome,
                "atestacao ausente, falsa ou nao assinada pelo implementador",
            )
        )
    modulos = registro["modules"]
    if not isinstance(modulos, list) or not modulos or not all(isinstance(m, str) for m in modulos):
        recusas.append(Recusa(CleanroomRefusal.RECORD_MALFORMED, nome, "modules vazio"))
    return recusas


def carregar_registros(cleanroom_dir: Path) -> dict[str, Any]:
    regs: dict[str, Any] = {}
    if cleanroom_dir.is_dir():
        for arq in sorted(cleanroom_dir.glob(REGISTRO_GLOB)):
            try:
                regs[arq.name] = json.loads(arq.read_text(encoding="utf-8"))
            except ValueError:
                regs[arq.name] = None
    return regs


def modulos_marcados(raiz: Path, arquivos: Iterable[str]) -> dict[str, set[str]]:
    """Mapa caminho -> EXTRACTs (`OMNI-V-<nn>`) declarados por marcador no arquivo."""
    marcados: dict[str, set[str]] = {}
    for rel in arquivos:
        alvo = raiz / rel
        if alvo.is_symlink() or not alvo.is_file():
            continue
        texto = alvo.read_bytes().decode("utf-8", errors="replace")
        achados = {f"OMNI-V-{m}" for m in _MARCADOR_RE.findall(texto)}
        if achados:
            marcados[rel] = achados
    return marcados


def verificar(raiz: Path, arquivos: Iterable[str], cleanroom_dir: Path) -> list[Recusa]:
    registros = carregar_registros(cleanroom_dir)
    recusas: list[Recusa] = []
    cobertos: set[tuple[str, str]] = set()
    for nome, reg in registros.items():
        problemas = validar_registro(nome, reg)
        recusas.extend(problemas)
        if not problemas and isinstance(reg, dict):
            if nome != f"CLEANROOM-{reg['extract']}.json":
                recusas.append(Recusa(CleanroomRefusal.RECORD_MALFORMED, nome, "nome do arquivo != extract"))
                continue
            cobertos.update((m, reg["extract"]) for m in reg["modules"])
    for rel, extracts in sorted(modulos_marcados(raiz, arquivos).items()):
        for ext in sorted(extracts):
            if (rel, ext) not in cobertos:
                recusas.append(
                    Recusa(CleanroomRefusal.RECORD_MISSING, rel, f"derived-from {ext} sem registro valido")
                )
    return recusas
