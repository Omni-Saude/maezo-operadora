package br.com.maezo.human.readprovider;

import br.com.maezo.human.Jcs;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.spec.X509EncodedKeySpec;
import java.util.Map;

/**
 * {@code portal-read-provider.v1}: the public, closed provider configuration mounted read-only
 * ({@code MAEZO_PORTAL_READ_PROVIDER_FILE}). It carries no secret. The admission it points at is
 * what grants authority, and that row is signed by the approver's installation root (D-F), which
 * this file only NAMES by SPKI and pin.
 */
final class ProviderConfiguration {
  static final String SCHEMA = "portal-read-provider.v1";

  final PublicKey root;
  final String rootPin;
  final String admissionRef;
  final long minimumAdmissionRevision;
  final String dataSourceJndi;
  final String nativeSchema;
  final long admissionTableOid;
  final String admissionTableOwner;
  final Path continuityKeysFile;
  /** Consumed by T1.7b (membership qualification); only its shape is closed here. */
  final Map<String, Object> membershipSource;

  private ProviderConfiguration(Map<String, Object> m) {
    Fields.keys(m, "schema", "root_public_key_spki_base64", "root_public_key_sha256",
        "admission_ref", "minimum_admission_revision", "datasource_jndi", "native_schema",
        "admission_table_oid", "admission_table_owner", "continuity_keys_file",
        "membership_source");
    Fields.exact(m, "schema", SCHEMA);
    byte[] spki = Fields.base64(m, "root_public_key_spki_base64", -1);
    rootPin = Fields.hash(m, "root_public_key_sha256");
    if (!Jcs.digest(spki).equals(rootPin))
      throw Fields.unavailable();
    try {
      root = KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(spki));
    } catch (GeneralSecurityException ex) {
      throw Fields.unavailable();
    }
    admissionRef = Fields.ref(m, "admission_ref");
    minimumAdmissionRevision =
        Fields.decimal(m, "minimum_admission_revision", 1, Long.MAX_VALUE - 1);
    dataSourceJndi = Fields.string(m, "datasource_jndi");
    if (!dataSourceJndi.matches("java:[A-Za-z0-9][A-Za-z0-9_./-]{0,199}"))
      throw Fields.unavailable();
    nativeSchema = Fields.identifier(m, "native_schema");
    admissionTableOid = Fields.decimal(m, "admission_table_oid", 1, 4294967295L);
    admissionTableOwner = Fields.identifier(m, "admission_table_owner");
    continuityKeysFile = Fields.absolute(m, "continuity_keys_file");
    var source = Fields.object(m.get("membership_source"));
    Fields.keys(source, "dsn_file", "ca_file", "source_schema", "publisher_ref");
    Fields.absolute(source, "dsn_file");
    Fields.absolute(source, "ca_file");
    Fields.identifier(source, "source_schema");
    Fields.ref(source, "publisher_ref");
    membershipSource = Map.copyOf(source);
  }

  static ProviderConfiguration load(Path file) {
    return new ProviderConfiguration(Fields.jsonFile(file));
  }

  static ProviderConfiguration of(Map<String, Object> record) {
    return new ProviderConfiguration(record);
  }
}
