package br.com.maezo.human;
import static org.junit.jupiter.api.Assertions.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.spec.X509EncodedKeySpec;
import java.util.*;
import org.junit.jupiter.api.Test;

/** Finite unit vectors only: no database, engine or consumer qualification. */
class ClassifiedDecisionTest {
  static final String WIRE64 = "eyJhdWRpdF9pbnRlbnRfcmVmIjoiOGEzYWIxNjdkODM4YzgwZWQwMzE3ODlkMjI3YTY3M2U1YTc2YzQyYWE4ZWM1MDcyZTE0YTZjZDRjNmI0ZDU2ZCIsImJpbmRpbmdfZGlnZXN0IjoiZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZCIsImV2aWRlbmNlX3JlZiI6ImV2aWRlbmNlLXRlc3QiLCJodW1hbl9iYXNpcyI6eyJjb250ZW50X2RpZ2VzdCI6ImZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmYiLCJjdXN0b2R5X3JlZiI6ImN1c3RvZHktdGVzdCJ9LCJvdXRjb21lIjp7ImRlY2lzYW9fYXVkaXRvciI6Ik5FR0FSIiwia2luZCI6ImF1dGhfZGVjaXNhbyJ9LCJwcmluY2lwYWxfaXNzdWVyIjoiaHR0cHM6Ly9pc3N1ZXIuZXhhbXBsZS50ZXN0LyIsInByaW5jaXBhbF9yZWYiOiJodW1hbi10ZXN0IiwicHJpbmNpcGFsX3N1YmplY3QiOiJzdWJqZWN0LXRlc3QiLCJyZXF1ZXN0X2RpZ2VzdCI6ImVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWUiLCJzY2hlbWEiOiJodW1hbi1jbGFzc2lmaWVkLWRlY2lzaW9uLnYxIiwic2NvcGUiOnsiZW52aXJvbm1lbnQiOiJ0ZXN0IiwidGVuYW50IjoidGVuYW50LXRlc3QiLCJ3b3JrbG9hZF9yZWYiOiJnYXRld2F5LXRlc3QifSwidGFyZ2V0Ijp7ImNvbW1hbmRfaWQiOiJjb21tYW5kLXRlc3QiLCJleHBlY3RlZF9hdXRob3JpdHlfcmV2aXNpb24iOiIzIiwiZXhwZWN0ZWRfZXZpZGVuY2VfZGlnZXN0IjoiY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjYyIsImV4cGVjdGVkX2V2aWRlbmNlX3JldmlzaW9uIjoiMyIsImV4cGVjdGVkX21lbWJlcnNoaXBfcmV2aXNpb24iOiIxIiwiZXhwZWN0ZWRfdGFza19yZXZpc2lvbiI6IjIiLCJmb3JtX2RpZ2VzdCI6ImJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmIiLCJmb3JtX2tleSI6ImF1dGhfZGVjaXNhbyIsImZvcm1fdmVyc2lvbiI6IjEiLCJwcm9jZXNzX2RlZmluaXRpb25fZGlnZXN0IjoiYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYSIsInByb2Nlc3NfZGVmaW5pdGlvbl9pZCI6InByb2Nlc3MtdGVzdDoxOmlkIiwicHJvY2Vzc19kZWZpbml0aW9uX2tleSI6IlNQLU9QLUFVVEgtMDAxIiwicHJvY2Vzc19kZWZpbml0aW9uX3ZlcnNpb24iOiIxIiwic2NoZW1hX3ZlcnNpb24iOiIxIiwidGFza19kZWZpbml0aW9uX2tleSI6IlVUX0FuYWxpc2VNZWRpY29BdWRpdG9yIiwidGFza19pZCI6InRhc2stdGVzdCJ9fQ==";
  static final String ENVELOPE64 = "eyJhbGdvcml0aG0iOiJFZDI1NTE5IiwiYXVkaWVuY2UiOiJlbmdpbmUtdGVzdCIsImNvbW1hbmQiOnsiYXVkaXRfaW50ZW50X3JlZiI6IjhhM2FiMTY3ZDgzOGM4MGVkMDMxNzg5ZDIyN2E2NzNlNWE3NmM0MmFhOGVjNTA3MmUxNGE2Y2Q0YzZiNGQ1NmQiLCJiaW5kaW5nX2RpZ2VzdCI6ImRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGQiLCJldmlkZW5jZV9yZWYiOiJldmlkZW5jZS10ZXN0IiwiaHVtYW5fYmFzaXMiOnsiY29udGVudF9kaWdlc3QiOiJmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmIiwiY3VzdG9keV9yZWYiOiJjdXN0b2R5LXRlc3QifSwib3V0Y29tZSI6eyJkZWNpc2FvX2F1ZGl0b3IiOiJORUdBUiIsImtpbmQiOiJhdXRoX2RlY2lzYW8ifSwicHJpbmNpcGFsX2lzc3VlciI6Imh0dHBzOi8vaXNzdWVyLmV4YW1wbGUudGVzdC8iLCJwcmluY2lwYWxfcmVmIjoiaHVtYW4tdGVzdCIsInByaW5jaXBhbF9zdWJqZWN0Ijoic3ViamVjdC10ZXN0IiwicmVxdWVzdF9kaWdlc3QiOiJlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlIiwic2NoZW1hIjoiaHVtYW4tY2xhc3NpZmllZC1kZWNpc2lvbi52MSIsInNjb3BlIjp7ImVudmlyb25tZW50IjoidGVzdCIsInRlbmFudCI6InRlbmFudC10ZXN0Iiwid29ya2xvYWRfcmVmIjoiZ2F0ZXdheS10ZXN0In0sInRhcmdldCI6eyJjb21tYW5kX2lkIjoiY29tbWFuZC10ZXN0IiwiZXhwZWN0ZWRfYXV0aG9yaXR5X3JldmlzaW9uIjoiMyIsImV4cGVjdGVkX2V2aWRlbmNlX2RpZ2VzdCI6ImNjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2MiLCJleHBlY3RlZF9ldmlkZW5jZV9yZXZpc2lvbiI6IjMiLCJleHBlY3RlZF9tZW1iZXJzaGlwX3JldmlzaW9uIjoiMSIsImV4cGVjdGVkX3Rhc2tfcmV2aXNpb24iOiIyIiwiZm9ybV9kaWdlc3QiOiJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiIiwiZm9ybV9rZXkiOiJhdXRoX2RlY2lzYW8iLCJmb3JtX3ZlcnNpb24iOiIxIiwicHJvY2Vzc19kZWZpbml0aW9uX2RpZ2VzdCI6ImFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWEiLCJwcm9jZXNzX2RlZmluaXRpb25faWQiOiJwcm9jZXNzLXRlc3Q6MTppZCIsInByb2Nlc3NfZGVmaW5pdGlvbl9rZXkiOiJTUC1PUC1BVVRILTAwMSIsInByb2Nlc3NfZGVmaW5pdGlvbl92ZXJzaW9uIjoiMSIsInNjaGVtYV92ZXJzaW9uIjoiMSIsInRhc2tfZGVmaW5pdGlvbl9rZXkiOiJVVF9BbmFsaXNlTWVkaWNvQXVkaXRvciIsInRhc2tfaWQiOiJ0YXNrLXRlc3QifX0sImRpZ2VzdCI6ImE2YmIzOTIyNjY1ZWQ2NjExMGU0YmY4OWQyMjdlZGM5Y2EzNWMxMzk3ZjM4ZTMwNmRhNDcwODJmMTcxMTVmMGUiLCJleHBpcmVzX2F0IjoiMTYwIiwiaXNzdWVkX2F0IjoiMTAwIiwiaXNzdWVyIjoiZ2F0ZXdheS10ZXN0Iiwia2V5X2lkIjoiY29tbWFuZC1rZXkiLCJwdXJwb3NlIjoiaHVtYW4tY29tbWFuZCIsInNjaGVtYSI6Imh1bWFuLWVudmVsb3BlLnYxIiwic2lnbmF0dXJlIjoidmVjcHMzQXN3TFdYVUdfM2NRZHRNc0R6Q3AzYVdmYkRpc0lQMm5GUi1wMmJEcHJETjBvbElhcW1Cc0RobXFweUlwaXdjQ3pCdWZtQms5SWJLQ2x4Q1EiLCJ0ZW5hbnQiOiJ0ZW5hbnQtdGVzdCJ9";
  static final String SPKI64 = "MCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo=";
  Map<String, Object> payload() { return Jcs.object(Jcs.parse(Base64.getDecoder().decode(WIRE64))); }
  HumanCommand command() { return HumanCommand.parse(payload()); }

