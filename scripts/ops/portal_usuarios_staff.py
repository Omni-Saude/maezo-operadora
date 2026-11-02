#!/usr/bin/env python3
"""Do CSV da operacao aos comandos Cognito e as memberships do portal staff, SEM executar nada.

Runbook: `docs/runbooks/portal-dev-provisionamento.md` §3.8 (o caminho de `plantao.teste` e
`atendimento.teste`) e `docs/runbooks/escalonamento-repin-dueDate.md` §6.
Modelo do CSV: `docs/runbooks/portal-usuarios-staff-modelo.csv`.

Entrada: CSV UTF-8 (`,` ou `;`) com o cabecalho EXATO ``nome,email,username,grupo,papel``; uma linha por
(pessoa, grupo). A mesma pessoa em dois grupos = duas linhas com o mesmo username e o mesmo e-mail.

Recusa (antes de escrever qualquer arquivo):
- grupo fora de ``GRUPOS`` (os 4 grupos humanos do SP-OP-ESCALATION-001,
  `engine_bootstrap.GRUPOS_DE_ATENDIMENTO`);
- papel fora de ``PAPEIS`` (`escalonamento-tratar`, o `required_roles` das tarefas do escalonamento;
  PROVISORIO, R-034);
- username igual ao e-mail ou com ``@`` (o pool tem `AliasAttributes=["email"]` e o Cognito recusa);
- e-mail malformado, username fora de ``[a-z0-9._-]{3,64}``, a mesma pessoa com e-mails diferentes ou
  o mesmo e-mail em duas pessoas.

Saida em ``--out`` (diretorio existente e vazio):
``cognito-comandos.sh``     os `admin-create-user` (`--message-action SUPPRESS`, `email_verified=true`) e o
                            `admin-get-user` que le o `sub` de cada um. NAO executados: o dono revisa e roda.
``pessoas.json``            o `PESSOAS` de `scripts/ops/portal_semear_membership.py` (sem e-mail, sem
                            nome). SO com ``--subs`` (o `sub` real de todos): sem ele nao e escrito.
``memberships.json``        cada `payload` montado por `portal_semear_membership.montar_registro` e
                            validado por `MembershipRecord.model_validate_json` (o modelo do store).
                            Sem ``--subs`` e PREVIA:
                            o `principal_ref` (`prn_<32hex>`) so e definitivo com o `sub` real.

Nao chama AWS nem banco. Imprime so contagens.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

POOL = "sa-east-1_9oKv7gHOJ"
REGIAO = "sa-east-1"
CABECALHO = ("nome", "email", "username", "grupo", "papel")
#: Os grupos humanos do SP-OP-ESCALATION-001 (`engine_bootstrap.GRUPOS_DE_ATENDIMENTO`; o teste confere).
GRUPOS = ("plantao-clinico", "enfermagem-triagem", "atendimento-humano", "supervisao-atendimento")
#: O papel que o gateway exige (`required_roles` contido nos `roles` da MESMA membership; provisorio, R-034).
PAPEIS = ("escalonamento-tratar",)
MARCADOR_SUB = "PENDENTE-sub-"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")
_SUB = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class Recusa(SystemExit):
    def __init__(self, motivo: str) -> None:
        super().__init__(f"RECUSADO: {motivo}")


@dataclass
class Pessoa:
    username: str
    email: str
    nome: str
    grupos: list[str] = field(default_factory=list)
    papeis: list[str] = field(default_factory=list)


def ler_csv(texto: str) -> list[Pessoa]:
    """Valida e agrupa por username. Puro."""
    texto = texto.lstrip("﻿")
    primeira = texto.splitlines()[0] if texto.strip() else ""
    delim = ";" if primeira.count(";") > primeira.count(",") else ","
    linhas = list(csv.reader(io.StringIO(texto), delimiter=delim))
    if not linhas or tuple(c.strip().lower() for c in linhas[0]) != CABECALHO:
        raise Recusa(f"cabecalho do CSV tem de ser exatamente {','.join(CABECALHO)}")
    pessoas: dict[str, Pessoa] = {}
    donos_email: dict[str, str] = {}
    for n, linha in enumerate(linhas[1:], start=2):
        if not any(c.strip() for c in linha):
            continue
        if len(linha) != len(CABECALHO):
            raise Recusa(f"linha {n}: {len(linha)} colunas, esperado {len(CABECALHO)}")
        nome, email, username, grupo, papel = (c.strip() for c in linha)
        email_n = email.lower()
        if not nome:
            raise Recusa(f"linha {n}: nome vazio")
        if not _EMAIL.fullmatch(email):
            raise Recusa(f"linha {n}: e-mail malformado")
        if username.lower() == email_n or "@" in username:
            raise Recusa(f"linha {n}: username nao pode ser o e-mail (o pool usa o e-mail como alias)")
        if not _USERNAME.fullmatch(username):
            raise Recusa(
                f"linha {n}: username fora de [a-z0-9._-]{{3,64}} (minusculas, comeca com letra/numero)"
            )
        if grupo not in GRUPOS:
            raise Recusa(f"linha {n}: grupo {grupo!r} fora da lista {list(GRUPOS)}")
        if papel not in PAPEIS:
            raise Recusa(f"linha {n}: papel {papel!r} fora da lista {list(PAPEIS)}")
        if donos_email.setdefault(email_n, username) != username:
            raise Recusa(f"linha {n}: o mesmo e-mail ja pertence a outro username")
        pessoa = pessoas.setdefault(username, Pessoa(username=username, email=email_n, nome=nome))
        if pessoa.email != email_n:
            raise Recusa(f"linha {n}: {username} aparece com dois e-mails")
        if grupo in pessoa.grupos and papel in pessoa.papeis:
            raise Recusa(f"linha {n}: linha repetida para {username}/{grupo}")
        if grupo not in pessoa.grupos:
            pessoa.grupos.append(grupo)
        if papel not in pessoa.papeis:
            pessoa.papeis.append(papel)
    if not pessoas:
        raise Recusa("o CSV nao tem nenhuma pessoa")
    return list(pessoas.values())


def comandos_cognito(pessoas: list[Pessoa], *, pool: str = POOL) -> str:
    """O roteiro bash (nao executado). Cada valor passa por `shlex.quote`."""
    q = shlex.quote
    partes = [
        "#!/usr/bin/env bash",
        "# GERADO por scripts/ops/portal_usuarios_staff.py. NAO EXECUTADO.",
        "# Revise e rode com AWS_PROFILE=adm-dev.",
        "# admin-create-user com --message-action SUPPRESS: nenhum e-mail sai; a senha temporaria e",
        "# definida a parte (admin-set-user-password --no-permanent, entregue por canal seguro;",
        "# nunca neste arquivo).",
        "set -euo pipefail",
        "",
    ]
    for p in pessoas:
        partes += [
            f"# {p.username}: grupos={','.join(p.grupos)} papeis={','.join(p.papeis)}",
            f"aws cognito-idp admin-create-user --region {REGIAO} --user-pool-id {q(pool)} "
            f"--username {q(p.username)} --message-action SUPPRESS "
            f"--user-attributes Name=email,Value={q(p.email)} Name=email_verified,Value=true",
            f"echo {q(p.username)} $(aws cognito-idp admin-get-user --region {REGIAO} "
            f"--user-pool-id {q(pool)} --username {q(p.username)} "
            "--query \"UserAttributes[?Name=='sub'].Value\" --output text)",
            "",
        ]
    return "\n".join(partes)


def ler_subs(texto: str) -> dict[str, str]:
    """`username sub` por linha (a saida dos `echo` do roteiro)."""
    subs: dict[str, str] = {}
    for linha in texto.splitlines():
        if not linha.strip():
            continue
        partes = linha.split()
        if len(partes) != 2 or not _SUB.fullmatch(partes[1]):
            raise Recusa("--subs: cada linha e `<username> <sub uuid>`")
        subs[partes[0]] = partes[1]
    return subs


def memberships(
    pessoas: list[Pessoa], *, issuer: str, reviewed_until: str, tenant: str = "amh", subs: dict[str, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(`PESSOAS` do semeador, `payload` de cada um validado por `MembershipRecord`)."""
    from scripts.ops.portal_semear_membership import montar_registro

    from maezo.portal.api.records import MembershipRecord

    if subs and set(subs) != {p.username for p in pessoas}:
        raise Recusa("--subs tem de trazer o sub de TODOS os usernames do CSV, e so deles")
    entrada: list[dict[str, Any]] = []
    registros: list[dict[str, Any]] = []
    for p in pessoas:
        subject = subs.get(p.username, f"{MARCADOR_SUB}{p.username}")
        entrada.append({"username": p.username, "subject": subject, "groups": p.grupos, "roles": p.papeis})
        reg = montar_registro(
            tenant=tenant,
            issuer=issuer,
            subject=subject,
            groups=p.grupos,
            roles=p.papeis,
            reviewed_until=reviewed_until,
        )
        MembershipRecord.model_validate_json(json.dumps(reg, sort_keys=True, separators=(",", ":")))
        registros.append({"username": p.username, "previa": p.username not in subs, "payload": reg})
    return entrada, registros


