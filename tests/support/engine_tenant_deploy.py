"""Deploy de definicoes DO TENANT para as suites que iniciam processo por transporte com tenant.

Desde 27/09/2026 todo start de agente/worker e' `/process-definition/key/{key}/tenant-id/{tenant}
/start` (`CibSevenHttpTransport(tenant_id=...)`), e o motor so resolve ali definicao daquele
tenant — a compartilhada nao e' fallback. A business rule task de um processo do tenant tambem so
resolve DMN do mesmo tenant. Medido num CIB Seven 2.1.0 local:

    tenant-id/amh/start sem definicao do amh -> "No matching process definition ... tenant-id: amh"
    processo do amh, DMN so compartilhada    -> "no decision definition deployed with key ... and
                                                 tenant-id 'amh'"

Por que SO os processos pedidos (e as DMNs que eles chamam), nunca a arvore inteira: o engine de
integracao e' compartilhado pela sessao, e uma copia por tenant de um BPMN com START POR MENSAGEM
(`auth.start`, `nip.instruct`) torna ambigua a correlacao SEM tenant que outras suites fazem pela
REST crua. Deployar o minimo mantem as outras suites exatamente como estavam.
"""

from __future__ import annotations

import re
from pathlib import Path

from maezo.platform.deploy.engine_deploy import EngineDeployClient, resolve_spec_processes_dir

_PROCESS_ID = re.compile(r"<(?:bpmn:)?process\b[^>]*\bid=\"([^\"]+)\"")
_DECISION_REF = re.compile(r"camunda:decisionRef=\"([^\"$]+)\"")
_DECISION_ID = re.compile(r"<(?:dmn:)?decision\b[^>]*\bid=\"([^\"]+)\"")


def artifacts_for(process_keys: tuple[str, ...], processes_dir: Path | None = None) -> list[Path]:
    """Os BPMN que definem `process_keys` + toda DMN que eles referenciam por `decisionRef`."""
    root = processes_dir or resolve_spec_processes_dir()
    bpmn_by_key: dict[str, Path] = {}
    for path in sorted((root / "bpmn").glob("*.bpmn")):
        for key in _PROCESS_ID.findall(path.read_text(encoding="utf-8")):
            bpmn_by_key.setdefault(key, path)
    dmn_by_decision: dict[str, Path] = {}
    for path in sorted((root / "dmn").glob("*.dmn")):
        for decision in _DECISION_ID.findall(path.read_text(encoding="utf-8")):
            dmn_by_decision.setdefault(decision, path)

    missing = [key for key in process_keys if key not in bpmn_by_key]
    if missing:
        raise LookupError(f"no spec BPMN defines process key(s) {missing}")
    bpmns = sorted({bpmn_by_key[key] for key in process_keys})
    refs = {ref for path in bpmns for ref in _DECISION_REF.findall(path.read_text(encoding="utf-8"))}
    unresolved = sorted(ref for ref in refs if ref not in dmn_by_decision)
    if unresolved:
        raise LookupError(f"decisionRef(s) {unresolved} not found under {root / 'dmn'}")
    return [*bpmns, *sorted({dmn_by_decision[ref] for ref in refs})]


def deploy_for_tenant(engine_base_url: str, tenant: str, *process_keys: str) -> None:
    """Deploya (idempotente, filtro de duplicata por nome+tenant) os processos pedidos NO tenant."""
    client = EngineDeployClient(base_url=engine_base_url)
    try:
        client.deploy(artifacts_for(process_keys), name=f"it-tenant-{tenant}", tenant_id=tenant)
    finally:
        client.close()
