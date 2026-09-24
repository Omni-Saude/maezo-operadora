"""Passo `publish` (servico `job`): o job da T1.5 contra o engine real, duas vezes.

1a rodada: catalogo staff minimo (`entries=[]`) uma vez, e para cada membership staff o principal no
`/v1/authority` e a membership no Q2. 2a rodada: nada muda (idempotencia pelo ledger CAS).
E o `__main__` do proprio job (`python -m maezo.gateway.human.membership_publication_job publish`),
com o pacote humano no caminho fixo e os dois pins no ambiente, como na task da Onda 6.
"""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from urllib.parse import quote

from maezo.gateway.human import membership_publication_job as job

from .common import ADMIN, MEMBERSHIP_PREFIX, ROOT, STATE, TENANT, iso, now, read_text, state, step, write
from .engine_config import (
    CATALOG_PREFIX,
    DEPLOYMENT_RECEIPT_DIGEST,
    DEPLOYMENT_RECEIPT_REF,
    HUMAN_AUDIENCE,
)
from .seed import BFF_LOGIN

JOB = ROOT / "job"


def config() -> dict:
    engine = state("engine")
    JOB.mkdir(mode=0o700, exist_ok=True)
    secret = read_text(ADMIN / f"{BFF_LOGIN}-password")
    write(JOB / "identity-dsn.txt", f"postgresql+asyncpg://{BFF_LOGIN}:{quote(secret, safe='')}@postgres:5432/maezo")
    from .common import CATALOG_REF

    return dict(
        schema="portal-membership-publication-job.v1",
        tenant=TENANT,
        identity_dsn_file=str(JOB / "identity-dsn.txt"),
        ledger_file=str(JOB / "ledger.json"),
        membership_source_ref_prefix=MEMBERSHIP_PREFIX,
        observation_seconds="600",
        catalog=dict(
            catalog_ref=CATALOG_REF, catalog_revision="1", admitted_catalog_digest=engine["catalog_digest"],
            deployment_receipt_ref=DEPLOYMENT_RECEIPT_REF, deployment_receipt_digest=DEPLOYMENT_RECEIPT_DIGEST,
            source_ref_prefix=CATALOG_PREFIX, valid_seconds="86400",
        ),
        authority=dict(
            key_file=str(STATE / "authority-key.pem"), key_id="c1-human-authority", audience=HUMAN_AUDIENCE,
            fingerprint=engine["authority_fingerprint"], not_after=iso(now() + timedelta(hours=12)),
            max_envelope_seconds="30",
        ),
    )


def run_once(path: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = job.main(["publish", "--config", path])
    return code, out.getvalue().strip(), err.getvalue().strip()


def main() -> None:
    engine = state("engine")
    os.environ["MAEZO_HUMAN_MATERIAL_VERSION_ID"] = engine["human_version"]
    os.environ["MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256"] = engine["human_manifest_sha256"]
    path = JOB / "config.json"
    write(path, json.dumps(config()), 0o400)
    first = run_once(str(path))
    second = run_once(str(path))
    ok = first[0] == 0 and second[0] == 0 and '"memberships_published": 0' in second[1]
    step("publish", ok, f"1a rodada rc={first[0]} {first[1] or first[2]} | 2a rodada rc={second[0]} {second[1] or second[2]}")


if __name__ == "__main__":
    main()
