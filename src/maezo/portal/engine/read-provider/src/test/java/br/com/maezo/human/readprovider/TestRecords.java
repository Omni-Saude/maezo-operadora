package br.com.maezo.human.readprovider;

import br.com.maezo.human.Jcs;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.GeneralSecurityException;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.Signature;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import javax.crypto.spec.SecretKeySpec;

/** Synthetic, public test records. Nothing here is a production key, pin or admission. */
public final class TestRecords {
  private TestRecords() {}

  public static final String ADMISSION_REF = "q2-staff-admission";
  public static final String KEY_ID = "native-continuity-1";
  public static final String PUBLISHER = "portal-publisher";
  public static final String CATALOG = "staff-catalog";
  public static final String MEMBERSHIP_PREFIX = "portal-identity:amh:membership:";

  public static KeyPair ed25519() {
    try {
      return KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
    } catch (GeneralSecurityException ex) {
      throw new IllegalStateException(ex);
    }
  }

  public static Map<String, Object> record(Object... pairs) {
    Map<String, Object> out = new TreeMap<>();
    for (int i = 0; i < pairs.length; i += 2) out.put((String) pairs[i], pairs[i + 1]);
    return out;
  }

  public static String time(Instant t) {
    return Fields.format(t);
  }

  public static Instant now() {
    return Instant.now().truncatedTo(ChronoUnit.MICROS);
  }

  public static Map<String, Object> scope() {
    return record("tenant", "tenant-test", "environment", "dev", "workload_ref", "portal-staff");
  }

  public static String commitment(byte[] secret) {
    return NativeContinuityKeys.commitment(new SecretKeySpec(secret, "HmacSHA256"));
  }

  /** A complete, valid admission record for the given bindings (mutable: tests break one field). */
  public static Map<String, Object> admission(long revision, String engine, String incarnation,
      String deployment, String deploymentDigest, String trustDigest, String engineCode,
      String providerCode, String commitment, Instant before, Instant until) {
    return record("schema", "portal-read-admission.v1", "admission_ref", ADMISSION_REF,
        "admission_revision", Long.toString(revision), "scope", scope(), "engine_name", engine,
        "database_incarnation", incarnation, "read_deployment_ref", deployment,
        "read_deployment_digest", deploymentDigest, "trust_configuration_digest", trustDigest,
        "purposes", new ArrayList<>(List.of("portal-task-read")), "code_digests",
        record("engine", engineCode, "provider", providerCode), "continuity_keys",
        new ArrayList<>(List.of(record("key_id", KEY_ID, "generation", "1", "commitment",
            commitment, "not_before", time(before), "not_after", time(until)))),
        "catalog",
        record("catalog_ref", CATALOG, "publisher_ref", PUBLISHER, "catalog_digest",
            "a".repeat(64)),
        "publishers",
        new ArrayList<>(List.of(
            record("kind", "membership", "publisher_ref", PUBLISHER, "source_ref_prefix",
                MEMBERSHIP_PREFIX),
            record("kind", "catalog-designate", "publisher_ref", PUBLISHER, "source_ref_prefix",
                CATALOG + ":"))),
        "statement_timeout_seconds", "5", "observation_seconds", "300", "not_before",
        time(before), "valid_until", time(until));
  }

  public static byte[] sign(PrivateKey key, byte[] raw) {
    try {
      Signature signer = Signature.getInstance("Ed25519");
      signer.initSign(key);
      signer.update(AdmissionRecord.DOMAIN);
      signer.update(raw);
      return signer.sign();
    } catch (GeneralSecurityException ex) {
      throw new IllegalStateException(ex);
    }
  }

  public static Map<String, Object> providerConfiguration(KeyPair root, String schema, long oid,
      String owner, Path keysFile) {
    return providerConfiguration(root, schema, oid, owner, keysFile,
        record("dsn_file", "/run/maezo-native/membership-source.dsn", "ca_file",
            "/run/maezo-native/membership-source-ca.pem", "source_schema", "amh",
            "publisher_ref", PUBLISHER));
  }

  public static Map<String, Object> providerConfiguration(KeyPair root, String schema, long oid,
      String owner, Path keysFile, Map<String, Object> membershipSource) {
    byte[] spki = root.getPublic().getEncoded();
    return record("schema", "portal-read-provider.v1", "root_public_key_spki_base64",
        Base64.getEncoder().encodeToString(spki), "root_public_key_sha256", Jcs.digest(spki),
        "admission_ref", ADMISSION_REF, "minimum_admission_revision", "1", "datasource_jndi",
        TestNaming.PROCESS_ENGINE, "native_schema", schema, "admission_table_oid",
        Long.toString(oid), "admission_table_owner", owner, "continuity_keys_file",
        keysFile.toString(), "membership_source", membershipSource);
  }

