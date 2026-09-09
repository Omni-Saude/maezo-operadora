"""P-17/F1/F7: identify the collaborator before comparing its shared method names."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from tests.unit.runtime import test_inference_fakes_match_protocol as fence

_HTTP = """import http.client as wire
import pytest
class Specimen:
    def close(self): pass
wire_alias = wire
replacement = Specimen
monkeypatch = pytest.MonkeyPatch()
monkeypatch.setattr(wire_alias, "HTTPConnection", replacement)
"""
_SOCKET = """from httpcore._backends.sync import SyncStream as Parent
class Stream(Parent): pass
class Specimen:
    def send(self, data): return len(data)
    def close(self): pass
Stream(Specimen())
"""


@pytest.mark.parametrize("source", [_HTTP, _SOCKET])
@pytest.mark.parametrize("name", ["Renamed", "FakeDmn", "RegionalTransport"])
def test_actual_external_collaborator_use_is_independent_of_class_name(source, name):
    assert fence._achados_estruturais_em_fonte(source.replace("Specimen", name), "arbitrary.py") == []


@pytest.mark.parametrize("source", [_HTTP, _SOCKET])
def test_external_import_and_lookalike_class_without_use_do_not_suppress_findings(source):
    without_use = source.rsplit("\n", 2)[0] + "\n"
    assert fence._achados_estruturais_em_fonte(without_use, "arbitrary.py")


@pytest.mark.parametrize("source", [_HTTP, _SOCKET])
def test_external_evidence_is_bound_to_one_class_not_its_sibling(source):
    source += "class Sibling:\n    def close(self): pass\n"
    findings = fence._achados_estruturais_em_fonte(source, "arbitrary.py")
    assert len(findings) == 1
    assert "Sibling" in findings[0] and "DmnTransport.close" in findings[0]


@pytest.mark.parametrize("source", [_HTTP, _SOCKET])
def test_external_collaborator_does_not_hide_dmn_anchor_or_close_drift(source):
    source = source.replace(
        "class Specimen:\n",
        "class Specimen:\n    async def evaluate(self, table, dmn_input): pass\n",
    )
    findings = fence._achados_estruturais_em_fonte(source, "arbitrary.py")
    assert any("DmnTransport.evaluate" in finding for finding in findings)
    assert any("DmnTransport.close" in finding for finding in findings)


@pytest.mark.parametrize("async_prefix", ["", "async "])
def test_regional_protocol_base_keeps_sync_and_signature_drift_visible(async_prefix):
    source = _SOCKET.replace(
        "class Specimen:",
        "from maezo.runtime.inference.br_regional import BrRegionalTransport as Transport\n"
        "class Specimen(Transport):",
    ).replace("def send(self, data)", async_prefix + "def send(self, data)")
    findings = fence._achados_estruturais_em_fonte(source, "arbitrary.py")
    assert any("BrRegionalTransport.send" in finding for finding in findings)


@pytest.mark.parametrize("async_prefix", ["", "async "])
def test_agent_and_regional_drift_in_sibling_classes_remains_visible(async_prefix):
    source = _SOCKET + (
        "from maezo.agents.lucas.graph import WhatsAppSender\n"
        "class Sender:\n    " + async_prefix + "def send(self, wrong, text): pass\n"
    )
    findings = fence._achados_estruturais_em_fonte(source, "arbitrary.py")
    assert len(findings) == 1 and "WhatsAppSender:lucas.send" in findings[0]
    assert "Sender" in findings[0]


@pytest.mark.parametrize(
    "source",
    [
        _SOCKET.replace("Stream(Specimen())", "def unrelated(Stream):\n    Stream(Specimen())"),
        _HTTP.replace(
            'monkeypatch.setattr(wire_alias, "HTTPConnection", replacement)',
            'def unrelated(wire_alias):\n    monkeypatch.setattr(wire_alias, "HTTPConnection", replacement)',
        ),
        _SOCKET.replace("Stream(Specimen())", "Stream = unrelated\nStream(Specimen())"),
        _SOCKET.replace("Stream(Specimen())", "def Stream(value): pass\nStream(Specimen())"),
    ],
)
def test_shadowed_external_bindings_do_not_prove_a_collaborator(source):
    assert fence._achados_estruturais_em_fonte(source, "arbitrary.py")


@pytest.mark.parametrize("kind", ["socket", "http"])
@pytest.mark.parametrize("binding", ["annotated", "tuple"])
def test_annotated_and_destructured_rebindings_invalidate_external_identity(kind, binding):
    if kind == "socket":
        rebinding = {
            "annotated": "Stream: object = unrelated",
            "tuple": "Stream, unused = (unrelated, None)",
        }[binding]
        source = "from maezo.agents.lucas.graph import WhatsAppSender\n" + _SOCKET.replace(
            "Stream(Specimen())", f"def unrelated(value): return value\n{rebinding}\nStream(Specimen())"
        )
        expected = "WhatsAppSender:lucas.send"
    else:
        rebinding = {
            "annotated": "wire_alias: object = Unrelated",
            "tuple": "wire_alias, unused = (Unrelated, None)",
        }[binding]
        source = _HTTP.replace(
            'monkeypatch.setattr(wire_alias, "HTTPConnection", replacement)',
            f"class Unrelated: pass\n{rebinding}\n"
            'monkeypatch.setattr(wire_alias, "HTTPConnection", replacement)',
        )
        expected = "DmnTransport.close"
    findings = fence._achados_estruturais_em_fonte(source, "arbitrary.py")
    assert any(expected in finding for finding in findings)


@pytest.mark.parametrize(
    "source",
    [
        _SOCKET.replace(
            "Stream(Specimen())",
            "def unrelated(value): return value\nStream = unrelated\nStream(Specimen())",
        ),
        _HTTP.replace(
            'monkeypatch.setattr(wire_alias, "HTTPConnection", replacement)',
            "class Unrelated: pass\nwire_alias = Unrelated\n"
            'monkeypatch.setattr(wire_alias, "HTTPConnection", replacement)',
        ),
    ],
)
def test_plain_rebinding_remains_conservative(source):
    assert fence._achados_estruturais_em_fonte(source, "arbitrary.py")


def test_annotation_without_a_value_does_not_rebind_external_identity():
    source = _HTTP.replace("replacement = Specimen", "wire_alias: object\nreplacement = Specimen")
    assert fence._achados_estruturais_em_fonte(source, "arbitrary.py") == []


def test_unrelated_setattr_receiver_does_not_prove_monkeypatch():
    source = """import http.client as wire
