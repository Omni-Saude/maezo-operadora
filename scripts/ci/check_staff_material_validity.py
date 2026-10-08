#!/usr/bin/env python3
"""Alarme: o material staff/human do portal nativo em DEV vence em 14 dias (N2) e derruba o motor.

Incidente de 08/10/2026: a janela do material venceu em 2026-10-08T04:44:25Z, o alarme do job T1.5
disparou para um topico SNS SEM assinante, e o motor so caiu ~13 h depois (no primeiro restart): o
`AuthResultSigner` recusa certificado vencido e o `StaffDeploymentComposition` derruba o boot.

Este portao le o fim da janela VERSIONADO em
`deploy/aws-ecs/envs/dev-sa-east-1/staff-material-validade.json` (atualizado na mesma PR de cada
renovacao, `docs/runbooks/renovar-material-staff-dev.md`) e falha quando faltam `aviso_dias` (3, a
decisao N2 "lembrete 3 dias antes") ou menos. Roda agendado (`.github/workflows/staff-material-validade.yml`):
o run vermelho e o aviso. Em PR que toca o arquivo, confere tambem a forma (janela <= 14 dias).

Sai 0 com folga, 1 no aviso/vencido, 2 com arquivo invalido.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ARQUIVO = Path("deploy/aws-ecs/envs/dev-sa-east-1/staff-material-validade.json")
SCHEMA = "maezo-staff-material-validity.v1"
CAMPOS = {
    "schema",
    "valid_until",
    "renovado_em",
    "designation_revision",
    "admission_revision",
    "aviso_dias",
    "runbook",
}
JANELA_MAXIMA = timedelta(days=14)


def instante(valor: object) -> datetime:
    if not isinstance(valor, str) or not valor.endswith("Z"):
        raise ValueError("instante UTC terminado em Z")
    return datetime.strptime(valor, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def avaliar(doc: dict[str, object], agora: datetime) -> tuple[int, str]:
    if set(doc) != CAMPOS or doc["schema"] != SCHEMA:
        return 2, f"{ARQUIVO}: esperado {SCHEMA} com {sorted(CAMPOS)}"
    try:
        fim, inicio = instante(doc["valid_until"]), instante(doc["renovado_em"])
    except ValueError as erro:
        return 2, f"{ARQUIVO}: {erro}"
    aviso = doc["aviso_dias"]
    if not isinstance(aviso, int) or isinstance(aviso, bool) or not 1 <= aviso <= 13:
        return 2, f"{ARQUIVO}: aviso_dias inteiro de 1 a 13"
    if not inicio < fim or fim - inicio > JANELA_MAXIMA:
        return 2, f"{ARQUIVO}: janela renovado_em..valid_until invalida ou maior que 14 dias (N2)"
    resta = fim - agora
    horas = int(resta.total_seconds() // 3600)
    material, renove = "material staff/human de dev", f"Renove: {doc['runbook']}"
    if resta <= timedelta(0):
        return 1, f"{material} VENCEU em {doc['valid_until']}: o motor nao sobe. {renove}"
    if resta <= timedelta(days=aviso):
        return 1, f"{material} vence em {horas} h ({doc['valid_until']}). {renove}"
    return 0, f"material staff/human de dev valido ate {doc['valid_until']} (faltam {horas} h)"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--arquivo", type=Path, default=ARQUIVO)
    p.add_argument("--agora", help="AAAA-MM-DDTHH:MM:SSZ (testes)")
    a = p.parse_args(argv)
    try:
        doc = json.loads(a.arquivo.read_text(encoding="utf-8"))
        agora = instante(a.agora) if a.agora else datetime.now(UTC)
    except (OSError, ValueError) as erro:
        print(f"::error::{a.arquivo}: ilegivel ({type(erro).__name__})")
        return 2
    codigo, mensagem = avaliar(doc if isinstance(doc, dict) else {}, agora)
    print(("::error::" if codigo else "::notice::") + mensagem)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
