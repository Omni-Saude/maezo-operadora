package br.com.maezo.human.readprovider;

import java.time.Instant;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * H1 (D-N, Onda 8 as a parallel track): the {@code human} block of {@code portal-read-admission.v1},
 * the approver-signed, independent classification and identity policy of each human task the Q2
 * plane may read. Absent block = nothing about tasks is admitted (the staff-only admission of
 * T1.7a/b keeps refusing every task read and every {@code resource} publication).
 *
 * <pre>
 * "human": {"entries": [{
 *   "process_definition_id": ref, "task_definition_key": ref,
 *   "classification": {"classification_ref": ref, "classification_digest": sha256,
 *                      "policy_ref": ref, "policy_digest": sha256,
 *                      "projection": "full_task_detail.v1", "fields_digest": sha256},
 *   "identity_policy": {"artifact_ref": ref, "digest": sha256},
 *   "task_id_format": "uuid" | "decimal",
 *   "candidate_groups": [ref, ...],
 *   "user_candidates": "refused" | "native_principal"}]}
 * </pre>
 *
 * Every check is synchronous, local and I/O-free (they run inside engine commands).
 */
final class HumanAdmission {
  static final String PROJECTION = "full_task_detail.v1";
  static final Set<String> CLASSIFICATION_KEYS = Set.of("classification_ref",
      "classification_digest", "policy_ref", "policy_digest", "projection", "fields_digest");
  static final int MAX_ENTRIES = 256, MAX_GROUPS = 64;
  /** Lower-case canonical UUID, what {@code StrongUuidGenerator} writes. */
  static final Pattern UUID =
      Pattern.compile("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");
  /** {@code DbIdGenerator}: a positive decimal without leading zeros. */
  static final Pattern DECIMAL = Pattern.compile("[1-9][0-9]{0,18}");

  record Entry(String processDefinitionId, String taskDefinitionKey,
      Map<String, Object> classification, Map<String, Object> identityPolicy, Pattern taskId,
      Set<String> candidateGroups, boolean userCandidates) {}

  /** Keyed by {@code process_definition_id + "\0" + task_definition_key}. */
  final Map<String, Entry> entries;

  private HumanAdmission(Map<String, Entry> entries) {
    this.entries = Map.copyOf(entries);
  }

  static String key(String processDefinitionId, String taskDefinitionKey) {
    return processDefinitionId + "\0" + taskDefinitionKey;
  }

  static HumanAdmission parse(Object value) {
    var human = Fields.object(value);
    Fields.keys(human, "entries");
    var list = Fields.list(human.get("entries"));
    if (list.isEmpty() || list.size() > MAX_ENTRIES)
      throw Fields.unavailable();
    Map<String, Entry> out = new HashMap<>();
    for (Object o : list) {
      var e = Fields.object(o);
      Fields.keys(e, "process_definition_id", "task_definition_key", "classification",
          "identity_policy", "task_id_format", "candidate_groups", "user_candidates");
      var c = Fields.object(e.get("classification"));
      if (!c.keySet().equals(CLASSIFICATION_KEYS))
        throw Fields.unavailable();
      Fields.ref(c, "classification_ref");
      Fields.hash(c, "classification_digest");
      Fields.ref(c, "policy_ref");
      Fields.hash(c, "policy_digest");
      Fields.exact(c, "projection", PROJECTION);
      Fields.hash(c, "fields_digest");
      var p = Fields.object(e.get("identity_policy"));
      Fields.keys(p, "artifact_ref", "digest");
      Fields.ref(p, "artifact_ref");
      Fields.hash(p, "digest");
      Pattern format = switch (Fields.string(e, "task_id_format")) {
        case "uuid" -> UUID;
        case "decimal" -> DECIMAL;
        default -> throw Fields.unavailable();
      };
      var groups = Fields.list(e.get("candidate_groups"));
      Set<String> admitted = new HashSet<>();
      if (groups.isEmpty() || groups.size() > MAX_GROUPS)
        throw Fields.unavailable();
      for (Object g : groups)
        if (!admitted.add(ref(g)))
          throw Fields.unavailable();
      boolean users = switch (Fields.string(e, "user_candidates")) {
        case "refused" -> false;
        case "native_principal" -> true;
        default -> throw Fields.unavailable();
      };
      var entry = new Entry(Fields.ref(e, "process_definition_id"),
          Fields.ref(e, "task_definition_key"), Map.copyOf(c), Map.copyOf(p), format,
          Set.copyOf(admitted), users);
      if (out.put(key(entry.processDefinitionId(), entry.taskDefinitionKey()), entry) != null)
        throw Fields.unavailable();
    }
    return new HumanAdmission(out);
  }

