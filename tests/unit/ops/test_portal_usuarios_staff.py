"""`scripts/ops/portal_usuarios_staff.py`: CSV -> comandos Cognito e memberships, sem executar.

Cada recusa tem controle positivo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.ops import portal_usuarios_staff as us
from scripts.ops.portal_semear_membership import ISSUER_PADRAO, REVIEWED_PADRAO

from maezo.platform.engine_bootstrap.bootstrap import GRUPOS_DE_ATENDIMENTO
from maezo.portal.api.records import MembershipRecord

CAB = "nome,email,username,grupo,papel\n"
OK = CAB + (
    "Ana Teste,ana.teste@exemplo.com.br,ana.teste,enfermagem-triagem,escalonamento-tratar\n"
    "Ana Teste,ana.teste@exemplo.com.br,ana.teste,supervisao-atendimento,escalonamento-tratar\n"
    "Bruno Teste,bruno@exemplo.com.br,bruno.t,supervisao-atendimento,escalonamento-tratar\n"
)
SUB_A = "13ac3a7a-30c1-70b4-7f0c-73e26de7c128"
SUB_B = "f33c1ada-40e1-70cd-5b78-128958f04003"


def test_os_grupos_sao_os_do_bootstrap_do_motor() -> None:
    assert set(us.GRUPOS) == {g for g, _ in GRUPOS_DE_ATENDIMENTO}


def test_csv_valido_agrupa_por_username() -> None:
    pessoas = us.ler_csv(OK)
    assert [(p.username, p.grupos) for p in pessoas] == [
        ("ana.teste", ["enfermagem-triagem", "supervisao-atendimento"]),
        ("bruno.t", ["supervisao-atendimento"]),
    ]
    assert us.ler_csv("﻿" + OK.replace(",", ";"))[0].username == "ana.teste"  # Excel pt-BR


@pytest.mark.parametrize(
    ("linha", "trecho"),
    [
        ("X,x@exemplo.com.br,x@exemplo.com.br,enfermagem-triagem,escalonamento-tratar", "username nao pode"),
        ("X,x@exemplo.com.br,X@EXEMPLO.COM.BR,enfermagem-triagem,escalonamento-tratar", "username nao pode"),
        ("X,x@exemplo.com.br,x@outro,enfermagem-triagem,escalonamento-tratar", "username nao pode"),
        ("X,x@exemplo.com.br,xis.teste,juridico-contratos,escalonamento-tratar", "fora da lista"),
        ("X,x@exemplo.com.br,xis.teste,Enfermagem-Triagem,escalonamento-tratar", "fora da lista"),
        ("X,x@exemplo.com.br,xis.teste,enfermagem-triagem,admin", "papel"),
        ("X,nao-e-email,xis.teste,enfermagem-triagem,escalonamento-tratar", "e-mail"),
        ("X,x@exemplo.com.br,Xis,enfermagem-triagem,escalonamento-tratar", "username fora"),
        (",x@exemplo.com.br,xis.teste,enfermagem-triagem,escalonamento-tratar", "nome vazio"),
        ("X,x@exemplo.com.br,xis.teste,enfermagem-triagem", "colunas"),
        ("X,ana.teste@exemplo.com.br,outra.ana,enfermagem-triagem,escalonamento-tratar", "outro username"),
        ("X,outro@exemplo.com.br,ana.teste,plantao-clinico,escalonamento-tratar", "dois e-mails"),
        ("Ana Teste,ana.teste@exemplo.com.br,ana.teste,enfermagem-triagem,escalonamento-tratar", "repetida"),
    ],
)
def test_csv_recusa(linha: str, trecho: str) -> None:
    us.ler_csv(OK)  # controle positivo
    with pytest.raises(us.Recusa, match=trecho):
        us.ler_csv(OK + linha + "\n")


@pytest.mark.parametrize(
    "texto", ["", "nome,email,username,grupo\n", CAB, "email,nome,username,grupo,papel\n"]
)
def test_csv_sem_cabecalho_ou_sem_pessoa(texto: str) -> None:
    with pytest.raises(us.Recusa):
        us.ler_csv(texto)


def test_comandos_cognito_suprimem_o_convite_e_citam_cada_valor() -> None:
    pessoas = us.ler_csv(OK)
    roteiro = us.comandos_cognito(pessoas)
    criar = [
        linha for linha in roteiro.splitlines() if "admin-create-user" in linha and not linha.startswith("#")
    ]
    assert len(criar) == 2
    for linha in criar:
        assert "--message-action SUPPRESS" in linha and "Name=email_verified,Value=true" in linha
        assert f"--user-pool-id {us.POOL}" in linha
    assert "--username ana.teste " in criar[0] and "Value=ana.teste@exemplo.com.br" in criar[0]
    assert "Ana Teste" not in roteiro  # o nome nao vai ao Cognito nem ao arquivo
    assert "admin-set-user-password --no-permanent" in roteiro and "--temporary-password" not in roteiro
    # shlex.quote: um valor com espaco nunca vira dois argumentos
    malicioso = [us.Pessoa(username="a b", email="x@y.com", nome="n", grupos=["g"], papeis=["r"])]
    assert "--username 'a b'" in us.comandos_cognito(malicioso)


def test_memberships_validam_no_modelo_do_store() -> None:
    pessoas = us.ler_csv(OK)
    entrada, regs = us.memberships(
        pessoas,
        issuer=ISSUER_PADRAO,
        reviewed_until=REVIEWED_PADRAO,
        subs={"ana.teste": SUB_A, "bruno.t": SUB_B},
    )
    assert [e["subject"] for e in entrada] == [SUB_A, SUB_B]
    for r in regs:
        rec = MembershipRecord.model_validate_json(json.dumps(r["payload"]))
        assert rec.principal_ref.startswith("prn_") and len(rec.principal_ref) == 36
        assert rec.audience == "staff" and not r["previa"]
        assert rec.memberships[0].roles == ("escalonamento-tratar",)
    assert regs[0]["payload"]["memberships"][0]["groups"] == ["enfermagem-triagem", "supervisao-atendimento"]
    assert "ana.teste@exemplo.com.br" not in json.dumps([entrada, regs])  # e-mail nao vai ao banco


def test_memberships_previa_sem_sub_e_subs_incompletos() -> None:
    pessoas = us.ler_csv(OK)
    _, regs = us.memberships(pessoas, issuer=ISSUER_PADRAO, reviewed_until=REVIEWED_PADRAO, subs={})
    assert all(r["previa"] and r["payload"]["subject"].startswith(us.MARCADOR_SUB) for r in regs)
    with pytest.raises(us.Recusa, match="TODOS"):
        us.memberships(
            pessoas, issuer=ISSUER_PADRAO, reviewed_until=REVIEWED_PADRAO, subs={"ana.teste": SUB_A}
        )
    with pytest.raises(us.Recusa):
        us.ler_subs("ana.teste nao-e-uuid\n")


def test_memberships_recusam_reviewed_until_sem_fuso() -> None:
    pessoas = us.ler_csv(OK)
    with pytest.raises(Exception, match="aware"):
        us.memberships(pessoas, issuer=ISSUER_PADRAO, reviewed_until="2026-12-31T23:59:59", subs={})


def _rodar(tmp_path: Path, csv: str, *extra: str) -> Any:
    (tmp_path / "u.csv").write_text(csv, encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return us.main(["--csv", str(tmp_path / "u.csv"), "--out", str(out), *extra])


def test_cli_previa_nao_escreve_pessoas(tmp_path: Path, capsys: Any) -> None:
    assert _rodar(tmp_path, OK) == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["cognito-comandos.sh", "memberships.json"]
    assert json.loads(capsys.readouterr().out)["por_grupo"]["supervisao-atendimento"] == 2


def test_cli_com_subs_escreve_pessoas_e_nao_sobrescreve(tmp_path: Path) -> None:
    (tmp_path / "subs.txt").write_text(f"ana.teste {SUB_A}\nbruno.t {SUB_B}\n", encoding="utf-8")
    assert _rodar(tmp_path, OK, "--subs", str(tmp_path / "subs.txt")) == 0
    pessoas = json.loads((tmp_path / "out" / "pessoas.json").read_text(encoding="utf-8"))
    assert {p["subject"] for p in pessoas} == {SUB_A, SUB_B}
    with pytest.raises(us.Recusa, match="vazio"):
        _rodar(tmp_path, OK)


def test_cli_recusa_antes_de_escrever(tmp_path: Path) -> None:
    with pytest.raises(us.Recusa):
        _rodar(tmp_path, OK + "X,x@exemplo.com.br,xis,financeiro,escalonamento-tratar\n")
    assert list((tmp_path / "out").iterdir()) == []


def test_o_modelo_do_csv_em_docs_e_so_o_cabecalho() -> None:
    raiz = Path(__file__).resolve().parents[3]
    texto = (raiz / "docs/runbooks/portal-usuarios-staff-modelo.csv").read_text(encoding="utf-8")
    assert texto == CAB
