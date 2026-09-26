"""Passo `supervisor` (servico `issuer`, depois do `rotate-issuer`): a escalacao passa do SLA e vai para
`UT_SupervisorAssume` (timer de resolucao antecipado pelo job do engine, sem mudar BPMN/DMN) e o
emissor roda de novo sob a r2. Reproduz o dev (26/09): caso escalado ao supervisor e caso VIVO."""

from __future__ import annotations

import httpx
from tools.dev_syn_fixture import core

from . import issuer
from .auth_fixture import GUIDE_NUMBER
from .common import ROOT, TENANT, save_state, state, step

ROTATION = ROOT / "rotation"


def main() -> None:
    key = core.escalation_key(TENANT, GUIDE_NUMBER)
    with httpx.Client(base_url=issuer.REST, timeout=30, trust_env=False) as client:
        supervisors = core.breach_resolution_sla(client, TENANT, GUIDE_NUMBER, worker="c1-worker")
        treating = client.get(
            "/task", params={"processInstanceBusinessKey": key, "taskDefinitionKey": core.HUMAN_TASK}
        ).json()
    completed = issuer.run_issuer(
        issuer.composition(
            ROOT / "issuer-run-r2",
            designation=ROTATION / "designation.json",
            proof=ROTATION / "installation-proof.json",
            designation_digest=state("rotation")["designation_digest"],
        )
    )
    line = (completed.stdout.strip().splitlines() or [completed.stderr.strip()[-400:]])[-1]
    save_state("supervisor", dict(supervisor_tasks=supervisors))
    ok = supervisors == 1 and not treating and completed.returncode == 0 and '"live": 1' in line
    step(
        "supervisor",
        ok,
        f"UT_SupervisorAssume={supervisors} UT_TratarEscalonamento={len(treating)}; "
        f"emissor rc={completed.returncode} {line}",
    )


if __name__ == "__main__":
    main()
