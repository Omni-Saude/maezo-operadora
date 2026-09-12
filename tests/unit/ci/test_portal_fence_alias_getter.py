"""PFSU-01-D1/D2: finite module aliases and the immediate credential composition."""

import pytest

from tests.unit.ci.test_portal_credential_boundary import CALL, MTLS, OIDC, scan


@pytest.mark.parametrize("path", [OIDC, MTLS, "portal/api/app.py", "agents/helena/adjacent.py"])
@pytest.mark.parametrize(
    "bindings, call",
    [
        ("import httpx\nh = httpx", "h.AsyncClient"),
        ("import httpx as imported\nh = imported", "h.Client"),
        ("import httpx\nh: object = httpx", "h.AsyncClient"),
        ("import httpx as imported\nh = imported\ni: object = h\nj = i", "j.Client"),
        ("import httpx\nh = i = httpx", "i.AsyncClient"),
        ("import httpx\nj = i\ni = h\nh = httpx", "j.AsyncClient"),
        ("import httpx\nh = httpx\nC = h.AsyncClient\nD: object = C", "D"),
        ("import httpx\nh = httpx\nh = object()", "h.Client"),
        ("import httpx\nh = object()\nh = httpx", "h.Client"),
        ("import httpx\nh = httpx\nh: object", "h.AsyncClient"),
        ("import httpx\nh = httpx\ni = h\nh = i", "i.Client"),
    ],
)
def test_finite_module_aliases_cannot_hide_construction(tmp_path, path, bindings, call):
    result = scan(tmp_path, path, bindings + "\ndef adjacent(): return " + call + "(verify=False)")
    assert not result.ok, result.render()
    assert any("[8.2]" in violation for violation in result.violations)


@pytest.mark.parametrize(
    "path, scope, safe",
    [
        (OIDC, "build_human_identity_adapters", CALL),
        (
            MTLS,
            "MTLSHumanEngineTransport._request",
            "httpx.AsyncClient(verify=self._tls, timeout=self._timeout, "
            "trust_env=False, follow_redirects=False)",
        ),
    ],
)
def test_module_alias_second_client_invalidates_sanctioned_count(tmp_path, path, scope, safe):
    if path == MTLS:
        declaration = "class MTLSHumanEngineTransport:\n    def _request(self):\n"
        indent = "        "
    else:
        declaration = "def " + scope + "(config):\n"
        indent = "    "
    source = "import httpx\n" + declaration
    source += indent + "safe = " + safe + "\n"
    source += indent + "h = httpx\n" + indent + "return h.AsyncClient(verify=False)\n"
    result = scan(tmp_path, path, source)
    assert not result.ok, result.render()
    assert result.counters.get("8.2_httpx_scoped_seam_sanctioned", 0) == 0
    assert len(result.violations) == 2


@pytest.mark.parametrize(
    "body",
    [
        "return config.database_url.get_secret_value",
        "unwrap = config.database_url.get_secret_value\n    return unwrap",
        "return consumer(config.database_url.get_secret_value)",
        "saved = [config.database_url.get_secret_value]\n    return saved",
        "return config.database_url.get_secret_value()",
        "secret = config.database_url.get_secret_value()\n    database_url = make_url(secret)",
        "database_url = make_url(config.database_url.get_secret_value)",
        "database_url = make_url(config.database_url.get_secret_value(1))",
        "database_url = make_url(config.database_url.get_secret_value(extra=True))",
        "database_url = make_url(config.database_url.get_secret_value(), extra=True)",
        "database_url = make_url(name_or_url=config.database_url.get_secret_value())",
        "database_url = consumer(config.database_url.get_secret_value())",
        "return make_url(config.database_url.get_secret_value())",
        "database_url = other = make_url(config.database_url.get_secret_value())",
        "database_url = make_url(other.database_url.get_secret_value())",
        "def nested():\n        database_url = make_url(config.database_url.get_secret_value())",
        "database_url = [make_url(config.database_url.get_secret_value()) for _ in range(1)]",
        "database_url = (lambda: make_url(config.database_url.get_secret_value()))()",
        "unwrap = getattr(config.database_url, 'get_secret_value')\n    database_url = make_url(unwrap())",
        "database_url = make_url(config.database_url.get_secret_value())\n"
        "    unwrap = config.database_url.get_secret_value",
    ],
)
def test_getter_capability_requires_immediate_exact_composition(tmp_path, body):
    result = scan(tmp_path, OIDC, "def build_human_identity_adapters(config):\n    " + body + "\n")
    assert not result.ok, result.render()
    assert result.counters.get("8.3_secret_scoped_seam_sanctioned", 0) == 0
    assert any("[8.3-secret]" in violation for violation in result.violations)


def test_exact_immediate_extraction_is_sanctioned_once(tmp_path):
    result = scan(
        tmp_path,
        OIDC,
        "def build_human_identity_adapters(config):\n"
        "    database_url = make_url(config.database_url.get_secret_value())\n",
    )
    assert result.ok, result.render()
    assert result.counters["8.3_secret_scoped_seam_sanctioned"] == 1


def test_unrelated_module_and_unused_aliases_are_not_clients(tmp_path):
    result = scan(
        tmp_path,
        "agents/helena/adjacent.py",
        "import unrelated\nimport httpx\nh = httpx\nh2: object = h\nother = unrelated\nother.AsyncClient()\n",
    )
    assert result.ok, result.render()
    assert result.counters.get("8.2_httpx_client_sanctioned", 0) == 0
