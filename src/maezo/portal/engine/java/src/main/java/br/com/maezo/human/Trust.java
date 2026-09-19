package br.com.maezo.human;

import java.nio.file.*;
import java.security.*;
import java.security.spec.*;
import java.util.*;

/** Dedicated deployment trust, ADR0049 D4/D5/D7. Public keys only, no secret lookup. */
final class Trust {
  record Key(
      String id,
      String purpose,
      String workload,
      String peerSpki,
      PublicKey publicKey,
      String fingerprint,
      long notBefore,
      long notAfter) {}

  final String tenant, audience, engineName;
  final long maxLifetime;
  final Map<String, Key> keys;
  final boolean syntheticEnabled;

  Trust(Map<String, Object> config) {
    Jcs.keys(
        config,
        "schema",
        "tenant",
        "audience",
        "engine_name",
        "max_lifetime_seconds",
        "keys",
        "enable_synthetic_fixture");
    if (!"human-trust.v1".equals(config.get("schema"))) throw Rejected.invalid();
    tenant = Jcs.ref(config, "tenant");
    audience = Jcs.ref(config, "audience");
    engineName = Jcs.ref(config, "engine_name");
    maxLifetime = Jcs.seconds(config, "max_lifetime_seconds");
    if (maxLifetime <= 0 || !(config.get("enable_synthetic_fixture") instanceof Boolean))
      throw Rejected.invalid();
    syntheticEnabled = (Boolean) config.get("enable_synthetic_fixture");
    if (!(config.get("keys") instanceof List<?> items) || items.isEmpty()) throw Rejected.invalid();
    Map<String, Key> result = new HashMap<>();
    Set<String> fingerprints = new HashSet<>();
    Set<String> purposes = new HashSet<>();
    for (Object item : items) {
      var m = Jcs.object(item);
      Jcs.keys(
          m,
          "id",
          "purpose",
          "workload",
          "peer_spki_sha256",
          "public_key_spki_base64",
          "not_before",
          "not_after");
      String purpose = Jcs.string(m, "purpose");
      if (!Set.of("human-command", "human-authority").contains(purpose)) throw Rejected.invalid();
      try {
        byte[] bytes = Base64.getDecoder().decode(Jcs.string(m, "public_key_spki_base64"));
        PublicKey pub =
            KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(bytes));
        long before = Jcs.seconds(m, "not_before"), after = Jcs.seconds(m, "not_after");
        if (before < 0 || after <= before) throw Rejected.invalid();
        Key key =
            new Key(
                Jcs.ref(m, "id"),
                purpose,
                Jcs.ref(m, "workload"),
                Jcs.hash(m, "peer_spki_sha256"),
                pub,
                Jcs.digest(pub.getEncoded()),
                before,
                after);
        if (result.putIfAbsent(key.id(), key) != null || !fingerprints.add(key.fingerprint()))
          throw Rejected.invalid();
        purposes.add(purpose);
      } catch (GeneralSecurityException | IllegalArgumentException ex) {
        throw Rejected.invalid();
      }
    }
    if (!purposes.equals(Set.of("human-command", "human-authority"))) throw Rejected.invalid();
    for (Key a : result.values())
      for (Key b : result.values()) {
        if (!a.purpose().equals(b.purpose())
            && (a.workload().equals(b.workload()) || a.peerSpki().equals(b.peerSpki())))
          throw Rejected.invalid();
      }
    keys = Map.copyOf(result);
  }

  static Trust load(Path path) throws java.io.IOException {
    return new Trust(Jcs.object(Jcs.parse(Files.readAllBytes(path))));
  }
}
