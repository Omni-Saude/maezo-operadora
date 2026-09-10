package br.com.maezo.human;

import java.util.*;

/** D3/D5 classified projection. Narrative is never written to General engine variables. */
record ClassifiedDecision(String environment, String bindingDigest, String requestDigest,
    String custodyRef, String contentDigest, String kind, String outcomeField, String outcome) {
  static HumanCommand parse(Map<String, Object> c) {
    Jcs.keys(c, "schema", "scope", "principal_ref", "principal_issuer", "principal_subject",
        "target", "binding_digest", "evidence_ref", "request_digest", "audit_intent_ref", "outcome", "human_basis");
    if (!"human-classified-decision.v1".equals(c.get("schema"))) throw Rejected.invalid();
    var scope = Jcs.object(c.get("scope"));
    Jcs.keys(scope, "tenant", "environment", "workload_ref");
    var t = Jcs.object(c.get("target"));
    Jcs.keys(t, "schema_version", "command_id", "task_id", "process_definition_key", "process_definition_version",
        "process_definition_id", "process_definition_digest", "task_definition_key", "form_key", "form_version",
        "form_digest", "expected_task_revision", "expected_evidence_revision", "expected_evidence_digest",
        "expected_membership_revision", "expected_authority_revision");
    if (!"1".equals(t.get("schema_version")) || "0".equals(t.get("form_version"))
        || "0".equals(t.get("process_definition_version"))) throw Rejected.invalid();
    String process = Jcs.ref(t, "process_definition_key"), task = Jcs.ref(t, "task_definition_key");
    String kind = switch (process + "/" + task) {
      case "SP-OP-AUTH-001/UT_AnaliseMedicoAuditor", "SP-OP-AUTH-001/UT_CoordenacaoAssume" -> "auth_decisao";
      case "SP-OP-AUTH-001/UT_RegistrarParecerJunta" -> "auth_junta";
      case "SP-OP-ESCALATION-001/UT_TratarEscalonamento", "SP-OP-ESCALATION-001/UT_SupervisorAssume" -> "escalation";
      case "SP-OP-PAGTO-001/UT_AnaliseAdmissibilidade" -> "pagto_admissibilidade";
      default -> throw Rejected.invalid();
    };
    if (!kind.equals(t.get("form_key"))) throw Rejected.invalid();
    String field = switch (kind) {
      case "auth_decisao", "auth_junta" -> "decisao_auditor";
      case "escalation" -> "resultado";
      default -> "decisao_admissibilidade";
    };
    var o = Jcs.object(c.get("outcome"));
    Jcs.keys(o, "kind", field);
    String outcome = Jcs.string(o, field);
    Set<String> allowed = switch (kind) {
      case "auth_decisao" -> Set.of("APROVAR", "NEGAR", "SOLICITAR_INFO", "JUNTA_MEDICA");
      case "auth_junta" -> Set.of("APROVAR", "NEGAR");
      case "escalation" -> Set.of("resolvido_humano", "devolvido_agente", "emergencia_acionada");
      default -> Set.of("PROSSEGUIR", "DEVOLVER");
    };
    if (!kind.equals(o.get("kind")) || !allowed.contains(outcome)) throw Rejected.invalid();
    var basis = Jcs.object(c.get("human_basis"));
    Jcs.keys(basis, "custody_ref", "content_digest");
    var decision = new ClassifiedDecision(Jcs.ref(scope, "environment"), Jcs.hash(c, "binding_digest"),
        Jcs.hash(c, "request_digest"), Jcs.ref(basis, "custody_ref"), Jcs.hash(basis, "content_digest"), kind, field, outcome);
    for (String ref : List.of(Jcs.ref(c, "evidence_ref"), decision.custodyRef()))
      if (!ref.matches("[A-Za-z0-9][A-Za-z0-9_.:@-]{0,254}")) throw Rejected.invalid();
    if (!Jcs.digest(Jcs.canonical(List.of(scope, Jcs.ref(t, "task_id"), Jcs.ref(t, "command_id"))))
        .equals(Jcs.ref(c, "audit_intent_ref"))) throw Rejected.invalid();
    String principal = Jcs.ref(c, "principal_ref");
    if (principal.equals(Jcs.ref(scope, "workload_ref"))) throw Rejected.invalid();
    return new HumanCommand(Jcs.ref(scope, "tenant"), Jcs.ref(t, "task_id"), Jcs.ref(t, "command_id"),
        principal, Jcs.string(c, "principal_issuer"), Jcs.ref(c, "principal_subject"),
        Jcs.ref(scope, "workload_ref"), "decision", Jcs.ref(t, "process_definition_id"), process,
        Jcs.decimal(t, "process_definition_version"), Jcs.hash(t, "process_definition_digest"), task, kind,
        Jcs.decimal(t, "form_version"), Jcs.hash(t, "form_digest"), Jcs.decimal(t, "expected_task_revision"),
        Jcs.decimal(t, "expected_authority_revision"), Jcs.decimal(t, "expected_membership_revision"),
        Jcs.decimal(t, "expected_evidence_revision"), Jcs.ref(c, "evidence_ref"), Jcs.hash(t, "expected_evidence_digest"),
        principal, Jcs.ref(c, "audit_intent_ref"), outcome, decision);
  }

  /** Only callable after an exact, current, owner-installed consumer qualification. */
  Map<String, Object> variables(HumanCommand command) {
    Map<String, Object> vars = new TreeMap<>();
    vars.put(outcomeField, outcome);
    vars.put("human_decision_custody_ref", custodyRef);
    vars.put("human_decision_content_digest", contentDigest);
    vars.put("human_decision_request_digest", requestDigest);
    vars.put("human_decision_binding_digest", bindingDigest);
    vars.put("human_decision_principal_ref", command.principalRef());
    vars.put("human_decision_workload_ref", command.workloadRef());
    vars.put("human_decision_command_ref", command.commandId());
    vars.put("human_decision_audit_intent_ref", command.auditIntentRef());
    if (kind.equals("auth_decisao") || kind.equals("auth_junta")) vars.put("auditor_id", command.principalRef());
    return Collections.unmodifiableMap(vars);
  }

  static void requireBinding(HumanCommand c, Map<String, Object> b, List<?> groups, long now) {
    var d = c.classified();
    if (!c.processKey().equals(b.get("process_key_")) || !c.processVersion().equals(b.get("process_version_"))
        || !c.processDigest().equals(b.get("process_digest_")) || !c.formKey().equals(b.get("form_key_"))
        || !c.formVersion().equals(b.get("form_version_")) || !c.formDigest().equals(b.get("form_digest_"))
        || !c.authorityRevision().equals(b.get("authority_rev_").toString())
        || !d.bindingDigest().equals(b.get("binding_digest_"))
        || !d.kind().equals(b.get("input_kind_"))
        || !c.workloadRef().equals(b.get("workload_"))
        || !(b.get("consumer_digest_") instanceof String consumer) || !consumer.matches("[0-9a-f]{64}")
        || !groups.contains(b.get("required_group_"))) throw new Rejected(409, "FORM_NOT_ACTIVATED");
    ClassifiedDecision.requireCurrent(b, now);
  }

  static void requireCurrent(Map<String, Object> binding, long now) {
    if (binding != null && (!Boolean.TRUE.equals(binding.get("active_"))
        || ((Number) binding.get("valid_until_")).longValue() <= now))
      throw new Rejected(409, "FORM_NOT_ACTIVATED");
  }
}
