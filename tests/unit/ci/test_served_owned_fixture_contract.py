"""Adversarial static/pure tests of repository-owned fixture recognition.

Synthetic AST/inspect JSON and a temporary certificate are UNIT evidence only.
No Docker, PostgreSQL, CIB or other service is started by this suite.
"""

from __future__ import annotations

import ast
import ssl
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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


def test_closed_ca_and_legacy_helper_profiles_are_both_constructive() -> None:
    legacy, ca = fence._owned_helper_source_profiles()
    assert fence._owned_helper_source_profile(legacy) == "LEGACY"
    assert fence._owned_helper_source_profile(ca) == "CLOSED_CA"
    actual = ast.parse(Path(provider_tls_pg.__file__).read_text(encoding="utf-8"))
    assert fence._owned_helper_source_profile(actual) == "CLOSED_CA"
    assert fence._owned_helper_contract_is_verified()


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("os.O_NOFOLLOW", "0"),
        ("os.O_EXCL", "0"),
        ("info.st_uid != os.getuid()", "False"),
        ("stat.S_IMODE(info.st_mode) != 448", "False"),
        ("before.st_nlink != 1", "False"),
        ("before.st_uid != os.getuid()", "False"),
        ("hashlib.sha256(raw).hexdigest() != expected_digest", "False"),
        ("material.ca_path != material.directory / 'database-ca.crt'", "False"),
        ("material.server_path != material.directory / 'server.crt'", "False"),
        ("material.key_path != material.directory / 'server.key'", "False"),
        ("material.owner != expected_owner", "False"),
        ("type(material.uid) is not int", "False"),
        ("material.uid != os.getuid()", "False"),
        ("ca_key.public_numbers() == server_key.public_numbers()", "False"),
        ("private_key.public_key().public_numbers() != server_key.public_numbers()", "False"),
        (
            "signer.verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), hashes.SHA256())",
            "pass",
        ),
        (
            "x509.BasicConstraints(ca=True, path_length=0)",
            "x509.BasicConstraints(ca=False, path_length=None)",
        ),
        ("not ca_constraints.critical", "False"),
        ("not ca_usage.critical", "False"),
        ("server.issuer != ca.subject", "False"),
        ("len(ca.extensions) != 2", "False"),
        ("len(server.extensions) != 4", "False"),
        ("x509.DNSName('localhost')", "x509.DNSName('external.invalid')"),
        ("ipaddress.ip_address('127.0.0.1')", "ipaddress.ip_address('0.0.0.0')"),
        ("not material.not_before <= now < material.not_after", "False"),
        ("timedelta(hours=2, minutes=1)", "timedelta(days=365)"),
        ("ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)", "ssl.create_default_context()"),
        ("context.load_verify_locations(cadata=ca_raw.decode('ascii'))", "context.load_default_certs()"),
        ("context.verify_mode != ssl.CERT_REQUIRED", "context.verify_mode != ssl.CERT_NONE"),
        ("not context.check_hostname", "False"),
        ("context.get_ca_certs(binary_form=True) != [ca.public_bytes(serialization.Encoding.DER)]", "False"),
        (
            "context, server_raw, key_raw = _admit_database_material(material, expected_owner=owner)",
            "context, server_raw, key_raw = ssl.create_default_context(), b'foreign-cert', b'foreign-key'",
        ),
        (
            "_private_write(directory_fd, 'server.key', key_raw)",
            "_private_write(directory_fd, 'server.key', material.key_path.read_bytes())",
        ),
        (
            "else _database_server_files(files, tls_server_material, owner=owner)",
            "else server_certificate(files)",
        ),
        ("else tls_server_material.ca_path", "else files / 'server.crt'"),
        ("ssl=context, timeout=2", "ssl=False, timeout=2"),
        (
            "expected_owner=owner, expected_token=token",
            "expected_owner='foreign-owner', expected_token=token",
        ),
        ("(files / 'server.key').unlink(missing_ok=True)", "pass"),
        ("docker('rm', '--force', own_id)", "docker('rm', '--force', name)"),
    ],
)
def test_closed_ca_profile_rejects_trust_scope_file_time_and_cleanup_mutants(
    before: str,
    after: str,
) -> None:
    _, ca = fence._owned_helper_source_profiles()
    source = ast.unparse(ca)
    assert before in source, before
    changed = source.replace(before, after)
    assert changed != source
    assert fence._owned_helper_source_profile(ast.parse(changed)) is None