class Specimen:
    def close(self): pass
class Recorder:
    def setattr(self, target, attribute, replacement): return replacement
Recorder().setattr(wire, "HTTPConnection", Specimen)
"""
    findings = fence._achados_estruturais_em_fonte(source, "arbitrary.py")
    assert any("DmnTransport.close" in finding for finding in findings)


def test_pytest_monkeypatch_fixture_forwarding_proves_the_receiver():
    source = """import http.client as wire
import pytest
class Specimen:
    def close(self): pass
def install(receiver):
    receiver.setattr(wire, "HTTPConnection", Specimen)
def test_install(monkeypatch):
    install(monkeypatch)
"""
    assert fence._achados_estruturais_em_fonte(source, "arbitrary.py") == []


def test_rebound_fixture_parameter_does_not_prove_forwarded_receiver():
    source = """import http.client as wire
import pytest
class Specimen:
    def close(self): pass
class Recorder:
    def setattr(self, target, attribute, replacement): return replacement
def install(receiver):
    receiver.setattr(wire, "HTTPConnection", Specimen)
def test_install(monkeypatch):
    monkeypatch = Recorder()
    install(monkeypatch)
"""
    findings = fence._achados_estruturais_em_fonte(source, "arbitrary.py")
    assert any("DmnTransport.close" in finding for finding in findings)


@pytest.mark.parametrize("override", ["fixture", "named_fixture", "parametrize", "pytest_binding"])
def test_overridden_or_parametrized_monkeypatch_name_does_not_prove_receiver(override):
    declarations = {
        "fixture": (
            "@pytest.fixture\ndef monkeypatch():\n    return Recorder()\ndef test_install(monkeypatch):\n"
        ),
        "named_fixture": (
            '@pytest.fixture(name="monkeypatch")\ndef recorder_fixture():\n'
            "    return Recorder()\ndef test_install(monkeypatch):\n"
        ),
        "parametrize": (
            '@pytest.mark.parametrize("monkeypatch", [Recorder()])\ndef test_install(monkeypatch):\n'
        ),
        "pytest_binding": "pytest = Recorder()\ndef test_install(monkeypatch):\n",
    }
    source = f"""import http.client as wire