  @Test void exactPythonBytesAndSignatureVerify() throws Exception {
    byte[] raw = Base64.getDecoder().decode(WIRE64);
    assertArrayEquals(raw, Jcs.canonical(payload()));
    assertEquals("a6bb3922665ed66110e4bf89d227edc9ca35c1397f38e306da47082f17115f0e", Jcs.digest(raw));
    TestKeys keys = new TestKeys();
    var config = keys.config();
    PublicKey pub = KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(Base64.getDecoder().decode(SPKI64)));
    config.put("keys", List.of(keys.key("command-key", "human-command", "gateway-test", keys.peer, pub),
        keys.key("authority-key", "human-authority", "publisher-test", keys.adminPeer, keys.authority.getPublic())));
    var verified = Envelope.verify(Base64.getDecoder().decode(ENVELOPE64), new Trust(config), "human-command", keys.peer, 120, x -> false);
    assertEquals("NEGAR", HumanCommand.parse(verified.command()).outcome());
  }

  @Test void typedProjectionKeepsBasisSeparateAndProvenanceDerived() {
    var c = command();
    var vars = c.classified().variables(c);
    assertEquals("NEGAR", vars.get("decisao_auditor"));
    assertEquals("human-test", vars.get("auditor_id"));
    assertEquals("custody-test", vars.get("human_decision_custody_ref"));
    assertEquals(Set.of("decisao_auditor", "auditor_id", "human_decision_custody_ref", "human_decision_content_digest",
        "human_decision_request_digest", "human_decision_binding_digest", "human_decision_principal_ref",
        "human_decision_workload_ref", "human_decision_command_ref", "human_decision_audit_intent_ref"), vars.keySet());
    assertThrows(UnsupportedOperationException.class, () -> vars.put("human_approved", true));
  }

  @Test void clinicalNamesAndUnclosedInputsAreRejected() {
    for (String field : List.of("justificativa_clinica", "cid10_referencia", "fundamentacao_dut", "human_approved", "variables")) {
      var c = payload(); Jcs.object(c.get("outcome")).put(field, "pointer");
      assertThrows(Rejected.class, () -> HumanCommand.parse(c));
    }
    var c = payload(); Jcs.object(c.get("human_basis")).put("custody_ref", "${unresolved}");
    assertThrows(Rejected.class, () -> HumanCommand.parse(c));
  }

  @Test void authenticatedEnvelopeDoesNotAuthorizeDifferentScopeOrMutation() {
    TestKeys keys = new TestKeys();
    var p = payload(); Jcs.object(p.get("scope")).put("tenant", "other-tenant");
    var raw = keys.sign(p, "human-command", 100, 160);
    assertThrows(Rejected.class, () -> Envelope.verify(raw, keys.trust(), "human-command", keys.peer, 120, x -> false));
    var good = keys.sign(payload(), "human-command", 100, 160);
    var e = Jcs.object(Jcs.parse(good)); Jcs.object(Jcs.object(e.get("command")).get("human_basis")).put("content_digest", "0".repeat(64));
    assertThrows(Rejected.class, () -> Envelope.verify(Jcs.canonical(e), keys.trust(), "human-command", keys.peer, 120, x -> false));
  }

  Map<String, Object> binding() {
    var c = command(); var d = c.classified(); var b = new HashMap<String, Object>();
    b.put("process_key_", c.processKey()); b.put("process_version_", c.processVersion());
    b.put("process_digest_", c.processDigest()); b.put("form_key_", c.formKey());
    b.put("form_version_", c.formVersion()); b.put("form_digest_", c.formDigest());
    b.put("authority_rev_", 3L); b.put("binding_digest_", d.bindingDigest());
    b.put("input_kind_", d.kind()); b.put("workload_", c.workloadRef());
    b.put("consumer_digest_", "1".repeat(64)); b.put("required_group_", "auditors");
    b.put("active_", true); b.put("valid_until_", 160L); return b;
  }

  @Test void qualifiedPinAndRoleAndFreshnessMutationsRefuse() {
    var c = command(); var b = binding();
    assertDoesNotThrow(() -> ClassifiedDecision.requireBinding(c, b, List.of("auditors"), 120));
    for (String field : List.of("process_key_", "process_version_", "process_digest_", "form_key_", "form_version_",
        "form_digest_", "binding_digest_", "input_kind_", "workload_", "consumer_digest_", "required_group_")) {
      var mutated = binding(); mutated.put(field, "different");
      assertThrows(Rejected.class, () -> ClassifiedDecision.requireBinding(c, mutated, List.of("auditors"), 120), field);
    }
    var revision = binding(); revision.put("authority_rev_", 4L);
    assertThrows(Rejected.class, () -> ClassifiedDecision.requireBinding(c, revision, List.of("auditors"), 120));
    assertThrows(Rejected.class, () -> ClassifiedDecision.requireBinding(c, b, List.of("admin"), 120));
    assertThrows(Rejected.class, () -> ClassifiedDecision.requireBinding(c, b, List.of("auditors"), 160));
    var inactive = binding(); inactive.put("active_", false);
    assertThrows(Rejected.class, () -> ClassifiedDecision.requireBinding(c, inactive, List.of("auditors"), 120));
  }
  @Test void allSixBoundTasksPreserveExactTypedOutcomes() {
    String[][] forms = {
      {"SP-OP-AUTH-001", "UT_AnaliseMedicoAuditor", "auth_decisao", "decisao_auditor", "APROVAR,NEGAR,SOLICITAR_INFO,JUNTA_MEDICA"},
      {"SP-OP-AUTH-001", "UT_CoordenacaoAssume", "auth_decisao", "decisao_auditor", "APROVAR,NEGAR,SOLICITAR_INFO,JUNTA_MEDICA"},
      {"SP-OP-AUTH-001", "UT_RegistrarParecerJunta", "auth_junta", "decisao_auditor", "APROVAR,NEGAR"},
      {"SP-OP-ESCALATION-001", "UT_TratarEscalonamento", "escalation", "resultado", "resolvido_humano,devolvido_agente,emergencia_acionada"},
      {"SP-OP-ESCALATION-001", "UT_SupervisorAssume", "escalation", "resultado", "resolvido_humano,devolvido_agente,emergencia_acionada"},
      {"SP-OP-PAGTO-001", "UT_AnaliseAdmissibilidade", "pagto_admissibilidade", "decisao_admissibilidade", "PROSSEGUIR,DEVOLVER"}
    };
    for (var form : forms) for (String outcome : form[4].split(",")) {
      var raw = payload(); var t = Jcs.object(raw.get("target"));
      t.put("process_definition_key", form[0]); t.put("task_definition_key", form[1]); t.put("form_key", form[2]);
      raw.put("outcome", Map.of("kind", form[2], form[3], outcome));
      var c = HumanCommand.parse(raw); var vars = c.classified().variables(c);
      assertEquals(outcome, vars.get(form[3]));
      assertEquals(form[0].equals("SP-OP-AUTH-001"), vars.containsKey("auditor_id"));
      assertFalse(vars.containsKey("aprovador_id")); assertFalse(vars.containsKey("human_approved"));
    }
  }

}
