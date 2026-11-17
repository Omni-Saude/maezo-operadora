#!/usr/bin/env python3
"""Recibo de confianca reproduzido do omni, degrau T2 (EP-CORE-10a, epico E-SF-33; registro CORE-10).

CORE-10 (resposta C, regra 4): um job Maezo-side faz checkout do sha omni consumido com Node 24.21.0 /
npm 11.19.0 exatos, roda `npm ci`, `npm run audit`, `lint`, `typecheck`, `test`, a prova `server-only`
e o build, e grava `docs/governance/omni-trust/OMNI-TRUST-RECEIPT-<sha>.json` (sha, data, versoes, exit
code de cada passo, contagem executada de testes). Aceite: consumo por copia (A) ou in-place (C) e
recusado sem recibo do MESMO sha com audit verde de no maximo N dias.

O produtor do recibo e `.github/workflows/omni-trust-receipt.yml` (so `workflow_dispatch`). Este modulo
e o consumidor: ele decide se um recibo serve. Nenhum recibo real foi produzido ate hoje.

Recusas tipadas (as quatro primeiras sao nomes fixados pela SPEC de EP-CORE-10a):

* `TrustRefusal.RECEIPT_MISSING` -- arquivo com cabecalho de proveniencia (09a) cujo sha nao tem recibo.
* `TrustRefusal.AUDIT_FAILED` -- `steps.audit.exit != 0`.
* `TrustRefusal.RUNTIME_NOT_PINNED` -- `runtime.node`/`runtime.npm` diferente do pin.
* `TrustRefusal.SHA_MISMATCH` -- recibo de um sha usado para outro.

Recusas adicionais (declaradas no relatorio de autoria; sem elas o aceite nao fecha):

* `TrustRefusal.RECEIPT_STALE` -- recibo mais velho que `VALIDADE_DIAS` (o "<= N dias" do aceite).
* `TrustRefusal.STEP_FAILED` -- outro passo obrigatorio com exit != 0 ou ausente.
* `TrustRefusal.RECEIPT_MALFORMED` -- recibo que nao e o objeto descrito em `docs/governance/omni-trust/`.

Por que em `scripts/docs/`: ver o cabecalho de `scripts/docs/omni_provenance.py`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from scripts.docs.omni_provenance import ler_cabecalho

REPO_ROOT = Path(__file__).resolve().parents[2]
TRUST_REL = "docs/governance/omni-trust"
WORKFLOW_REL = ".github/workflows/omni-trust-receipt.yml"
RECIBO_PREFIXO = "OMNI-TRUST-RECEIPT-"

#: Pin de runtime do omni (`omni@b3de4b4:.node-version` = 24.21.0; CORE-10 regra 4).
NODE_PIN = "v24.21.0"
NPM_PIN = "11.19.0"
#: PROVISORIO: validade do recibo em dias. Sugestao do plano (EP-CORE-10a, "N decidido no plano;
#: sugestao 7"); o plano nao fixou N. Trocar exige so este valor e o teste que o cita.
VALIDADE_DIAS = 7
#: Passos obrigatorios do recibo, na ordem do workflow (CORE-10 regra 4).
PASSOS = ("npm_ci", "audit", "lint", "typecheck", "test", "server_only_proof", "build")
_SHA40 = re.compile(r"[0-9a-f]{40}")


class TrustRefusal(StrEnum):
    RECEIPT_MISSING = "RECEIPT_MISSING"
    AUDIT_FAILED = "AUDIT_FAILED"
    RUNTIME_NOT_PINNED = "RUNTIME_NOT_PINNED"
    SHA_MISMATCH = "SHA_MISMATCH"
    RECEIPT_STALE = "RECEIPT_STALE"
    STEP_FAILED = "STEP_FAILED"
    RECEIPT_MALFORMED = "RECEIPT_MALFORMED"


@dataclass(frozen=True)
class Recusa:
    motivo: TrustRefusal
    alvo: str
    detalhe: str


def nome_recibo(sha: str) -> str:
    return f"{RECIBO_PREFIXO}{sha}.json"


def avaliar_recibo(recibo: Any, sha_consumido: str, hoje: date, alvo: str = "<recibo>") -> Recusa | None:
    """Decide se `recibo` autoriza consumir `sha_consumido` em `hoje`. None = autoriza."""
    try:
        sha = recibo["omni_sha"]
        gerado = datetime.fromisoformat(recibo["generated_at"]).date()
        node, npm = recibo["runtime"]["node"], recibo["runtime"]["npm"]
        passos = recibo["steps"]
        contagem = recibo["tests_executed"]
    except (KeyError, TypeError, ValueError) as exc:
        return Recusa(TrustRefusal.RECEIPT_MALFORMED, alvo, f"recibo malformado: {exc!r}")
    if not (isinstance(sha, str) and _SHA40.fullmatch(sha)) or not isinstance(passos, dict):
        return Recusa(TrustRefusal.RECEIPT_MALFORMED, alvo, "omni_sha sem 40 hex ou steps nao e objeto")
    if not isinstance(contagem, int) or isinstance(contagem, bool) or contagem < 0:
        return Recusa(TrustRefusal.RECEIPT_MALFORMED, alvo, "tests_executed nao e inteiro >= 0")
    if sha != sha_consumido:
        return Recusa(TrustRefusal.SHA_MISMATCH, alvo, f"recibo de {sha} usado para {sha_consumido}")
    if node != NODE_PIN or npm != NPM_PIN:
        return Recusa(
            TrustRefusal.RUNTIME_NOT_PINNED, alvo, f"node={node} npm={npm}; pin {NODE_PIN}/{NPM_PIN}"
        )
    audit = passos.get("audit")
    if not isinstance(audit, dict) or audit.get("exit") != 0:
        return Recusa(TrustRefusal.AUDIT_FAILED, alvo, f"audit={audit!r}")
    for passo in PASSOS:
        res = passos.get(passo)
        if not isinstance(res, dict) or res.get("exit") != 0:
            return Recusa(TrustRefusal.STEP_FAILED, alvo, f"{passo}={res!r}")
    idade = (hoje - gerado).days
    if idade < 0 or idade > VALIDADE_DIAS:
        return Recusa(
            TrustRefusal.RECEIPT_STALE, alvo, f"recibo de {gerado} tem {idade} dias (max {VALIDADE_DIAS})"
        )
    return None


def exigir_recibo(sha_consumido: str, trust_dir: Path, hoje: date, alvo: str) -> Recusa | None:
    arq = trust_dir / nome_recibo(sha_consumido)
    if not arq.is_file():
        return Recusa(TrustRefusal.RECEIPT_MISSING, alvo, f"sem {TRUST_REL}/{arq.name}")
    try:
        recibo = json.loads(arq.read_text(encoding="utf-8"))
    except ValueError as exc:
        return Recusa(TrustRefusal.RECEIPT_MALFORMED, alvo, f"{arq.name}: {exc}")
    return avaliar_recibo(recibo, sha_consumido, hoje, alvo)


def verificar_consumo(raiz: Path, arquivos: Iterable[str], trust_dir: Path, hoje: date) -> list[Recusa]:
    """Todo arquivo com cabecalho de proveniencia (copia A / consumo C) exige recibo valido do sha."""
    recusas: list[Recusa] = []
    for rel in arquivos:
        alvo = raiz / rel
        if alvo.is_symlink() or not alvo.is_file():
            continue
        cab = ler_cabecalho(alvo.read_bytes())
        if cab is None:
            continue
        recusa = exigir_recibo(cab.commit, trust_dir, hoje, rel)
        if recusa is not None:
            recusas.append(recusa)
    return recusas


def recibos_publicados(trust_dir: Path) -> list[Path]:
    return sorted(trust_dir.glob(f"{RECIBO_PREFIXO}*.json")) if trust_dir.is_dir() else []
