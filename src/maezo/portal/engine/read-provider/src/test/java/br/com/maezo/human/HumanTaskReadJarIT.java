package br.com.maezo.human;

import static br.com.maezo.human.readprovider.TestRecords.*;
import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.readprovider.ProviderFixture;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.security.Signature;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import org.cibseven.bpm.engine.ProcessEngine;
import org.cibseven.bpm.engine.ProcessEngineConfiguration;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.api.function.Executable;

/**
 * H1 (D-N) end to end on a REAL engine: CIB Seven on PostgreSQL 17 with the shipped human and
 * portal-read DDL, the {@link PortalReadPlugin} discovered through {@code ServiceLoader} from the
 * BUILT provider JAR, the real SP-OP-ESCALATION-001 BPMN and escalation_routing DMN, and signed
 * envelopes through the plugin's own routes. The approver's admission carries the {@code human}
 * block for {@code UT_TratarEscalonamento}; the catalog, both memberships and the task resource are
 * published through {@code qualifyPublication}; then the Q2 reads run.
 *
 * <p>Proves: the human task (candidate group chosen by the DMN) is visible in the team queue of the
 * principal of THAT group and absent from the queue of the principal of another group; the task read
 * runs the provider's {@code verifyClassification}/{@code verifyIdentityPolicy}; a staff-only
 * admission refuses the resource publication; a resource whose classification the approver did not
 * admit is refused.
 */
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class HumanTaskReadJarIT {
  static final String DEFINITION = "SP-OP-ESCALATION-001", TASK = "UT_TratarEscalonamento";
  static final String GROUP = "atendimento-humano", OTHER_GROUP = "enfermagem-triagem";
  ProviderFixture f;
  String engineSchema, definitionId, deploymentId;
  ProcessEngine engine;
  ProcessEngineConfigurationImpl config;
  PortalReadPlugin plugin;
  Map<String, Object> artifact, entry, human;
  Member inGroup, outside;
  final String context = Base64.getUrlEncoder().withoutPadding().encodeToString(new byte[32]);
  long admissionRevision = 1;

  @BeforeAll
  void start() throws Exception {
    f = new ProviderFixture();
    engineSchema = "rp_engine_" + f.suffix;
    String url = f.adminUrl + (f.adminUrl.contains("?") ? "&" : "?") + "currentSchema="
        + engineSchema;
    try (var c = f.admin(); var s = c.createStatement()) {
      s.execute("CREATE SCHEMA " + engineSchema);
    }
    try (Connection c = DriverManager.getConnection(url, f.adminUser, f.adminPassword);
         var s = c.createStatement()) {
      for (String ddl : List.of("human-schema-postgres.sql", "portal-read-schema-postgres.sql"))
        try (var in = PortalReadPlugin.class.getResourceAsStream("/" + ddl)) {
          s.execute(new String(Objects.requireNonNull(in, ddl).readAllBytes(),
              StandardCharsets.UTF_8));
        }
      s.execute("INSERT INTO MZO_HUMAN_TENANT VALUES('" + TENANT + "',0)");
    }
    // Boot admission: staff-only (the engine boots before anything is deployed).
    f.install(f.publicationAdmission(admissionRevision));
    plugin = new PortalReadPlugin();
    config = (ProcessEngineConfigurationImpl) ProcessEngineConfiguration
                 .createStandaloneProcessEngineConfiguration()
                 .setProcessEngineName(f.engine)
                 .setJdbcDriver("org.postgresql.Driver")
                 .setJdbcUrl(url)
                 .setJdbcUsername(f.adminUser)
                 .setJdbcPassword(f.adminPassword)
                 .setDatabaseSchemaUpdate(ProcessEngineConfiguration.DB_SCHEMA_UPDATE_TRUE)
                 .setJobExecutorActivate(false)
                 .setHistory("none");
    config.setMetricsEnabled(false);
    config.setProcessEnginePlugins(new ArrayList<>(List.of(plugin)));
    engine = config.buildProcessEngine();
    Path repo = Path.of(ProviderFixture.required("MAEZO_PORTAL_READ_IT_REPO"));
    var deploy = engine.getRepositoryService().createDeployment().tenantId(TENANT);
    for (String name : List.of("bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn",
             "dmn/escalation_routing.dmn"))
      deploy.addInputStream(name, new java.io.ByteArrayInputStream(
          Files.readAllBytes(repo.resolve("spec/processes").resolve(name))));
    deploymentId = deploy.deploy().getId();
    definitionId = engine.getRepositoryService().createProcessDefinitionQuery()
                       .deploymentId(deploymentId).processDefinitionKey(DEFINITION)
                       .singleResult().getId();
    buildCatalog();
    human = humanBlock(entryClassification());
    // The approver admits the deployed task: a NEW revision supersedes the boot one.
    f.install(withHuman(withCatalog(f.publicationAdmission(++admissionRevision)), human));
    publish("catalog-designate", plainProvenance(CATALOG + ":1", now().minusSeconds(1),
        f.until.minusSeconds(3600)), designation());
    inGroup = member("staff-no-grupo", f.until.minusSeconds(120));
    outside = member("staff-outro-grupo", f.until.minusSeconds(120));
    for (var m : List.of(inGroup, outside)) {
      var groups = List.of(m == inGroup ? GROUP : OTHER_GROUP);
      f.putMembership(TENANT, m, groups);
      nativeHuman(m, groups);
      var projection = projection(m, groups);
      publish("membership",
          membershipProvenance(TENANT, projection, now().minusSeconds(1), 300), projection);
    }
  }

  @AfterAll
  void stop() throws Exception {
    if (engine != null)
      engine.close();
    if (f != null) {
      try (var c = f.admin(); var s = c.createStatement()) {
        s.execute("DROP SCHEMA IF EXISTS " + engineSchema + " CASCADE");
      }
      f.close();
    }
  }

  // ------------------------------------------------------------------------------ fixtures

  Connection engineDb() throws SQLException {
    var c = f.admin();
    try (var s = c.createStatement()) {
      s.execute("SET search_path = " + engineSchema);
    }
    return c;
  }

  long authorityRevision() throws SQLException {
    try (var c = engineDb(); var s = c.createStatement();
         var r = s.executeQuery("SELECT REV_ FROM MZO_HUMAN_TENANT WHERE TENANT_='" + TENANT + "'")) {
      r.next();
      return r.getLong(1);
    }
  }

  void nativeHuman(Member m, List<String> groups) throws SQLException {
    try (var c = engineDb(); var q = c.prepareStatement(
             "INSERT INTO MZO_HUMAN_PRINCIPAL VALUES(?,?,?,?,1,true,?,?)")) {
      q.setString(1, TENANT);
      q.setString(2, m.principal());
      q.setString(3, m.issuer());
      q.setString(4, m.subject());
      q.setLong(5, f.until.getEpochSecond());
      q.setString(6, new String(Jcs.canonical(groups), StandardCharsets.UTF_8));
      q.executeUpdate();
    }
  }

  static Map<String, Object> pin(String name) {
    return record("artifact_ref", "PUBLIC_SYNTHETIC-" + name, "digest",
        Jcs.digest(("PUBLIC_SYNTHETIC " + name).getBytes(StandardCharsets.UTF_8)));
  }

  void buildCatalog() throws Exception {
    var def = engine.getRepositoryService().createProcessDefinitionQuery()
                  .processDefinitionId(definitionId).singleResult();
    var compiled = PortalReadCommand.COMPILED.get(def.getKey() + "/" + TASK);
    byte[] form = ("PUBLIC_SYNTHETIC read form " + compiled.get(0)).getBytes(StandardCharsets.UTF_8);
    var d = engine.getRepositoryService().createDecisionDefinitionQuery()
                .decisionDefinitionKey("escalation_routing").singleResult();
    var domain = record("kind", "dmn", "groups",
        new ArrayList<>(List.of("plantao-clinico", OTHER_GROUP, GROUP)), "dmn_definition_id",
        d.getId(), "dmn_definition_key", d.getKey(), "dmn_definition_version",
        Integer.toString(d.getVersion()), "dmn_resource_digest",
        Jcs.digest(engine.getRepositoryService().getDecisionModel(d.getId()).readAllBytes()));
    entry = record("process_definition_id", definitionId, "process_definition_key", def.getKey(),
        "process_definition_version", Integer.toString(def.getVersion()),
        "process_definition_digest",
        Jcs.digest(engine.getRepositoryService().getProcessModel(definitionId).readAllBytes()),
        "task_definition_key", TASK, "form_key", compiled.get(0), "form_version", "1",
        "form_digest", Jcs.digest(form), "form_source_status", compiled.get(1), "allowed_inputs",
        new ArrayList<>(compiled.subList(2, compiled.size())), "required_roles",
        new ArrayList<>(List.of("atendimento")), "subject_policy", pin("subject-policy"),
        "consent_policy", pin("consent-policy"), "resource_policy", pin("resource-policy"),
        "disclosure_policy", pin("disclosure-policy"), "opaque_task_id_policy",
        pin("opaque-task-id-policy"), "group_domain", domain);
    var policies = new ArrayList<Object>();
    for (String name : List.of("consent-policy", "disclosure-policy", "opaque-task-id-policy",
             "resource-policy", "subject-policy"))
      policies.add(record("artifact_ref", pin(name).get("artifact_ref"), "digest",
          pin(name).get("digest"), "bytes_base64", Base64.getEncoder().encodeToString(
              ("PUBLIC_SYNTHETIC " + name).getBytes(StandardCharsets.UTF_8))));
    artifact = record("schema", "portal-read-catalog.v1", "catalog_ref", CATALOG, "publisher_ref",
        PUBLISHER, "entries", new ArrayList<>(List.of(entry)), "policies", policies, "forms",
        new ArrayList<>(List.of(record("artifact_ref", compiled.get(0), "digest",
            Jcs.digest(form), "bytes_base64", Base64.getEncoder().encodeToString(form)))),
        "deployment_receipt_ref", deploymentId, "deployment_receipt_digest",
        Jcs.digest(Jcs.canonical(List.of(entry))));
  }

  Map<String, Object> withCatalog(Map<String, Object> admission) {
    @SuppressWarnings("unchecked")
    var catalog = (Map<String, Object>) admission.get("catalog");
    catalog.put("catalog_digest", Jcs.digest(Jcs.canonical(artifact)));
    return admission;
  }

  Map<String, Object> designation() {
    return record("catalog_ref", CATALOG, "catalog_revision", "1", "catalog_digest",
        Jcs.digest(Jcs.canonical(artifact)), "catalog_artifact_base64",
        Base64.getEncoder().encodeToString(Jcs.canonical(artifact)), "deployment_receipt_ref",
        deploymentId, "deployment_receipt_digest", artifact.get("deployment_receipt_digest"),
        "valid_until", time(f.until.minusSeconds(3600)));
  }

  static Map<String, Object> entryClassification() {
    return record("classification_ref", "PUBLIC_SYNTHETIC-classification",
        "classification_digest", Jcs.digest("PUBLIC_SYNTHETIC classification".getBytes(
            StandardCharsets.UTF_8)),
        "policy_ref", pin("disclosure-policy").get("artifact_ref"), "policy_digest",
        pin("disclosure-policy").get("digest"), "projection", "full_task_detail.v1",
        "fields_digest", Jcs.digest("PUBLIC_SYNTHETIC fields".getBytes(StandardCharsets.UTF_8)));
  }

  Map<String, Object> humanBlock(Map<String, Object> classification) {
    return record("entries", new ArrayList<>(List.of(record("process_definition_id", definitionId,
        "task_definition_key", TASK, "classification", classification, "identity_policy",
        pin("opaque-task-id-policy"), "task_id_format", "decimal", "candidate_groups",
        new ArrayList<>(List.of("plantao-clinico", OTHER_GROUP, GROUP)), "user_candidates",
        "refused"))));
  }

  /** A live escalation task whose candidate group the REAL DMN picks (atendimento-humano). */
  String task() {
    var decision = engine.getRepositoryService().createDecisionDefinitionQuery()
                       .decisionDefinitionKey("escalation_routing").singleResult();
    var result = engine.getDecisionService().evaluateDecisionTableById(decision.getId(),
        Map.of("motivo_categoria", "falha_tecnica", "severidade", "leve")).getSingleResult()
        .getEntryMap();
    var pi = engine.getRuntimeService().createProcessInstanceById(definitionId)
                 .setVariable("roteamento", new HashMap<>(result))
                 .startBeforeActivity(TASK).execute();
    String id = engine.getTaskService().createTaskQuery().processInstanceId(pi.getId())
                    .singleResult().getId();
    var groups = engine.getTaskService().getIdentityLinksForTask(id).stream()
                     .map(l -> l.getGroupId()).filter(Objects::nonNull).toList();
    assertEquals(List.of(GROUP), groups, "the DMN routes this escalation to " + GROUP);
    return id;
  }

  Map<String, Object> resourcePayload(String id, Map<String, Object> classification) throws Exception {
    long revision = config.getCommandExecutorTxRequired().execute(
        c -> (long) c.getTaskManager().findTaskById(id).getRevision());
    try (var c = engineDb(); var p = c.prepareStatement(
             "INSERT INTO MZO_HUMAN_EVIDENCE VALUES(?,?,1,'evidence-'||?,?,?,?) "
             + "ON CONFLICT(TENANT_,TASK_) DO NOTHING")) {
      p.setString(1, TENANT);
      p.setString(2, id);
      p.setString(3, id); // one evidence per task: the read ceilings key it by ref
      p.setString(4, "a".repeat(64));
      p.setLong(5, f.until.getEpochSecond());
      p.setString(6, definitionId);
      p.executeUpdate();
    }
    var c = new HashMap<>(classification);
    c.put("valid_until", time(f.until.minusSeconds(3600)));
    return record("task_id", id, "process_definition_id", definitionId,
        "process_definition_digest", entry.get("process_definition_digest"),
        "observed_task_revision", Long.toString(revision), "evidence_ref", "evidence-" + id,
        "evidence_revision", "1", "evidence_digest", "a".repeat(64), "resource_ref",
        "resource-" + id, "resource_revision", "1", "resource_digest", "b".repeat(64),
        "resource_policy", pin("resource-policy"), "classification", sorted(c),
        "required_subject_bindings", new ArrayList<>(), "required_consent_scopes",
        new ArrayList<>(), "positive_grants", grants(id), "read_only_evidence", null,
        "state", "complete", "valid_until", time(f.until.minusSeconds(3600)));
  }

  /**
   * The resource decision grants BOTH principals: what keeps the other group out is the candidate
   * group of the real task, not a missing grant.
   */
  List<Object> grants(String task) {
    var out = new ArrayList<Object>();
    for (var m : List.of(inGroup, outside))
      out.add(record("issuer", m.issuer(), "subject", m.subject(), "principal_ref", m.principal(),
          "membership_revision", Long.toString(m.revision()), "consent_scopes", new ArrayList<>(),
          "decision_receipt_ref", "PUBLIC_SYNTHETIC-decision-" + task + "-" + m.principal(), "decision_digest",
          "d".repeat(64), "valid_until", time(f.until.minusSeconds(3600))));
    return out;
  }

  static Map<String, Object> sorted(Map<String, Object> m) {
    var out = new java.util.TreeMap<String, Object>();
    out.putAll(m);
    return out;
  }

  // ------------------------------------------------------------------------------ envelopes

  byte[] signed(Map<String, Object> request, boolean publication) throws Exception {
    long now = Instant.now().getEpochSecond();
    var e = record("schema", "portal-read-envelope.v1", "purpose",
        publication ? "portal-read-publication" : "portal-task-read", "algorithm", "Ed25519",
        "audience", "portal-staff-read", "issuer", publication ? PUBLISHER : "portal-staff",
        "tenant", TENANT, "key_id", publication ? "publisher" : "reader", "issued_at",
        Long.toString(now), "expires_at", Long.toString(now + 30), "digest",
        Jcs.digest(Jcs.canonical(request)), "request", request);
    var sign = Signature.getInstance("Ed25519");
    sign.initSign((publication ? f.publisher : f.reader).getPrivate());
    sign.update(Jcs.canonical(e));
    e.put("signature", Base64.getUrlEncoder().withoutPadding().encodeToString(sign.sign()));
    return Jcs.canonical(e);
  }

  byte[] publish(String kind, Map<String, Object> source, Map<String, Object> payload)
      throws Exception {
    return plugin.execute(signed(f.publication(kind, source, payload, authorityRevision()), true),
        ProviderFixture.PUBLISHER_PEER, "publications");
  }

  byte[] publishResource(Map<String, Object> payload) throws Exception {
    return publish("resource", resourceProvenance(TENANT, payload, now().minusSeconds(1), 300),
        payload);
  }

  Map<String, Object> read(String operation, Map<String, Object> extras) throws Exception {
    byte[] nonce = new byte[32];
    new SecureRandom().nextBytes(nonce);
    var r = record("schema", "portal-engine-read.v1", "operation", operation, "request_id",
        Base64.getUrlEncoder().withoutPadding().encodeToString(nonce), "read_context_id",
        context, "scope", scope(), "engine_name", f.engine, "database_incarnation",
        f.incarnation, "read_deployment_ref", f.deployment, "read_deployment_digest",
        f.deploymentDigest);
    r.putAll(extras);
    return Jcs.object(Jcs.parse(plugin.execute(signed(r, false), "a".repeat(64), operation)));
  }

  Map<String, Object> principal(Member m, List<String> groups) {
    return record("schema_version", "1", "principal_ref", m.principal(), "issuer", m.issuer(),
        "subject", m.subject(), "tenant", TENANT, "membership_revision",
        Long.toString(m.revision()), "memberships",
        new ArrayList<>(List.of(record("membership_ref", "staff-membership", "roles",
            new ArrayList<>(List.of("atendimento")), "groups", new ArrayList<>(groups)))),
        "session_ref", "session-" + m.principal(), "authenticated_at", time(now()),
        "subject_bindings", new ArrayList<>());
  }

  @SuppressWarnings("unchecked")
  List<Object> team(Member m, List<String> groups) throws Exception {
    var catalog = (Map<String, Object>) read("catalog", record("anchor", f.anchor())).get("value");
    var value = (Map<String, Object>) read("discover", record("principal", principal(m, groups),
        "expectation", catalog.get("expectation"), "queue", "team", "limit", "25",
        "after_task_id", null)).get("value");
    return (List<Object>) value.get("task_ids");
  }

  void refused(Executable action) {
    Rejected r = assertThrows(Rejected.class, action);
    assertEquals(503, r.status, r.code);
  }

  // --------------------------------------------------------------------------------- tests

  @Test
  @SuppressWarnings("unchecked")
  void theHumanTaskIsVisibleToItsGroupAndInvisibleToAnother() throws Exception {
    String id = task();
    assertTrue(ENGINE_ID.matcher(id).matches(), "engine ids are the admitted format: " + id);
    long before = authorityRevision();
    publishResource(resourcePayload(id, entryClassification()));
    assertEquals(before + 1, authorityRevision());
    assertTrue(team(inGroup, List.of(GROUP)).contains(id), "visible to " + GROUP);
    assertFalse(team(outside, List.of(OTHER_GROUP)).contains(id), "invisible to " + OTHER_GROUP);
    // The task read runs verifyClassification + verifyIdentityPolicy of the provider.
    var t = (Map<String, Object>) read("task", record("anchor", f.anchor(), "task_id", id))
                .get("value");
    var snapshot = (Map<String, Object>) ((Map<String, Object>) t.get("task")).get("snapshot");
    assertEquals(id, snapshot.get("task_id"));
    assertEquals(List.of(GROUP), snapshot.get("eligible_candidate_groups"));
  }

  @Test
  void aClassificationTheApproverDidNotAdmitIsRefusedAndChangesNothing() throws Exception {
    String id = task();
    var forged = entryClassification();
    forged.put("fields_digest", "0".repeat(64));
    long before = authorityRevision();
    refused(() -> publishResource(resourcePayload(id, forged)));
    assertEquals(before, authorityRevision());
    publishResource(resourcePayload(id, entryClassification()));
    assertEquals(before + 1, authorityRevision());
  }

  @Test
  void aStaffOnlyAdmissionRefusesTheResourceAndTheTaskRead() throws Exception {
    String id = task();
    publishResource(resourcePayload(id, entryClassification()));
    // Supersede with a staff-only revision (same catalog): no human block, no resource publisher.
    f.install(withCatalog(f.publicationAdmission(++admissionRevision)));
    String other = task();
    try {
      refused(() -> publishResource(resourcePayload(other, entryClassification())));
      refused(() -> read("task", record("anchor", f.anchor(), "task_id", id)));
    } finally {
      f.install(withHuman(withCatalog(f.publicationAdmission(++admissionRevision)), human));
    }
    // A live task with no resource publication makes the queue unavailable (fail-closed), never
    // a shorter list; its resource under the human revision restores it.
    refused(() -> team(inGroup, List.of(GROUP)));
    publishResource(resourcePayload(other, entryClassification()));
    assertTrue(team(inGroup, List.of(GROUP)).containsAll(List.of(id, other)),
        "the human revision restores it");
  }

  @Test
  void anIdentityPolicyOutsideTheAdmittedGroupsRefusesTheTaskRead() throws Exception {
    String id = task();
    publishResource(resourcePayload(id, entryClassification()));
    var narrow = humanBlock(entryClassification());
    @SuppressWarnings("unchecked")
    var e = (Map<String, Object>) ((List<Object>) narrow.get("entries")).get(0);
    e.put("candidate_groups", new ArrayList<>(List.of(OTHER_GROUP)));
    f.install(withHuman(withCatalog(f.publicationAdmission(++admissionRevision)), narrow));
    try {
      refused(() -> read("task", record("anchor", f.anchor(), "task_id", id)));
    } finally {
      f.install(withHuman(withCatalog(f.publicationAdmission(++admissionRevision)), human));
    }
    read("task", record("anchor", f.anchor(), "task_id", id));
  }

  /** The standalone default (DbIdGenerator); the Dockerfile.human image generates UUIDs (C1, 25/09). */
  static final java.util.regex.Pattern ENGINE_ID =
      java.util.regex.Pattern.compile("[1-9][0-9]{0,18}");
}
