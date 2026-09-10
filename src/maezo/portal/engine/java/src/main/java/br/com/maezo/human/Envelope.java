package br.com.maezo.human;

import java.security.*;
import java.util.*;
import java.util.function.Predicate;

/** Ed25519 is fixed by the versioned profile, never negotiated from request data. */
final class Envelope {
  record Verified(
      Map<String, Object> command, String digest, Trust.Key key, long issuedAt, long expiresAt) {
    void requireCurrent(long now) {
      if (issuedAt > now || now >= expiresAt || now < key.notBefore() || now >= key.notAfter())
        throw Rejected.denied();
    }
  }

  static Verified verify(
      byte[] raw,
      Trust trust,
      String purpose,
      String peerSpki,
      long now,
      Predicate<String> revoked) {
    var e = Jcs.object(Jcs.parse(raw));
    Jcs.keys(
        e,
        "schema",
        "purpose",
        "algorithm",
        "audience",
        "issuer",
        "tenant",
        "key_id",
        "issued_at",
        "expires_at",
        "digest",
        "command",
        "signature");
    if (!"human-envelope.v1".equals(e.get("schema"))
        || !"Ed25519".equals(e.get("algorithm"))
        || !purpose.equals(e.get("purpose"))
        || !trust.tenant.equals(e.get("tenant"))
        || !trust.audience.equals(e.get("audience"))) throw Rejected.denied();
    Trust.Key key = trust.keys.get(Jcs.ref(e, "key_id"));
    String keyPurpose = purpose.equals("human-receipt") ? "human-command" : purpose;
    if (key == null
        || !keyPurpose.equals(key.purpose())
        || !key.workload().equals(e.get("issuer"))
        || !key.peerSpki().equals(peerSpki)
        || revoked.test(key.fingerprint())) throw Rejected.denied();
    long issued = Jcs.seconds(e, "issued_at"), expires = Jcs.seconds(e, "expires_at");
    if (issued > now
        || issued < key.notBefore()
        || expires <= now
        || expires <= issued
        || expires - issued > trust.maxLifetime
        || expires > key.notAfter()
        || now >= key.notAfter()) throw Rejected.denied();
    Map<String, Object> command = Jcs.object(e.get("command"));
    Map<String, Object> commandScope = "human-classified-decision.v1".equals(command.get("schema"))
        && purpose.equals("human-command") ? Jcs.object(command.get("scope")) : command;
    if (!trust.tenant.equals(commandScope.get("tenant"))
        || !key.workload().equals(commandScope.get("workload_ref"))) throw Rejected.denied();
    String digest = Jcs.hash(e, "digest");
    if (!digest.equals(Jcs.digest(Jcs.canonical(command)))) throw Rejected.denied();
    String encoded = Jcs.string(e, "signature");
    if (!encoded.matches("[A-Za-z0-9_-]{86}")) throw Rejected.denied();
    try {
      byte[] sig = Base64.getUrlDecoder().decode(encoded);
      if (!Base64.getUrlEncoder().withoutPadding().encodeToString(sig).equals(encoded))
        throw Rejected.denied();
      Map<String, Object> signed = new TreeMap<>(e);
      signed.remove("signature");
      Signature verifier = Signature.getInstance("Ed25519");
      verifier.initVerify(key.publicKey());
      verifier.update(Jcs.canonical(signed));
      if (!verifier.verify(sig)) throw Rejected.denied();
    } catch (GeneralSecurityException | IllegalArgumentException ex) {
      throw Rejected.denied();
    }
    return new Verified(Collections.unmodifiableMap(command), digest, key, issued, expires);
  }
}
