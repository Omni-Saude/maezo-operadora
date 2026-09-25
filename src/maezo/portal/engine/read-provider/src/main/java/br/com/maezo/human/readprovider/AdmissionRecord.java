package br.com.maezo.human.readprovider;

import br.com.maezo.human.Jcs;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.PublicKey;
import java.security.Signature;
import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * {@code portal-read-admission.v1}: the installed Q2 admission, one row of
 * {@code <native_schema>.mzo_portal_read_admission}, signed by the approver's installation root
 * over {@code "maezo/portal-read-admission/v1\0" || JCS(record)}. The engineering side cannot
 * self-admit: without that signature nothing here verifies (plan section 3.1, D-F).
 */
final class AdmissionRecord {
  static final String SCHEMA = "portal-read-admission.v1";
  static final byte[] DOMAIN =
      "maezo/portal-read-admission/v1\0".getBytes(StandardCharsets.US_ASCII);
  static final Set<String> PURPOSES = Set.of("portal-task-read", "portal-read-publication");
  /**
   * Staff scope (T1.7a/b) plus H1 (D-N): `resource` is admitted only together with the `human`
   * block, and the `human` block only together with a `resource` publisher.
   */
  static final Set<String> SOURCE_KINDS = Set.of("membership", "catalog-designate", "resource");
  static final Duration MAX_VALIDITY = Duration.ofDays(14);

  record Continuity(String keyId, String generation, String commitment, Instant notBefore,
      Instant notAfter) {}
  record Publisher(String kind, String publisherRef, String sourceRefPrefix) {}

  final String admissionRef;
  final long revision;
  final Map<String, Object> scope;
  final String engine, incarnation, deployment, deploymentDigest, trustConfigurationDigest;
  final Set<String> purposes;
  final String engineCodeDigest, providerCodeDigest;
  final Map<String, Continuity> continuity;
  final String catalogRef, catalogPublisherRef, catalogDigest;
  final Map<String, Publisher> publishers;
  final int statementTimeoutSeconds;
  final long observationSeconds;
  final Instant notBefore, validUntil;
  /** H1: the admitted human tasks; null when the admission is staff-only. */
  final HumanAdmission human;
  /** SHA-256 of the exact signed bytes; the Admission's capabilityDigest. */
  final String digest;

