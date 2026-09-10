"""PFSU-01 narrow seam positives and adjacent/aliased credential/effect refusals."""

from pathlib import Path

import pytest
from scripts.ci.check_effect_chokepoint_fence import scan_tree

ROOT = Path(__file__).resolve().parents[3]
MTLS = "gateway/human/transport.py"
OIDC = "gateway/portal_identity.py"
ENGINE = "gateway/engine_transport.py"
READ = "gateway/human/read_transport.py"
ASSIGNMENT = "gateway/human/assignment_transport.py"
AUTH = "gateway/human/auth_transport.py"
NATIVE_FETCH = "gateway/native_fetch/transport.py"
DOCUMENT_REQUESTS = "gateway/document_requests/transport.py"
STAFF = "gateway/staff_cases/publisher.py"
STAFF_BOOTSTRAP = "gateway/staff_cases/production.py"
CALL = "httpx.AsyncClient(verify=True, trust_env=False, follow_redirects=False, timeout=10.0)"


def scan(tmp_path, path, source):
    file = tmp_path / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(source)
    return scan_tree(tmp_path)


def test_exact_production_seams_and_credential_read_are_nonvacuous():
    result = scan_tree(ROOT / "src/maezo")
    assert result.ok, result.render()
    assert result.counters["8.2_httpx_scoped_seam_sanctioned"] == 9
    assert result.counters["8.3_secret_scoped_seam_sanctioned"] == 2


@pytest.mark.parametrize(
    "path",
    [
        MTLS,
        OIDC,
        ENGINE,
        READ,
        ASSIGNMENT,
        AUTH,
        NATIVE_FETCH,
        DOCUMENT_REQUESTS,
        STAFF,
        "portal/api/app.py",
        "gateway/human/nearby.py",
    ],
)
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


@pytest.mark.parametrize(
    "path", [MTLS, OIDC, ENGINE, READ, STAFF_BOOTSTRAP, "portal/api/app.py", "agents/helena/escape.py"]
)
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


@pytest.mark.parametrize(
    "path",
    [MTLS, OIDC, ENGINE, READ, ASSIGNMENT, AUTH, NATIVE_FETCH, DOCUMENT_REQUESTS, STAFF, "portal/api/app.py"],
)
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


# Pin independently from the gate registry: a registry edit alone cannot redefine
# which constructor, class, method, or TLS source this evidence authorizes.
NEW_SEAMS = [
    (
        ENGINE,
        "EngineOperationsClient",
        "_exchange",
        "httpx.AsyncClient(verify=context, trust_env=False, "
        "follow_redirects=False, timeout=self._config.timeout)",
    ),
    (
        READ,
        "PortalReadClient",
        "__init__",
        "httpx.AsyncClient(verify=tls_context, transport=_BorrowedTransport(self._transport), "
        "follow_redirects=False, trust_env=False, timeout=timeout_seconds)",
    ),
    (
        ASSIGNMENT,
        "AssignmentPrivateTransport",
        "__init__",
        "httpx.AsyncClient(verify=tls_context, transport=transport, timeout=timeout_seconds, "
        "trust_env=False, follow_redirects=False)",
    ),
    (
        AUTH,
        "AuthNativeClient",
        "__init__",
        "httpx.AsyncClient(verify=tls_context, transport=transport, follow_redirects=False, "
        "trust_env=False, timeout=timeout_seconds)",
    ),
    (
        NATIVE_FETCH,
        "NativeFetchClient",
        "_exchange",
        "httpx.AsyncClient(verify=context, trust_env=False, follow_redirects=False, timeout=30)",
    ),
    (
        DOCUMENT_REQUESTS,
        "NativeChannel",
        "exchange",
        "httpx.AsyncClient(verify=tls, trust_env=False, follow_redirects=False, timeout=30)",
    ),
    (
        STAFF,
        "StaffNativeClient",
        "__init__",
        "httpx.AsyncClient(verify=tls, timeout=seconds, trust_env=False, follow_redirects=False)",
    ),
]


def scoped_source(cls, method, body, imports="import httpx"):
    return f"{imports}\nclass {cls}:\n    def {method}(self):\n        {body}\n"


@pytest.mark.parametrize("path,cls,method,call", NEW_SEAMS)
def test_new_exact_constructor_in_its_own_scope_is_allowed(tmp_path, path, cls, method, call):
    result = scan(tmp_path, path, scoped_source(cls, method, f"return {call}"))
    assert result.ok, result.render()
    assert result.counters["8.2_httpx_scoped_seam_sanctioned"] == 1


@pytest.mark.parametrize("path,cls,method,call", NEW_SEAMS)
@pytest.mark.parametrize("flag", ["verify", "trust_env", "follow_redirects", "timeout"])
def test_new_constructor_security_options_are_exact(tmp_path, path, cls, method, call, flag):
    import ast

    node = ast.parse(call, mode="eval").body
    keyword = next(k for k in node.keywords if k.arg == flag)
    keyword.value = ast.Constant(value=False if flag == "verify" else None if flag == "timeout" else True)
    result = scan(tmp_path, path, scoped_source(cls, method, f"return {ast.unparse(node)}"))
    assert not result.ok
    assert any("[8.2]" in v for v in result.violations)


