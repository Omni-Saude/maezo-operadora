package br.com.maezo.human.readprovider;

import br.com.maezo.human.PortalReadTrust;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFileAttributes;
import java.nio.file.attribute.PosixFilePermission;
import java.security.GeneralSecurityException;
import java.time.Clock;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * Custody of the native read-continuity HMAC keys ({@code portal-read-continuity-keys.v1}).
 * Loaded ONCE at boot from a file owned by the engine process with mode 0400. A key is usable only
 * when the CURRENT admission commits to it: {@code HMAC(key, COMMITMENT)} must equal the admitted
 * commitment, and {@link PortalReadTrust.NativeKey#digest} is that commitment, never a hash of the
 * key. The secret never leaves {@link SecretKeySpec}; nothing here logs or prints it.
 */
final class NativeContinuityKeys {
  static final String SCHEMA = "portal-read-continuity-keys.v1";
  static final byte[] COMMITMENT = "maezo/portal-native-read-continuity/v1/commitment"
                                       .getBytes(StandardCharsets.US_ASCII);

  private record Loaded(String id, String generation, Instant notBefore, Instant notAfter,
      SecretKeySpec secret, String commitment) {
    @Override
    public String toString() {
      return "NativeContinuityKey[" + id + ",redacted]";
    }
  }

  private final List<Loaded> keys;

  private NativeContinuityKeys(List<Loaded> keys) {
    this.keys = List.copyOf(keys);
  }

  static NativeContinuityKeys load(Path file) {
    try {
      if (file == null || Files.isSymbolicLink(file))
        throw Fields.unavailable();
      PosixFileAttributes attributes =
          Files.readAttributes(file, PosixFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
      if (!attributes.isRegularFile()
          || !attributes.permissions().equals(Set.of(PosixFilePermission.OWNER_READ))
          || !attributes.owner().getName().equals(System.getProperty("user.name")))
        throw Fields.unavailable();
    } catch (IOException | UnsupportedOperationException | SecurityException ex) {
      throw Fields.unavailable();
    }
    var root = Fields.jsonFile(file);
    Fields.keys(root, "schema", "keys");
    Fields.exact(root, "schema", SCHEMA);
    List<Loaded> loaded = new ArrayList<>();
    Set<String> ids = new HashSet<>();
    for (Object o : Fields.list(root.get("keys"))) {
      var k = Fields.object(o);
      Fields.keys(k, "key_id", "generation", "secret_base64", "not_before", "not_after");
      String id = Fields.ref(k, "key_id");
      String generation = Long.toString(Fields.decimal(k, "generation", 0, Long.MAX_VALUE - 1));
      Instant before = Fields.time(k, "not_before"), after = Fields.time(k, "not_after");
      if (!before.isBefore(after) || !ids.add(id))
        throw Fields.unavailable();
      byte[] raw = Fields.base64(k, "secret_base64", 32);
      try {
        var secret = new SecretKeySpec(raw, "HmacSHA256");
        loaded.add(new Loaded(id, generation, before, after, secret, commitment(secret)));
      } finally {
        Arrays.fill(raw, (byte) 0);
      }
    }
    if (loaded.isEmpty())
      throw Fields.unavailable();
    return new NativeContinuityKeys(loaded);
  }

  static String commitment(SecretKeySpec secret) {
    try {
      Mac mac = Mac.getInstance("HmacSHA256");
      mac.init(secret);
      return HexFormat.of().formatHex(mac.doFinal(COMMITMENT));
    } catch (GeneralSecurityException ex) {
      throw Fields.unavailable();
    }
  }

  /**
   * Binds the custody to one admission. EVERY loaded key must be committed by that admission with
   * the same id, generation and window; an uncommitted key in the mounted file refuses the whole
   * set (the file is not allowed to carry authority the approver did not sign). Each NativeKey's
   * liveness check is the admission's own {@code requireCurrent}, so revocation or supersession
   * observed by a later acquire kills the keys too.
   */
  PortalReadTrust.NativeKeySet bind(ProviderAdmission admission, Clock clock) {
    Map<String, AdmissionRecord.Continuity> admitted = admission.record.continuity;
    List<PortalReadTrust.NativeKey> bound = new ArrayList<>();
    for (Loaded k : keys) {
      var c = admitted.get(k.id);
      if (c == null || !c.generation().equals(k.generation) || !c.commitment().equals(k.commitment)
          || !c.notBefore().equals(k.notBefore) || !c.notAfter().equals(k.notAfter))
        throw Fields.unavailable();
      bound.add(new PortalReadTrust.NativeKey(k.id, k.generation, k.commitment, k.notBefore,
          k.notAfter, k.secret, admission::requireCurrent));
    }
    var set = new BoundKeys(List.copyOf(bound), admission, clock);
    set.requireCurrent();
    return set;
  }

  private static final class BoundKeys implements PortalReadTrust.NativeKeySet {
    private final List<PortalReadTrust.NativeKey> keys;
    private final ProviderAdmission admission;
    private final Clock clock;

    BoundKeys(List<PortalReadTrust.NativeKey> keys, ProviderAdmission admission, Clock clock) {
      this.keys = keys;
      this.admission = admission;
      this.clock = clock;
    }

    @Override
    public PortalReadTrust.NativeKey current() {
      admission.requireCurrent();
      Instant now = clock.instant();
      return keys.stream()
          .filter(k -> !now.isBefore(k.notBefore) && now.isBefore(k.notAfter))
          .max(Comparator.<PortalReadTrust.NativeKey, Instant>comparing(k -> k.notBefore)
                   .thenComparing(k -> Long.parseLong(k.generation)))
          .orElseThrow(Fields::unavailable);
    }

    @Override
    public PortalReadTrust.NativeKey verification(String configuredKeyId) {
      admission.requireCurrent();
      for (var k : keys)
        if (k.id.equals(configuredKeyId))
          return k;
      return null;
    }

    @Override
    public void requireCurrent() {
      current();
    }

    @Override
    public String toString() {
      return "NativeKeySet[redacted]";
    }
  }
}
