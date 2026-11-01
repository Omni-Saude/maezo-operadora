#!/usr/bin/env python3
"""Monta OFFLINE o catalogo e as entradas `human` da versao NOVA do SP-OP-ESCALATION-001 (DL-0145, DL-0143).

Runbook: `docs/runbooks/escalonamento-repin-dueDate.md` (renovacao + repin num movimento so) e
`docs/runbooks/material-staff-na-aws.md` passo 5. Modelo: `scripts/ops/staff_material_cancel_catalogo.py`.

Por que existe: o BPMN com `dueDate` (#848) muda o `process_definition_id` quando e implantado, e o catalogo
humano do portal admite uma versao FIXA (`SP-OP-ESCALATION-001:2`). Sem repin, as tarefas da versao nova ficam
fora da fila e o job T1.5 emite `human_task_skipped` com `reason=definition_not_admitted`.

Subcomandos:

``montar``       OFFLINE, sem rede. Le dois arquivos locais (o catalogo e a admissao Q2 VIVOS, do segredo
                 `staff-job/materials`, extraidos por ``extrair-vivo``), clona as 2 tarefas do escalonamento
                 da versao mais alta ja admitida para cada ``--definicao ID=SHA256`` nova e escreve em
                 ``--out``
                 ``task-catalog.json`` (JCS) e ``entradas-humanas.json`` (so as entradas NOVAS: o `gerar`
                 ACRESCENTA as entradas, `staff_material_renovar_dev.py` passo 8). Antes de escrever, refaz o
                 que o `gerar` faz com a admissao (troca o `catalog_digest`, acrescenta as entradas, sobe a
                 revisao) e roda o MESMO `approver.review_admission` que o `gerar` roda antes de assinar, mais
                 os limites de tamanho (65536 do `strict_loads`, da coluna `RECORD_` e do Secrets Manager).
``ensaio``       igual ao ``montar``, sem ``--definicao``: usa um id de ENSAIO (`SP-OP-ESCALATION-001:<N>:` +
                 uuid zero) e o SHA-256 do BPMN do repositorio. So confere e imprime; NAO escreve insumo.
                 Serve para provar, ANTES do deploy, que o catalogo e a admissao aceitam a versao nova.
``extrair-vivo`` (dono, leitura de segredo, sem efeito) le `job/task-catalog.json` e `job/task-admission.json`
                 do segredo `staff-job/materials` e grava os dois em ``--out``. Nao imprime valor; so digests
                 e
                 o tamanho do segredo (para projetar o limite de 65536).

Imprime so digests, contagens e tamanhos.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SEGREDO = "maezo-operadora/dev/staff-job/materials"
PROCESSO = "SP-OP-ESCALATION-001"
#: As 2 tarefas humanas do BPMN (`UT_TratarEscalonamento` dinamica pela DMN, `UT_SupervisorAssume` estatica).
TAREFAS = ("UT_TratarEscalonamento", "UT_SupervisorAssume")
BPMN_REPO = Path("spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn")
#: Limites medidos (`maezo.portal.engine.profile.strict_loads`, coluna `RECORD_`, Secrets Manager).
LIMITE_JSON = 65536
LIMITE_ADMISSAO = 65536
LIMITE_SEGREDO = 65536
UUID_ENSAIO = "00000000-0000-0000-0000-000000000000"
_ID = re.compile(
    rf"{PROCESSO}:([1-9][0-9]*):[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}"
)
_HEX64 = re.compile(r"[0-9a-f]{64}")


class Recusa(SystemExit):
    """Recusa com mensagem (codigo 1 e texto no stderr, como as outras ferramentas de material)."""

    def __init__(self, motivo: str) -> None:
        super().__init__(f"RECUSADO: {motivo}")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def ler_definicao(texto: str) -> tuple[str, int, str]:
    """`SP-OP-ESCALATION-001:<versao>:<uuid>=<sha256>` -> (id, versao, sha256)."""
    pid, sep, digest = texto.partition("=")
    m = _ID.fullmatch(pid)
    if not sep or m is None or not _HEX64.fullmatch(digest):
        raise Recusa(f"--definicao invalida (esperado {PROCESSO}:<versao>:<uuid>=<sha256 hex>): {texto!r}")
    return pid, int(m.group(1)), digest


def base_admitida(catalogo_vivo: dict[str, Any], admissao_viva: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """A versao mais alta do escalonamento ja admitida e, por tarefa, a entrada do catalogo e a `human`."""
    vivas = [e for e in catalogo_vivo["entries"] if e["process_definition_key"] == PROCESSO]
    if not vivas:
        raise Recusa(f"o catalogo vivo nao tem nenhuma tarefa de {PROCESSO}: nada a clonar")
    versao = max(int(e["process_definition_version"]) for e in vivas)
    da_base = {e["task_definition_key"]: e for e in vivas if int(e["process_definition_version"]) == versao}
    if set(da_base) != set(TAREFAS):
        raise Recusa(
            f"a versao {versao} admitida nao tem exatamente as tarefas {list(TAREFAS)}: {sorted(da_base)}"
        )
    pid_base = {e["process_definition_id"] for e in da_base.values()}
    if len(pid_base) != 1:
        raise Recusa(f"a versao {versao} aparece com mais de um process_definition_id no catalogo")
    humanas = {
        e["task_definition_key"]: e
        for e in admissao_viva["human"]["entries"]
        if e["process_definition_id"] in pid_base
    }
    if set(humanas) != set(TAREFAS):
        raise Recusa(f"a admissao viva nao tem as entradas `human` da versao {versao}: {sorted(humanas)}")
    return versao, {t: (da_base[t], humanas[t]) for t in TAREFAS}


def montar(
    catalogo_vivo: dict[str, Any],
    admissao_viva: dict[str, Any],
    definicoes: list[tuple[str, int, str]],
    *,
    dmn: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Catalogo novo (vivo + tarefas de cada definicao nova) e as entradas `human` NOVAS. Puro: sem rede."""
    if not definicoes:
        raise Recusa("informe ao menos uma --definicao")
    versao_base, base = base_admitida(catalogo_vivo, admissao_viva)
    ja = {e["process_definition_id"] for e in catalogo_vivo["entries"]}
    ja |= {e["process_definition_id"] for e in admissao_viva["human"]["entries"]}
    vistos: set[str] = set()
    for pid, versao, _ in definicoes:
        if pid in ja:
            raise Recusa(f"{pid} ja esta admitido (catalogo ou admissao vivos)")
        if pid in vistos:
            raise Recusa(f"{pid} repetido em --definicao")
        if versao <= versao_base:
            raise Recusa(
                f"versao {versao} nao e posterior a admitida ({versao_base}); a versao so anda para a frente"
            )
        vistos.add(pid)
    catalogo = json.loads(json.dumps(catalogo_vivo))
    novas: list[dict[str, Any]] = []
    for pid, versao, digest in definicoes:
        for tarefa in TAREFAS:
            entrada, humana = base[tarefa]
            nova = json.loads(json.dumps(entrada))
            nova.update(
                process_definition_id=pid,
                process_definition_version=str(versao),
                process_definition_digest=digest,
            )
            if dmn is not None and nova["group_domain"]["kind"] == "dmn":
                nova["group_domain"].update(
                    dmn_definition_id=dmn["id"],
                    dmn_definition_version=dmn["version"],
                    dmn_resource_digest=dmn["sha256"],
                )
            catalogo["entries"].append(nova)
            nova_h = json.loads(json.dumps(humana))
            nova_h["process_definition_id"] = pid
            novas.append(nova_h)
    return catalogo, novas


