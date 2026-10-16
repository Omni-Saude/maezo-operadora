"""`python -m tools.staff_ops material`: o material staff/human do portal NA AWS (DL-0113).

Runbook: `docs/runbooks/material-staff-na-aws.md`. Substitui o volume Docker `maezo-onda2b-materials` do
notebook do dono: a raiz de assinatura de DEV (delegacao N1, so dev) mora num S3 dedicado (CMK propria) e
o resto do que o `gerar` lia do volume e REMONTADO dos segredos vivos (`tools.staff_materials.restaurar`).
Esta tarefa NUNCA grava segredo (a SCP `p-9m8f47yd` nega `PutSecretValue` a task role e a role nao o
pede): ela escreve a saida no S3 e o operador publica com a OrganizationAccountAccessRole.

Modo (variavel `MATERIAL_MODO`; parametros por `containerOverrides.environment`, nunca segredo):

``verificar``   DRY-RUN SEM EFEITO: le os segredos vivos, remonta tudo em memoria, confere por hash e
                imprime so caminho/tamanho/SHA-256 truncado. Nao escreve em lugar nenhum.
``ensaio``      raiz EFEMERA de teste (so em `TMPDIR`) + `gerar` completo + verificadores offline
                (`decode_bundle`, `verify_materials`, `review_admission`, `rows.verify`) + tamanhos dos
                documentos assinados ja com as 4 entradas do CANCEL-001. Nada vai para o S3.
``raiz-gerar``  gera a raiz Ed25519 de dev DENTRO da tarefa e a guarda no S3 (`raiz/<id>/`), sem
                sobrescrever. Imprime so o SHA-256 da SPKI (o `root_key_sha256` que o dono aprova) e
                publica a chave PUBLICA em `publico/<id>/installation-root.der` para conferencia.
``gerar``       renovacao real: raiz do S3 + `gerar` com `--nova-raiz-sha256` (o hash que o dono
                aprovou) + conferencia `rows.verify`; grava `saida/<id>/{ops-secrets,public}/` no S3.

Variaveis: ``MATERIAL_BUCKET``, ``MATERIAL_KMS_KEY_ARN``, ``MATERIAL_SECRET_{NATIVE,STAFF,JOB,ROWS,
ASSIGNMENT,SYN}`` (ARN; SYN opcional), ``MATERIAL_RAIZ_ID``, ``MATERIAL_RAIZ_SHA256``, ``MATERIAL_SAIDA_ID``,
``MATERIAL_SUFIXO`` (AAAAMMDD), ``MATERIAL_CATALOGO`` / ``MATERIAL_ENTRADAS_HUMANAS`` (chaves em `insumos/`).
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .common import OpsError, env, parse_json_object, secrets_client

MODOS = ("verificar", "ensaio", "raiz-gerar", "gerar")
#: Limite do Secrets Manager por segredo (`SecretString`); o ensaio mede cada saida contra ele.
LIMITE_SEGREDO = 65536
#: Limite da coluna `RECORD_` da admissao (`engine-native-install.sql`).
LIMITE_ADMISSAO = 65536
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")
_SUFIXO = re.compile(r"^[0-9]{8}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
#: As 4 tarefas do SP-OP-CANCEL-001 que a admissao humana ganha na proxima renovacao (cancel-001-pf §3.3).
TAREFAS_CANCEL = (
    "UT_AnaliseRescisao",
    "UT_CoordenacaoCancelamento",
    "UT_ExecutarCancelamentoCadastro",
    "UT_CoordenacaoExecucaoCadastro",
)


def identificador(nome: str) -> str:
    valor = env(nome)
    if not _ID.fullmatch(valor):
        raise OpsError(f"{nome}: esperado [a-z0-9-], 3 a 63 caracteres")
    return valor


def carregar_segredos(cliente: Any) -> Any:
    """Le os segredos vivos (AWSCURRENT) pelos ARNs exatos das variaveis."""
    from tools.staff_materials.restaurar import Segredos

    def um(variavel: str) -> dict[str, Any]:
        resposta = cliente.get_secret_value(SecretId=env(variavel)).get("SecretString")
        if not isinstance(resposta, str):
            raise OpsError(f"{variavel}: segredo sem SecretString")
        return parse_json_object(resposta, variavel)

    syn = um("MATERIAL_SECRET_SYN") if os.environ.get("MATERIAL_SECRET_SYN") else None
    return Segredos(
        native=um("MATERIAL_SECRET_NATIVE"),
        staff=um("MATERIAL_SECRET_STAFF"),
        job=um("MATERIAL_SECRET_JOB"),
        rows=um("MATERIAL_SECRET_ROWS"),
        assignment=um("MATERIAL_SECRET_ASSIGNMENT"),
        syn=syn,
    )


def gravar_arvore(base: Path, arvore: dict[str, bytes]) -> None:
    """Grava `{caminho: bytes}` em `base` (NOVO, 0700; arquivos 0400, O_EXCL, sem symlink)."""
    from tools.staff_materials.secure_io import PRIVATE, new_private_directory, subdirectory, write_new

    raiz = new_private_directory(base)
    pastas: dict[str, Path] = {}
    for caminho in sorted(arvore):
        pasta, _, nome = caminho.rpartition("/")
        destino = raiz
        if pasta:
            if pasta not in pastas:
                pastas[pasta] = subdirectory(raiz, pasta)
            destino = pastas[pasta]
        write_new(destino / nome, arvore[caminho], PRIVATE)


def restaurar_em(base: Path, segredos: Any) -> Any:
    """Remonta os materiais em `base/{materials,estado,fontes}` e devolve o `Restaurado`."""
    from tools.staff_materials.restaurar import restaurar
    from tools.staff_materials.secure_io import new_private_directory

    restaurado = restaurar(segredos)
    raiz = new_private_directory(base)
    gravar_arvore(raiz / "materials", restaurado.materiais)
    gravar_arvore(raiz / "estado", restaurado.estado)
    gravar_arvore(raiz / "fontes", restaurado.fontes)
    return restaurado


def imprimir_relatorio(restaurado: Any) -> None:
    print(json.dumps({"conferencias": sorted(restaurado.conferencias), "fatos": restaurado.fatos}))
    for aviso in restaurado.avisos:
        print(f"AVISO {aviso}")
    for linha in restaurado.relatorio:
        print(f"{linha['arvore']}/{linha['caminho']} bytes={linha['bytes']} sha256={linha['sha256_16']}")


def argumentos_gerar(
    base: Path, *, sufixo: str, saida: Path, raiz_sha256: str, catalogo: Path | None, entradas: Path | None
) -> Any:
    from argparse import Namespace

    return Namespace(
        cmd="gerar",
        anterior=base / "estado",
        materials=base / "materials",
        approver=base / "aprov",
        generate_spec=None,
        conexoes=base / "fontes" / "conexoes-staff.json",
        assignment_source=base / "fontes" / "assignment-source.json",
        sufixo=sufixo,
        out=saida,
        repo=Path("/app"),
        nova_raiz_sha256=raiz_sha256,
        catalogo=catalogo,
        entradas_humanas=entradas,
    )


def _carregar_renovar() -> Any:
    """Carrega `scripts/ops/staff_material_renovar_dev.py` por CAMINHO (na imagem `scripts/` nao e pacote)."""
    import importlib.util

    caminho = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "staff_material_renovar_dev.py"
    especificacao = importlib.util.spec_from_file_location("staff_material_renovar_dev", caminho)
    if especificacao is None or especificacao.loader is None:
        raise OpsError("staff_material_renovar_dev.py ausente da imagem")
    modulo = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(modulo)
    return modulo


def rodar_gerar(args: Any) -> dict[str, Any]:
    """Roda o `gerar` versionado e devolve o `public/resumo.json` (so digests e ids publicos)."""
    renovar = _carregar_renovar()
    if renovar.gerar(args) != 0:
        raise OpsError("gerar terminou com erro")
    return dict(json.loads((args.out / "public" / "resumo.json").read_bytes()))


def conferir_linhas(saida: Path) -> None:
    """`rows.verify` (os verificadores do runtime) sobre o segredo de linhas recem-gerado."""
    from . import rows as rows_tool

    documento = json.loads((saida / "ops-secrets" / "rows.json").read_bytes())
    rows_tool.verify(rows_tool.parse(documento))


def medir(saida: Path) -> dict[str, int]:
    """Tamanhos (bytes) dos documentos assinados e dos segredos de saida, sem nenhum conteudo."""
    from tools.staff_materials.approver import ADMISSION_DOMAIN

    medidas: dict[str, int] = {}
    for caminho in sorted((saida / "ops-secrets").iterdir()):
        medidas[f"segredo/{caminho.name}"] = caminho.stat().st_size
    admissao = (saida / "public" / "portal-read-admission.json").read_bytes()
    medidas["mensagem_assinada/admissao_q2"] = len(ADMISSION_DOMAIN) + len(admissao)
    medidas["mensagem_assinada/designacao"] = (saida / "public" / "designation.json").stat().st_size
    medidas["mensagem_assinada/prova_instalacao"] = (
        (saida / "public" / "installation-proof.json").stat().st_size
    )
    return medidas


def projetar_cancel(saida: Path) -> dict[str, int]:
    """Tamanho da admissao Q2 se ganhar as 4 entradas do CANCEL-001 (clones da 1a entrada `human`)."""
    from tools.staff_materials.approver import ADMISSION_DOMAIN

    from maezo.portal.engine.profile import canonicalize

    registro = json.loads((saida / "public" / "portal-read-admission.json").read_bytes())
    entradas = registro["human"]["entries"]
    modelo = entradas[0]
    existentes = {(e["process_definition_id"], e["task_definition_key"]) for e in entradas}
    novas = []
    for tarefa in TAREFAS_CANCEL:
        clone = dict(modelo, process_definition_id="SP-OP-CANCEL-001:1:ensaio", task_definition_key=tarefa)
        if (clone["process_definition_id"], tarefa) in existentes:
            raise OpsError("ensaio: tarefa do cancelamento ja admitida; nada a projetar")
        novas.append(clone)
    projetado = dict(registro, human=dict(registro["human"], entries=[*entradas, *novas]))
    tamanho = len(ADMISSION_DOMAIN) + len(canonicalize(projetado))
    return {
        "mensagem_assinada/admissao_q2_com_4_cancel": tamanho,
        "limite_coluna_RECORD_": LIMITE_ADMISSAO,
        "folga_bytes": LIMITE_ADMISSAO - tamanho,
    }


def modo_verificar() -> int:
    restaurado = _restaurar_efemero(gravar=False)
    imprimir_relatorio(restaurado)
    print("verificar: OK (nada foi escrito)")
    return 0


def _restaurar_efemero(*, gravar: bool, base: Path | None = None) -> Any:
    segredos = carregar_segredos(secrets_client())
    if gravar and base is not None:
        return restaurar_em(base, segredos)
    from tools.staff_materials.restaurar import restaurar

    return restaurar(segredos)


def _tmp() -> Path:
    return Path(env("TMPDIR"))


def modo_ensaio() -> int:
    from tools.staff_materials import approver
    from tools.staff_materials.secure_io import new_private_directory

    sufixo = os.environ.get("MATERIAL_SUFIXO") or datetime.now(UTC).strftime("%Y%m%d")
    if not _SUFIXO.fullmatch(sufixo):
        raise OpsError("MATERIAL_SUFIXO: esperado AAAAMMDD")
    base = _tmp() / "ensaio"
    restaurado = _restaurar_efemero(gravar=True, base=base)
    imprimir_relatorio(restaurado)
    aprov = new_private_directory(base / "aprov")
    raiz_sha = approver.root_keygen(aprov / "root", passphrase=None, plaintext=True)
    saida = base / "saida"
    resumo = rodar_gerar(
        argumentos_gerar(base, sufixo=sufixo, saida=saida, raiz_sha256=raiz_sha, catalogo=None, entradas=None)
    )
    conferir_linhas(saida)
    medidas = {**medir(saida), **projetar_cancel(saida)}
    excedidos = [k for k, v in medidas.items() if k.startswith("segredo/") and v >= LIMITE_SEGREDO]
    print(json.dumps({"ensaio": "OK", "raiz_efemera_sha256": raiz_sha, "resumo": resumo}, sort_keys=True))
    print(json.dumps({"tamanhos_bytes": medidas}, sort_keys=True))
    if excedidos or medidas["folga_bytes"] < 0:
        raise OpsError(f"ensaio: acima do limite: {excedidos or 'admissao com o CANCEL'}")
    print("ensaio: OK (raiz efemera; nada foi para o S3 nem para o Secrets Manager)")
    return 0


def _s3() -> Any:
    import boto3  # type: ignore[import-untyped]

    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "sa-east-1"))


def _existe(cliente: Any, bucket: str, chave: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        cliente.head_object(Bucket=bucket, Key=chave)
    except ClientError as erro:
        if erro.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    return True


def _gravar_s3(cliente: Any, bucket: str, chave: str, corpo: bytes) -> None:
    """Objeto NOVO, SSE-KMS com a CMK de estado; nunca sobrescreve."""
    if _existe(cliente, bucket, chave):
        raise OpsError(f"ja existe s3://{bucket}/{chave}: recusado (use outro id)")
    cliente.put_object(
        Bucket=bucket,
        Key=chave,
        Body=corpo,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=env("MATERIAL_KMS_KEY_ARN"),
    )


def modo_raiz_gerar() -> int:
    from tools.staff_materials import approver
    from tools.staff_materials.secure_io import new_private_directory

    bucket, raiz_id = env("MATERIAL_BUCKET"), identificador("MATERIAL_RAIZ_ID")
    cliente = _s3()
    chaves = [f"raiz/{raiz_id}/installation-root-key.pem", f"raiz/{raiz_id}/installation-root.der"]
    if any(_existe(cliente, bucket, c) for c in chaves):
        raise OpsError("raiz/<id> ja existe: recusado (a raiz nunca e sobrescrita)")
    base = new_private_directory(_tmp() / "raiz")
    sha = approver.root_keygen(base / "r", passphrase=None, plaintext=True)
    privada = (base / "r" / "installation-root-key.pem").read_bytes()
    publica = (base / "r" / "installation-root.der").read_bytes()
    publico = f"publico/{raiz_id}/installation-root.der"
    if _existe(cliente, bucket, publico):
        raise OpsError("publico/<id> ja existe: recusado")
    _gravar_s3(cliente, bucket, chaves[0], privada)
    _gravar_s3(cliente, bucket, chaves[1], publica)
    # Copia da PUBLICA fora de `raiz/` (legivel pelo caminho sancionado): o dono confere o hash por conta
    # propria (`openssl dgst -sha256 installation-root.der`), como o aprovador da D-F.
    _gravar_s3(cliente, bucket, publico, publica)
    print(json.dumps({"raiz_id": raiz_id, "root_key_sha256": sha, "publica_bytes": len(publica)}))
    print("raiz-gerar: OK. A chave privada ficou so no S3 de estado; aprove o root_key_sha256 acima.")
    return 0


def _baixar(cliente: Any, bucket: str, chave: str) -> bytes:
    return bytes(cliente.get_object(Bucket=bucket, Key=chave)["Body"].read())


def modo_gerar() -> int:
    from tools.staff_materials.secure_io import PRIVATE, new_private_directory, subdirectory, write_new

    bucket, raiz_id = env("MATERIAL_BUCKET"), identificador("MATERIAL_RAIZ_ID")
    saida_id, sufixo = identificador("MATERIAL_SAIDA_ID"), env("MATERIAL_SUFIXO")
    aprovado = env("MATERIAL_RAIZ_SHA256")
    if not _SUFIXO.fullmatch(sufixo) or not _HEX64.fullmatch(aprovado):
        raise OpsError("MATERIAL_SUFIXO (AAAAMMDD) ou MATERIAL_RAIZ_SHA256 (hex de 64) invalido")
    cliente = _s3()
    if _existe(cliente, bucket, f"saida/{saida_id}/public/resumo.json"):
        raise OpsError("saida/<id> ja existe: recusado (use outro MATERIAL_SAIDA_ID)")
    base = _tmp() / "gerar"
    restaurado = _restaurar_efemero(gravar=True, base=base)
    imprimir_relatorio(restaurado)
    aprov = new_private_directory(base / "aprov")
    pasta_raiz = subdirectory(aprov, "root")
    for nome in ("installation-root-key.pem", "installation-root.der"):
        write_new(pasta_raiz / nome, _baixar(cliente, bucket, f"raiz/{raiz_id}/{nome}"), PRIVATE)

    def insumo(variavel: str, nome: str) -> Path | None:
        chave = os.environ.get(variavel)
        if not chave:
            return None
        destino = base / "insumos"
        if not destino.exists():
            new_private_directory(destino)
        write_new(destino / nome, _baixar(cliente, bucket, f"insumos/{chave}"), PRIVATE)
        return destino / nome

    saida = base / "saida"
    resumo = rodar_gerar(
        argumentos_gerar(
            base,
            sufixo=sufixo,
            saida=saida,
            raiz_sha256=aprovado,
            catalogo=insumo("MATERIAL_CATALOGO", "task-catalog.json"),
            entradas=insumo("MATERIAL_ENTRADAS_HUMANAS", "entradas-humanas.json"),
        )
    )
    conferir_linhas(saida)
    for pasta in ("ops-secrets", "public"):
        for arquivo in sorted((saida / pasta).iterdir()):
            _gravar_s3(cliente, bucket, f"saida/{saida_id}/{pasta}/{arquivo.name}", arquivo.read_bytes())
    print(json.dumps({"saida_id": saida_id, "resumo": resumo}, sort_keys=True))
    print(f"gerar: OK. Saida em s3://{bucket}/saida/{saida_id}/ (publique com a OAAR; runbook passo 5)")
    return 0


def main() -> int:
    try:
        modo = env("MATERIAL_MODO")
        if modo not in MODOS:
            raise OpsError(f"MATERIAL_MODO: esperado um de {MODOS}")
        return {
            "verificar": modo_verificar,
            "ensaio": modo_ensaio,
            "raiz-gerar": modo_raiz_gerar,
            "gerar": modo_gerar,
        }[modo]()
    except OpsError as erro:
        print(f"RECUSADO: {erro}", file=sys.stderr)
        return 1
    except Exception as erro:  # mensagem publica: nunca o valor de segredo nem a pilha completa
        print(f"FALHA {type(erro).__name__}: {str(erro)[:200]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