  public static Map<String, Object> continuityKey(
      String id, String generation, byte[] secret, Instant before, Instant until) {
    return record("key_id", id, "generation", generation, "secret_base64",
        Base64.getEncoder().encodeToString(secret), "not_before", time(before), "not_after",
        time(until));
  }

  /** Writes the key file with the custody mode the provider requires (0400) unless told otherwise. */
  public static void writeKeys(Path file, List<Map<String, Object>> keys, String mode)
      throws IOException {
    Files.deleteIfExists(file);
    Files.createDirectories(file.getParent());
    Files.write(file,
        Jcs.canonical(record("schema", "portal-read-continuity-keys.v1", "keys", keys)));
    Files.setPosixFilePermissions(file, PosixFilePermissions.fromString(mode));
  }

  public static void writeJson(Path file, Map<String, Object> value) throws IOException {
    Files.createDirectories(file.getParent());
    Files.write(file, Jcs.canonical(value));
  }

  // ------------------------------------------------------------------ T1.7b: membership source

  public static final String TENANT = "tenant-test";
  public static final String ISSUER = "https://issuer.example/maezo";

  /** One synthetic human of the membership source. */
  public record Member(String issuer, String subject, String principal, long revision,
      boolean revoked, String reviewedUntil) {
    public Member revision(long next) {
      return new Member(issuer, subject, principal, next, revoked, reviewedUntil);
    }

    public Member revoked(boolean value) {
      return new Member(issuer, subject, principal, revision, value, reviewedUntil);
    }
  }

  public static Member member(String who, Instant reviewedUntil) {
    return new Member(ISSUER, "subject-" + who, who, 1, false, time(reviewedUntil));
  }

  /**
   * {@code portal_memberships.payload} as the administration plane writes it: pydantic
   * {@code MembershipRecord.model_dump_json()}, with the revision as a JSON INTEGER.
   */
  public static String membershipRow(String tenant, Member m) {
    var row = record("tenant", tenant, "issuer", m.issuer(), "subject", m.subject(),
        "principal_ref", m.principal(), "revision", "@@REVISION@@", "audience", "staff",
        "memberships", new ArrayList<>(List.of(record("membership_ref", "staff-membership",
            "roles", new ArrayList<>(List.of("atendimento")), "groups",
            new ArrayList<>(List.of("atendimento-humano", "medico-auditor"))))),
        "subject_bindings", new ArrayList<>(), "reviewed_until", m.reviewedUntil(), "revoked",
        m.revoked());
    return new String(Jcs.canonical(row), java.nio.charset.StandardCharsets.UTF_8)
        .replace("\"@@REVISION@@\"", Long.toString(m.revision()));
  }

  /**
   * What the Python publisher signs for that row ({@code MembershipProjection} wired), written out
   * field by field here so the provider's own projection is never its own oracle.
   */
  public static Map<String, Object> projection(Member m) {
    return record("principal_ref", m.principal(), "issuer", m.issuer(), "subject", m.subject(),
        "membership_revision", Long.toString(m.revision()), "audience", "staff", "memberships",
        new ArrayList<>(List.of(record("membership_ref", "staff-membership", "roles",
            new ArrayList<>(List.of("atendimento")), "groups",
            new ArrayList<>(List.of("atendimento-humano", "medico-auditor"))))),
        "subject_bindings", new ArrayList<>(), "state", m.revoked() ? "revoked" : "active",
        "reviewed_until", m.reviewedUntil());
  }

  /** The T1.5 handshake contract for a membership snapshot (plan section 3.1). */
  public static Map<String, Object> membershipProvenance(
      String tenant, Map<String, Object> projection, Instant observedAt, long observationSeconds) {
    String revision = (String) projection.get("membership_revision");
    return record("publisher_ref", PUBLISHER, "source_ref",
        MEMBERSHIP_PREFIX + projection.get("principal_ref"), "source_revision", revision,
        "source_digest", Jcs.digest(Jcs.canonical(projection)), "receipt_ref",
        "portal-identity:" + tenant + ":membership:" + projection.get("principal_ref") + "@"
            + revision,
        "observed_at", time(observedAt), "valid_until",
        time(observedAt.plusSeconds(observationSeconds)));
  }

  /** Provenance for the kinds that carry no live source (catalog, revocations). */
  public static Map<String, Object> plainProvenance(String sourceRef, Instant observedAt,
      Instant validUntil) {
    return record("publisher_ref", PUBLISHER, "source_ref", sourceRef, "source_revision", "1",
        "source_digest", "e".repeat(64), "receipt_ref", "receipt-" + sourceRef, "observed_at",
        time(observedAt), "valid_until", time(validUntil));
  }
}