  private static String ref(Object value) {
    if (!(value instanceof String))
      throw Fields.unavailable();
    return Fields.ref(Map.of("v", value), "v");
  }

  private Entry entry(Object processDefinitionId, Object taskDefinitionKey) {
    if (!(processDefinitionId instanceof String p) || !(taskDefinitionKey instanceof String t))
      throw Fields.unavailable();
    var e = entries.get(key(p, t));
    if (e == null)
      throw Fields.unavailable();
    return e;
  }

  /**
   * The classification the publisher attached to a resource (and the engine stored) is EXACTLY the
   * admitted one for that catalog entry, its disclosure policy is the admitted policy, and it is
   * not retained past the admission's own window.
   */
  void verifyClassification(
      Map<String, Object> classification, Map<String, Object> catalogEntry, Instant ceiling) {
    if (classification == null || catalogEntry == null)
      throw Fields.unavailable();
    var e = entry(catalogEntry.get("process_definition_id"),
        catalogEntry.get("task_definition_key"));
    Fields.keys(classification, "classification_ref", "classification_digest", "policy_ref",
        "policy_digest", "projection", "fields_digest", "valid_until");
    for (String k : CLASSIFICATION_KEYS)
      if (!e.classification().get(k).equals(classification.get(k)))
        throw Fields.unavailable();
    if (Fields.time(classification, "valid_until").isAfter(ceiling))
      throw Fields.unavailable();
    var disclosure = Fields.object(catalogEntry.get("disclosure_policy"));
    Fields.keys(disclosure, "artifact_ref", "digest");
    if (!e.classification().get("policy_ref").equals(disclosure.get("artifact_ref"))
        || !e.classification().get("policy_digest").equals(disclosure.get("digest")))
      throw Fields.unavailable();
  }

  /** For a {@code resource} publication, before the task key is known: any admitted entry of it. */
  void verifyPublishedClassification(
      String processDefinitionId, Map<String, Object> classification, Instant ceiling) {
    Fields.keys(classification, "classification_ref", "classification_digest", "policy_ref",
        "policy_digest", "projection", "fields_digest", "valid_until");
    if (Fields.time(classification, "valid_until").isAfter(ceiling))
      throw Fields.unavailable();
    for (var e : entries.values())
      if (e.processDefinitionId().equals(processDefinitionId) && CLASSIFICATION_KEYS.stream()
              .allMatch(k -> e.classification().get(k).equals(classification.get(k))))
        return;
    throw Fields.unavailable();
  }

  /**
   * The native task row and its candidate links only carry admitted, non-PHI identifiers: the task
   * id in the admitted engine id format, the admitted tenant, candidate groups inside the admitted
   * group namespace, and a candidate user only when the entry admits native principals (the engine
   * then resolves every user and the assignee against {@code MZO_HUMAN_PRINCIPAL}). The entry's
   * opaque task id policy is the admitted pin.
   */
  void verifyIdentityPolicy(Map<String, Object> policy, Map<String, Object> task,
      List<Object> links, String tenant) {
    if (policy == null || task == null || links == null)
      throw Fields.unavailable();
    var e = entry(task.get("process_definition_id"), task.get("task_definition_key"));
    Fields.keys(policy, "artifact_ref", "digest");
    if (!e.identityPolicy().equals(policy))
      throw Fields.unavailable();
    String id = Fields.string(task, "task_id");
    if (!e.taskId().matcher(id).matches() || !tenant.equals(task.get("tenant_id"))
        || !Boolean.TRUE.equals(task.get("active")))
      throw Fields.unavailable();
    Object assignee = task.get("assignee_ref");
    if (assignee != null)
      ref(assignee);
    if (links.size() > MAX_GROUPS * 4)
      throw Fields.unavailable();
    for (Object o : links) {
      var l = Fields.object(o);
      if (!id.equals(l.get("task_id")) || !tenant.equals(l.get("tenant_id"))
          || !"candidate".equals(l.get("type")))
        throw Fields.unavailable();
      Object group = l.get("group_id"), user = l.get("user_id");
      if ((group == null) == (user == null))
        throw Fields.unavailable();
      if (group != null) {
        if (!e.candidateGroups().contains(ref(group)))
          throw Fields.unavailable();
      } else if (!e.userCandidates()) {
        throw Fields.unavailable();
      } else {
        ref(user);
      }
    }
  }
}
