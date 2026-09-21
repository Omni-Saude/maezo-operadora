"""ADR-0054 concrete same-cursor readback from the pinned native installer source.

The source bundle and real installation binding are supplied by the qualified
migration owner. No metadata or native/D receipt is manufactured here.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

from maezo.gateway.native_installation_readback import CATALOGUE_FUNCTION_SQL
from maezo.gateway.native_owner_allocation import source_allocation
from maezo.gateway.native_owner_qualification import NativeObservation
from maezo.gateway.native_owner_store import Cursor, one
from maezo.platform.engine_bootstrap import native_owner_contracts as n
from maezo.platform.engine_bootstrap.controller_storage import (
    canonical,
    digest,
    encode64,
    parse_wire,
    require,
    scalar,
)


class FrozenNativeReadback:
    def __init__(
        self,
        binding_wire: bytes,
        *,
        installer_sha256: str,
        manifest_sha256: str,
        template_sha256: str,
        act_schema_owner_oid: int,
        act_relation_owner_oid: int,
    ) -> None:
        for sha in (installer_sha256, manifest_sha256, template_sha256):
            scalar("Sha256", sha)
        for oid in (act_schema_owner_oid, act_relation_owner_oid):
            scalar("Oid", oid)
        self.binding = parse_wire(binding_wire)
        self.act_schema_owner_oid = act_schema_owner_oid
        self.act_relation_owner_oid = act_relation_owner_oid
        path = Path(__file__).resolve().parents[1] / "install_v2.py"
        require(not path.is_symlink(), "PRECONDITION_MISMATCH")
        source = path.read_bytes()
        require(digest(source) == installer_sha256, "PRECONDITION_MISMATCH")
        spec = importlib.util.spec_from_file_location("native_owner_pinned_renderer", path)
        require(spec is not None, "UNAVAILABLE")
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        # Execute exactly the bytes hashed above, with its real asset path; never
        # reread source after verifying and never load a caller-authored plugin.
        exec(compile(source, str(path), "exec"), module.__dict__)
        manifest_path, template_path = module.MANIFEST, module.SQL
        require(not manifest_path.is_symlink() and not template_path.is_symlink(), "PRECONDITION_MISMATCH")
        manifest_bytes, template = manifest_path.read_bytes(), template_path.read_bytes()
        require(
            digest(manifest_bytes) == manifest_sha256 and digest(template) == template_sha256,
            "PRECONDITION_MISMATCH",
        )
        manifest = json.loads(manifest_bytes)
        require(manifest["sql_template_sha256"] == template_sha256, "PRECONDITION_MISMATCH")
        self.manifest_sha256 = manifest_sha256
        self.function_names = frozenset(row["name"] for row in manifest["functions"])
        self.checks = module.qualification_checks(
            self.binding, manifest_bytes=manifest_bytes, template_bytes=template
        )
        match = re.search(
            rb"CREATE FUNCTION maezo_native_v2\.catalogue_v2\(\).*? AS \$\$(.*?)\$\$;", template, re.S
        )
        require(match is not None, "PRECONDITION_MISMATCH")
        assert match is not None
        self.catalogue_function_sha256 = hashlib.sha256(match[1]).hexdigest()
        require(
            "\\set" not in self.checks
            and "\\if" not in self.checks
            and "CREATE TABLE" not in self.checks
            and "CREATE FUNCTION" not in self.checks
            and "COMMIT;" not in self.checks,
            "PRECONDITION_MISMATCH",
        )

    def validate_request(self, input_wire: bytes) -> None:
        q = n.request(input_wire)
        binding, selector = self.binding, self.binding["d_preparation"]
        require(
            all(
                q["scope"][key] == selector[key]
                for key in ("account", "region", "tenant", "environment", "engine_name")
            ),
            "PRECONDITION_MISMATCH",
        )
        require(
            q["scope"]["database_binding"]["database_incarnation"] == selector["database_incarnation"]
            and digest(canonical(q["scope"]["database_binding"])) == binding["database_binding_sha256"],
            "PRECONDITION_MISMATCH",
        )
        require(
            q["d_preparation_id"] == selector["preparation_id"]
            and q["login_oid"] == binding["runtime_role_oid"]
            and q["login_name"] == binding["runtime_role"],
            "PRECONDITION_MISMATCH",
        )
        require(
            all(
                q[key] == selector[key]
                for key in ("generation_id", "purpose", "generation_core_sha256", "decision_sha256")
            ),
            "PRECONDITION_MISMATCH",
        )

    def observe(self, cursor: Cursor) -> NativeObservation:
        binding = self.binding
        function = one(cursor, CATALOGUE_FUNCTION_SQL)
        require(
            function
            == (
                binding["function_owner_oid"],
                True,
                ["search_path=pg_catalog, pg_temp"],
                "s",
                "jsonb",
                self.catalogue_function_sha256,
            ),
            "PRECONDITION_MISMATCH",
        )
        cursor.execute(self.checks)
        row = one(
            cursor,
            (
                "SELECT pg_catalog.to_jsonb(v) FROM maezo_native_v2.schema_version v "
                "WHERE migration_key='native-acquisition-v2'"
            ),
        )[0]
        require(row["manifest_digest"] == self.manifest_sha256, "PRECONDITION_MISMATCH")
        raw = row["preparation_receipt_bytes"]
        require(type(raw) is str and raw.startswith("\\x"), "PRECONDITION_MISMATCH")
        row = {**row, "preparation_receipt_bytes": encode64(bytes.fromhex(raw[2:]))}
        installed = n.installed_record(row)
        native = one(
            cursor,
            (
                "SELECT pg_catalog.convert_to(maezo_native_v2.catalogue_v2()::text,'UTF8'),"
                "maezo_native_v2.catalogue_v2()"
            ),
        )
        native_bytes = bytes(native[0])
        require(digest(native_bytes) == row["catalogue_digest"], "PRECONDITION_MISMATCH")
        act = one(
            cursor,
            """SELECT n.oid::bigint,n.nspowner::bigint,c.oid::bigint,c.relowner::bigint,
         pg_catalog.pg_has_role(current_user,n.nspowner,'USAGE'),
         pg_catalog.pg_has_role(current_user,c.relowner,'USAGE'),
         (SELECT array_agg(a.attname::text ORDER BY a.attname) FROM pg_catalog.pg_attribute a
           WHERE a.attrelid=c.oid AND NOT a.attisdropped AND a.attnum>0 AND a.attname=ANY(%s::text[]))
         FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid
         WHERE n.nspname=%s AND c.relname='act_ru_ext_task' AND c.relkind='r'""",
            (list(n.ACT_SELECT_COLUMNS), binding["act_schema"]),
        )
        require(
            act[0:2] == (binding["act_schema_oid"], self.act_schema_owner_oid)
            and act[3:6] == (self.act_relation_owner_oid, True, True)
            and act[6] == sorted(n.ACT_SELECT_COLUMNS),
            "AUTH_REFUSED",
        )
        defaults = one(
            cursor,
            """SELECT NOT EXISTS (
         SELECT 1 FROM pg_catalog.pg_default_acl d
         CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) a
         WHERE (d.defaclnamespace=to_regnamespace('maezo_native_v2')
          OR (d.defaclnamespace=0 AND d.defaclrole=ANY(%s::oid[])))
          AND (a.grantee<>d.defaclrole OR a.grantor<>d.defaclrole))""",
            ([binding["schema_owner_oid"], binding["function_owner_oid"]],),
        )
        require(defaults == (True,), "AUTH_REFUSED")
        allocation = source_allocation(
            native[1],
            binding,
            self.function_names,
            act_relation_oid=act[2],
            act_schema_owner_oid=act[1],
            act_relation_owner_oid=act[3],
        )
        return NativeObservation(installed, native_bytes, tuple(allocation))
