"""Offline bilateral TISS 04.03 structural checks (ADR-0063 §4, SP-OP-CONTAS/RECURSO).

Receives bytes from an already authorized custody reader. No storage, transport,
credentials, signing, source authority or clinical/financial decision is provided here.
All diagnostics are fixed tokens: libxml errors and caller names never escape.
"""

from __future__ import annotations

import hashlib
import stat
import time
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from types import MappingProxyType
from urllib.parse import urlsplit

from lxml import etree

from maezo.agents import resolve_spec_dir

NAMESPACE = "http://www.ans.gov.br/padroes/tiss/schemas"
CATALOGUE_VERSION = "04.03.00"
WIRE_VERSION = "4.03.00"
SOURCE_PIN_SHA256 = "d071d5ec3f3ed4d0b93526262e32539e046c7b21d51a3173bf2b3c42f14e6a97"
MAX_BYTES = 2 * 1024 * 1024
MAX_NODES = 50_000
MAX_DEPTH = 64
MAX_SECONDS = 2.0
_XSD = "http://www.w3.org/2001/XMLSchema"


class Profile(StrEnum):
    XML = "bilateral_xml"
    WS_BODY = "bilateral_ws_body"


class Direction(StrEnum):
    PROVIDER_TO_OPERATOR = "provider_to_operator"
    OPERATOR_TO_PROVIDER = "operator_to_provider"


@dataclass(frozen=True, slots=True)
class Operation:
    """Fixed transaction/body correspondence; never an authority to execute it."""

    direction: Direction
    xml_body: str
    ws_root: str
    ws_body: str
    subtype: str | None = None