  private AdmissionRecord(Map<String, Object> m, String digest) {
    // `human` is the one optional key (H1): a staff-only record keeps its exact T1.7a shape.
    if (m.containsKey("human"))
      Fields.keys(m, "schema", "admission_ref", "admission_revision", "scope", "engine_name",
          "database_incarnation", "read_deployment_ref", "read_deployment_digest",
          "trust_configuration_digest", "purposes", "code_digests", "continuity_keys", "catalog",
          "publishers", "statement_timeout_seconds", "observation_seconds", "not_before",
          "valid_until", "human");
    else
      Fields.keys(m, "schema", "admission_ref", "admission_revision", "scope", "engine_name",
          "database_incarnation", "read_deployment_ref", "read_deployment_digest",
          "trust_configuration_digest", "purposes", "code_digests", "continuity_keys", "catalog",
          "publishers", "statement_timeout_seconds", "observation_seconds", "not_before",
          "valid_until");
    Fields.exact(m, "schema", SCHEMA);
    this.digest = digest;
    admissionRef = Fields.ref(m, "admission_ref");
    revision = Fields.decimal(m, "admission_revision", 1, Long.MAX_VALUE - 1);
    var s = Fields.object(m.get("scope"));
    Fields.keys(s, "tenant", "environment", "workload_ref");
    for (String k : List.of("tenant", "environment", "workload_ref"))
      Fields.ref(s, k);
    scope = Map.copyOf(new TreeMap<>(s));
    engine = Fields.ref(m, "engine_name");
    incarnation = Fields.ref(m, "database_incarnation");
    deployment = Fields.ref(m, "read_deployment_ref");
    deploymentDigest = Fields.hash(m, "read_deployment_digest");
    trustConfigurationDigest = Fields.hash(m, "trust_configuration_digest");
    purposes = Fields.distinctStrings(m.get("purposes"), PURPOSES);
    var code = Fields.object(m.get("code_digests"));
    Fields.keys(code, "engine", "provider");
    engineCodeDigest = Fields.hash(code, "engine");
    providerCodeDigest = Fields.hash(code, "provider");
    Map<String, Continuity> keys = new HashMap<>();
    Set<String> commitments = new java.util.HashSet<>();
    for (Object o : Fields.list(m.get("continuity_keys"))) {
      var k = Fields.object(o);
      Fields.keys(k, "key_id", "generation", "commitment", "not_before", "not_after");
      var c = new Continuity(Fields.ref(k, "key_id"),
          Long.toString(Fields.decimal(k, "generation", 0, Long.MAX_VALUE - 1)),
          Fields.hash(k, "commitment"), Fields.time(k, "not_before"), Fields.time(k, "not_after"));
      if (!c.generation().equals(k.get("generation")) || !c.notBefore().isBefore(c.notAfter())
          || keys.put(c.keyId(), c) != null || !commitments.add(c.commitment()))
        throw Fields.unavailable();
    }
    if (keys.isEmpty())
      throw Fields.unavailable();
    continuity = Map.copyOf(keys);
    var catalog = Fields.object(m.get("catalog"));
    Fields.keys(catalog, "catalog_ref", "publisher_ref", "catalog_digest");
    catalogRef = Fields.ref(catalog, "catalog_ref");
    catalogPublisherRef = Fields.ref(catalog, "publisher_ref");
    catalogDigest = Fields.hash(catalog, "catalog_digest");
    Map<String, Publisher> admitted = new HashMap<>();
    for (Object o : Fields.list(m.get("publishers"))) {
      var p = Fields.object(o);
      Fields.keys(p, "kind", "publisher_ref", "source_ref_prefix");
      String kind = Fields.string(p, "kind");
      if (!SOURCE_KINDS.contains(kind))
        throw Fields.unavailable();
      // The prefix must end at a segment separator, so `...:amh:` never admits `...:amhx:...`.
      String prefix = Fields.ref(p, "source_ref_prefix");
      if (!prefix.endsWith(":") && !prefix.endsWith("/"))
        throw Fields.unavailable();
      var publisher = new Publisher(kind, Fields.ref(p, "publisher_ref"), prefix);
      if (admitted.put(kind, publisher) != null)
        throw Fields.unavailable();
    }
    publishers = Map.copyOf(admitted);
    human = m.containsKey("human") ? HumanAdmission.parse(m.get("human")) : null;
    if ((human == null) == publishers.containsKey("resource"))
      throw Fields.unavailable();
    statementTimeoutSeconds = (int) Fields.decimal(m, "statement_timeout_seconds", 1, 10);
    observationSeconds = Fields.decimal(m, "observation_seconds", 60, 900);
    notBefore = Fields.time(m, "not_before");
    validUntil = Fields.time(m, "valid_until");
    if (!notBefore.isBefore(validUntil)
        || Duration.between(notBefore, validUntil).compareTo(MAX_VALIDITY) > 0)
      throw Fields.unavailable();
  }

  /**
   * Verifies the root signature over the exact stored bytes FIRST, then the closed shape. The
   * stored bytes must be their own JCS form, so nothing unsigned can ride along in the row.
   */
  static AdmissionRecord verify(byte[] raw, byte[] signature, PublicKey root) {
    if (raw == null || signature == null || signature.length != 64 || root == null
        || raw.length > Fields.MAX_FILE)
      throw Fields.unavailable();
    try {
      Signature verifier = Signature.getInstance("Ed25519");
      verifier.initVerify(root);
      verifier.update(DOMAIN);
      verifier.update(raw);
      if (!verifier.verify(signature))
        throw Fields.unavailable();
    } catch (GeneralSecurityException ex) {
      throw Fields.unavailable();
    }
    return new AdmissionRecord(Fields.canonical(raw), Jcs.digest(raw));
  }
}
