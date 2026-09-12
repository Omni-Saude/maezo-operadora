package br.com.maezo.human;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.time.*;
import java.time.format.DateTimeFormatter;
import java.util.*;

/** Closed number-free Q2 records. Shape is never native authority. */
final class PortalReadModels {
  private PortalReadModels() {}
  /** Untrusted ingress bound: request bodies, canonical envelopes, base64 fields, stored rows.
   * Mirrors the Python read plane (`gateway/human/read_profile.py:150`,
   * `read_transport.py:156`, `auth_transport.py:184`). Never raise this to admit a resource. */
  static final int MAX = 65536;
  /** Deployed process/decision model bound, a different concern from {@link #MAX}: these bytes
   * come from the engine deployment the operadora itself published (ACT_GE_BYTEARRAY), not from
   * a caller. 7 of the 16 production BPMN exceed 64 KiB (largest today
   * `SP-OP-RECURSO-001_Recurso_Glosa.bpmn` 90 561 B; `SP-OP-AUTH-001_Autorizacao_Previa.bpmn`
   * 69 706 B), so reusing MAX made `verifyCatalog` refuse the operadora's own catalog with
   * READ_DEPENDENCY_UNAVAILABLE. The bound is not invented here: it is the SAME 1 MiB the landed
   * Python reader already applies to the SAME artifact
   * (`gateway/human/decision_binding.py:707,758` `octet_length(b.bytes_)<=1048576`), and it stays
   * well under the 2 MiB qualification-artifact bound
   * (`gateway/human/decision_binding_qualification.py:26`). Memory stays bounded: the reader still
   * refuses at RESOURCE_MAX+1 instead of streaming without limit.
   * Pinned by `PortalReadResourceBoundTest` and
   * `tests/unit/portal/test_read_dependency_resource_bound.py`. */
  static final int RESOURCE_MAX = 1048576;
  /**
   * The ONLY bounded read of an engine-deployed process/decision model. Every reader of
   * {@code RepositoryService.getProcessModel/getDecisionModel} in this package goes through it, so
   * the bound is stated once instead of being re-typed per call site (V9-Q4 F2: `AuthRuntime`
   * carried its own `1048577`/`1048576` literals and `AtomicHumanCommand` read with an unbounded
   * `readAllBytes()`).
   *
   * <p>Returns whether the model is within {@link #RESOURCE_MAX} AND matches {@code expected};
   * it never throws a refusal itself, because the callers' refusal codes differ and are part of
   * their own contracts (503 READ_DEPENDENCY_UNAVAILABLE, 403 denied, 409 conflict). Memory stays
   * bounded: it reads at most RESOURCE_MAX+1 bytes and rejects at RESOURCE_MAX+1.
   *
   * <p>Pinned by {@code PortalReadResourceBoundTest} and
   * {@code tests/unit/portal/test_read_dependency_resource_bound.py}, both of which scan the whole
   * package so a future reader cannot reintroduce a private bound.
   */
  static boolean resourceMatches(InputStream stream, Object expected) throws IOException {
    byte[] raw = stream.readNBytes(RESOURCE_MAX + 1);
    return raw.length <= RESOURCE_MAX && Jcs.digest(raw).equals(expected);
  }
  static final String[] COMMON = {"schema", "operation", "scope", "engine_name",
      "database_incarnation", "read_deployment_ref", "read_deployment_digest", "request_id",
      "read_context_id"};
  static final Set<String> OPERATIONS =
      Set.of("catalog", "discover", "task", "authority", "disclosure");
  static final Set<String> KINDS =
      Set.of("catalog-designate", "catalog-revoke", "membership", "resource", "revoke-key");
  static final Comparator<String> UTF8 = (a, b)
      -> Arrays.compareUnsigned(
          a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8));
  static final DateTimeFormatter TIME =
      DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss.SSSSSS'Z'", Locale.ROOT)
          .withZone(ZoneOffset.UTC);
  static final Map<String, String> SHAPES = Map.ofEntries(
      Map.entry("scope", "tenant:r environment:r workload_ref:r"),
      Map.entry("anchor", "scope:@scope catalog_ref:r publisher_ref:r"),
      Map.entry("expectation",
          "anchor:@anchor catalog_revision:n catalog_digest:h source_observed_at:t valid_until:t"),
      Map.entry("member", "membership_ref:r roles:[r groups:[r"),
      Map.entry("subject", "kind:beneficiary|provider resource_ref:r"),
      Map.entry("principal",
          "schema_version:1 principal_ref:r issuer:s subject:r tenant:r membership_revision:n "
              + "memberships:[@member session_ref:r authenticated_at:t subject_bindings:[@subject"),
      Map.entry("source",
          "publisher_ref:r source_ref:r source_revision:n source_digest:h receipt_ref:r "
              + "observed_at:t valid_until:t"),
      Map.entry("pin", "artifact_ref:r digest:h"),
      Map.entry("artifact", "artifact_ref:r digest:h bytes_base64:b64"),
      Map.entry("domain",
          "kind:static|dmn groups:[r dmn_definition_id:?r dmn_definition_key:?r "
              + "dmn_definition_version:?v dmn_resource_digest:?h"),
      Map.entry("catalogentry",
          "process_definition_id:r process_definition_key:r process_definition_version:v "
              + "process_definition_digest:h task_definition_key:r form_key:r form_version:v "
              + "form_digest:h form_source_status:s allowed_inputs:[r required_roles:[r "
              + "subject_policy:@pin consent_policy:@pin resource_policy:@pin "
                + "disclosure_policy:@pin "
              + "group_domain:@domain opaque_task_id_policy:@pin"),
      Map.entry("artifactcatalog",
          "schema:portal-read-catalog.v1 catalog_ref:r publisher_ref:r entries:[@catalogentry "
              + "policies:[@artifact forms:[@artifact deployment_receipt_ref:r "
              + "deployment_receipt_digest:h"),
      Map.entry("catalog-designate",
          "catalog_ref:r catalog_revision:n catalog_digest:h catalog_artifact_base64:b64 "
              + "deployment_receipt_ref:r deployment_receipt_digest:h valid_until:t"),
      Map.entry("catalog-revoke", "catalog_ref:r expected_catalog_revision:n"),
      Map.entry("membership",
          "principal_ref:r issuer:s subject:r membership_revision:n "
              + "audience:staff|beneficiary|provider memberships:[@member "
                + "subject_bindings:[@subject "
              + "state:active|revoked reviewed_until:t"),
      Map.entry("grant",
          "issuer:s subject:r principal_ref:r membership_revision:n consent_scopes:[r "
              + "decision_receipt_ref:r decision_digest:h valid_until:t"),
      Map.entry("classification",
          "classification_ref:r classification_digest:h policy_ref:r policy_digest:h "
              + "projection:full_task_detail.v1 fields_digest:h valid_until:t"),
      Map.entry("pagto",
          "kind:pagto_admissibilidade valor_pagamento_cents:centavos dados_pagamento_validos:bool "
              + "lastro_confirmado:bool "
                + "lastro_origem:?contas_adjudicacao_automatica|contas_adjudicacao_humana|recurso_"
                + "deferimento_humano lastro_decisor_id:?emptyref "
              + "duplicidade_suspeita:bool"),
      Map.entry("resource",
          "task_id:r process_definition_id:r process_definition_digest:h observed_task_revision:n "
              + "evidence_ref:r evidence_revision:n evidence_digest:h resource_ref:r "
              + "resource_revision:n resource_digest:h resource_policy:@pin "
              + "classification:@classification required_subject_bindings:[@subject "
              + "required_consent_scopes:[r positive_grants:[@grant read_only_evidence:?@pagto "
              + "state:complete|revoked valid_until:t"),
      Map.entry("revoke-key", "key_fingerprint:h"),
      Map.entry("snapshot",
          "schema_version:1 snapshot_at:t task_id:r process_definition_key:r "
              + "process_definition_version:v process_definition_id:r process_definition_digest:h "
              + "task_definition_key:r form_key:r form_version:v form_digest:h "
                + "form_source_status:s "
              + "task_revision:n assignee_ref:?r eligible_candidate_groups:[r evidence_revision:n "
              + "evidence_digest:h engine_due_at:?t allowed_actions:[r allowed_inputs:[r "
              + "read_only_evidence:?@pagto"),
      Map.entry("task",
          "valid_until:t tenant:r snapshot:@snapshot active:bool authority_revision:n "
              + "required_roles:[r required_subject_bindings:[@subject required_consent_scopes:[r"),
      Map.entry("authority",
          "valid_until:t tenant:r task_id:r process_definition_key:r process_definition_version:v "
              + "process_definition_id:r process_definition_digest:h task_definition_key:r "
                + "form_key:r "
              + "form_version:v form_digest:h issuer:s subject:r principal_ref:r "
                + "membership_revision:n "
              + "authority_revision:n task_revision:n evidence_revision:n evidence_digest:h "
              + "read_permitted:bool permitted_operations:[r consent_scopes:[r"),
      Map.entry("requester", "issuer:r key_id:r public_key_sha256:h peer_spki_sha256:h"),
      Map.entry("binding",
          "scope:@scope engine_name:r database_incarnation:r read_deployment_ref:r "
              + "read_deployment_digest:h runtime_admission_generation:n read_context_id:token "
              + "requester:@requester"),
      Map.entry("ceiling",
          "kind:native_admission|requester_key|request_envelope|native_key|catalog|resource|"
              + "evidence|classification|membership|task_predecessor|authority_predecessor "
              + "source_ref:r source_revision:n source_digest:h observed_at:t valid_until:t"),
      Map.entry("claims",
          "schema:portal-native-read-continuity.v1 algorithm:HMAC-SHA256 stage:task|authority "
              + "key_id:r binding:@binding origin_request_digest:h task_digest:h snapshot_digest:h "
              + "snapshot_at:t native_task_state_digest:h catalog_state_digest:h "
              + "task_continuity_digest:?h authority_digest:?h principal_digest:?h "
              + "native_authority_state_digest:?h issued_at:t valid_until:t ceilings:[@ceiling"),
      Map.entry("continuity", "claims:@claims mac:h"));

  static Rejected invalid() {
    return new Rejected(400, "INVALID_REQUEST");
  }
  static Rejected unavailable() {
    return new Rejected(503, "READ_DEPENDENCY_UNAVAILABLE");
  }
  static Rejected conflict() {
    return new Rejected(409, "READ_REVISION_CONFLICT");
  }
  static Rejected denied() {
    return new Rejected(403, "READ_AUTHENTICATION_DENIED");
  }
  static Rejected absent() {
    return new Rejected(404, "RESOURCE_UNAVAILABLE");
  }
  static Map<String, Object> map(Object value) {
    return Jcs.object(value);
  }
  static Map<String, Object> obj(Map<String, Object> m, String k) {
    return map(m.get(k));
  }
  static String str(Map<String, Object> m, String k) {
    return Jcs.string(m, k);
  }
  @SuppressWarnings("unchecked")
  static List<Object> list(Object value) {
    if (!(value instanceof List<?>) )
      throw invalid();
    return (List<Object>) value;
  }
  static String hash(Object value) {
    return Jcs.digest(Jcs.canonical(value));
  }
  static byte[] bounded(Object value) {
    byte[] b = Jcs.canonical(value);
    if (b.length > MAX)
      throw unavailable();
    return b;
  }
  static Map<String, Object> copy(Map<String, Object> value) {
    return map(Jcs.parse(Jcs.canonical(value)));
  }
  static Map<String, Object> record(Object... pairs) {
    Map<String, Object> result = new TreeMap<>();
    for (int i = 0; i < pairs.length; i += 2) result.put((String) pairs[i], pairs[i + 1]);
    return result;
  }
  static String time(Instant value) {
    if (value.getNano() % 1000 != 0)
      value = value.truncatedTo(java.time.temporal.ChronoUnit.MICROS);
    return TIME.format(value);
  }
  static Instant time(Object value) {
    if (!(value instanceof String s)
        || !s.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z"))
      throw invalid();
    try {
      Instant t = Instant.parse(s);
      if (!time(t).equals(s))
        throw invalid();
      return t;
    } catch (DateTimeException ex) {
      throw invalid();
    }
  }
  static void current(Instant now, Object until) {
    if (!now.isBefore(time(until)))
      throw unavailable();
  }
  static long number(Object value) {
    if (!(value instanceof String s) || !s.matches("0|[1-9][0-9]*"))
      throw invalid();
    try {
      return Long.parseLong(s);
    } catch (NumberFormatException ex) {
      throw invalid();
    }
  }
  static byte[] b64(Object value, boolean url, int size) {
    if (!(value instanceof String s))
      throw invalid();
    try {
      if (url && !s.matches("[A-Za-z0-9_-]+"))
        throw invalid();
      byte[] raw = (url ? Base64.getUrlDecoder() : Base64.getDecoder()).decode(s);
      String encoded =
          (url ? Base64.getUrlEncoder().withoutPadding() : Base64.getEncoder()).encodeToString(raw);
      if (!encoded.equals(s) || raw.length > MAX || (size >= 0 && raw.length != size))
        throw invalid();
      return raw;
    } catch (IllegalArgumentException ex) {
      throw invalid();
    }
  }
  static Map<String, Object> validate(String shape, Object value) {
    Map<String, Object> m = map(value);
    String spec = SHAPES.get(shape);
    if (spec == null)
      throw invalid();
    Set<String> names = new HashSet<>();
    for (String field : spec.split(" ")) {
      String[] p = field.split(":", 2);
      names.add(p[0]);
      if (!m.containsKey(p[0]))
        throw invalid();
      type(p[1], m.get(p[0]));
    }
    if (!m.keySet().equals(names))
      throw invalid();
    if (shape.equals("domain")) {
      boolean dynamic = m.get("kind").equals("dmn");
      for (String k : List.of("dmn_definition_id", "dmn_definition_key", "dmn_definition_version",
               "dmn_resource_digest"))
        if (dynamic == (m.get(k) == null))
          throw invalid();
      if (list(m.get("groups")).isEmpty())
        throw invalid();
    }
    if (shape.equals("task") && list(m.get("required_roles")).isEmpty())
      throw invalid();
    if (shape.equals("snapshot")) {
      if (!list(m.get("allowed_actions")).isEmpty())
        throw invalid();
      if (m.get("form_key").equals("pagto_admissibilidade")
          != (m.get("read_only_evidence") != null))
        throw invalid();
    }
    if (shape.equals("authority") && !list(m.get("permitted_operations")).isEmpty())
      throw invalid();
    if (shape.equals("claims")) {
      boolean authority = m.get("stage").equals("authority");
      for (String k : List.of("task_continuity_digest", "authority_digest", "principal_digest",
               "native_authority_state_digest"))
        if (authority == (m.get(k) == null))
          throw invalid();
      var ceilings = list(m.get("ceilings"));
      if (ceilings.isEmpty())
        throw invalid();
      String previous = null;
      Instant minimum = null, issued = time(m.get("issued_at"));
      for (Object o : ceilings) {
        var c = map(o);
        String identity = ceilingId(c);
        if (previous != null && UTF8.compare(previous, identity) >= 0)
          throw invalid();
        previous = identity;
        Instant until = time(c.get("valid_until"));
        if (time(c.get("observed_at")).isAfter(issued) || !issued.isBefore(until))
          throw invalid();
        minimum = minimum == null || until.isBefore(minimum) ? until : minimum;
      }
      if (!time(m.get("valid_until")).equals(minimum))
        throw invalid();
    }
    return m;
  }
  static String ceilingId(Map<String, Object> c) {
    return str(c, "kind") + "\0" + str(c, "source_ref") + "\0" + str(c, "source_revision") + "\0"
        + str(c, "source_digest");
  }
  static void type(String kind, Object value) {
    if (kind.startsWith("?")) {
      if (value != null)
        type(kind.substring(1), value);
      return;
    }
    if (kind.startsWith("[")) {
      var a = list(value);
      Set<String> seen = new HashSet<>();
      for (Object v : a) {
        type(kind.substring(1), v);
        if (!seen.add(hash(v)))
          throw invalid();
      }
      return;
    }
    if (kind.startsWith("@")) {
      validate(kind.substring(1), value);
      return;
    }
    if (kind.equals("bool")) {
      if (!(value instanceof Boolean))
        throw invalid();
      return;
    }
    if (!(value instanceof String s))
      throw invalid();
    switch (kind) {
      case "n" -> number(s);
      case "v" -> {
        long n = number(s);
        if (n < 1 || n > Integer.MAX_VALUE)
          throw invalid();
      }
      case "t" -> time(s);
      case "token" -> b64(s, true, 32);
      case "b64" -> b64(s, false, -1);
      case "centavos" -> {
        if (!s.matches("0|-?[1-9][0-9]*"))
          throw invalid();
      }
      case "h" -> {
        if (!s.matches("[0-9a-f]{64}"))
          throw invalid();
      }
      case "r" -> {
        if (!s.matches("[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}"))
          throw invalid();
      }
      case "s" -> {
        if (s.isEmpty() || s.chars().anyMatch(c -> c < 32 || c == 127))
          throw invalid();
      }
      case "emptyref" -> {
        if (!s.isEmpty() && !s.matches("[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}"))
          throw invalid();
      }
      case "text" -> {
        if (s.chars().anyMatch(c -> c < 32 || c == 127))
          throw invalid();
      }
      default -> {
        if (!Set.of(kind.split("\\|", -1)).contains(s))
          throw invalid();
      }
    }
  }
  static Map<String, Object> readRequest(Object value) {
    var m = map(value);
    String op = str(m, "operation");
    if (!OPERATIONS.contains(op))
      throw invalid();
    Set<String> keys = new HashSet<>(Arrays.asList(COMMON));
    String[] extra = switch (op) {
      case "catalog" -> new String[] {"anchor"};
      case "discover" ->
        new String[] {"principal", "expectation", "queue", "limit", "after_task_id"};
      case "task" -> new String[] {"anchor", "task_id"};
      case "authority" -> new String[] {"anchor", "principal", "task", "task_continuity"};
      default ->
        new String[] {
            "anchor", "principal", "task", "task_continuity", "authority", "authority_continuity"};
    };
    keys.addAll(Arrays.asList(extra));
    if (!m.keySet().equals(keys) || !m.get("schema").equals("portal-engine-read.v1"))
      throw invalid();
    validate("scope", m.get("scope"));
    for (String k : List.of("engine_name", "database_incarnation", "read_deployment_ref"))
      type("r", m.get(k));
    type("h", m.get("read_deployment_digest"));
    type("token", m.get("request_id"));
    type("token", m.get("read_context_id"));
    if (m.containsKey("anchor"))
      validate("anchor", m.get("anchor"));
    if (m.containsKey("principal"))
      validate("principal", m.get("principal"));
    if (op.equals("discover")) {
      validate("expectation", m.get("expectation"));
      type("mine|team", m.get("queue"));
      long n = number(m.get("limit"));
      if (n < 1 || n > 100)
        throw invalid();
      type("?r", m.get("after_task_id"));
    }
    if (op.equals("task"))
      type("r", m.get("task_id"));
    if (m.containsKey("task")) {
      validate("task", m.get("task"));
      validate("continuity", m.get("task_continuity"));
    }
    if (op.equals("disclosure")) {
      validate("authority", m.get("authority"));
      validate("continuity", m.get("authority_continuity"));
    }
    return m;
  }
}
