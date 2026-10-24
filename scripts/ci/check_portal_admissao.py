#!/usr/bin/env python3
"""Portao: BPMN com tarefa no portal cujos bytes a admissao Q2 viva NAO nomeia.

Incidente (28/09, 13/10 e 17/10/2026, o ultimo com casos P1 reais de teste): o `deploy-processes`
publicou uma versao nova do `SP-OP-ESCALATION-001` (bytes mudados pelo #816) e o portal so le
tarefas cujo par (`process_definition_id`, tarefa) a admissao Q2 assinada nomeia. As instancias
novas nasceram na v3, as tarefas delas ficaram sem recurso de leitura (`MZO_PORTAL_READ_RESOURCE`
vazio) e sumiram de TODAS as filas, sem erro nenhum (`T15_RESULT ok=true`). Ninguem tinha como
saber, no PR, que aquele BPMN tinha fila no portal.

O que este portao confere: `deploy-processes` envia os bytes do arquivo como estao, com
`deploy-changed-only=true`, entao "o SHA-256 do arquivo do repo esta entre os admitidos" e o mesmo
que "implantar a main nao cria versao invisivel". O espelho publico do que a admissao viva nomeia
fica em `deploy/aws-ecs/envs/dev-sa-east-1/portal-admissao.json` (atualizado na mesma PR de cada
renovacao do material, `docs/runbooks/material-staff-na-aws.md`). Para cada processo admitido:

* bytes do repo admitidos: ok;
* bytes do repo declarados em `aguardando_admissao` (com o `motivo`): aviso, sai 0. E a forma
  honesta de mesclar uma mudanca de BPMN antes do corte: o PR declara que o `deploy-processes`
  dela exige repinar a admissao;
* qualquer outro digest: FALHA (1). Declare em `aguardando_admissao` ou repine a admissao antes.

Tambem falha uma entrada `aguardando_admissao` velha (digest que nao e mais o do repo) ou ja
admitida. Sai 2 com o espelho invalido.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ESPELHO = Path("deploy/aws-ecs/envs/dev-sa-east-1/portal-admissao.json")
BPMN_DIR = Path("spec/processes/bpmn")
SCHEMA = "maezo-portal-admissao.v1"
RUNBOOK = "docs/runbooks/material-staff-na-aws.md (passo 5, 'Versao nova de processo ja admitido')"
_BPMN_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"([A-Z][A-Z0-9-]*[A-Z0-9]):([1-9][0-9]*):[0-9a-f-]{36}")


class EspelhoInvalidoError(ValueError):
    pass


def carregar_espelho(raw: bytes) -> tuple[dict[str, set[str]], dict[str, dict[str, str]]]:
    """(digests admitidos por chave, {chave: {digest: motivo}} aguardando)."""
    try:
        doc = json.loads(raw)
    except ValueError as erro:
        raise EspelhoInvalidoError(f"JSON invalido: {erro}") from None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise EspelhoInvalidoError(f"esperado schema {SCHEMA}")
    admitidos: dict[str, set[str]] = {}
    for e in doc.get("admitidos") or []:
        chave, digest, pid = (
            e.get("process_definition_key"),
            e.get("process_definition_digest"),
            e.get("process_definition_id"),
        )
        m = _ID.fullmatch(pid or "")
        if (
            not isinstance(chave, str)
            or not _HEX64.fullmatch(digest or "")
            or m is None
            or m.group(1) != chave
        ):
            raise EspelhoInvalidoError(f"entrada admitida invalida: {pid!r}")
        if m.group(2) != e.get("process_definition_version") or not e.get("tarefas"):
            raise EspelhoInvalidoError(f"versao ou tarefas invalidas em {pid}")
        admitidos.setdefault(chave, set()).add(digest)
    if not admitidos:
        raise EspelhoInvalidoError("nenhum processo admitido")
    aguardando: dict[str, dict[str, str]] = {}
    for e in doc.get("aguardando_admissao") or []:
        chave, digest, motivo = (
            e.get("process_definition_key"),
            e.get("process_definition_digest"),
            e.get("motivo"),
        )
        if (
            chave not in admitidos
            or not _HEX64.fullmatch(digest or "")
            or not isinstance(motivo, str)
            or not motivo
        ):
            raise EspelhoInvalidoError(
                f"aguardando_admissao invalido para {chave!r} (chave admitida, digest, motivo)"
            )
        aguardando.setdefault(chave, {})[digest] = motivo
    return admitidos, aguardando


def processos_do_repo(pasta: Path) -> dict[str, list[tuple[Path, str]]]:
    """{process id do BPMN: [(arquivo, sha256 dos bytes)]}."""
    out: dict[str, list[tuple[Path, str]]] = {}
    for arquivo in sorted(pasta.glob("*.bpmn")):
        raw = arquivo.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        for proc in ET.fromstring(raw).iter(f"{_BPMN_NS}process"):
            out.setdefault(proc.get("id", ""), []).append((arquivo, digest))
    return out


def avaliar(espelho: bytes, pasta: Path) -> tuple[int, list[str]]:
    try:
        admitidos, aguardando = carregar_espelho(espelho)
    except EspelhoInvalidoError as erro:
        return 2, [f"::error::{ESPELHO}: {erro}"]
    repo = processos_do_repo(pasta)
    linhas: list[str] = []
    codigo = 0
    for chave in sorted(admitidos):
        arquivos = repo.get(chave)
        if not arquivos:
            linhas.append(f"::error::{chave} esta admitido no portal mas nao ha BPMN dele em {pasta}")
            codigo = 1
            continue
        pendentes = aguardando.get(chave, {})
        for arquivo, digest in arquivos:
            if digest in admitidos[chave]:
                linhas.append(f"ok: {chave} ({arquivo.name}) sha256={digest[:16]} admitido")
            elif digest in pendentes:
                linhas.append(
                    f"::warning::{chave} ({arquivo.name}) sha256={digest[:16]} AGUARDANDO admissao: "
                    f"{pendentes[digest]} O deploy-processes destes bytes exige repinar a admissao "
                    "no mesmo corte "
                    f"({RUNBOOK})."
                )
            else:
                linhas.append(
                    f"::error::{chave} ({arquivo.name}) sha256={digest[:16]} NAO e admitido no portal. "
                    "Implantar "
                    "estes bytes cria uma versao cujas tarefas nao aparecem em fila nenhuma. Declare em "
                    f"`aguardando_admissao` de {ESPELHO} (com o motivo) ou repine a admissao antes "
                    f"({RUNBOOK})."
                )
                codigo = 1
        atuais = {d for _, d in arquivos}
        for digest in sorted(pendentes):
            if digest in admitidos[chave]:
                linhas.append(
                    f"::error::{chave} sha256={digest[:16]} ja admitido: tire de `aguardando_admissao`"
                )
                codigo = 1
            elif digest not in atuais:
                linhas.append(
                    f"::error::{chave} sha256={digest[:16]} em `aguardando_admissao` nao e mais "
                    "o BPMN do repo"
                )
                codigo = 1
    return codigo, linhas


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--espelho", type=Path, default=ESPELHO)
    p.add_argument("--bpmn", type=Path, default=BPMN_DIR)
    a = p.parse_args(argv)
    codigo, linhas = avaliar(a.espelho.read_bytes(), a.bpmn)
    for linha in linhas:
        print(linha)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
