package br.com.maezo.human;

import java.security.*;
import java.util.*;

/** Generated ephemeral synthetic test keys only. */
final class TestKeys {
  final KeyPair command, authority;
  final String peer = "f".repeat(64), adminPeer = "e".repeat(64);

  TestKeys() {
    try {
      command = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
      authority = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
    } catch (GeneralSecurityException ex) {
      throw new AssertionError(ex);
    }
  }

  Map<String, Object> key(String id, String purpose, String workload, String peer, PublicKey key) {
    return Map.of(
        "id",
        id,
        "purpose",
        purpose,
        "workload",
        workload,
        "peer_spki_sha256",
        peer,
        "public_key_spki_base64",
        Base64.getEncoder().encodeToString(key.getEncoded()),
        "not_before",
        "0",
        "not_after",
        "4102444800");
  }

  Map<String, Object> config() {
    return new TreeMap<>(
        Map.of(
            "schema",
            "human-trust.v1",
            "tenant",
            "tenant-test",
            "audience",
            "engine-test",
            "engine_name",
            "human-it",
            "max_lifetime_seconds",
            "60",
            "enable_synthetic_fixture",
            true,
            "keys",
            List.of(
                key("command-key", "human-command", "gateway-test", peer, command.getPublic()),
                key(
                    "authority-key",
                    "human-authority",
                    "publisher-test",
                    adminPeer,
                    authority.getPublic()))));
  }

  Trust trust() {
    return new Trust(config());
  }

  byte[] sign(Map<String, Object> command, String purpose, long issued, long expires) {
    var e = envelope(command, purpose, issued, expires);
    return signEnvelope(
        e, purpose.equals("human-authority") ? authority.getPrivate() : this.command.getPrivate());
  }

  Map<String, Object> envelope(
      Map<String, Object> command, String purpose, long issued, long expires) {
    var e = new TreeMap<String, Object>();
    boolean admin = purpose.equals("human-authority");
    e.put("schema", "human-envelope.v1");
    e.put("purpose", purpose);
    e.put("algorithm", "Ed25519");
    e.put("tenant", "tenant-test");
    e.put("audience", "engine-test");
    e.put("issuer", admin ? "publisher-test" : "gateway-test");
    e.put("key_id", admin ? "authority-key" : "command-key");
    e.put("issued_at", Long.toString(issued));
    e.put("expires_at", Long.toString(expires));
    e.put("command", command);
    e.put("digest", Jcs.digest(Jcs.canonical(command)));
    return e;
  }

  static byte[] signEnvelope(Map<String, Object> envelope, PrivateKey key) {
    var e = new TreeMap<>(envelope);
    e.remove("signature");
    try {
      Signature signer = Signature.getInstance("Ed25519");
      signer.initSign(key);
      signer.update(Jcs.canonical(e));
      e.put("signature", Base64.getUrlEncoder().withoutPadding().encodeToString(signer.sign()));
      return Jcs.canonical(e);
    } catch (GeneralSecurityException ex) {
      throw new AssertionError(ex);
    }
  }
}