@pytest.mark.parametrize(
    "extra",
    [
        "context = ssl.create_default_context()",
        "ssl = object()",
        "from tests.support.unverified import _admit_database_material",
        "def _admit_database_material(*args, **kwargs):\n    return ssl.create_default_context(), b'x', b'y'",
        "DatabaseTlsServerMaterial = object",
        "def unknown_effect():\n    return __import__('subprocess').run(['docker', 'start', 'foreign'])",
    ],
)
def test_closed_ca_profile_rejects_extra_bindings_imports_and_effects(extra: str) -> None:
    _, ca = fence._owned_helper_source_profiles()
    source = ast.unparse(ca) + "\n" + extra + "\n"
    assert fence._owned_helper_source_profile(ast.parse(source)) is None


def test_legacy_profile_also_rejects_a_tls_downgrade() -> None:
    legacy, _ = fence._owned_helper_source_profiles()
    source = ast.unparse(legacy)
    changed = source.replace("ssl=context, timeout=2", "ssl=False, timeout=2")
    assert changed != source
    assert fence._owned_helper_source_profile(ast.parse(changed)) is None


def test_closed_ca_pure_context_trusts_only_its_ca(tmp_path: Path) -> None:
    # PKI/SSL evidence only; no container, socket, owner API or business authority.
    material = provider_tls_pg.database_tls_server_material(tmp_path / "pki", owner="unit-ca")
    context = provider_tls_pg.validate_database_tls_server_material(material, expected_owner="unit-ca")
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert len(context.get_ca_certs(binary_form=True)) == 1
    assert material.ca_path != material.server_path
    assert material.key_path.stat().st_mode & 0o777 == 0o600


def test_ca_validator_name_and_forged_module_cannot_manufacture_source_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert fence._owned_helper_contract_is_verified()

    def accept_untrusted_material(*args: object, **kwargs: object) -> tuple[ssl.SSLContext, bytes, bytes]:
        return ssl.create_default_context(), b"foreign-certificate", b"foreign-key"

    accept_untrusted_material.__module__ = provider_tls_pg.__name__
    monkeypatch.setattr(provider_tls_pg, "_admit_database_material", accept_untrusted_material)
    assert not fence._owned_helper_contract_is_verified()


