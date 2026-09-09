"""PFSU-01 narrow seam positives and adjacent/aliased credential/effect refusals."""

from pathlib import Path

import pytest
from scripts.ci.check_effect_chokepoint_fence import scan_tree

ROOT = Path(__file__).resolve().parents[3]
MTLS = "gateway/human/transport.py"
OIDC = "gateway/human/identity_composition.py"
CALL = "httpx.AsyncClient(verify=True, trust_env=False, follow_redirects=False, timeout=10.0)"


def scan(tmp_path, path, source):
    file = tmp_path / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(source)
    return scan_tree(tmp_path)


def test_exact_production_seams_and_credential_read_are_nonvacuous():
    result = scan_tree(ROOT / "src/maezo")
    assert result.ok, result.render()
    assert result.counters["8.2_httpx_scoped_seam_sanctioned"] == 2
    assert result.counters["8.3_secret_scoped_seam_sanctioned"] == 1


@pytest.mark.parametrize("path", [MTLS, OIDC, "portal/api/app.py", "gateway/human/nearby.py"])
@pytest.mark.parametrize(
    "source",
    [
        "import httpx\ndef adjacent(): return httpx.AsyncClient()",
        "import httpx as h\ndef adjacent(): return h.AsyncClient()",
        "from httpx import AsyncClient as C\ndef adjacent(): return C()",
        "from httpx import Client as C\ndef adjacent(): return C()",
        "import httpx\nC = httpx.AsyncClient\ndef adjacent(): return C()",
        "from httpx import AsyncClient\nC = AsyncClient\nD = C\ndef adjacent(): return D()",
    ],
)
def test_adjacent_and_aliased_constructors_refuse(tmp_path, path, source):
    result = scan(tmp_path, path, source)
    assert not result.ok
    assert any("[8.2]" in v for v in result.violations)


@pytest.mark.parametrize("replacement", ["verify=False", "trust_env=True", "follow_redirects=True"])
def test_exact_oidc_seam_security_option_mutations_refuse(tmp_path, replacement):
    key = replacement.split("=")[0]
    old = key + ("=True" if key == "verify" else "=False")
    result = scan(
        tmp_path,
        OIDC,
        "import httpx\ndef build_human_identity_adapters():\n    return " + CALL.replace(old, replacement),
    )
    assert not result.ok


@pytest.mark.parametrize(
    "body",
    [
        f"return ({CALL}, {CALL})",
        f"return lambda: {CALL}",
        f"def nested(): return {CALL}",
        f"return [{CALL} for _ in range(1)]",
    ],
)
def test_duplicate_or_nested_constructor_does_not_inherit_seam(tmp_path, body):
    result = scan(tmp_path, OIDC, "import httpx\ndef build_human_identity_adapters():\n    " + body)
    assert not result.ok


@pytest.mark.parametrize("path", [MTLS, OIDC, "portal/api/app.py", "agents/helena/escape.py"])
@pytest.mark.parametrize(
    "body",
    [
        "return config.database_url.get_secret_value()",
        "unwrap = config.database_url.get_secret_value\n    return unwrap()",
        "return getattr(config.database_url, 'get_secret_value')()",
    ],
)
def test_escaped_or_adjacent_credential_extraction_refuses(tmp_path, path, body):
    result = scan(tmp_path, path, "def adjacent():\n    " + body)
    assert not result.ok
    assert any("[8.3-secret]" in v for v in result.violations)


@pytest.mark.parametrize("path", [MTLS, OIDC, "portal/api/app.py"])
def test_raw_engine_operation_has_no_new_rest_exemption(tmp_path, path):
    result = scan(tmp_path, path, "def raw(client): return client.post('/message', json={})")
    assert not result.ok
    assert any("[8.2]" in v for v in result.violations)


def test_definition_time_constructor_does_not_inherit_factory_scope(tmp_path):
    result = scan(tmp_path, OIDC, f"import httpx\ndef build_human_identity_adapters(client={CALL}): pass")
    assert not result.ok


@pytest.mark.parametrize("replacement", ["verify=False", "trust_env=True", "follow_redirects=True"])
def test_mtls_exact_constructor_security_mutants_refuse(tmp_path, replacement):
    source = (ROOT / "src/maezo" / MTLS).read_text()
    key = replacement.split("=")[0]
    old = key + ("=self._tls" if key == "verify" else "=False")
    assert source.count(old) == 1
    result = scan(tmp_path, MTLS, source.replace(old, replacement))
    assert not result.ok
    assert any("[8.2]" in v for v in result.violations)
