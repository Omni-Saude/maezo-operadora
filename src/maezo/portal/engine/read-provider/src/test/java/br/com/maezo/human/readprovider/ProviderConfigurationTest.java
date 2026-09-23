package br.com.maezo.human.readprovider;

import static br.com.maezo.human.readprovider.TestRecords.*;
import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.Rejected;
import java.nio.file.Path;
import java.security.KeyPair;
import java.util.Base64;
import java.util.Map;
import java.util.function.Consumer;
import org.junit.jupiter.api.Test;

class ProviderConfigurationTest {
  final KeyPair root = ed25519();

  Map<String, Object> valid() {
    return providerConfiguration(
        root, "maezo_native", 16384, "maezo_native_schema_owner",
        Path.of("/run/maezo-native/portal-read-continuity-keys.json").toAbsolutePath());
  }

  void refusedWith(Consumer<Map<String, Object>> change) {
    var c = valid();
    change.accept(c);
    Rejected r = assertThrows(Rejected.class, () -> ProviderConfiguration.of(c));
    assertEquals(503, r.status);
  }

  @Test
  void validConfigurationLoads() {
    var c = ProviderConfiguration.of(valid());
    assertEquals("maezo_native", c.nativeSchema);
    assertEquals(16384, c.admissionTableOid);
    assertEquals("maezo_native_schema_owner", c.admissionTableOwner);
    assertEquals(ADMISSION_REF, c.admissionRef);
    assertEquals(1, c.minimumAdmissionRevision);
    assertEquals("java:jdbc/ProcessEngine", c.dataSourceJndi);
  }

  @Test
  void rootKeyMustMatchItsPin() {
    refusedWith(c -> c.put("root_public_key_sha256", "0".repeat(64)));
    refusedWith(c
        -> c.put("root_public_key_spki_base64",
            Base64.getEncoder().encodeToString(ed25519().getPublic().getEncoded())));
    refusedWith(c -> c.put("root_public_key_spki_base64", "AAAA"));
  }

  @Test
  void theShapeIsClosed() {
    refusedWith(c -> c.put("extra", "x"));
    refusedWith(c -> c.remove("membership_source"));
    refusedWith(c -> c.put("schema", "portal-read-provider.v2"));
    refusedWith(c -> ((Map<String, Object>) c.get("membership_source")).put("dsn", "x"));
  }

  @Test
  void identifiersAndPathsAreStrict() {
    refusedWith(c -> c.put("native_schema", "Maezo_Native"));
    refusedWith(c -> c.put("native_schema", "maezo_native;drop"));
    refusedWith(c -> c.put("native_schema", "\"maezo_native\""));
    refusedWith(c -> c.put("admission_table_owner", "owner-with-dash"));
    refusedWith(c -> c.put("admission_table_oid", "0"));
    refusedWith(c -> c.put("minimum_admission_revision", "0"));
    refusedWith(c -> c.put("continuity_keys_file", "relative/keys.json"));
    refusedWith(c -> c.put("datasource_jndi", "jdbc/ProcessEngine"));
    refusedWith(c -> c.put("datasource_jndi", "ldap://evil/ProcessEngine"));
  }
}
