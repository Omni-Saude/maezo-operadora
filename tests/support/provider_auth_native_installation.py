"""TestOnly native installation admission and real lifecycle composition.

This module never manufactures preflight approval, professional authority, native
receipts or ACKs. Deployment owner supplies measured resources and two separately
issued gate envelopes. Pure unit verification does not qualify E04.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import sqlite3
import stat
import subprocess
import zipfile
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator

from maezo.gateway.human.auth_profile import Definition, Scope
from maezo.gateway.human.read_profile import digest, parse_model, wire
from maezo.gateway.intake.native_authority import (
    AuthLifecycleConfiguration,
    AuthProductionComposition,
    NativeDatabaseBinding,
    ProtectedDatabaseTlsRoot,
    _database_tls,
    load_auth_lifecycle,
    protected_bytes,
)
from maezo.portal.engine.profile import canonicalize, strict_loads
from tests.support.provider_auth_native import NativeTestMaterials
from tests.support.provider_tls_pg import OwnedTlsPostgres

DESIGN_DIGEST = "cdd2561e51616346512d39ce6dac57192500a478b5325f12d281f89b696dfa79"
# Exact admitted R2b schemas; embedded to avoid private evidence paths as runtime dependencies.
_DEFINITIONS = {
    "Artifact": {
        "additionalProperties": False,
        "properties": {
            "media_type": {
                "enum": [
                    "application/json",
                    "application/java-archive",
                    "application/xml",
                    "application/pem-certificate-chain",
                    "application/octet-stream",
                    "text/plain",
                ]
            },
            "path": {"maxLength": 4096, "pattern": "^/[^\\x00\\r\\n]+$", "type": "string"},
            "ref": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
        },
        "required": ["ref", "path", "sha256", "media_type"],
        "type": "object",
    },
    "Binding": {
        "additionalProperties": False,
        "properties": {
            "database_name": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "database_oid": {"pattern": "^[1-9][0-9]*$", "type": "string"},
            "owner_role": {"const": "maezo_native_schema_owner"},
            "runtime_role": {"const": "cibseven_app"},
            "schema": {"const": "human-auth-native-database.v1"},
            "schema_name": {"const": "maezo_native"},
            "schema_oid": {"pattern": "^[1-9][0-9]*$", "type": "string"},
        },
        "required": [
            "schema",
            "database_name",
            "database_oid",
            "schema_name",
            "schema_oid",
            "owner_role",
            "runtime_role",
        ],
        "type": "object",
    },
    "BootSwitch": {
        "additionalProperties": False,
        "properties": {
            "candidate_sha": {"pattern": "^[a-f0-9]{40}$", "type": "string"},
            "database_binding_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "definition": {"$ref": "#/$defs/Definition"},
            "evidence": {
                "items": {"$ref": "#/$defs/Artifact"},
                "maxItems": 32,
                "minItems": 1,
                "type": "array",
                "uniqueItems": True,
            },
            "native_jar_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "observed_at": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "outcome": {"const": "PREPARED_ADMITTED_ONLY"},
            "phase_a_descriptor_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "phase_a_image_id": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "phase_b_descriptor_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "phase_b_executed": {"const": False},
            "phase_b_image_id": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "schema": {"const": "provider-auth-testonly-boot-switch.v2"},
            "scope": {"$ref": "#/$defs/Scope"},
            "support_jar_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "valid_until": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
        },
        "required": [
            "schema",
            "candidate_sha",
            "scope",
            "definition",
            "phase_a_image_id",
            "phase_a_descriptor_sha256",
            "phase_b_image_id",
            "phase_b_descriptor_sha256",
            "native_jar_sha256",
            "support_jar_sha256",
            "database_binding_sha256",
            "observed_at",
            "valid_until",
            "outcome",
            "phase_b_executed",
            "evidence",
        ],
        "type": "object",
    },
    "DatabaseTlsRoot": {
        "additionalProperties": False,
        "properties": {
            "path": {"maxLength": 4096, "pattern": "^/[^\\x00\\r\\n]+$", "type": "string"},
            "sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
        },
        "required": ["path", "sha256"],
        "type": "object",
    },
    "Definition": {
        "additionalProperties": False,
        "properties": {
            "definition_digest": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "definition_id": {
                "maxLength": 512,
                "minLength": 1,
                "pattern": "^[^\\x00-\\x1f\\x7f/?#]+$",
                "type": "string",
            },
            "deployment_id": {
                "maxLength": 512,
                "minLength": 1,
                "pattern": "^[^\\x00-\\x1f\\x7f/?#]+$",
                "type": "string",
            },
            "input_profile": {"const": "portal-auth-intake.v1"},
            "process_key": {"const": "SP-OP-AUTH-001"},
            "profile_digest": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
        },
        "required": [
            "process_key",
            "definition_id",
            "definition_digest",
            "deployment_id",
            "input_profile",
            "profile_digest",
        ],
        "type": "object",
    },
    "GateEnvelope": {
        "additionalProperties": False,
        "properties": {
            "algorithm": {"const": "Ed25519"},
            "issuer": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "key_id": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "record": {"$ref": "#/$defs/GateRecord"},
            "record_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "schema": {"const": "provider-auth-testonly-preflight-envelope.v2"},
            "signature": {"pattern": "^[A-Za-z0-9_-]{86}$", "type": "string"},
        },
        "required": ["schema", "algorithm", "issuer", "key_id", "record", "record_sha256", "signature"],
        "type": "object",
    },
    "GateRecord": {
        "additionalProperties": False,
        "properties": {
            "allowed_scope": {"const": "INSTALL_PROVIDER_AUTH_TESTONLY_PRELIMINARY"},
            "authority_registry_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "binding_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "candidate_sha": {"pattern": "^[a-f0-9]{40}$", "type": "string"},
            "checker_candidate_sha": {"pattern": "^[a-f0-9]{40}$", "type": "string"},
            "checker_source_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "database_tls_root_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "definition": {"$ref": "#/$defs/Definition"},
            "evidence": {
                "items": {"$ref": "#/$defs/Artifact"},
                "maxItems": 64,
                "minItems": 1,
                "type": "array",
                "uniqueItems": True,
            },
            "gate_ref": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "issued_at": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "issuer": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "measurements_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "not_functional_e04": {"const": True},
            "not_production_authority": {"const": True},
            "schema": {"const": "provider-auth-testonly-preflight-gate.v2"},
            "scope": {"$ref": "#/$defs/Scope"},
            "source_bundle_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "support_bundle_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "valid_until": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "verdict": {"const": "PASS_FOR_TESTONLY_INSTALLATION"},
            "verifier_id": {"maxLength": 256, "pattern": "^/root/[a-z0-9_]+$", "type": "string"},
            "verifier_role": {"enum": ["architecture", "security"]},
        },
        "required": [
            "schema",
            "gate_ref",
            "verifier_id",
            "verifier_role",
            "issuer",
            "checker_candidate_sha",
            "checker_source_sha256",
            "allowed_scope",
            "verdict",
            "candidate_sha",
            "source_bundle_sha256",
            "support_bundle_sha256",
            "measurements_sha256",
            "scope",
            "definition",
            "binding_sha256",
            "database_tls_root_sha256",
            "authority_registry_sha256",
            "evidence",
            "issued_at",
            "valid_until",
            "not_functional_e04",
            "not_production_authority",
        ],
        "type": "object",
    },
    "InstallerInput": {
        "additionalProperties": False,
        "properties": {
            "approved_qualification": {"$ref": "#/$defs/Artifact"},
            "candidate_sha": {"pattern": "^[a-f0-9]{40}$", "type": "string"},
            "database_tls_root": {"$ref": "#/$defs/DatabaseTlsRoot"},
            "identity_binding": {"$ref": "#/$defs/Artifact"},
            "native_materials_manifest": {"$ref": "#/$defs/Artifact"},
            "owned_resource_inventory": {"$ref": "#/$defs/Artifact"},
            "owner": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "protected_binding": {"$ref": "#/$defs/Artifact"},
            "role_secret_files": {
                "additionalProperties": False,
                "properties": {
                    "identity_control": {"$ref": "#/$defs/Artifact"},
                    "identity_reader": {"$ref": "#/$defs/Artifact"},
                    "identity_receipts": {"$ref": "#/$defs/Artifact"},
                    "identity_writer": {"$ref": "#/$defs/Artifact"},
                    "native_owner": {"$ref": "#/$defs/Artifact"},
                    "native_reader": {"$ref": "#/$defs/Artifact"},
                    "protected_runtime": {"$ref": "#/$defs/Artifact"},
                },
                "required": [
                    "identity_reader",
                    "identity_writer",
                    "identity_control",
                    "identity_receipts",
                    "native_reader",
                    "protected_runtime",
                    "native_owner",
                ],
                "type": "object",
            },
            "schema": {"const": "provider-auth-native-test-installation.v2"},
            "scope": {"$ref": "#/$defs/Scope"},
            "source_bundle_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "source_pins": {
                "items": {"$ref": "#/$defs/SourcePin"},
                "maxItems": 1024,
                "minItems": 1,
                "type": "array",
                "uniqueItems": True,
            },
            "support_bundle_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "valid_until": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
        },
        "required": [
            "schema",
            "owner",
            "candidate_sha",
            "source_pins",
            "source_bundle_sha256",
            "support_bundle_sha256",
            "scope",
            "identity_binding",
            "protected_binding",
            "role_secret_files",
            "database_tls_root",
            "native_materials_manifest",
            "approved_qualification",
            "owned_resource_inventory",
            "valid_until",
        ],
        "type": "object",
    },
    "InstallerOutput": {
        "additionalProperties": False,
        "properties": {
            "activation_admitted": {"const": False},
            "candidate_sha": {"pattern": "^[a-f0-9]{40}$", "type": "string"},
            "database_tls_root": {"$ref": "#/$defs/DatabaseTlsRoot"},
            "definition": {"$ref": "#/$defs/Definition"},
            "e04_final_qualified": {"const": False},
            "image_id": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "installed_at": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "lifecycle_configuration": {"$ref": "#/$defs/Artifact"},
            "native_database_binding": {"$ref": "#/$defs/Artifact"},
            "native_jar": {"$ref": "#/$defs/Artifact"},
            "origin": {"pattern": "^https://(localhost|127\\.0\\.0\\.1):[1-9][0-9]{0,4}$", "type": "string"},
            "owned_resources": {
                "items": {"$ref": "#/$defs/Resource"},
                "maxItems": 12,
                "minItems": 3,
                "type": "array",
                "uniqueItems": True,
            },
            "owner": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "owner_action_results": {
                "items": {"$ref": "#/$defs/Artifact"},
                "maxItems": 8,
                "minItems": 4,
                "type": "array",
                "uniqueItems": True,
            },
            "preliminary_qualification": {"$ref": "#/$defs/Artifact"},
            "schema": {"const": "provider-auth-native-test-installation-result.v2"},
            "scope": {"$ref": "#/$defs/Scope"},
            "support_jar": {"$ref": "#/$defs/Artifact"},
            "valid_until": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
        },
        "required": [
            "schema",
            "owner",
            "candidate_sha",
            "scope",
            "definition",
            "native_jar",
            "support_jar",
            "image_id",
            "origin",
            "native_database_binding",
            "lifecycle_configuration",
            "database_tls_root",
            "preliminary_qualification",
            "owner_action_results",
            "owned_resources",
            "installed_at",
            "valid_until",
            "e04_final_qualified",
            "activation_admitted",
        ],
        "type": "object",
    },
    "Measurements": {
        "additionalProperties": False,
        "properties": {
            "authorization_enabled": {"const": True},
            "binding": {"$ref": "#/$defs/Binding"},
            "boot_switch": {"$ref": "#/$defs/Artifact"},
            "candidate_sha": {"pattern": "^[a-f0-9]{40}$", "type": "string"},
            "cibseven_version": {"const": "2.1.0"},
            "client_ca_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "clock_database": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "clock_engine": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "database_catalog_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "database_tls_root": {"$ref": "#/$defs/DatabaseTlsRoot"},
            "definition": {"$ref": "#/$defs/Definition"},
            "descriptor_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "execution_record": {"$ref": "#/$defs/Artifact"},
            "image_id": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "maven_compiler_plugin": {"const": "3.16.0"},
            "native_jar": {"$ref": "#/$defs/Artifact"},
            "observed_at": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "owned_resources": {
                "items": {"$ref": "#/$defs/Resource"},
                "maxItems": 12,
                "minItems": 3,
                "type": "array",
                "uniqueItems": True,
            },
            "pom": {"$ref": "#/$defs/SourcePin"},
            "profile_artifact": {"$ref": "#/$defs/Artifact"},
            "retrieved_xml": {"$ref": "#/$defs/Artifact"},
            "role_observations": {
                "items": {"$ref": "#/$defs/RoleObservation"},
                "maxItems": 32,
                "minItems": 3,
                "type": "array",
                "uniqueItems": True,
            },
            "route_probe_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "schema": {"const": "provider-auth-testonly-preflight-measurements.v2"},
            "schema_update": {"const": "false"},
            "scope": {"$ref": "#/$defs/Scope"},
            "server_peer_spki_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "source_bundle_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "source_xml": {"$ref": "#/$defs/Artifact"},
            "support_bundle_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "support_jar": {"$ref": "#/$defs/Artifact"},
            "tenant_check_enabled": {"const": True},
            "tls_probe_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "valid_until": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
        },
        "required": [
            "schema",
            "candidate_sha",
            "source_bundle_sha256",
            "support_bundle_sha256",
            "scope",
            "binding",
            "definition",
            "source_xml",
            "retrieved_xml",
            "profile_artifact",
            "native_jar",
            "support_jar",
            "pom",
            "maven_compiler_plugin",
            "cibseven_version",
            "image_id",
            "descriptor_sha256",
            "database_tls_root",
            "client_ca_sha256",
            "server_peer_spki_sha256",
            "authorization_enabled",
            "tenant_check_enabled",
            "schema_update",
            "role_observations",
            "database_catalog_sha256",
            "tls_probe_sha256",
            "route_probe_sha256",
            "clock_database",
            "clock_engine",
            "observed_at",
            "valid_until",
            "owned_resources",
            "execution_record",
            "boot_switch",
        ],
        "type": "object",
    },
    "OwnerActionInput": {
        "additionalProperties": False,
        "properties": {
            "action": {"enum": ["install", "designate", "revoke", "readback"]},
            "binding": {"$ref": "#/$defs/Binding"},
            "candidate_sha": {"pattern": "^[a-f0-9]{40}$", "type": "string"},
            "designation": {"anyOf": [{"$ref": "#/$defs/Artifact"}, {"type": "null"}]},
            "expected_installation_revision": {"pattern": "^[1-9][0-9]*$", "type": "string"},
            "issued_at": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "owner": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "owner_jdbc_config": {"$ref": "#/$defs/Artifact"},
            "preliminary_qualification": {"$ref": "#/$defs/Artifact"},
            "revoked_key_id": {
                "anyOf": [
                    {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
                    {"type": "null"},
                ]
            },
            "schema": {"const": "provider-auth-testonly-owner-action.v2"},
            "scope": {"$ref": "#/$defs/Scope"},
            "valid_until": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
        },
        "required": [
            "schema",
            "action",
            "owner",
            "candidate_sha",
            "scope",
            "binding",
            "preliminary_qualification",
            "owner_jdbc_config",
            "designation",
            "revoked_key_id",
            "expected_installation_revision",
            "issued_at",
            "valid_until",
        ],
        "type": "object",
    },
    "OwnerActionResult": {
        "additionalProperties": False,
        "properties": {
            "action": {"enum": ["install", "designate", "revoke", "readback"]},
            "binding_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "candidate_sha": {"pattern": "^[a-f0-9]{40}$", "type": "string"},
            "catalog_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "committed_at": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "database_observed_at": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "designation_sha256": {
                "anyOf": [{"pattern": "^[a-f0-9]{64}$", "type": "string"}, {"type": "null"}]
            },
            "installation_binding_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "installation_qualification_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "installation_revision": {"pattern": "^[1-9][0-9]*$", "type": "string"},
            "outcome": {"const": "COMMITTED_AND_READ_BACK"},
            "preliminary_qualification_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "read_back_at": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "request_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "revoked_key_id": {
                "anyOf": [
                    {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
                    {"type": "null"},
                ]
            },
            "schema": {"const": "provider-auth-testonly-owner-action-result.v2"},
            "scope": {"$ref": "#/$defs/Scope"},
        },
        "required": [
            "schema",
            "action",
            "request_sha256",
            "candidate_sha",
            "scope",
            "binding_sha256",
            "preliminary_qualification_sha256",
            "outcome",
            "installation_revision",
            "installation_binding_sha256",
            "installation_qualification_sha256",
            "designation_sha256",
            "revoked_key_id",
            "catalog_sha256",
            "database_observed_at",
            "committed_at",
            "read_back_at",
        ],
        "type": "object",
    },
    "PreliminaryQualification": {
        "additionalProperties": False,
        "properties": {
            "architecture_receipt": {"$ref": "#/$defs/Artifact"},
            "authority_registry": {"$ref": "#/$defs/Artifact"},
            "measurements": {"$ref": "#/$defs/Artifact"},
            "qualification": {
                "additionalProperties": False,
                "properties": {
                    "cutover_ref": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
                    "definition": {"$ref": "#/$defs/Definition"},
                    "native_code_digest": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
                    "review_receipt_ref": {
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$",
                        "type": "string",
                    },
                    "runtime_qualification_ref": {
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$",
                        "type": "string",
                    },
                    "schema": {"const": "human-auth-installation-qualification.v1"},
                    "source_freeze_contract_digest": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
                    "valid_until": {
                        "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                        "type": "string",
                    },
                },
                "required": [
                    "schema",
                    "definition",
                    "native_code_digest",
                    "source_freeze_contract_digest",
                    "cutover_ref",
                    "review_receipt_ref",
                    "runtime_qualification_ref",
                    "valid_until",
                ],
                "type": "object",
            },
            "readback_record": {"$ref": "#/$defs/Artifact"},
            "schema": {"const": "provider-auth-testonly-preliminary-qualification.v2"},
            "security_receipt": {"$ref": "#/$defs/Artifact"},
        },
        "required": [
            "schema",
            "authority_registry",
            "measurements",
            "architecture_receipt",
            "security_receipt",
            "readback_record",
            "qualification",
        ],
        "type": "object",
    },
    "ProfileSource": {
        "additionalProperties": False,
        "properties": {
            "definition_shape_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "input_profile": {"const": "portal-auth-intake.v1"},
            "java_auth_models": {"$ref": "#/$defs/SourcePin"},
            "publication_query_shape_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "publication_shape_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "python_auth_profile": {"$ref": "#/$defs/SourcePin"},
            "schema": {"const": "human-auth-canonical-input-profile-source.v1"},
            "scope_shape_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
        },
        "required": [
            "schema",
            "input_profile",
            "python_auth_profile",
            "java_auth_models",
            "definition_shape_sha256",
            "scope_shape_sha256",
            "publication_shape_sha256",
            "publication_query_shape_sha256",
        ],
        "type": "object",
    },
    "ProtectedJavaConfiguration": {
        "additionalProperties": False,
        "properties": {
            "alias": {"const": "provider-native-result"},
            "audience": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "configuration_digest": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "issuer": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "key_id": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "max_lifetime_seconds": {"pattern": "^[1-9][0-9]*$", "type": "string"},
            "preliminary_qualification_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "schema": {"const": "provider-auth-testonly-java-composition.v2"},
            "scope": {"$ref": "#/$defs/Scope"},
            "signing_password_file": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", "type": "string"},
            "signing_pkcs12_file": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", "type": "string"},
            "signing_spki_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "source_candidate_sha": {"pattern": "^[a-f0-9]{40}$", "type": "string"},
            "timeout_seconds": {"pattern": "^[1-9][0-9]*$", "type": "string"},
        },
        "required": [
            "schema",
            "scope",
            "audience",
            "max_lifetime_seconds",
            "timeout_seconds",
            "signing_pkcs12_file",
            "signing_password_file",
            "alias",
            "key_id",
            "issuer",
            "signing_spki_sha256",
            "preliminary_qualification_sha256",
            "source_candidate_sha",
            "configuration_digest",
        ],
        "type": "object",
    },
    "RegistryEntry": {
        "additionalProperties": False,
        "properties": {
            "allowed_scope": {"const": "INSTALL_PROVIDER_AUTH_TESTONLY_PRELIMINARY"},
            "issuer": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "key_id": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "not_after": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "not_before": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "public_key_spki_base64": {"maxLength": 256, "minLength": 1, "type": "string"},
            "purpose": {"const": "provider-auth-testonly-preflight"},
            "verifier_id": {"maxLength": 256, "pattern": "^/root/[a-z0-9_]+$", "type": "string"},
            "verifier_role": {"enum": ["architecture", "security"]},
        },
        "required": [
            "verifier_id",
            "verifier_role",
            "issuer",
            "key_id",
            "public_key_spki_base64",
            "purpose",
            "allowed_scope",
            "not_before",
            "not_after",
        ],
        "type": "object",
    },
    "Resource": {
        "additionalProperties": False,
        "properties": {
            "id": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "inspect_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "kind": {"enum": ["container", "network", "image"]},
            "owner": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "source_candidate_sha": {"pattern": "^[a-f0-9]{40}$", "type": "string"},
            "token": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
        },
        "required": ["kind", "id", "owner", "token", "source_candidate_sha", "inspect_sha256"],
        "type": "object",
    },
    "RoleObservation": {
        "additionalProperties": False,
        "properties": {
            "bypassrls": {"const": False},
            "createdb": {"const": False},
            "createrole": {"const": False},
            "membership_digest": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "replication": {"const": False},
            "role": {"pattern": "^[a-z_][a-z0-9_]{0,62}$", "type": "string"},
            "schema_acl_digest": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "superuser": {"const": False},
            "table_column_acl_digest": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
        },
        "required": [
            "role",
            "superuser",
            "replication",
            "bypassrls",
            "createdb",
            "createrole",
            "membership_digest",
            "schema_acl_digest",
            "table_column_acl_digest",
        ],
        "type": "object",
    },
    "Scope": {
        "additionalProperties": False,
        "properties": {
            "database_incarnation": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "engine_name": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "environment": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "installation_ref": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "installation_revision": {"pattern": "^[1-9][0-9]*$", "type": "string"},
            "tenant": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
        },
        "required": [
            "tenant",
            "environment",
            "engine_name",
            "database_incarnation",
            "installation_ref",
            "installation_revision",
        ],
        "type": "object",
    },
    "SourcePin": {
        "additionalProperties": False,
        "properties": {
            "git_blob_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "repository_path": {"pattern": "^[A-Za-z0-9][A-Za-z0-9_./-]{0,2047}$", "type": "string"},
        },
        "required": ["repository_path", "git_blob_sha256"],
        "type": "object",
    },
    "VerifierRegistry": {
        "additionalProperties": False,
        "properties": {
            "candidate_sha": {"pattern": "^[a-f0-9]{40}$", "type": "string"},
            "decision_record": {"$ref": "#/$defs/Artifact"},
            "entries": {
                "items": {"$ref": "#/$defs/RegistryEntry"},
                "maxItems": 2,
                "minItems": 2,
                "type": "array",
                "uniqueItems": True,
            },
            "helper_author_id": {"maxLength": 256, "pattern": "^/root/[a-z0-9_]+$", "type": "string"},
            "installer_author_id": {"maxLength": 256, "pattern": "^/root/[a-z0-9_]+$", "type": "string"},
            "installer_support_sha256": {"pattern": "^[a-f0-9]{64}$", "type": "string"},
            "issued_at": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
            "registry_ref": {"pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$", "type": "string"},
            "repair_author_id": {"maxLength": 256, "pattern": "^/root/[a-z0-9_]+$", "type": "string"},
            "schema": {"const": "provider-auth-testonly-verifier-registry.v2"},
            "valid_until": {
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
                "type": "string",
            },
        },
        "required": [
            "schema",
            "registry_ref",
            "decision_record",
            "candidate_sha",
            "installer_author_id",
            "helper_author_id",
            "repair_author_id",
            "installer_support_sha256",
            "entries",
            "issued_at",
            "valid_until",
        ],
        "type": "object",
    },
}


class NativeInstallationError(RuntimeError):
    """A prerequisite or binding is absent; no secret-bearing diagnostics."""


# Successor profiles are carried identically in both signed GateRecord.evidence
# arrays. They do not rewrite any of the frozen R2b definitions above. Settings
# omit only fields derived AFTER the preliminary is signed, avoiding a SHA cycle.
_JAVA_DERIVED = {"preliminary_qualification_sha256", "source_candidate_sha", "configuration_digest"}
_FROZEN_SCHEMA_DATA: dict[str, Any] = dict(_DEFINITIONS)
_SUPPLEMENTS = {
    "OwnerClasspathManifest": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema": {"const": "provider-auth-testonly-owner-classpath-manifest.v1"},
            "candidate_sha": _FROZEN_SCHEMA_DATA["GateRecord"]["properties"]["candidate_sha"],
            "ordered_artifacts": {
                "type": "array",
                "minItems": 3,
                "maxItems": 128,
                "uniqueItems": True,
                "items": {"$ref": "#/$defs/Artifact"},
            },
        },
        "required": ["schema", "candidate_sha", "ordered_artifacts"],
    },
    "OwnerRuntimeManifest": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema": {"const": "provider-auth-testonly-owner-runtime-manifest.v1"},
            "candidate_sha": _FROZEN_SCHEMA_DATA["GateRecord"]["properties"]["candidate_sha"],
            "scope": {"$ref": "#/$defs/Scope"},
            "java_settings": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    k: v
                    for k, v in _FROZEN_SCHEMA_DATA["ProtectedJavaConfiguration"]["properties"].items()
                    if k not in _JAVA_DERIVED
                },
                "required": [
                    k
                    for k in _FROZEN_SCHEMA_DATA["ProtectedJavaConfiguration"]["required"]
                    if k not in _JAVA_DERIVED
                ],
            },
            "signing_pkcs12": {"$ref": "#/$defs/Artifact"},
            "signing_password": {"$ref": "#/$defs/Artifact"},
            "owner_jdbc_config": {"$ref": "#/$defs/Artifact"},
            "action_specs": {
                "type": "array",
                "minItems": 5,
                "maxItems": 16,
                "uniqueItems": True,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "action": _FROZEN_SCHEMA_DATA["OwnerActionInput"]["properties"]["action"],
                        "designation": _FROZEN_SCHEMA_DATA["OwnerActionInput"]["properties"]["designation"],
                        "revoked_key_id": _FROZEN_SCHEMA_DATA["OwnerActionInput"]["properties"][
                            "revoked_key_id"
                        ],
                    },
                    "required": ["action", "designation", "revoked_key_id"],
                },
            },
            "native_database_binding": {"$ref": "#/$defs/Artifact"},
            "lifecycle_configuration": {"$ref": "#/$defs/Artifact"},
            "origin": _FROZEN_SCHEMA_DATA["InstallerOutput"]["properties"]["origin"],
            "owned_resources": _FROZEN_SCHEMA_DATA["InstallerOutput"]["properties"]["owned_resources"],
            "issued_at": _FROZEN_SCHEMA_DATA["OwnerActionInput"]["properties"]["issued_at"],
            "valid_until": _FROZEN_SCHEMA_DATA["OwnerActionInput"]["properties"]["valid_until"],
        },
        "required": [
            "schema",
            "candidate_sha",
            "scope",
            "java_settings",
            "signing_pkcs12",
            "signing_password",
            "owner_jdbc_config",
            "action_specs",
            "native_database_binding",
            "lifecycle_configuration",
            "origin",
            "owned_resources",
            "issued_at",
            "valid_until",
        ],
    },
}


def validate_supplement(kind: str, record: Any) -> dict[str, Any]:
    if not isinstance(record, dict) or kind not in _SUPPLEMENTS:
        raise NativeInstallationError("Unknown successor admission profile")
    schema = {"$ref": "#/$defs/" + kind, "$defs": _DEFINITIONS | _SUPPLEMENTS}
    if not Draft202012Validator(schema).is_valid(record):
        raise NativeInstallationError("Closed successor admission profile refused")
    return record


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def validate_record(kind: str, record: Any) -> dict[str, Any]:
    if not isinstance(record, dict) or kind not in _DEFINITIONS:
        raise NativeInstallationError("Unknown closed installation record")
    validator = Draft202012Validator({"$ref": "#/$defs/" + kind, "$defs": _DEFINITIONS})
    if not validator.is_valid(record):
        raise NativeInstallationError("Closed installation record refused")
    return record


def _read(ref: Mapping[str, Any], *, canonical: bool = True) -> Any:
    validate_record("Artifact", ref)
    raw = _artifact_bytes(ref)
    value = strict_loads(raw)
    if canonical and canonicalize(value) != raw:
        raise NativeInstallationError("Installation artifact is not canonical")
    return value


def _time(value: str) -> datetime:
    try:
        now = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise NativeInstallationError("Installation clock refused") from None
    if now.tzinfo != UTC:
        raise NativeInstallationError("Installation clock is not UTC")
    return now


def _artifact_bytes(ref: Mapping[str, Any]) -> bytes:
    validate_record("Artifact", ref)
    path = Path(ref["path"])
    limit = 67108864 if ref["media_type"] == "application/java-archive" else 4194304
    if not path.is_absolute():
        raise NativeInstallationError("Artifact path is not absolute")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid not in (0, os.geteuid())
                or stat.S_IMODE(metadata.st_mode) & 0o022
                or not 0 < metadata.st_size <= limit
            ):
                raise NativeInstallationError("Artifact custody refused")
            with os.fdopen(os.dup(fd), "rb") as stream:
                raw = stream.read(limit + 1)
            if len(raw) != metadata.st_size or _hash(raw) != ref["sha256"]:
                raise NativeInstallationError("Artifact bytes differ from independent pin")
            return raw
        finally:
            os.close(fd)
    except OSError:
        raise NativeInstallationError("Artifact unavailable") from None


@dataclass(frozen=True)
class TrustedPreflightDispatch:
    """Owner-selected independent pins; never constructed from the input being admitted."""

    candidate_sha: str
    registry_sha256: str
    source_bundle_sha256: str
    support_bundle_sha256: str
    evidence_database: Path
    measurements_sha256: str
    consumer_source_sha256: str
    expected_scope: Scope
    expected_definition: Definition
    owner_classpath_manifest: Mapping[str, Any]
    owner_runtime_manifest: Mapping[str, Any]
    # ROOT pins actual post-signature request bytes; each semantic target must
    # also be independently admitted by the runtime manifest in BOTH receipts.
    runtime_action_inputs: tuple[Mapping[str, Any], ...]


def verify_gate_signature(envelope: dict[str, Any], entry: dict[str, Any]) -> None:
    """Verify a closed gate envelope with an independently selected registry entry."""
    validate_record("GateEnvelope", envelope)
    validate_record("RegistryEntry", entry)
    record = envelope["record"]
    if (
        record["verifier_id"],
        record["verifier_role"],
        record["issuer"],
        envelope["issuer"],
        envelope["key_id"],
    ) != (
        entry["verifier_id"],
        entry["verifier_role"],
        entry["issuer"],
        entry["issuer"],
        entry["key_id"],
    ) or envelope["record_sha256"] != digest(record):
        raise NativeInstallationError("Independent gate signer or record differs")
    try:
        key = serialization.load_der_public_key(
            base64.b64decode(entry["public_key_spki_base64"], validate=True)
        )
        if not isinstance(key, Ed25519PublicKey):
            raise NativeInstallationError("Preflight signer algorithm differs")
        signature = base64.urlsafe_b64decode(envelope["signature"] + "==")
        if base64.urlsafe_b64encode(signature).decode().rstrip("=") != envelope["signature"]:
            raise NativeInstallationError("Preflight signature is not canonical")
        key.verify(signature, canonicalize({k: v for k, v in envelope.items() if k != "signature"}))
    except Exception:
        raise NativeInstallationError("Independent preflight signature refused") from None


def verify_preliminary_qualification(
    artifact: Mapping[str, Any], *, dispatch: TrustedPreflightDispatch, now: datetime | None = None
) -> dict[str, Any]:
    """Verify real independent envelopes, immutable evidence and SQLite readback."""
    now = datetime.now(UTC) if now is None else now
    preliminary = validate_record("PreliminaryQualification", _read(artifact))
    registry_ref = preliminary["authority_registry"]
    if registry_ref["sha256"] != dispatch.registry_sha256:
        raise NativeInstallationError("Untrusted preflight registry")
    registry = validate_record("VerifierRegistry", _read(registry_ref))
    _artifact_bytes(registry["decision_record"])
    measurements = validate_record("Measurements", _read(preliminary["measurements"]))
    if (
        preliminary["measurements"]["sha256"] != dispatch.measurements_sha256
        or registry["candidate_sha"] != dispatch.candidate_sha
        or measurements["candidate_sha"] != dispatch.candidate_sha
        or registry["installer_support_sha256"] != dispatch.support_bundle_sha256
        or measurements["source_bundle_sha256"] != dispatch.source_bundle_sha256
        or measurements["support_bundle_sha256"] != dispatch.support_bundle_sha256
        or measurements["scope"] != wire(dispatch.expected_scope)
        or measurements["definition"] != wire(dispatch.expected_definition)
        or not measurements["scope"]["environment"].startswith("TestOnly-")
    ):
        raise NativeInstallationError("Preflight candidate/source/scope differs")
    authors = {registry[k] for k in ("installer_author_id", "helper_author_id", "repair_author_id")}
    entries = registry["entries"]
    if (
        {e["verifier_role"] for e in entries} != {"architecture", "security"}
        or len({e["verifier_id"] for e in entries}) != 2
        or any(e["verifier_id"] in authors for e in entries)
        or len({e["key_id"] for e in entries}) != 2
        or len({e["public_key_spki_base64"] for e in entries}) != 2
    ):
        raise NativeInstallationError("Preflight verifier independence refused")
    for clock in (measurements["observed_at"], measurements["clock_database"], measurements["clock_engine"]):
        if abs((_time(clock) - _time(measurements["observed_at"])).total_seconds()) > 5:
            raise NativeInstallationError("Preflight clock skew refused")
    for ref in (
        measurements["source_xml"],
        measurements["retrieved_xml"],
        measurements["profile_artifact"],
        measurements["native_jar"],
        measurements["support_jar"],
        measurements["execution_record"],
        measurements["boot_switch"],
    ):
        _artifact_bytes(ref)
    if (
        measurements["source_xml"]["sha256"] != measurements["retrieved_xml"]["sha256"]
        or measurements["retrieved_xml"]["sha256"] != dispatch.expected_definition.definition_digest
        or measurements["profile_artifact"]["sha256"] != dispatch.expected_definition.profile_digest
    ):
        raise NativeInstallationError("Actual XML/profile does not match the qualified definition")
    boot = validate_record("BootSwitch", _read(measurements["boot_switch"]))
    if (
        boot["candidate_sha"],
        boot["scope"],
        boot["definition"],
        boot["database_binding_sha256"],
        boot["native_jar_sha256"],
        boot["support_jar_sha256"],
        boot["phase_a_image_id"],
        boot["phase_a_descriptor_sha256"],
    ) != (
        dispatch.candidate_sha,
        measurements["scope"],
        measurements["definition"],
        digest(measurements["binding"]),
        measurements["native_jar"]["sha256"],
        measurements["support_jar"]["sha256"],
        measurements["image_id"],
        measurements["descriptor_sha256"],
    ):
        raise NativeInstallationError("Prepared boot switch differs from measured resources")
    for ref in boot["evidence"]:
        _artifact_bytes(ref)
    root = ProtectedDatabaseTlsRoot.model_validate(measurements["database_tls_root"], strict=False)
    _database_tls(root)
    readback = _read(preliminary["readback_record"])
    if (
        not isinstance(readback, dict)
        or set(readback) != {"schema", "records"}
        or readback["schema"] != "provider-auth-testonly-preflight-readback.v2"
    ):
        raise NativeInstallationError("Independent readback shape refused")
    if len(readback["records"]) != 2:
        raise NativeInstallationError("Independent readback cardinality refused")
    db_path = dispatch.evidence_database
    if not db_path.is_absolute() or db_path.is_symlink() or not db_path.is_file():
        raise NativeInstallationError("Real independent evidence database missing")
    records = []
    with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as db:
        for role, ref_name in (("architecture", "architecture_receipt"), ("security", "security_receipt")):
            ref = preliminary[ref_name]
            envelope = validate_record("GateEnvelope", _read(ref))
            record = envelope["record"]
            entry = next(e for e in entries if e["verifier_role"] == role)
            if (
                record["verifier_role"],
                record["verifier_id"],
                record["issuer"],
                envelope["issuer"],
                envelope["key_id"],
            ) != (role, entry["verifier_id"], entry["issuer"], entry["issuer"], entry["key_id"]):
                raise NativeInstallationError("Preflight issuer/verifier substituted")
            if envelope["record_sha256"] != digest(record):
                raise NativeInstallationError("Preflight record digest differs")
            verify_gate_signature(envelope, entry)
            expected = (
                dispatch.candidate_sha,
                dispatch.source_bundle_sha256,
                dispatch.support_bundle_sha256,
                dispatch.measurements_sha256,
                wire(dispatch.expected_scope),
                wire(dispatch.expected_definition),
                digest(measurements["binding"]),
                measurements["database_tls_root"]["sha256"],
                dispatch.registry_sha256,
            )
            actual = tuple(
                record[k]
                for k in (
                    "candidate_sha",
                    "source_bundle_sha256",
                    "support_bundle_sha256",
                    "measurements_sha256",
                    "scope",
                    "definition",
                    "binding_sha256",
                    "database_tls_root_sha256",
                    "authority_registry_sha256",
                )
            )
            if actual != expected:
                raise NativeInstallationError("Independent preflight binding differs")
            if (
                record["checker_candidate_sha"] != dispatch.candidate_sha
                or record["checker_source_sha256"] != dispatch.consumer_source_sha256
            ):
                raise NativeInstallationError("Measured admission consumer source differs")
            issued, until = _time(record["issued_at"]), _time(record["valid_until"])
            if (
                issued > now + timedelta(seconds=5)
                or until <= issued
                or until - issued > timedelta(minutes=15)
            ):
                raise NativeInstallationError("Independent preflight lifetime refused")
            for source in (registry, entry, measurements, boot, record):
                start = source.get("not_before", source.get("issued_at", source.get("observed_at")))
                end = source.get("not_after", source.get("valid_until"))
                if not isinstance(start, str) or not isinstance(end, str):
                    raise NativeInstallationError("Preflight lifetime metadata absent")
                if now < _time(start) - timedelta(seconds=5) or now >= _time(end) or until > _time(end):
                    raise NativeInstallationError("Independent preflight expired or ceiling differs")
            for item in record["evidence"]:
                _artifact_bytes(item)
            rows = db.execute(
                "SELECT verifier,verdict,artifact_sha256,evidence_path FROM gateways WHERE id=?",
                (record["gate_ref"],),
            ).fetchall()
            if rows != [(record["verifier_id"], record["verdict"], ref["sha256"], ref["path"])]:
                raise NativeInstallationError("Real preflight receipt record absent or conflicting")
            expected_readback = {
                "gate_ref": record["gate_ref"],
                "verifier_id": record["verifier_id"],
                "issuer": record["issuer"],
                "verdict": record["verdict"],
                "artifact_sha256": ref["sha256"],
                "evidence_path": ref["path"],
            }
            if expected_readback not in readback["records"]:
                raise NativeInstallationError("Independent issuer readback differs")
            records.append(record)
    qualification = preliminary["qualification"]
    if (
        qualification["definition"],
        qualification["native_code_digest"],
        qualification["source_freeze_contract_digest"],
        qualification["cutover_ref"],
    ) != (
        measurements["definition"],
        measurements["native_jar"]["sha256"],
        dispatch.source_bundle_sha256,
        measurements["boot_switch"]["ref"],
    ):
        raise NativeInstallationError("Owner qualification derivation differs")
    if (
        qualification["review_receipt_ref"] != preliminary["readback_record"]["ref"]
        or qualification["runtime_qualification_ref"] != preliminary["measurements"]["ref"]
        or now >= _time(qualification["valid_until"])
        or any(_time(qualification["valid_until"]) > _time(r["valid_until"]) for r in records)
    ):
        raise NativeInstallationError("Owner qualification refs or expiry differ")
    manifests = _admitted_profiles(preliminary, dispatch=dispatch)
    _fresh_preflight(preliminary, registry, measurements, boot, records, manifests)
    return preliminary


def _admitted_profiles(
    preliminary: dict[str, Any], *, dispatch: TrustedPreflightDispatch
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Called only after signature/readback admission; never issues a receipt."""
    refs = (dispatch.owner_classpath_manifest, dispatch.owner_runtime_manifest)
    for receipt in ("architecture_receipt", "security_receipt"):
        evidence = _read(preliminary[receipt])["record"]["evidence"]
        if any(sum(item == ref for item in evidence) != 1 for ref in refs):
            raise NativeInstallationError(
                "Both independent receipts must admit the identical successor profiles"
            )
    classpath = validate_supplement("OwnerClasspathManifest", _read(refs[0]))
    runtime = validate_supplement("OwnerRuntimeManifest", _read(refs[1]))
    measurements = _read(preliminary["measurements"])
    boot = validate_record("BootSwitch", _read(measurements["boot_switch"]))
    ordered = classpath["ordered_artifacts"]
    if (
        classpath["candidate_sha"] != dispatch.candidate_sha
        or runtime["candidate_sha"] != dispatch.candidate_sha
        or runtime["scope"] != wire(dispatch.expected_scope)
        or runtime["java_settings"]["scope"] != runtime["scope"]
        or ordered[:2] != [measurements["native_jar"], measurements["support_jar"]]
        or len({ref["path"] for ref in ordered}) != len(ordered)
        or len({ref["sha256"] for ref in ordered}) != len(ordered)
        or len({ref["ref"] for ref in ordered}) != len(ordered)
        or any(ref["media_type"] != "application/java-archive" for ref in ordered)
        or any(r["source_candidate_sha"] != dispatch.candidate_sha for r in runtime["owned_resources"])
        or not any(
            r["kind"] == "image" and r["id"] == boot["phase_b_image_id"] for r in runtime["owned_resources"]
        )
    ):
        raise NativeInstallationError("Successor profile candidate/order/scope/resources differ")
    for ref in ordered:
        _artifact_bytes(ref)
    for name in ("signing_pkcs12", "signing_password", "owner_jdbc_config"):
        _protected_material(runtime[name])
    for name in ("native_database_binding", "lifecycle_configuration"):
        _artifact_bytes(runtime[name])
    for spec in runtime["action_specs"]:
        if spec["designation"] is not None:
            _artifact_bytes(spec["designation"])
    initial = runtime["action_specs"][:5]
    if [spec["action"] for spec in initial] != ["install", "designate", "designate", "designate", "readback"]:
        raise NativeInstallationError("Admitted initial owner actions have an invalid order or purpose")
    return classpath, runtime


