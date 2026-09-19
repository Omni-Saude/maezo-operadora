package br.com.maezo.human;
import static br.com.maezo.human.PortalReadModels.*;
import static org.junit.jupiter.api.Assertions.*;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.Test;

/** PUBLIC_SYNTHETIC cross-language vectors. No engine/source/runtime authority. */
class PortalReadProfileTest {
  static final String TASK = """
{"active":true,"authority_revision":"7","required_consent_scopes":[],"required_roles":["staff"],"required_subject_bindings":[],"snapshot":{"allowed_actions":[],"allowed_inputs":["decisao_auditor","justificativa_clinica","cid10_referencia","fundamentacao_dut"],"assignee_ref":null,"eligible_candidate_groups":["medico-auditor"],"engine_due_at":null,"evidence_digest":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","evidence_revision":"3","form_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","form_key":"auth_decisao","form_source_status":"BPMN_FORMDATA","form_version":"1","process_definition_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","process_definition_id":"auth:1:id","process_definition_key":"SP-OP-AUTH-001","process_definition_version":"1","read_only_evidence":null,"schema_version":"1","snapshot_at":"2026-09-09T21:00:00.000001Z","task_definition_key":"UT_AnaliseMedicoAuditor","task_id":"task-1","task_revision":"2"},"tenant":"test-tenant","valid_until":"2026-09-09T21:00:10.000004Z"}
""";
  static final String CT = """
{"claims":{"algorithm":"HMAC-SHA256","authority_digest":null,"binding":{"database_incarnation":"incarnation-1","engine_name":"engine-1","read_context_id":"YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE","read_deployment_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","read_deployment_ref":"read-release-1","requester":{"issuer":"read-workload","key_id":"read-key-1","peer_spki_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","public_key_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},"runtime_admission_generation":"8","scope":{"environment":"test","tenant":"test-tenant","workload_ref":"read-gateway"}},"catalog_state_digest":"2426df8be113f0c4a90954060bf726e994713cc4e2a70c326c177ec53f621ceb","ceilings":[{"kind":"catalog","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"a12d253b16e1a7611f8b9af1c95cd3547a53e88b23a5b444b3e5e616b9d67c4b","source_ref":"catalog","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"classification","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"b5dd84117b853956109496f2f3aeebbcb9482298a4edf0ff6a2aefb089908836","source_ref":"classification","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"evidence","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"9fdaf154415b3d01ae5f43c57ce34c52ce4a4ecfe9d57ae32f2c3c000309af0d","source_ref":"evidence","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"native_admission","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"c405102635e5caae8be8d833511c663a9333a5d0694a52edcf7d859ab2d975a4","source_ref":"native_admission","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"native_key","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"abab0fb5d034b68ed3be658452db05970ee8671f617a1a0f0151f3904382f050","source_ref":"native_key","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"request_envelope","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"5527e8ed5adb5a067930405b96948a603be49dbdcd16ea54aa4cafccfb7b3848","source_ref":"request_envelope","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"requester_key","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"cb0a3eaadfb9657f1dd6d31ea365d5e15092f6c1913cbecca4f8505d9ddafacd","source_ref":"requester_key","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"resource","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"5153bbe79a96d8931c8ca46605a76fbfc783d7b8e5a080c2ce1f208398970a5b","source_ref":"resource","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"}],"issued_at":"2026-09-09T21:00:00.000001Z","key_id":"public-test-key","native_authority_state_digest":null,"native_task_state_digest":"2def4f568e2fc20aa3fcc3b8902e9dcbf54ae67e07ca168042b8590eb3a2de34","origin_request_digest":"85333775bb4d07c95dfdec95f327d438612c786a466b785419dbdd489a896435","principal_digest":null,"schema":"portal-native-read-continuity.v1","snapshot_at":"2026-09-09T21:00:00.000001Z","snapshot_digest":"3b159e3d627538d9b4a4c2deb652c51cf3794bf0a5c1d10ab67c7f7b39c224a2","stage":"task","task_continuity_digest":null,"task_digest":"44f4d632d628e69f4639a97de57d3b345a97cebeaeb49de6fb8b7fc9a545fe78","valid_until":"2026-09-09T21:00:10.000004Z"},"mac":"fbff6a4040f5e264268819bf14186837741e4db6b654a7975268a020274547d4"}
""";
  static final String CA = """
{"claims":{"algorithm":"HMAC-SHA256","authority_digest":"6f2cab9c7a39d5192d42fa67e0cb20a0a7f1b05b099a0ad7e1802a6da5b8cf3a","binding":{"database_incarnation":"incarnation-1","engine_name":"engine-1","read_context_id":"YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE","read_deployment_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","read_deployment_ref":"read-release-1","requester":{"issuer":"read-workload","key_id":"read-key-1","peer_spki_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","public_key_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},"runtime_admission_generation":"8","scope":{"environment":"test","tenant":"test-tenant","workload_ref":"read-gateway"}},"catalog_state_digest":"2426df8be113f0c4a90954060bf726e994713cc4e2a70c326c177ec53f621ceb","ceilings":[{"kind":"catalog","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"a12d253b16e1a7611f8b9af1c95cd3547a53e88b23a5b444b3e5e616b9d67c4b","source_ref":"catalog","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"classification","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"b5dd84117b853956109496f2f3aeebbcb9482298a4edf0ff6a2aefb089908836","source_ref":"classification","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"evidence","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"9fdaf154415b3d01ae5f43c57ce34c52ce4a4ecfe9d57ae32f2c3c000309af0d","source_ref":"evidence","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"membership","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"7d3857a6306a0ce3b0faa59f8613327b0b44d4dc42935b5c243d72300041543f","source_ref":"membership-1","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"native_admission","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"c405102635e5caae8be8d833511c663a9333a5d0694a52edcf7d859ab2d975a4","source_ref":"native_admission","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"native_key","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"abab0fb5d034b68ed3be658452db05970ee8671f617a1a0f0151f3904382f050","source_ref":"native_key","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"request_envelope","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"5527e8ed5adb5a067930405b96948a603be49dbdcd16ea54aa4cafccfb7b3848","source_ref":"request_envelope","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"requester_key","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"cb0a3eaadfb9657f1dd6d31ea365d5e15092f6c1913cbecca4f8505d9ddafacd","source_ref":"requester_key","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"},{"kind":"resource","observed_at":"2026-09-09T21:00:00.000001Z","source_digest":"5153bbe79a96d8931c8ca46605a76fbfc783d7b8e5a080c2ce1f208398970a5b","source_ref":"resource","source_revision":"1","valid_until":"2026-09-09T21:00:10.000004Z"}],"issued_at":"2026-09-09T21:00:01.000002Z","key_id":"public-test-key","native_authority_state_digest":"e5ef74e6cc8aa01e49600f3d42e10b528ff5a66de761af3e4427974a584fcb2a","native_task_state_digest":"2def4f568e2fc20aa3fcc3b8902e9dcbf54ae67e07ca168042b8590eb3a2de34","origin_request_digest":"d12e36457732e9661f9e1eeb6504327ac69fc071313d1438fe8727e21298a34e","principal_digest":"71ff4ea588d99f9fe67370b8fc1df1404db3f0ad5ee17dc95757548492e63661","schema":"portal-native-read-continuity.v1","snapshot_at":"2026-09-09T21:00:00.000001Z","snapshot_digest":"3b159e3d627538d9b4a4c2deb652c51cf3794bf0a5c1d10ab67c7f7b39c224a2","stage":"authority","task_continuity_digest":"3341e17fbe7a150b301aeb6aba0f7308d40611475f328fe21d8d37f1cd0f5548","task_digest":"44f4d632d628e69f4639a97de57d3b345a97cebeaeb49de6fb8b7fc9a545fe78","valid_until":"2026-09-09T21:00:10.000004Z"},"mac":"4fa0fb18a76d1373e45bcfe32b41926139e6469c8966c5d31003fcbd75a0b672"}
""";
  @Test
  void typedFullTaskRoundtrip() {
    var task = validate("task", Jcs.parse(TASK.getBytes(StandardCharsets.UTF_8)));
    assertEquals(TASK.strip(), new String(Jcs.canonical(task), StandardCharsets.UTF_8));
  }
  @Test
  void crossLanguageNativeHmac() {
    byte[] bytes = new byte[32];
    for (int i = 0; i < 32; i++) bytes[i] = (byte) i;
    var key = new PortalReadTrust.NativeKey("public-test-key", "1", "a".repeat(64),
        Instant.parse("2026-09-09T20:00:00Z"), Instant.parse("2026-09-09T22:00:00Z"),
        new SecretKeySpec(bytes, "HmacSHA256"), () -> {});
    for (String raw : List.of(CT, CA)) {
      var receipt = validate("continuity", Jcs.parse(raw.getBytes(StandardCharsets.UTF_8)));
      var c = obj(receipt, "claims");
      assertEquals(receipt.get("mac"),
          HexFormat.of().formatHex(
              key.mac(str(c, "stage"), c, Instant.parse("2026-09-09T21:00:01Z"))));
      var changed = copy(c);
      changed.put("snapshot_at", "2026-09-09T21:00:00.000002Z");
      assertNotEquals(receipt.get("mac"),
          HexFormat.of().formatHex(
              key.mac(str(c, "stage"), changed, Instant.parse("2026-09-09T21:00:01Z"))));
      assertNotEquals(receipt.get("mac"),
          HexFormat.of().formatHex(key.mac(str(c, "stage").equals("task") ? "authority" : "task", c,
              Instant.parse("2026-09-09T21:00:01Z"))));
    }
  }
  @Test
  void strictWireRefusals() {
    for (String raw : List.of("{\"n\":1}", "{\"a\":\"x\",\"a\":\"y\"}", "{\"a\":null,\"n\":1.0}"))
      assertThrows(Rejected.class, () -> Jcs.parse(raw.getBytes(StandardCharsets.UTF_8)));
    var task = map(Jcs.parse(TASK.getBytes(StandardCharsets.UTF_8)));
    obj(task, "snapshot").put("snapshot_at", "2026-09-09T21:00:00Z");
    assertThrows(Rejected.class, () -> validate("task", task));
  }
  @Test
  void unsignedLinkOrderingAndPreparedQueueShape() {
    assertTrue(UTF8.compare("a", "b") < 0);
    assertTrue(PortalReadStore.ELIGIBLE.contains("ORDER BY ID_ COLLATE \"C\" ASC LIMIT ?"));
    assertFalse(PortalReadStore.ELIGIBLE.contains("OFFSET"));
    assertTrue(PortalReadStore.PREFLIGHT.contains("resource IS NULL"));
  }

