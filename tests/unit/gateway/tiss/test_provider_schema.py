"""Genuine ANS XSD, exclusively synthetic payloads; no authority/receipt claim."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest
from lxml import etree

from maezo.gateway.tiss import Direction, Profile, ProviderSchemaValidator
from maezo.gateway.tiss import provider_schema as module

N = module.NAMESPACE
NS = {"x": "http://www.w3.org/2001/XMLSchema"}
# All identifiers and hash below are SYNTHETIC_TEST_ONLY. This positive proves only shape.
HEADER = """<ans:cabecalho><ans:identificacaoTransacao>
<ans:tipoTransacao>PROTOCOLO_RECEBIMENTO</ans:tipoTransacao>
<ans:sequencialTransacao>TESTONLY1</ans:sequencialTransacao>
<ans:dataRegistroTransacao>2026-10-05</ans:dataRegistroTransacao>
<ans:horaRegistroTransacao>00:00:00</ans:horaRegistroTransacao>
</ans:identificacaoTransacao><ans:origem><ans:registroANS>000000</ans:registroANS></ans:origem>
<ans:destino><ans:identificacaoPrestador><ans:codigoPrestadorNaOperadora>TESTONLY</ans:codigoPrestadorNaOperadora>
</ans:identificacaoPrestador></ans:destino><ans:Padrao>4.03.00</ans:Padrao></ans:cabecalho>"""
BODY = """<ans:recebimentoLote><ans:mensagemErro><ans:codigoGlosa>1001</ans:codigoGlosa>
<ans:descricaoGlosa>SYNTHETIC TEST ONLY</ans:descricaoGlosa></ans:mensagemErro></ans:recebimentoLote>"""
XML = (
    f'<ans:mensagemTISS xmlns:ans="{N}">{HEADER}<ans:operadoraParaPrestador>{BODY}'
    "</ans:operadoraParaPrestador><ans:epilogo><ans:hash>TESTONLY_NOT_VALIDATED</ans:hash>"
    "</ans:epilogo></ans:mensagemTISS>"
).encode()
WS = (
    f'<ans:protocoloRecebimentoWS xmlns:ans="{N}">{HEADER}{BODY}'
    "<ans:hash>TESTONLY_NOT_VALIDATED</ans:hash></ans:protocoloRecebimentoWS>"
).encode()


def validate(payload: bytes, **kwargs: object) -> module.ProviderSchemaResult:
    options = dict(
        payload=payload,
        profile=Profile.XML,
        direction=Direction.OPERATOR_TO_PROVIDER,
        operation="PROTOCOLO_RECEBIMENTO",
        catalogue_version="04.03.00",
    )
    options.update(kwargs)
    return ProviderSchemaValidator().validate(**options)  # type: ignore[arg-type]


@pytest.mark.parametrize("profile,payload", [(Profile.XML, XML), (Profile.WS_BODY, WS)])
def test_official_graphs_accept_synthetic_shape(profile: Profile, payload: bytes) -> None:
    result = validate(payload, profile=profile)
    assert result.available and result.schema_valid and result.xsd_valid
    assert result.graph_sha256 == module._GRAPHS[profile]
    assert result.reasons == ()
    assert not any(field in asdict(result) for field in ("transmitted", "signed", "approved", "payload_hash"))


@pytest.mark.parametrize("old_version", [b"4.00.00", b"4.00.01", b"4.01.00", b"4.02.00"])
def test_xsd_accepts_old_wire_but_contract_refuses(old_version: bytes) -> None:
    result = validate(XML.replace(b"4.03.00", old_version))
    assert result.xsd_valid is True and result.reasons == ("WIRE_VERSION_MISMATCH",)
    assert not result.schema_valid


def test_xsd_accepts_missing_body_but_contract_refuses() -> None:
    root = etree.fromstring(XML)
    body = root.find(f"{{{N}}}operadoraParaPrestador")
    assert body is not None
    root.remove(body)
    result = validate(etree.tostring(root))
    assert result.xsd_valid is True and result.reasons == ("BODY_OPERATION_MISMATCH",)


def test_xsd_accepts_empty_wrapper_but_contract_refuses() -> None:
    root = etree.fromstring(XML)
    body = root.find(f"{{{N}}}operadoraParaPrestador")
    assert body is not None
    body.clear()
    result = validate(etree.tostring(root))
    assert result.xsd_valid is True and result.reasons == ("BODY_OPERATION_MISMATCH",)


def test_xsd_accepts_transaction_swap_but_contract_refuses() -> None:
    result = validate(XML.replace(b"PROTOCOLO_RECEBIMENTO", b"RECURSO_GLOSA"))
    assert result.xsd_valid is True and result.reasons == ("TRANSACTION_MISMATCH",)


def test_transaction_matches_selected_but_body_disagrees() -> None:
    payload = XML.replace(b"PROTOCOLO_RECEBIMENTO", b"CANCELAMENTO_GUIA_RECIBO")
    result = validate(payload, operation="CANCELAMENTO_GUIA_RECIBO")
    assert result.xsd_valid is True and result.reasons == ("BODY_OPERATION_MISMATCH",)


def test_ws_root_cannot_be_selected_by_header_alone() -> None:
    payload = WS.replace(b"PROTOCOLO_RECEBIMENTO", b"CANCELAMENTO_GUIA_RECIBO")
    result = validate(payload, profile=Profile.WS_BODY, operation="CANCELAMENTO_GUIA_RECIBO")
    assert result.xsd_valid is True and result.reasons == ("ROOT_MISMATCH",)


@pytest.mark.parametrize("mutation", ["header", "cardinality"])
def test_required_header_and_cardinality(mutation: str) -> None:
    root = etree.fromstring(XML)
    if mutation == "header":
        header = root.find(f"{{{N}}}cabecalho")
        assert header is not None
        root.remove(header)
    else:
        etree.SubElement(root, f"{{{N}}}epilogo")
    result = validate(etree.tostring(root))
    assert result.xsd_valid is False and result.reasons == ("XSD_INVALID",)


def test_provider_direction_accepts_synthetic_cancel_shape() -> None:
    header = HEADER.replace("PROTOCOLO_RECEBIMENTO", "CANCELA_GUIA")
    root = etree.fromstring(f'<ans:mensagemTISS xmlns:ans="{N}">{header}</ans:mensagemTISS>')
    origin = root.find(f"{{{N}}}cabecalho/{{{N}}}origem")
    destination = root.find(f"{{{N}}}cabecalho/{{{N}}}destino")
    assert origin is not None and destination is not None
    first, second = origin[0], destination[0]
    origin.remove(first)
    destination.remove(second)
    origin.append(second)
    destination.append(first)
    body = etree.SubElement(root, f"{{{N}}}prestadorParaOperadora")
    body.append(
        etree.fromstring(
            f'<ans:cancelaGuia xmlns:ans="{N}"><ans:dadosPrestador>'
            "<ans:codigoPrestadorNaOperadora>TESTONLY</ans:codigoPrestadorNaOperadora>"
            "</ans:dadosPrestador>"
            "<ans:tipoCancelamento><ans:tipoCancelamentoLote><ans:numeroLote>TESTONLY1</ans:numeroLote>"
            "</ans:tipoCancelamentoLote></ans:tipoCancelamento></ans:cancelaGuia>"
        )
    )
    epilogue = etree.SubElement(root, f"{{{N}}}epilogo")
    etree.SubElement(epilogue, f"{{{N}}}hash").text = "TESTONLY_NOT_VALIDATED"
    result = validate(
        etree.tostring(root), operation="CANCELA_GUIA", direction=Direction.PROVIDER_TO_OPERATOR
    )
    assert result.schema_valid and result.xsd_valid


def test_utf16_cannot_hide_doctype() -> None:
    source = '<!DOCTYPE ans:mensagemTISS [<!ENTITY private "PRIVATE_CANARY">]>' + XML.decode()
    assert validate(source.encode("utf-16")).reasons == ("DTD_REFUSED",)


@pytest.mark.parametrize(
    "options,reason",
    [
        ({"direction": Direction.PROVIDER_TO_OPERATOR}, "OPERATION_DIRECTION_MISMATCH"),
        ({"catalogue_version": "01.06.00"}, "UNSUPPORTED_CATALOGUE_VERSION"),
        ({"catalogue_version": "4.03.00"}, "UNSUPPORTED_CATALOGUE_VERSION"),
        ({"operation": "PRIVATE_CANARY"}, "UNSUPPORTED_OPERATION"),
        ({"profile": "bilateral_xml"}, "UNSUPPORTED_PROFILE_OR_DIRECTION"),
        ({"direction": "operator_to_ANS"}, "UNSUPPORTED_PROFILE_OR_DIRECTION"),
    ],
)
def test_closed_input_catalog(options: dict[str, object], reason: str) -> None:
    result = validate(XML, **options)
    assert not result.schema_valid and result.reasons == (reason,)


def test_xsd_accepts_wrong_origin_but_contract_refuses() -> None:
    root = etree.fromstring(XML)
    header = root.find(f"{{{N}}}cabecalho")
    assert header is not None
    origin = header.find(f"{{{N}}}origem")
    destination = header.find(f"{{{N}}}destino")
    assert origin is not None and destination is not None
    first, second = origin[0], destination[0]
    origin.remove(first)
    destination.remove(second)
    origin.append(second)
    destination.append(first)
    result = validate(etree.tostring(root))
    assert result.xsd_valid is True and result.reasons == ("HEADER_DIRECTION_MISMATCH",)


@pytest.mark.parametrize(
    "payload",
    [
        XML.replace(b"2026-10-05", b"BAD_PRIVATE_CANARY"),
        XML.replace(N.encode(), b"urn:PRIVATE_CANARY"),
        XML.replace(b"<ans:epilogo>", b"<ans:PRIVATE_CANARY/><ans:epilogo>"),
        XML.replace(b"<ans:cabecalho>", b"<ans:cabecalho><ans:PRIVATE_CANARY/>"),
        b"<PRIVATE_CANARY>",
        XML.replace(b"<ans:mensagemTISS ", b"<ans:mensagemTISS PRIVATE_CANARY='SECRET' "),
    ],
)
def test_no_private_diagnostics_or_logs(
    payload: bytes, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    result = validate(payload)
    assert not result.schema_valid
    combined = json.dumps(asdict(result)) + caplog.text + repr(capsys.readouterr())
    assert "PRIVATE_CANARY" not in combined and "SECRET" not in combined
    assert "Traceback" not in combined and "/Users/" not in combined


@pytest.mark.parametrize(
    "declaration",
    [
        '<!DOCTYPE ans:mensagemTISS [<!ENTITY private "PRIVATE_CANARY">]>',
        '<!DOCTYPE ans:mensagemTISS SYSTEM "file:///PRIVATE_CANARY">',
        '<!DOCTYPE ans:mensagemTISS [<!ENTITY private SYSTEM "https://PRIVATE_CANARY">]>',
    ],
)
def test_dtd_never_resolves(declaration: str) -> None:
    result = validate(declaration.encode() + XML)
    assert not result.schema_valid and result.reasons == ("DTD_REFUSED",)


def test_xinclude_is_never_processed() -> None:
    payload = XML.replace(
        b"<ans:epilogo>",
        b'<xi:include xmlns:xi="http://www.w3.org/2001/XInclude" href="file:///PRIVATE_CANARY"/><ans:epilogo>',
    )
    assert validate(payload).reasons == ("XML_EXTERNAL_CONSTRUCT_REFUSED",)


def test_payload_schema_location_cannot_select_a_schema() -> None:
    injected = XML.replace(
        b"<ans:mensagemTISS ",
        b'<ans:mensagemTISS xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        b'xsi:schemaLocation="urn:PRIVATE_CANARY https://PRIVATE_CANARY" ',
    )
    # schemaLocation is a hint only. The fixed local schema remains authoritative.
    assert validate(injected).schema_valid


@pytest.mark.parametrize("payload", [b"", b"x" * (module.MAX_BYTES + 1), "PRIVATE_CANARY"])
def test_byte_boundary(payload: bytes) -> None:
    assert validate(payload).reasons == ("PAYLOAD_SIZE_OR_TYPE_REFUSED",)


def test_node_ceiling() -> None:
    assert validate(b"<root>" + b"<child/>" * module.MAX_NODES + b"</root>").reasons == (
        "XML_COMPLEXITY_REFUSED",
    )


def test_depth_ceiling() -> None:
    assert validate(b"<root>" * (module.MAX_DEPTH + 1) + b"</root>" * (module.MAX_DEPTH + 1)).reasons == (
        "XML_COMPLEXITY_REFUSED",
    )


def test_deadline_refuses_even_valid_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    readings = iter([0.0, module.MAX_SECONDS + 1])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(readings))
    assert validate(XML).reasons == ("VALIDATION_DEADLINE_EXCEEDED",)


@pytest.fixture
def copied_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    original = module.resolve_spec_dir() / "schemas" / "tiss" / "04.03.00"
    target = tmp_path / "schemas" / "tiss" / "04.03.00"
    shutil.copytree(original, target)
    monkeypatch.setattr(module, "resolve_spec_dir", lambda: tmp_path)
    return target


@pytest.mark.parametrize("name", [*module._FILES, "provider-source-pin.json"])
def test_each_pin_rechecked_after_cache_hit(copied_package: Path, name: str) -> None:
    validator = ProviderSchemaValidator()
    options = dict(
        payload=XML,
        profile=Profile.XML,
        direction=Direction.OPERATOR_TO_PROVIDER,
        operation="PROTOCOLO_RECEBIMENTO",
        catalogue_version="04.03.00",
    )
    assert validator.validate(**options).schema_valid  # type: ignore[arg-type]
    target = copied_package / name
    target.write_bytes(target.read_bytes() + b" ")
    result = validator.validate(**options)  # type: ignore[arg-type]
    assert not result.available and result.reasons == ("PUBLIC_SOURCE_PIN_UNAVAILABLE",)


@pytest.mark.parametrize("kind", ["missing", "extra", "symlink", "dependency_injection"])
def test_source_graph_refuses_changes(copied_package: Path, kind: str) -> None:
    path = copied_package / "tissSimpleTypesV4_03_00.xsd"
    if kind == "missing":
        path.unlink()
    elif kind == "extra":
        (copied_package / "PRIVATE_CANARY.xsd").write_text("PRIVATE_CANARY")
    elif kind == "symlink":
        path.unlink()
        path.symlink_to("/PRIVATE_CANARY")
    else:
        path = copied_package / "tissV4_03_00.xsd"
        path.write_bytes(path.read_bytes().replace(b"tissSimpleTypesV4_03_00.xsd", b"https://PRIVATE_CANARY"))
    assert validate(XML).reasons == ("PUBLIC_SOURCE_PIN_UNAVAILABLE",)


@pytest.mark.parametrize(
    "uri",
    [
        "file:///tmp/x.xsd",
        "/tmp/x.xsd",
        "../x.xsd",
        "https://example.org/x.xsd",
        "tiss-pin:///../x.xsd",
        "tiss-pin://host/x.xsd",
        "tissV4_03_00.xsd?other=1",
    ],
)
def test_resolver_refuses_unpinned_locations(uri: str) -> None:
    resolver = module._OfflineResolver({"tissV4_03_00.xsd": b""})
    with pytest.raises(module._RefusedError):
        resolver.resolve(uri, None, object())


def test_manifest_graphs_match_official_bytes_and_catalog_names() -> None:
    package = module.resolve_spec_dir() / "schemas" / "tiss" / "04.03.00"
    manifest = json.loads((package / "provider-source-pin.json").read_text())
    assert (
        hashlib.sha256((package / "provider-source-pin.json").read_bytes()).hexdigest()
        == module.SOURCE_PIN_SHA256
    )
    for profile in manifest["profiles"]:
        canonical = json.dumps(profile["files"], sort_keys=True, separators=(",", ":")).encode()
        assert hashlib.sha256(canonical).hexdigest() == profile["canonical_file_map_sha256"]
        for name, digest in profile["files"].items():
            assert hashlib.sha256((package / name).read_bytes()).hexdigest() == digest
    xml = etree.parse(str(package / "tissV4_03_00.xsd"))
    ws = etree.parse(str(package / "tissWebServicesV4_03_00.xsd"))
    simple = etree.parse(str(package / "tissSimpleTypesV4_03_00.xsd"))
    transactions = simple.xpath(
        '//x:simpleType[@name="dm_tipoTransacao"]//x:enumeration/@value', namespaces=NS
    )
    assert set(transactions) == set(module.OPERATIONS)
    for op in module.OPERATIONS.values():
        kind = (
            "prestadorOperadora" if op.direction == Direction.PROVIDER_TO_OPERATOR else "operadoraPrestador"
        )
        assert op.xml_body in xml.xpath(
            f'//x:complexType[@name="{kind}"]/x:choice/x:element/@name', namespaces=NS
        )
        assert ws.xpath(
            f'/x:schema/x:element[@name="{op.ws_root}"]/x:complexType/x:sequence/x:element[@name="{op.ws_body}"]',
            namespaces=NS,
        )