import pytest
class Specimen:
    def close(self): pass
class Recorder:
    def setattr(self, target, attribute, replacement): return replacement
{declarations[override]}    monkeypatch.setattr(wire, "HTTPConnection", Specimen)
"""
    findings = fence._achados_estruturais_em_fonte(source, "arbitrary.py")
    assert any("DmnTransport.close" in finding for finding in findings)


@pytest.mark.parametrize(
    "replacement",
    [
        "Recorder().setattr",
        "getattr(monkeypatch, 'setattr')",
        "monkeypatch = Recorder()\n    monkeypatch.setattr",
    ],
)
def test_unsupported_or_shadowed_setter_shapes_do_not_grant_exemption(replacement):
    source = """import http.client as wire
import pytest
class Specimen:
    def close(self): pass
class Recorder:
    def setattr(self, target, attribute, replacement): return replacement
def test_install(monkeypatch):
    SETTER(wire, "HTTPConnection", Specimen)
""".replace("SETTER", replacement)
    findings = fence._achados_estruturais_em_fonte(source, "arbitrary.py")
    assert any("DmnTransport.close" in finding for finding in findings)


@pytest.mark.parametrize(
    "relative",
    ["tests/unit/ci/test_ecs_portal_deployment.py", "tests/unit/portal/test_tls_failure_oracle.py"],
)
def test_actual_correct_doubles_are_accepted_without_editing_or_tagging_them(relative):
    source = (fence._RAIZ / relative).read_text()
    assert "__fence_negative_fixture__" not in source
    assert fence._achados_estruturais_em_fonte(source, "renamed/location.py") == []
    # AST renaming proves no concrete test class name participates in the decision.
    tree = ast.parse(source)
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            node.name = "Renamed" + node.name
        elif isinstance(node, ast.Name) and node.id in names:
            node.id = "Renamed" + node.id
    assert fence._achados_estruturais_em_fonte(ast.unparse(tree), "elsewhere.py") == []


def test_full_tree_fence_is_still_checkout_bound():
    assert Path(__file__).resolve().parents[2] == fence._TESTS_ROOT


@pytest.mark.parametrize(
    ("relative", "method", "family"),
    [
        ("tests/unit/agents/test_andre.py", "evaluate", "DmnTransport"),
        ("tests/unit/runtime/test_inference_retry_budget.py", "send", "BrRegionalTransport"),
        ("tests/unit/agents/test_lucas.py", "send", "WhatsAppSender:lucas"),
    ],
)
@pytest.mark.parametrize("mutation", ["parameter", "sync"])
def test_real_family_fixture_mutations_are_still_refused(relative, method, family, mutation):
    source = (fence._RAIZ / relative).read_text()
    assert fence._achados_estruturais_em_fonte(source, relative) == []
    tree = ast.parse(source)
    owner, original = next(
        (owner, node)
        for owner in ast.walk(tree)
        if isinstance(owner, ast.ClassDef)
        for node in owner.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == method
    )
    if mutation == "parameter":
        original.args.args[1].arg = "drifted_parameter"
    else:
        replacement = ast.FunctionDef(
            name=original.name,
            args=original.args,
            body=original.body,
            decorator_list=original.decorator_list,
            returns=original.returns,
            type_comment=original.type_comment,
        )
        owner.body[owner.body.index(original)] = ast.copy_location(replacement, original)
    findings = fence._achados_estruturais_em_fonte(ast.unparse(tree), relative)
    assert any(owner.name in finding and family + "." + method in finding for finding in findings)