def admissao_como_o_gerar(
    admissao_viva: dict[str, Any], catalogo_raw: bytes, novas: list[dict[str, Any]]
) -> bytes:
    """O que o `gerar` faz com a admissao antes de assinar (passo 8): catalogo, entradas e revisao.

    A janela, o trust e a continuidade tambem mudam no `gerar`, mas nao entram no `review_admission`.
    """
    from maezo.portal.engine.profile import canonicalize

    reg = json.loads(json.dumps(admissao_viva))
    reg["admission_revision"] = str(int(reg["admission_revision"]) + 1)
    reg["catalog"] = dict(reg["catalog"], catalog_digest=sha(catalogo_raw))
    reg["human"] = dict(reg["human"], entries=[*reg["human"]["entries"], *novas])
    return canonicalize(reg)


def conferir(
    catalogo: dict[str, Any],
    admissao_viva: dict[str, Any],
    novas: list[dict[str, Any]],
    *,
    tamanho_segredo_job: int | None = None,
    catalogo_vivo_raw: bytes | None = None,
) -> dict[str, Any]:
    """`review_admission` offline (o mesmo do `gerar`) + limites. Recusa com o motivo; devolve so medidas."""
    from tools.staff_materials import approver
    from tools.staff_materials.secure_io import MaterialError

    from maezo.portal.engine.profile import canonicalize, strict_loads

    catalogo_raw = canonicalize(catalogo)
    if len(catalogo_raw) > LIMITE_JSON:
        raise Recusa(f"catalogo com {len(catalogo_raw)} bytes passa do limite {LIMITE_JSON} do strict_loads")
    strict_loads(catalogo_raw)
    reg_raw = admissao_como_o_gerar(admissao_viva, catalogo_raw, novas)
    if len(reg_raw) > LIMITE_ADMISSAO:
        raise Recusa(f"admissao com {len(reg_raw)} bytes passa do limite {LIMITE_ADMISSAO} da coluna RECORD_")
    try:
        _, digest_adm, linhas = approver.review_admission(reg_raw, catalogo_raw)
    except MaterialError as exc:
        raise Recusa(f"review_admission: {exc}") from None
    medidas: dict[str, Any] = {
        "review_admission": "OK",
        "admission_sha256_ensaio": digest_adm,
        "catalog_sha256": sha(catalogo_raw),
        "catalog_bytes": len(catalogo_raw),
        "admission_bytes": len(reg_raw),
        "folga_admissao_bytes": LIMITE_ADMISSAO - len(reg_raw),
        "entradas_catalogo": len(catalogo["entries"]),
        "entradas_human_novas": len(novas),
        "linhas_escalonamento": [linha for linha in linhas if PROCESSO in linha],
    }
    if tamanho_segredo_job is not None and catalogo_vivo_raw is not None:
        adm_viva_raw = canonicalize(admissao_viva)
        # O segredo do job guarda catalogo e admissao em base64 (4/3); o resto do segredo nao muda de tamanho.
        delta = (
            _b64(len(catalogo_raw))
            - _b64(len(catalogo_vivo_raw))
            + _b64(len(reg_raw))
            - _b64(len(adm_viva_raw))
        )
        projetado = tamanho_segredo_job + delta
        medidas["segredo_job_projetado_bytes"] = projetado
        if projetado > LIMITE_SEGREDO:
            raise Recusa(f"segredo do job projetado em {projetado} bytes passa do limite {LIMITE_SEGREDO}")
    return medidas


