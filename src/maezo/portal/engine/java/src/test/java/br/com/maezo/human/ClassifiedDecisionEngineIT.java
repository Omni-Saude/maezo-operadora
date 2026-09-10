package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import java.sql.*;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.task.Task;
import org.junit.jupiter.api.*;

/** Actual PostgreSQL/CIB transaction controls. Never executed by the unit lane.
 * Requires the existing MAEZO_HUMAN_IT_* settings and -Dmaezo.repo.root=absolute repo.
 * Uses unchanged production AUTH BPMN, starts at the auditor task under fixture admin.
 * Six source bindings and signed edge rows use explicit synthetic TEST authorities, never production qualification.
 * No clinical worker executes: this is NOT a full AUTH/PHI journey or activation proof.
 */
@Tag("integration")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class ClassifiedDecisionEngineIT {
  final ConsumerLineageEngineIT.Fixture f = new ConsumerLineageEngineIT.Fixture();
  String authId, authDigest;

  @BeforeEach void start() throws Exception {
    String root = System.getProperty("maezo.repo.root");
    if (root == null || !Path.of(root).isAbsolute()) throw new IllegalStateException("explicit repo root required");
    byte[] xml = Files.readAllBytes(Path.of(root, "spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn"));
    f.start();
    authId = f.definitions.get("SP-OP-AUTH-001"); authDigest = Jcs.digest(xml);
    f.principal("human-test", true, List.of("medico-auditor"));
  }

  @AfterEach void stop() throws Exception { f.stop(); }

  Map<String, Object> command() throws Exception {
    return command("SP-OP-AUTH-001","UT_AnaliseMedicoAuditor",Map.of("sla",new HashMap<>(Map.of("sla_alerta","PT1H","sla_analise","PT2H"))));
  }
  Map<String,Object> command(String process,String taskKey,Map<String,Object> variables)throws Exception{
    String definition=f.definitions.get(process),digest=f.digests.get(process);
    var pi=f.engine.getRuntimeService().createProcessInstanceById(definition).setVariables(variables).startBeforeActivity(taskKey).execute();
    Task task = f.engine.getTaskService().createTaskQuery().processInstanceId(pi.getId()).singleResult();
    f.engine.getTaskService().setAssignee(task.getId(), "human-test");
    var ev = f.authority("evidence");
    ev.put("task_id", task.getId()); ev.put("process_definition_id", definition);
    ev.put("evidence_ref", "evidence-test"); ev.put("evidence_digest", "c".repeat(64));
    ev.put("valid_until", Long.toString(Instant.now().getEpochSecond()+600)); f.publish(ev);
    var c = new ClassifiedDecisionTest().payload(); var t = Jcs.object(c.get("target"));
    c.put("principal_subject", "subject-human-test");
    t.put("process_definition_id", definition); t.put("process_definition_digest", digest);
    t.put("process_definition_key",process);t.put("task_definition_key",taskKey);
    String kind=ConsumerLineage.SOURCES.get(process+"/"+taskKey);t.put("form_key",kind);
    if(kind.equals("escalation"))c.put("outcome",Map.of("kind",kind,"resultado","resolvido_humano"));
    t.put("task_id", task.getId()); t.put("command_id", UUID.randomUUID().toString());
    t.put("expected_authority_revision", Long.toString(f.revision()));
    try (var con = f.connection(); var s = con.createStatement()) {
      try (var r = s.executeQuery("SELECT REV_ FROM ACT_RU_TASK WHERE ID_='" + task.getId() + "'")) {
        assertTrue(r.next()); t.put("expected_task_revision", r.getString(1));
      }
      try (var r = s.executeQuery("SELECT REV_ FROM MZO_HUMAN_PRINCIPAL WHERE PRINCIPAL_='human-test'")) {
        assertTrue(r.next()); t.put("expected_membership_revision", r.getString(1));
      }
      try (var r = s.executeQuery("SELECT REV_ FROM MZO_HUMAN_EVIDENCE WHERE TASK_='" + task.getId() + "'")) {
        assertTrue(r.next()); t.put("expected_evidence_revision", r.getString(1));
      }
    }
    c.put("audit_intent_ref", Jcs.digest(Jcs.canonical(List.of(c.get("scope"), t.get("task_id"), t.get("command_id")))));
    return c;
  }

  void installTestQualification(Map<String, Object> raw) throws Exception {
    f.qualify(raw);
  }

  @Test void noQualificationRefusesWithoutTaskOrReceiptEffects() throws Exception {
    var c = command(); String id = Jcs.ref(Jcs.object(c.get("target")), "task_id");
    assertThrows(Rejected.class, () -> f.send(c));
    assertNotNull(f.engine.getTaskService().createTaskQuery().taskId(id).singleResult());
    assertEquals(0, f.countReceipts());
  }

  @Test void qualifiedCompletionAndExactRetryShareOneReceipt() throws Exception {
    var c = command(); installTestQualification(c); var t = Jcs.object(c.get("target"));
    Task task = f.engine.getTaskService().createTaskQuery().taskId((String)t.get("task_id")).singleResult();
    byte[] first = f.send(c); assertArrayEquals(first, f.send(c));
    assertNull(f.engine.getTaskService().createTaskQuery().taskId(task.getId()).singleResult());
    assertEquals(1, f.countReceipts()); var r = Jcs.object(Jcs.parse(first));
    assertEquals("human-engine-receipt.v2", r.get("schema")); assertNull(r.get("resulting_task_revision"));
    var vars = f.engine.getRuntimeService().getVariables(task.getProcessInstanceId());
    assertEquals("NEGAR", vars.get("decisao_auditor")); assertEquals("human-test", vars.get("auditor_id"));
    assertEquals("custody-test", vars.get("human_decision_custody_ref"));
    for (String name : List.of("justificativa_clinica", "cid10_referencia", "fundamentacao_dut")) assertFalse(vars.containsKey(name));
  }

  @Test void staleBindingDigestRefusesWithoutConsumption() throws Exception {
    var c = command(); installTestQualification(c); c.put("binding_digest", "0".repeat(64));
    assertThrows(Rejected.class, () -> f.send(c)); assertEquals(0, f.countReceipts());
    assertEquals(1, f.engine.getTaskService().createTaskQuery().processDefinitionId(authId).count());
  }

  @Test void deferredCommitFailureRollsBackTaskVariablesAndReceipt() throws Exception {
    var c = command(); installTestQualification(c);
    Task task = f.engine.getTaskService().createTaskQuery().taskId((String)Jcs.object(c.get("target")).get("task_id")).singleResult();
    try (var con = f.connection(); var s = con.createStatement()) {
      s.execute("CREATE FUNCTION fail_classified_commit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'classified-final-commit'; END $$");
      s.execute("CREATE CONSTRAINT TRIGGER classified_commit_fault AFTER INSERT ON MZO_HUMAN_RECEIPT DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fail_classified_commit()");
    }
    try {
      var failure = assertThrows(RuntimeException.class, () -> f.send(c));
      StringBuilder causes = new StringBuilder();
      for (Throwable error = failure; error != null; error = error.getCause()) causes.append(error.getMessage());
      assertTrue(causes.toString().contains("classified-final-commit"), "Must reach deferred database commit");
      assertNotNull(f.engine.getTaskService().createTaskQuery().taskId(task.getId()).singleResult());
      assertEquals(0, f.countReceipts()); assertEquals(0, f.count("POINTER")); assertEquals(0, f.count("LINK"));
      assertNull(f.engine.getRuntimeService().getVariable(task.getProcessInstanceId(), "decisao_auditor"));
    } finally {
      try (var con = f.connection(); var s = con.createStatement()) {
        s.execute("DROP TRIGGER classified_commit_fault ON MZO_HUMAN_RECEIPT"); s.execute("DROP FUNCTION fail_classified_commit()");
      }
    }
    assertEquals("committed", Jcs.object(Jcs.parse(f.send(c))).get("status"));
  }
}
