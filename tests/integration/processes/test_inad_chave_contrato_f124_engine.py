"""F-124 no motor REAL — start de SP-OP-INADIMPLENCIA-001 sem `numero_contrato` e' recusado.

Decisao do DPO de 31/10/2026 (#1082, opcao a), flag dark `MAEZO_INAD_CHAVE_EXIGE_CONTRATO`.
Mesmo harness de `test_t33_a2_agent_start_idempotency_matrix.py` (fixtures `engine`,
`audit_sink`, `audit_pg`, `audit_tenant` deste pacote; nada de engine mockado): a chave vem da
derivacao REAL do Fernando (`_business_key(..., exige_contrato=True)`) e o start passa pelo
chokepoint REAL com `require_contract_key=True`.

Prova, contra o CIB Seven e o Postgres de audit reais:
  * sem contrato (so' matricula) -> `CibSevenStartKeyError`, ZERO instancia sob a chave
    degenerada E sob a chave da matricula, ZERO elo novo na cadeia de audit;
  * com contrato -> exatamente uma instancia `INAD-{tenant}-{numero_contrato}` e um elo.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from maezo.agents.fernando.graph import PROCESS_KEY, _business_key
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    CibSevenHttpTransport,
    CibSevenStartKeyError,
    start_process_idempotent,
)

from .conftest import CIBSEVEN_BASE_URL, count_chain_rows
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_DEPLOY = (
    _REPO / "spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn",
    _REPO / "spec/processes/dmn/inadimplencia_status.dmn",
    _REPO / "spec/processes/dmn/inadimplencia_purga.dmn",
    _REPO / "spec/processes/dmn/inadimplencia_sla.dmn",
)
_DEPLOY_NAME = "SP-OP-INADIMPLENCIA-001-qa-f124"
_TENANT = "amh"


def _provenance() -> AgentDecisionProvenance:
    return AgentDecisionProvenance(
        agent_id="fernando", agent_version="f124-qa", tenant_id=_TENANT, decision_basis={"route": "f124_qa"}
    )


async def test_f124_start_sem_contrato_e_recusado_no_motor_real(
    engine: EngineRest, audit_sink: object, audit_pg: tuple[str, str], audit_tenant: str
) -> None:
    await engine.deploy(*_DEPLOY, name=_DEPLOY_NAME, tenant_id=_TENANT)
    matricula = f"MAT-F124-{uuid.uuid4().hex[:8]}"
    state = {"tenant_id": _TENANT, "numero_contrato": "", "matricula_beneficiario": matricula}
    business_key = _business_key(state, exige_contrato=True)  # type: ignore[arg-type]
    assert business_key == f"INAD-{_TENANT}-"

    transport = CibSevenHttpTransport(CIBSEVEN_BASE_URL, tenant_id=_TENANT)
    try:
        chain_before = await count_chain_rows(audit_pg[0], audit_tenant)
        with pytest.raises(CibSevenStartKeyError):
            await start_process_idempotent(
                transport,
                process_key=PROCESS_KEY,
                business_key=business_key,
                variables={"tenant_id": _TENANT, "numero_contrato": ""},
                audit_sink=audit_sink,  # type: ignore[arg-type]
                provenance=_provenance(),
                require_contract_key=True,
            )
        assert await count_chain_rows(audit_pg[0], audit_tenant) == chain_before
        assert await engine.find_active_instances(f"INAD-{_TENANT}-{matricula}") == []
    finally:
        await transport.close()


async def test_f124_start_com_contrato_inicia_uma_instancia_no_motor_real(
    engine: EngineRest, audit_sink: object, audit_pg: tuple[str, str], audit_tenant: str
) -> None:
    await engine.deploy(*_DEPLOY, name=_DEPLOY_NAME, tenant_id=_TENANT)
    contrato = f"CONTRATO-F124-{uuid.uuid4().hex[:8]}"
    state = {"tenant_id": _TENANT, "numero_contrato": contrato, "matricula_beneficiario": "MAT-IGNORADA"}
    business_key = _business_key(state, exige_contrato=True)  # type: ignore[arg-type]
    assert business_key == f"INAD-{_TENANT}-{contrato}"

    transport = CibSevenHttpTransport(CIBSEVEN_BASE_URL, tenant_id=_TENANT)
    try:
        chain_before = await count_chain_rows(audit_pg[0], audit_tenant)
        instance = await start_process_idempotent(
            transport,
            process_key=PROCESS_KEY,
            business_key=business_key,
            variables={"numero_contrato": contrato},
            audit_sink=audit_sink,  # type: ignore[arg-type]
            provenance=_provenance(),
            require_contract_key=True,
        )
        # Reentrega sob a flag: idempotente, mesma instancia, nenhum elo novo.
        again = await start_process_idempotent(
            transport,
            process_key=PROCESS_KEY,
            business_key=business_key,
            variables={"numero_contrato": contrato},
            audit_sink=audit_sink,  # type: ignore[arg-type]
            provenance=_provenance(),
            require_contract_key=True,
        )
        assert instance.already_existed is False
        assert again.instance_id == instance.instance_id
        assert await count_chain_rows(audit_pg[0], audit_tenant) - chain_before == 1
        active = await engine.find_active_instances(business_key)
        assert len(active) == 1
    finally:
        await transport.close()
