package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.Timestamp;
import java.time.*;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;

/** Actual CIB read command. Tenant lock serializes publishers, never all ACT_* writers. */
final class PortalReadCommand implements Command<PortalReadCommand.Result> {
  static final class Result {
    final byte[] bytes;
    final Runnable local;
    boolean committed;
    Result(byte[] bytes, Runnable local) {
      this.bytes = bytes;
      this.local = local;
    }
    byte[] bytes() {
      if (!committed)
        throw unavailable();
      local.run();
      return bytes.clone();
    }
  }
  final PortalReadTrust trust;
  final PortalReadEnvelope.Verified envelope;
  final PortalReadTrust.Admission admission;
  final PortalReadTrust.NativeKeySet keys;
  private Runnable assignmentGuard;
  PortalReadStore db;
  CommandContext context;
  Instant last;
  final List<Map<String, Object>> ceilings = new ArrayList<>();
  final List<Runnable> finalReads = new ArrayList<>();
  PortalReadCommand(PortalReadTrust trust, PortalReadEnvelope.Verified envelope,
      PortalReadTrust.Admission admission, PortalReadTrust.NativeKeySet keys) {
    this.trust = trust;
    this.envelope = envelope;
    this.admission = admission;
    this.keys = keys;
  }
  /** Internal constraint facts only: no read envelope or assignment permit is minted. */
  PortalReadCommand(PortalReadTrust trust, PortalReadTrust.Admission admission, Runnable guard) {
    this.trust=trust;this.admission=admission;this.envelope=null;this.keys=null;this.assignmentGuard=guard;
  }
  record AssignmentFacts(Map<String,Object> facts, Runnable current) {}
  AssignmentFacts assignmentConstraints(CommandContext context, Map<String,Object> principal,
      Map<String,Object> binding, Map<String,Object> designation) {
    this.context=context;db=new PortalReadStore(context,trust,admission.statementTimeoutSeconds());
    guard();ceiling("native_admission",admission.providerRef(),admission.providerRevision(),admission.capabilityDigest(),admission.observedAt(),admission.validUntil());
    // The outer assignment command already holds the tenant lock. Retain original
    // complete tuple/source/catalog/classification/identity policy checks.
    var anchor=record("scope",trust.scope,"catalog_ref",binding.get("catalog_ref"),"publisher_ref",
      str(map(Jcs.parse(java.util.Base64.getDecoder().decode(str(assignmentCatalog(binding),"bytes_base64")))),"publisher_ref"));
    State state=state(str(designation,"task_id"),anchor,str(principal,"principal_ref"));
    if(!state.resource.get("resource_ref").equals(designation.get("resource_ref"))
      ||!state.resource.get("resource_revision").equals(designation.get("resource_revision"))
      ||!state.resource.get("resource_digest").equals(designation.get("resource_digest")))throw conflict();
    var authority=authorityState(state,principal);guard();
    return new AssignmentFacts(record("resource",state.resource,"grant",authority.get("matching_resource_grant"),"valid_until",time(until())), () -> guard());
  }
  private Map<String,Object> assignmentCatalog(Map<String,Object> binding) {
    // Current authenticated catalog designation is verified by catalogRow below;
    // this preliminary publisher lookup supplies only the closed lookup anchor.
    var row=db.catalog(str(binding,"catalog_ref"));if(row==null)throw unavailable();
    var artifact=validate("artifactcatalog",PortalReadStore.json(row.get("artifact_")));
    return record("bytes_base64",java.util.Base64.getEncoder().encodeToString(Jcs.canonical(artifact)));
  }
  Instant guard() {
    Instant now = Instant.now().truncatedTo(java.time.temporal.ChronoUnit.MICROS);
    if (last != null && now.isBefore(last))
      throw unavailable();
    last = now;
    admission.requireCurrent();
    if (now.isBefore(admission.observedAt()) || !now.isBefore(admission.validUntil()))
      throw unavailable();
    if (assignmentGuard != null) assignmentGuard.run();
    else { envelope.current(now); keys.requireCurrent(); }
    for (var c : ceilings) {
      if (time(c.get("observed_at")).isAfter(now))
        throw unavailable();
      current(now, c.get("valid_until"));
    }
    return now;
  }
  void ceiling(
      String kind, String ref, String revision, String digest, Instant observed, Instant until) {
    addCeiling(record("kind", kind, "source_ref", ref, "source_revision", revision, "source_digest",
        digest, "observed_at", time(observed), "valid_until", time(until)));
  }
  void addCeiling(Map<String, Object> c) {
    validate("ceiling", c);
    for (int i = 0; i < ceilings.size(); i++) {
      var old = ceilings.get(i);
      if (c.get("kind").equals("membership")
          && str(c, "source_ref").startsWith("native-human:")
          && old.get("kind").equals(c.get("kind"))
          && old.get("source_ref").equals(c.get("source_ref"))) {
        for (String field : List.of("source_revision", "source_digest", "valid_until"))
          if (!old.get(field).equals(c.get(field)))
            throw conflict();
        // CA is verified after the fresh membership read. Retain the original observation,
        // in either import order, without renewing its facts or deadline.
        if (time(c.get("observed_at")).isBefore(time(old.get("observed_at"))))
          ceilings.set(i, copy(c));
        return;
      }
      if (ceilingId(old).equals(ceilingId(c))) {
        if (!old.equals(c))
          throw unavailable();
        return;
      }
    }
    ceilings.add(copy(c));
  }
  void source(String kind, Map<String, Object> source) {
    validate("source", source);
    admission.verifySource(kind, source);
    ceiling(kind.equals("catalog-designate") ? "catalog" : kind, str(source, "source_ref"),
        str(source, "source_revision"), str(source, "source_digest"),
        time(source.get("observed_at")), time(source.get("valid_until")));
  }
  Instant until() {
    guard();
    return ceilings.stream()
        .map(c -> time(c.get("valid_until")))
        .min(Instant::compareTo)
        .orElseThrow(PortalReadModels::unavailable);
  }
  Map<String, Object> binding() {
    var r = envelope.request();
    return record("scope", trust.scope, "engine_name", trust.engine, "database_incarnation",
        trust.incarnation, "read_deployment_ref", trust.deployment, "read_deployment_digest",
        trust.deploymentDigest, "runtime_admission_generation", admission.generation(),
        "read_context_id", r.get("read_context_id"), "requester", envelope.requester());
  }
  void base() {
    Instant now = guard();
    var key = keys.current();
    key.current(now);
    ceiling("native_admission", admission.providerRef(), admission.providerRevision(),
        admission.capabilityDigest(), admission.observedAt(), admission.validUntil());
    ceiling("requester_key", envelope.key().id(), "0", envelope.key().fingerprint(),
        envelope.key().notBefore(), envelope.key().notAfter());
    ceiling("request_envelope", "request:" + str(envelope.request(), "request_id"), "0", envelope.digest(),
        envelope.issued(), envelope.expires());
    ceiling("native_key", key.id, key.generation, key.digest, key.notBefore, key.notAfter);
  }