@pytest.mark.parametrize(
    "binding",
    [
        "_admit_database_material",
        "validate_database_tls_server_material",
        "server_certificate",
        "_inspect",
        "docker",
    ],
)
@pytest.mark.parametrize("forgery", ["wrapped_metadata", "source_location", "foreign_globals"])
def test_effective_executable_provenance_rejects_foreign_callbacks(
    binding: str, forgery: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never invoke foreign code: metadata, borrowed bytecode and filename are not proof."""
    import types

    original = getattr(provider_tls_pg, binding)
    assert fence._owned_helper_contract_is_verified()
    if forgery == "wrapped_metadata":

        def foreign(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("Foreign body must never execute during admission")

        foreign.__dict__["__wrapped__"] = original
        foreign.__module__ = provider_tls_pg.__name__
    elif forgery == "source_location":
        # Independent foreign executable deliberately claims the legitimate source location.
        namespace: dict[str, Any] = {}
        exec(
            compile(
                f'def {binding}(*args, **kwargs):\n    return "foreign"\n',
                original.__code__.co_filename,
                "exec",
            ),
            namespace,
        )
        code = namespace[binding].__code__.replace(co_firstlineno=original.__code__.co_firstlineno)
        foreign = types.FunctionType(code, provider_tls_pg.__dict__, binding)
        foreign.__module__ = provider_tls_pg.__name__
    else:
        globals_copy = dict(original.__globals__)
        globals_copy["ssl"] = object()
        foreign = types.FunctionType(
            original.__code__, globals_copy, binding, original.__defaults__, original.__closure__
        )
        foreign.__kwdefaults__ = original.__kwdefaults__
        foreign.__annotations__ = original.__annotations__
        foreign.__module__ = provider_tls_pg.__name__
    monkeypatch.setattr(provider_tls_pg, binding, foreign)
    assert not fence._owned_helper_contract_is_verified()


@pytest.mark.parametrize(
    "binding",
    [
        "OWNER_LABEL",
        "TOKEN_LABEL",
        "CONTRACT_LABEL",
        "CONTAINER_NAME_PREFIX",
        "POSTGRES_IMAGE",
        "_START_COMMAND",
    ],
)
def test_runtime_constants_cannot_diverge_from_admitted_source(
    binding: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_tls_pg, binding, "foreign-constant")
    assert not fence._owned_helper_contract_is_verified()


@pytest.mark.parametrize("binding", ["ssl", "subprocess", "asyncpg", "URL", "asynccontextmanager"])
def test_effective_import_bindings_are_closed(binding: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_tls_pg, binding, object())
    assert not fence._owned_helper_contract_is_verified()


def test_docker_cli_dependency_cannot_be_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    def foreign_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("No CLI may execute during admission")

    monkeypatch.setattr(provider_tls_pg.__dict__["subprocess"], "run", foreign_run)
    assert not fence._owned_helper_contract_is_verified()


def test_only_declared_standard_asynccontextmanager_wrapper_is_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import types

    legitimate = provider_tls_pg.owned_tls_postgres
    assert fence._owned_helper_contract_is_verified()

    def foreign(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("No wrapper may execute during admission")

    foreign.__dict__["__wrapped__"] = legitimate.__dict__["__wrapped__"]
    foreign.__module__ = provider_tls_pg.__name__
    monkeypatch.setattr(provider_tls_pg, "owned_tls_postgres", foreign)
    assert not fence._owned_helper_contract_is_verified()
    monkeypatch.setattr(provider_tls_pg, "owned_tls_postgres", legitimate)

    # Borrow the exact standard wrapper bytecode but alter its executable closure.
    def cell(value: Any) -> types.CellType:
        cells = (lambda: value).__closure__
        assert cells is not None
        return cells[0]

    clone = types.FunctionType(
        legitimate.__code__,
        legitimate.__globals__,
        legitimate.__name__,
        legitimate.__defaults__,
        (cell(foreign),),
    )
    clone.__dict__["__wrapped__"] = legitimate.__dict__["__wrapped__"]
    clone.__module__ = provider_tls_pg.__name__
    clone.__annotations__ = legitimate.__annotations__
    monkeypatch.setattr(provider_tls_pg, "owned_tls_postgres", clone)
    assert not fence._owned_helper_contract_is_verified()


def test_changed_defaults_and_builtin_shadowing_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    actual = provider_tls_pg.owned_tls_postgres.__dict__["__wrapped__"]
    monkeypatch.setattr(actual, "__kwdefaults__", {"tls_server_material": object()})
    assert not fence._owned_helper_contract_is_verified()
    monkeypatch.undo()
    monkeypatch.setattr(provider_tls_pg, "isinstance", lambda *args: True, raising=False)
    assert not fence._owned_helper_contract_is_verified()


@pytest.mark.parametrize("method", ["url_for", "__init__", "__repr__", "__hash__"])
def test_dataclass_effective_methods_are_bound_to_the_finite_production(
    method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def foreign(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("No foreign class method may execute during admission")

    original = getattr(provider_tls_pg.OwnedTlsPostgres, method)
    foreign.__module__ = provider_tls_pg.__name__
    foreign.__dict__["__wrapped__"] = original
    monkeypatch.setattr(provider_tls_pg.OwnedTlsPostgres, method, foreign)
    assert not fence._owned_helper_contract_is_verified()


def test_bool_and_integer_executable_constants_are_not_interchangeable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import types

    original = provider_tls_pg.validate_inspected_container
    assert any(value is True for value in original.__code__.co_consts)
    code = original.__code__.replace(
        co_consts=tuple(1 if value is True else value for value in original.__code__.co_consts)
    )
    replacement = types.FunctionType(code, provider_tls_pg.__dict__, original.__name__)
    replacement.__annotations__ = original.__annotations__
    replacement.__kwdefaults__ = original.__kwdefaults__
    replacement.__module__ = original.__module__
    monkeypatch.setattr(provider_tls_pg, "validate_inspected_container", replacement)
    assert not fence._owned_helper_contract_is_verified()


def test_nested_executable_constants_are_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    original = provider_tls_pg.__dict__["asynccontextmanager"]
    nested = [value for value in original.__code__.co_consts if isinstance(value, types.CodeType)]
    assert nested
    replacement_nested = nested[0].replace(co_consts=(*nested[0].co_consts, "foreign-marker"))
    code = original.__code__.replace(
        co_consts=tuple(
            replacement_nested if value is nested[0] else value for value in original.__code__.co_consts
        )
    )
    # Same imported function object and outer instructions; executable nested body diverges.
    monkeypatch.setattr(original, "__code__", code)
    assert not fence._owned_helper_contract_is_verified()


@pytest.mark.parametrize(
    "target",
    [
        "function_module",
        "function_doc",
        "class_module",
        "class_doc",
        "module_file",
        "module_name",
        "module_doc",
        "module_package",
        "field_name",
        "field_type",
        "field_metadata",
        "dataclass_param",
        "annotations",
        "defaults",
    ],
)
def test_foreign_metadata_rejects_without_any_callbacks(
    target: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class ForeignMetadata:
        def __eq__(self, other: object) -> bool:
            calls.append("eq")
            return True

        def __ne__(self, other: object) -> bool:
            calls.append("ne")
            return False

        def __iter__(self) -> Iterator[Any]:
            calls.append("iter")
            return iter(())

        def __getattr__(self, name: str) -> Any:
            calls.append("getattr")
            return None

        def __fspath__(self) -> str:
            calls.append("fspath")
            return "foreign"

    foreign = ForeignMetadata()
    function = provider_tls_pg._admit_database_material
    cls = provider_tls_pg.DatabaseTlsServerMaterial
    member = cls.__dataclass_fields__["owner"]
    if target == "function_module":
        monkeypatch.setattr(function, "__module__", foreign)
    elif target == "function_doc":
        monkeypatch.setattr(function, "__doc__", foreign)
    elif target == "class_module":
        monkeypatch.setattr(cls, "__module__", foreign)
    elif target == "class_doc":
        monkeypatch.setattr(cls, "__doc__", foreign)
    elif target.startswith("module_"):
        monkeypatch.setattr(provider_tls_pg, "__" + target.removeprefix("module_") + "__", foreign)
    elif target.startswith("field_"):
        monkeypatch.setattr(member, target.removeprefix("field_"), foreign)
    elif target == "dataclass_param":
        monkeypatch.setattr(cls.__dict__["__dataclass_params__"], "frozen", foreign)
    elif target == "annotations":
        monkeypatch.setattr(function, "__annotations__", {"material": foreign})
    elif target == "defaults":
        raw = provider_tls_pg.owned_tls_postgres.__dict__["__wrapped__"]
        monkeypatch.setattr(raw, "__kwdefaults__", {"tls_server_material": foreign})
    else:
        raise AssertionError("Unknown negative fixture")
    assert not fence._owned_helper_contract_is_verified()
    assert calls == []


@pytest.mark.parametrize(
    "slot", ["__new__", "__init__", "load_verify_locations", "verify_mode", "check_hostname"]
)
def test_called_ssl_context_slots_cannot_be_replaced(
    slot: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def foreign(*args: Any, **kwargs: Any) -> None:
        calls.append("called")
        raise AssertionError("Foreign TLS implementation must never execute")

    cls = provider_tls_pg.__dict__["ssl"].SSLContext
    replacement: Any = staticmethod(foreign) if slot == "__new__" else foreign
    if slot in {"verify_mode", "check_hostname"}:
        replacement = property(foreign, foreign)
    monkeypatch.setattr(cls, slot, replacement)
    assert not fence._owned_helper_contract_is_verified()
    assert calls == []


@pytest.mark.parametrize("slot", ["__init__", "subject_name", "sign"])
def test_called_certificate_builder_slots_are_bound(
    slot: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def foreign(*args: Any, **kwargs: Any) -> None:
        calls.append("called")
        raise AssertionError("Foreign certificate implementation must never execute")

    monkeypatch.setattr(provider_tls_pg.__dict__["x509"].CertificateBuilder, slot, foreign)
    assert not fence._owned_helper_contract_is_verified()
    assert calls == []


def test_static_dependency_discovery_does_not_invoke_foreign_module_getattr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def foreign_getattr(name: str) -> Any:
        calls.append(name)
        raise AssertionError("Static proof must not invoke module fallback")

    monkeypatch.setattr(provider_tls_pg.__dict__["ssl"], "__getattr__", foreign_getattr, raising=False)
    monkeypatch.delattr(provider_tls_pg.__dict__["ssl"], "SSLContext")
    assert not fence._owned_helper_contract_is_verified()
    assert calls == []


@pytest.mark.parametrize("target", ["class_module", "field_name", "parameter_frozen"])
def test_metadata_descriptors_are_never_called_during_admission(
    target: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dataclasses

    calls: list[str] = []

    class ForeignDescriptor:
        def __get__(self, instance: Any, owner: Any = None) -> str:
            calls.append("get")
            return "foreign"

    if target == "class_module":
        monkeypatch.setattr(provider_tls_pg.DatabaseTlsServerMaterial, "__module__", ForeignDescriptor())
    elif target == "field_name":
        monkeypatch.setattr(dataclasses.Field, "name", ForeignDescriptor())
    else:
        params = provider_tls_pg.DatabaseTlsServerMaterial.__dict__["__dataclass_params__"]
        monkeypatch.setattr(type(params), "frozen", ForeignDescriptor())
    assert not fence._owned_helper_contract_is_verified()
    assert calls == []


@pytest.mark.parametrize(
    "backing", ["foreign_empty", "foreign_nonempty", "native_empty", "foreign_shared_default"]
)
def test_dataclass_metadata_proxy_requires_declared_default_identity(
    backing: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dataclasses
    from collections.abc import Mapping
    from types import MappingProxyType

    calls: list[str] = []

    class ForeignMapping(Mapping[str, Any]):
        def __len__(self) -> int:
            calls.append("len")
            return 0 if backing == "foreign_empty" else 1

        def __iter__(self) -> Iterator[str]:
            calls.append("iter")
            return iter(()) if backing == "foreign_empty" else iter(("foreign",))

        def __getitem__(self, key: str) -> Any:
            calls.append("getitem")
            raise KeyError(key)

    owner_field = provider_tls_pg.DatabaseTlsServerMaterial.__dataclass_fields__["owner"]
    assert fence._owned_helper_contract_is_verified()
    metadata = MappingProxyType({}) if backing == "native_empty" else MappingProxyType(ForeignMapping())
    monkeypatch.setattr(owner_field, "metadata", metadata)
    if backing == "foreign_shared_default":
        # A regenerated reference must not trust a changed stdlib default either.
        monkeypatch.setattr(dataclasses, "_EMPTY_METADATA", metadata)
    assert not fence._owned_helper_contract_is_verified()
    assert calls == []