  record Verification(PortalReadCommand command, Map<String, Object> task,
      Map<String, Object> proof, PortalReadTrust.NativeKey key, Instant now) {}
  Verification verification() throws Exception {
    Instant now = Instant.now().truncatedTo(java.time.temporal.ChronoUnit.MICROS),
            before = now.minusSeconds(2), until = now.plusSeconds(60);
    var pair = java.security.KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
    var original = map(Jcs.parse(CT.getBytes(StandardCharsets.UTF_8)));
    var claims = obj(original, "claims");
    var scope = obj(obj(claims, "binding"), "scope");
    var keyRecord = record("key_id", "reader", "purpose", "portal-task-read", "workload_ref",
        scope.get("workload_ref"), "peer_spki_sha256", "c".repeat(64), "public_key_spki_base64",
        Base64.getEncoder().encodeToString(pair.getPublic().getEncoded()), "not_before",
        time(before), "not_after", time(until));
    var admitted = new PortalReadTrust.Admission() {
      public String generation() {
        return "8";
      }
      public String providerRef() {
        return "test-admission";
      }
      public String providerRevision() {
        return "1";
      }
      public String capabilityDigest() {
        return "d".repeat(64);
      }
      public Instant observedAt() {
        return before;
      }
      public Instant validUntil() {
        return until;
      }
      public int statementTimeoutSeconds() {
        return 1;
      }
      public void requireCurrent() {}
      public void verifySource(String k, Map<String, Object> s) {
        throw unavailable();
      }
      public void verifyCatalog(Map<String, Object> a) {
        throw unavailable();
      }
      public void verifyIdentityPolicy(
          Map<String, Object> p, Map<String, Object> t, List<Object> l) {
        throw unavailable();
      }
      public void verifyClassification(Map<String, Object> c, Map<String, Object> e) {
        throw unavailable();
      }
    };
    var nativeKey = new PortalReadTrust.NativeKey("public-test-key", "1", "a".repeat(64), before,
        until, new SecretKeySpec(new byte[32], "HmacSHA256"), () -> {});
    var keySet = new PortalReadTrust.NativeKeySet() {
      public PortalReadTrust.NativeKey current() {
        return nativeKey;
      }
      public PortalReadTrust.NativeKey verification(String id) {
        return id.equals(nativeKey.id) ? nativeKey : null;
      }
      public void requireCurrent() {}
    };
    var providers = new PortalReadTrust.Providers() {
      public PortalReadTrust.Admission acquire(Map<String, Object> s, String e, String i, String d,
          String h, String configurationDigest, String p) {
        return admitted;
      }
      public PortalReadTrust.NativeKeySet continuity(PortalReadTrust.Admission a) {
        return keySet;
      }
      public PortalReadTrust.PublicationQualification qualifyPublication(
          PortalReadTrust.Admission a, Map<String, Object> p) {
        throw unavailable();
      }
    };
    var b = obj(claims, "binding");
    var trust = PortalReadTrust.configured(
        record("schema", "portal-read-trust.v1", "scope", scope, "engine_name",
            b.get("engine_name"), "database_incarnation", b.get("database_incarnation"), "audience",
            "test", "read_deployment_ref", b.get("read_deployment_ref"), "read_deployment_digest",
            b.get("read_deployment_digest"), "validity_policy_ref", "PUBLIC_SYNTHETIC",
            "validity_policy_digest", "d".repeat(64), "max_envelope_seconds", "60", "public_keys",
            List.of(keyRecord)),
        providers);
    var envelope = new PortalReadEnvelope.Verified(
        record("read_context_id", b.get("read_context_id"), "request_id", b.get("read_context_id")),
        "1".repeat(64), trust.keys.get("reader"), before, until);
    var command = new PortalReadCommand(trust, envelope, admitted, keySet);
    var task = map(Jcs.parse(TASK.getBytes(StandardCharsets.UTF_8)));
    task.put("valid_until", time(until));
    obj(task, "snapshot").put("snapshot_at", time(now));
    claims.put("binding", command.binding());
    claims.put("task_digest", hash(task));
    claims.put("snapshot_digest", hash(task.get("snapshot")));
    claims.put("snapshot_at", time(now));
    claims.put("issued_at", time(now));
    claims.put("valid_until", time(until));
    for (Object o : list(claims.get("ceilings"))) {
      map(o).put("observed_at", time(before));
      map(o).put("valid_until", time(until));
    }
    original.put("mac", HexFormat.of().formatHex(nativeKey.mac("task", claims, now)));
    return new Verification(command, task, original, nativeKey, now);
  }
  @Test
  void actualVerifierChecksMacContextRequesterTaskSnapshotAndExpiry() throws Exception {
    for (String change : List.of("none", "mac", "context", "requester", "task", "snapshot", "time",
             "expiry", "generation")) {
      var f = verification();
      var proof = copy(f.proof);
      var c = obj(proof, "claims");
      switch (change) {
        case "context" ->
          obj(c, "binding")
              .put("read_context_id",
                  Base64.getUrlEncoder().withoutPadding().encodeToString(new byte[32]));
        case "requester" ->
          obj(obj(c, "binding"), "requester").put("public_key_sha256", "f".repeat(64));
        case "task" -> c.put("task_digest", "f".repeat(64));
        case "snapshot" -> c.put("snapshot_digest", "f".repeat(64));
        case "time" -> c.put("snapshot_at", time(f.now.minusSeconds(1)));
        case "expiry" -> {
          c.put("valid_until", time(f.now.minusSeconds(1)));
          c.put("issued_at", time(f.now.minusSeconds(2)));
          for (Object o : list(c.get("ceilings"))) {
            map(o).put("observed_at", time(f.now.minusSeconds(3)));
            map(o).put("valid_until", time(f.now.minusSeconds(1)));
          }
        }
        case "generation" -> obj(c, "binding").put("runtime_admission_generation", "7");
      }
      proof.put("mac",
          change.equals("mac") ? "f".repeat(64)
                               : HexFormat.of().formatHex(f.key.mac("task", c, f.now)));
      if (change.equals("none"))
        assertNotNull(f.command.verify(proof, "task", f.task));
      else
        assertThrows(Rejected.class, () -> f.command.verify(proof, "task", f.task), change);
    }
  }

