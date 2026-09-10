"""PUBLIC_SYNTHETIC repaired-contract cross-language fixture; no engine authority."""

import copy
import hashlib
import hmac
import json

import pytest

from maezo.gateway.human.models import AuthoritativeTask, CurrentTaskAuthority
from maezo.gateway.human.read_profile import NativeContinuity, parse_model, wire
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import ProfileError, canonicalize, strict_loads

FIXTURE = json.loads(r"""
{"authority":{"authority_revision":"7","consent_scopes":[],"evidence_digest":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","evidence_revision":"3","form_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","form_key":"auth_decisao","form_version":"1","issuer":"https://issuer.example","membership_revision":"5","permitted_operations":[],"principal_ref":"human-1","process_definition_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","process_definition_id":"auth:1:id","process_definition_key":"SP-OP-AUTH-001","process_definition_version":"1","read_permitted":true,"subject":"subject-1","task_definition_key":"UT_AnaliseMedicoAuditor","task_id":"task-1","task_revision":"2","tenant":"test-tenant","valid_until":"2026-09-09T21:00:10.000004Z"},"authority_continuity":{"claims":{"algorithm":"HMAC-SHA256","authority_digest":"6f2cab9c7a39d5192d42fa67e0cb20a0a7f1b05b099a0ad7e1802a6da5b8cf3a","binding":{"database_incarnation":"incarnation-1","engine_name":"engine-1","read_context_id":"YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE","read_deployment_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","read_deployment_ref":"read-release-1","requester":{"issuer":"read-workload","key_id":"read-key-1","peer_spki_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","public_key_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},"runtime_admission_generation":"8","scope":{"environment":"test","tenant":"test-tenant","workload_ref":"read-gateway"}},"catalog_state_digest":"2426df8be113f0c4a90954060bf726e994713cc4e2a70c326c177ec53f621ceb","ceilings":[{"kind":"catalog","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"a12d253b16e1a7611f8b9af1c95cd3547a53e88b23a5b444b3e5e616b9d67c4b","source_ref":"catalog","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"classification","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"b5dd84117b853956109496f2f3aeebbcb9482298a4edf0ff6a2aefb089908836","source_ref":"classification","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"evidence","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"9fdaf154415b3d01ae5f43c57ce34c52ce4a4ecfe9d57ae32f2c3c000309af0d","source_ref":"evidence","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"membership","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"7d3857a6306a0ce3b0faa59f8613327b0b44d4dc42935b5c243d72300041543f","source_ref":"membership-1","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"native_admission","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"c405102635e5caae8be8d833511c663a9333a5d0694a52edcf7d859ab2d975a4","source_ref":"native_admission","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"native_key","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"abab0fb5d034b68ed3be658452db05970ee8671f617a1a0f0151f3904382f050","source_ref":"native_key","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"request_envelope","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"5527e8ed5adb5a067930405b96948a603be49dbdcd16ea54aa4cafccfb7b3848","source_ref":"request_envelope","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"requester_key","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"cb0a3eaadfb9657f1dd6d31ea365d5e15092f6c1913cbecca4f8505d9ddafacd","source_ref":"requester_key","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"resource","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"5153bbe79a96d8931c8ca46605a76fbfc783d7b8e5a080c2ce1f208398970a5b","source_ref":"resource","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"}],"issued_at":"2026-09-09T21:00:01.000002Z","key_id":"public-test-key","native_authority_state_digest":"e5ef74e6cc8aa01e49600f3d42e10b528ff5a66de761af3e4427974a584fcb2a","native_task_state_digest":"2def4f568e2fc20aa3fcc3b8902e9dcbf54ae67e07ca168042b8590eb3a2de34","origin_request_digest":"d12e36457732e9661f9e1eeb6504327ac69fc071313d1438fe8727e21298a34e","principal_digest":"71ff4ea588d99f9fe67370b8fc1df1404db3f0ad5ee17dc95757548492e63661","schema":"portal-native-read-continuity.v1","snapshot_at":"2026-09-09T21:00:00.000001Z","snapshot_digest":"3b159e3d627538d9b4a4c2deb652c51cf3794bf0a5c1d10ab67c7f7b39c224a2","stage":"authority","task_continuity_digest":"3341e17fbe7a150b301aeb6aba0f7308d40611475f328fe21d8d37f1cd0f5548","task_digest":"44f4d632d628e69f4639a97de57d3b345a97cebeaeb49de6fb8b7fc9a545fe78","valid_until":"2026-09-09T21:00:10.000004Z"},"mac":"4fa0fb18a76d1373e45bcfe32b41926139e6469c8966c5d31003fcbd75a0b672"},"principal":{"authenticated_at":"2026-09-09T21:00:00.000001Z","issuer":"https://issuer.example","membership_revision":"5","memberships":[{"groups":["medico-auditor"],"membership_ref":"m1","roles":["staff"]}],"principal_ref":"human-1","schema_version":"1","session_ref":"session-1","subject":"subject-1","subject_bindings":[],"tenant":"test-tenant"},"task":{"active":true,"authority_revision":"7","required_consent_scopes":[],"required_roles":["staff"],"required_subject_bindings":[],"snapshot":{"allowed_actions":[],"allowed_inputs":["decisao_auditor","justificativa_clinica","cid10_referencia","fundamentacao_dut"],"assignee_ref":null,"eligible_candidate_groups":["medico-auditor"],"engine_due_at":null,"evidence_digest":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","evidence_revision":"3","form_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","form_key":"auth_decisao","form_source_status":"BPMN_FORMDATA","form_version":"1","process_definition_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","process_definition_id":"auth:1:id","process_definition_key":"SP-OP-AUTH-001","process_definition_version":"1","read_only_evidence":null,"schema_version":"1","snapshot_at":"2026-09-09T21:00:00.000001Z","task_definition_key":"UT_AnaliseMedicoAuditor","task_id":"task-1","task_revision":"2"},"tenant":"test-tenant","valid_until":"2026-09-09T21:00:10.000004Z"},"task_continuity":{"claims":{"algorithm":"HMAC-SHA256","authority_digest":null,"binding":{"database_incarnation":"incarnation-1","engine_name":"engine-1","read_context_id":"YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE","read_deployment_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","read_deployment_ref":"read-release-1","requester":{"issuer":"read-workload","key_id":"read-key-1","peer_spki_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","public_key_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},"runtime_admission_generation":"8","scope":{"environment":"test","tenant":"test-tenant","workload_ref":"read-gateway"}},"catalog_state_digest":"2426df8be113f0c4a90954060bf726e994713cc4e2a70c326c177ec53f621ceb","ceilings":[{"kind":"catalog","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"a12d253b16e1a7611f8b9af1c95cd3547a53e88b23a5b444b3e5e616b9d67c4b","source_ref":"catalog","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"classification","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"b5dd84117b853956109496f2f3aeebbcb9482298a4edf0ff6a2aefb089908836","source_ref":"classification","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"evidence","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"9fdaf154415b3d01ae5f43c57ce34c52ce4a4ecfe9d57ae32f2c3c000309af0d","source_ref":"evidence","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"native_admission","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"c405102635e5caae8be8d833511c663a9333a5d0694a52edcf7d859ab2d975a4","source_ref":"native_admission","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"native_key","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"abab0fb5d034b68ed3be658452db05970ee8671f617a1a0f0151f3904382f050","source_ref":"native_key","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"request_envelope","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"5527e8ed5adb5a067930405b96948a603be49dbdcd16ea54aa4cafccfb7b3848","source_ref":"request_envelope","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"requester_key","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"cb0a3eaadfb9657f1dd6d31ea365d5e15092f6c1913cbecca4f8505d9ddafacd","source_ref":"requester_key","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"resource","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"5153bbe79a96d8931c8ca46605a76fbfc783d7b8e5a080c2ce1f208398970a5b","source_ref":"resource","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"}],"issued_at":"2026-09-09T21:00:00.000001Z","key_id":"public-test-key","native_authority_state_digest":null,"native_task_state_digest":"2def4f568e2fc20aa3fcc3b8902e9dcbf54ae67e07ca168042b8590eb3a2de34","origin_request_digest":"85333775bb4d07c95dfdec95f327d438612c786a466b785419dbdd489a896435","principal_digest":null,"schema":"portal-native-read-continuity.v1","snapshot_at":"2026-09-09T21:00:00.000001Z","snapshot_digest":"3b159e3d627538d9b4a4c2deb652c51cf3794bf0a5c1d10ab67c7f7b39c224a2","stage":"task","task_continuity_digest":null,"task_digest":"44f4d632d628e69f4639a97de57d3b345a97cebeaeb49de6fb8b7fc9a545fe78","valid_until":"2026-09-09T21:00:10.000004Z"},"mac":"fbff6a4040f5e264268819bf14186837741e4db6b654a7975268a020274547d4"}}
""")