def main(argv: list[str] | None = None) -> int:
    from scripts.ops.portal_semear_membership import ISSUER_PADRAO, REVIEWED_PADRAO

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True, help="diretorio existente e vazio")
    ap.add_argument(
        "--subs", type=Path, default=None, help="`username sub` por linha (depois do admin-get-user)"
    )
    ap.add_argument("--issuer", default=ISSUER_PADRAO)
    ap.add_argument("--reviewed-until", default=REVIEWED_PADRAO)
    args = ap.parse_args(argv)
    if not args.out.is_dir() or any(args.out.iterdir()):
        raise Recusa("--out tem de ser um diretorio existente e vazio (nada e sobrescrito)")
    pessoas = ler_csv(args.csv.read_text(encoding="utf-8"))
    subs = ler_subs(args.subs.read_text(encoding="utf-8")) if args.subs is not None else {}
    entrada, registros = memberships(
        pessoas, issuer=args.issuer, reviewed_until=args.reviewed_until, subs=subs
    )
    args.out.joinpath("cognito-comandos.sh").write_text(
        comandos_cognito(pessoas), encoding="utf-8", newline="\n"
    )
    if subs:  # sem o `sub` real o semeador gravaria uma linha que nenhum login encontra
        args.out.joinpath("pessoas.json").write_text(json.dumps(entrada, indent=1), encoding="utf-8")
    args.out.joinpath("memberships.json").write_text(json.dumps(registros, indent=1), encoding="utf-8")
    por_grupo = {g: sum(g in p.grupos for p in pessoas) for g in GRUPOS}
    print(json.dumps({"pessoas": len(pessoas), "por_grupo": por_grupo, "previa": not subs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    raise SystemExit(main())