  static Map<String, Object> identityRow(String principal, Instant until) {
    return record("tenant_", "test-tenant", "principal_", principal, "issuer_",
        "https://public-synthetic.example", "subject_", "subject-" + principal, "rev_", 17L,
        "active_", true, "valid_until_", until.getEpochSecond(), "groups_", "[\"group-a\"]");
  }
  static PortalReadCommand next(PortalReadCommand c) {
    var next = new PortalReadCommand(c.trust, c.envelope, c.admission, c.keys);
    next.base();
    return next;
  }
  static PortalReadCommand.State identityState(Map<String, Object> identity) {
    return new PortalReadCommand.State(PortalReadCommand.identityState(record("task", "synthetic"),
        Map.of(str(identity, "principal_ref"), identity)), record("catalog", "synthetic"),
        Map.of(), Map.of(), null, Map.of());
  }
  @Test
  void contributingIdentityMinimumAndExactRepeatedPrincipalAreRetained() throws Exception {
    var f = verification(); var c = f.command(); c.base();
    Instant first = f.now().plusSeconds(25).truncatedTo(java.time.temporal.ChronoUnit.SECONDS);
    Instant shortest = first.minusSeconds(5);
    var own = identityRow("requester-and-assignee", first);
    var original = c.humanIdentity("requester-and-assignee", own);
    var frozen = c.ceilings.stream().map(PortalReadModels::copy).toList();
    assertEquals(original, c.humanIdentity("requester-and-assignee", own));
    assertEquals(frozen, c.ceilings);
    c.humanIdentity("other-candidate", identityRow("other-candidate", shortest));
    c.humanIdentity("later-candidate", identityRow("later-candidate", first.plusSeconds(2)));
    assertEquals(shortest, c.until());
    assertEquals("test-tenant", original.get("tenant"));
    assertEquals("17", original.get("revision"));
    assertEquals(first, time(original.get("valid_until")));
  }
  @Test
  void nativeIdentityOnlyChangesCannotReuseTaskContinuity() throws Exception {
    for (String field : List.of("issuer_", "subject_", "rev_", "active_", "valid_until_", "groups_")) {
      var f = verification(); var c = f.command(); c.base();
      var row = identityRow("other", f.now().plusSeconds(25));
      var original = c.humanIdentity("other", row);
      var changed = new LinkedHashMap<>(row);
      changed.put(field, switch (field) {
        case "rev_" -> 18L; case "active_" -> false;
        case "valid_until_" -> ((Long) row.get(field)) + 10;
        case "groups_" -> "[\"group-b\"]"; default -> "changed-identity";
      });
      assertThrows(Rejected.class, () -> c.humanIdentity("other", changed), field);
      if (!field.equals("active_")) {
        var fresh = next(c);
        var later = fresh.humanIdentity("other", changed);
        assertNotEquals(hash(identityState(original).nativeState()), hash(identityState(later).nativeState()), field);
      }
    }
  }
  @Test
  void missingForeignInactiveAndExpiredNativeRowsRefuse() throws Exception {
    var f = verification(); var c = f.command(); c.base();
    var row = identityRow("other", f.now().plusSeconds(25));
    assertThrows(Rejected.class, () -> c.humanIdentity("other", null));
    for (String field : row.keySet()) {
      var missing = new LinkedHashMap<>(row); missing.remove(field);
      assertThrows(Rejected.class, () -> c.humanIdentity("other", missing), field);
    }
    for (String field : List.of("tenant_", "principal_")) {
      var foreign = new LinkedHashMap<>(row); foreign.put(field, "foreign");
      assertThrows(Rejected.class, () -> c.humanIdentity("other", foreign));
    }
    var inactive = new LinkedHashMap<>(row); inactive.put("active_", false);
    assertThrows(Rejected.class, () -> c.humanIdentity("other", inactive));
    assertThrows(Rejected.class, () -> c.humanIdentity("other", identityRow("other", f.now().minusSeconds(1))));
  }
  @Test
  void nativeIdentityStateOrderingIsExactAndCallerIsNotInvented() throws Exception {
    var f = verification(); var c = f.command(); c.base();
    var a = c.humanIdentity("a", identityRow("a", f.now().plusSeconds(25)));
    var z = c.humanIdentity("z", identityRow("z", f.now().plusSeconds(20)));
    var one = new LinkedHashMap<String, Map<String,Object>>(); one.put("z", z); one.put("a", a);
    var two = new LinkedHashMap<String, Map<String,Object>>(); two.put("a", a); two.put("z", z);
    var state = PortalReadCommand.identityState(record("task", "actual-observation"), one);
    assertEquals(state, PortalReadCommand.identityState(record("task", "actual-observation"), two));
    assertEquals(List.of(a,z), state.get("native_identities"));
    assertFalse(state.containsKey("requester"));
    assertEquals(List.of(), PortalReadCommand.identityState(Map.of(), Map.of()).get("native_identities"));
  }
  @Test
  void originalIdentityObservationSurvivesTaskAuthorityDisclosureAndRenewal() throws Exception {
    var f = verification(); var c = f.command(); c.base();
    Instant shortEnd = f.now().plusSeconds(25).truncatedTo(java.time.temporal.ChronoUnit.SECONDS);
    var row = identityRow("other", shortEnd); var identity = c.humanIdentity("other", row);
    var task = copy(f.task()); task.put("valid_until", time(c.until()));
    var state = identityState(identity); var ct = c.mint("task", task, state, null, null, null);
    var caCommand = next(c); caCommand.verify(ct, "task", task);
    assertEquals(identity, caCommand.humanIdentity("other", row));
    assertEquals(shortEnd, caCommand.until());
    // Exercise the actual native commitment producer/verifier; these minimal records are unit facts.
    var authority = record("valid_until", time(shortEnd));
    var ca = caCommand.mint("authority", task, state, ct, authority,
        record("principal_digest", "e".repeat(64)));
    var disclosure = next(c); disclosure.verify(ct, "task", task); disclosure.verify(ca, "authority", task);
    disclosure.humanIdentity("other", row);
    assertEquals(shortEnd, disclosure.until());
    var original = list(obj(ct, "claims").get("ceilings")).stream().map(PortalReadModels::map)
        .filter(x -> str(x,"source_ref").startsWith("native-human:")).toList();
    assertEquals(1, original.size());
    for (var command : List.of(caCommand, disclosure))
      assertTrue(command.ceilings.contains(original.get(0)));
    var renewed = new LinkedHashMap<>(row); renewed.put("valid_until_", shortEnd.plusSeconds(10).getEpochSecond());
    assertEquals(409, assertThrows(Rejected.class, () -> disclosure.humanIdentity("other", renewed)).status);
    assertEquals(shortEnd, disclosure.until());
  }
  @Test
  void identityExpiryRefusesEveryRemainingNativeBoundary() throws Exception {
    var f = verification(); var c = f.command(); c.base();
    Instant shortEnd = Instant.now().plusSeconds(2).truncatedTo(java.time.temporal.ChronoUnit.SECONDS);
    var identity = c.humanIdentity("other", identityRow("other", shortEnd));
    var task = copy(f.task()); task.put("valid_until", time(shortEnd));
    var state = identityState(identity); var ct = c.mint("task", task, state, null, null, null);
    var caCommand = next(c); caCommand.verify(ct, "task", task);
    var ca = caCommand.mint("authority", task, state, ct, record("valid_until", time(shortEnd)),
        record("principal_digest", "e".repeat(64)));
    var result = new PortalReadCommand.Result(new byte[]{1}, () -> c.guard());
    assertThrows(Rejected.class, result::bytes); result.committed = true; assertArrayEquals(new byte[]{1}, result.bytes());
    long wait = java.time.Duration.between(Instant.now(), shortEnd).toMillis()+10;
    if (wait > 0) Thread.sleep(wait);
    assertThrows(Rejected.class, () -> next(c).verify(ct,"task",task));
    assertThrows(Rejected.class, () -> next(c).verify(ca,"authority",task));
    assertThrows(Rejected.class, c::guard); // same source-derived guard used by COMMITTING and discovery
    assertThrows(Rejected.class, c::until);
    assertThrows(Rejected.class, result::bytes);
  }

