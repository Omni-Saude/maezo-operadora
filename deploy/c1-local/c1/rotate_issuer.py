"""Passo `rotate-issuer` (servico `issuer`, depois do `rotate` e do engine reiniciado): o emissor com a
composicao da designacao r2 (digest e `configuration_digest` novos, gravados pelo `rotate`)."""

from __future__ import annotations

import httpx

from . import issuer
from .auth_fixture import GUIDE_NUMBER
from .common import ROOT, TENANT, save_state, state, step

ROTATION = ROOT / "rotation"


def main() -> None:
    digest = state("rotation")["designation_digest"]
    completed = issuer.run_issuer(
        issuer.composition(
            ROOT / "issuer-run-r2",
            designation=ROTATION / "designation.json",
            proof=ROTATION / "installation-proof.json",
            designation_digest=digest,
        )
    )
    # H3: a tarefa corrente do caso e a da INSTANCIA do caso (AUTH), nao a da escalacao; o engine a
    # omite quando nao ha (nunca inventa). O `portal-r2` confere o detalhe contra este fato medido.
    with httpx.Client(base_url=issuer.REST, timeout=30, trust_env=False) as client:
        live = client.get(
            "/task", params={"processInstanceBusinessKey": f"AUTH-{TENANT}-{GUIDE_NUMBER}", "tenantIdIn": TENANT}
        ).json()
    save_state("rotation", dict(state("rotation"), case_live_tasks=[t["id"] for t in live]))
    line = (completed.stdout.strip().splitlines() or [completed.stderr.strip()[-300:]])[-1]
    ok = (
        completed.returncode == 0
        and '"policy_ref": "staff-escalation-routing@d2"' in line
        and '"refused": 0' in line
    )
    step("rotate-issuer", ok, f"emissor(r2 {digest[:12]}) rc={completed.returncode} {line}")


if __name__ == "__main__":
    main()
