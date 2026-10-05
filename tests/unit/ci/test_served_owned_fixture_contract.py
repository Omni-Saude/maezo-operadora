"""Adversarial static/pure tests of repository-owned fixture recognition.

Synthetic AST/inspect JSON and a temporary certificate are UNIT evidence only.
No Docker, PostgreSQL, CIB or other service is started by this suite.
"""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest

from tests.support import provider_tls_pg
from tests.unit.ci import test_live_suite_defaults_are_served as fence

SOURCE = """
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.engine import URL
from tests.support.provider_tls_pg import owned_tls_postgres
pytestmark = pytest.mark.integration

@pytest.fixture
async def owned_pg(tmp_path):
    async with owned_tls_postgres(tmp_path, owner="unit-source") as pg:
        admin = pg.admin
        context = pg.tls_context
        engine = create_async_engine(pg.url_for("unit-role", "unit-password"), connect_args={"ssl": context})
        yield engine

async def test_uses_owned(owned_pg):
    assert owned_pg
"""


def candidate(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "candidate.py"
    path.write_text(source)
    return path


def test_actual_helper_and_all_real_consumers_are_structurally_verified() -> None:
    assert fence._owned_helper_contract_is_verified()
    consumers = [path for path in fence._iter_live_suites() if fence._claims_owned_tls_fixture(path)]
    assert len(consumers) >= 3
    for path in consumers:
        assert fence._suite_uses_owned_tls_fixture(path)
        assert str(path.relative_to(fence._REPO_ROOT)) not in fence._NAME_ONLY
        assert str(path.relative_to(fence._REPO_ROOT)) not in fence._EXPLICIT_FIXTURES


def test_owned_context_requires_fixture_to_stay_alive_for_the_test(tmp_path: Path) -> None:
    assert fence._suite_uses_owned_tls_fixture(candidate(tmp_path, SOURCE))
    escaped = SOURCE.replace("        yield engine", "    yield engine")
    assert escaped != SOURCE
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, escaped))


def test_dead_branch_or_nested_generator_does_not_prove_served_fixture(tmp_path: Path) -> None:
    branched = SOURCE.replace(
        '    async with owned_tls_postgres(tmp_path, owner="unit-source") as pg:',
        '    if False:\n        async with owned_tls_postgres(tmp_path, owner="unit-source") as pg:',
    )
    lines = branched.splitlines()
    start = next(i for i, line in enumerate(lines) if "async with owned_tls" in line)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("        "):
            lines[i] = "    " + lines[i]
        elif lines[i].startswith("async def test_"):
            break
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, "\n".join(lines)))
    nested = SOURCE.replace(
        "        yield engine", "        def unused():\n            yield engine\n        return unused"
    )
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, nested))