_P = Direction.PROVIDER_TO_OPERATOR
_O = Direction.OPERATOR_TO_PROVIDER
# Literal names preserve the genuine schema spelling, including envioDOcumento.
# Correspondence is a software contract subject to independent review, not a DMN.
OPERATIONS = MappingProxyType(
    {
        "ENVIO_LOTE_GUIAS": Operation(_P, "loteGuias", "loteGuiasWS", "loteGuias"),
        "ENVIO_ANEXO": Operation(_P, "loteAnexos", "loteAnexoWS", "loteAnexo"),
        "SOLIC_DEMONSTRATIVO_RETORNO": Operation(
            _P,
            "solicitacaoDemonstrativoRetorno",
            "solicitacaoDemonstrativoRetornoWS",
            "solicitacaoDemonstrativoRetorno",
        ),
        "SOLIC_STATUS_PROTOCOLO": Operation(
            _P,
            "solicitacaoStatusProtocolo",
            "solicitacaoStatusProtocoloWS",
            "solicitacaoStatusProtocolo",
        ),
        "SOLICITACAO_PROCEDIMENTOS": Operation(
            _P,
            "solicitacaoProcedimento",
            "solicitacaoProcedimentoWS",
            "solicitacaoProcedimento",
        ),
        "SOLICITA_STATUS_AUTORIZACAO": Operation(
            _P,
            "solicitaStatusAutorizacao",
            "solicitacaoStatusAutorizacaoWS",
            "solicitacaoStatusAutorizacao",
        ),
        "VERIFICA_ELEGIBILIDADE": Operation(
            _P,
            "verificaElegibilidade",
            "pedidoElegibilidadeWS",
            "pedidoElegibilidade",
        ),
        "CANCELA_GUIA": Operation(_P, "cancelaGuia", "cancelaGuiaWS", "cancelaGuia"),
        "COMUNICACAO_BENEFICIARIO": Operation(
            _P,
            "comunicacaoInternacao",
            "comunicacaoBeneficiarioWS",
            "comunicacaoBeneficiario",
        ),
        "RECURSO_GLOSA": Operation(_P, "recursoGlosa", "loteRecursoGlosaWS", "loteRecurso"),
        "SOLIC_STATUS_RECURSO_GLOSA": Operation(
            _P,
            "solicitacaoStatusRecursoGlosa",
            "solicitacaoStatusRecursoGlosaWS",
            "solicitacaoStatusProtocoloRecurso",
        ),
        "ENVIO_DOCUMENTO": Operation(_P, "envioDocumentos", "envioDocumentoWS", "envioDOcumento"),
        "PROTOCOLO_RECEBIMENTO": Operation(
            _O,
            "recebimentoLote",
            "protocoloRecebimentoWS",
            "recebimentoLote",
        ),
        "PROTOCOLO_RECEBIMENTO_ANEXO": Operation(
            _O,
            "recebimentoAnexo",
            "protocoloRecebimentoAnexoWS",
            "loteAnexo",
        ),
        "RECEBIMENTO_RECURSO_GLOSA": Operation(
            _O,
            "recebimentoRecursoGlosa",
            "protocoloRecebimentoRecursoWS",
            "recebimentoRecurso",
        ),
        "DEMONSTRATIVO_ANALISE_CONTA": Operation(
            _O,
            "demonstrativosRetorno",
            "demonstrativoRetornoWS",
            "demonstrativoRetorno",
            "demonstrativoAnaliseConta",
        ),
        "DEMONSTRATIVO_PAGAMENTO": Operation(
            _O,
            "demonstrativosRetorno",
            "demonstrativoRetornoWS",
            "demonstrativoRetorno",
            "demonstrativoPagamento",
        ),
        "DEMONSTRATIVO_ODONTOLOGIA": Operation(
            _O,
            "demonstrativosRetorno",
            "demonstrativoRetornoWS",
            "demonstrativoRetorno",
            "demonstrativoPagamentoOdonto",
        ),
        "SITUACAO_PROTOCOLO": Operation(
            _O,
            "situacaoProtocolo",
            "situacaoProtocoloWS",
            "situacaoProtocolo",
        ),
        "RESPOSTA_SOLICITACAO": Operation(
            _O,
            "autorizacaoServicos",
            "autorizacaoProcedimentoWS",
            "autorizacaoProcedimento",
        ),
        "AUTORIZACAO_ODONTOLOGIA": Operation(
            _O,
            "autorizacaoServicos",
            "autorizacaoProcedimentoWS",
            "autorizacaoProcedimento",
            "autorizacaoServicoOdonto",
        ),
        "STATUS_AUTORIZACAO": Operation(
            _O,
            "situacaoAutorizacao",
            "situacaoAutorizacaoWS",
            "situacaoAutorizacao",
        ),
        "SITUACAO_ELEGIBILIDADE": Operation(
            _O,
            "respostaElegibilidade",
            "respostaElegibilidadeWS",
            "respostaElegibilidade",
        ),
        "CANCELAMENTO_GUIA_RECIBO": Operation(
            _O,
            "reciboCancelaGuia",
            "reciboCancelaGuiaWS",
            "reciboCancelaGuia",
        ),
        "RECIBO_COMUNICACAO": Operation(
            _O,
            "reciboComunicacao",
            "reciboComunicacaoWS",
            "reciboComunicacao",
        ),
        "RESPOSTA_RECURSO_GLOSA": Operation(
            _O,
            "respostaRecursoGlosa",
            "situacaoProtocoloRecursoWS",
            "situacaoProtocoloRecurso",
        ),
        "RECEBIMENTO_DOCUMENTO": Operation(
            _O,
            "recebimentoDocumentos",
            "reciboDocumentosWS",
            "recebimentoDocumento",
        ),
    }
)
_FILES = MappingProxyType(
    {
        "tissAssinaturaDigital_v1.01.xsd": "8567690a0eb05b9681fdc575ca7c867f75bf5cb33573175b8d333617ef035221",
        "tissComplexTypesV4_03_00.xsd": "83a24d606620cd3907c5cd574a71bf9c4411a2ec9e0510979f44293fe7c5956a",
        "tissGuiasV4_03_00.xsd": "587f0b6bac24175c50635ede52356cab00ff1ae28b550adc42e3a7f03cf605c7",
        "tissSimpleTypesV4_03_00.xsd": "d854debf58ac2d96194f72db95315ef2d102de57aeaf0388512f242824fdc794",
        "tissV4_03_00.xsd": "d4c421c7e3cf936551b70d6bd794a419867b6daea554d557b4d928363c54044c",
        "tissWebServicesV4_03_00.xsd": "3044f18d2e984910c7670e3357724c2f5798d64cc4ab5919094389a3f01bce02",
        "xmldsig-core-schema.xsd": "b6388292d746c6cc8f932ce3b6bf7c7fc0bf6cba58ec385b38988887bcf5fdbb",
    }
)
_ENTRYPOINTS = {Profile.XML: "tissV4_03_00.xsd", Profile.WS_BODY: "tissWebServicesV4_03_00.xsd"}
_GRAPHS = {
    Profile.XML: "e76b264ee9a2b0069d8cf6cd4906f5267a365abb61e44b37e1edd3aa6515aa51",
    Profile.WS_BODY: "667f822947cdaacf47af4991566b64c07103d365171083a9b33aa1feb5388fe6",
}


