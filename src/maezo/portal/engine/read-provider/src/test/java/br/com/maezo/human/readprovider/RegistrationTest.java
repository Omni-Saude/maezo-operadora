package br.com.maezo.human.readprovider;

import static br.com.maezo.human.readprovider.TestRecords.*;
import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.PortalReadTrust;
import br.com.maezo.human.Rejected;
import java.io.IOException;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.List;
import java.util.ServiceLoader;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class RegistrationTest {
  @Test
  void exactlyOneRegistrationNamesTheInstalledProvider() throws Exception {
    try (var in = getClass().getResourceAsStream(
             "/META-INF/services/br.com.maezo.human.PortalReadTrust$Providers")) {
      assertEquals("br.com.maezo.human.readprovider.InstalledReadProviders\n",
          new String(in.readAllBytes(), StandardCharsets.UTF_8));
    }
    var providers = ServiceLoader.load(PortalReadTrust.Providers.class).stream().toList();
    assertEquals(1, providers.size());
    assertEquals(InstalledReadProviders.class, providers.get(0).type());
    assertTrue(Modifier.isPublic(InstalledReadProviders.class.getModifiers()));
    assertTrue(Modifier.isFinal(InstalledReadProviders.class.getModifiers()));
  }

  /** Classes directory, not a JAR: the loaded-code digest cannot be computed, so nothing works. */
  @Test
  void brokenProviderRefusesEveryMethodAndADirectoryHasNoCodeDigest() {
    var p = new InstalledReadProviders(Path.of("/nonexistent/portal-read-provider.json"),
        java.time.Clock.systemUTC());
    for (var action : List.<Runnable>of(
             () -> p.acquire(scope(), "default", "i", "d", "c".repeat(64), "d".repeat(64),
                 "portal-task-read"),
             () -> p.continuity(null), () -> p.qualifyPublication(null, record())))
      assertEquals(503, assertThrows(Rejected.class, action::run).status);
    assertThrows(Rejected.class, () -> InstalledReadProviders.codeDigest(RegistrationTest.class));
  }

  @Test
  void noArgumentConstructionNeverThrows() {
    var p = new InstalledReadProviders();
    assertEquals("InstalledReadProviders[redacted]", p.toString());
  }

  @Test
  void continuityFileCustodyIsChecked(@TempDir Path dir) throws IOException {
    byte[] secret = new byte[32];
    secret[1] = 7;
    Path file = dir.resolve("keys.json");
    var key = continuityKey(KEY_ID, "1", secret, now(), now().plusSeconds(60));
    writeKeys(file, List.of(key), "r--------");
    NativeContinuityKeys.load(file);
    for (String mode : List.of("rw-------", "r--r--r--", "rwx------"))
      refusedLoading(file, List.of(key), mode);
    refusedLoading(file, List.of(), "r--------");
    refusedLoading(file, List.of(key, key), "r--------");
    var shortKey = record("key_id", KEY_ID, "generation", "1", "secret_base64",
        Base64.getEncoder().encodeToString(new byte[16]), "not_before", time(now()), "not_after",
        time(now().plusSeconds(60)));
    refusedLoading(file, List.of(shortKey), "r--------");
    Path link = dir.resolve("link.json");
    writeKeys(file, List.of(key), "r--------");
    Files.createSymbolicLink(link, file);
    assertThrows(Rejected.class, () -> NativeContinuityKeys.load(link));
  }

  static void refusedLoading(Path file, List<java.util.Map<String, Object>> keys, String mode)
      throws IOException {
    writeKeys(file, keys, mode);
    assertThrows(Rejected.class, () -> NativeContinuityKeys.load(file));
  }

  @Test
  void commitmentIsTheLabelledHmacNotTheKeyHash() throws Exception {
    byte[] secret = new byte[32];
    secret[0] = 42;
    var mac = javax.crypto.Mac.getInstance("HmacSHA256");
    mac.init(new javax.crypto.spec.SecretKeySpec(secret, "HmacSHA256"));
    String expected = java.util.HexFormat.of().formatHex(mac.doFinal(
        "maezo/portal-native-read-continuity/v1/commitment".getBytes(StandardCharsets.US_ASCII)));
    assertEquals(expected, commitment(secret));
    assertNotEquals(br.com.maezo.human.Jcs.digest(secret), commitment(secret));
  }
}