def test_transitive_fixture_names_cannot_be_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = tmp_path / "provider.py"
    provider.write_text(SOURCE)
    original = fence._module_path
    monkeypatch.setattr(
        fence,
        "_module_path",
        lambda name: provider if name == "tests.integration.unit_owned_fixture" else original(name),
    )
    source = """
import pytest
from tests.integration.unit_owned_fixture import owned_pg
pytestmark = pytest.mark.integration
@pytest.fixture
async def forwarded(owned_pg):
    yield owned_pg
async def test_forwarded(forwarded):
    assert forwarded
"""
    assert fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))
    overridden = source.replace(
        "pytestmark = pytest.mark.integration",
        "pytestmark = [pytest.mark.integration, pytest.mark.parametrize('owned_pg', [None], indirect=True)]",
    )
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, overridden))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s.replace(
            'async with owned_tls_postgres(tmp_path, owner="unit-source") as pg:', "if True:"
        ),
        lambda s: s.replace(
            "from tests.support.provider_tls_pg import owned_tls_postgres",
            "from tests.support.unverified import owned_tls_postgres",
        ),
        lambda s: s.replace("import owned_tls_postgres", "import owned_tls_postgres as other"),
        lambda s: s.replace("pytestmark = pytest.mark.integration", "pytestmark = pytest.mark.asyncio"),
        lambda s: s.replace("        yield engine", "        return engine"),
        lambda s: s.replace("@pytest.fixture", "@unknown_wrapper\n@pytest.fixture"),
        lambda s: s.replace(
            "async def test_uses_owned(owned_pg):",
            "@pytest.mark.parametrize('owned_pg', [None])\nasync def test_uses_owned(owned_pg):",
        ),
        lambda s: s.replace(
            "pytestmark = pytest.mark.integration",
            "pytestmark = [pytest.mark.integration, pytest.mark.parametrize('owned_pg', [None])]",
        ),
        lambda s: s.replace('owner="unit-source"', 'owner="../foreign"'),
        lambda s: s + "\nowned_tls_postgres = lambda *args, **kwargs: None\n",
        lambda s: s + "\ndef _dsn():\n    return 'postgresql://maezo:maezo@localhost:5433/maezo'\n",
        lambda s: s.replace("    assert owned_pg", "    owned_pg = object()\n    assert owned_pg"),
    ],
)
def test_named_import_claims_cannot_replace_verified_fixture_usage(tmp_path: Path, mutation) -> None:
    changed = mutation(SOURCE)
    assert changed != SOURCE
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, changed))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s.replace('connect_args={"ssl": context}', 'connect_args={"ssl": False}'),
        lambda s: s.replace('connect_args={"ssl": context}', "connect_args={}"),
        lambda s: s.replace(
            'pg.url_for("unit-role", "unit-password")',
            "'postgresql+asyncpg://external.invalid:5432/postgres'",
        ),
        lambda s: s.replace(
            'pg.url_for("unit-role", "unit-password")',
            'pg.url_for("unit-role", "unit-password").set(host="external.invalid")',
        ),
        lambda s: s.replace("        engine =", "        context.check_hostname = False\n        engine ="),
        lambda s: s.replace("        engine =", "        context = None\n        engine ="),
        lambda s: s.replace("        admin =", "        pg = object()\n        admin ="),
        lambda s: s.replace(
            "        admin =", "        external = os.environ.get('MAEZO_TEST_DATABASE_URL')\n        admin ="
        ),
    ],
)
def test_incorrect_transport_or_dsn_fallback_invalidates_owned_classification(
    tmp_path: Path, mutation
) -> None:
    changed = mutation(SOURCE)
    assert changed != SOURCE
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, changed))


def test_indirect_local_factory_tls_drift_is_not_hidden_by_context_usage(tmp_path: Path) -> None:
    source = (
        SOURCE.replace(
            '        engine = create_async_engine(pg.url_for("unit-role", "unit-password"), '
            'connect_args={"ssl": context})',
            "        engine = factory(pg.port, context)",
        )
        + """
def factory(port, context):
    url = URL.create("postgresql+asyncpg", username="unit", password="unit",
                     host="127.0.0.1", port=port, database="postgres")
    return create_async_engine(url, connect_args={"ssl": context})
"""
    )
    assert fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))
    assert not fence._suite_uses_owned_tls_fixture(
        candidate(tmp_path, source.replace('connect_args={"ssl": context}', 'connect_args={"ssl": False}'))
    )


@pytest.mark.parametrize(
    "uri",
    [
        'f"postgresql+asyncpg://unit:unit@external.invalid:5432/postgres@127.0.0.1:{pg.port}"',
        'f"postgresql+asyncpg://unit@127.0.0.1:{pg.port}@external.invalid:5432/postgres"',
        'f"postgresql+asyncpg://unit:unit@external.invalid:5432/postgres?next=@127.0.0.1:{pg.port}"',
        'f"postgresql+asyncpg://unit:unit@external.invalid:5432/postgres#@127.0.0.1:{pg.port}"',
        'f"rubbish-postgresql+asyncpg://unit:unit@127.0.0.1:{pg.port}/postgres"',
        'f"postgresql+asyncpg://unit:unit@127.0.0.1:5432/postgres@127.0.0.1:{pg.port}"',
        'f"postgresql+asyncpg://unit:unit@127.0.0.1:{pg.port}/postgres"',
    ],
)
def test_interpolated_uri_markers_never_prove_host_or_published_port(tmp_path: Path, uri: str) -> None:
    source = SOURCE.replace('pg.url_for("unit-role", "unit-password")', uri)
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