@dataclass(frozen=True, slots=True)
class ProviderSchemaResult:
    available: bool
    schema_valid: bool
    xsd_valid: bool | None
    reasons: tuple[str, ...]
    source_pin_sha256: str = SOURCE_PIN_SHA256
    catalogue_version: str = CATALOGUE_VERSION
    wire_version: str = WIRE_VERSION
    graph_sha256: str | None = None


class _RefusedError(Exception):
    """Internal fixed failure; never contains parser, filesystem or payload text."""


class _OfflineResolver(etree.Resolver):
    def __init__(self, files: dict[str, bytes]) -> None:
        super().__init__()
        self._files = files

    def resolve(self, url: str, pubid: str | None, context: object) -> object:  # type: ignore[override]
        parts = urlsplit(url)
        if parts.scheme == "tiss-pin" and not parts.netloc and parts.path.count("/") == 1:
            name = parts.path[1:]
        elif not parts.scheme and not parts.netloc and "/" not in parts.path:
            name = parts.path
        else:
            raise _RefusedError()
        if parts.query or parts.fragment or name not in self._files:
            raise _RefusedError()
        return self.resolve_string(self._files[name], context, base_url=f"tiss-pin:///{name}")


def _parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        huge_tree=False,
        remove_comments=False,
        strip_cdata=False,
    )


def _verified_files() -> dict[str, bytes]:
    package = resolve_spec_dir() / "schemas" / "tiss" / CATALOGUE_VERSION
    if package.is_symlink() or any(parent.is_symlink() for parent in package.parents):
        raise _RefusedError()
    expected = set(_FILES) | {"provider-source-pin.json"}
    if {p.name for p in package.iterdir()} != expected:
        raise _RefusedError()
    files: dict[str, bytes] = {}
    for name in sorted(expected):
        path = package / name
        if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size > MAX_BYTES:
            raise _RefusedError()
        with path.open("rb") as source:
            data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise _RefusedError()
        digest = SOURCE_PIN_SHA256 if name == "provider-source-pin.json" else _FILES[name]
        if hashlib.sha256(data).hexdigest() != digest:
            raise _RefusedError()
        if name != "provider-source-pin.json":
            files[name] = data
    # Verify explicit dependency closure before libxml sees the schema, including namespaces.
    for name, data in files.items():
        root = etree.fromstring(data, _parser())
        expected_ns = "http://www.w3.org/2000/09/xmldsig#" if name == "xmldsig-core-schema.xsd" else NAMESPACE
        if root.tag != f"{{{_XSD}}}schema" or root.get("targetNamespace") != expected_ns:
            raise _RefusedError()
        for ref in (
            root.findall(f"{{{_XSD}}}include")
            + root.findall(f"{{{_XSD}}}import")
            + root.findall(f"{{{_XSD}}}redefine")
        ):
            if ref.get("schemaLocation") not in files:
                raise _RefusedError()
    return files


def _q(name: str) -> str:
    return f"{{{NAMESPACE}}}{name}"


def _element_children(node: etree._Element) -> list[etree._Element]:
    return [child for child in node if isinstance(child.tag, str)]


def _contract(root: etree._Element, profile: Profile, op: str, selected: Operation) -> str | None:
    expected_root = "mensagemTISS" if profile == Profile.XML else selected.ws_root
    if root.tag != _q(expected_root):
        return "ROOT_MISMATCH"
    header = root.find(_q("cabecalho"))
    if header is None:
        return "HEADER_MISSING"
    if header.findtext(_q("Padrao")) != WIRE_VERSION:
        return "WIRE_VERSION_MISMATCH"
    if header.findtext(f"{_q('identificacaoTransacao')}/{_q('tipoTransacao')}") != op:
        return "TRANSACTION_MISMATCH"
    origin = "identificacaoPrestador" if selected.direction == _P else "registroANS"
    destination = "registroANS" if selected.direction == _P else "identificacaoPrestador"
    for container, required in (("origem", origin), ("destino", destination)):
        parent = header.find(_q(container))
        if parent is None or [c.tag for c in _element_children(parent)] != [_q(required)]:
            return "HEADER_DIRECTION_MISMATCH"
    if profile == Profile.XML:
        wrapper = "prestadorParaOperadora" if selected.direction == _P else "operadoraParaPrestador"
        body = root.find(_q(wrapper))
        if body is None or [c.tag for c in _element_children(body)] != [_q(selected.xml_body)]:
            return "BODY_OPERATION_MISMATCH"
        body = body.find(_q(selected.xml_body))
    else:
        body = root.find(_q(selected.ws_body))
    if body is None or not _element_children(body):
        return "BODY_MISSING"
    if selected.subtype is not None:
        names = {c.tag for c in _element_children(body)}
        if not names <= {_q(selected.subtype), _q("mensagemErro")}:
            return "BODY_SUBTYPE_MISMATCH"
    if op == "RESPOSTA_SOLICITACAO" and body.find(_q("autorizacaoServicoOdonto")) is not None:
        return "BODY_SUBTYPE_MISMATCH"
    return None


