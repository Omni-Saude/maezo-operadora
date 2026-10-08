#!/usr/bin/env python3
"""Renova o material staff/human do portal nativo em DEV (N2: 14 dias), sem mudar o desenho.

Runbook: `docs/runbooks/renovar-material-staff-dev.md`. Plano: `docs/plans/portal-autoridade-nativa-dev.md`
(Ondas 4-10, emenda N1 de 25/09 e D-L). A delegacao N1 vale SO em dev: o agente usa a raiz
`installation-root` do volume e assina designacao, admissoes e dono da atribuicao DENTRO do container.
Producao exige aprovador humano.

Dois comandos:

``gerar``     (container runner, ``--network none``, root, repo :ro, volume do material montado)
              Le o ESTADO da renovacao anterior (``--anterior``, layout de ``estado/``), gera a janela
              nova [agora-2min, +14d] e grava num diretorio NOVO: chaves humanas novas (CA nova),
              politica de validade, designacao N+1 + prova, segredo nativo, trust de atribuicao + dono,
              pacote staff do portal (conferido pelo loader do portal), admissao Q2 N+1, admissao humana
              espelho, pacote humano (conferido pelo loader do job), segredos do job, das linhas e da
              ativacao, e o ``estado/`` que a PROXIMA renovacao le. Imprime so digests e ids publicos.
``publicar``  (container runner com rede e as credenciais da OrganizationAccountAccessRole por
              ``--env-file``) grava a versao nova de cada segredo com boto3, byte a byte
              (``put_secret_value``; staff/human com ``ClientRequestToken`` = ``material_version_id``),
              rele pela VersionId e confere. Nunca imprime valor.

Nenhum arquivo do repositorio e escrito; a ferramenta de material recusa saida dentro do repo.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Os segredos de DEV (conta 203312548462). Nomes com sufixo real: o ARN completo e o que o ECS pina.
ARN = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/"
SEGREDOS = {
    "native-materials.json": ARN + "engine/native-materials-XoACan",
    "staff-materials.json": ARN + "portal/amh/staff-materials-R8JeAL",
    "portal-human.json": ARN + "portal/amh/human-materials-FAErGt",
    "job.json": ARN + "staff-job/materials-glnOol",
    "rows.json": ARN + "staff-install/rows-R8q4LK",
    "assignment-activate.json": ARN + "staff-install/assignment-EJgSnC",
    "syn-fixture.json": ARN + "staff-install/syn-fixture-wt3sOm",
}
#: Onde a raiz instalada esta pinada (versionado). A trava do `gerar` compara a raiz do volume com ela.
PINS_PORTAL = Path("deploy/aws-ecs/envs/dev-sa-east-1/portal.auto.tfvars")
JANELA = timedelta(days=14)  # N2 (dono, 23/09/2026): designacao, admissao e chaves


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def jcs_ou_dumps(antigo: bytes, valor: dict[str, Any]) -> bytes:
    """Reescreve no MESMO formato do arquivo antigo (JCS ou json.dumps com os separadores dele)."""
    from maezo.portal.engine.profile import canonicalize

    old = json.loads(antigo)
    try:
        if canonicalize(old) == antigo:
            return canonicalize(valor)
    except Exception:  # JCS do perfil recusa numeros: cai para json.dumps
        pass
    for seps in ((", ", ": "), (",", ":")):
        for ordenado in (False, True):
            if json.dumps(old, separators=seps, sort_keys=ordenado).encode() == antigo:
                return json.dumps(valor, separators=seps, sort_keys=ordenado).encode()
    raise SystemExit("formato do arquivo antigo nao reconhecido")


def entrada_nativa(nativo: dict[str, str], *, inicio: str, fim: str, politica: str) -> bytes:
    """`staff-materials-native-secret.v1` derivado dos arquivos PUBLICOS do segredo nativo anterior.

    Provado em 08/10/2026: com a janela e as chaves humanas antigas, reproduz byte a byte o
    portal-read-trust.json, o trust.json e o provider do segredo vivo (v5).
    """
    from maezo.portal.engine.profile import canonicalize

    def doc(nome: str) -> dict[str, Any]:
        return json.loads(base64.b64decode(nativo[nome]))

    leitura, humano = doc("engine-run/portal-read-trust.json"), doc("engine-run/trust.json")
    provedor, comp = (
        doc("engine-run/portal-read-provider.json"),
        doc("engine-run/staff/staff-composition.json"),
    )
    continuidade = doc("engine-run/continuity-keys.json")["keys"]
    publicacao = next(k for k in leitura["public_keys"] if k["purpose"] == "portal-read-publication")
    autoridade = next(k for k in humano["keys"] if k["purpose"] == "human-authority")
    auth = comp["auth"]
    valor = dict(
        schema="staff-materials-native-secret.v1",
        scope=leitura["scope"],
        engine_name=leitura["engine_name"],
        database_incarnation=leitura["database_incarnation"],
        not_before=inicio,
        not_after=fim,
        read_trust=dict(
            audience=leitura["audience"],
            read_deployment_ref=leitura["read_deployment_ref"],
            read_deployment_digest=leitura["read_deployment_digest"],
            validity_policy_ref=leitura["validity_policy_ref"],
            validity_policy_digest=politica,
            max_envelope_seconds=leitura["max_envelope_seconds"],
            catalog_ref=publicacao["catalog_ref"],
            publication_key_id=publicacao["key_id"],
        ),
        human_trust=dict(
            audience=humano["audience"],
            max_lifetime_seconds=humano["max_lifetime_seconds"],
            authority_key_id=autoridade["id"],
        ),
        provider=dict(
            admission_ref=provedor["admission_ref"],
            minimum_admission_revision=provedor["minimum_admission_revision"],
            native_schema=provedor["native_schema"],
            admission_table=dict(
                oid=provedor["admission_table_oid"], owner=provedor["admission_table_owner"]
            ),
            continuity_keys_file=provedor["continuity_keys_file"],
            continuity_generation=continuidade[0]["generation"],
            membership_source=provedor["membership_source"],
        ),
        staff=dict(
            auth_scope=comp["auth_scope"],
            native_role=comp["native_role"],
            native_schema=comp["native_schema"],
            engine_schema=comp["engine_schema"],
            relation_pins=comp["relation_pins"],
            maximum_seconds=comp["maximum_seconds"],
            catalog_ref=comp["catalog_anchor"]["catalog_ref"],
            catalog_publisher_ref=comp["catalog_anchor"]["publisher_ref"],
            auth={
                k: auth[k]
                for k in ("audience", "max_lifetime_seconds", "timeout_seconds", "alias", "key_id", "issuer")
            },
        ),
    )
    return canonicalize(valor)


def trava_dev(designacao: dict[str, Any], raiz_der: bytes, pins_portal: str) -> None:
    """Fail-closed: a delegacao N1 (o agente assina com a raiz) so vale em DEV, tenant amh, com a raiz
    que o portal de dev pina (`root_key_sha256` de `portal.auto.tfvars`). Qualquer outra coisa aborta
    ANTES de abrir a chave privada."""
    import re

    escopo = designacao.get("scope") or {}
    if escopo.get("environment") != "dev" or escopo.get("tenant") != "amh":
        raise SystemExit("recusado: a renovacao delegada (N1) so roda em dev/amh")
    pins = re.findall(r'^\s*root_key_sha256\s*=\s*"([0-9a-f]{64})"', pins_portal, re.MULTILINE)
    if len(pins) != 1:
        raise SystemExit("recusado: portal.auto.tfvars sem exatamente um root_key_sha256")
    if sha(raiz_der) != pins[0]:
        raise SystemExit("recusado: a raiz do volume nao e a que o portal de dev pina")


def renovar_syn(syn: dict[str, Any], nativo: dict[str, str]) -> dict[str, Any]:
    """Fixture SYN: troca SO o `result_certificate` pelo certificado AUTH do segredo nativo novo."""
    from cryptography.hazmat.primitives.serialization import Encoding, pkcs12

    p12 = base64.b64decode(nativo["engine-run/staff/auth-signing.p12"])
    senha = base64.b64decode(nativo["engine-run/staff/auth-signing.password"])
    _, cert, _ = pkcs12.load_key_and_certificates(p12, senha)
    if cert is None or "result_certificate" not in syn["files"]:
        raise SystemExit("segredo SYN ou PKCS12 do AUTH sem certificado")
    files = dict(syn["files"], result_certificate=b64(cert.public_bytes(Encoding.PEM)))
    return dict(syn, files=files)


def gerar(args: argparse.Namespace) -> int:
    from tools.staff_materials import approver
    from tools.staff_materials import assignment_trust as at
    from tools.staff_materials import human_bundle as hb
    from tools.staff_materials.assemble import assemble, bundle_bytes, read_directories
    from tools.staff_materials.assemble import load_input as load_assemble
    from tools.staff_materials.generate import next_designation
    from tools.staff_materials.native_secret import build
    from tools.staff_materials.native_secret import load_input as load_native
    from tools.staff_materials.secure_io import (
        PRIVATE,
        PUBLIC,
        new_private_directory,
        subdirectory,
        write_new,
    )
    from tools.staff_materials.spec import load_spec
    from tools.staff_materials.verify import load_pins, verify_bundle

    from maezo.gateway.external_cases.models import digest, instant
    from maezo.portal.engine.profile import canonicalize, strict_loads

    ant: Path = args.anterior
    materiais: Path = args.materials

    def ler(nome: str) -> Any:
        return strict_loads((ant / nome).read_bytes())

    def segredo(nome: str) -> dict[str, Any]:
        return json.loads((ant / nome).read_bytes())

    agora = datetime.now(UTC).replace(microsecond=0)
    inicio_dt = agora - timedelta(minutes=2)
    fim_dt = inicio_dt + JANELA
    inicio, fim = instant(inicio_dt), instant(fim_dt)
    resumo: dict[str, Any] = {"janela": [inicio, fim]}

    raiz_der = (args.approver / "root/installation-root.der").read_bytes()
    trava_dev(ler("designation.json"), raiz_der, (args.repo / PINS_PORTAL).read_text(encoding="utf-8"))
    resumo["root_key_sha256"] = sha(raiz_der)
    raiz = approver.load_root(
        args.approver / "root/installation-root-key.pem",
        approver._passphrase(args.approver / "root-passphrase.txt"),
    )

    out = new_private_directory(args.out)
    pub, sec = subdirectory(out, "public"), subdirectory(out, "ops-secrets")
    estado = subdirectory(out, "estado")

    def publico(nome: str, raw: bytes, *, estado_tambem: bool = True) -> None:
        write_new(pub / nome, raw, PUBLIC)
        if estado_tambem:
            write_new(estado / nome, raw, PUBLIC)

    def privado(nome: str, valor: dict[str, Any]) -> None:
        raw = json.dumps(valor, sort_keys=True, separators=(",", ":")).encode()
        write_new(sec / nome, raw, PRIVATE)
        write_new(estado / nome, raw, PRIVATE)

    # 1. chaves humanas novas (a CA de clientes delas vence com a janela; a chave da CA nao e gravada)
    spec_ant = ler("human-bundle-spec.json")
    spec_h = dict(spec_ant, issued_at=inicio, valid_until=fim)
    for campo, nome in (("read_key", "read"), ("assignment_key", "assignment"), ("command_key", "command")):
        spec_h[campo] = dict(spec_ant[campo], key_id=f"amh-dev-{nome}-{args.sufixo}")
    spec_h["revocation"] = dict(
        spec_ant["revocation"], revision=str(int(spec_ant["revocation"]["revision"]) + 1)
    )
    spec_h_raw = canonicalize(spec_h)
    publico("human-bundle-spec.json", spec_h_raw)
    hspec = hb.load_spec(spec_h_raw)
    hb.write_keys(out / "human-keys", hspec, hb.new_keys(hspec))
    resumo_h = strict_loads((out / "human-keys/public/human-keys-summary.json").read_bytes())
    resumo["human_client_ca_sha256"] = resumo_h["client_ca_sha256"]

    # 2. politica de validade (D-L c)
    politica_raw = canonicalize(
        dict(
            ler("validity-policy.json"),
            designation_valid_until=fim,
            keys_not_before=inicio,
            keys_not_after=fim,
        )
    )
    publico("validity-policy.json", politica_raw)
    politica = sha(politica_raw)
    resumo["validity_policy_digest"] = politica

    # 3. designacao N+1 (mesmas chaves) + prova de instalacao
    desig_raw = canonicalize(next_designation(ler("designation.json"), not_before=inicio, valid_until=fim))
    _, desig, _ = approver.review_designation(desig_raw)
    prova = approver.sign_designation(
        desig_raw, raiz, confirm_digest=desig, expires_at=fim_dt - timedelta(seconds=60)
    )
    publico("designation.json", desig_raw)
    publico("installation-proof.json", prova)
    resumo["designation_sha256"] = desig
    resumo["designation_revision"] = json.loads(desig_raw)["designation_revision"]

    # 4. segredo nativo (mesma entrada, janela nova)
    nativo_ant = segredo("native-materials.json")
    entrada = entrada_nativa(nativo_ant, inicio=inicio, fim=fim, politica=politica)
    publico("native-secret-input.json", entrada, estado_tambem=False)
    arquivos, nsum = build(
        materiais,
        raiz_der,
        load_native(entrada, resumo_h),
        human_client_ca=resumo_h["client_ca_pem"].encode("ascii"),
        designation=desig_raw,
    )
    publico("native-secret-summary.json", canonicalize(nsum), estado_tambem=False)
    config = nsum["staff_native_configuration_digest"]
    resumo["staff_native_configuration_digest"] = config
    resumo["trust_configuration_digest"] = nsum["trust_configuration_digest"]

    # 5. plano de atribuicao: dono assinado + trust (chave da fonte reaproveitada)
    dono_raw = canonicalize(dict(ler("assignment-owner.json"), not_before=inicio, valid_until=fim))
    _, dono, _ = approver.review_assignment_owner(dono_raw)
    dono_prova = approver.sign_assignment_owner(dono_raw, raiz, confirm_digest=dono)
    publico("assignment-owner.json", dono_raw)
    publico("assignment-owner-proof.json", dono_prova, estado_tambem=False)
    aspec = dict(ler("assignment-spec.json"), not_before=inicio, not_after=fim)
    aspec["validity_policy"] = dict(aspec["validity_policy"], digest=politica)
    publico("assignment-spec.json", canonicalize(aspec))
    atrib_raw, atrib, recibo = at.build_trust(
        aspec,
        strict_loads(arquivos["engine-run/trust.json"]),
        resumo_h,
        strict_loads(args.assignment_source.read_bytes()),
        owner=dono_prova,
        root_spki=raiz_der,
        now=datetime.now(UTC),
    )
    publico("assignment-trust.json", atrib_raw, estado_tambem=False)
    resumo["assignment_configuration_digest"] = atrib
    resumo["owner_receipt_digest"] = recibo

    # 6. segredo nativo completo (mesmo conjunto de arquivos do anterior)
    nativo = {n: b64(raw) for n, raw in arquivos.items()}
    nativo["engine-run/assignment-trust.json"] = b64(atrib_raw)
    for nome, valor in nativo_ant.items():
        if nome == "engine-run/observer-dsn.txt" or nome.startswith("staff-issuer/"):
            nativo.setdefault(nome, valor)
    nativo["staff-issuer/designation.json"] = b64(desig_raw)
    nativo["staff-issuer/installation-proof.json"] = b64(prova)
    comp_ant = base64.b64decode(nativo_ant["staff-issuer/composition.json"])
    comp = json.loads(comp_ant)
    comp["designation_digest"] = desig
    comp["native"]["configuration_digest"] = config
    nativo["staff-issuer/composition.json"] = b64(jcs_ou_dumps(comp_ant, comp))
    if set(nativo) != set(nativo_ant):
        raise SystemExit("conjunto de arquivos do segredo nativo mudou")
    privado("native-materials.json", nativo)

    # 7. pacote staff do portal (assemble com a designacao nova) + verify do loader do portal
    aprov = new_private_directory(out / "approver-staff")
    write_new(aprov / "installation-root.der", raiz_der, PUBLIC)
    write_new(aprov / "installation-proof.json", prova, PUBLIC)
    em = instant(datetime.now(UTC))
    versao_staff = "amh-dev-staff-" + os.urandom(13).hex()
    ent_ant = ler("assemble-input.json")
    revog = dict(ent_ant["revocation"], observed_at=em, valid_until=fim)
    revog["revision"] = str(int(revog["revision"]) + 1)
    ent_raw = canonicalize(
        dict(
            ent_ant,
            material_version_id=versao_staff,
            issued_at=em,
            valid_until=fim,
            native_configuration_digest=config,
            revocation=revog,
        )
    )
    publico("assemble-input.json", ent_raw)
    portal, aprov_arquivos, gsum = read_directories(materiais, aprov)
    manifesto, staff = assemble(
        portal,
        aprov_arquivos,
        gsum,
        load_spec(args.generate_spec.read_bytes()),
        load_assemble(ent_raw),
        designation=desig_raw,
    )
    pacote = bundle_bytes(manifesto, staff)
    write_new(sec / "staff-materials.json", pacote, PRIVATE)
    publico("staff-public-manifest.json", canonicalize(manifesto), estado_tambem=False)
    pins = dict(json.loads((ant / "approver-pins.json").read_bytes()))
    pins.update(
        staff_material_version_id=versao_staff,
        staff_public_manifest_sha256=digest(manifesto),
        staff_designation_sha256=desig,
        staff_native_configuration_sha256=config,
    )
    pins_raw = json.dumps(pins).encode()
    publico("approver-pins.json", pins_raw)
    verify_bundle(pacote, load_pins(pins_raw))
    resumo["staff_material_version_id"] = versao_staff
    resumo["staff_public_manifest_sha256"] = digest(manifesto)

    # 8. admissao Q2 N+1 (mesmo catalogo, trust e janela novos)
    job_ant = segredo("job.json")
    catalogo = base64.b64decode(job_ant["job/task-catalog.json"])
    reg = json.loads(base64.b64decode(job_ant["job/task-admission.json"]))
    reg.update(
        admission_revision=str(int(reg["admission_revision"]) + 1),
        trust_configuration_digest=nsum["trust_configuration_digest"],
        not_before=inicio,
        valid_until=fim,
    )
    reg["continuity_keys"] = [dict(k, not_before=inicio, not_after=fim) for k in reg["continuity_keys"]]
    if reg["continuity_keys"][0]["commitment"] != nsum["continuity"]["commitment"]:
        raise SystemExit("commitment de continuidade mudou: a admissao nao descreve o segredo nativo")
    reg_raw = canonicalize(reg)
    _, adm, _ = approver.review_admission(reg_raw, catalogo)
    assinatura = approver.sign_admission(reg_raw, raiz, confirm_digest=adm, catalog=catalogo)
    publico("portal-read-admission.json", reg_raw, estado_tambem=False)
    resumo["admission_sha256"] = adm
    resumo["admission_revision"] = reg["admission_revision"]

    # 9. admissao humana espelho (capability = admissao Q2 nova)
    h_ant = strict_loads(base64.b64decode(job_ant["human/read-admission.json"]))["record"]
    hreg_raw = canonicalize(
        dict(
            h_ant,
            capability_digest=adm,
            provider_revision=reg["admission_revision"],
            runtime_admission_generation=reg["admission_revision"],
            observed_at=inicio,
            valid_until=fim,
        )
    )
    _, hadm, _ = approver.review_human_admission(hreg_raw)
    hdoc = approver.sign_human_admission(hreg_raw, raiz, confirm_digest=hadm)

    # 10. pacote humano (mesmas DSNs) + verify do loader do job
    hfiles, hman = hb.package(
        hspec,
        hb.read_keys(out / "human-keys", hspec),
        root_spki=raiz_der,
        admission=hdoc,
        server_ca_pem=(materiais / "portal/native-ca.pem").read_bytes(),
        server_spki=gsum["native_server_spki_sha256"],
        source_dsn=base64.b64decode(job_ant["human/source-dsn.txt"]),
        outbox_dsn=base64.b64decode(job_ant["human/outbox-dsn.txt"]),
    )
    hb.write_package(out / "human-bundle", hfiles, hman)
    humano = {f"human/{n}": b64(raw) for n, raw in {**hfiles, "manifest.json": hman.canonical()}.items()}
    privado("portal-human.json", humano)
    resumo["human_material_version_id"] = hman.material_version_id
    resumo["human_public_manifest_sha256"] = sha(hman.canonical())

    # 11. segredo do job (config com not_after novo, admissao nova, pacote humano novo)
    cfg_ant = base64.b64decode(job_ant["job/config.json"])
    cfg = json.loads(cfg_ant)
    cfg["publication"]["not_after"] = fim
    cfg["authority"]["not_after"] = fim
    job = {k: v for k, v in job_ant.items() if k.startswith("job/")}
    job["job/config.json"] = b64(jcs_ou_dumps(cfg_ant, cfg))
    job["job/task-admission.json"] = b64(reg_raw)
    job.update(humano)
    if set(job) != set(job_ant):
        raise SystemExit("conjunto de arquivos do segredo do job mudou")
    privado("job.json", job)

    # 12. segredo de linhas (designacao N+1, admissao N+1, instalacao da atribuicao)
    linhas = segredo("rows.json")
    linhas.update(
        designation_revision=int(resumo["designation_revision"]),
        designation_b64=b64(desig_raw),
        designation_sha256=desig,
        installation_proof_b64=b64(prova),
        admission_revision=int(reg["admission_revision"]),
        admission_record_b64=b64(reg_raw),
        admission_signature_b64=assinatura.decode("ascii"),
        admission_sha256=sha(reg_raw),
    )
    linhas["assignment"]["installation"] = dict(
        linhas["assignment"]["installation"],
        configuration_digest=atrib,
        valid_until=str(int(fim_dt.timestamp())),
    )
    privado("rows.json", linhas)

    # 13. ativacao (so e usada se a fonte de atribuicao precisar de geracao nova)
    ativ = segredo("assignment-activate.json")
    ref = ativ["owner_receipt"]["artifact_ref"].rsplit(":", 1)
    ativ["owner_receipt"] = dict(artifact_ref=f"{ref[0]}:{int(ref[1]) + 1}", digest=recibo)
    privado("assignment-activate.json", ativ)

    # 14. fixture SYN (opcional): certificado do AUTH novo
    if (ant / "syn-fixture.json").is_file():
        privado("syn-fixture.json", renovar_syn(segredo("syn-fixture.json"), nativo))

    publico("resumo.json", canonicalize(resumo))
    print(json.dumps(resumo, indent=1, sort_keys=True))
    return 0


def publicar(args: argparse.Namespace) -> int:
    import boto3

    resumo = json.loads((args.out / "public/resumo.json").read_bytes())
    tokens = {
        "staff-materials.json": resumo["staff_material_version_id"],
        "portal-human.json": resumo["human_material_version_id"],
    }
    sm = boto3.client("secretsmanager", region_name="sa-east-1")
    for nome in args.nomes:
        corpo = (args.out / "ops-secrets" / nome).read_bytes()
        texto = corpo.decode("utf-8")
        resp = sm.put_secret_value(
            SecretId=SEGREDOS[nome],
            SecretString=texto,
            ClientRequestToken=tokens.get(nome) or str(uuid.uuid4()),
        )
        volta = sm.get_secret_value(SecretId=SEGREDOS[nome], VersionId=resp["VersionId"])[
            "SecretString"
        ].encode()
        print(
            f"{nome}: VersionId={resp['VersionId']} len={len(corpo)} sha={sha(corpo)[:16]}",
            f"identico={volta == corpo}",
        )
        if volta != corpo:
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cmds = p.add_subparsers(dest="cmd", required=True)
    g = cmds.add_parser("gerar")
    g.add_argument("--anterior", type=Path, required=True, help="estado/ da renovacao anterior")
    g.add_argument("--materials", type=Path, default=Path("/m/materials"), help="saida do generate (Onda 2)")
    g.add_argument("--approver", type=Path, default=Path("/m/approver"), help="raiz installation-root (dev)")
    g.add_argument("--generate-spec", type=Path, required=True, help="spec do generate (conexoes)")
    g.add_argument("--assignment-source", type=Path, required=True, help="assignment-source.json publico")
    g.add_argument("--sufixo", required=True, help="AAAAMMDD dos key_id humanos novos")
    g.add_argument("--out", type=Path, required=True, help="diretorio NOVO no volume")
    g.add_argument("--repo", type=Path, default=Path("/repo"), help="checkout (le o pin da raiz)")
    s = cmds.add_parser("publicar")
    s.add_argument("--out", type=Path, required=True, help="monte SO ops-secrets/ e public/ da saida")
    s.add_argument("nomes", nargs="+", choices=sorted(SEGREDOS))
    args = p.parse_args(argv)
    return gerar(args) if args.cmd == "gerar" else publicar(args)


if __name__ == "__main__":
    sys.exit(main())