@pytest.mark.parametrize(
    "expression",
    [
        'pg.url_for("unit-role", "unit-password").set(None, None, None, "external.invalid", 5432)',
        'pg.url_for("unit-role", "unit-password").set(drivername="sqlite")',
        'pg.url_for("unit-role", "unit-password").set(query={"sslmode": "disable"})',
        'pg.url_for("unit-role", "unit-password").set(query={"sslrootcert": "/foreign"})',
        'pg.url_for("unit-role", "unit-password").set(**overrides)',
        'pg.url_for("unit-role", "unit-password", host="external.invalid")',
        'URL.create("mysql", host="127.0.0.1", port=pg.port, database="postgres")',
        'URL.create("postgresql+asyncpg", host="127.0.0.1", port=5432, database="postgres")',
        'URL.create("postgresql+asyncpg", host="external.invalid", port=pg.port, database="postgres")',
    ],
)
def test_url_constructor_or_transform_cannot_switch_endpoint_or_tls_profile(
    tmp_path: Path, expression: str
) -> None:
    source = SOURCE.replace('pg.url_for("unit-role", "unit-password")', expression)
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


def test_keyword_forwarding_cannot_hide_unsafe_local_network_factory(tmp_path: Path) -> None:
    source = (
        SOURCE.replace(
            '        engine = create_async_engine(pg.url_for("unit-role", "unit-password"), '
            'connect_args={"ssl": context})',
            "        engine = factory(port=pg.port, context=context)",
        )
        + """
def factory(*, port, context):
    url = f"postgresql+asyncpg://unit:unit@external.invalid:5432/postgres@127.0.0.1:{port}"
    return create_async_engine(url, connect_args={"ssl": context})
"""
    )
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


def test_url_constructor_name_cannot_be_supplied_by_unverified_module(tmp_path: Path) -> None:
    source = SOURCE.replace(
        'pg.url_for("unit-role", "unit-password")',
        'URL.create("postgresql+asyncpg", host="127.0.0.1", port=pg.port, database="postgres")',
    )
    assert fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))
    changed = source.replace("from sqlalchemy.engine import URL", "from tests.support.unverified import URL")
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, changed))


@pytest.mark.parametrize("wrapper", ["str", "make_url"])
def test_trusted_wrapper_requires_actual_builtin_or_sqlalchemy_binding(tmp_path: Path, wrapper: str) -> None:
    source = SOURCE.replace(
        'pg.url_for("unit-role", "unit-password")', f'{wrapper}(pg.url_for("unit-role", "unit-password"))'
    )
    if wrapper == "make_url":
        source = source.replace(
            "from sqlalchemy.engine import URL", "from sqlalchemy.engine import URL, make_url"
        )
    assert fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


@pytest.mark.parametrize("wrapper", ["str", "make_url"])
@pytest.mark.parametrize(
    "shadow",
    [
        "module_def",
        "module_lambda",
        "local_lambda",
        "context_argument",
        "local_import",
        "module_import",
        "global_assignment",
    ],
)
def test_wrapper_names_cannot_grant_url_provenance_under_shadowing(
    tmp_path: Path, wrapper: str, shadow: str
) -> None:
    source = SOURCE.replace(
        'pg.url_for("unit-role", "unit-password")', f'{wrapper}(pg.url_for("unit-role", "unit-password"))'
    )
    if wrapper == "make_url":
        source = source.replace(
            "from sqlalchemy.engine import URL", "from sqlalchemy.engine import URL, make_url"
        )
    target = '"postgresql+asyncpg://unit:unit@external.invalid:5432/postgres"'
    if shadow == "module_def":
        source += f"\ndef {wrapper}(value):\n    return {target}\n"
    elif shadow == "module_lambda":
        source += f"\n{wrapper} = lambda value: {target}\n"
    elif shadow == "local_lambda":
        source = source.replace(
            "        admin =", f"        {wrapper} = lambda value: {target}\n        admin ="
        )
    elif shadow == "context_argument":
        source = source.replace("async def owned_pg(tmp_path):", f"async def owned_pg(tmp_path, {wrapper}):")
    elif shadow == "local_import":
        source = source.replace(
            "        admin =", f"        from tests.support.unverified import {wrapper}\n        admin ="
        )
    elif shadow == "module_import":
        source += f"\nfrom tests.support.unverified import {wrapper}\n"
    else:
        source += f"\ndef mutate_namespace():\n    global {wrapper}\n    {wrapper} = lambda value: {target}\n"
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


