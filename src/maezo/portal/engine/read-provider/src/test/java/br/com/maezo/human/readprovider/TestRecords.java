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
                CATALOG))),
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
    byte[] spki = root.getPublic().getEncoded();
    return record("schema", "portal-read-provider.v1", "root_public_key_spki_base64",
        Base64.getEncoder().encodeToString(spki), "root_public_key_sha256", Jcs.digest(spki),
        "admission_ref", ADMISSION_REF, "minimum_admission_revision", "1", "datasource_jndi",
        TestNaming.PROCESS_ENGINE, "native_schema", schema, "admission_table_oid",
        Long.toString(oid), "admission_table_owner", owner, "continuity_keys_file",
        keysFile.toString(), "membership_source",
        record("dsn_file", "/run/maezo-native/membership-source.dsn", "ca_file",
            "/run/maezo-native/membership-source-ca.pem", "source_schema", "amh",
            "publisher_ref", PUBLISHER));
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
}
