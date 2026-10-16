"""`restaurar` - remonta, dos SEGREDOS VIVOS na AWS, o que o `gerar` de renovacao lia de um volume local.

Contexto (DL-0113, `docs/runbooks/material-staff-na-aws.md`): ate 15/10/2026 a renovacao do material
staff/human do portal dependia do volume Docker `maezo-onda2b-materials` do notebook do dono
(`/m/materials`, `estado/` da renovacao anterior, `spec.r2.json`, `assignment-source.json`). Quase tudo
isso ja esta, byte a byte, nos segredos publicados (`engine/native-materials`, `staff-job/materials`,
`portal/amh/staff-materials`, `staff-install/rows`, `staff-install/assignment`, `staff-install/syn-fixture`).
Esta funcao DERIVA o resto e CONFERE cada derivacao por hash contra o que o motor e o portal pinam. So a
raiz de assinatura (chave privada) NAO esta nos segredos: ela nasce na AWS (`material raiz-gerar`).

Entrada: os 6 segredos ja lidos (JSON). Saida: tres arvores EM MEMORIA (`{caminho: bytes}`), para o
chamador gravar em diretorio 0700 (`secure_io`) ou so medir:

``materiais``  o que a saida do `generate` da Onda 2 continha e o `gerar` le (``portal/``, ``engine/``,
               ``job/``, ``public/summary.json``);
``estado``     o `estado/` da renovacao anterior (arquivos publicos reconstruidos + os segredos);
``fontes``     ``assignment-source.json`` (publico) e ``conexoes-staff.json`` (os dois `ConnectionSpec`
               que o `assemble` le do spec do generate).

O que NAO e recuperavel e e REESCRITO (registrado no relatorio, nao escondido):
``validity-policy.json`` (o corpo antigo so existia no volume; o engine so compara o DIGEST como pin
opaco, D-L (c), e o `gerar` ja troca o digest a cada renovacao).

Nada aqui imprime valor: o relatorio so tem caminho, tamanho e SHA-256 truncado (16 hex).
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, pkcs12
from cryptography.x509.oid import NameOID

from maezo.gateway.external_cases.models import digest, instant
from maezo.portal.engine.profile import canonicalize

from .assignment_trust import SOURCE_SCHEMA, SPEC_SCHEMA
from .generate import (
    ISSUER_WITNESS_ENTRY,
    ISSUER_WITNESS_KEY,
    JOB_CLIENT,
    JOB_KEY_FILES,
    continuity_commitment,
)
from .secure_io import MaterialError

APPROVER_FILES = ("installation-root.der", "installation-proof.json")
HUMAN_PURPOSE_KEYS = {
    "read_key": "portal-task-read",
    "assignment_key": "human-assignment-read",
    "command_key": "human-command",
}
#: Sufixo do CN dos certificados de cliente humanos (`human_bundle.new_keys`): `<label> read client`.
_CN_SUFFIX = " read client"
_POLICY_SCHEMA = "maezo-staff-validity-policy.v1"


@dataclass(frozen=True)
class Segredos:
    """Os segredos vivos, ja em JSON. `syn` e opcional (a fixture SYN pode nao existir)."""

    native: dict[str, Any]
    staff: dict[str, Any]
    job: dict[str, Any]
    rows: dict[str, Any]
    assignment: dict[str, Any]
    syn: dict[str, Any] | None = None


@dataclass
class Restaurado:
    materiais: dict[str, bytes] = field(default_factory=dict)
    estado: dict[str, bytes] = field(default_factory=dict)
    fontes: dict[str, bytes] = field(default_factory=dict)
    #: `[{"arvore", "caminho", "bytes", "sha256_16"}]`: sem nenhum valor.
    relatorio: list[dict[str, Any]] = field(default_factory=list)
    #: Conferencias por hash, `{nome: True}`. Qualquer divergencia ja levantou `MaterialError`.
    conferencias: dict[str, bool] = field(default_factory=dict)
    #: Fatos publicos (hashes completos) que o operador compara com os pins do Terraform.
    fatos: dict[str, str] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _spki_der(public_key: Any) -> bytes:
    return bytes(public_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo))


def _b64d(valor: Any, onde: str) -> bytes:
    if not isinstance(valor, str) or not valor:
        raise MaterialError(f"{onde}: base64 ausente")
    try:
        return base64.b64decode(valor.encode("ascii"), validate=True)
    except ValueError:
        raise MaterialError(f"{onde}: base64 invalido") from None


def _arquivo(mapa: dict[str, Any], nome: str, onde: str) -> bytes:
    if nome not in mapa:
        raise MaterialError(f"{onde}: arquivo {nome} ausente")
    return _b64d(mapa[nome], f"{onde}/{nome}")


def _json(raw: bytes, onde: str) -> dict[str, Any]:
    try:
        valor = json.loads(raw)
    except ValueError:
        raise MaterialError(f"{onde}: JSON invalido") from None
    if type(valor) is not dict:
        raise MaterialError(f"{onde}: esperado objeto")
    return valor


def _exige(condicao: bool, nome: str, resultado: Restaurado) -> None:
    if not condicao:
        raise MaterialError(f"conferencia por hash falhou: {nome}")
    resultado.conferencias[nome] = True


def _pem_ed25519(raw: bytes, onde: str) -> Ed25519PrivateKey:
    try:
        chave = serialization.load_pem_private_key(raw, password=None)
    except (TypeError, ValueError):
        raise MaterialError(f"{onde}: chave privada ilegivel") from None
    if not isinstance(chave, Ed25519PrivateKey):
        raise MaterialError(f"{onde}: esperado Ed25519")
    return chave


def _cert(raw: bytes, onde: str) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(raw)
    except ValueError:
        raise MaterialError(f"{onde}: certificado ilegivel") from None


def _cn(certificado: x509.Certificate) -> str:
    atributos = certificado.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if len(atributos) != 1 or not isinstance(atributos[0].value, str):
        raise MaterialError("certificado sem CN unico")
    return atributos[0].value


def _ca_de_clientes_nativa(p12: bytes) -> bytes:
    """O `native-client-ca.pem` do generate, tirado do truststore `client-ca.p12` do segredo nativo."""
    try:
        carga = pkcs12.load_pkcs12(p12, None)
    except ValueError:
        raise MaterialError("client-ca.p12 ilegivel (truststore sem senha esperado)") from None
    achados = [c.certificate for c in carga.additional_certs if c.friendly_name == b"maezo-native-client-ca"]
    if len(achados) != 1:
        raise MaterialError("client-ca.p12 sem exatamente uma CA `maezo-native-client-ca`")
    return achados[0].public_bytes(Encoding.PEM)


def _registrar(r: Restaurado, arvore: str, caminho: str, raw: bytes) -> None:
    r.relatorio.append({"arvore": arvore, "caminho": caminho, "bytes": len(raw), "sha256_16": _sha(raw)[:16]})


def restaurar(seg: Segredos) -> Restaurado:
    """Deriva e confere. Levanta `MaterialError` (mensagem publica) na primeira divergencia."""
    from tools.staff_materials import human_bundle as hb
    from tools.staff_materials.assemble import load_input as load_assemble

    r = Restaurado()
    nat = seg.native
    manifesto = seg.staff.get("public_manifest")
    if not isinstance(manifesto, dict) or not isinstance(seg.staff.get("files"), dict):
        raise MaterialError("segredo staff-materials fora do formato portal-staff-secret-bundle.v1")
    arquivos_staff = {n: _b64d(v, f"staff/{n}") for n, v in seg.staff["files"].items()}

    # ---------------------------------------------------------------- materiais/ (o `generate`)
    designacao = arquivos_staff["designation.json"]
    _exige(
        _arquivo(nat, "staff-issuer/designation.json", "native") == designacao,
        "designacao do segredo nativo == a do pacote staff",
        r,
    )
    desig = _json(designacao, "designacao")
    desig_digest = digest(desig)
    _exige(desig_digest == manifesto["designation_digest"], "designacao == manifesto.designation_digest", r)
    _exige(
        desig_digest == seg.rows["designation_sha256"] == hashlib.sha256(designacao).hexdigest(),
        "designacao == segredo de linhas (sha256 dos bytes e digest JCS)",
        r,
    )
    entradas = {e["entry_ref"]: e for e in desig["entries"]}

    portal = {n: b for n, b in arquivos_staff.items() if n not in APPROVER_FILES}
    for nome, raw in portal.items():
        r.materiais[f"portal/{nome}"] = raw

    certificado_servidor = _arquivo(nat, "engine-native/server.crt", "native")
    cert_srv = _cert(certificado_servidor, "server.crt")
    spki_servidor = _sha(_spki_der(cert_srv.public_key()))
    _exige(
        spki_servidor == manifesto["native_server_spki_sha256"],
        "SPKI do server.crt == manifesto.native_server_spki_sha256",
        r,
    )
    p12 = _arquivo(nat, "engine-native/client-ca.p12", "native")
    resultado_pk8 = _arquivo(nat, "engine-run/staff/native-result.pk8", "native")
    chave_resultado = serialization.load_der_private_key(resultado_pk8, password=None)
    if not isinstance(chave_resultado, Ed25519PrivateKey):
        raise MaterialError("native-result.pk8: esperado Ed25519")
    continuidade = _json(_arquivo(nat, "engine-run/continuity-keys.json", "native"), "continuity-keys")
    chave_cont = continuidade["keys"][0]
    segredo_cont = _b64d(chave_cont["secret_base64"], "continuity")
    r.materiais["engine/native-server-key.pem"] = _arquivo(nat, "engine-native/server.key", "native")
    r.materiais["engine/native-server-certificate.pem"] = certificado_servidor
    r.materiais["engine/native-client-ca.pem"] = _ca_de_clientes_nativa(p12)
    r.materiais["engine/client-ca.p12"] = p12
    r.materiais["engine/native-result-signing-key.pem"] = chave_resultado.private_bytes(
        Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    r.materiais["engine/portal-read-continuity-key.bin"] = segredo_cont

    job = seg.job
    cert_job = _arquivo(job, "job/client-certificate.pem", "job")
    r.materiais["job/job-client-key.pem"] = _arquivo(job, "job/client-key.pem", "job")
    r.materiais["job/job-client-certificate.pem"] = cert_job
    chaves_job = {
        proposito: _pem_ed25519(_arquivo(job, f"job/{arquivo}", "job"), f"job/{arquivo}")
        for proposito, arquivo in JOB_KEY_FILES.items()
    }
    for arquivo in JOB_KEY_FILES.values():
        r.materiais[f"job/{arquivo}"] = _arquivo(job, f"job/{arquivo}", "job")

    # Conferencias por hash contra o que o motor pina (trusts) e o que o portal pina (manifesto).
    confianca_leitura = _json(
        _arquivo(nat, "engine-run/portal-read-trust.json", "native"), "portal-read-trust"
    )
    confianca_humana = _json(_arquivo(nat, "engine-run/trust.json", "native"), "trust")
    publicacao = next(
        k for k in confianca_leitura["public_keys"] if k["purpose"] == "portal-read-publication"
    )
    autoridade = next(k for k in confianca_humana["keys"] if k["purpose"] == "human-authority")
    spki_publicacao = _spki_der(chaves_job["portal-read-publication"].public_key())
    spki_autoridade = _spki_der(chaves_job["human-authority"].public_key())
    _exige(
        base64.b64encode(spki_publicacao).decode() == publicacao["public_key_spki_base64"],
        "chave de publicacao do job == portal-read-trust",
        r,
    )
    _exige(
        base64.b64encode(spki_autoridade).decode() == autoridade["public_key_spki_base64"],
        "chave human-authority do job == trust humano",
        r,
    )
    spki_cliente_job = _sha(_spki_der(_cert(cert_job, "job/client-certificate").public_key()))
    _exige(
        spki_cliente_job == publicacao["peer_spki_sha256"] == autoridade["peer_spki_sha256"],
        "certificado de cliente do job == peers dos trusts",
        r,
    )
    composicao = _json(_arquivo(nat, "engine-run/staff/staff-composition.json", "native"), "composicao")
    _exige(
        composicao["configuration_digest"] == manifesto["native_configuration_digest"],
        "composicao nativa == manifesto.native_configuration_digest",
        r,
    )
    _exige(
        base64.b64encode(_spki_der(chave_resultado.public_key())).decode() == composicao["result_public_key"],
        "chave de resultado nativo == composicao",
        r,
    )
    comprometimento = continuity_commitment(segredo_cont)
    admissao = _json(_arquivo(job, "job/task-admission.json", "job"), "task-admission")
    _exige(
        comprometimento == admissao["continuity_keys"][0]["commitment"],
        "compromisso de continuidade == admissao Q2",
        r,
    )
    chave_leitura = _pem_ed25519(portal["read-signing-key.pem"], "portal/read-signing-key.pem")
    chave_witness = _pem_ed25519(portal["witness-signing-key.pem"], "portal/witness-signing-key.pem")
    f_leitura = _sha(_spki_der(chave_leitura.public_key()))
    f_witness = _sha(_spki_der(chave_witness.public_key()))
    _exige(
        f_leitura == manifesto["read_key_fingerprint"] and f_witness == manifesto["witness_key_fingerprint"],
        "chaves de leitura/witness do portal == manifesto",
        r,
    )
    raiz_staff = arquivos_staff["installation-root.der"]
    _exige(
        _sha(raiz_staff) == manifesto["root_key_fingerprint"]
        and _arquivo(job, "human/installation-root.der", "job") == raiz_staff
        and _arquivo(nat, "staff-issuer/installation-root.der", "native") == raiz_staff,
        "raiz vigente igual em manifesto, pacote humano e segredo nativo",
        r,
    )
    r.fatos["raiz_vigente_sha256"] = _sha(raiz_staff)

    fingerprints = {ref: e["key_fingerprint"] for ref, e in entradas.items() if e["entry_ref"] == e["role"]}
    fingerprints[ISSUER_WITNESS_KEY] = entradas[ISSUER_WITNESS_ENTRY]["key_fingerprint"]
    _exige(
        fingerprints["read_requester"] == f_leitura and fingerprints["identity_verifier"] == f_witness,
        "designacao == chaves de leitura/witness do portal",
        r,
    )
    cert_leitura = _cert(portal["read-client-certificate.pem"], "portal/read-client-certificate.pem")
    cert_importador = _cert(
        _arquivo(nat, "staff-issuer/importer-client-certificate.pem", "native"), "importer-client-certificate"
    )
    summary = dict(
        schema="staff-materials-summary.v1",
        scope=manifesto["scope"],
        native_hostname=str(manifesto["native_origin"]).removeprefix("https://"),
        designation_sha256=desig_digest,
        certificate_not_after=instant(cert_srv.not_valid_after_utc),
        key_fingerprints=dict(sorted(fingerprints.items())),
        client_certificate_spki_sha256={
            JOB_CLIENT: spki_cliente_job,
            "read_requester": _sha(_spki_der(cert_leitura.public_key())),
            "publication_importer": _sha(_spki_der(cert_importador.public_key())),
        },
        native_server_spki_sha256=spki_servidor,
        continuity=dict(key_ref=chave_cont["key_id"], commitment=comprometimento),
    )
    r.materiais["public/summary.json"] = canonicalize(summary)

    # ---------------------------------------------------------------- estado/ (renovacao anterior)
    r.estado["designation.json"] = designacao
    humano = _json(_arquivo(job, "human/manifest.json", "job"), "manifesto humano")
    chaves_h = {k["purpose"]: k for k in humano["keys"]}
    cert_humano = _cert(
        _arquivo(job, "human/read-client-certificate.pem", "job"), "human/read-client-certificate"
    )
    rotulo = _cn(cert_humano)
    if not rotulo.endswith(_CN_SUFFIX):
        raise MaterialError("CN do certificado de leitura humano fora do padrao `<label> read client`")
    versao_h = str(humano["material_version_id"])
    spec_humana: dict[str, Any] = dict(
        schema="staff-materials-human-bundle.v1",
        scope=humano["scope"],
        engine_name=humano["engine_name"],
        database_incarnation=humano["database_incarnation"],
        issuer=humano["issuer"],
        issued_at=humano["issued_at"],
        valid_until=humano["valid_until"],
        origin=humano["command_endpoint"],
        catalog_ref=humano["catalog_ref"],
        material_version_prefix=versao_h[:-26],
        certificate_label=rotulo.removesuffix(_CN_SUFFIX),
        max_envelope_seconds=chaves_h["portal-task-read"]["max_envelope_seconds"],
        timeout_seconds=humano["read_surface"]["timeout_seconds"],
        outbox_connection=humano["outbox_connection"],
        source_connection=humano["source_connection"],
        relay_lease_seconds=humano["relay_lease_seconds"],
        relay_retry_seconds=humano["relay_retry_seconds"],
        relay_poll_seconds=humano["relay_poll_seconds"],
        revocation=dict(
            source_ref=humano["revocation_snapshot"]["source_ref"],
            revision=humano["revocation_snapshot"]["revision"],
        ),
    )
    for campo, proposito in HUMAN_PURPOSE_KEYS.items():
        spec_humana[campo] = dict(
            key_id=chaves_h[proposito]["key_id"], audience=chaves_h[proposito]["audience"]
        )
    spec_humana_raw = canonicalize(spec_humana)
    hb.load_spec(spec_humana_raw)  # o mesmo modelo fechado do `human-bundle`
    r.estado["human-bundle-spec.json"] = spec_humana_raw

    politica = canonicalize(
        dict(
            schema=_POLICY_SCHEMA,
            policy_ref=confianca_leitura["validity_policy_ref"],
            designation_valid_until=manifesto["valid_until"],
            keys_not_before=manifesto["issued_at"],
            keys_not_after=manifesto["valid_until"],
        )
    )
    r.estado["validity-policy.json"] = politica
    r.avisos.append(
        "validity-policy.json REESCRITO: o corpo antigo so existia no volume local; o engine compara "
        "apenas o digest (pin opaco, D-L c) e o gerar troca esse digest a cada renovacao"
    )

    revogacao = manifesto["revocation_snapshot"]
    entrada_assemble = dict(
        schema="staff-materials-assemble.v1",
        material_version_id=manifesto["material_version_id"],
        issuer=manifesto["issuer"],
        issued_at=manifesto["issued_at"],
        valid_until=manifesto["valid_until"],
        native_configuration_digest=manifesto["native_configuration_digest"],
        native_maximum_seconds=manifesto["native_maximum_seconds"],
        native_schema=manifesto["native_schema"],
        engine_schema=manifesto["engine_schema"],
        session_lock_function_pin=manifesto["session_lock_connection"]["function_pin"],
        native_relation_pins=manifesto["native_relation_pins"],
        revocation={
            k: revogacao[k]
            for k in ("source_ref", "revision", "observed_at", "valid_until", "revoked_fingerprints")
        },
    )
    entrada_assemble_raw = canonicalize(entrada_assemble)
    load_assemble(entrada_assemble_raw)
    r.estado["assemble-input.json"] = entrada_assemble_raw

    atribuicao = seg.assignment
    linhas = seg.rows
    escopo = linhas["scope"]
    dono = canonicalize(
        dict(
            schema="staff-assignment-owner.v1",
            owner_ref=atribuicao["owner_ref"],
            source_ref=atribuicao["source_ref"],
            tenant=escopo["tenant"],
            environment=escopo["environment"],
            engine_name=escopo["engine_name"],
            database_incarnation=escopo["database_incarnation"],
            source_key_fingerprint=atribuicao["source_fingerprint"],
            not_before=manifesto["issued_at"],
            valid_until=manifesto["valid_until"],
        )
    )
    from .assignment_trust import parse_owner

    parse_owner(dono)
    r.estado["assignment-owner.json"] = dono
    confianca_atrib = _json(_arquivo(nat, "engine-run/assignment-trust.json", "native"), "assignment-trust")
    r.estado["assignment-spec.json"] = canonicalize(
        dict(
            schema=SPEC_SCHEMA,
            tenant=escopo["tenant"],
            environment=escopo["environment"],
            engine_name=escopo["engine_name"],
            database_incarnation=escopo["database_incarnation"],
            deployment_receipt=confianca_atrib["deployment_receipt"],
            validity_policy=confianca_atrib["validity_policy"],
            owner_ref=atribuicao["owner_ref"],
            source_ref=atribuicao["source_ref"],
            not_before=manifesto["issued_at"],
            not_after=manifesto["valid_until"],
        )
    )
    # Pins do verify do portal (`approver-pins.json`): o `gerar` troca os 4 que mudam por renovacao.
    r.estado["approver-pins.json"] = json.dumps(
        dict(
            capabilities="identity,staff_cases",
            staff_material_directory="/run/maezo-staff-materials/current",
            tenant=manifesto["scope"]["tenant"],
            issuer=manifesto["issuer"],
            staff_material_version_id=manifesto["material_version_id"],
            staff_public_manifest_sha256=digest(manifesto),
            staff_root_key_sha256=manifesto["root_key_fingerprint"],
            staff_designation_sha256=desig_digest,
            staff_native_configuration_sha256=manifesto["native_configuration_digest"],
            staff_scope=manifesto["scope"],
            staff_native_origin=manifesto["native_origin"],
            staff_native_server_spki_sha256=spki_servidor,
            staff_native_schema=manifesto["native_schema"],
            staff_engine_schema=manifesto["engine_schema"],
            staff_read_key_sha256=f_leitura,
            staff_witness_key_sha256=f_witness,
            staff_maximum_seconds=int(manifesto["native_maximum_seconds"]),
        ),
        sort_keys=True,
    ).encode()

    for nome, valor in (
        ("native-materials.json", nat),
        ("job.json", job),
        ("rows.json", linhas),
        ("assignment-activate.json", atribuicao),
    ):
        r.estado[nome] = json.dumps(valor, sort_keys=True, separators=(",", ":")).encode()
    if seg.syn is not None:
        r.estado["syn-fixture.json"] = json.dumps(seg.syn, sort_keys=True, separators=(",", ":")).encode()

    # ---------------------------------------------------------------- fontes/
    chave_fonte = _pem_ed25519(
        _b64d(atribuicao["source_key_pem_b64"], "assignment/source_key"), "chave da fonte"
    )
    spki_fonte = _spki_der(chave_fonte.public_key())
    _exige(
        _sha(spki_fonte) == atribuicao["source_fingerprint"],
        "chave da fonte de atribuicao == fingerprint do segredo",
        r,
    )
    r.fontes["assignment-source.json"] = canonicalize(
        dict(
            schema=SOURCE_SCHEMA,
            key_id=atribuicao["source_key_id"],
            public_key_spki_base64=base64.b64encode(spki_fonte).decode(),
            fingerprint=atribuicao["source_fingerprint"],
        )
    )
    r.fontes["conexoes-staff.json"] = canonicalize(
        dict(
            session_lock={
                k: v for k, v in manifesto["session_lock_connection"].items() if k != "function_pin"
            },
            native_witness=manifesto["native_witness_connection"],
        )
    )

    for arvore, conteudo in (("materiais", r.materiais), ("estado", r.estado), ("fontes", r.fontes)):
        for caminho in sorted(conteudo):
            _registrar(r, arvore, caminho, conteudo[caminho])
    return r