@pytest.mark.parametrize("alias", ["url_alias", "parse_alias"])
def test_unverified_name_alias_is_not_a_supported_wrapper(tmp_path: Path, alias: str) -> None:
    source = SOURCE.replace(
        'pg.url_for("unit-role", "unit-password")', f'{alias}(pg.url_for("unit-role", "unit-password"))'
    )
    source += f'\n{alias} = lambda value: "postgresql+asyncpg://unit:unit@external.invalid:5432/postgres"\n'
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


def test_url_constructor_local_shadow_is_denied_even_with_trusted_module_import(tmp_path: Path) -> None:
    source = SOURCE.replace(
        'pg.url_for("unit-role", "unit-password")',
        'URL.create("postgresql+asyncpg", host="127.0.0.1", port=pg.port, database="postgres")',
    ).replace("async def owned_pg(tmp_path):", "async def owned_pg(tmp_path, URL):")
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


def test_builtin_module_attribute_rebinding_cannot_hide_converter_shadow(tmp_path: Path) -> None:
    source = SOURCE.replace(
        'pg.url_for("unit-role", "unit-password")', 'str(pg.url_for("unit-role", "unit-password"))'
    )
    source += '\nimport builtins as builtin_alias\nbuiltin_alias.str = lambda value: "postgresql+asyncpg://unit:unit@external.invalid:5432/postgres"\n'
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


@pytest.mark.parametrize("wrapper", ["str", "make_url"])
def test_local_builtin_namespace_alias_or_global_mapping_cannot_rebind_converter(
    tmp_path: Path, wrapper: str
) -> None:
    source = SOURCE.replace(
        'pg.url_for("unit-role", "unit-password")', 'str(pg.url_for("unit-role", "unit-password"))'
    )
    local_alias = source.replace(
        "        admin =",
        "        import builtins as local_builtin\n"
        '        local_builtin.str = lambda value: "external.invalid"\n        admin =',
    )
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, local_alias))
    if wrapper == "make_url":
        source = source.replace("str(pg.url_for", "make_url(pg.url_for").replace(
            "from sqlalchemy.engine import URL", "from sqlalchemy.engine import URL, make_url"
        )
    global_map = source + f'\nglobals()["{wrapper}"] = lambda value: "external.invalid"\n'
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, global_map))


def test_local_factory_parameter_cannot_shadow_the_module_network_factory(tmp_path: Path) -> None:
    source = (
        SOURCE.replace(
            '        engine = create_async_engine(pg.url_for("unit-role", "unit-password"), '
            'connect_args={"ssl": context})',
            "        engine = factory(pg.port, context)",
        ).replace("async def owned_pg(tmp_path):", "async def owned_pg(tmp_path, factory):")
        + """
def factory(port, context):
    url = URL.create("postgresql+asyncpg", host="127.0.0.1", port=port, database="postgres")
    return create_async_engine(url, connect_args={"ssl": context})
"""
    )
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


@pytest.mark.parametrize("name", ["create_async_engine", "asyncpg"])
def test_network_client_symbols_require_unshadowed_library_origin(tmp_path: Path, name: str) -> None:
    if name == "create_async_engine":
        source = SOURCE + "\ncreate_async_engine = lambda *args, **kwargs: object()\n"
    else:
        source = SOURCE.replace(
            "from sqlalchemy.ext.asyncio import create_async_engine",
            "import asyncpg\nfrom sqlalchemy.ext.asyncio import create_async_engine",
        )
        source = source.replace(
            'engine = create_async_engine(pg.url_for("unit-role", "unit-password"), '
            'connect_args={"ssl": context})',
            'engine = await asyncpg.connect(pg.url_for("unit-role", "unit-password").'
            'set(drivername="postgresql").render_as_string(hide_password=False), ssl=context)',
        )
        source += "\nasyncpg.connect = lambda *args, **kwargs: object()\n"
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