def _b64(n: int) -> int:
    return 4 * ((n + 2) // 3)


def _ler_json(caminho: Path) -> dict[str, Any]:
    valor = json.loads(caminho.read_bytes())
    if not isinstance(valor, dict):
        raise Recusa(f"{caminho.name}: esperado um objeto JSON")
    return valor


def _dmn(args: argparse.Namespace) -> dict[str, str] | None:
    partes = (args.dmn_definition_id, args.dmn_definition_version, args.dmn_sha256)
    if all(p is None for p in partes):
        return None
    if any(p is None for p in partes):
        raise Recusa("--dmn-definition-id, --dmn-definition-version e --dmn-sha256 andam juntos")
    if not _HEX64.fullmatch(args.dmn_sha256) or not re.fullmatch(r"[1-9][0-9]*", args.dmn_definition_version):
        raise Recusa("--dmn-sha256 (hex 64) ou --dmn-definition-version (inteiro > 0) invalido")
    return {"id": args.dmn_definition_id, "version": args.dmn_definition_version, "sha256": args.dmn_sha256}


def _vivos(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    cat_raw = args.catalogo_vivo.read_bytes()
    return _ler_json(args.catalogo_vivo), _ler_json(args.admissao_viva), cat_raw


def cmd_montar(args: argparse.Namespace) -> int:
    catalogo_vivo, admissao_viva, cat_raw = _vivos(args)
    definicoes = [ler_definicao(d) for d in args.definicao]
    if args.bpmn_repo is not None:
        local = sha(args.bpmn_repo.read_bytes())
        ultima = max(definicoes, key=lambda d: d[1])
        if ultima[2] != local:
            raise Recusa(
                f"o BPMN implantado na versao {ultima[1]} (sha {ultima[2][:16]}) nao e o do repositorio "
                f"(sha {local[:16]}): a versao viva nao e a do main com dueDate"
            )
    catalogo, novas = montar(catalogo_vivo, admissao_viva, definicoes, dmn=_dmn(args))
    medidas = conferir(
        catalogo,
        admissao_viva,
        novas,
        tamanho_segredo_job=args.tamanho_segredo_job,
        catalogo_vivo_raw=cat_raw,
    )
    from maezo.portal.engine.profile import canonicalize

    catalogo_raw = canonicalize(catalogo)
    args.out.joinpath("task-catalog.json").write_bytes(catalogo_raw)
    args.out.joinpath("entradas-humanas.json").write_bytes(json.dumps(novas, sort_keys=True).encode())
    medidas["arquivos"] = ["task-catalog.json", "entradas-humanas.json"]
    medidas["definicoes"] = [d[0] for d in definicoes]
    print(json.dumps(medidas, sort_keys=True))
    return 0


def cmd_ensaio(args: argparse.Namespace) -> int:
    catalogo_vivo, admissao_viva, cat_raw = _vivos(args)
    versao_base, _ = base_admitida(catalogo_vivo, admissao_viva)
    versao = args.versao if args.versao is not None else versao_base + 1
    digest = sha(args.bpmn_repo.read_bytes())
    pid = f"{PROCESSO}:{versao}:{UUID_ENSAIO}"
    catalogo, novas = montar(catalogo_vivo, admissao_viva, [(pid, versao, digest)], dmn=_dmn(args))
    medidas = conferir(
        catalogo,
        admissao_viva,
        novas,
        tamanho_segredo_job=args.tamanho_segredo_job,
        catalogo_vivo_raw=cat_raw,
    )
    medidas.update(
        ensaio=True,
        versao_admitida=versao_base,
        versao_ensaiada=versao,
        bpmn_repo_sha256=digest,
        aviso="ENSAIO: id de definicao ficticio; nenhum insumo escrito. Depois do deploy, rode `montar`.",
    )
    print(json.dumps(medidas, sort_keys=True))
    return 0


def cmd_extrair_vivo(args: argparse.Namespace) -> int:  # pragma: no cover - so o dono roda (le segredo)
    import boto3  # type: ignore[import-untyped]  # so o dono roda (le segredo)

    cliente = boto3.client("secretsmanager", region_name="sa-east-1")
    texto = cliente.get_secret_value(SecretId=SEGREDO)["SecretString"]
    job = json.loads(texto)
    saidas = {
        "catalogo-vivo.json": base64.b64decode(job["job/task-catalog.json"]),
        "admissao-viva.json": base64.b64decode(job["job/task-admission.json"]),
    }
    for nome, bruto in saidas.items():
        args.out.joinpath(nome).write_bytes(bruto)
    resumo: dict[str, Any] = {nome: {"sha256": sha(b), "bytes": len(b)} for nome, b in saidas.items()}
    resumo["tamanho_segredo_job"] = len(texto.encode())
    print(json.dumps(resumo, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def comuns(s: argparse.ArgumentParser) -> None:
        s.add_argument(
            "--catalogo-vivo", type=Path, required=True, help="job/task-catalog.json (extrair-vivo)"
        )
        s.add_argument(
            "--admissao-viva", type=Path, required=True, help="job/task-admission.json (extrair-vivo)"
        )
        s.add_argument(
            "--tamanho-segredo-job", type=int, default=None, help="bytes do segredo (extrair-vivo)"
        )
        s.add_argument("--dmn-definition-id", default=None, help="so se a escalation_routing tambem mudou")
        s.add_argument("--dmn-definition-version", default=None)
        s.add_argument("--dmn-sha256", default=None)

    m = sub.add_parser("montar", help="OFFLINE: catalogo + entradas human da(s) versao(oes) nova(s)")
    comuns(m)
    m.add_argument(
        "--definicao",
        action="append",
        required=True,
        help=f"{PROCESSO}:<versao>:<uuid>=<sha256 dos bytes do BPMN implantado>; repetivel",
    )
    m.add_argument(
        "--bpmn-repo", type=Path, default=None, help="confere o sha da versao mais alta com este arquivo"
    )
    m.add_argument("--out", type=Path, required=True, help="diretorio (existente) de saida")
    m.set_defaults(func=cmd_montar)

    e = sub.add_parser("ensaio", help="OFFLINE: confere com um id ficticio; nao escreve insumo")
    comuns(e)
    e.add_argument("--versao", type=int, default=None, help="versao a ensaiar (padrao: admitida + 1)")
    e.add_argument("--bpmn-repo", type=Path, default=BPMN_REPO)
    e.set_defaults(func=cmd_ensaio)

    x = sub.add_parser("extrair-vivo", help="DONO: le o segredo do job e grava catalogo/admissao vivos")
    x.add_argument("--out", type=Path, required=True)
    x.set_defaults(func=cmd_extrair_vivo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    resultado: int = args.func(args)
    return resultado


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    raise SystemExit(main())