  public Result execute(CommandContext context) {
    this.context = context;
    db = new PortalReadStore(context, trust, admission.statementTimeoutSeconds());
    base();
    db.lockTenant();
    if (db.revoked(envelope.key().fingerprint()))
      throw denied();
    var r = envelope.request();
    String op = str(r, "operation");
    Map<String, Object> value;
    switch (op) {
      case "catalog" -> {
        var cat = catalog(obj(r, "anchor"));
        String pin = hash(cat);
        finalReads.add(() -> {
          if (!hash(catalog(obj(r, "anchor"))).equals(pin))
            throw conflict();
        });
        value = catalogValue(cat);
      }
      case "discover" -> value = discover(r);
      case "task" -> {
        State state = state(str(r, "task_id"), obj(r, "anchor"), null);
        Instant observed = guard();
        var task = task(state, observed, until());
        var ct = mint("task", task, state, null, null, null);
        retain(state, obj(r, "anchor"), null);
        value = record("catalog", catalogValue(state.catalog), "task", task, "task_continuity", ct);
      }
      case "authority", "disclosure" -> value = continued(r, op.equals("disclosure"));
      default -> throw invalid();
    }
    Instant observed = guard(), valid = until();
    var result = record("schema", "portal-engine-read-result.v1", "request_digest",
        envelope.digest(), "scope", trust.scope, "engine_name", trust.engine,
        "database_incarnation", trust.incarnation, "read_deployment_ref", trust.deployment,
        "read_deployment_digest", trust.deploymentDigest, "operation", op, "source_observed_at",
        time(observed), "valid_until", time(valid), "value", value);
    Result out = new Result(bounded(result), () -> {
      guard();
      for (var c : ceilings) current(Instant.now(), c.get("valid_until"));
    });
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING, ignored -> {
      guard();
      for (Runnable check : finalReads) check.run();
      if (db.revoked(envelope.key().fingerprint()))
        throw unavailable();
      guard();
    });
    context.getTransactionContext().addTransactionListener(
        TransactionState.COMMITTED, ignored -> out.committed = true);
    return out;
  }
  Map<String, Object> catalog(Map<String, Object> anchor) {
    validate("anchor", anchor);
    if (!anchor.get("scope").equals(trust.scope))
      throw denied();
    return catalogRow(anchor, db.catalog(str(anchor, "catalog_ref")));
  }
  Map<String, Object> catalogRow(Map<String, Object> anchor, Map<String, Object> row) {
    validate("anchor", anchor);
    if (!anchor.get("scope").equals(trust.scope))
      throw denied();
    if (Boolean.TRUE.equals(row.get("revoked_")))
      throw unavailable();
    if (!anchor.get("publisher_ref").equals(row.get("publisher_")))
      throw unavailable();
    var artifact = validate("artifactcatalog", PortalReadStore.json(row.get("artifact_")));
    if (!hash(artifact).equals(row.get("digest_"))
        || !artifact.get("catalog_ref").equals(anchor.get("catalog_ref"))
        || !artifact.get("publisher_ref").equals(anchor.get("publisher_ref")))
      throw unavailable();
    admission.verifyCatalog(artifact);
    verifyCatalog(artifact);
    var src = PortalReadStore.json(row.get("source_"));
    db.proof((String) row.get("publication_"), "catalog-designate", src);
    source("catalog-designate", src);
    Instant valid = ((Timestamp) row.get("valid_until_")).toInstant();
    ceiling("catalog", str(anchor, "catalog_ref"), row.get("revision_").toString(),
        (String) row.get("digest_"), time(src.get("observed_at")), valid);
    return record("anchor", anchor, "catalog_revision", row.get("revision_").toString(),
        "catalog_digest", row.get("digest_"), "publication_id", row.get("publication_"), "source",
        src, "valid_until", time(valid), "artifact", artifact);
  }
  Map<String, Object> designation(Map<String, Object> cat) {
    var d = copy(cat);
    d.remove("artifact");
    return d;
  }
  Map<String, Object> catalogValue(Map<String, Object> cat) {
    return record("expectation",
        record("anchor", cat.get("anchor"), "catalog_revision", cat.get("catalog_revision"),
            "catalog_digest", cat.get("catalog_digest"), "source_observed_at",
            obj(cat, "source").get("observed_at"), "valid_until", time(until())),
        "catalog_artifact_base64",
        Base64.getEncoder().encodeToString(bounded(cat.get("artifact"))));
  }
  void verifyCatalog(Map<String, Object> artifact) {
    Set<String> ids = new HashSet<>(), policies = new HashSet<>(), forms = new HashSet<>();
    for (Object item : list(artifact.get("entries"))) {
      var e = validate("catalogentry", item);
      String id = str(e, "process_definition_id");
      if (!ids.add(id + "\0" + str(e, "task_definition_key"))
          || list(e.get("required_roles")).isEmpty())
        throw unavailable();
      compiled(e);
      var repository = context.getProcessEngineConfiguration().getRepositoryService();
      var definition =
          repository.createProcessDefinitionQuery().processDefinitionId(id).singleResult();
      if (definition == null || !trust.scope.get("tenant").equals(definition.getTenantId())
          || !e.get("process_definition_key").equals(definition.getKey())
          || number(e.get("process_definition_version")) != definition.getVersion())
        throw unavailable();
      resourceDigest(repository.getProcessModel(id), str(e, "process_definition_digest"));
      var domain = obj(e, "group_domain");
      if (domain.get("kind").equals("dmn")) {
        var decision = repository.createDecisionDefinitionQuery()
                           .decisionDefinitionId(str(domain, "dmn_definition_id"))
                           .singleResult();
        if (decision == null || !trust.scope.get("tenant").equals(decision.getTenantId())
            || !decision.getKey().equals(domain.get("dmn_definition_key"))
            || decision.getVersion() != number(domain.get("dmn_definition_version")))
          throw unavailable();
        resourceDigest(
            repository.getDecisionModel(decision.getId()), str(domain, "dmn_resource_digest"));
      }
      for (String k : List.of("subject_policy", "consent_policy", "resource_policy",
               "disclosure_policy", "opaque_task_id_policy"))
        policies.add(hash(e.get(k)));
      forms.add(hash(record("artifact_ref", e.get("form_key"), "digest", e.get("form_digest"))));
    }
    verifyArtifacts(list(artifact.get("policies")), policies);
    verifyArtifacts(list(artifact.get("forms")), forms);
  }
  static void verifyArtifacts(List<Object> artifacts, Set<String> expected) {
    Set<String> actual = new HashSet<>();
    for (Object value : artifacts) {
      var a = validate("artifact", value);
      if (!Jcs.digest(b64(a.get("bytes_base64"), false, -1)).equals(a.get("digest"))
          || !actual.add(
              hash(record("artifact_ref", a.get("artifact_ref"), "digest", a.get("digest")))))
        throw unavailable();
    }
    if (!actual.equals(expected))
      throw unavailable();
  }
  static void resourceDigest(InputStream stream, String expected) {
    if (stream == null)
      throw unavailable();
    try (stream) {
      if (!resourceMatches(stream, expected))
        throw unavailable();
    } catch (IOException ex) {
      throw unavailable();
    }
  }
  record State(Map<String, Object> nativeState, Map<String, Object> catalog,
      Map<String, Object> entry, Map<String, Object> resource, Map<String, Object> membership,
      Map<String, Object> row) {}
  State state(String id, Map<String, Object> anchor, String principal) {
    var tuple = db.tuple(id, str(anchor, "catalog_ref"), principal);
    if (tuple == null)
      throw absent();
    if (((Number) tuple.get("suspension_state_")).intValue() != 1)
      throw absent();
    if (((Number) tuple.get("resource_proof_")).longValue() != 1)
      throw unavailable();
    if (tuple.get("resource_") == null || tuple.get("evidence_ref_") == null
        || tuple.get("catalog_revision_") == null)
      throw unavailable();
    // All mutable designation/resource/membership/link facts below come from this
    // SAME tuple SELECT statement snapshot, not a second catalog query or entity cache.
    var cat = catalogRow(anchor,
        record("publisher_", tuple.get("catalog_publisher_"), "artifact_",
            tuple.get("catalog_artifact_"), "digest_", tuple.get("catalog_digest_"), "source_",
            tuple.get("catalog_source_"), "valid_until_", tuple.get("catalog_until_"), "revision_",
            tuple.get("catalog_revision_"), "publication_", tuple.get("catalog_publication_"),
            "revoked_", tuple.get("catalog_revoked_")));
    var resource = validate("resource", PortalReadStore.json(tuple.get("resource_")));
    var source = PortalReadStore.json(tuple.get("resource_source_"));
    db.proof((String) tuple.get("resource_publication_"), "resource", source);
    source("resource", source);
    if (!resource.get("state").equals("complete"))
      throw unavailable();
    var entries = list(obj(cat, "artifact").get("entries"))
                      .stream()
                      .map(PortalReadModels::map)
                      .filter(e
                          -> e.get("process_definition_id").equals(tuple.get("proc_def_id_"))
                              && e.get("task_definition_key").equals(tuple.get("task_def_key_")))
                      .toList();
    if (entries.size() != 1)
      throw unavailable();
    var entry = entries.get(0);
    if (!resource.get("task_id").equals(id)
        || !resource.get("process_definition_id").equals(tuple.get("proc_def_id_"))
        || !resource.get("process_definition_digest").equals(entry.get("process_definition_digest"))
        || !resource.get("observed_task_revision").equals(tuple.get("rev_").toString())
        || !resource.get("evidence_ref").equals(tuple.get("evidence_ref_"))
        || !resource.get("evidence_revision").equals(tuple.get("evidence_revision_").toString())
        || !resource.get("evidence_digest").equals(tuple.get("evidence_digest_"))
        || !resource.get("process_definition_id").equals(tuple.get("evidence_definition_")))
      throw conflict();
    if (!resource.get("resource_policy").equals(entry.get("resource_policy")))
      throw unavailable();
    var classification = obj(resource, "classification");
    if (!classification.get("policy_ref")
            .equals(obj(entry, "disclosure_policy").get("artifact_ref"))
        || !classification.get("policy_digest")
            .equals(obj(entry, "disclosure_policy").get("digest")))
      throw unavailable();
    admission.verifyClassification(classification, entry);
    var links = list(Jcs.parse(((String) tuple.get("links_")).getBytes(StandardCharsets.UTF_8)));
    Set<String> seen = new HashSet<>();
    String prev = null;
    var groups = new TreeSet<String>(UTF8);
    var identities = new TreeMap<String, Map<String, Object>>(UTF8);
    for (Object o : links) {
      var l = map(o);
      Jcs.keys(
          l, "link_id", "link_revision", "task_id", "tenant_id", "type", "group_id", "user_id");
      String link = Jcs.ref(l, "link_id");
      number(l.get("link_revision"));
      if (!seen.add(link) || (prev != null && UTF8.compare(prev, link) >= 0)
          || !id.equals(l.get("task_id")) || !trust.scope.get("tenant").equals(l.get("tenant_id"))
          || !"candidate".equals(l.get("type"))
          || (l.get("group_id") == null) == (l.get("user_id") == null))
        throw unavailable();
      prev = link;
      if (l.get("group_id") != null) {
        String group = Jcs.ref(l, "group_id");
        if (!list(obj(entry, "group_domain").get("groups")).contains(group))
          throw unavailable();
        groups.add(group);
      } else {
        String user = Jcs.ref(l, "user_id");
        identities.put(user, humanIdentity(user));
      }
    }
    String assignee = (String) tuple.get("assignee_");
    if (assignee != null)
      identities.put(assignee, humanIdentity(assignee));
    var row = record("task_id", id, "task_revision", tuple.get("rev_").toString(),
        "process_definition_id", tuple.get("proc_def_id_"), "task_definition_key",
        tuple.get("task_def_key_"), "tenant_id", tuple.get("tenant_id_"), "assignee_ref", assignee,
        "engine_due_at",
        tuple.get("due_date_") == null ? null
                                       : time(((Timestamp) tuple.get("due_date_")).toInstant()),
        "active", true);
    admission.verifyIdentityPolicy(obj(entry, "opaque_task_id_policy"), row, links);
    Instant evidenceUntil =
        Instant.ofEpochSecond(((Number) tuple.get("evidence_until_")).longValue());
    var evidence = record("evidence_ref", tuple.get("evidence_ref_"), "evidence_revision",
        tuple.get("evidence_revision_").toString(), "evidence_digest",
        tuple.get("evidence_digest_"), "valid_until", time(evidenceUntil), "process_definition_id",
        tuple.get("evidence_definition_"));
    var observation = record("publication_id", tuple.get("resource_publication_"), "source", source,
        "stored_resource", resource);
    ceiling("resource", str(resource, "resource_ref"), str(resource, "resource_revision"),
        str(resource, "resource_digest"), time(source.get("observed_at")),
        time(resource.get("valid_until")));
    ceiling("evidence", str(evidence, "evidence_ref"), str(evidence, "evidence_revision"),
        str(evidence, "evidence_digest"), time(source.get("observed_at")), evidenceUntil);
    ceiling("classification", str(classification, "classification_ref"),
        str(resource, "resource_revision"), str(classification, "classification_digest"),
        time(source.get("observed_at")), time(classification.get("valid_until")));
    var state = record("task_row", row, "candidate_links", links, "designation", designation(cat),
        "resource_observation", observation, "evidence_pin", evidence, "authority_revision",
        tuple.get("authority_revision_").toString());
    Map<String, Object> membership = tuple.get("membership_") == null
        ? null
        : record("publication_id", tuple.get("membership_publication_"), "source",
              PortalReadStore.json(tuple.get("membership_source_")), "stored_membership",
              PortalReadStore.json(tuple.get("membership_")));
    return new State(identityState(state, identities), cat, entry, resource, membership, row);
  }
  /** Private commitment state extends the native task observation, never a public Q1 DTO. */
  static Map<String, Object> identityState(
      Map<String, Object> state, Map<String, Map<String, Object>> identities) {
    var result = copy(state);
    var ordered = new TreeMap<String, Map<String, Object>>(UTF8);
    ordered.putAll(identities);
    result.put("native_identities", new ArrayList<>(ordered.values()));
    return result;
  }
  Map<String, Object> humanIdentity(String principal) {
    return humanIdentity(principal, db.human(principal));
  }
  /** Exact scoped native identity facts, with a non-renewable original observation ceiling. */
  Map<String, Object> humanIdentity(String principal, Map<String, Object> p) {
    Instant observed = guard();
    if (p == null || !trust.scope.get("tenant").equals(p.get("tenant_"))
        || !principal.equals(p.get("principal_")) || !Boolean.TRUE.equals(p.get("active_"))
        || !(p.get("rev_") instanceof Long revision) || revision < 0
        || !(p.get("valid_until_") instanceof Long expiry)
        || !(p.get("issuer_") instanceof String issuer) || issuer.isEmpty()
        || !(p.get("subject_") instanceof String subject) || subject.isEmpty()
        || !(p.get("groups_") instanceof String groups))
      throw unavailable();
    Instant valid = Instant.ofEpochSecond(expiry);
    current(observed, time(valid));
    var nativeGroups = list(Jcs.parse(groups.getBytes(StandardCharsets.UTF_8)));
    for (Object group : nativeGroups)
      if (!(group instanceof String))
        throw unavailable();
    var identity = record("tenant", trust.scope.get("tenant"), "principal_ref", principal,
        "issuer", issuer, "subject", subject, "revision", Long.toString(revision),
        "active", true, "valid_until", time(valid), "groups", nativeGroups);
    String reference = "native-human:" + hash(record("tenant", trust.scope.get("tenant"),
        "principal_ref", principal));
    String digest = hash(identity);
    for (var prior : ceilings)
      if (prior.get("kind").equals("membership") && prior.get("source_ref").equals(reference)) {
        if (!prior.get("source_revision").equals(Long.toString(revision))
            || !prior.get("source_digest").equals(digest)
            || !prior.get("valid_until").equals(time(valid)))
          throw conflict();
        // Keep the first observed_at, including an authenticated predecessor's original value.
        return identity;
      }
    ceiling("membership", reference, Long.toString(revision), digest, observed, valid);
    return identity;
  }
  Map<String, Object> task(State s, Instant snapshotAt, Instant until) {
    var snap = record("schema_version", "1", "snapshot_at", time(snapshotAt));
    for (String k : List.of("process_definition_id", "process_definition_key",
             "process_definition_version", "process_definition_digest", "task_definition_key",
             "form_key", "form_version", "form_digest", "form_source_status", "allowed_inputs"))
      snap.put(k, s.entry.get(k));
    for (String k : List.of("task_id", "task_revision", "assignee_ref", "engine_due_at"))
      snap.put(k, s.row.get(k));
    snap.put("evidence_revision", s.resource.get("evidence_revision"));
    snap.put("evidence_digest", s.resource.get("evidence_digest"));
    snap.put("read_only_evidence", s.resource.get("read_only_evidence"));
    snap.put("allowed_actions", List.of());
    var groups = new TreeSet<String>(UTF8);
    for (Object l : list(s.nativeState.get("candidate_links")))
      if (map(l).get("group_id") != null)
        groups.add((String) map(l).get("group_id"));
    snap.put("eligible_candidate_groups", new ArrayList<>(groups));
    var task = record("tenant", trust.scope.get("tenant"), "snapshot", snap, "active", true,
        "authority_revision", s.nativeState.get("authority_revision"), "required_roles",
        s.entry.get("required_roles"), "required_subject_bindings",
        s.resource.get("required_subject_bindings"), "required_consent_scopes",
        s.resource.get("required_consent_scopes"), "valid_until", time(until));
    validate("task", task);
    return task;
  }
  Map<String, Object> membership(Map<String, Object> principal, Map<String, Object> observation) {
    if (observation == null)
      throw unavailable();
    var m = validate("membership", observation.get("stored_membership"));
    var src = obj(observation, "source");
    db.proof(str(observation, "publication_id"), "membership", src);
    source("membership", src);
    if (!m.get("state").equals("active") || !m.get("audience").equals("staff"))
      throw absent();
    for (String k : List.of("issuer", "subject", "principal_ref", "membership_revision",
             "memberships", "subject_bindings"))
      if (!m.get(k).equals(principal.get(k)))
        throw conflict();
    if (!principal.get("tenant").equals(trust.scope.get("tenant")))
      throw denied();
    ceiling("membership", str(m, "principal_ref"), str(m, "membership_revision"), hash(m),
        time(src.get("observed_at")), time(m.get("reviewed_until")));
    var human = db.human(str(principal, "principal_ref"));
    humanIdentity(str(principal, "principal_ref"), human);
    if (human == null || !Boolean.TRUE.equals(human.get("active_"))
        || !human.get("issuer_").equals(principal.get("issuer"))
        || !human.get("subject_").equals(principal.get("subject")))
      throw unavailable();
    Instant humanUntil = Instant.ofEpochSecond(((Number) human.get("valid_until_")).longValue());
    current(guard(), time(humanUntil));
    var flat = new TreeSet<String>(UTF8);
    for (Object mb : list(m.get("memberships")))
      for (Object g : list(map(mb).get("groups"))) flat.add((String) g);
    var old = list(Jcs.parse(((String) human.get("groups_")).getBytes(StandardCharsets.UTF_8)));
    if (!new HashSet<>(old).equals(flat))
      throw unavailable();
    ceiling("membership", str(m, "principal_ref") + ":native", human.get("rev_").toString(),
        hash(record("issuer", human.get("issuer_"), "subject", human.get("subject_"), "groups", old,
            "active", human.get("active_"))),
        time(src.get("observed_at")), humanUntil);
    return m;
  }
  Map<String, Object> authorityState(State s, Map<String, Object> principal) {
    var m = membership(principal, s.membership);
    var groups = new HashSet<Object>();
    for (Object l : list(s.nativeState.get("candidate_links")))
      if (map(l).get("group_id") != null)
        groups.add(map(l).get("group_id"));
    boolean roleGroup = false;
    for (Object mb : list(m.get("memberships"))) {
      var member = map(mb);
      if (list(member.get("roles")).containsAll(list(s.entry.get("required_roles")))
          && !Collections.disjoint(list(member.get("groups")), groups))
        roleGroup = true;
    }
    if (!roleGroup
        || !list(m.get("subject_bindings"))
            .containsAll(list(s.resource.get("required_subject_bindings"))))
      throw absent();
    var matches = list(s.resource.get("positive_grants"))
                      .stream()
                      .map(PortalReadModels::map)
                      .filter(g
                          -> List.of("issuer", "subject", "principal_ref", "membership_revision")
                              .stream()
                              .allMatch(k -> g.get(k).equals(principal.get(k))))
                      .toList();
    if (matches.isEmpty())
      throw absent();
    if (matches.size() != 1)
      throw unavailable();
    var grant = matches.get(0);
    current(guard(), grant.get("valid_until"));
    if (!list(grant.get("consent_scopes"))
            .containsAll(list(s.resource.get("required_consent_scopes"))))
      throw absent();
    ceiling("resource", str(grant, "decision_receipt_ref"), str(s.resource, "resource_revision"),
        str(grant, "decision_digest"),
        time(obj(obj(s.nativeState, "resource_observation"), "source").get("observed_at")),
        time(grant.get("valid_until")));
    return record("membership_observation", s.membership, "principal_digest", hash(principal),
        "matching_resource_grant", grant, "resource_observation_digest",
        hash(s.nativeState.get("resource_observation")), "authority_revision",
        s.nativeState.get("authority_revision"));
  }
  Map<String, Object> authority(
      State s, Map<String, Object> principal, Map<String, Object> astate, Instant valid) {
    var a = record("valid_until", time(valid), "tenant", trust.scope.get("tenant"),
        "read_permitted", true, "permitted_operations", List.of(), "authority_revision",
        s.nativeState.get("authority_revision"), "consent_scopes",
        obj(astate, "matching_resource_grant").get("consent_scopes"));
    for (String k : List.of("issuer", "subject", "principal_ref", "membership_revision"))
      a.put(k, principal.get(k));
    for (String k : List.of("task_id", "task_revision")) a.put(k, s.row.get(k));
    for (String k : List.of("evidence_revision", "evidence_digest")) a.put(k, s.resource.get(k));
    for (String k : List.of("process_definition_key", "process_definition_version",
             "process_definition_id", "process_definition_digest", "task_definition_key",
             "form_key", "form_version", "form_digest"))
      a.put(k, s.entry.get(k));
    validate("authority", a);
    return a;
  }
  Map<String, Object> mint(String stage, Map<String, Object> task, State s, Map<String, Object> ct,
      Map<String, Object> a, Map<String, Object> astate) {
    var key = keys.current();
    Instant issued = guard(), valid = until();
    if (valid.isAfter(time(task.get("valid_until"))))
      throw unavailable();
    if (a != null && !time(a.get("valid_until")).equals(valid))
      throw unavailable();
    var ordered = ceilings.stream()
                      .sorted((x, y) -> UTF8.compare(ceilingId(x), ceilingId(y)))
                      .map(PortalReadModels::copy)
                      .toList();
    var claims = record("schema", "portal-native-read-continuity.v1", "algorithm", "HMAC-SHA256",
        "stage", stage, "key_id", key.id, "binding", binding(), "origin_request_digest",
        envelope.digest(), "task_digest", hash(task), "snapshot_digest", hash(task.get("snapshot")),
        "snapshot_at", obj(task, "snapshot").get("snapshot_at"), "native_task_state_digest",
        hash(s.nativeState), "catalog_state_digest", hash(designation(s.catalog)),
        "task_continuity_digest", ct == null ? null : hash(ct), "authority_digest",
        a == null ? null : hash(a), "principal_digest",
        astate == null ? null : astate.get("principal_digest"), "native_authority_state_digest",
        astate == null ? null : hash(astate), "issued_at", time(issued), "valid_until", time(valid),
        "ceilings", new ArrayList<>(ordered));
    validate("claims", claims);
    return record(
        "claims", claims, "mac", HexFormat.of().formatHex(key.mac(stage, claims, issued)));
  }
  Map<String, Object> verify(
      Map<String, Object> continuity, String stage, Map<String, Object> task) {
    validate("continuity", continuity);
    var c = obj(continuity, "claims");
    if (!c.get("stage").equals(stage))
      throw unavailable();
    var key = keys.verification(str(c, "key_id"));
    if (key == null)
      throw unavailable();
    Instant now = guard();
    byte[] actual = HexFormat.of().parseHex(str(continuity, "mac"));
    if (!MessageDigest.isEqual(actual, key.mac(stage, c, now)))
      throw unavailable();
    var wanted = binding();
    var got = copy(obj(c, "binding"));
    Object generation = got.remove("runtime_admission_generation");
    wanted.remove("runtime_admission_generation");
    if (!got.equals(wanted))
      throw unavailable();
    if (!generation.equals(admission.generation()))
      throw conflict();
    if (!hash(task).equals(c.get("task_digest"))
        || !hash(task.get("snapshot")).equals(c.get("snapshot_digest"))
        || !obj(task, "snapshot").get("snapshot_at").equals(c.get("snapshot_at")))
      throw unavailable();
    if (time(c.get("issued_at")).isAfter(now))
      throw unavailable();
    current(now, c.get("valid_until"));
    current(now, task.get("valid_until"));
    for (Object o : list(c.get("ceilings"))) addCeiling(map(o));
    if (time(c.get("valid_until")).isAfter(time(task.get("valid_until"))))
      throw unavailable();
    return c;
  }
  Map<String, Object> continued(Map<String, Object> r, boolean disclosure) {
    var task = obj(r, "task");
    var ct = obj(r, "task_continuity");
    var principal = obj(r, "principal");
    var anchor = obj(r, "anchor");
    var tc = verify(ct, "task", task);
    String id = str(obj(task, "snapshot"), "task_id");
    State state = state(id, anchor, str(principal, "principal_ref"));
    if (!hash(state.nativeState).equals(tc.get("native_task_state_digest"))
        || !hash(designation(state.catalog)).equals(tc.get("catalog_state_digest")))
      throw conflict();
    var reconstructed = task(state, time(tc.get("snapshot_at")), time(task.get("valid_until")));
    if (!reconstructed.equals(task))
      throw conflict();
    ceiling("task_predecessor", id, str(obj(task, "snapshot"), "task_revision"), hash(ct),
        time(tc.get("issued_at")), time(tc.get("valid_until")));
    var as = authorityState(state, principal);
    Map<String, Object> value;
    if (disclosure) {
      var a = obj(r, "authority");
      var ca = obj(r, "authority_continuity");
      var ac = verify(ca, "authority", task);
      if (!hash(ct).equals(ac.get("task_continuity_digest"))
          || !hash(a).equals(ac.get("authority_digest"))
          || !hash(principal).equals(ac.get("principal_digest")))
        throw unavailable();
      if (!hash(state.nativeState).equals(ac.get("native_task_state_digest"))
          || !hash(as).equals(ac.get("native_authority_state_digest"))
          || !hash(designation(state.catalog)).equals(ac.get("catalog_state_digest")))
        throw conflict();
      if (!authority(state, principal, as, time(a.get("valid_until"))).equals(a))
        throw conflict();
      ceiling("authority_predecessor", id, str(a, "authority_revision"), hash(ca),
          time(ac.get("issued_at")), time(ac.get("valid_until")));
      var classification = obj(state.resource, "classification");
      var grant = record("scope", trust.scope, "principal", principal, "snapshot",
          task.get("snapshot"), "authority_revision", a.get("authority_revision"),
          "classification_ref", classification.get("classification_ref"), "classification_digest",
          classification.get("classification_digest"), "projection", "full_task_detail.v1",
          "valid_until", time(until()));
      value = record("catalog", catalogValue(state.catalog), "grant", grant,
          "task_continuity_digest", hash(ct), "authority_continuity_digest", hash(ca));
    } else {
      var a = authority(state, principal, as, until());
      var ca = mint("authority", task, state, ct, a, as);
      value = record("catalog", catalogValue(state.catalog), "authority", a,
          "task_continuity_digest", hash(ct), "authority_continuity", ca);
    }
    String stateDigest = hash(as);
    retain(state, anchor, principal);
    finalReads.add(() -> {
      State latest = state(id, anchor, str(principal, "principal_ref"));
      if (!hash(authorityState(latest, principal)).equals(stateDigest))
        throw conflict();
    });
    return value;
  }
  void retain(State original, Map<String, Object> anchor, Map<String, Object> principal) {
    String originalDigest = hash(original.nativeState);
    finalReads.add(() -> {
      State current = state(str(original.row, "task_id"), anchor,
          principal == null ? null : str(principal, "principal_ref"));
      if (!hash(current.nativeState).equals(originalDigest))
        throw conflict();
    });
  }
  Map<String, Object> discover(Map<String, Object> r) {
    var expectation = obj(r, "expectation");
    var anchor = obj(expectation, "anchor");
    var principal = obj(r, "principal");
    var cat = catalog(anchor);
    if (!expectation.get("catalog_revision").equals(cat.get("catalog_revision"))
        || !expectation.get("catalog_digest").equals(cat.get("catalog_digest"))
        || !expectation.get("source_observed_at").equals(obj(cat, "source").get("observed_at")))
      throw conflict();
    current(guard(), expectation.get("valid_until"));
    ceiling("catalog", str(anchor, "catalog_ref") + ":expectation",
        str(expectation, "catalog_revision"), hash(expectation),
        time(expectation.get("source_observed_at")), time(expectation.get("valid_until")));
    var m = db.membership(str(principal, "principal_ref"));
    if (m == null)
      throw unavailable();
    var observation = record("publication_id", m.get("publication_"), "source",
        PortalReadStore.json(m.get("source_")), "stored_membership",
        PortalReadStore.json(m.get("payload_")));
    membership(principal, observation);
    String md = hash(observation), cd = hash(cat);
    int limit = (int) number(r.get("limit"));
    String queue = str(r, "queue"), after = (String) r.get("after_task_id");
    var ids = db.discover(principal, obj(cat, "artifact"), queue, limit, after, guard());
    for (String id : ids) {
      try {
        State state = state(id, anchor, str(principal, "principal_ref"));
        var a = authorityState(state, principal);
        String ad = hash(a);
        retain(state, anchor, principal);
        finalReads.add(() -> {
          var current = state(id, anchor, str(principal, "principal_ref"));
          if (!hash(authorityState(current, principal)).equals(ad))
            throw conflict();
        });
      } catch (Rejected ex) {
        if (ex.status == 404)
          throw conflict();
        throw ex;
      }
    }
    finalReads.add(() -> {
      var latest = db.membership(str(principal, "principal_ref"));
      if (latest == null)
        throw unavailable();
      var o = record("publication_id", latest.get("publication_"), "source",
          PortalReadStore.json(latest.get("source_")), "stored_membership",
          PortalReadStore.json(latest.get("payload_")));
      membership(principal, o);
      if (!hash(o).equals(md) || !hash(catalog(anchor)).equals(cd))
        throw conflict();
      var current = db.discover(principal, obj(cat, "artifact"), queue, limit, after, guard());
      if (!current.equals(ids))
        throw conflict();
    });
    var binding = record("scope", trust.scope, "principal", principal, "queue", queue, "limit",
        r.get("limit"), "catalog_revision", cat.get("catalog_revision"), "catalog_ref",
        anchor.get("catalog_ref"), "publisher_ref", anchor.get("publisher_ref"), "catalog_digest",
        cat.get("catalog_digest"), "order", "task_id_ascending");
    return record("binding", binding, "task_ids", new ArrayList<>(ids), "after_task_id",
        ids.size() > limit ? ids.get(limit - 1) : null);
  }
  static void compiled(Map<String, Object> entry) {
    String key = str(entry, "process_definition_key") + "/" + str(entry, "task_definition_key");
    var expected = COMPILED.get(key);
    if (expected == null || !expected.get(0).equals(entry.get("form_key"))
        || !expected.get(1).equals(entry.get("form_source_status"))
        || !expected.subList(2, expected.size()).equals(entry.get("allowed_inputs")))
      throw unavailable();
  }
  static final Map<String, List<String>> COMPILED = Map.ofEntries(
      Map.entry("SP-OP-ADEQUACAO-001/UT_CoordenacaoRede",
          List.of("adequacao_coordenacao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_coordenacao", "decisao_remediacao", "tipo_fallback", "justificativa_fallback", "referencia_regulatoria", "estimativa_custo_cents")),
      Map.entry("SP-OP-ADEQUACAO-001/UT_DecisaoFallback",
          List.of("adequacao_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_remediacao", "tipo_fallback", "justificativa_fallback", "referencia_regulatoria", "estimativa_custo_cents")),
      Map.entry("SP-OP-ANS-SUBMIT-001/UT_CoordenacaoEnvioAssume",
          List.of("ans_coordenacao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_envio", "justificativa_adiamento")),
      Map.entry("SP-OP-ANS-SUBMIT-001/UT_CorrigirPendenciaEnvio",
          List.of("ans_pendencia", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "dataset_complete", "schema_valid", "lgpd_anonimizado")),
      Map.entry("SP-OP-ANS-SUBMIT-001/UT_RevisarEnvio",
          List.of("ans_revisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_envio", "justificativa_adiamento")),
      Map.entry("SP-OP-ANS-SUBMIT-001/UT_RevisarEnvioJuridico",
          List.of("ans_revisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_envio", "justificativa_adiamento")),
      Map.entry("SP-OP-ANS-SUBMIT-001/UT_TratarNack",
          List.of("ans_nack", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_nack")),
      Map.entry("SP-OP-CRED-001/UT_AnaliseCredenciamento",
          List.of("cred_cred", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_cred", "fundamentacao", "referencia_regulatoria", "data_efeito_iso")),
      Map.entry("SP-OP-CRED-001/UT_AnaliseDescredenciamento",
          List.of("cred_descred", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_cred", "fundamentacao", "referencia_regulatoria", "comprovacao_notificacao_previa", "plano_substituicao", "data_efeito_iso")),
      Map.entry("SP-OP-CRED-001/UT_CoordenacaoRedeCred",
          List.of("cred_cred", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_cred", "fundamentacao", "referencia_regulatoria", "data_efeito_iso")),
      Map.entry("SP-OP-CRED-001/UT_CoordenacaoRedeDescred",
          List.of("cred_descred", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_cred", "fundamentacao", "referencia_regulatoria", "comprovacao_notificacao_previa", "plano_substituicao", "data_efeito_iso")),
      Map.entry("SP-OP-FRAUDE-001/UT_CoordenacaoInvestigacao",
          List.of("fraude_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_fraude", "fundamentacao_investigacao", "indicadores_fundamentantes", "referencia_normativa", "destino_referral")),
      Map.entry("SP-OP-FRAUDE-001/UT_DecisaoInvestigador",
          List.of("fraude_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_fraude", "fundamentacao_investigacao", "indicadores_fundamentantes", "referencia_normativa", "destino_referral")),
      Map.entry("SP-OP-FRAUDE-001/UT_RevisaoReferral",
          List.of("fraude_referral", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "destino_referral")),
      Map.entry("SP-OP-LGPD-DSR-001/UT_RevisaoDpo",
          List.of("lgpd_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_dsr", "fundamentacao_legal")),
      Map.entry("SP-OP-NIP-001/UT_CoordenacaoNip",
          List.of("nip_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_nip", "fundamentacao_regulatoria", "referencia_negativa_original", "texto_resposta_nip")),
      Map.entry("SP-OP-NIP-001/UT_ElaborarRespostaNip",
          List.of("nip_minuta", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "texto_resposta_nip")),
      Map.entry("SP-OP-NIP-001/UT_RevisaoJuridicaNip",
          List.of("nip_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_nip", "fundamentacao_regulatoria", "referencia_negativa_original", "texto_resposta_nip")),
      Map.entry("SP-OP-PAGTO-001/UT_AprovacaoAlcada",
          List.of("pagto_aprovacao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_pagamento", "valor_aprovado_cents", "justificativa_aprovacao", "justificativa_recusa")),
      Map.entry("SP-OP-PAGTO-001/UT_CoordenacaoAlcada",
          List.of("pagto_coordenacao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_coordenacao", "decisao_pagamento", "valor_aprovado_cents", "justificativa_aprovacao", "justificativa_recusa")),
      Map.entry("SP-OP-AUTH-001/UT_DecidirPendenciaExpirada",
          List.of("auth_pendencia", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_pendencia")),
      Map.entry("SP-OP-AUTH-001/UT_AnaliseMedicoAuditor",
          List.of("auth_decisao", "BPMN_FORMDATA", "decisao_auditor", "justificativa_clinica",
              "cid10_referencia", "fundamentacao_dut")),
      Map.entry("SP-OP-AUTH-001/UT_CoordenacaoAssume",
          List.of("auth_decisao", "BPMN_FORMDATA", "decisao_auditor", "justificativa_clinica",
              "cid10_referencia", "fundamentacao_dut")),
      Map.entry("SP-OP-AUTH-001/UT_RegistrarParecerJunta",
          List.of("auth_junta", "BPMN_FORMDATA", "decisao_auditor", "justificativa_clinica",
              "cid10_referencia", "fundamentacao_dut")),
      Map.entry("SP-OP-ESCALATION-001/UT_TratarEscalonamento",
          List.of("escalation", "BPMN_FORMDATA", "resultado", "notas_resolucao")),
      Map.entry("SP-OP-ESCALATION-001/UT_SupervisorAssume",
          List.of("escalation", "BPMN_FORMDATA", "resultado", "notas_resolucao")),
      Map.entry("SP-OP-PAGTO-001/UT_AnaliseAdmissibilidade",
          List.of("pagto_admissibilidade", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
              "decisao_admissibilidade", "justificativa_recusa")),
      Map.entry("SP-OP-CONTAS-001/UT_AnalistaContas",
          List.of("contas_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_contas",
              "justificativa_glosa", "codigo_glosa_tiss", "valor_glosado_centavos",
              "valor_liberado_centavos", "justificativa_devolucao")),
      Map.entry("SP-OP-CONTAS-001/UT_CoordenacaoContasAssume",
          List.of("contas_coordenacao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_contas",
              "justificativa_glosa", "codigo_glosa_tiss", "valor_glosado_centavos",
              "valor_liberado_centavos", "justificativa_devolucao", "decisao_coordenacao")),
      Map.entry("SP-OP-RECURSO-001/UT_AnaliseRecursoAnalista",
          List.of("recurso_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_recurso",
              "fundamentacao_indeferimento", "valor_glosa_mantido_centavos",
              "valor_deferido_centavos", "referencia_contratual")),
      Map.entry("SP-OP-RECURSO-001/UT_CoordenacaoRecursoAssume",
          List.of("recurso_coordenacao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_recurso",
              "fundamentacao_indeferimento", "valor_glosa_mantido_centavos",
              "valor_deferido_centavos", "referencia_contratual", "desfecho_humano")),
      Map.entry("SP-OP-RECURSO-001/UT_EscalonamentoPrazo",
          List.of("recurso_coordenacao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_recurso",
              "fundamentacao_indeferimento", "valor_glosa_mantido_centavos",
              "valor_deferido_centavos", "referencia_contratual", "desfecho_humano")),
      Map.entry("SP-OP-RECURSO-001/UT_RevisaoAuditorMedico",
          List.of("recurso_auditor", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
              "decisao_auditor_recurso", "parecer_auditor", "fundamentacao_indeferimento",
              "valor_glosa_mantido_centavos", "valor_deferido_centavos", "referencia_contratual")),
      Map.entry("SP-OP-REEMBOLSO-001/UT_DecidirPendenciaExpirada",
          List.of(
              "reembolso_pendencia", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_pendencia")),
      Map.entry("SP-OP-REEMBOLSO-001/UT_AnaliseReembolso",
          List.of("reembolso_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_reembolso",
              "valor_reembolso_aprovado_cents", "justificativa", "fundamentacao_contratual")),
      Map.entry("SP-OP-REEMBOLSO-001/UT_CoordenacaoReembolso",
          List.of("reembolso_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_reembolso",
              "valor_reembolso_aprovado_cents", "justificativa", "fundamentacao_contratual")),
      Map.entry("SP-OP-REEMBOLSO-001/UT_RevisaoAuditorMedico",
          List.of("reembolso_auditor", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_reembolso",
              "valor_reembolso_aprovado_cents", "justificativa", "fundamentacao_contratual",
              "cid10_referencia", "parecer_auditor")),
      Map.entry("SP-OP-CANCEL-001/UT_AnaliseRescisao",
          List.of("cancel_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_cancelamento",
              "fundamentacao_contratual", "referencia_regulatoria",
              "comprovacao_notificacao_previa")),
      Map.entry("SP-OP-CANCEL-001/UT_CoordenacaoCancelamento",
          List.of("cancel_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_cancelamento",
              "fundamentacao_contratual", "referencia_regulatoria",
              "comprovacao_notificacao_previa")),
      Map.entry("SP-OP-INADIMPLENCIA-001/UT_AnaliseInadimplencia",
          List.of("inad_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_inadimplencia",
              "fundamentacao_contratual", "referencia_regulatoria",
              "comprovacao_notificacao_previa", "comprovacao_periodo_minimo", "data_efeito_iso")),
      Map.entry("SP-OP-INADIMPLENCIA-001/UT_CoordenacaoCobranca",
          List.of("inad_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_inadimplencia",
              "fundamentacao_contratual", "referencia_regulatoria",
              "comprovacao_notificacao_previa", "comprovacao_periodo_minimo", "data_efeito_iso")),
      Map.entry("SP-OP-PROGRAMA-001/UT_DecisaoClinica",
          List.of("programa_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_programa",
              "motivo_desligamento_clinico", "referencia_clinica")),
      Map.entry("SP-OP-PROGRAMA-001/UT_CoordenacaoDecisao",
          List.of("programa_decisao", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY", "decisao_programa",
              "motivo_desligamento_clinico", "referencia_clinica")));
}