@pytest.mark.parametrize(
    "mutation",
    [
        "saved = URL.create\nsetattr(URL, 'create', lambda *a, **k: "
        "saved('postgresql+asyncpg', host='external.invalid', port=5432))",
        "mutate = setattr\nmutate(URL, 'create', lambda *a, **k: object())",
        "from builtins import setattr as mutate\nmutate(URL, 'create', lambda *a, **k: object())",
        "import builtins as builtin_alias\nbuiltin_alias.setattr(URL, 'create', lambda *a, **k: object())",
        "import builtins as builtin_alias\nmutate = getattr(builtin_alias, 'setattr')\n"
        "mutate(URL, 'create', lambda *a, **k: object())",
        "delattr(URL, 'create')",
        "remove = delattr\nremove(URL, 'create')",
        "alias = URL\nalias.create = lambda *a, **k: object()",
        "first = URL\nsecond = first\nsecond.create = lambda *a, **k: object()",
        "alias = URL\ndel alias.create",
        "namespace = URL.__dict__\nnamespace['create'] = lambda *a, **k: object()",
        "namespace = vars(URL)\nnamespace['create'] = lambda *a, **k: object()",
        "namespace = URL.__dict__\nnamespace.update({'create': lambda *a, **k: object()})",
        "object.__setattr__(URL, 'create', lambda *a, **k: object())",
        "attribute_name = 'create'\nconstructor = getattr(URL, attribute_name)",
        "mutate = getattr(object, '__setattr__')\nmutate(URL, 'create', lambda *a, **k: object())",
    ],
)
def test_reflection_or_aliases_cannot_mutate_or_recover_trusted_url_constructor(
    tmp_path: Path, mutation: str
) -> None:
    source = (
        SOURCE.replace(
            'pg.url_for("unit-role", "unit-password")',
            'URL.create("postgresql+asyncpg", host=pg.host, port=pg.port, database="postgres")',
        )
        + "\n"
        + mutation
        + "\n"
    )
    # Parse only. Executing these monkeypatches would contaminate shared modules.
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


def test_ordinary_record_attribute_observation_is_not_reflective_url_authority(tmp_path: Path) -> None:
    source = SOURCE.replace(
        "        context =", '        state = getattr(admin, "is_closed", None)\n        context ='
    )
    assert fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


@pytest.mark.parametrize(
    "recovery",
    [
        "lookup = getattr\nsetter = lookup(builtins, 'setattr')\n"
        "setter(URL, 'create', lambda *a, **k: object())",
        "lookup = getattr\napply = lookup(type, '__setattr__')\n"
        "apply(URL, 'create', lambda *a, **k: object())",
        "first = getattr\nsecond = first\nsecond(URL, 'create')",
        "lookup = lambda obj, name: getattr(obj, name)\n"
        "lookup(builtins, 'setattr')(URL, 'create', lambda *a, **k: object())",
        "def lookup(obj, name):\n    return getattr(obj, name)\n"
        "lookup(builtins, 'setattr')(URL, 'create', lambda *a, **k: object())",
        "lookups = [getattr]\nlookups[0](builtins, 'setattr')",
        "lookups = {'getter': getattr}\nlookups['getter'](builtins, 'setattr')",
        "from builtins import getattr as lookup\nlookup(builtins, 'setattr')",
        "lookup = builtins.getattr\nlookup(type, '__setattr__')",
    ],
)
def test_getter_references_or_opaque_wrappers_cannot_recover_mutating_callables(
    tmp_path: Path, recovery: str
) -> None:
    source = (
        SOURCE.replace(
            'pg.url_for("unit-role", "unit-password")',
            'URL.create("postgresql+asyncpg", host=pg.host, port=pg.port, database="postgres")',
        )
        + "\nimport builtins\n"
        + recovery
        + "\n"
    )
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


def test_direct_getter_cannot_become_a_callable_result_or_shadowed_context_symbol(tmp_path: Path) -> None:
    source = SOURCE.replace(
        "        context =", '        observed = getattr(admin, "is_closed", None)\n        context ='
    )
    assert fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))
    called = source.replace("        yield engine", "        observed()\n        yield engine")
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, called))
    shadowed = source.replace("async def owned_pg(tmp_path):", "async def owned_pg(tmp_path, getattr):")
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, shadowed))


def test_direct_record_field_dictionary_remains_supported(tmp_path: Path) -> None:
    source = SOURCE.replace(
        "        context =",
        '        fields = dict(**{name: getattr(admin, name) for name in ("closed", "status")})\n'
        "        context =",
    )
    assert fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


