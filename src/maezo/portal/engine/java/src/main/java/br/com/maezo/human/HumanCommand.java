package br.com.maezo.human;

import java.util.Map;

/** ADR0049 D3/D5, typed engine projection: no input-to-variable map. */
record HumanCommand(
    String tenant,
    String taskId,
    String commandId,
    String principalRef,
    String principalIssuer,
    String principalSubject,
    String workloadRef,
    String operation,
    String processId,
    String processKey,
    String processVersion,
    String processDigest,
    String taskKey,
    String formKey,
    String formVersion,
    String formDigest,
    String taskRevision,
    String authorityRevision,
    String membershipRevision,
    String evidenceRevision,
    String evidenceRef,
    String evidenceDigest,
    String assigneeRef,
    String auditIntentRef,
    String outcome) {
  static HumanCommand parse(Map<String, Object> c) {
    Jcs.keys(
        c,
        "schema",
        "tenant",
        "task_id",
        "command_id",
        "principal_ref",
        "principal_issuer",
        "principal_subject",
        "workload_ref",
        "operation",
        "process_definition_id",
        "process_definition_key",
        "process_definition_version",
        "process_definition_digest",
        "task_definition_key",
        "form_key",
        "form_version",
        "form_digest",
        "task_revision",
        "authority_revision",
        "membership_revision",
        "evidence_revision",
        "evidence_ref",
        "evidence_digest",
        "assignee_ref",
        "audit_intent_ref",
        "outcome");
    if (!"human-command.v1".equals(c.get("schema"))) throw Rejected.invalid();
    String op = Jcs.string(c, "operation");
    if (!java.util.Set.of("claim", "release", "decision").contains(op)) throw Rejected.invalid();
    String outcome = c.get("outcome") == null ? null : Jcs.string(c, "outcome");
    if (!java.util.Objects.equals(outcome, op.equals("decision") ? "ACK" : null))
      throw Rejected.invalid();
    return new HumanCommand(
        Jcs.ref(c, "tenant"),
        Jcs.ref(c, "task_id"),
        Jcs.ref(c, "command_id"),
        Jcs.ref(c, "principal_ref"),
        Jcs.string(c, "principal_issuer"),
        Jcs.ref(c, "principal_subject"),
        Jcs.ref(c, "workload_ref"),
        op,
        Jcs.ref(c, "process_definition_id"),
        Jcs.ref(c, "process_definition_key"),
        Jcs.decimal(c, "process_definition_version"),
        Jcs.hash(c, "process_definition_digest"),
        Jcs.ref(c, "task_definition_key"),
        Jcs.ref(c, "form_key"),
        Jcs.decimal(c, "form_version"),
        Jcs.hash(c, "form_digest"),
        Jcs.decimal(c, "task_revision"),
        Jcs.decimal(c, "authority_revision"),
        Jcs.decimal(c, "membership_revision"),
        Jcs.decimal(c, "evidence_revision"),
        Jcs.ref(c, "evidence_ref"),
        Jcs.hash(c, "evidence_digest"),
        c.get("assignee_ref") == null ? null : Jcs.ref(c, "assignee_ref"),
        Jcs.ref(c, "audit_intent_ref"),
        outcome);
  }
}