class ProviderSchemaValidator:
    """Fixed public pin, no caller path/config override; source bytes rechecked every call.

    Cache is private and keyed by the full pin/graph/profile. The deadline is checked
    around bounded libxml work; it does not preempt a native libxml call.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str, Profile], etree.XMLSchema] = {}
        self._lock = Lock()

    def validate(
        self,
        *,
        payload: bytes,
        profile: Profile,
        direction: Direction,
        operation: str,
        catalogue_version: str,
    ) -> ProviderSchemaResult:
        start = time.monotonic()
        # Runtime validation also protects callers that ignore the Python type contract.
        if not isinstance(profile, Profile) or not isinstance(direction, Direction):
            return ProviderSchemaResult(False, False, None, ("UNSUPPORTED_PROFILE_OR_DIRECTION",))
        if catalogue_version != CATALOGUE_VERSION:
            return ProviderSchemaResult(False, False, None, ("UNSUPPORTED_CATALOGUE_VERSION",))
        if not isinstance(operation, str) or operation not in OPERATIONS:
            return ProviderSchemaResult(False, False, None, ("UNSUPPORTED_OPERATION",))
        selected = OPERATIONS[operation]
        if direction != selected.direction:
            return ProviderSchemaResult(False, False, None, ("OPERATION_DIRECTION_MISMATCH",))
        graph = _GRAPHS[profile]

        def result(reason: str, *, xsd: bool | None = None, available: bool = True) -> ProviderSchemaResult:
            return ProviderSchemaResult(available, False, xsd, (reason,), graph_sha256=graph)

        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_BYTES:
            return result("PAYLOAD_SIZE_OR_TYPE_REFUSED", available=False)
        try:
            files = _verified_files()
            key = (SOURCE_PIN_SHA256, graph, profile)
            with self._lock:
                schema = self._cache.get(key)
                if schema is None:
                    parser = _parser()
                    parser.resolvers.add(_OfflineResolver(files))
                    entrypoint = _ENTRYPOINTS[profile]
                    schema = etree.XMLSchema(
                        etree.fromstring(
                            files[entrypoint],
                            parser,
                            base_url=f"tiss-pin:///{entrypoint}",
                        )
                    )
                    self._cache[key] = schema
        except (OSError, ValueError, _RefusedError, etree.LxmlError):
            return result("PUBLIC_SOURCE_PIN_UNAVAILABLE", available=False)
        try:
            root = etree.fromstring(payload, _parser())
        except (ValueError, etree.LxmlError):
            return result("XML_MALFORMED")
        if root.getroottree().docinfo.doctype:  # type: ignore[union-attr]
            return result("DTD_REFUSED")
        nodes = 0
        stack = [(root, 1)]
        while stack:
            node, depth = stack.pop()
            nodes += 1
            if nodes > MAX_NODES or depth > MAX_DEPTH:
                return result("XML_COMPLEXITY_REFUSED")
            if node.tag == "{http://www.w3.org/2001/XInclude}include" or isinstance(node, etree._Entity):
                return result("XML_EXTERNAL_CONSTRUCT_REFUSED")
            stack.extend((child, depth + 1) for child in node)
        if time.monotonic() - start > MAX_SECONDS:
            return result("VALIDATION_DEADLINE_EXCEEDED")
        # Shared XMLSchema.error_log is intentionally never read, rendered or logged.
        try:
            with self._lock:
                valid = schema.validate(root)
        except etree.LxmlError:
            return result("XSD_INVALID", xsd=False)
        if time.monotonic() - start > MAX_SECONDS:
            return result("VALIDATION_DEADLINE_EXCEEDED")
        if not valid:
            return result("XSD_INVALID", xsd=False)
        reason = _contract(root, profile, operation, selected)
        if reason:
            return result(reason, xsd=True)
        return ProviderSchemaResult(True, True, True, (), graph_sha256=graph)