def test_read_constructor_cannot_substitute_an_unowned_pool(tmp_path):
    path, cls, method, call = NEW_SEAMS[1]
    altered = call.replace("_BorrowedTransport(self._transport)", "self._transport")
    result = scan(tmp_path, path, scoped_source(cls, method, f"return {altered}"))
    assert not result.ok
    assert any("[8.2]" in v for v in result.violations)


@pytest.mark.parametrize("path,cls,method,call", NEW_SEAMS)
@pytest.mark.parametrize("relocation", ["file", "class", "method", "nested", "lambda", "comprehension"])
def test_exact_new_call_cannot_move_or_nest(tmp_path, path, cls, method, call, relocation):
    body = f"return {call}"
    if relocation == "file":
        path = "gateway/human/copied_transport.py"
    elif relocation == "class":
        cls += "Copy"
    elif relocation == "method":
        method += "_copy"
    elif relocation == "nested":
        body = f"def inner(): return {call}"
    elif relocation == "lambda":
        body = f"return lambda: {call}"
    else:
        body = f"return [{call} for _ in range(1)]"
    result = scan(tmp_path, path, scoped_source(cls, method, body))
    assert not result.ok
    assert any("[8.2]" in v for v in result.violations)


@pytest.mark.parametrize("path,cls,method,call", NEW_SEAMS)
@pytest.mark.parametrize("copy", ["same_method", "duplicate_class", "adjacent_method", "definition_time"])
def test_new_scope_does_not_authorize_extra_constructors(tmp_path, path, cls, method, call, copy):
    source = scoped_source(cls, method, f"return {call}")
    if copy == "same_method":
        source = scoped_source(cls, method, f"return ({call}, {call})")
    elif copy == "duplicate_class":
        source += scoped_source(cls, method, f"return {call}")
    elif copy == "adjacent_method":
        source += f"    def adjacent(self): return {call}\n"
    else:
        source = f"import httpx\nclass {cls}:\n    def {method}(self, client={call}): pass\n"
    result = scan(tmp_path, path, source)
    assert not result.ok
    assert any("[8.2]" in v for v in result.violations)


@pytest.mark.parametrize("path,cls,method,call", NEW_SEAMS)
@pytest.mark.parametrize(
    "imports,constructor",
    [
        ("import httpx as h", "h.AsyncClient"),
        ("from httpx import AsyncClient as C", "C"),
        ("import httpx\nC = httpx.AsyncClient", "C"),
        ("import httpx\nh = httpx\nC = h.AsyncClient", "C"),
    ],
)
def test_new_exact_scope_does_not_authorize_constructor_aliases(
    tmp_path, path, cls, method, call, imports, constructor
):
    source = scoped_source(cls, method, "return " + call.replace("httpx.AsyncClient", constructor), imports)
    result = scan(tmp_path, path, source)
    assert not result.ok
    assert any("[8.2]" in v for v in result.violations)


# Independent closed assignments: a registry edit cannot redefine the permitted
# identity source, target variable or extraction wrapper. No real secret is read.
SECRET_ASSIGNMENTS = [
    (
        OIDC,
        "build_human_identity_adapters",
        "database_url = make_url(config.database_url.get_secret_value())",
    ),
    (STAFF_BOOTSTRAP, "staff_runtime", "identity_url = make_url(identity.database_url.get_secret_value())"),
]


@pytest.mark.parametrize("path,function,assignment", SECRET_ASSIGNMENTS)
def test_exact_identity_credential_assignments_are_allowed(tmp_path, path, function, assignment):
    result = scan(tmp_path, path, f"async def {function}():\n    {assignment}\n")
    assert result.ok, result.render()
    assert result.counters["8.3_secret_scoped_seam_sanctioned"] == 1


@pytest.mark.parametrize("path,function,assignment", SECRET_ASSIGNMENTS)
@pytest.mark.parametrize(
    "change",
    [
        "target",
        "receiver",
        "wrapper",
        "alias",
        "duplicate",
        "file",
        "function",
        "nested",
        "lambda",
        "definition_time",
    ],
)
def test_exact_identity_credential_assignment_mutations_refuse(tmp_path, path, function, assignment, change):
    source = f"async def {function}():\n    {assignment}\n"
    if change == "target":
        source = source.replace(assignment.split(" = ")[0], "copied_url", 1)
    elif change == "receiver":
        source = source.replace(".database_url", ".other_url")
    elif change == "wrapper":
        source = source.replace("make_url(", "str(")
    elif change == "alias":
        receiver = "config" if path == OIDC else "identity"
        source = (
            f"async def {function}():\n    getter = {receiver}.database_url.get_secret_value\n"
            "    identity_url = make_url(getter())\n"
        )
    elif change == "duplicate":
        source += f"    {assignment}\n"
    elif change == "file":
        path = "gateway/staff_cases/copied_production.py"
    elif change == "function":
        source = source.replace(function, function + "_copy", 1)
    elif change == "nested":
        source = f"async def {function}():\n    def nested():\n        {assignment}\n"
    elif change == "lambda":
        rhs = assignment.split(" = ")[1]
        source = f"async def {function}():\n    return lambda: {rhs}\n"
    else:
        rhs = assignment.split(" = ")[1]
        source = f"async def {function}(default={rhs}):\n    pass\n"
    result = scan(tmp_path, path, source)
    assert not result.ok
    assert any("[8.3-secret]" in v for v in result.violations)