def _protected_material(ref: Mapping[str, Any]) -> bytes:
    validate_record("Artifact", ref)
    path = Path(ref["path"])
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir() or parent.resolve() != parent:
        raise NativeInstallationError("Protected material parent differs")
    mode = parent.stat()
    if mode.st_uid not in (0, os.geteuid()) or stat.S_IMODE(mode.st_mode) & 0o077:
        raise NativeInstallationError("Protected material parent custody refused")
    try:
        return protected_bytes(path, ref["sha256"])
    except (OSError, ValueError, RuntimeError):
        raise NativeInstallationError("Protected material custody or independent pin differs") from None


def _fresh_preflight(
    preliminary: dict[str, Any],
    registry: dict[str, Any],
    measurements: dict[str, Any],
    boot: dict[str, Any],
    records: list[dict[str, Any]],
    manifests: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    """Terminal REAL clock after artifact/signature/SQL/CA I/O; `now` cannot freeze it."""
    _database_tls(
        ProtectedDatabaseTlsRoot(
            path=Path(measurements["database_tls_root"]["path"]),
            sha256=measurements["database_tls_root"]["sha256"],
        )
    )
    current = datetime.now(UTC)
    end = _time(preliminary["qualification"]["valid_until"])
    sources = [registry, *registry["entries"], measurements, boot, *records, manifests[1]]
    for source in sources:
        start = source.get("not_before", source.get("issued_at", source.get("observed_at")))
        until = source.get("not_after", source.get("valid_until"))
        if (
            not isinstance(start, str)
            or not isinstance(until, str)
            or current < _time(start) - timedelta(seconds=5)
            or current >= _time(until)
            or end > _time(until)
        ):
            raise NativeInstallationError("Preflight expired after verification I/O or ceiling differs")
    if current >= end:
        raise NativeInstallationError("Owner qualification expired after verification I/O")


def _action_spec(action: dict[str, Any]) -> dict[str, Any]:
    return {k: action[k] for k in ("action", "designation", "revoked_key_id")}


def _verify_action_binding(
    action: dict[str, Any],
    preliminary: dict[str, Any],
    dispatch: TrustedPreflightDispatch,
    runtime: dict[str, Any],
    *,
    now: datetime,
) -> None:
    measurements = _read(preliminary["measurements"])
    if (
        action["candidate_sha"] != dispatch.candidate_sha
        or action["scope"] != wire(dispatch.expected_scope)
        or action["binding"] != measurements["binding"]
        or action["owner_jdbc_config"] != runtime["owner_jdbc_config"]
        or action["expected_installation_revision"] != action["scope"]["installation_revision"]
        or _action_spec(action) not in runtime["action_specs"]
        or not action["owner"].startswith("provider-")
    ):
        raise NativeInstallationError("Owner action is not an independently admitted semantic target")
    issued, until = _time(action["issued_at"]), _time(action["valid_until"])
    if (
        issued > now + timedelta(seconds=5)
        or now >= until
        or until <= issued
        or until - issued > timedelta(minutes=15)
        or until > min(_time(preliminary["qualification"]["valid_until"]), _time(runtime["valid_until"]))
    ):
        raise NativeInstallationError("Owner action deadline amplifies the admitted ceiling")
    designation, revoked = action["designation"], action["revoked_key_id"]
    if (
        action["action"] in ("install", "readback")
        and (designation is not None or revoked is not None)
        or action["action"] == "designate"
        and (designation is None or revoked is not None)
        or action["action"] == "revoke"
        and (designation is not None or revoked is None)
    ):
        raise NativeInstallationError("Owner action purpose/target fields differ")


def _verify_owner_readback(
    record: dict[str, Any],
    action: dict[str, Any],
    action_ref: Mapping[str, Any],
    preliminary: dict[str, Any],
    *,
    now: datetime,
) -> None:
    q = preliminary["qualification"]
    if (
        record["request_sha256"] != action_ref["sha256"]
        or record["candidate_sha"] != action["candidate_sha"]
        or record["scope"] != action["scope"]
        or record["binding_sha256"] != digest(action["binding"])
        or record["preliminary_qualification_sha256"] != action["preliminary_qualification"]["sha256"]
        or record["action"] != action["action"]
        or record["installation_revision"] != action["expected_installation_revision"]
        or record["installation_binding_sha256"] != digest(action["binding"])
        or record["installation_qualification_sha256"] != digest(q)
        or record["designation_sha256"]
        != (None if action["designation"] is None else digest(_read(action["designation"])))
        or record["revoked_key_id"] != action["revoked_key_id"]
    ):
        raise NativeInstallationError("Actual owner action row/target readback differs")
    committed, read_back, database = (
        _time(record[k]) for k in ("committed_at", "read_back_at", "database_observed_at")
    )
    if (
        committed > read_back
        or read_back > now + timedelta(seconds=5)
        or committed < _time(action["issued_at"]) - timedelta(seconds=5)
        or abs((read_back - database).total_seconds()) > 5
        or read_back >= min(_time(action["valid_until"]), _time(q["valid_until"]))
        or now >= min(_time(action["valid_until"]), _time(q["valid_until"]))
    ):
        raise NativeInstallationError("Owner commit/readback chronology or fresh deadline differs")


@dataclass(frozen=True, repr=False)
class NativeTestRealm:
    composition: AuthProductionComposition
    scope: Scope
    definition: Definition
    config_path: Path
    config_digest: str
    native_binding: NativeDatabaseBinding
    origin: str
    qualification_ref: Mapping[str, Any]
    owned_resources: tuple[Mapping[str, Any], ...]


def verify_owner_classpath(classpath: tuple[Mapping[str, Any], ...], admitted: dict[str, Any]) -> None:
    """Exact ordered signed inventory, including CIB/JDBC; no self-pinned extras."""
    if list(classpath) != admitted["ordered_artifacts"]:
        raise NativeInstallationError("Complete owner classpath differs from both independent receipts")
    origins: dict[str, int] = {}
    entrypoints = {
        "br/com/maezo/human/ProviderAuthTestOwner.class",
        "br/com/maezo/human/ProviderAuthTestComposition.class",
    }
    for index, ref in enumerate(classpath):
        try:
            with zipfile.ZipFile(io.BytesIO(_artifact_bytes(ref))) as jar:
                if len(jar.namelist()) != len(set(jar.namelist())):
                    raise NativeInstallationError("Owner classpath JAR has duplicate entries")
                for entry in jar.infolist():
                    name = entry.filename
                    if (
                        not name.endswith(".class")
                        or name.endswith("/module-info.class")
                        or name == "module-info.class"
                    ):
                        continue
                    if name.startswith("META-INF/versions/"):
                        pieces = name.split("/", 3)
                        if len(pieces) != 4 or not pieces[2].isdigit():
                            raise NativeInstallationError(
                                "Owner classpath has an invalid multi-release origin"
                            )
                        name = pieces[3]
                    if name in origins and origins[name] != index:
                        raise NativeInstallationError("Owner classpath contains conflicting class origins")
                    origins[name] = index
        except (OSError, zipfile.BadZipFile):
            raise NativeInstallationError("Admitted owner classpath JAR is unavailable") from None
    if any(origins.get(name) != 1 for name in entrypoints):
        raise NativeInstallationError("Test owner entrypoints are not confined to the admitted support JAR")


def verify_installed_bindings(
    output: dict[str, Any],
    native: NativeDatabaseBinding,
    config: AuthLifecycleConfiguration,
    *,
    dispatch: TrustedPreflightDispatch,
    preliminary: dict[str, Any],
    runtime: dict[str, Any],
    expected_identity: Any,
    now: datetime,
) -> None:
    """Pure correspondence check of typed ACTUAL readbacks, not a runtime proof."""
    measurements = _read(preliminary["measurements"])
    boot = validate_record("BootSwitch", _read(measurements["boot_switch"]))
    q = preliminary["qualification"]
    binding = measurements["binding"]
    observed_native = wire(native)
    if (
        native.scope != dispatch.expected_scope
        or config.native.scope != dispatch.expected_scope
        or native.scope.tenant != config.identity.tenant
        or config.native != native
        or config.identity != expected_identity
        or config.client.ca_digest != measurements["client_ca_sha256"]
        or native.installed_binding_digest != digest(binding)
        or native.installed_qualification_digest != digest(q)
        or any(
            observed_native[k] != binding[k]
            for k in ("database_name", "database_oid", "schema_name", "schema_oid", "owner_role")
        )
        or output["scope"] != wire(dispatch.expected_scope)
        or output["definition"] != wire(dispatch.expected_definition)
        or output["candidate_sha"] != dispatch.candidate_sha
        or output["native_jar"] != measurements["native_jar"]
        or output["support_jar"] != measurements["support_jar"]
        or output["image_id"] != boot["phase_b_image_id"]
        or output["origin"] != runtime["origin"]
        or config.client.origin != runtime["origin"]
        or output["owned_resources"] != runtime["owned_resources"]
        or output["native_database_binding"] != runtime["native_database_binding"]
        or output["lifecycle_configuration"] != runtime["lifecycle_configuration"]
        or output["database_tls_root"] != measurements["database_tls_root"]
        or any(resource["owner"] != output["owner"] for resource in runtime["owned_resources"])
    ):
        raise NativeInstallationError(
            "Loaded native/configuration/resources are not the exact admitted realm"
        )
    installed, until = _time(output["installed_at"]), _time(output["valid_until"])
    ceilings = [
        native.valid_until,
        config.identity.valid_until,
        config.protected.valid_until,
        _time(q["valid_until"]),
        _time(runtime["valid_until"]),
        _time(boot["valid_until"]),
    ]
    if installed > now + timedelta(seconds=5) or installed >= until or now >= until or until > min(ceilings):
        raise NativeInstallationError("Loaded realm is expired or amplifies an admitted ceiling")


def verify_installation_action_set(
    output: dict[str, Any],
    *,
    preliminary: dict[str, Any],
    dispatch: TrustedPreflightDispatch,
    runtime: dict[str, Any],
    materials: NativeTestMaterials,
    now: datetime,
) -> None:
    actions = []
    refs = []
    for ref in dispatch.runtime_action_inputs:
        action = validate_record("OwnerActionInput", _read(ref))
        if action["action"] == "revoke":
            continue  # Explicit negative-case revocation is never an initial installation result.
        if action["preliminary_qualification"] != output["preliminary_qualification"]:
            raise NativeInstallationError("Owner request names a different authenticated preliminary")
        _verify_action_binding(action, preliminary, dispatch, runtime, now=now)
        if action["owner"] != output["owner"]:
            raise NativeInstallationError("Owner action belongs to a different resource owner")
        refs.append(ref)
        actions.append(action)
    if (
        len(actions) != 5
        or [a["action"] for a in actions].count("install") != 1
        or [a["action"] for a in actions].count("readback") != 1
        or [a["action"] for a in actions].count("designate") != 3
        or len({ref["sha256"] for ref in refs}) != 5
        or len(output["owner_action_results"]) != 5
        or [_action_spec(action) for action in actions] != runtime["action_specs"][:5]
    ):
        raise NativeInstallationError(
            "Installation requires exactly install, three distinct designations and readback"
        )
    designations = [_read(a["designation"]) for a in actions if a["action"] == "designate"]
    expected = [wire(d) for d in materials.proposed_designations]
    if sorted(digest(d) for d in designations) != sorted(digest(d) for d in expected):
        raise NativeInstallationError(
            "Owner did not designate the exact three purpose-bound real material keys"
        )
    by_request = {ref["sha256"]: (ref, action) for ref, action in zip(refs, actions, strict=True)}
    seen = set()
    catalogs = set()
    completed = {}
    for ref in output["owner_action_results"]:
        result = validate_record("OwnerActionResult", _read(ref))
        request = result["request_sha256"]
        if request not in by_request or request in seen:
            raise NativeInstallationError(
                "Owner result set has an extra, duplicate or missing admitted request"
            )
        seen.add(request)
        action_ref, action = by_request[request]
        _verify_owner_readback(result, action, action_ref, preliminary, now=datetime.now(UTC))
        catalogs.add(result["catalog_sha256"])
        completed[request] = result
    if len(catalogs) != 1 or seen != set(by_request):
        raise NativeInstallationError("Owner catalog/readback set differs between installation actions")
    ordered_results = [completed[ref["sha256"]] for ref in refs]
    if any(
        _time(before["read_back_at"]) > _time(after["committed_at"])
        for before, after in zip(ordered_results[:-1], ordered_results[1:], strict=True)
    ):
        raise NativeInstallationError("Owner initial actions were not committed/read back in admitted order")


def invoke_owner_action(
    action_artifact: Mapping[str, Any],
    *,
    dispatch: TrustedPreflightDispatch,
    classpath: tuple[Mapping[str, Any], ...],
    java_executable: Path,
    result_path: Path,
    pg: OwnedTlsPostgres,
) -> dict[str, Any]:
    """ROOT-only invocation bridge: authenticate admission before the actual Java owner API.

    Only public pins and protected file paths enter argv/environment. Credentials
    remain in the closed owner file. Captured output never enters diagnostics.
    """
    action = validate_record("OwnerActionInput", _read(action_artifact))
    preliminary = verify_preliminary_qualification(action["preliminary_qualification"], dispatch=dispatch)
    measurements = _read(preliminary["measurements"])
    class_manifest, runtime = _admitted_profiles(preliminary, dispatch=dispatch)
    if type(pg) is not OwnedTlsPostgres or action_artifact not in dispatch.runtime_action_inputs:
        raise NativeInstallationError("Owner action scope/binding is not independently admitted")
    _verify_action_binding(action, preliminary, dispatch, runtime, now=datetime.now(UTC))
    verify_owner_classpath(classpath, class_manifest)
    jdbc = strict_loads(_protected_material(action["owner_jdbc_config"]))
    if (
        not isinstance(jdbc, dict)
        or set(jdbc) != {"schema", "url", "user", "password"}
        or jdbc["schema"] != "provider-auth-testonly-owner-jdbc.v2"
    ):
        raise NativeInstallationError("Protected owner credential record is not closed")
    parsed = urlsplit(jdbc["url"].removeprefix("jdbc:"))
    query = parse_qs(parsed.query, strict_parsing=True)
    if (
        parsed.scheme != "postgresql"
        or parsed.hostname != pg.published.host
        or parsed.port != pg.published.port
        or parsed.path != "/" + action["binding"]["database_name"]
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or jdbc["user"] != action["binding"]["owner_role"]
        or query
        != {
            "currentSchema": ["maezo_native,cibseven"],
            "sslmode": ["verify-full"],
            "sslrootcert": [measurements["database_tls_root"]["path"]],
        }
    ):
        raise NativeInstallationError("Owner JDBC target or protected TLS root differs")
    _database_tls(
        ProtectedDatabaseTlsRoot(
            path=Path(measurements["database_tls_root"]["path"]),
            sha256=measurements["database_tls_root"]["sha256"],
        )
    )
    if (
        not java_executable.is_absolute()
        or not java_executable.is_file()
        or not result_path.is_absolute()
        or result_path.exists()
    ):
        raise NativeInstallationError("Owner invocation executable/output custody refused")
    verify_preliminary_qualification(action["preliminary_qualification"], dispatch=dispatch)
    _verify_action_binding(action, preliminary, dispatch, runtime, now=datetime.now(UTC))
    _protected_material(action["owner_jdbc_config"])
    environment = {
        "MAEZO_PROVIDER_TESTONLY_ACTION_SHA256": action_artifact["sha256"],
        "MAEZO_PROVIDER_TESTONLY_VERIFIED_PRELIMINARY_SHA256": action["preliminary_qualification"]["sha256"],
        "MAEZO_PROVIDER_TESTONLY_VERIFIED_RUNTIME_MANIFEST_SHA256": dispatch.owner_runtime_manifest["sha256"],
        "MAEZO_PROVIDER_TESTONLY_RUNTIME_MANIFEST_FILE": dispatch.owner_runtime_manifest["path"],
    }
    budget = min(30.0, (_time(action["valid_until"]) - datetime.now(UTC)).total_seconds())
    if budget <= 0:
        raise NativeInstallationError("Owner action expired before dispatch")
    try:
        result = subprocess.run(
            [
                str(java_executable),
                "-cp",
                os.pathsep.join(ref["path"] for ref in classpath),
                "br.com.maezo.human.ProviderAuthTestOwner",
                action_artifact["path"],
                str(result_path),
            ],
            env=environment,
            capture_output=True,
            timeout=budget,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise NativeInstallationError("Owner action execution unavailable; reconcile before retry") from None
    if result.returncode:
        raise NativeInstallationError("Owner action not confirmed; reconcile before retry")
    record = validate_record("OwnerActionResult", strict_loads(protected_bytes(result_path)))
    verify_preliminary_qualification(action["preliminary_qualification"], dispatch=dispatch)
    _verify_owner_readback(record, action, action_artifact, preliminary, now=datetime.now(UTC))
    return record


@asynccontextmanager
async def installed_provider_auth_native_realm(
    tmp_path: Path,
    *,
    pg: OwnedTlsPostgres,
    identity_composition: Any,
    materials: NativeTestMaterials,
    approved_qualification: Mapping[str, Any],
    database_tls_root: ProtectedDatabaseTlsRoot,
    owner: str,
    dispatch: TrustedPreflightDispatch,
    installation_result: Mapping[str, Any],
) -> AsyncIterator[NativeTestRealm]:
    """Consume owner-installed, independently admitted resources and load the real lifecycle.

    Installation result is an owner-API commit/readback artifact. This context does
    not treat generated material or fixture presence as a successful installation.
    ROOT executes bootstrap/owner utility in its serialized lane before this use.
    """
    if type(pg) is not OwnedTlsPostgres or not owner.startswith("provider-") or not tmp_path.is_absolute():
        raise NativeInstallationError("TestOnly resource custody refused")
    materials.guard()
    preliminary = verify_preliminary_qualification(approved_qualification, dispatch=dispatch)
    _, runtime = _admitted_profiles(preliminary, dispatch=dispatch)
    output = validate_record("InstallerOutput", _read(installation_result))
    if (
        output["owner"] != owner
        or output["candidate_sha"] != dispatch.candidate_sha
        or output["scope"] != wire(dispatch.expected_scope)
        or output["definition"] != wire(dispatch.expected_definition)
        or output["preliminary_qualification"] != approved_qualification
        or output["database_tls_root"]
        != {"path": str(database_tls_root.path), "sha256": database_tls_root.sha256}
    ):
        raise NativeInstallationError("Owner-installed realm differs from admitted inputs")
    if output["database_tls_root"] != _read(preliminary["measurements"])["database_tls_root"]:
        raise NativeInstallationError("Database root is not admitted by both gates")
    verify_installation_action_set(
        output,
        preliminary=preliminary,
        dispatch=dispatch,
        runtime=runtime,
        materials=materials,
        now=datetime.now(UTC),
    )
    native = parse_model(NativeDatabaseBinding, _read(output["native_database_binding"]))
    config_ref = output["lifecycle_configuration"]
    config = AuthLifecycleConfiguration.model_validate_json(_artifact_bytes(config_ref), strict=True)
    if config.client != materials.client_binding(
        origin=output["origin"], audience=config.client.audience
    ) or not any(
        resource["kind"] == "container"
        and resource["id"] == pg.published.container_id
        and resource["owner"] == pg.published.owner
        and resource["token"] == pg.published.token
        for resource in runtime["owned_resources"]
    ):
        raise NativeInstallationError("Protected lifecycle bindings differ")
    result_designation = next(d for d in materials.proposed_designations if d.purpose == "human-auth-result")
    settings = runtime["java_settings"]
    if (
        settings["key_id"] != config.client.native_key_id
        or settings["issuer"] != result_designation.issuer
        or settings["signing_spki_sha256"] != result_designation.peer_spki_sha256
        or result_designation.peer_spki_sha256
        != _read(preliminary["measurements"])["server_peer_spki_sha256"]
        or settings["audience"] != config.client.audience
        or settings["signing_pkcs12_file"] != materials.result_pkcs12.name
        or settings["signing_password_file"] != materials.result_password.name
        or runtime["signing_pkcs12"]["path"] != str(materials.result_pkcs12)
        or runtime["signing_password"]["path"] != str(materials.result_password)
    ):
        raise NativeInstallationError(
            "Actual Java signer material differs from the independently admitted settings"
        )
    verify_installed_bindings(
        output,
        native,
        config,
        dispatch=dispatch,
        preliminary=preliminary,
        runtime=runtime,
        expected_identity=identity_composition.binding,
        now=datetime.now(UTC),
    )
    verify_preliminary_qualification(approved_qualification, dispatch=dispatch)
    composition = load_auth_lifecycle(
        Path(config_ref["path"]),
        tenant=config.identity.tenant,
        identity_writer=identity_composition.writer,
        database_tls_root=database_tls_root,
    )
    try:
        await composition.qualify()
        verify_preliminary_qualification(approved_qualification, dispatch=dispatch)
        materials.guard()
        # Re-read pinned records after awaited qualification; no captured DTO can
        # hide artifact replacement or a deadline crossed by loader/source I/O.
        current_native = parse_model(NativeDatabaseBinding, _read(output["native_database_binding"]))
        current_config = AuthLifecycleConfiguration.model_validate_json(
            _artifact_bytes(config_ref), strict=True
        )
        if validate_record("InstallerOutput", _read(installation_result)) != output:
            raise NativeInstallationError("Owner installation result changed after qualification")
        if composition.config != current_config or composition.native.binding != current_native:
            raise NativeInstallationError("Actual loaded bindings changed after qualification")
        verify_installed_bindings(
            output,
            current_native,
            current_config,
            dispatch=dispatch,
            preliminary=preliminary,
            runtime=runtime,
            expected_identity=identity_composition.binding,
            now=datetime.now(UTC),
        )
        verify_installation_action_set(
            output,
            preliminary=preliminary,
            dispatch=dispatch,
            runtime=runtime,
            materials=materials,
            now=datetime.now(UTC),
        )
        verify_preliminary_qualification(approved_qualification, dispatch=dispatch)
        if datetime.now(UTC) >= _time(output["valid_until"]):
            raise NativeInstallationError("Actual realm expired before yield")
        yield NativeTestRealm(
            composition,
            dispatch.expected_scope,
            dispatch.expected_definition,
            Path(config_ref["path"]),
            config_ref["sha256"],
            native,
            output["origin"],
            approved_qualification,
            tuple(output["owned_resources"]),
        )
    finally:
        await composition.close()