  @Test
  void laterCAImportCorrelatesEarlierCurrentIdentityObservationWithoutRenewal() throws Exception {
    var f = verification(); var original = f.command(); original.base();
    var row = identityRow("requester", f.now().plusSeconds(25));
    original.humanIdentity("requester", row);
    var ceiling = original.ceilings.stream().filter(x -> str(x,"source_ref").startsWith("native-human:")).findFirst().orElseThrow();
    var current = next(original); current.humanIdentity("requester",row);
    current.addCeiling(ceiling); // mirrors disclosure's current membership read before CA verification
    assertTrue(current.ceilings.contains(ceiling));
    assertEquals(1, current.ceilings.stream().filter(x -> str(x,"source_ref").startsWith("native-human:")).count());
    for (String field : List.of("source_revision","source_digest","valid_until")) {
      var changed = copy(ceiling);
      changed.put(field, field.equals("source_revision") ? "18" : field.equals("source_digest") ? "f".repeat(64) : time(f.now().plusSeconds(50)));
      assertEquals(409, assertThrows(Rejected.class, () -> current.addCeiling(changed)).status);
    }
  }
  @Test void pendingNativeReadUsesExistingExactAuthFormWithoutReembolsoAlias() {
    var entry=Map.<String,Object>of("process_definition_key","SP-OP-AUTH-001","task_definition_key","UT_DecidirPendenciaExpirada",
        "form_key","auth_pendencia","form_source_status","BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY","allowed_inputs",List.of("decisao_pendencia"));
    assertDoesNotThrow(()->PortalReadCommand.compiled(entry));
    var wrong=new HashMap<>(entry);wrong.put("process_definition_key","SP-OP-REEMBOLSO-001");
    var foreign=wrong;assertThrows(RuntimeException.class,()->PortalReadCommand.compiled(foreign));
    wrong=new HashMap<>(entry);wrong.put("form_source_status","BPMN_FORMDATA");
    var changedSource=wrong;assertThrows(RuntimeException.class,()->PortalReadCommand.compiled(changedSource));
  }

}