@pytest.mark.parametrize(
    "construction",
    [
        "import operator\nsetter = operator.attrgetter('setattr')(builtins)\n"
        "setter(URL, 'create', lambda *a, **k: object())",
        "from operator import attrgetter\nsetter = attrgetter('setattr')(builtins)\n"
        "setter(URL, 'create', lambda *a, **k: object())",
        "calls = {'setter': lambda *a, **k: object()}\ncalls['setter'](URL, 'create', None)",
        "(lambda value: value)(URL)",
        "constructor_alias = URL.create\n"
        "constructor_alias('postgresql+asyncpg', host='external.invalid', port=5432)",
        "from sqlalchemy.engine import unknown_factory\nunknown_factory(URL)",
    ],
)
def test_only_proven_callable_construction_and_invocation_forms_are_admitted(
    tmp_path: Path, construction: str
) -> None:
    source = SOURCE + "\n" + construction + "\n"
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


@pytest.mark.parametrize(
    "construction",
    [
        '        stash = {}\n        field = "__set" + "attr__"\n'
        '        stash["setter"] = getattr(type, field)\n'
        '        stash["setter"](URL, "create", lambda *a, **k: object())\n',
        '        field = "sql" + "state"\n        observed = getattr(admin, field)\n',
        '        names = ("__setattr__",)\n        values = {key: getattr(type, key) for key in names}\n',
        '        constructor_alias = URL.create\n        constructor_alias("postgresql+asyncpg")\n',
    ],
)
def test_dynamic_getter_field_or_subscript_callable_has_no_construction_proof(
    tmp_path: Path, construction: str
) -> None:
    source = SOURCE.replace("        admin =", construction + "        admin =")
    assert not fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


def test_verified_record_fields_are_data_observation_not_callable_inference(tmp_path: Path) -> None:
    source = SOURCE.replace(
        "        context =",
        '        original = getattr(admin, "orig", None)\n'
        '        state = getattr(original, "sqlstate", None)\n'
        '        fields = {field: getattr(admin, field) for field in ("closed", "status")}\n'
        "        context =",
    )
    assert fence._suite_uses_owned_tls_fixture(candidate(tmp_path, source))


def test_name_only_cannot_pardon_a_real_owned_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    target = next(path for path in fence._iter_live_suites() if fence._claims_owned_tls_fixture(path))
    relative = str(target.relative_to(fence._REPO_ROOT))
    monkeypatch.setitem(
        fence._NAME_ONLY, relative, "This false exclusion must never admit owned infrastructure"
    )
    with pytest.raises(AssertionError, match="no name-only"):
        fence.test_every_live_suite_exposes_a_resolver_or_is_explicitly_excluded()


def test_helper_metadata_or_always_accept_inspector_cannot_manufacture_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert fence._owned_helper_contract_is_verified()
    monkeypatch.setattr(provider_tls_pg, "SELF_PROVISIONED_TLS_PG_CONTRACT_VERSION", "unverified")
    assert not fence._owned_helper_contract_is_verified()
    monkeypatch.undo()
    original = provider_tls_pg.validate_inspected_container

    def always_accept(inspected, **expected):
        return provider_tls_pg.PublishedPostgres(
            "b" * 64,
            expected["expected_name"],
            expected["expected_owner"],
            expected["expected_token"],
            "127.0.0.1",
            55441,
            "postgres:16",
        )

    always_accept.__module__ = provider_tls_pg.__name__
    monkeypatch.setattr(provider_tls_pg, "validate_inspected_container", always_accept)
    assert not fence._owned_helper_contract_is_verified()
    assert original is not always_accept


def test_helper_actual_tls_context_and_url_are_bound_to_inspected_descriptor(tmp_path: Path) -> None:
    context = provider_tls_pg.server_certificate(tmp_path)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.cert_store_stats()["x509"] >= 1
    assert (tmp_path / "server.key").stat().st_mode & 0o777 == 0o600
    published = provider_tls_pg.PublishedPostgres(
        "b" * 64,
        provider_tls_pg.CONTAINER_NAME_PREFIX + "a" * 32,
        "unit-source",
        "a" * 32,
        "127.0.0.1",
        55441,
        "postgres:16",
    )
    fixture = provider_tls_pg.OwnedTlsPostgres(
        published, tmp_path / "server.crt", context, "UNIT-password", None
    )
    url = fixture.url_for("unit-role", "UNIT-role-password")
    assert (url.host, url.port, url.drivername, url.database) == (
        "127.0.0.1",
        55441,
        "postgresql+asyncpg",
        "postgres",
    )
    assert "UNIT-password" not in repr(fixture)
    assert fence._pure_owned_inspect_contract(provider_tls_pg)