def fixture():
    return copy.deepcopy(FIXTURE)


@pytest.mark.parametrize(
    "key,model",
    [
        ("task", AuthoritativeTask),
        ("authority", CurrentTaskAuthority),
        ("principal", HumanPrincipal),
        ("task_continuity", NativeContinuity),
        ("authority_continuity", NativeContinuity),
    ],
)
def test_exact_typed_wire(key, model):
    value = fixture()[key]
    assert wire(parse_model(model, value)) == value


@pytest.mark.parametrize("key", ["task_continuity", "authority_continuity"])
def test_native_mac_cross_language_reference(key):
    value = fixture()[key]
    c = value["claims"]
    expected = hmac.new(
        bytes(range(32)),
        b"maezo/portal-native-read-continuity/v1/" + c["stage"].encode() + b"\0" + canonicalize(c),
        hashlib.sha256,
    ).hexdigest()
    assert expected == value["mac"]


@pytest.mark.parametrize(
    "raw",
    [b'{"x":1}', b'{"x":"a","x":"b"}', b'{"x":"\xff"}', b'{"x":NaN}', b'{"x":"\\ud800"}', b"[]" * 40000],
)
def test_number_free_strict_parser(raw):
    with pytest.raises(ProfileError):
        strict_loads(raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_revision", "01"),
        ("task_revision", 1),
        ("snapshot_at", "2026-09-09T21:00:00Z"),
        ("snapshot_at", "2026-09-09T21:00:00.000001+00:00"),
        ("unexpected", "x"),
    ],
)
def test_task_rejects_coercion_or_extra(field, value):
    task = fixture()["task"]
    task["snapshot"][field] = value
    with pytest.raises(ProfileError):
        parse_model(AuthoritativeTask, task)
