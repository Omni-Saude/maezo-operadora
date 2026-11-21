#!/usr/bin/env python3
"""Tier B (evals ao vivo) na zona PHI do dev — execucao MANUAL, fora do CI.

Por que
-------
Os grafos chamam o modelo com `phi=True`; so' um provedor da zona PHI responde. No CI nao ha' um (o
noturno diz "Tier B nao roda no CI"), entao o Tier B roda por aqui, por um humano com sessao AWS do dev,
contra `bedrock_br` (Bedrock regional, o mesmo provedor que o simulador do dev usa com dado sintetico).
Roteiro completo: tests/evals/README.md, secao "Tier B: execucao manual na zona PHI do dev".

O que faz
---------
1. Confere o ambiente (a CONFERENCIA nao imprime valor nenhum): `MAEZO_INFERENCE_PROVIDER` vazio ou
   `bedrock_br`; os tres portoes que o dono preenche no tfvars do dev (`MAEZO_PHI_ENDPOINT_URL`,
   `MAEZO_PHI_VENDOR_DPA_REF`, `MAEZO_INFERENCE_MODEL`); credencial AWS resolvivel pelo boto3.
2. Roda o MESMO preflight do noturno (`scripts/ci/tier_b_preflight.py`, phi=True), so' o Tier B
   (`pytest tests/evals -m "eval and llm_live"`) e o MESMO resumo (`scripts/ci/tier_b_resumo.py`).
3. Grava SO' o resumo (ids e resultados; nenhuma saida de modelo) em `<saida>/resumo.md`. O junit fica num
   diretorio temporario e e' apagado. No TERMINAL, os logs INFO do provedor mostram o id do modelo e o host
   do endpoint (nunca o DPA ref); nada disso vai para o resumo.

Uso: `.venv/bin/python scripts/dev/tier_b_manual.py [--saida DIR]` (padrao: `$TMPDIR/tier-b-manual-<UTC>`).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
PROVEDOR = "bedrock_br"
#: Os tres portoes da zona PHI (nomes das variaveis; os valores vem do tfvars do dev).
PORTOES = ("MAEZO_PHI_ENDPOINT_URL", "MAEZO_PHI_VENDOR_DPA_REF", "MAEZO_INFERENCE_MODEL")
SELECAO_TIER_B = "eval and llm_live"


def problemas_do_ambiente(env: Mapping[str, str], *, credencial_aws: bool) -> list[str]:
    """O que falta para rodar. Nunca inclui o VALOR de variavel alguma."""
    problemas = []
    provedor = env.get("MAEZO_INFERENCE_PROVIDER", "").strip()
    if provedor not in ("", PROVEDOR):
        problemas.append(
            f"MAEZO_INFERENCE_PROVIDER deve ser vazio ou {PROVEDOR} (este roteiro so' roda {PROVEDOR})"
        )
    problemas += [
        f"falta {nome} (valor do tfvars do dev)" for nome in PORTOES if not env.get(nome, "").strip()
    ]
    if not credencial_aws:
        problemas.append(
            "sem credencial AWS do dev (ex.: `aws sso login --profile <perfil-do-dev>` e AWS_PROFILE)"
        )
    return problemas


def _credencial_aws() -> bool:
    try:
        import boto3  # type: ignore[import-untyped]  # extra [bedrock], como em br_regional.py

        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


def comandos(junit: Path) -> list[list[str]]:
    py = sys.executable
    return [
        [py, "-I", "scripts/ci/tier_b_preflight.py"],
        [
            py,
            "-m",
            "pytest",
            "tests/evals",
            "-q",
            "--tb=no",
            "-rN",
            "-m",
            SELECAO_TIER_B,
            f"--junitxml={junit}",
        ],
        [py, "-I", "scripts/ci/tier_b_resumo.py", "--junit", str(junit), "--codigo"],
    ]


def ambiente_da_execucao(env: Mapping[str, str], resumo: Path) -> dict[str, str]:
    # As MESMAS opcoes do noturno: o job recusa PYTHON*/PYTEST_* herdados; aqui eles sao descartados (um
    # `PYTEST_ADDOPTS=-rA` ou `-s` mudaria as opcoes e imprimiria saida capturada; `PYTHONPATH`, o import).
    novo = {k: v for k, v in env.items() if not k.startswith(("PYTHON", "PYTEST_"))}
    novo.update(
        MAEZO_INFERENCE_PROVIDER=PROVEDOR,
        NIGHTLY_EVAL="1",
        TIER_B_EXECUCAO="manual",
        TIER_B_BLOQUEANTE=env.get("TIER_B_BLOQUEANTE", "false"),
        GITHUB_STEP_SUMMARY=str(resumo),
    )
    return novo


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    carimbo = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    # Fora do repositorio por padrao: o resumo e' entregue a ROOT, nunca commitado.
    p.add_argument("--saida", type=Path, default=Path(tempfile.gettempdir()) / f"tier-b-manual-{carimbo}")
    a = p.parse_args(argv)
    problemas = problemas_do_ambiente(os.environ, credencial_aws=_credencial_aws())
    if problemas:
        for item in problemas:
            print(f"tier_b_manual: {item}", file=sys.stderr)
        return 2
    a.saida.mkdir(parents=True, exist_ok=True)
    resumo = (a.saida / "resumo.md").resolve()
    env = ambiente_da_execucao(os.environ, resumo)
    with tempfile.TemporaryDirectory(prefix="tier-b-") as tmp:
        junit = Path(tmp) / "junit.xml"
        preflight, suite, veredito = comandos(junit)
        if subprocess.run(preflight, cwd=RAIZ, env=env, check=False).returncode != 0:
            print(f"tier_b_manual: preflight QUEBRADO — veja {resumo}", file=sys.stderr)
            return 1
        codigo = subprocess.run(suite, cwd=RAIZ, env=env, check=False).returncode
        saida = subprocess.run([*veredito, str(codigo)], cwd=RAIZ, env=env, check=False).returncode
    print(f"tier_b_manual: resumo em {resumo}")
    return saida


if __name__ == "__main__":
    sys.exit(main())
